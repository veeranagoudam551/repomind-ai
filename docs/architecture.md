# RepoMind AI — Architecture

This document is a living record of the system design. It grows as
each phase of the project is built; it is not written all at once.

## 1. High-Level System View

```
USER
  |
  v
NEXT.JS FRONTEND
  |
  v
FASTAPI BACKEND
  |
  |------------------------|
  |                        |
  v                        v
POSTGRESQL              REDIS
  |
  v
REPOSITORY METADATA
```

- **Next.js frontend** — the developer-facing UI (dashboard, repo view, chat).
- **FastAPI backend** — the single API surface for auth, repository
  ingestion, RAG, and analysis features.
- **PostgreSQL** — system of record for users, repositories, files,
  chunk metadata, conversations, and messages.
- **Redis** — backing store for background job queues (repository
  processing runs async, not inline with the HTTP request).
- **Qdrant** (introduced Phase 3) — vector store for code embeddings,
  used for semantic search and RAG retrieval.

## 2. Repository Processing Pipeline (Phase 2)

```
GitHub Repository
        |
        v
Repository Validation
        |
        v
Repository Clone / Download
        |
        v
File Scanner
        |
        v
File Parser
        |
        v
Code Chunking
        |
        v
Embedding Generation
        |
        v
QDRANT VECTOR DATABASE
```

Repository code is only ever read, never executed. See Security
Requirements below.

## 3. RAG Pipeline (Phase 3)

```
User Question
        |
        v
Question Embedding
        |
        v
Qdrant Semantic Search (filtered by repository_id)
        |
        v
Retrieve Relevant Code Chunks
        |
        v
Build Context
        |
        v
LLM
        |
        v
Grounded Answer + Source Files
```

The LLM is instructed to answer only from retrieved context, state
uncertainty explicitly, and never claim a file or function exists
that was not actually retrieved.

## 4. Data Model

All tables use a `UUID` primary key (`id`). Implemented as async
SQLAlchemy 2.0 models in `backend/app/models/`, with an Alembic
migration (`backend/alembic/versions/`) as the source of truth for the
actual schema.

**`users`** — account credentials and profile
| Column | Type | Notes |
|---|---|---|
| `email` | string(320) | unique, indexed |
| `hashed_password` | string(255) | never store plaintext |
| `full_name` | string(255) | nullable |
| `is_active` | bool | default `true` |
| `created_at` / `updated_at` | timestamptz | |

**`repositories`** — a GitHub repo a user has added, plus processing status/stats
| Column | Type | Notes |
|---|---|---|
| `owner_id` | UUID FK → `users.id` | `ON DELETE CASCADE`, indexed |
| `github_url` | string(2048) | |
| `name` | string(255) | e.g. `owner/repo` |
| `description` | text | nullable |
| `default_branch` | string(255) | nullable |
| `status` | enum: `pending`/`cloning`/`processing`/`completed`/`failed` | default `pending` |
| `error_message` | text | nullable |
| `file_count` | int | default `0` |
| `total_size_bytes` | bigint | default `0` |
| `last_indexed_at` | timestamptz | nullable |
| `created_at` / `updated_at` | timestamptz | |

Unique constraint on `(owner_id, github_url)` — a user can't add the
same repo twice.

**`conversations`** — a chat thread scoped to one repository
| Column | Type | Notes |
|---|---|---|
| `repository_id` | UUID FK → `repositories.id` | `ON DELETE CASCADE`, indexed |
| `user_id` | UUID FK → `users.id` | `ON DELETE CASCADE`, indexed |
| `title` | string(255) | nullable |
| `created_at` / `updated_at` | timestamptz | |

**`messages`** — individual turns within a conversation
| Column | Type | Notes |
|---|---|---|
| `conversation_id` | UUID FK → `conversations.id` | `ON DELETE CASCADE`, indexed |
| `role` | enum: `user`/`assistant`/`system` | |
| `content` | text | |
| `created_at` | timestamptz | |

**`repository_files`** — per-file metadata for a processed repository
| Column | Type | Notes |
|---|---|---|
| `repository_id` | UUID FK → `repositories.id` | `ON DELETE CASCADE`, indexed |
| `file_path` | string(1024) | relative path within the repo |
| `language` | string(64) | nullable |
| `size_bytes` | int | default `0` |
| `content_hash` | string(64) | nullable, for change detection |
| `created_at` | timestamptz | |

Unique constraint on `(repository_id, file_path)`.

