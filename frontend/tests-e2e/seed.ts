import { execFileSync } from "node:child_process";
import { unlinkSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

// Shells out to the backend's own venv Python to seed data directly via
// its models, the same technique global-setup.ts already uses for
// cleanup and the same one used manually throughout Days 22-36's live
// verification - now made permanent so these Playwright specs don't need
// a running Celery worker or a reachable Qdrant/Redis (this environment
// doesn't reliably have either) to test pages whose backend endpoints
// don't need embeddings at all (explain/review/architecture/security/
// agent's file-only tools). Real ingestion also rolls back and persists
// zero files on any failure (Day 23's finding), which would make tests
// relying on it for file/chunk setup flaky by construction.
const BACKEND_DIR = path.resolve(__dirname, "../../backend");
const PYTHON_BIN = path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe");

export type SeedFile = { path: string; content: string; language?: string };

const CREATE_REPOSITORY_SCRIPT = `
import asyncio, sys
from app.core.database import AsyncSessionLocal
from app.models.user import User
from app.models.repository import Repository, RepositoryStatus
from sqlalchemy import select

email = sys.argv[1]
name = sys.argv[2]

async def main():
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == email))
        repo = Repository(
            owner_id=user.id,
            github_url=f"https://github.com/{name}",
            name=name,
            status=RepositoryStatus.PENDING,
        )
        db.add(repo)
        await db.commit()
        await db.refresh(repo)
        print(f"SEED_REPOSITORY_ID:{repo.id}")

asyncio.run(main())
`;

const SEED_FILES_SCRIPT = `
import asyncio, json, sys, uuid
from app.core.database import AsyncSessionLocal
from app.models.repository import Repository
from app.models.repository_file import RepositoryFile
from app.models.code_chunk import CodeChunk

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    payload = json.load(fh)

repo_id = uuid.UUID(payload["repository_id"])
files = payload["files"]

async def main():
    async with AsyncSessionLocal() as db:
        for f in files:
            rf = RepositoryFile(
                repository_id=repo_id,
                file_path=f["path"],
                language=f.get("language"),
                size_bytes=len(f["content"].encode("utf-8")),
            )
            db.add(rf)
            await db.flush()
            db.add(CodeChunk(
                repository_id=repo_id,
                repository_file_id=rf.id,
                chunk_index=0,
                content=f["content"],
                start_line=1,
                end_line=f["content"].count(chr(10)) + 1,
            ))
        repo = await db.get(Repository, repo_id)
        repo.file_count = (repo.file_count or 0) + len(files)
        await db.commit()
        print(f"[e2e] seeded {len(files)} file(s)")

asyncio.run(main())
`;

const SET_STATUS_SCRIPT = `
import asyncio, sys, uuid
from app.core.database import AsyncSessionLocal
from app.models.repository import Repository, RepositoryStatus

repo_id = uuid.UUID(sys.argv[1])
new_status = sys.argv[2]

async def main():
    async with AsyncSessionLocal() as db:
        repo = await db.get(Repository, repo_id)
        repo.status = RepositoryStatus[new_status.upper()]
        await db.commit()
        print("[e2e] status updated")

asyncio.run(main())
`;

const SEED_ID_MARKER = /SEED_REPOSITORY_ID:([0-9a-f-]{36})/i;

/** Inserts a repository row directly, bypassing the GitHub API call and
 * Celery/Redis ingestion dispatch entirely. Returns the new repository's id. */
export function seedRepository(ownerEmail: string, name = "e2e/seeded-repo"): string {
  const output = execFileSync(PYTHON_BIN, ["-c", CREATE_REPOSITORY_SCRIPT, ownerEmail, name], {
    cwd: BACKEND_DIR,
  }).toString();
  // The engine's own echo=True SQL logging lands on stdout too, and its
  // INSERT statement's own log line embeds *several* UUIDs (owner_id
  // among them) before our print ever runs - a bare UUID-shaped regex
  // would grab the wrong one, so the script wraps its real answer in an
  // unambiguous sentinel instead of relying on shape or line position.
  const match = output.match(SEED_ID_MARKER);
  if (!match) {
    throw new Error(`seedRepository: no repository id found in seed script output:\n${output}`);
  }
  return match[1];
}

/** Inserts real repository_files + code_chunks rows and bumps file_count. */
export function seedRepositoryFiles(repositoryId: string, files: SeedFile[]): void {
  const tmpFile = path.join(
    os.tmpdir(),
    `e2e-seed-${Date.now()}-${Math.random().toString(36).slice(2)}.json`
  );
  writeFileSync(tmpFile, JSON.stringify({ repository_id: repositoryId, files }), "utf-8");
  try {
    execFileSync(PYTHON_BIN, ["-c", SEED_FILES_SCRIPT, tmpFile], {
      cwd: BACKEND_DIR,
      stdio: "inherit",
    });
  } finally {
    unlinkSync(tmpFile);
  }
}

export type SeedRepositoryStatus = "pending" | "cloning" | "processing" | "completed" | "failed";

/** Flips a repository's status directly - the same trick Days 19/21/27
 * used manually to test embeddings-gated UI (Chat/Search/Debug) without a
 * real OPENAI_API_KEY, since real ingestion can't reach "completed"
 * without one. */
export function setRepositoryStatus(repositoryId: string, status: SeedRepositoryStatus): void {
  execFileSync(PYTHON_BIN, ["-c", SET_STATUS_SCRIPT, repositoryId, status], {
    cwd: BACKEND_DIR,
    stdio: "inherit",
  });
}
