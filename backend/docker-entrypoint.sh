#!/bin/sh
# Runs migrations before starting the ASGI server. Idempotent - `alembic
# upgrade head` is a no-op if the schema is already current, so this is
# safe to run on every container start (including every worker replica
# starting at once - Alembic's own migration-lock table serializes that).
#
# Deliberately NOT the entrypoint for the Celery worker: docker-compose.yml's
# celery-worker service overrides the image's command entirely, so this
# script (and the migration it runs) only ever executes once per deploy,
# from the api service - not once per worker replica racing the same
# migration unnecessarily.
set -e

alembic upgrade head
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers "${WEB_CONCURRENCY:-2}" \
    --proxy-headers \
    --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}"
