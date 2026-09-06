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

## 4. Data Model (initial design, Phase 1)

| Table | Purpose |
|---|---|
| `users` | Account credentials (hashed passwords), profile |
| `repositories` | A GitHub repo a user has added, plus processing status/stats |
| `conversations` | A chat thread scoped to one repository |
| `messages` | Individual turns within a conversation |
| `repository_files` | Per-file metadata for a processed repository |
| `code_chunks` | Metadata for each chunk sent to the vector DB (vectors themselves live in Qdrant, not Postgres) |

Full column definitions are added in Day 4 alongside the SQLAlchemy
models, so the doc and the code stay in sync rather than drifting.

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

