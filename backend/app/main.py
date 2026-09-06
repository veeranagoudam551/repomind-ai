import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.core.config import settings

logging.basicConfig(level=settings.log_level)

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


@app.get("/")
def root():
    return {"service": "RepoMind AI API", "docs": "/docs"}
