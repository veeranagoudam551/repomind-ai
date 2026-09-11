import asyncio

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db

router = APIRouter()

_READINESS_QUERY_TIMEOUT_SECONDS = 3.0


@router.get("/health")
def health_check():
    """Liveness: is the process up at all. No dependency checks - a
    load balancer/orchestrator restarting the process on a failure here
    would be the wrong response to "the database happens to be down"."""
    return {
        "status": "ok",
        "service": "repomind-ai-backend",
        "environment": settings.environment,
    }


@router.get("/health/ready")
async def readiness_check(response: Response, db: AsyncSession = Depends(get_db)):
    """Readiness (Day 47): can the process actually serve requests right
    now, not just "is it running". Checks Postgres only, deliberately not
    Redis/Qdrant - both already degrade gracefully when unreachable (Days
    38-43: ingestion fails fast into a clear `failed` status, rate
    limiting fails open, search/chat map to a clean 502/503) so reporting
    "not ready" for either would flag a condition that doesn't actually
    block most requests, making the probe misleading rather than useful.
    Postgres is different: nearly every endpoint needs a session.

    Bounded with an explicit timeout rather than relying on the engine's
    own (unconfigured) connection behavior - the same "an unreachable, as
    opposed to actively refused, host can hang indefinitely" class of bug
    already fixed for every other external dependency in this project
    (Days 38, 42, 43), scoped to just this check rather than changing the
    shared engine's behavior for every query in the app.

    Never includes the connection string, credentials, or any other
    infrastructure detail in the response - just a status.
    """
    try:
        await asyncio.wait_for(
            db.execute(text("SELECT 1")), timeout=_READINESS_QUERY_TIMEOUT_SECONDS
        )
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not ready", "detail": "database unreachable"}

    return {"status": "ready"}
