from fastapi import APIRouter

from app.core.config import settings

router = APIRouter()


@router.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "repomind-ai-backend",
        "environment": settings.environment,
    }
