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

