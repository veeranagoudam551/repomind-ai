"""Request ID middleware + per-request access logging (Day 48).

One request ID per request, readable from three places without threading
it through every function signature: the response's X-Request-ID header
(for the client to hand back when reporting a problem), request.state
(for any route handler that wants it explicitly), and
app.core.logging_config's contextvar (so every log line emitted while
handling this request - including deep inside a service function that
never sees the Request object - gets stamped with it automatically).

Also does the request-completed logging itself (method/path/status/
duration) rather than a second middleware, since both need the same
start-of-request timestamp and end-of-request hook.

Unhandled exceptions are caught and turned into a generic 500 response
right here too, rather than via a `@app.exception_handler(Exception)`
registration - FastAPI/Starlette route that specific case to
`ServerErrorMiddleware`, which sits *outside* every `add_middleware()`
call (this one included) and, by design, re-raises the exception again
after sending the response ("this allows test clients to optionally
raise the error within the test case" - Starlette's own comment).  That
re-raise propagates back out through this middleware's `call_next`
uncaught, which is harmless for a real ASGI server (the response was
already sent) but leaves the request's async context torn down
mid-flight - confirmed by hitting exactly that: it corrupted the next
test's database connection. Handling it here instead means the
exception never leaves this middleware at all.
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.logging_config import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"

# Deliberately narrow: a client-supplied X-Request-ID is untrusted input
# that ends up in both a response header and every log line for this
# request, so control characters (log/header injection) and unbounded
# length are rejected outright rather than partially cleaned up - an
# incoming value that doesn't match this is treated the same as no
# header at all, and a fresh UUID is generated instead.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

logger = logging.getLogger(__name__)


def _resolve_request_id(incoming: str | None) -> str:
    if incoming and _VALID_REQUEST_ID.match(incoming):
        return incoming
    return str(uuid.uuid4())


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            # Anything that reaches here already passed through
            # ExceptionMiddleware without being claimed - FastAPI's own
            # HTTPException/RequestValidationError handlers (and typed
            # subclasses like RateLimitExceeded) already turned their
            # cases into a normal response before this middleware ever
            # sees them, so this really is "nobody knew how to handle
            # this". Logged with the full stack trace server-side; the
            # client gets a generic message only.
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "unhandled exception",
                extra={
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            response = JSONResponse(status_code=500, content={"detail": "Internal server error"})
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        else:
            duration_ms = (time.perf_counter() - start) * 1000
            response.headers[REQUEST_ID_HEADER] = request_id
            logger.info(
                "request completed",
                extra={
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            return response
        finally:
            request_id_var.reset(token)