**`code_chunks`** — metadata for each chunk sent to the vector DB
(vectors themselves live in Qdrant, not Postgres)
| Column | Type | Notes |
|---|---|---|
| `repository_id` | UUID FK → `repositories.id` | `ON DELETE CASCADE`, indexed (denormalized for fast filtering) |
| `repository_file_id` | UUID FK → `repository_files.id` | `ON DELETE CASCADE`, indexed |
| `chunk_index` | int | order within the file |
| `content` | text | |
| `start_line` / `end_line` | int | nullable |
| `vector_id` | string(64) | indexed; the corresponding point ID in Qdrant |
| `created_at` | timestamptz | |

## 5. Security Principles (apply from Day 1 onward)

- Repository code is **read-only** — the system never executes code
  from an ingested repository (no `npm install`, no running scripts).
- Secrets (JWT key, DB credentials, API keys) live only in `.env`,
  never in source. See `.env.example` for the full contract.
- Repository access is always scoped to the owning user — no
  cross-user data leakage, including in vector search filters.
- Dangerous/irrelevant directories (`.git`, `node_modules`, `.venv`,
  build output, etc.) are excluded from ingestion.

## 6. Status Log

| Day | Milestone |
|---|---|
| Day 1 | Project structure, git repo, env contract, this doc |
| Day 2 | Next.js frontend scaffold (TypeScript, Tailwind, shadcn/ui, TanStack Query); base layout with nav/footer shell; placeholder landing, login, and dashboard pages |
| Day 3 | FastAPI backend skeleton (`app/main.py`, `app/core/config.py` via pydantic-settings, `app/api/health.py`); `/health` endpoint; CORS configured for the frontend origin |
| Day 4 | Async SQLAlchemy 2.0 models for all 6 tables (`app/models/`); async engine/session setup (`app/core/database.py`); Alembic configured for async migrations; initial migration generated and applied against a live local PostgreSQL 17 database |
| Day 5 | Auth: password hashing (bcrypt) and JWT issuing/verification (`app/core/security.py`); `POST /auth/register`, `POST /auth/login`, `GET /auth/me` (protected via `app/api/deps.py`); full flow verified end-to-end against the live database |
| Day 6 | GitHub API client (`app/services/github.py`) — parses a repo URL, fetches metadata via the GitHub REST API; `POST /repositories` (validates the repo, enforces `MAX_REPO_SIZE_MB`, rejects private repos and duplicates, creates a `pending` row) and `GET /repositories` (list your own), both protected; verified against real public GitHub repos |
| Day 7 | Repository download + file scanner (`app/services/repository_ingestion.py`) — downloads the default branch as a tarball via the GitHub API (no `git` subprocess), safely extracts it, walks the tree excluding `.git`/`node_modules`/`.venv`/build output/etc., skips files over `MAX_FILE_SIZE_KB`, hashes and language-tags the rest, and writes `repository_files` rows; runs via FastAPI `BackgroundTasks` (Celery/Redis lands Day 34) and drives `repositories.status` through `pending → cloning → processing → completed`/`failed`; added `GET /repositories/{id}` and `GET /repositories/{id}/files`; verified end-to-end against real public repos (`octocat/Hello-World`, `github/gitignore`) |
| Day 8 | File parser + code chunking (`app/services/code_chunking.py`) — for each scanned file, skips binary content (by extension and a null-byte sniff), decodes the rest as UTF-8, and splits it into overlapping line-window chunks (`CHUNK_MAX_LINES`, default 100; `CHUNK_OVERLAP_LINES`, default 15), preserving `start_line`/`end_line`; wired into `repository_ingestion.py` right after each `repository_files` row is written, populating `code_chunks` (`vector_id` stays `null` until Qdrant on Day 15); added `GET /repositories/{id}/chunks` (optional `file_id` filter); verified against real repos — a 705-line file produced 9 correctly-overlapping chunks, `github/gitignore`'s 319 files produced 345 chunks |
| Day 9 | Automated test suite (`backend/tests/`, pytest + pytest-asyncio) covering auth (register/login/me, including duplicate emails, wrong passwords, bad tokens) and repositories (create/list/get, ownership isolation, validation errors, private/oversized rejection, duplicates, empty files/chunks before ingestion) — replaces the manual curl verification used through Day 8; runs against a real Postgres database (`<db>_test`, auto-created, schema recreated per test) with GitHub API calls and the background ingestion task stubbed out so it's fast, deterministic, and never touches the dev database; 21 tests, all passing; sanity-checked by deliberately breaking a status code and confirming the suite catches it |
| Day 10 | Repository management — `DELETE /repositories/{id}` (removes the row; `repository_files`/`code_chunks`/`conversations` cascade via existing FK `ondelete="CASCADE"`) and `POST /repositories/{id}/reindex` (re-runs the Day 7-8 ingestion pipeline for an existing repository); reindex claims the job with a single conditional `UPDATE ... WHERE status NOT IN (pending, cloning, processing)` so two concurrent reindex calls can't both start an ingestion for the same repo — one succeeds, the other gets `409 Conflict`; 7 new tests (28 total) covering delete + cascade, ownership checks, the in-progress guard, and that reindex actually re-queues the background task; verified live against the real database (create → delete → cascade confirmed via `GET .../files`) |
| Day 11 | Frontend wired to the real backend — login/register are now real forms (`app/actions/auth.ts`, React Server Actions + `useActionState`) that call the Day 5 auth endpoints and store the returned JWT in an httpOnly cookie (`lib/session.ts`; expiry taken straight from the token's own `exp` claim); the dashboard (`app/dashboard/page.tsx`) is an async Server Component that calls `GET /auth/me` and `GET /repositories` directly (server-to-server, so no CORS involved) and renders real status badges instead of placeholder data, plus an add-repository form and per-repo delete wired to `POST/DELETE /repositories`; route protection lives in `src/proxy.ts` — this Next.js version (16) renamed `middleware.ts` to `proxy.ts` — doing an optimistic cookie-presence check to redirect `/dashboard` when logged out and `/login`+`/register` when already logged in; the header now shows Log out vs. Log in/Get started based on the same cookie, read in the (now async) root layout. `tsc --noEmit` and `eslint` both pass; verified live with curl against real dev servers — proxy redirects both directions, and the dashboard genuinely renders "Signed in as `<email>`" by hitting the real API with a real JWT. The interactive form submissions (actual browser clicks through login/register/add-repository) were not exercised in a real browser since no browser-automation tool is available in this environment — worth a manual click-through. |
| Day 12 | Repository detail page (`app/dashboard/[id]/page.tsx`) — clicking a repo on the dashboard now goes to a page showing full status, the failure `error_message` when one exists, default branch/size/last-updated, a Reindex button wired to Day 10's `POST /repositories/{id}/reindex` (disabled client-side while ingestion is already in progress, and the action swallows a `409` gracefully since the page revalidates either way), a Delete button that redirects back to `/dashboard`, and a scrollable table of every scanned file from `GET /repositories/{id}/files`; unknown/not-owned IDs render a custom `not-found.tsx` (backend 404 mapped via Next's `notFound()`) instead of the generic framework page. `formatBytes` moved to `lib/utils.ts` for reuse. `tsc --noEmit` and `eslint` pass clean; verified live end-to-end — real ingested repo's detail page renders its actual file (`README`), and an unknown ID correctly 404s with the site's own header/footer intact. Interactive clicks (Reindex/Delete buttons) weren't exercised in a real browser, same disclosed gap as Day 11. |
| Day 13 | Playwright end-to-end tests (`frontend/tests-e2e/`) — closes the Day 11-12 gap: a real Chromium browser now drives the actual UI instead of curl-checking SSR output. `global-setup.ts` starts the real FastAPI backend (or reuses one already running) and tears it down after; `playwright.config.ts` starts the Next.js dev server itself. 7 tests across `auth.spec.ts` (register → dashboard → log out → log back in, wrong-password error, unauthenticated redirect, authenticated-visitor redirected away from `/login`) and `repository.spec.ts` (add a real repo, wait for background ingestion to complete by polling via reload, view its scanned file, reindex, delete, invalid-URL validation error, unknown-ID not-found page) — exercising real ingestion end-to-end through the actual browser UI, not mocks. Caught two real test bugs in the first run (a race between clicking Log out and asserting the redirect landed, and `CardTitle` rendering a `<div>` rather than a semantic heading so `getByRole("heading")` never matched) — both fixed; all 7 pass. Test users are cleaned from the dev database after each run, same as every other day's manual verification. |
| Day 14 | Embedding generation (`app/services/embeddings.py`) — `generate_embedding`/`generate_embeddings` call OpenAI's `/v1/embeddings` (model from `EMBEDDING_MODEL`, default `text-embedding-3-small`), batching in groups of 100 and re-sorting each response by `index` so vectors can't silently misalign with their input texts on a reordered response. Deliberately a standalone, OpenAI-specific module rather than part of a shared "LLM provider" abstraction — Anthropic has no embeddings endpoint, so the eventual chat/RAG provider (Phase 3) and this will always be different providers regardless of how that's built. Not yet wired into the ingestion pipeline or persisted anywhere: `code_chunks.vector_id` points into Qdrant, which doesn't exist until Day 15, so wiring the two together happens then. No `OPENAI_API_KEY` was available in this environment, so verification is 6 unit tests mocking the OpenAI call via `httpx.MockTransport` (success, order-preservation on a reordered response, batch-splitting at the 100-item boundary, missing-key config error, API error, empty-input short-circuit that asserts no HTTP call is made) — live verification against the real API is a disclosed gap, same shape as the browser-testing gap on Days 11-12, until a key is added to `.env`. 34/34 backend tests pass. |

