# RepoMind AI

[![CI](https://github.com/veeranagoudam551/repomind-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/veeranagoudam551/repomind-ai/actions/workflows/ci.yml)

**AI-Powered Codebase Intelligence & Software Engineering Assistant**

RepoMind AI lets a developer connect a GitHub repository and use AI to
explore, understand, debug, and analyze the codebase — grounded in the
actual source code via Retrieval-Augmented Generation (RAG), not
guesswork.

> Status: Under active development. This README grows alongside the
> project; see [docs/architecture.md](docs/architecture.md) for the
> full system design.

## Planned Features

- Ask questions about a codebase and get answers grounded in real source
- Semantic code search across a repository
- AI-assisted debugging using repository context
- Code review and code explanation
- Architecture analysis and basic defensive security scanning
- Specialized AI agents for complex, multi-step tasks

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js, TypeScript, Tailwind CSS, shadcn/ui, TanStack Query |
| Backend | Python, FastAPI, Pydantic, SQLAlchemy |
| Relational DB | PostgreSQL |
| Vector DB | Qdrant |
| AI / RAG | LLM provider abstraction, embeddings, LangGraph (agents) |
| Background jobs | Redis + Celery |
| Infra | Docker, Docker Compose |

## Architecture

```
  Browser
     │
     ▼
  Next.js (frontend)         all API calls are server-side —
     │                       the browser never calls FastAPI directly
     ▼
  FastAPI (backend)
     ├──► PostgreSQL     users, repositories, files, chunks, conversations
     ├──► Qdrant         code-chunk vectors (embeddings)
     ├──► Redis          Celery broker + rate-limit counters
     └──► Celery worker
              │
              ▼
        background ingestion (clone → scan → chunk → embed)
```

Embeddings (turning code/chat text into vectors) happen in two places
that share one interface (`app/services/embedding_providers.py`):
repository ingestion (Celery worker, writing to Qdrant) and every
search/chat/debug request (FastAPI, embedding the query to search
Qdrant) — see "Embedding Providers" below for the OpenAI vs. local
choice. LLM calls (Anthropic Claude, via LangGraph for the multi-step
agent) happen only in FastAPI request handlers that need one: chat,
explain, review, architecture, security-scan, and the agent — never
during ingestion itself. See [docs/architecture.md](docs/architecture.md)
for the full system design, including the data model and RAG pipeline
in more detail, and "Production Deployment → Architecture" below for
how this maps onto the actual Docker containers/ports.

## Project Structure

```
repomind-ai/
├── frontend/     # Next.js app
├── backend/      # FastAPI app
├── docs/         # Architecture & design docs
├── docker/       # Docker Compose & service configs
├── .env.example  # Full environment variable contract
└── README.md
```

## Development Status

This project is being built incrementally, one milestone at a time.
See [docs/architecture.md](docs/architecture.md) for what's done and
what's planned.

## Prerequisites

Versions actually pinned/validated by this project (in
[`backend/Dockerfile`](backend/Dockerfile),
[`frontend/Dockerfile`](frontend/Dockerfile), and
[`.github/workflows/ci.yml`](.github/workflows/ci.yml)) — not invented
minimums:

| Requirement | Version | Needed for |
|---|---|---|
| Python | 3.9 | Backend (FastAPI, Celery worker) |
| Node.js | 20 | Frontend (Next.js) |
| PostgreSQL | 17 (`postgres:17-alpine` in Docker/CI) | Always |
| Redis | 7 (`redis:7-alpine` in Docker/CI) | Always (Celery broker + rate limiting) |
| Qdrant | latest (`qdrant/qdrant:latest` — not version-pinned upstream) | Always (vector search) |
| Docker Engine + Compose v2 | any recent version supporting `docker compose` (not the standalone v1 `docker-compose` binary) | Only if using Docker for infra or the full stack — see "Local infrastructure" and "Docker Compose deployment" below |

Postgres/Redis/Qdrant can each be run natively/via WSL instead of
Docker — see "Local infrastructure" below; Docker is never a hard
requirement of the application itself.

## Getting Started

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Visit `http://localhost:3000`. Requires the backend running (see
below) at the URL in `NEXT_PUBLIC_API_BASE_URL`. Register an account
at `/register`, then `/login` — `/dashboard` is real from here on:
it lists your repositories from the live API, lets you add one by
GitHub URL, and shows its ingestion status as it moves through
`pending → cloning → processing → completed`. Click a repository to
see its full detail page (`/dashboard/[id]`) — status, any failure
message, and every scanned file — with buttons to reindex or delete.
Once a repository is `completed`, its **Chat** button opens
`/dashboard/[id]/chat`, where "New chat" starts a conversation
(`/dashboard/[id]/chat/[conversationId]`) — ask a question and it's
answered grounded in that repository's indexed code, with the source
files shown under each reply. Its **Search** button opens
`/dashboard/[id]/search` for semantic code search without an LLM
round-trip — type a query and get back the closest matching code
chunks directly, ranked by similarity; the query lives in the URL
(`?q=...`), so results are a plain, shareable, bookmarkable link.

#### End-to-end tests

```bash
cd frontend
npx playwright install chromium   # first time only
npm run test:e2e
```

Drives a real Chromium browser through register/login/logout and the
full add-repository → wait for ingestion → view files → reindex →
delete flow. Starts the Next.js dev server itself and starts/reuses
the FastAPI backend automatically (needs a local Postgres reachable
via the backend's `DATABASE_URL`, same as running the backend
manually), and deletes the `e2e_`-prefixed test users it creates from
the dev database when the run finishes. A real `GITHUB_TOKEN` in
`.env` is strongly recommended before running this suite repeatedly —
each run adds a real repository via the live GitHub API, and the
unauthenticated 60 req/hour limit exhausts fast. Since ingestion now
also embeds every chunk (Day 15), the suite passes whether or not a
real `OPENAI_API_KEY` is configured: it waits for either terminal
status and asserts accordingly (`README` visible on `completed`, the
real error text on `failed`).

### Local infrastructure (PostgreSQL, Redis, Qdrant)

The backend needs all three reachable before `alembic upgrade head` or
`uvicorn` will work. Two ways to get them — pick whichever fits your
machine, and mix and match if you like (e.g. native Postgres plus
Dockerized Redis/Qdrant); the backend only ever talks to
`localhost:<port>` per `.env`, so it can't tell which option provided
that port and neither is a hard dependency of the app itself.

**Option A — Docker Compose.** Needs Docker Desktop/Engine running.
The most convenient option if your machine can spare the resources:

```bash
docker compose -f docker/docker-compose.yml up -d
```

Starts Postgres, Redis, and Qdrant together on their default ports
with credentials matching `.env.example`'s defaults, no configuration
needed. If you've changed `POSTGRES_PASSWORD`/etc. in your own `.env`,
pass it through explicitly: `docker compose -f docker/docker-compose.yml
--env-file .env up -d` (run from the repo root; Compose only
auto-loads a `.env` next to the compose file, not the project root's).
`docker compose -f docker/docker-compose.yml down` stops them; add
`-v` to also drop the named volumes and lose all local data.

**Option B — native/WSL services.** No Docker Desktop at all — what
this project has actually been built and verified against day to day,
e.g. on an 8 GB RAM laptop where Docker Desktop's own overhead is a
real problem on top of everything else running:

- **PostgreSQL** — install it natively (the official Windows
  installer, `apt install postgresql` in WSL, Homebrew on macOS, etc.)
  and `createdb repomind_ai`.
- **Redis** — no official native-Windows build, so run it inside
  WSL2 instead: `wsl --install` if you don't have a distro yet, then
  inside it `sudo apt install redis-server` and
  `redis-server --daemonize yes` (or `sudo service redis-server
  start`, if that's set up). WSL2 forwards `localhost:6379` to Windows
  automatically. On Linux/macOS, just install and run `redis-server`
  directly.
- **Qdrant** — also no official native-Windows build. Download the
  standalone binary from [Qdrant's GitHub
  releases](https://github.com/qdrant/qdrant/releases) (the
  `x86_64-pc-windows-msvc` zip on Windows), extract it somewhere with
  a short path (Windows' `MAX_PATH` limit breaks its on-disk storage
  from a deeply-nested one, e.g. a temp folder), and run `qdrant.exe`
  directly — no config needed, it listens on `localhost:6333`. On
  Linux/macOS the prebuilt binary or `cargo install` both work fine
  directly, no workaround needed.

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate      # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
cp ../.env.example ../.env  # the root .env.example - shared with the frontend; edit DATABASE_URL to point at your local Postgres
alembic upgrade head        # create the schema (users, repositories, conversations, messages, repository_files, code_chunks)
uvicorn app.main:app --reload --port 8000
```

Visit `http://localhost:8000/health` for the health check or
`http://localhost:8000/docs` for interactive API docs.

Ingestion also embeds every code chunk and stores the vectors in
Qdrant (`QDRANT_HOST`/`QDRANT_PORT`, default `localhost:6333` — see
"Local infrastructure" above to get one running). The collection
(`QDRANT_COLLECTION_NAME`) is created automatically on first use. An
`OPENAI_API_KEY` is also required — without one, ingestion fails at
the embedding step and the repository is marked `failed` with that
error message. Chatting in a conversation additionally requires an
`ANTHROPIC_API_KEY` — without one, sending a message returns `503`.

Since Day 34, ingestion runs as a Celery task (`app/tasks.py`) instead
of a FastAPI `BackgroundTasks` job, so `POST /repositories` and
`POST /repositories/{id}/reindex` need a Redis instance reachable at
`REDIS_URL` (default `redis://localhost:6379/0` — see "Local
infrastructure" above) and a worker process running:

```bash
celery -A app.core.celery_app worker --loglevel=info --pool=solo
```

`--pool=solo` is required on native Windows — Celery's default
"prefork" pool needs `os.fork()`, which Windows doesn't have. Without a
running worker, `.delay()` calls still succeed (they just publish to
Redis), but queued repositories stay `pending` forever until one
starts — and since Day 38, if Redis itself isn't reachable at all,
`POST /repositories`/`.../reindex` fail fast into a `failed` status
with a clear message instead of hanging the request.

Auth endpoints: `POST /auth/register`, `POST /auth/login` (returns a
JWT), `GET /auth/me` (requires `Authorization: Bearer <token>`).

Repository endpoints (require `Authorization: Bearer <token>`):
`POST /repositories` (body: `{"github_url": "owner/repo"}`, validates
the repo via the GitHub API, creates a `pending` row, and kicks off
background ingestion), `GET /repositories` (paginated list of your own
repositories, newest first — optional `?page=`/`?page_size=`, default
`page=1`/`page_size=10`, max `page_size` 100; returns
`{items, page, page_size, total, total_pages, has_next, has_previous}`),
`GET /repositories/{id}` (single repository, with live
`status`), `DELETE /repositories/{id}` (removes it, cascading to its
files/chunks), `POST /repositories/{id}/reindex` (re-runs ingestion;
`409` if one is already in progress for that repo),
`GET /repositories/{id}/files` (scanned file metadata once ingestion
completes), `GET /repositories/{id}/chunks` (optional `?file_id=`
filter; the line-window chunks generated from each file's content),
`POST /repositories/{id}/search` (body: `{"query": str, "limit":
int}`, default `limit` 10; embeds the query and returns the closest
code chunks from that repository — `content`, `file_path`, line range,
and similarity `score`, highest first), and
`POST /repositories/{id}/files/{file_id}/explain` (no body; reconstructs
that file's original content from its chunks and asks the LLM to
explain it — needs only `ANTHROPIC_API_KEY`, not `OPENAI_API_KEY`,
since no embedding/search is involved; `400` if the file has no chunks
to explain, e.g. a binary file). Set `GITHUB_TOKEN` in `.env` to raise
GitHub's rate limit from 60 to 5000 requests/hour.

After creation, a repository moves through
`pending → cloning → processing → completed` (or `failed`, see
`error_message`) as it's downloaded and its files are scanned; poll
`GET /repositories/{id}` to watch progress.

Conversation endpoints (require `Authorization: Bearer <token>`):
`POST /repositories/{id}/conversations` (body: `{"title": str | null}`)
and `GET /repositories/{id}/conversations` create/list chat threads
scoped to a repository; `POST /conversations/{id}/messages` (body:
`{"content": str}`) is the RAG chat endpoint — it stores your message,
embeds it, searches Qdrant for that repository's closest code chunks,
and asks the LLM (`ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL`, default
`claude-sonnet-5`) to answer strictly from that retrieved context,
returning the assistant's reply plus the `sources` (file path, line
range) it was grounded in; if nothing relevant is indexed yet, it
short-circuits to a canned "couldn't find any indexed code" reply
without calling the LLM. `GET /conversations/{id}/messages` replays
the full thread with each past assistant reply's sources resolved the
same way.

### Rate limiting

Since Day 46, resource-intensive endpoints are rate limited per
authenticated user via a Redis-backed fixed-window counter (reuses
`REDIS_URL`; if Redis itself is unreachable, requests are allowed
through rather than the app failing closed). Exceeding a limit returns
`429` with a `detail` message naming the limit and window. All limits
are configurable via `.env` (see `.env.example`'s "Rate limiting"
section) — defaults:

| Scope | Endpoints | Default |
|---|---|---|
| `ingestion` | `POST /repositories`, `.../reindex` | 20/hour/user |
| `ai` | search, chat, debug, architecture, security-scan, file explain/review | 20/minute/user |
| `agent` | `POST /repositories/{id}/agent` | 5/minute/user |
| `auth_register` | `POST /auth/register` | 30/hour/IP |
| `auth_login` | `POST /auth/login` | 40/hour/IP |

`auth_register`/`auth_login` are IP-based (there's no authenticated
user yet); every other scope is per-user, so one user's usage never
affects another's. `RATE_LIMIT_ENABLED=false` disables all of them.
Set very low for local testing: `curl` a protected endpoint repeatedly
and confirm the `429` after the configured count.

### Embedding Providers

Since Day 51, which model turns code into vectors for semantic search is
configurable (`EMBEDDING_PROVIDER` in `.env`), behind one small interface
(`app/services/embedding_providers.py`: `embed_texts`/`embed_query`) that
every caller — ingestion, search, debug, chat, the agent's tools — goes
through instead of a specific provider directly.

**`EMBEDDING_PROVIDER=openai`** (the default, unchanged since Day 14):
- Requires `OPENAI_API_KEY`; requests return a clean `503` if it's unset
  (never a crash) — see `.env.example`.
- Uses `EMBEDDING_MODEL` (default `text-embedding-3-small`, 1536
  dimensions) via OpenAI's Embeddings API.

**`EMBEDDING_PROVIDER=local`**:
- No OpenAI API key required at all — useful for demoing or developing
  against this project without spending OpenAI credits.
- Runs entirely inside the backend process via
  [fastembed](https://github.com/qdrant/fastembed) (ONNX Runtime, CPU) —
  no PyTorch, chosen specifically to keep the dependency footprint small
  (versus the full `sentence-transformers` + PyTorch stack, which would
  add several times as much).
- **Not installed by default** (Day 51's review): `fastembed` and its
  own dependencies (onnxruntime/onnx/numpy/tokenizers/huggingface_hub,
  roughly 150MB) live in a separate
  [`backend/requirements-local-embedding.txt`](backend/requirements-local-embedding.txt),
  not `backend/requirements.txt` — so the default, `EMBEDDING_PROVIDER=openai`
  install/image never pays for them. To actually use `local`:
  - Native/WSL: `pip install -r backend/requirements-local-embedding.txt`
    instead of `requirements.txt` (`requirements-dev.txt` already includes
    it, since the test suite exercises the local provider for real).
  - Docker Compose `full` profile: set `BACKEND_DOCKER_TARGET=with-local-embedding`
    in `.env` before building — `backend/Dockerfile` has two build
    targets, `production` (default, `requirements.txt` only) and
    `with-local-embedding`; see `.env.example`.
- The model (`LOCAL_EMBEDDING_MODEL`, default
  `sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions) is downloaded
  and cached on first use, not committed to git and not baked into the
  Docker image either way — the very first local-provider request after a
  fresh start pays a one-time download/load cost (observed ~20s cold on
  this project's own dev machine; effectively free after that, cached for
  the life of the process).
- Runs on CPU inside whatever container/process is already running the
  backend, so it needs more of that process's CPU/RAM per request than
  a network call to OpenAI does - there's no separate service to scale.
- **Not claimed to match OpenAI's embedding quality** — no evaluation of
  retrieval quality between the two has been done here; `all-MiniLM-L6-v2`
  is a small, general-purpose sentence-embedding model, not one
  specialized for code.

**Switching providers is not retroactive.** OpenAI's 1536-dimensional
vectors and the local model's 384-dimensional ones cannot coexist in one
Qdrant collection — Qdrant fixes a collection's vector size for its
lifetime, and silently mixing dimensions is exactly what this design
avoids. Each provider gets its **own** Qdrant collection automatically
(`{QDRANT_COLLECTION_NAME}` for `openai` — unchanged, so every existing
deployment's data stays exactly where it is; `{QDRANT_COLLECTION_NAME}_local`
for `local`), and `vector_store.ensure_collection()` also checks an
existing collection's actual vector size before upserting into it,
raising a clear error rather than a cryptic Qdrant one on any mismatch.
Practical effect: a repository ingested under one provider has to be
**re-ingested** (`POST /repositories/{id}/reindex`) after switching
`EMBEDDING_PROVIDER` before it's searchable under the new one — nothing
does this automatically, since re-embedding an entire repository isn't
free and shouldn't happen as a side effect of an env var change.

### Running tests

```bash
cd backend
.venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
```

Tests run against a real Postgres database (`<your DATABASE_URL's
db>_test`), created automatically on first run; each test gets a
freshly recreated schema. GitHub API calls and background ingestion
are stubbed out, so the suite needs no network access and never
touches your dev database.

## Testing & Continuous Integration

Every layer below runs locally with the commands already documented in
"Running tests" and "End-to-end tests" above; this section covers what
[`.github/workflows/ci.yml`](.github/workflows/ci.yml) additionally
validates on every push/PR to `main`, so nothing here duplicates those
setup steps.

| Job | What it validates | Depends on |
|---|---|---|
| `backend` (Backend tests) | The pytest suite, against a Postgres service container. No API keys needed — every external call (OpenAI, Anthropic, GitHub, Qdrant, Celery) is mocked per-test, same as running it locally. | — |
| `frontend` (Frontend typecheck & lint) | `next typegen`, `tsc --noEmit`, `eslint .` | — |
| `e2e` (End-to-end tests) | The full Playwright suite against real Postgres, Redis, and Qdrant service containers plus a real Celery worker, all on the runner. `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` are deliberately left unset — the suite asserts the graceful inline error both produce when missing — so it needs no paid API access to pass. `GITHUB_TOKEN` is the workflow's own automatic token, raising the unauthenticated GitHub API rate limit; no repo secret is configured. | `backend`, `frontend` |
| `docker` (Docker build validation) | Builds the real `backend/Dockerfile` (both the `production` and `with-local-embedding` targets) and `frontend/Dockerfile` images with `docker/build-push-action`, and validates `docker compose -f docker/docker-compose.yml --profile full config`. Images are never pushed anywhere. | — |
| `docker-smoke` (Docker Compose full-stack smoke test) | Starts the real `--profile full` stack (`docker compose ... up -d --build --wait --wait-timeout 180`), runs [`scripts/smoke_check.py`](scripts/smoke_check.py) against it (frontend + `/health` + `/health/ready`), then drives a real Chromium browser through a small production-like flow — register, add a repository, view its detail page, log out, confirm the protected dashboard redirects — against the actual containerized frontend/API/Postgres/Redis. Always tears the stack down (`down -v`), win or lose, and captures `docker compose ps`/`logs` and a Playwright HTML report on failure. | `docker` |

`e2e` depends on `backend`/`frontend` and `docker-smoke` depends on
`docker`, so an obviously broken push fails fast without also paying
for a browser install plus several services, or a full image build
plus a container startup, for something that was never going to pass.
On failure, `e2e` and `docker-smoke` each upload their own Playwright
HTML report as a build artifact.

## Production Deployment

Since Day 47, there's a complete (if generic — no specific cloud target
is assumed) production deployment path: two Dockerfiles, a Celery
worker that reuses the backend's image, and an extended
`docker/docker-compose.yml`. Nothing here changes local development at
all — see "Local infrastructure" above, which is still the primary,
actually-used-day-to-day path on this project's own 8 GB RAM dev
machine. Day 50 re-audited all of it (still no cloud target, still no
Docker Desktop required to validate any of this) and found the
existing design already sound; what follows folds in that audit's
fixes and additions — a corrected migration-safety comment, a fuller
environment-variable reference, documented (not newly invented)
operational defaults, and a small smoke-check script.

### Architecture

```
                    ┌─────────────┐
  browser  ───────► │  frontend   │  Next.js standalone, port 3000
                    └──────┬──────┘
                           │ NEXT_PUBLIC_API_BASE_URL
                           ▼
                    ┌─────────────┐
  reverse   ──────► │     api     │  FastAPI + uvicorn, port 8000
  proxy              └──────┬──────┘
  (your own,                │
   not included)     ┌──────┴───────┬─────────────┐
                      ▼              ▼             ▼
                 ┌─────────┐   ┌─────────┐   ┌──────────┐
                 │postgres │   │  redis  │   │  qdrant  │
                 └─────────┘   └────┬────┘   └──────────┘
                                    │
                              ┌─────┴──────┐
                              │celery-worker│  same image as api,
                              └────────────┘  different command
```

`frontend` and `api` are deployed separately and talk over HTTP; all of
that traffic is server-side (`frontend`'s own Next.js server calling
`api`, not the end user's browser calling it directly), so what matters
is `frontend`'s build getting baked with a URL that's actually reachable
*from inside the frontend container*, not from a developer's host
machine or browser — `docker/docker-compose.yml`'s `full` profile
handles this with its own `COMPOSE_FRONTEND_API_BASE_URL` variable
(defaulting to the Compose-internal `http://api:8000`) precisely so it
doesn't collide with `NEXT_PUBLIC_API_BASE_URL`, which stays whatever
native/WSL development needs instead — see "Frontend deployment" below
for why conflating the two was a real bug. No reverse proxy is
included — TLS termination, domain routing, etc. are deployment-specific
and deliberately out of scope here, but `api`'s uvicorn is already
configured to trust `X-Forwarded-*` headers from one (see below).

### Required environment variables

Every variable is documented with its default and which day introduced
it in [`.env.example`](.env.example) — this is the **one** canonical
copy (`cp .env.example .env` from the repo root, or the equivalent step
in "Getting Started" above); nothing else in this repo defines or
duplicates this contract. **Never commit the real `.env`** — `.gitignore`
already excludes it, `.env.example` itself contains only placeholders
(`changeme`, blank), and this is checked by
`tests/test_deployment_config.py`.

At a glance (Day 52):
- **Required production secrets** (no usable default; the app refuses to
  start without real values — see below): `JWT_SECRET_KEY`,
  `DATABASE_URL`'s password.
- **Conditionally required secrets** (needed only for the feature that
  uses them; each fails with a clean `503`, never a crash, if missing):
  `OPENAI_API_KEY` (only when `EMBEDDING_PROVIDER=openai`),
  `ANTHROPIC_API_KEY` (only when an LLM-backed endpoint actually runs).
- **Optional**: `GITHUB_TOKEN` (raises a rate limit, nothing breaks
  without it), all `RATE_LIMIT_*` variables, `WEB_CONCURRENCY`/
  `FORWARDED_ALLOW_IPS`.
- **Required production service URLs**: `DATABASE_URL`, `REDIS_URL`,
  `QDRANT_HOST`/`QDRANT_PORT`.
- **Public, not secret**: `NEXT_PUBLIC_API_BASE_URL` and
  `COMPOSE_FRONTEND_API_BASE_URL` — Next.js inlines every
  `NEXT_PUBLIC_*` variable into the JavaScript actually shipped to the
  browser, so neither one may ever hold a secret (only ever a URL here).

This table (Day 50) is the consolidated production-audit view of the
same file: **when** each value is read matters as much as what it's for.

| Variable | When read | Required in production |
|---|---|---|
| `ENVIRONMENT` | Runtime (backend process start) | Yes — must be `production` to enable the guards below |
| `JWT_SECRET_KEY` | Runtime | **Yes, real value** — app refuses to start with the placeholder or empty |
| `DATABASE_URL` | Runtime | **Yes, real value** — app refuses to start with the placeholder `changeme` password |
| `CORS_ORIGINS` | Runtime | Recommended — logs a warning (not a hard failure) if left at the localhost default |
| `GITHUB_TOKEN` | Runtime | Optional — raises GitHub's rate limit from 60/hr to 5000/hr |
| `LLM_PROVIDER` / `ANTHROPIC_MODEL` / `EMBEDDING_MODEL` | Runtime | Optional (defaults are usable) |
| `OPENAI_API_KEY` | Runtime | Yes, for search/chat/debug/etc. to work at all — endpoints return a clean `503` (not a crash) if unset |
| `ANTHROPIC_API_KEY` | Runtime | Yes, same reasoning as `OPENAI_API_KEY` |
| `REDIS_URL` | Runtime | Yes — Celery broker and Day 46's rate limiting both need it; rate limiting fails open (not closed) if unreachable |
| `QDRANT_HOST` / `QDRANT_PORT` / `QDRANT_COLLECTION_NAME` | Runtime | Yes — search/chat/debug depend on it; see the Qdrant note below |
| `RATE_LIMIT_*` (ten variables) | Runtime | Optional (defaults are usable); `RATE_LIMIT_ENABLED=false` disables all of them |
| `WEB_CONCURRENCY` / `FORWARDED_ALLOW_IPS` | Runtime, but read by `backend/docker-entrypoint.sh` directly, **not** by the FastAPI app itself | Optional (defaults are usable) |
| `NEXT_PUBLIC_API_BASE_URL` | **Build-time only** — inlined into the frontend's compiled output; changing it after the image is built has no effect | Native/WSL development only — see "Frontend deployment" below |
| `COMPOSE_FRONTEND_API_BASE_URL` | **Build-time only**, same mechanism | Docker Compose `full` profile only; leave blank to use the correct default |

Two secrets deliberately have **no usable default** anywhere in this
stack (`.env.example`'s placeholders, `docker/docker-compose.yml`'s
`full` profile): `JWT_SECRET_KEY` and `DATABASE_URL`'s password —
generate a real `JWT_SECRET_KEY` with
`python -c "import secrets; print(secrets.token_urlsafe(48))"`. Every
other variable above has a default that's fine for local development
but should be reviewed before a real deployment.

**Qdrant limitation (Day 50):** `app/services/vector_store.py` talks to
Qdrant over plain HTTP with no API key/auth support at all — fine for a
self-hosted Qdrant container (as `docker/docker-compose.yml` runs), but
this codebase does **not** currently support a hosted Qdrant Cloud
instance that requires an API key. Adding that is real feature work,
not a config change, and is explicitly out of scope here.

### Backend deployment

[`backend/Dockerfile`](backend/Dockerfile) — single-stage `python:3.9-slim`
(matches the version this project has been built and verified against
throughout), runs as a non-root user. On start,
[`backend/docker-entrypoint.sh`](backend/docker-entrypoint.sh) runs
`alembic upgrade head` (idempotent — safe on every deploy) and then execs:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  --workers "${WEB_CONCURRENCY:-2}" \
  --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}"
```

No gunicorn — uvicorn's own `--workers` flag already gives multi-process
concurrency, so adding gunicorn on top would be an unnecessary
dependency for what it actually buys here. `--proxy-headers` plus
`--forwarded-allow-ips` is what "runs behind a reverse proxy" actually
means in practice: without it, every request would appear to originate
from the proxy's own IP, which would also silently break Day 46's
per-IP `auth_register`/`auth_login` rate limiting (everyone behind the
proxy would share one counter).

**Migration safety scope (Day 50):** "safe on every deploy" above means
safe for the single `api` container/replica this Compose file actually
runs — `alembic upgrade head` records its progress in a plain table,
not a real distributed lock, so running *multiple* `api` replicas that
each execute this entrypoint concurrently could race each other. Add a
real lock (e.g. a Postgres advisory lock in `alembic/env.py`) before
ever scaling `api` beyond one replica; nothing here claims that safety
today.

### Frontend deployment

[`frontend/Dockerfile`](frontend/Dockerfile) — multi-stage, built on
Next's own `output: "standalone"` (`next.config.ts`), which traces only
the files actually needed to run in production (including select
`node_modules`) into `.next/standalone`; the final image needs neither
the full `node_modules` install nor the rest of the source tree. Base
image is `node:20-slim`, not alpine — this project's dependencies
include native Rust bindings (Tailwind v4's oxide engine, lightningcss)
published as per-platform prebuilt binaries, and glibc has the
broadest, best-tested coverage for those.

The one real gotcha: `NEXT_PUBLIC_API_BASE_URL` is inlined into the
compiled output by Next.js itself at *build* time, not read from the
container's environment at *start* time — so it has to be a Docker
build arg, not just a runtime variable, or the running frontend would
silently keep whatever value was baked in when the image was built:

```bash
docker build --build-arg NEXT_PUBLIC_API_BASE_URL=https://api.example.com \
  -t repomind-frontend frontend/
```

A second, sharper version of that same gotcha (Day 47's review):
building this image as part of `docker/docker-compose.yml`'s `full`
profile must **not** reuse `.env`'s `NEXT_PUBLIC_API_BASE_URL` directly
— that variable is correct for native/WSL development (frontend and
backend as separate processes on the same host, where `localhost:8000`
really does reach the backend), but every API call this app makes
happens server-side, inside whichever process is running the frontend.
Built with `localhost:8000` baked in and run as its own Compose
container, the frontend would try to reach `localhost:8000` *from
inside itself* — nothing listens there, since `api` is a separate
container — and every Server Component and Server Action would fail
silently. `docker-compose.yml` avoids this with its own
`COMPOSE_FRONTEND_API_BASE_URL` variable instead, defaulting to the
Compose-internal `http://api:8000` so this works correctly with zero
configuration; see `.env.example`'s comment on that variable, and the
`frontend` service's own `build.args` comment, for the full reasoning.

Verified locally without Docker (see "Running tests" above): a real
`npm run build` succeeds and produces `.next/standalone/server.js`,
exercising the exact build step the Dockerfile's builder stage runs.

### Celery worker deployment

No separate image — [`docker/docker-compose.yml`](docker/docker-compose.yml)'s
`celery-worker` service builds from the *same* `backend/Dockerfile`,
overrides its `ENTRYPOINT` (which is api-specific: migrations + uvicorn)
to empty, and runs:

```bash
celery -A app.core.celery_app worker --loglevel=info
```

No `--pool=solo` — that's Day 34's Windows-only workaround for the lack
of `os.fork()`; Linux's default `prefork` pool works as-is inside a
container (same reasoning Day 44's CI already established for the
worker it starts there). Uses the exact same Redis broker config
(`REDIS_URL`) and sees the exact same rate-limiting settings as `api`
(Day 46's config is a Redis-backed check inside request handling, not
something the worker itself needs to know about — it just needs the
same `REDIS_URL` to reach the same Redis).

**Concurrency (Day 50):** no `--concurrency=N` flag is set, so Celery
uses its own default (one process per CPU core, prefork pool). That's
a reasonable starting point, not a value this project has load-tested —
if `ingest_repository` throughput ever actually becomes a bottleneck,
override it directly in `docker/docker-compose.yml`'s `celery-worker`
`command:` (e.g. `["celery", "-A", "app.core.celery_app", "worker",
"--loglevel=info", "--concurrency=4"]`) rather than guessing a number
here ahead of any evidence it's needed.

### PostgreSQL, Redis, and Qdrant

Same requirements as local development — see "Local infrastructure"
above for what each is used for. In production, run them as managed
services or long-lived containers with real persistent volumes/backups;
`docker/docker-compose.yml`'s `postgres`/`redis`/`qdrant` services are
adequate for a small, single-host deployment but don't include backup
automation, replication, or TLS between services — add those
separately for anything beyond that scale.

**Connection pool sizing (Day 50):** `app/core/database.py` creates its
async engine with no explicit `pool_size`/`max_overflow`, so it uses
SQLAlchemy's defaults (5 + 10 = 15 connections, per process). Each of
`api`'s `WEB_CONCURRENCY` uvicorn worker *processes* gets its own pool
(they don't share one), plus one more from the Celery worker process —
so a default `WEB_CONCURRENCY=2` deployment can open up to roughly
`2 × 15 + 15 = 45` Postgres connections at once. Postgres's own default
`max_connections` is 100, so this is comfortable out of the box, but
size accordingly if `WEB_CONCURRENCY` is raised well beyond 2.

### Docker Compose deployment

The same file Day 40 introduced for local infra now also represents
the complete stack, gated behind a Compose profile so the original
infra-only behavior is completely unchanged by default:

```bash
# Infra only (unchanged since Day 40):
docker compose -f docker/docker-compose.yml up -d

# The complete stack - postgres, redis, qdrant, api, celery-worker, frontend:
docker compose -f docker/docker-compose.yml --env-file .env --profile full up -d

# Same command CI's docker-smoke job actually runs (Day 54) - blocks
# until every service with a healthcheck reports healthy, or fails
# after 180s instead of hanging indefinitely:
docker compose -f docker/docker-compose.yml --env-file .env --profile full \
  up -d --build --wait --wait-timeout 180

# Stop and remove the stack (add -v to also delete the named volumes -
# Postgres/Redis/Qdrant data - and lose all local data):
docker compose -f docker/docker-compose.yml --profile full down
docker compose -f docker/docker-compose.yml --profile full down -v
```

Create that real `.env` the same way as for local development —
`cp .env.example .env`, then fill in real values, `ENVIRONMENT=production`
included — never commit it. Secrets (`JWT_SECRET_KEY`, `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `GITHUB_TOKEN`) have no fallback in the `full`
profile's services, unlike `POSTGRES_PASSWORD`'s local-dev-only
`changeme` default — they must come from that real `.env` (`--env-file
.env`, since Compose only auto-loads a `.env` file next to the compose
file, not the project root's) or from your deployment environment's own
secret injection (e.g. a platform's secret manager exporting the same
variable names) — either way, never hardcoded into `docker-compose.yml`
itself.

### Verifying a deployment (Day 50)

After bringing up the `full` profile (or any other deployment of this
stack — native/WSL, or a future cloud target), check it actually works
with:

```bash
# Everything defaults to localhost - matches the full Compose profile's
# own published ports (frontend :3000, api :8000).
python scripts/smoke_check.py

# Against a real deployment elsewhere:
python scripts/smoke_check.py --frontend-url https://app.example.com --api-url https://api.example.com
```

[`scripts/smoke_check.py`](scripts/smoke_check.py) is a small,
dependency-free script (Python standard library only — no `pip install`
needed) that makes three plain GET requests — the frontend's own URL,
`GET /health`, and `GET /health/ready` — and prints one line per check
plus a nonzero exit code if anything failed. It never sends anything
but GET requests, so it's safe to run against a live deployment at any
time. `backend/tests/test_smoke_check.py` covers its logic directly
against a local fake server (no live deployment needed for that).

To check each piece manually instead:

```bash
curl -s http://localhost:8000/health          # liveness
curl -s http://localhost:8000/health/ready    # readiness (503 if Postgres is unreachable)
curl -sI http://localhost:3000                # frontend responds at all
docker compose -f docker/docker-compose.yml --profile full ps   # every service "healthy"?
docker compose -f docker/docker-compose.yml --profile full logs celery-worker --tail 50
```

### Health / readiness checks

- `GET /health` — liveness: is the process up at all. No dependency
  checks, no auth needed.
- `GET /health/ready` — readiness (Day 47): can the process actually
  serve requests right now. Checks Postgres reachability only (with an
  explicit timeout, so an unreachable — not just erroring — database
  can't hang the check), deliberately not Redis/Qdrant: both already
  degrade gracefully when unreachable (Days 38-43 — ingestion fails
  fast into `status: "failed"`, rate limiting fails open, search/chat
  map to a clean `502`/`503`), so reporting "not ready" for either would
  flag a condition that doesn't actually block most requests. Neither
  endpoint ever includes a connection string, credential, or other
  infrastructure detail in its response.

`docker/docker-compose.yml`'s `api`/`celery-worker`/`frontend` services
all have their own `healthcheck:` blocks, each probing with a tool
already guaranteed present in that image rather than installing one
just for this (Python's `urllib` for `api`, Node's `http` module for
`frontend`, `celery inspect ping` for the worker) — the same "no
guaranteed shell tools" reasoning Day 40 already established for why
Qdrant's own service has no healthcheck at all.

### Production security checklist

Actual safeguards already in this codebase, not aspirational ones —
check each before a real deployment:

- [ ] **Real `JWT_SECRET_KEY`** — `app/core/config.py`'s `Settings`
      refuses to construct at all if `ENVIRONMENT=production` and this
      is empty or still the placeholder default. Generate one with
      `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
- [ ] **Real `DATABASE_URL` password** — same startup guard rejects the
      placeholder `changeme` password once `ENVIRONMENT=production`.
- [ ] **Secrets only via environment variables, never hardcoded** —
      `docker/docker-compose.yml`'s `full` profile gives
      `JWT_SECRET_KEY`/`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`/`GITHUB_TOKEN`
      no local-dev-style fallback default; they must come from a real
      `.env` or your platform's secret injection.
- [ ] **No secrets committed to git** — `.gitignore` excludes `.env`,
      `.env.example` holds only placeholders, and
      `backend/tests/test_secret_safety.py`/`test_deployment_config.py`
      check this directly rather than by convention alone.
- [ ] **`CORS_ORIGINS` set to your real frontend origin(s)** — left at
      the localhost default it only logs a warning (not a hard
      failure — a same-origin/reverse-proxy setup may not need CORS at
      all), so it's easy to forget in a real deployment.
- [ ] **`ENVIRONMENT=production` actually set** — it's what turns on
      every guard above, and disables SQL echo logging
      (`database.py`) regardless of `LOG_LEVEL`.
- [ ] **No sensitive data in logs** — checked directly, not assumed: no
      code anywhere logs an `Authorization` header, JWT, or password.
- [ ] **Containers run as non-root** — both `backend/Dockerfile` and
      `frontend/Dockerfile` create and switch to an unprivileged user.
- [ ] **`GITHUB_TOKEN` only if needed** — optional; only raises the
      GitHub API rate limit, nothing depends on it being set.
- [ ] **External AI provider credentials scoped and real** —
      `OPENAI_API_KEY` (only if `EMBEDDING_PROVIDER=openai`) and
      `ANTHROPIC_API_KEY` are read only at request time; a missing one
      returns a clean `503`, never a crash, but the corresponding
      feature won't work without it.
- [ ] **`/health`/`/health/ready` never leak infrastructure details** —
      no connection string, credential, or hostname in either response
      (`tests/test_health.py`).
- [ ] **HTTPS/TLS termination** — **not included** in this repository;
      `uvicorn` runs plain HTTP and trusts `X-Forwarded-*` from a
      reverse proxy you provide (nginx, Caddy, Traefik, your cloud
      provider's load balancer) via `FORWARDED_ALLOW_IPS`. Terminate
      TLS at that proxy, not here.
- [ ] Per-user/per-IP rate limiting (Day 46) and fail-fast/graceful
      degradation for every external dependency (Days 38-43) apply
      unchanged in production — neither is dev-only behavior.

### Native/WSL fallback for resource-constrained machines

Nothing above requires Docker. Every piece — the backend, the Celery
worker, the frontend, Postgres, Redis, Qdrant — can run exactly as
"Getting Started" and "Local infrastructure" already document, just
with `ENVIRONMENT=production`-appropriate values in `.env` and a real
reverse proxy (nginx, Caddy, Traefik, your cloud provider's load
balancer — anything that can set `X-Forwarded-*` headers) in front of
`uvicorn`/`next start`. This is not a hypothetical: it's the same
Option B this project's own README has documented since Day 40, for
the same reason — Docker Desktop's resource overhead is a real cost on
an 8 GB RAM machine, in production every bit as much as in local dev.

## Troubleshooting

Practical fixes for actual failure modes this project already handles
or has hit, not a generic checklist:

| Symptom | Likely cause / what to check |
|---|---|
| `api` container never becomes healthy | Check `docker compose -f docker/docker-compose.yml --profile full logs api`. The most common cause: `ENVIRONMENT=production` with `JWT_SECRET_KEY` or `DATABASE_URL`'s password still at its placeholder — `app/core/config.py`'s startup guard raises during `alembic upgrade head` (the entrypoint's first command), so `uvicorn` never starts and the healthcheck (`GET /health`) has nothing to reach. See "Required environment variables" above. |
| Database migration/startup fails | Confirm `DATABASE_URL` is correct and Postgres is actually reachable — `api`/`celery-worker` both `depends_on: postgres: condition: service_healthy`, so a wrong password/host is the usual cause once Postgres itself is up. Run `alembic upgrade head` manually (see "Backend" above) to see the real error outside a container. |
| Redis unavailable | `POST /repositories`/`.../reindex` fail fast into `status: "failed"` with a clear "background worker is unreachable" message rather than hanging (Day 38). Rate limiting fails **open** (requests allowed through), not closed, if Redis is unreachable — it won't block traffic, but limits stop being enforced. |
| Qdrant unavailable | No healthcheck on the `qdrant` service by design — its image has no shell tools to probe with. Check manually: `curl http://localhost:6333/collections`. Search/chat/debug map an unreachable Qdrant to a clean `502`/`503`, not a crash. |
| Ingestion ends in `failed` — OpenAI missing/quota | Expected without a real `OPENAI_API_KEY` (or with one that's exhausted) when `EMBEDDING_PROVIDER=openai` — the repository's `error_message` names it directly. Either set a real key, or switch to `EMBEDDING_PROVIDER=local` (see "Embedding Providers") to avoid needing one at all. |
| Chat/explain/review/etc. return `503` | `ANTHROPIC_API_KEY` is unset or invalid — every LLM-backed endpoint returns a clean `503` rather than crashing when it's missing. |
| Repository stuck in `pending` forever | No Celery worker is running. `.delay()` calls succeed either way (they just publish to Redis) — start one: `celery -A app.core.celery_app worker --loglevel=info` (add `--pool=solo` on native Windows; see "Backend" above). |
| Port already in use (3000/8000/5432/6379/6333) | Something else on the host is already bound to it. For Compose, override via `.env` (`BACKEND_PORT`, `POSTGRES_PORT`, `REDIS_PORT`, `QDRANT_PORT`); for native processes, pass the equivalent flag/env var to that tool directly. |
| Docker isn't available on this machine | Not a hard requirement — use Option B (native/WSL Postgres/Redis/Qdrant) from "Local infrastructure" above; every other piece of the stack already runs the same way regardless of Docker. |
| Setting up local embeddings (`EMBEDDING_PROVIDER=local`) | Native/WSL: `pip install -r backend/requirements-local-embedding.txt` instead of `requirements.txt`. Docker: set `BACKEND_DOCKER_TARGET=with-local-embedding` in `.env` before `--build`ing. See "Embedding Providers" above for the full explanation — including that switching providers is not retroactive for already-ingested repositories. |

## Project Status

As of the Day 55 commit (`b655058`, GitHub Actions Run #15), the CI
pipeline described in "Testing & Continuous Integration" above is fully
green end to end:

- Backend pytest suite, frontend typecheck/lint, and the full Playwright
  e2e suite all pass.
- Both backend Docker image targets (`production` and
  `with-local-embedding`) and the frontend Docker image build
  successfully in GitHub Actions, and `docker compose ... config`
  validates cleanly.
- The complete `--profile full` Docker Compose stack (Postgres, Redis,
  Qdrant, API, Celery worker, frontend) starts and every service with a
  healthcheck reports healthy, purely from CI's own `--wait` gate —
  no fixed sleep.
- `/health` and `/health/ready` both pass via `scripts/smoke_check.py`
  against the running containers.
- A real Chromium browser, driven by Playwright, successfully
  registers a user, adds a repository, views its detail page, logs
  out, and confirms the protected dashboard redirects to `/login` —
  all against the actual containerized frontend/API/Postgres/Redis,
  not a mock.
- The stack is torn down cleanly (`down -v`) afterward every time.

This full verification happens in **GitHub Actions**, not on this
project's own development machine — Docker itself isn't installed
there (see "Native/WSL fallback" above for why), so nothing in this
README claims the Docker Compose stack was run or tested locally.
Day-to-day development and the commands throughout this README are
verified the native/WSL way instead.
