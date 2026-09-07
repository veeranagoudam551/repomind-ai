import { execFileSync, spawn } from "node:child_process";
import path from "node:path";

const BACKEND_DIR = path.resolve(__dirname, "../../backend");
const PYTHON_BIN = path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe");
const HEALTH_URL = "http://localhost:8000/health";

// All e2e-created accounts use uniqueEmail() from ./helpers, which always
// prefixes with "e2e_" — see there for the exact format.
const CLEANUP_SCRIPT = `
import asyncio
from app.core.database import AsyncSessionLocal
from app.models.user import User
from sqlalchemy import select, delete

async def main():
    async with AsyncSessionLocal() as db:
        users = (await db.scalars(select(User).where(User.email.like("e2e_%@example.com")))).all()
        for u in users:
            await db.execute(delete(User).where(User.id == u.id))
        await db.commit()
        print(f"[e2e] cleaned up {len(users)} test user(s)")

asyncio.run(main())
`;

async function isHealthy(): Promise<boolean> {
  try {
    const res = await fetch(HEALTH_URL);
    return res.ok;
  } catch {
    return false;
  }
}

async function waitForHealth(timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await isHealthy()) return;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Backend at ${HEALTH_URL} did not become healthy in time`);
}

function cleanUpTestUsers(): void {
  try {
    execFileSync(PYTHON_BIN, ["-c", CLEANUP_SCRIPT], { cwd: BACKEND_DIR, stdio: "inherit" });
  } catch (err) {
    console.warn("[e2e] Failed to clean up test users (leftover e2e_* rows in the dev DB):", err);
  }
}

// The e2e suite exercises real ingestion (Days 6-10), so it needs the real
// FastAPI backend, not a mock. If it's already running (e.g. a dev session),
// reuse it and leave it running; otherwise start and stop it ourselves.
// Either way, the test users these specs create get cleaned up afterward.
export default async function globalSetup() {
  if (await isHealthy()) {
    console.log("[e2e] Backend already running at :8000, reusing it.");
    return async () => cleanUpTestUsers();
  }

  console.log("[e2e] Starting backend (uvicorn)...");
  const backend = spawn(PYTHON_BIN, ["-m", "uvicorn", "app.main:app", "--port", "8000"], {
    cwd: BACKEND_DIR,
    stdio: "ignore",
  });

  await waitForHealth(30_000);
  console.log("[e2e] Backend is healthy.");

  return async () => {
    cleanUpTestUsers();
    console.log("[e2e] Stopping backend...");
    backend.kill();
  };
}
