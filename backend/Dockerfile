# Production image for the FastAPI backend. Also the image the Celery
# worker runs from (docker/docker-compose.yml's celery-worker service
# clears this image's ENTRYPOINT below and runs
# `celery -A app.core.celery_app worker` as its own command instead,
# rather than building a second image for what is, dependency-wise, the
# same app - same reasoning as every other "one image, different
# command per role" deployment of a Celery-backed app).
#
# Two build targets, not one (Day 51's review): `production` (the
# default - EMBEDDING_PROVIDER=openai, requirements.txt only) and
# `with-local-embedding` (EMBEDDING_PROVIDER=local - adds fastembed and
# its ~150MB of ONNX/numpy/tokenizers dependencies via
# requirements-local-embedding.txt). Diverging right after `base` - before
# either target's `pip install` - rather than sharing one dependency
# layer keeps a plain `docker build` (no --target) both lightweight AND
# unambiguous about which one it gets: `production` is the *last* stage
# in this file (Docker's own default target when none is specified) and
# is also set explicitly via `target:` in docker-compose.yml, so neither
# relies on the other to get this right. Build the other target with
# `docker build --target with-local-embedding .`; docker-compose.yml
# does this via its `BACKEND_DOCKER_TARGET` build arg (see .env.example).
#
# Single-stage per target, not multi-stage: asyncpg/bcrypt/PyJWT etc. all
# ship manylinux wheels for this Python version, so there's no compiled
# build step whose toolchain would need to be discarded afterward.
#
# Pinned to Python 3.9 to match the version this whole project has been
# built and verified against (Day 44's same reasoning for CI).
FROM python:3.9-slim AS base

WORKDIR /app

# Runs as a real user, not root - standard hardening, and nothing here
# needs root (no privileged ports, no system package installs at runtime).
RUN useradd --create-home --uid 1000 appuser

FROM base AS with-local-embedding
# Neither this nor the `production` target below installs
# requirements-dev.txt (pytest and friends) - dev-only, same reasoning
# as always.
COPY requirements.txt requirements-local-embedding.txt ./
RUN pip install --no-cache-dir -r requirements-local-embedding.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]

FROM base AS production
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
