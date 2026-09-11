import asyncio

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db

router = APIRouter(tags=["Health"])

# Bounded rather than relying on the engine's own (unconfigured) connection
# behavior - the same "an unreachable, as opposed to actively refused, host
# can hang indefinitely" class of bug already fixed for every other
# external dependency in this project (Days 38, 42, 43), scoped to just
# this check rather than changing the shared engine's behavior for every
# query in the app.
_READINESS_QUERY_TIMEOUT_SECONDS = 3.0


class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str


class ReadinessOkResponse(BaseModel):
    status: str


class ReadinessNotReadyResponse(BaseModel):
    status: str
    detail: str


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description=(
        "Reports only whether the process itself is up - it never checks "
        "Postgres, Redis, or Qdrant. An orchestrator restarting the process "
        "on a dependency outage (instead of on this endpoint failing) would "
        "make things worse, not better. Use /health/ready to check whether "
        "the process can actually serve requests right now."
    ),
)
def health_check():
    return {
        "status": "ok",
        "service": "repomind-ai-backend",
        "environment": settings.environment,
    }


@router.get(
    "/health/ready",
    summary="Readiness probe",
    description=(
        "Checks whether the process can actually serve requests right now: "
        "currently just a bounded Postgres query. Redis and Qdrant are "
        "deliberately not checked here - both already degrade gracefully "
        "when unreachable (ingestion fails into a clear `failed` status, "
        "rate limiting fails open, search/chat map to a clean 502/503), so "
        "including them would make this probe flag conditions that don't "
        "actually block most requests. Never includes the connection "
        "string, credentials, or any other infrastructure detail."
    ),
    responses={
        200: {"model": ReadinessOkResponse, "description": "Database reachable; the process can serve requests."},
        503: {"model": ReadinessNotReadyResponse, "description": "Database unreachable or the check timed out."},
    },
)
async def readiness_check(response: Response, db: AsyncSession = Depends(get_db)):
    try:
        await asyncio.wait_for(
            db.execute(text("SELECT 1")), timeout=_READINESS_QUERY_TIMEOUT_SECONDS
        )
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not ready", "detail": "database unreachable"}

    return {"status": "ready"}
