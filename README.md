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
background ingestion), `GET /repositories` (lists your own
repositories), `GET /repositories/{id}` (single repository, with live
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
