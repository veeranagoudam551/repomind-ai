import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.conversations import router as conversations_router
from app.api.health import router as health_router
from app.api.repositories import router as repositories_router
from app.core.config import settings

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

# Day 47: unlike the JWT/database checks in config.py (hard failures - a
# forgeable secret or a guessable DB password are never acceptable), a
# stale CORS origin is a warning, not a refusal to start: a legitimate
# same-origin/reverse-proxy setup might not need CORS configured at all,
# so this can't tell "misconfigured" from "genuinely doesn't apply" the
# way the other two checks can.
if settings.environment == "production" and settings.cors_origins == "http://localhost:3000":
    logger.warning(
        "CORS_ORIGINS is still the localhost default in a production "
        "environment - set it to the real frontend origin(s)."
    )

app = FastAPI(
    title="RepoMind AI",
    description="AI-powered codebase intelligence & software engineering assistant API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(repositories_router)
app.include_router(conversations_router)


@app.get("/")
def root():
    return {"service": "RepoMind AI API", "docs": "/docs"}
