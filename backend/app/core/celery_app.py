"""Celery application (architecture.md Phase 5 - background workers).

Replaces the FastAPI `BackgroundTasks` used for ingestion since Day 7: a
`BackgroundTasks` job only runs as long as the API process that queued it
stays alive and gives no visibility or retry if it fails silently, which
is fine for a single-developer demo but not a real deployment. Redis is
the broker; there's no result backend configured since nothing needs a
task's return value - `ingest_repository` already reports outcome via the
`repositories.status`/`error_message` columns, so a Celery-side result
store would just be a second, redundant place for the same information.

On native Windows, run the worker with `--pool=solo` (the default
"prefork" pool needs `os.fork()`, which Windows doesn't have):

    celery -A app.core.celery_app worker --loglevel=info --pool=solo
"""

from celery import Celery

from app.core.config import settings

celery_app = Celery("repomind", broker=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Day 38: bound how long a blocked/unreachable broker can stall a
    # `.delay()` call. Without these, an unreachable (as opposed to
    # actively refused) Redis port can leave the connect() syscall itself
    # hanging for the OS's own TCP timeout - tens of seconds - and the
    # caller (a FastAPI request handler) blocks the whole time. These
    # timeouts don't make `.delay()` non-blocking, just bound the wait;
    # app/api/repositories.py still wraps every call so a failure - fast
    # or eventually-timed-out - never hangs or 500s the request.
    broker_connection_timeout=5,
    broker_transport_options={
        "socket_connect_timeout": 5,
        "socket_timeout": 5,
    },
)

# Importing app.tasks registers its @celery_app.task-decorated functions.
# Celery's autodiscover_tasks is built for Django's app-per-directory
# layout and doesn't fit this project's flat structure, so a plain import
# is simpler - app.tasks imports celery_app back from this already-fully-
# defined module, so the circularity resolves fine.
import app.tasks  # noqa: E402,F401
