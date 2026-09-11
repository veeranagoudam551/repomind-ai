import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.conversations import router as conversations_router
from app.api.health import router as health_router
from app.api.repositories import router as repositories_router
from app.core.config import settings
from app.core.logging_config import configure_logging
from app.core.request_id import RequestIDMiddleware

configure_logging()
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

# Day 49: one static list instead of letting each router's `tags=[...]`
# implicitly invent the OpenAPI tag registry - lets every tag carry a
# description in Swagger/ReDoc, and keeps the full set of tags visible
# in one place rather than scattered across five router files.
_OPENAPI_TAGS = [
    {"name": "Health", "description": "Liveness/readiness probes for load balancers and orchestrators. No authentication required."},
    {"name": "Authentication", "description": "Register, log in, and inspect the current user. Issues the JWT bearer token every other endpoint requires."},
    {"name": "Repositories", "description": "Add, list, inspect, delete, and re-index GitHub repositories for analysis."},
    {"name": "Files", "description": "Browse a repository's indexed files and the code chunks they were split into."},
    {"name": "Search", "description": "Semantic (embedding-based) code search within a single repository."},
    {"name": "Conversations", "description": "Multi-turn, repository-grounded chat threads and their messages (RAG)."},
    {"name": "AI Analysis", "description": "One-shot LLM-generated analysis: file explain/review, bug debugging, architecture overview, and a static security scan."},
    {"name": "Agent", "description": "A multi-step tool-using agent that autonomously investigates a repository toward a stated goal."},
]

app = FastAPI(
    title="RepoMind AI API",
    description=(
        "AI-powered codebase intelligence backend: ingests a public GitHub "
        "repository, indexes it for semantic search (embeddings + Qdrant), "
        "and exposes LLM-backed search, chat, debugging, architecture, "
        "security-scan, and agent endpoints on top of it. All endpoints "
        "except /health, /health/ready, and /auth/register|login require a "
        "JWT bearer token obtained from POST /auth/login."
    ),
    version="1.0.0",
    openapi_tags=_OPENAPI_TAGS,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Added last so it runs first (outermost) - every response, including
# ones CORS or routing itself rejects, still gets an X-Request-ID header,
# a logged request summary, and (app.core.request_id) a safe generic 500
# for any exception nothing else handled.
app.add_middleware(RequestIDMiddleware)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(repositories_router)
app.include_router(conversations_router)


@app.get("/", summary="API root", description="Points to the interactive API docs.")
def root():
    return {"service": "RepoMind AI API", "docs": "/docs"}
