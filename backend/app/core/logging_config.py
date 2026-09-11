"""Structured logging (Day 48).

One place that decides *how* every log line in this process looks -
the API (via app.main), and the Celery worker (via app.core.celery_app's
`setup_logging` signal receiver, so task logs get the same treatment
without duplicating this configuration in two places).

Format depends on `settings.environment`, the same flag config.py's
production-safety guard and main.py's CORS warning already key off of:
production gets one JSON object per line (machine-readable - built for
a log aggregator, not a human staring at a terminal), anything else
gets a short human-readable line. Both formats carry the same fields;
JSON just doesn't need column alignment to stay readable.

The request ID (app.core.request_id) travels via a contextvar rather
than being passed explicitly to every log call - a `logging.Filter`
reads it here and stamps it onto every record, including ones logged
deep inside a service function that has never seen a Request object.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Optional

from app.core.config import settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Attributes every stdlib LogRecord already carries - anything else set
# via `extra={...}` is application-specific and worth surfacing in the
# structured output (see JSONFormatter.format below).
_STANDARD_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "request_id",
}


def get_request_id() -> str:
    return request_id_var.get()


class RequestIDLogFilter(logging.Filter):
    """Stamps the current request ID (or "-" outside a request) onto
    every record, so both formatters below can reference %(request_id)s
    / include it in the JSON body unconditionally - never a KeyError for
    a log line emitted from a background task or at startup."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        # Anything passed via extra={...} at the call site (e.g. the
        # request-logging middleware's http_method/status_code/duration_ms)
        # rides along as-is rather than needing a fixed schema here.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_TEXT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s [request_id=%(request_id)s] %(message)s"


def configure_logging(environment: Optional[str] = None, log_level: Optional[str] = None) -> None:
    """Idempotent: safe to call more than once (e.g. once from app.main
    at import time, and Celery's own `setup_logging` signal could in
    principle fire more than once per process) - re-configuring just
    replaces the handler rather than stacking duplicate ones."""
    env = environment if environment is not None else settings.environment
    level = log_level if log_level is not None else settings.log_level

    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler()
    handler.addFilter(RequestIDLogFilter())
    if env == "production":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FORMAT))

    root.addHandler(handler)
    root.setLevel(level)
