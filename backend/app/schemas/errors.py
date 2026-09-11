"""Reusable OpenAPI error response documentation (Day 49).

Every HTTPException raised across this API already returns the same
shape - `{"detail": "..."}` (FastAPI's own default HTTPException
handler, unchanged since Day 5) - so one Pydantic model plus one small
helper documents every non-2xx response consistently instead of
repeating an inline schema at every endpoint. Purely descriptive:
importing/using this doesn't change what any endpoint actually returns,
and it's deliberately not used for 422 (FastAPI's automatic request
validation errors return a different shape - a list of per-field
errors, not a single string - and are already documented correctly by
FastAPI itself without any help from this module).
"""

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    """The shape of every error response this API returns via
    HTTPException: `{"detail": "<human-readable message>"}`."""

    detail: str


def error_response(description: str) -> dict:
    """One entry for a FastAPI `responses={...}` dict, documenting an
    error status code with this API's standard error shape."""
    return {"model": ErrorDetail, "description": description}
