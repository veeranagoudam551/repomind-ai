# RepoMind AI

[![CI](https://github.com/veeranagoudam551/repomind-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/veeranagoudam551/repomind-ai/actions/workflows/ci.yml)

**Autonomous AI Codebase Intelligence Platform**

## What is RepoMind AI?

RepoMind AI is an autonomous AI codebase intelligence platform: it
ingests a GitHub repository, indexes its source code for semantic
retrieval, and uses Retrieval-Augmented Generation (RAG) plus a
LangGraph multi-step agent to help a developer understand, review,
debug, analyze, and secure that codebase — grounded in the actual
source, not guesswork. See [docs/architecture.md](docs/architecture.md)
for the full system design.

## The Problem

Understanding an unfamiliar codebase is slow: reading through files by
hand, grepping for context, and re-deriving how pieces fit together
before you can safely make a change or answer a question about it.
Generic chatbots don't help much either — without real access to a
repository's actual code, they either can't answer or start guessing.
RepoMind AI closes that gap: point it at a public GitHub repository and
it answers questions, reviews files, diagnoses bugs, and investigates
multi-step goals — every answer grounded in that repository's actual
indexed source, with citations back to the files and lines it used.

## Key Features

Everything below is implemented and covered by the automated test
suite — see "Testing & Verification" further down for the current
test/CI counts.

- **RAG-based codebase chat** — ask questions and get answers grounded
  in a repository's actual indexed code, with cited source files
- **Semantic code search** — vector search across a repository's code
  chunks, no LLM round-trip required
- **AI code explanation** — a plain-language explanation of a single file
- **AI code review** — bugs, security issues, edge cases, and code
  smells for a single file
- **AI debugging** — diagnoses a described bug using the most relevant
  retrieved code as context
- **Architecture analysis** — infers likely components, tech stack, and
  entry points from a repository's file tree and README
- **Static security scanning** — regex-based detection of hardcoded
  secrets, `eval`/`exec`, shell injection, insecure deserialization, and
  more; no LLM call and no API key required
- **LangGraph multi-step autonomous agent** — given a goal, plans and
  executes up to `max_steps` (1–10, default 4) tool calls before
  answering, choosing among six fixed tools on each turn:
  1. `search_code` — semantic search
  2. `explain_file` — explain one file
  3. `review_file` — review one file
  4. `debug` — diagnose a described bug
  5. `architecture` — analyze repository structure
  6. `security_scan` — scan for common security issues

  The agent's tool set is exactly these six — it cannot call anything
  outside them — and every tool call is scoped to the same
  repository/user the original request was authorized for. The planner
  uses the LLM provider's **native tool-calling API** (Groq/OpenAI-style
  `tools`/`tool_calls`, or Anthropic's `tool_use`/`tool_result` blocks) —
  not a hand-rolled JSON-in-text convention — so tool selection is
  structured and validated by the provider itself. Repository-derived
  content fed back into the planner (tool results) is explicitly wrapped
  as untrusted data, never as an instruction, and per-run duplicate code
  chunks are suppressed so an investigation doesn't resend the same
  snippet twice. See "LLM Providers" below for which provider actually
  answers these calls and its current, honestly-documented limitations.

## Architecture

Two different diagrams, for two different questions:

**How a question gets answered** — the conceptual data flow behind
chat, search, debug, and the agent's `search_code` tool:

```
GitHub Repository
        ↓
Repository Ingestion        (clone, scan — Celery worker, background)
        ↓
File Parsing & Chunking     (overlapping line-window chunks)
        ↓
Embeddings                  (OpenAI, or local fastembed — one or the
        ↓                    other, never both at once)
Qdrant Vector Store         (one collection per embedding provider)
        ↓
Semantic Retrieval          (cosine similarity, scoped to one repository)
        ↓
RAG / AI Tools              (chat, search, debug, explain, review,
        ↓                    architecture, security-scan)
Groq or Anthropic /          (grounded answer, or up to max_steps native
LangGraph Agent      ↓       tool calls before one)
Developer Answer
```

The top half (ingestion through Qdrant) runs once per repository, in
the background, via a Celery worker (see "Repository Processing
Pipeline" in [docs/architecture.md](docs/architecture.md) for the full
pipeline). The bottom half (retrieval through the developer's answer)
runs per-request, synchronously, inside a FastAPI request handler —
nothing in it is precomputed or cached beyond what's already in
Qdrant/Postgres from ingestion.

**Container / deployment topology** — which process talks to which
service. This is the *production* topology (Caddy in front); running
locally without Docker is simpler — see "How to Run Locally" below,
which is the primary, actually-used-day-to-day way this project is
developed and demoed.

```
Internet
   │
   │  80 / 443 only — the single public entry point
   ▼
 Caddy                reverse proxy, automatic HTTPS (real domain) or
   │                  plain HTTP (local testing) — see "Optional:
   │                  Production Deployment" below
   ├──────────────┬─────────────┐
   ▼              ▼             │
Frontend        FastAPI         │ (server-side calls from the frontend
(Next.js,       (backend,       │  to the backend also go over this
 :3000)          :8000)         │  same internal network, not through
                   │             ▼  Caddy — see "Deployment
   ┌───────────────┼──────────────  Architecture" below)
   ▼               ▼              ▼
PostgreSQL       Redis          Qdrant
(users,          (Celery        (code-chunk vectors)
 repos, files,    broker +
 chunks,          rate-limit
 conversations)   counters)
   │               │
   │               ▼
   │          Celery worker
   │               │
   └───────────────┴──► background ingestion (clone → scan → chunk → embed)
```

Every service except Caddy is reachable only over the Docker-internal
network — never directly from the host or the public internet. This
container topology, including Caddy, is present in the repository and
CI-verified (see "Optional: Production Deployment" below), but **is not
required to run or demo RepoMind AI locally** — local development never
needs Caddy, TLS, or any of this network isolation at all.

Embeddings (turning code/chat text into vectors) happen in two places
that share one interface (`app/services/embedding_providers.py`):
repository ingestion (Celery worker, writing to Qdrant) and every
search/chat/debug request (FastAPI, embedding the query to search
Qdrant) — see "Embedding Providers" below for the OpenAI vs. local
choice. LLM calls (Groq or Anthropic, selected by `LLM_PROVIDER` — see
"LLM Providers" below — via LangGraph's native tool calling for the
multi-step agent) happen only in FastAPI request handlers that need
one: chat, explain, review, architecture, security-scan, and the
agent — never during ingestion itself.

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js, TypeScript, Tailwind CSS, shadcn/ui, TanStack Query |
| Backend | Python, FastAPI, Pydantic, SQLAlchemy |
| Relational DB | PostgreSQL |
| Vector DB | Qdrant |
| AI / RAG | Groq or Anthropic Claude (chat, explain/review/debug/architecture, agent reasoning — one provider active at a time via `LLM_PROVIDER`, see "LLM Providers" below); OpenAI embeddings (default) or local fastembed/`all-MiniLM-L6-v2` (optional — one embedding provider active at a time, never both simultaneously); LangGraph (multi-step agent orchestration, native tool calling) |
| Background jobs | Redis + Celery |
| Infra | Docker, Docker Compose; Caddy (reverse proxy / automatic TLS — optional, production deployment only, see below) |

## Project Structure

```
repomind-ai/
├── frontend/     # Next.js app
├── backend/      # FastAPI app
├── docs/         # Architecture & design docs
├── docker/       # Docker Compose, Dockerfiles, Caddy config
├── .env.example  # Full environment variable contract
└── README.md
```

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
| Docker Engine + Compose v2 | any recent version supporting `docker compose` (not the standalone v1 `docker-compose` binary) | Only if using Docker for infra or the full stack — see "Local infrastructure" below and "Optional: Production Deployment" |

Postgres/Redis/Qdrant can each be run natively/via WSL instead of
Docker — see "Local infrastructure" below; Docker is never a hard
requirement of the application itself.

## How to Run Locally

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

Ingestion runs as a Celery task (`app/tasks.py`) rather than a FastAPI
`BackgroundTasks` job, so `POST /repositories` and
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
starts — and if Redis itself isn't reachable at all,
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
and similarity `score`, highest first),
`POST /repositories/{id}/files/{file_id}/explain` (no body; reconstructs
that file's original content from its chunks and asks the LLM to
explain it — needs only `ANTHROPIC_API_KEY`, not `OPENAI_API_KEY`,
since no embedding/search is involved; `400` if the file has no chunks
to explain, e.g. a binary file),
`POST /repositories/{id}/files/{file_id}/review` (same shape as
`explain` above, but asks the LLM for a code review — bugs, security
issues, edge cases, code smells — instead of an explanation; same
`ANTHROPIC_API_KEY`-only requirement and `400`-on-no-content behavior),
`POST /repositories/{id}/debug` (body: `{"description": str}`; embeds
the description, retrieves the closest matching code chunks from that
repository, and asks the LLM to diagnose the likely root cause, citing
sources — needs both an embedding provider and `ANTHROPIC_API_KEY`;
returns a fixed explanatory message, still `200`, if nothing relevant
is indexed yet),
`POST /repositories/{id}/architecture` (no body; infers likely
components, tech stack, and entry points from the repository's file
tree and README content alone — no other file contents — and asks the
LLM to summarize them; needs only `ANTHROPIC_API_KEY`; `400` if no
files are indexed yet),
`POST /repositories/{id}/security-scan` (no body; runs a local,
rule-based static scan — no LLM call, no API key needed at all — over
every indexed file's content for patterns like hardcoded secrets,
`eval`/`exec`, shell injection, and SQL built via string interpolation,
returning findings sorted by severity; `400` if no files are indexed
yet), and
`POST /repositories/{id}/agent` (body: `{"goal": str, "max_steps":
int}`, `max_steps` optional, 1–10, default 4; runs the LangGraph
multi-step agent — see "Key Features" above for its six tools — toward
the stated goal, returning a final answer plus the sequence of tool
calls it made). Set `GITHUB_TOKEN` in `.env` to raise
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

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Visit `http://localhost:3000`. Requires the backend running (see
above) at the URL in `NEXT_PUBLIC_API_BASE_URL`. Register an account
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

### Rate limiting

Resource-intensive endpoints are rate limited per authenticated user
via a Redis-backed fixed-window counter (reuses `REDIS_URL`; if Redis
itself is unreachable, requests are allowed through rather than the
app failing closed). Exceeding a limit returns `429` with a `detail`
message naming the limit and window. All limits are configurable via
`.env` (see `.env.example`'s "Rate limiting" section) — defaults:

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

Which model turns code into vectors for semantic search is
configurable (`EMBEDDING_PROVIDER` in `.env`), behind one small interface
(`app/services/embedding_providers.py`: `embed_texts`/`embed_query`) that
every caller — ingestion, search, debug, chat, the agent's tools — goes
through instead of a specific provider directly.

**`EMBEDDING_PROVIDER=openai`** (the default):
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
- **Not installed by default**: `fastembed` and its own dependencies
  (onnxruntime/onnx/numpy/tokenizers/huggingface_hub, roughly 150MB)
  live in a separate
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

### LLM Providers

Which model answers chat/explain/review/debug/architecture requests and
drives the agent's planning is configurable (`LLM_PROVIDER` in `.env`),
behind one interface (`app/services/llm.py`: `generate_response()` for a
single grounded answer, `generate_with_tools()` for the agent's native
tool-calling planner) that every caller goes through instead of a
specific provider directly — the same pattern `embedding_providers.py`
already established for embeddings.

**`LLM_PROVIDER=groq`**:
- Uses Groq's OpenAI-compatible Chat Completions API. Requires
  `GROQ_API_KEY`; model is `GROQ_MODEL` (default `openai/gpt-oss-120b`).
- The agent's planner uses Groq's **native tool-calling** support
  (`tools`/`tool_calls`) — not a hand-written JSON convention the model
  is merely asked to follow.
- **Known limitation, documented honestly**: this project's Groq account
  tier currently has an 8,000 tokens-per-minute (TPM) ceiling for
  `openai/gpt-oss-120b`. A multi-step agent investigation can hit that
  limit mid-run — live testing during development found this to be
  driven primarily by *account-level* TPM availability (which fluctuates
  independently of any single request's own size, confirmed by two
  back-to-back identical requests reporting very different "requested
  token" figures from Groq) rather than a fixed, predictable ceiling
  this codebase alone controls. `SEARCH_LIMIT`, per-run duplicate-chunk
  suppression, and a reduced planner token budget (see "Agent
  Safeguards" below) reduce how much each request needs, but do not
  eliminate the underlying account-tier constraint. When Groq returns
  HTTP 429, the API responds with a clean, user-facing message ("The AI
  service is temporarily rate-limited...") instead of the raw upstream
  error — see `app/services/llm.py`'s `LLMRateLimitError`.

**`LLM_PROVIDER=anthropic`** (the original implementation):
- Uses Anthropic's Messages API, including its own native tool-calling
  format (`tools` with `input_schema`, `tool_use`/`tool_result` content
  blocks) for the agent's planner. Requires `ANTHROPIC_API_KEY`; model is
  `ANTHROPIC_MODEL` (default `claude-sonnet-5`).
- **Not live-verified in this environment** — no `ANTHROPIC_API_KEY` is
  configured here to test against a real endpoint. The implementation is
  covered by an extensive mocked/unit test suite
  (`backend/tests/test_llm.py`) exercising tool-schema translation,
  message-history translation (including Anthropic's requirement that
  multiple tool results answering one turn share a single following user
  turn), response normalization, and error mapping — but "passes every
  mocked test" is not the same claim as "confirmed working against
  Anthropic's real API," and this README does not claim the latter.

Both providers share the same Agent safeguards (`app/services/agent.py`):

- **`max_steps`** (1–10, default 4) bounds how many tool calls one agent
  run can make; the final call is sent with `tool_choice="none"` so the
  provider itself cannot return another tool call once the budget is
  used, rather than just asking the model in English to stop.
- **Malformed/missing tool arguments** never crash a request — a tool
  invoked with an unparseable or incomplete argument set resolves to the
  same clean "error: ..." observation a REST caller would get from the
  equivalent endpoint.
- **Unknown tool names** (defensive only — the provider is constrained to
  the six declared tools) resolve to an "unknown tool, ignored" step
  rather than an exception.
- **Prompt-injection / untrusted-content boundary**: every tool result is
  wrapped in explicit `[BEGIN/END UNTRUSTED REPOSITORY CONTENT]` markers
  before being replayed into the next planning call, with the system
  prompt instructing the model to always treat that content as data,
  never as an instruction — repository code is attacker-influenced (
  anyone can author a public GitHub repo), so this is the one place in
  the app where such content re-enters a context that makes control-flow
  decisions.
- **Forced-finish / tool-choice-violation fallback**: some models
  occasionally attempt a tool call despite `tool_choice="none"`, which
  Groq's API rejects outright. When that happens on the final,
  budget-exhausted turn, one bounded (never more than one) fallback
  call — with no tools declared at all, so the same violation can't
  structurally repeat — asks the model to synthesize a final answer from
  what's already been gathered, instead of surfacing a raw provider
  error.
- **Per-run duplicate-chunk suppression**: if `search_code`/`debug`
  return a code chunk the same agent run already retrieved earlier, it's
  not resent — the tool result says so concisely instead. Scoped to one
  run only (in-memory `AgentState`, never global, never persisted); a
  fresh agent run starts with no suppression history.
- **Token-consumption optimization**: `SEARCH_LIMIT` (chunks per
  `search_code`/`debug` call) and the planner's own output token budget
  were both deliberately kept small, specifically to reduce how much
  each planning turn costs — see "Known Limitations" below for why this
  doesn't fully eliminate Groq's account-level rate limiting.

## Testing & Verification

### Running the backend test suite

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

### Running the end-to-end (Playwright) suite

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
unauthenticated 60 req/hour limit exhausts fast. Since ingestion also
embeds every chunk, the suite passes whether or not a real
`OPENAI_API_KEY` is configured: it waits for either terminal status and
asserts accordingly (`README` visible on `completed`, the real error
text on `failed`).

### What CI additionally validates

Every layer above runs locally with the commands already documented;
this section covers what
[`.github/workflows/ci.yml`](.github/workflows/ci.yml) additionally
validates on every push/PR to `main`, so nothing here duplicates those
setup steps.

| Job | What it validates | Depends on |
|---|---|---|
| `backend` (Backend tests) | The pytest suite, against a Postgres service container. No API keys needed — every external call (OpenAI, Anthropic, Groq, GitHub, Qdrant, Celery) is mocked per-test, same as running it locally. | — |
| `frontend` (Frontend typecheck & lint) | `next typegen`, `tsc --noEmit`, `eslint .` | — |
| `e2e` (End-to-end tests) | The full Playwright suite against real Postgres, Redis, and Qdrant service containers plus a real Celery worker, all on the runner. `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` are deliberately left unset — the suite asserts the graceful inline error both produce when missing — so it needs no paid API access to pass. `GITHUB_TOKEN` is the workflow's own automatic token, raising the unauthenticated GitHub API rate limit; no repo secret is configured. | `backend`, `frontend` |
| `docker` (Docker build validation) | Builds the real `backend/Dockerfile` (both the `production` and `with-local-embedding` targets) and `frontend/Dockerfile` images with `docker/build-push-action`, and validates `docker compose -f docker/docker-compose.yml --profile full config`. Images are never pushed anywhere. | — |
| `docker-smoke` (Docker Compose full-stack smoke test) | Starts the real `--profile full` stack (Postgres, Redis, Qdrant, api, celery-worker, frontend, **and Caddy** — `docker compose ... up -d --build --wait --wait-timeout 180`), runs [`scripts/smoke_check.py`](scripts/smoke_check.py) through Caddy (frontend + `/health` + `/health/ready` via Caddy's `/api/*` route), then drives a real Chromium browser through a small production-like flow — register, add a repository, view its detail page, log out, confirm the protected dashboard redirects — against the actual containerized stack, reached only through Caddy on port 80, exactly as a real deployment's only public entry point would be. Always tears the stack down (`down -v`), win or lose, and captures `docker compose ps`/`logs` and a Playwright HTML report on failure. | `docker` |

`e2e` depends on `backend`/`frontend` and `docker-smoke` depends on
`docker`, so an obviously broken push fails fast without also paying
for a browser install plus several services, or a full image build
plus a container startup, for something that was never going to pass.
On failure, `e2e` and `docker-smoke` each upload their own Playwright
HTML report as a build artifact.

### Verification snapshot

**As of commit `92699bf`** ("feat: harden production compose and CI
smoke test"), verified directly in this environment (not recalled from
memory — see the commit history for what each recent commit changed):

- **389 backend `pytest` tests** (`backend/tests/`): **387 passing, 1
  skipped** (a real-Redis integration test that only runs on Linux/WSL,
  independently verified passing there), **1 failing** — and only
  because this particular local checkout's own `.env` sets
  `EMBEDDING_PROVIDER=local`; the test asserts OpenAI is the *default*
  provider, which is a property of an unconfigured environment, not of
  this one. This is a local-environment choice, not a code defect — CI
  itself is unaffected, since its workflow never sets
  `EMBEDDING_PROVIDER` at all.
- Frontend `npx tsc --noEmit`, `npx eslint .`, and `npm run build`
  (production build, including static analysis of every route) all pass
  cleanly.
- **19 Playwright end-to-end tests** across 5 spec files
  (`frontend/tests-e2e/`) — all pass. (`rag-gated-features.spec.ts` has
  3 tests that only fail locally if `EMBEDDING_PROVIDER=local` is
  intentionally configured — same reasoning as the backend test above,
  and see that spec file's own comment; CI itself is unaffected.)
- **CI for commit `92699bf` was independently checked via GitHub's own
  API and is green across all 5 jobs**: Backend tests, Frontend
  typecheck & lint, Docker build validation, Docker Compose full-stack
  smoke test, and End-to-end tests — including the `docker-smoke` job
  actually building and running the real Caddy container for the first
  time. The CI badge at the top of this README is the authoritative,
  always-current source going forward; this snapshot will age as
  development continues — check the badge, not this paragraph, for the
  current state of `main`.

These are two genuinely different test suites, not one count split two
ways — the backend tests never touch a browser, and the E2E tests never
run against the backend's mocked external services.

RepoMind AI is **feature-complete**: every feature under "Key Features"
above — RAG chat, semantic search, AI debugging/review/explain,
architecture analysis, security scanning, and the LangGraph agent with
native tool calling — is implemented and covered by the automated test
suite. What follows in "Known Limitations" below is a dated snapshot,
not a claim that nothing will ever change again, and not a claim that
every listed capability is guaranteed to behave identically on every
run.

## Demo / Screenshots

Not included yet — this README is text-only for now. Screenshots or a
short screen recording of the dashboard, chat, and agent views would be
a natural addition here.

## Optional: Production Deployment

**Cloud deployment is entirely optional and is not required to run,
develop against, or demonstrate RepoMind AI.** "How to Run Locally"
above is the complete, actually-used path for local development and
demos. Everything in this section is a deployment *path* — Dockerfiles,
a Caddy reverse proxy, a hardened Compose profile, an environment-
variable contract, healthchecks — kept in this repository because it
demonstrates real deployment/production-hardening engineering, and
verified in CI's own disposable runner (including a real Caddy
container, since the `docker-smoke` job above builds and runs it). It
does **not** mean this application is currently deployed anywhere
publicly reachable — see "Known Limitations" below for exactly what
would still be needed before a real internet-facing deployment (a real
domain/DNS, real production secrets, and ARM64 image verification on
the actual target hardware, none of which have been done).

### Architecture

```
Internet
   │
   │  80 / 443 only — the single public entry point
   ▼
┌───────────────────────────────────┐
│               Caddy                │  reverse proxy, automatic HTTPS
└─────────────────┬─────────────────┘  for a real domain (Let's
                   │                    Encrypt/ACME) or plain HTTP for
        ┌──────────┴──────────┐        local testing — see docker/Caddyfile
        ▼                     ▼
  ┌───────────┐         ┌───────────┐
  │ frontend  │◄───────►│    api    │  FastAPI + uvicorn, port 8000
  │ Next.js,  │  server-│           │  (also reachable directly by
  │ port 3000 │  side   └─────┬─────┘   `frontend`, over the same
  └───────────┘  calls        │         internal network — not
                               │         through Caddy; see below)
                  ┌────────────┼───────────────┐
                  ▼            ▼               ▼
             ┌─────────┐ ┌──────────┐   ┌──────────┐
             │postgres │ │  redis   │   │  qdrant  │
             └─────────┘ └────┬─────┘   └──────────┘
                               │
                         ┌─────┴──────┐
                         │celery-worker│  same image as api,
                         └────────────┘  different command
```

Only Caddy publishes a host port (`80`/`443`). Postgres, Redis, Qdrant,
`api`, and `frontend` publish nothing to the host at all — each is
reachable only by service name over one of two Docker-internal
networks (`edge`: Caddy + frontend + api; `internal`: api +
celery-worker + the three data stores), never directly from the host or
the public internet. `frontend` and `api` are still deployed as
separate containers and talk over HTTP; all of that traffic is
server-side (`frontend`'s own Next.js server calling `api`, not the end
user's browser calling it directly — see the "Architecture" section
near the top of this README for why the browser never needs to know
`api`'s address at all), so what matters is `frontend`'s build getting
baked with a URL that's actually reachable *from inside the frontend
container*, not from a developer's host machine or browser —
`docker/docker-compose.yml`'s `full` profile handles this with its own
`COMPOSE_FRONTEND_API_BASE_URL` variable (defaulting to the
Compose-internal `http://api:8000`) precisely so it doesn't collide
with `NEXT_PUBLIC_API_BASE_URL`, which stays whatever native/WSL
development needs instead — see "Frontend deployment" below for why
conflating the two was a real bug.

Caddy itself (`docker/Caddyfile`) routes `https://<PRODUCTION_DOMAIN>/`
to `frontend:3000` and `https://<PRODUCTION_DOMAIN>/api/*` to
`api:8000` (stripping the `/api` prefix first — none of FastAPI's real
routes start with `/api`, so this doesn't collide with anything). The
`/api/*` route exists for direct API reachability (manual testing, the
OpenAPI docs) — the frontend itself never uses it, since its own calls
to `api` happen server-side over the internal network, not through
Caddy. TLS is automatic: a real public domain (`PRODUCTION_DOMAIN` in
`.env`) gets a real Let's Encrypt certificate; Caddy is never configured
with a self-signed certificate or a hardcoded domain.

### Required environment variables

Every variable is documented with its default in
[`.env.example`](.env.example) — this is the **one** canonical copy
(`cp .env.example .env` from the repo root, or the equivalent step in
"How to Run Locally" above); nothing else in this repo defines or
duplicates this contract. **Never commit the real `.env`** —
`.gitignore` already excludes it, `.env.example` itself contains only
placeholders (`changeme`, blank), and this is checked by
`tests/test_deployment_config.py`.

At a glance:
- **Required production secrets** (no usable default; the app refuses to
  start without real values — see below): `JWT_SECRET_KEY`,
  `DATABASE_URL`'s password.
- **Conditionally required secrets** (needed only for the feature that
  uses them; each fails with a clean `503`, never a crash, if missing):
  `OPENAI_API_KEY` (only when `EMBEDDING_PROVIDER=openai`),
  `ANTHROPIC_API_KEY` (only when `LLM_PROVIDER=anthropic`, the code
  default), `GROQ_API_KEY` (only when `LLM_PROVIDER=groq` — see "LLM
  Providers" above).
- **Optional, empty by default, unlock hardening features when set**:
  `REDIS_PASSWORD` (Redis auth), `QDRANT_API_KEY` (Qdrant auth) —
  neither is required for local development; both preserve today's
  unauthenticated local Redis/Qdrant exactly as-is when left blank.
- **Optional**: `GITHUB_TOKEN` (raises a rate limit, nothing breaks
  without it), all `RATE_LIMIT_*` variables, `WEB_CONCURRENCY`/
  `FORWARDED_ALLOW_IPS`, `PRODUCTION_DOMAIN` (Caddy only).
- **Required production service URLs**: `DATABASE_URL`, `REDIS_URL`,
  `QDRANT_HOST`/`QDRANT_PORT`.
- **Public, not secret**: `NEXT_PUBLIC_API_BASE_URL` and
  `COMPOSE_FRONTEND_API_BASE_URL` — Next.js inlines every
  `NEXT_PUBLIC_*` variable into the JavaScript actually shipped to the
  browser, so neither one may ever hold a secret (only ever a URL here).

The consolidated production-audit view of the same file — **when** each
value is read matters as much as what it's for:

| Variable | When read | Required in production |
|---|---|---|
| `ENVIRONMENT` | Runtime (backend process start) | Yes — must be `production` to enable the guards below |
| `JWT_SECRET_KEY` | Runtime | **Yes, real value** — app refuses to start with the placeholder or empty |
| `DATABASE_URL` | Runtime | **Yes, real value** — app refuses to start with the placeholder `changeme` password |
| `CORS_ORIGINS` | Runtime | Recommended — logs a warning (not a hard failure) if left at the localhost default |
| `PRODUCTION_DOMAIN` | Read by `docker/Caddyfile` (Caddy container only, not the app) | Yes, for real HTTPS — a real domain with DNS pointing at the deployment host; a local/non-public value falls back to Caddy's own local-only test HTTPS |
| `REDIS_PASSWORD` | Compose-time (embedded into `REDIS_URL`) and by the `redis` container itself | Recommended — empty preserves unauthenticated Redis |
| `QDRANT_API_KEY` | Runtime (sent as the `api-key` header) and by the `qdrant` container itself | Recommended — empty preserves unauthenticated Qdrant |
| `FORWARDED_ALLOW_IPS` | Runtime, but read by `backend/docker-entrypoint.sh` directly, **not** by the FastAPI app itself | Optional — Compose's own default scopes trust to the internal network Caddy runs on, not `*` |
| `GITHUB_TOKEN` | Runtime | Optional — raises GitHub's rate limit from 60/hr to 5000/hr |
| `LLM_PROVIDER` / `ANTHROPIC_MODEL` / `GROQ_MODEL` / `EMBEDDING_MODEL` | Runtime | Optional (defaults are usable) |
| `OPENAI_API_KEY` | Runtime | Yes, for search/chat/debug/etc. to work at all — endpoints return a clean `503` (not a crash) if unset |
| `ANTHROPIC_API_KEY` | Runtime | Yes, only when `LLM_PROVIDER=anthropic` — same clean-`503`-if-unset reasoning as `OPENAI_API_KEY` |
| `GROQ_API_KEY` | Runtime | Yes, only when `LLM_PROVIDER=groq` — same reasoning |
| `REDIS_URL` | Runtime | Yes — Celery broker and rate limiting both need it; rate limiting fails open (not closed) if unreachable |
| `QDRANT_HOST` / `QDRANT_PORT` / `QDRANT_COLLECTION_NAME` | Runtime | Yes — search/chat/debug depend on it |
| `RATE_LIMIT_*` (ten variables) | Runtime | Optional (defaults are usable); `RATE_LIMIT_ENABLED=false` disables all of them |
| `WEB_CONCURRENCY` | Runtime, but read by `backend/docker-entrypoint.sh` directly, **not** by the FastAPI app itself | Optional (defaults are usable) |
| `NEXT_PUBLIC_API_BASE_URL` | **Build-time only** — inlined into the frontend's compiled output; changing it after the image is built has no effect | Native/WSL development only — see "Frontend deployment" below |
| `COMPOSE_FRONTEND_API_BASE_URL` | **Build-time only**, same mechanism | Docker Compose `full` profile only; leave blank to use the correct default |

Two secrets deliberately have **no usable default** anywhere in this
stack (`.env.example`'s placeholders, `docker/docker-compose.yml`'s
`full` profile): `JWT_SECRET_KEY` and `DATABASE_URL`'s password —
generate a real `JWT_SECRET_KEY` with
`python -c "import secrets; print(secrets.token_urlsafe(48))"`. Every
other variable above has a default that's fine for local development
but should be reviewed before a real deployment.

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
from the proxy's own IP, which would also silently break per-IP
`auth_register`/`auth_login` rate limiting (everyone behind the proxy
would share one counter). The `*` default above is this variable's
**native/WSL development** default (no reverse proxy at all in that
setup); `docker/docker-compose.yml`'s `full` profile overrides it to the
`edge` network's own subnet instead, so only Caddy is actually trusted
to set these headers once the API has no host-published port left —
see "Architecture" above and `.env.example`'s own comment on this
variable.

**Migration safety scope:** "safe on every deploy" above means safe for
the single `api` container/replica this Compose file actually runs —
`alembic upgrade head` records its progress in a plain table, not a
real distributed lock, so running *multiple* `api` replicas that each
execute this entrypoint concurrently could race each other. Add a real
lock (e.g. a Postgres advisory lock in `alembic/env.py`) before ever
scaling `api` beyond one replica; nothing here claims that safety today.

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

A second, sharper version of that same gotcha: building this image as
part of `docker/docker-compose.yml`'s `full` profile must **not** reuse
`.env`'s `NEXT_PUBLIC_API_BASE_URL` directly — that variable is correct
for native/WSL development (frontend and backend as separate processes
on the same host, where `localhost:8000` really does reach the
backend), but every API call this app makes happens server-side, inside
whichever process is running the frontend. Built with `localhost:8000`
baked in and run as its own Compose container, the frontend would try
to reach `localhost:8000` *from inside itself* — nothing listens there,
since `api` is a separate container — and every Server Component and
Server Action would fail silently. `docker-compose.yml` avoids this
with its own `COMPOSE_FRONTEND_API_BASE_URL` variable instead,
defaulting to the Compose-internal `http://api:8000` so this works
correctly with zero configuration — and stays that value even behind
Caddy, since the browser itself never sees or needs this URL at all
(see "Architecture" above); see `.env.example`'s comment on that
variable, and the `frontend` service's own `build.args` comment, for
the full reasoning.

Verified locally without Docker (see "Testing & Verification" above): a
real `npm run build` succeeds and produces `.next/standalone/server.js`,
exercising the exact build step the Dockerfile's builder stage runs.

### Celery worker deployment

No separate image — [`docker/docker-compose.yml`](docker/docker-compose.yml)'s
`celery-worker` service builds from the *same* `backend/Dockerfile`,
overrides its `ENTRYPOINT` (which is api-specific: migrations + uvicorn)
to empty, and runs:

```bash
celery -A app.core.celery_app worker --loglevel=info --concurrency=1
```

No `--pool=solo` — that's the Windows-only workaround for the lack of
`os.fork()`; Linux's default `prefork` pool works as-is inside a
container. Uses the exact same Redis broker config (`REDIS_URL`) and
sees the exact same rate-limiting settings as `api` (rate limiting is a
Redis-backed check inside request handling, not something the worker
itself needs to know about — it just needs the same `REDIS_URL` to
reach the same Redis).

**Concurrency:** explicitly pinned to `--concurrency=1`, not left at
Celery's own default (one process per CPU core). Ingestion here is a
background, one-at-a-time-per-repository workload, not a
high-throughput queue, and an unpinned worker count is unpredictable
memory usage on a resource-constrained deployment target — each worker
process can hold a full copy of the app's imports (and, if
`EMBEDDING_PROVIDER=local`, the ONNX model) in memory. Raise it directly
in `docker/docker-compose.yml`'s `celery-worker` `command:` if ingestion
throughput ever actually becomes a measured bottleneck, rather than
guessing a higher number ahead of any evidence it's needed.

### PostgreSQL, Redis, and Qdrant

Same requirements as local development — see "Local infrastructure"
above for what each is used for. In production, run them as managed
services or long-lived containers with real persistent volumes/backups;
`docker/docker-compose.yml`'s `postgres`/`redis`/`qdrant` services are
adequate for a small, single-host deployment but don't include backup
automation or replication — add those separately for anything beyond
that scale. Redis and Qdrant both support optional password/API-key
authentication (`REDIS_PASSWORD`/`QDRANT_API_KEY` — see "Required
environment variables" above); neither is required, but both are
recommended once the stack is reachable by anything beyond a single
trusted developer.

**Connection pool sizing:** `app/core/database.py` creates its async
engine with no explicit `pool_size`/`max_overflow`, so it uses
SQLAlchemy's defaults (5 + 10 = 15 connections, per process). Each of
`api`'s `WEB_CONCURRENCY` uvicorn worker *processes* gets its own pool
(they don't share one), plus one more from the Celery worker process —
so a default `WEB_CONCURRENCY=2` deployment can open up to roughly
`2 × 15 + 15 = 45` Postgres connections at once. Postgres's own default
`max_connections` is 100, so this is comfortable out of the box, but
size accordingly if `WEB_CONCURRENCY` is raised well beyond 2.

### Docker Compose deployment

The same file used for local infra also represents the complete stack,
gated behind a Compose profile so the original infra-only behavior is
completely unchanged by default:

```bash
# Infra only (unchanged default behavior):
docker compose -f docker/docker-compose.yml up -d

# The complete stack - postgres, redis, qdrant, api, celery-worker, frontend, caddy:
docker compose -f docker/docker-compose.yml --env-file .env --profile full up -d

# Same command CI's docker-smoke job actually runs - blocks until every
# service with a healthcheck reports healthy, or fails after 180s
# instead of hanging indefinitely:
docker compose -f docker/docker-compose.yml --env-file .env --profile full \
  up -d --build --wait --wait-timeout 180

# Stop and remove the stack (add -v to also delete the named volumes -
# Postgres/Redis/Qdrant/Caddy data - and lose all local data,
# including issued TLS certificates):
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

### Verifying a deployment

After bringing up the `full` profile (or any other deployment of this
stack — native/WSL, or a future cloud target), check it actually works.
Through Caddy (the real production path — only 80/443 are host-published,
so this is how a real deployment's own frontend/api are actually
reached):

```bash
python scripts/smoke_check.py --frontend-url http://localhost --api-url http://localhost/api
```

Against a native/WSL deployment with no Caddy in front (frontend/api on
their own default ports, as in "How to Run Locally" above):

```bash
python scripts/smoke_check.py   # defaults to localhost:3000 / localhost:8000
```

[`scripts/smoke_check.py`](scripts/smoke_check.py) is a small,
dependency-free script (Python standard library only — no `pip install`
needed) that makes three plain GET requests — the frontend's own URL,
`GET /health`, and `GET /health/ready` — and prints one line per check
plus a nonzero exit code if anything failed. It never sends anything
but GET requests, so it's safe to run against a live deployment at any
time. `backend/tests/test_smoke_check.py` covers its logic directly
against a local fake server (no live deployment needed for that).

To check each piece of the Docker Compose `full` profile manually
instead (from inside the Docker network, since `api`/`frontend` have no
host-published port — see "Architecture" above):

```bash
docker compose -f docker/docker-compose.yml --profile full exec api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health/ready').status)"
docker compose -f docker/docker-compose.yml --profile full ps   # every service "healthy"?
docker compose -f docker/docker-compose.yml --profile full logs celery-worker --tail 50
```

### Health / readiness checks

- `GET /health` — liveness: is the process up at all. No dependency
  checks, no auth needed.
- `GET /health/ready` — readiness: can the process actually serve
  requests right now. Checks Postgres reachability only (with an
  explicit timeout, so an unreachable — not just erroring — database
  can't hang the check), deliberately not Redis/Qdrant: both already
  degrade gracefully when unreachable (ingestion fails fast into
  `status: "failed"`, rate limiting fails open, search/chat map to a
  clean `502`/`503`), so reporting "not ready" for either would flag a
  condition that doesn't actually block most requests. Neither endpoint
  ever includes a connection string, credential, or other
  infrastructure detail in its response.

`docker/docker-compose.yml`'s `api`/`celery-worker`/`frontend`/`caddy`
services all have their own `healthcheck:` blocks, each probing with a
tool already guaranteed present in that image rather than installing
one just for this (Python's `urllib` for `api`, Node's `http` module
for `frontend`, `celery inspect ping` for the worker, busybox `wget`
for Caddy's Alpine base) — the same "no guaranteed shell tools"
reasoning behind why Qdrant's own service has no healthcheck at all.
`api`'s own healthcheck probes `/health/ready` specifically, not just
`/health` — so a mid-life database outage is correctly reflected in the
container's health status, not just whether the process itself is
still running.

### Observability

- **Structured logging** (`backend/app/core/logging_config.py`) — one
  JSON object per log line in production (machine-readable), a short
  human-readable line otherwise; both formats carry the same fields.
- **Request correlation** (`backend/app/core/request_id.py`) — every
  response, success or failure, carries an `X-Request-ID` header, and
  every log line emitted while handling that request carries the same
  id. The frontend reads this back (`frontend/src/lib/api.ts`'s
  `ApiError.requestId`) and appends a `(reference: <id>)` suffix to
  error messages shown to the user, so a user-reported problem can be
  traced to a specific backend log line.
- **Celery ingestion diagnostics** (`backend/app/services/repository_ingestion.py`,
  `backend/app/tasks.py`) — every ingestion run logs when it starts and
  how long it took, on both success and failure. `ingest_repository_task`
  is bounded by a Celery `soft_time_limit`/`time_limit` (600s/660s) so a
  hung task can't occupy a worker indefinitely, with a recovery path
  that marks the repository `failed` (with a clear message) instead of
  leaving it stuck in `cloning`/`processing`.
- **Graceful degradation for every external dependency** (GitHub,
  OpenAI/local embeddings, Anthropic, Qdrant, Redis) — see "Health /
  readiness checks" above and the Troubleshooting table below for the
  specific behavior of each.

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
- [x] **HTTPS/TLS termination** — Caddy (`docker/docker-compose.yml`'s
      `caddy` service, `docker/Caddyfile`) terminates TLS automatically
      for a real `PRODUCTION_DOMAIN`; `uvicorn` runs plain HTTP behind it
      and trusts `X-Forwarded-*` only from the internal network Caddy
      runs on (`FORWARDED_ALLOW_IPS`).
- [x] **Only the reverse proxy is publicly reachable** — Postgres,
      Redis, Qdrant, `api`, and `frontend` publish no host ports at all;
      only Caddy (`80`/`443`) does.
- [ ] **`REDIS_PASSWORD`/`QDRANT_API_KEY` set to real values** —
      supported and recommended, but empty by default; the app and both
      containers must agree on the same values (see "Required
      environment variables" above).
- [ ] Per-user/per-IP rate limiting and fail-fast/graceful degradation
      for every external dependency apply unchanged in production —
      neither is dev-only behavior.

### Native/WSL fallback for resource-constrained machines

Nothing above requires Docker. Every piece — the backend, the Celery
worker, the frontend, Postgres, Redis, Qdrant — can run exactly as "How
to Run Locally" already documents, just with
`ENVIRONMENT=production`-appropriate values in `.env` and a real reverse
proxy (Caddy, nginx, Traefik, your cloud provider's load balancer —
anything that can set `X-Forwarded-*` headers) in front of
`uvicorn`/`next start`. This is not a hypothetical: it's the same option
this project's own local development actually uses day to day, for the
same reason — Docker Desktop's resource overhead is a real cost on an
8 GB RAM machine, in production every bit as much as in local dev.

## Troubleshooting

Practical fixes for actual failure modes this project already handles
or has hit, not a generic checklist:

| Symptom | Likely cause / what to check |
|---|---|
| `api` container never becomes healthy | Check `docker compose -f docker/docker-compose.yml --profile full logs api`. The most common cause: `ENVIRONMENT=production` with `JWT_SECRET_KEY` or `DATABASE_URL`'s password still at its placeholder — `app/core/config.py`'s startup guard raises during `alembic upgrade head` (the entrypoint's first command), so `uvicorn` never starts and the healthcheck (`GET /health/ready`) has nothing to reach. The `api` container's healthcheck probes `/health/ready`, not just `/health` — it verifies Postgres is actually reachable, not just that the process is alive, so a database outage after startup is also correctly reflected here. See "Required environment variables" above. |
| Database migration/startup fails | Confirm `DATABASE_URL` is correct and Postgres is actually reachable — `api`/`celery-worker` both `depends_on: postgres: condition: service_healthy`, so a wrong password/host is the usual cause once Postgres itself is up. Run `alembic upgrade head` manually (see "How to Run Locally" above) to see the real error outside a container. |
| Redis unavailable | `POST /repositories`/`.../reindex` fail fast into `status: "failed"` with a clear "background worker is unreachable" message rather than hanging. Rate limiting fails **open** (requests allowed through), not closed, if Redis is unreachable — it won't block traffic, but limits stop being enforced. |
| Qdrant unavailable | No healthcheck on the `qdrant` service by design — its image has no shell tools to probe with. Check from another container on the same Docker network, or `curl http://localhost:6333/collections` in a native/WSL setup where Qdrant is host-reachable. Search/chat/debug map an unreachable Qdrant to a clean `502`/`503`, not a crash. |
| Ingestion ends in `failed` — OpenAI missing/quota | Expected without a real `OPENAI_API_KEY` (or with one that's exhausted) when `EMBEDDING_PROVIDER=openai` — the repository's `error_message` names it directly. Either set a real key, or switch to `EMBEDDING_PROVIDER=local` (see "Embedding Providers" above) to avoid needing one at all. |
| Chat/explain/review/etc. return `503` | Whichever key `LLM_PROVIDER` currently selects (`ANTHROPIC_API_KEY` for `anthropic`, `GROQ_API_KEY` for `groq`) is unset or invalid — every LLM-backed endpoint returns a clean `503` rather than crashing when it's missing. |
| Agent request returns `502` with "temporarily rate-limited" | The configured LLM provider returned an HTTP 429 (Groq's shared account-tier TPM limit is the one observed in practice — see "LLM Providers" above). The API translates this into a clean message rather than the raw provider error; wait a few seconds and retry manually — the app does not auto-retry. |
| Repository stuck in `pending` forever | No Celery worker is running. `.delay()` calls succeed either way (they just publish to Redis) — start one: `celery -A app.core.celery_app worker --loglevel=info` (add `--pool=solo` on native Windows; see "How to Run Locally" above). |
| Port already in use (3000/8000/5432/6379/6333) | Something else on the host is already bound to it. For Compose, override via `.env` (`BACKEND_PORT`, `POSTGRES_PORT`, `REDIS_PORT`, `QDRANT_PORT`); for native processes, pass the equivalent flag/env var to that tool directly. Only relevant to infra-only Compose mode or native/WSL — the `full` profile no longer publishes these ports at all. |
| Docker isn't available on this machine | Not a hard requirement — use Option B (native/WSL Postgres/Redis/Qdrant) from "Local infrastructure" above; every other piece of the stack already runs the same way regardless of Docker. |
| Setting up local embeddings (`EMBEDDING_PROVIDER=local`) | Native/WSL: `pip install -r backend/requirements-local-embedding.txt` instead of `requirements.txt`. Docker: set `BACKEND_DOCKER_TARGET=with-local-embedding` in `.env` before `--build`ing. See "Embedding Providers" above for the full explanation — including that switching providers is not retroactive for already-ingested repositories. |

## Known Limitations

Documented honestly, not glossed over:

- **Groq's account-tier TPM limit can interrupt a multi-step agent run.**
  The `openai/gpt-oss-120b` model currently used is subject to an 8,000
  tokens-per-minute ceiling on this project's Groq account tier. A
  longer agent investigation (several real tool calls in one run) can
  hit that limit before producing a final answer. This is not guaranteed
  to happen on every run, and the app never shows a raw provider error
  when it does (see "LLM Providers" above) — but **reliable, every-time
  Agent completion is not currently guaranteed**, and this README makes
  no claim that it is.
- **Anthropic's native tool-calling path has not been live-verified.**
  It's implemented and covered by an extensive mocked/unit test suite,
  but no `ANTHROPIC_API_KEY` has been available in this project's own
  environment to confirm it against Anthropic's real API. Switching
  `LLM_PROVIDER=anthropic` in a real deployment should work per the test
  coverage, but that is a claim about test coverage, not about a
  confirmed live run.
- **This is not a public or production deployment.** The deployment
  path described in "Optional: Production Deployment" above — Caddy,
  TLS, host-port isolation, Redis/Qdrant auth, hardened Compose — is
  real, implemented, and CI-verified (including a real Caddy container
  actually starting and routing traffic in CI). It is **not** the same
  claim as "this is running somewhere publicly reachable right now." A
  real deployment would still need: a real domain with DNS pointing at
  the target host, real production secrets, and verification that the
  Docker images actually build and run on the target architecture —
  none of that has been done, and this README does not claim otherwise.
- **Not free forever, and not guaranteed rate-limit-free.** Groq's free
  tier and local fastembed embeddings avoid *some* costs (no OpenAI
  embedding spend, no Anthropic spend while `LLM_PROVIDER=groq`), but
  "avoids some costs today" is not the same claim as "will always be
  free" or "will never be rate-limited" — see the TPM point above.

## Development History

RepoMind AI was built incrementally, one milestone at a time. See
[docs/architecture.md](docs/architecture.md) for the full system design
and its own development log. The CI badge at the top of this README
always reflects the live state of the latest commit on `main`; the
"Verification snapshot" above is a dated point-in-time count, not a
claim that nothing will ever change again.
