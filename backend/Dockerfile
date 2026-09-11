# Production image for the FastAPI backend. Also the image the Celery
# worker runs from (docker/docker-compose.yml's celery-worker service
# clears this image's ENTRYPOINT below and runs
# `celery -A app.core.celery_app worker` as its own command instead,
# rather than building a second image for what is, dependency-wise, the
# same app - same reasoning as every other "one image, different
# command per role" deployment of a Celery-backed app).
#
# Single-stage, not multi-stage: asyncpg/bcrypt/PyJWT etc. all ship
# manylinux wheels for this Python version, so there's no compiled
# build step whose toolchain would need to be discarded afterward - a
# second stage would add complexity without shrinking the image.
#
# Pinned to Python 3.9 to match the version this whole project has been
# built and verified against (Day 44's same reasoning for CI).
FROM python:3.9-slim

WORKDIR /app

# Runs as a real user, not root - standard hardening, and nothing here
# needs root (no privileged ports, no system package installs at runtime).
RUN useradd --create-home --uid 1000 appuser

# requirements.txt only, not requirements-dev.txt: pytest and friends
# have no reason to ship inside a production image.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
