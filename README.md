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

Visit `http://localhost:3000`. There is no database or auth wired up
yet (see the architecture doc for the current phase), so pages are
mostly static placeholders.

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

Auth endpoints: `POST /auth/register`, `POST /auth/login` (returns a
JWT), `GET /auth/me` (requires `Authorization: Bearer <token>`).

Repository endpoints (require `Authorization: Bearer <token>`):
`POST /repositories` (body: `{"github_url": "owner/repo"}`, validates
the repo via the GitHub API, creates a `pending` row, and kicks off
background ingestion), `GET /repositories` (lists your own
repositories), `GET /repositories/{id}` (single repository, with live
`status`), `GET /repositories/{id}/files` (scanned file metadata once
ingestion completes), and `GET /repositories/{id}/chunks` (optional
`?file_id=` filter; the line-window chunks generated from each file's
content, ready for embedding once Qdrant lands). Set `GITHUB_TOKEN` in
`.env` to raise GitHub's rate limit from 60 to 5000 requests/hour.

After creation, a repository moves through
`pending → cloning → processing → completed` (or `failed`, see
`error_message`) as it's downloaded and its files are scanned; poll
`GET /repositories/{id}` to watch progress.

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
