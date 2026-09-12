#!/bin/sh
# Runs migrations before starting the ASGI server. Idempotent - `alembic
# upgrade head` is a no-op if the schema is already current, so this is
# safe to run on every container start.
#
# Day 50: correcting an overstatement from Day 47's own comment here -
# Alembic's `alembic_version` table records the current revision, but
# it is not a real distributed lock (no `SELECT ... FOR UPDATE`-style
# claim before applying a migration). That's a non-issue for the single
# `api` replica docker/docker-compose.yml actually runs - only one
# process ever executes this script at a time - but it would NOT be
# safe to scale `api` to multiple replicas without adding a real lock
# (e.g. a Postgres advisory lock in alembic/env.py) first; don't assume
# that safety exists just because this comment used to claim it did.
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
