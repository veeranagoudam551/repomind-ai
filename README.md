# RepoMind AI

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
files shown under each reply.

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
`http://localhost:8000/docs` for interactive API docs. Requires a
running PostgreSQL instance with a database matching `DATABASE_URL`
(create it yourself, e.g. `createdb repomind_ai`) — there is no
Docker Compose service for it yet.

Ingestion also embeds every code chunk and stores the vectors in Qdrant
(`QDRANT_HOST`/`QDRANT_PORT`, default `localhost:6333`), so a local
Qdrant instance needs to be running too, e.g.
`docker run -p 6333:6333 qdrant/qdrant`. The collection
(`QDRANT_COLLECTION_NAME`) is created automatically on first use. An
`OPENAI_API_KEY` is also required — without one, ingestion fails at
the embedding step and the repository is marked `failed` with that
error message. Chatting in a conversation additionally requires an
`ANTHROPIC_API_KEY` — without one, sending a message returns `503`.

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
and `POST /repositories/{id}/search` (body: `{"query": str, "limit":
int}`, default `limit` 10; embeds the query and returns the closest
code chunks from that repository — `content`, `file_path`, line range,
and similarity `score`, highest first). Set `GITHUB_TOKEN` in `.env`
to raise GitHub's rate limit from 60 to 5000 requests/hour.

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
