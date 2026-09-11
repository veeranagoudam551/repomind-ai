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
cp .env.example ../.env     # shared with the frontend; edit DATABASE_URL to point at your local Postgres
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

## Continuous Integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push
and pull request against `main`, as three jobs:

- **Backend tests** — the pytest suite above, against a Postgres service
  container. No API keys needed: every external call (OpenAI, Anthropic,
  GitHub, Qdrant, Celery) is mocked per-test, the same as running it
  locally.
- **Frontend typecheck & lint** — `next typegen`, `tsc --noEmit`,
  `eslint .`.
- **End-to-end tests** — the full Playwright suite (see below) against
  Postgres, Redis, and Qdrant service containers plus a real Celery
  worker, all on the runner itself. `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`
  are deliberately left unset — the suite already asserts the graceful
  inline error both produce when missing (Days 19-37), so it needs no
  paid API access to pass. `GITHUB_TOKEN` is the workflow's own automatic
  token, used only to raise the unauthenticated GitHub API rate limit;
  no repo secret needs to be configured.

The e2e job depends on the other two, so an obviously broken push fails
fast without also paying for browser install + three services. On
failure, the Playwright HTML report is uploaded as a build artifact.

## Production Deployment

Since Day 47, there's a complete (if generic — no specific cloud target
is assumed) production deployment path: two Dockerfiles, a Celery
worker that reuses the backend's image, and an extended
`docker/docker-compose.yml`. Nothing here changes local development at
all — see "Local infrastructure" above, which is still the primary,
actually-used-day-to-day path on this project's own 8 GB RAM dev
machine.

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
it in [`.env.example`](.env.example) — copy it to `.env` and fill in
real values. The ones that matter specifically for a production
deployment, beyond everything already covered elsewhere in this README:

- `ENVIRONMENT=production` — enables the startup guard described under
  "Production security considerations" below, and disables SQL echo
  logging regardless of `LOG_LEVEL`.
- `JWT_SECRET_KEY` — **must** be a real random value in production; the
  app refuses to start otherwise. Generate one with
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
- `DATABASE_URL` — must not contain the placeholder `changeme` password
  once `ENVIRONMENT=production`, same reasoning.
- `CORS_ORIGINS` — set to the real frontend origin(s), comma-separated.
- `NEXT_PUBLIC_API_BASE_URL` — the frontend's build-time (not
  runtime — see the frontend Dockerfile) reference to the backend's
  public URL.
- `WEB_CONCURRENCY` / `FORWARDED_ALLOW_IPS` — read by
  `backend/docker-entrypoint.sh`; see `.env.example`'s "Production
  deployment" section.

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

### PostgreSQL, Redis, and Qdrant

Same requirements as local development — see "Local infrastructure"
above for what each is used for. In production, run them as managed
services or long-lived containers with real persistent volumes/backups;
`docker/docker-compose.yml`'s `postgres`/`redis`/`qdrant` services are
adequate for a small, single-host deployment but don't include backup
automation, replication, or TLS between services — add those
separately for anything beyond that scale.

### Docker Compose deployment

The same file Day 40 introduced for local infra now also represents
the complete stack, gated behind a Compose profile so the original
infra-only behavior is completely unchanged by default:

```bash
# Infra only (unchanged since Day 40):
docker compose -f docker/docker-compose.yml up -d

# The complete stack - postgres, redis, qdrant, api, celery-worker, frontend:
docker compose -f docker/docker-compose.yml --env-file .env --profile full up -d
```

Secrets (`JWT_SECRET_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GITHUB_TOKEN`) have no fallback in the `full` profile's services,
unlike `POSTGRES_PASSWORD`'s local-dev-only `changeme` default — they
must come from your real `.env` (`--env-file .env`, since Compose only
auto-loads a `.env` file next to the compose file, not the project
root's).

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

### Production security considerations

- **Insecure defaults can't reach production silently.**
  `app/core/config.py`'s `Settings` refuses to construct at all if
  `ENVIRONMENT=production` and `JWT_SECRET_KEY` is empty or still the
  placeholder default, or `DATABASE_URL` still contains the placeholder
  `changeme` password — the app won't start, rather than starting with
  a forgeable JWT secret or a guessable database password. `CORS_ORIGINS`
  left at its localhost default logs a warning (not a hard failure — a
  same-origin/reverse-proxy setup may not need CORS at all).
- **Secrets only ever come from environment variables** — never
  hardcoded, and `docker/docker-compose.yml`'s `full` profile services
  give real secrets no local-dev-style fallback default.
- **Logging was audited, not just assumed safe**: SQL echo logging
  (`database.py`) is already gated to `ENVIRONMENT=development` only, so
  production never logs full queries/parameters. No code anywhere logs
  an `Authorization` header, JWT, or password — checked directly rather
  than inferred, since this project has repeatedly found real bugs by
  actually checking rather than assuming (Days 38-45's whole run of
  cross-platform/connection-handling fixes).
- **`/health`/`/health/ready` never leak infrastructure details** — no
  connection string, credential, or hostname in either response, by
  design and covered by tests (`tests/test_health.py`).
- Everything from Day 46 (per-user/per-IP rate limiting) and Days 38-43
  (fail-fast/graceful-degradation for every external dependency) applies
  unchanged in production — none of it is a dev-only behavior.

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
