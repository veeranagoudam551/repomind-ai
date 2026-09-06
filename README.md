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

Setup instructions will be added here once the backend and frontend
scaffolding exist (see architecture doc for the current phase).
