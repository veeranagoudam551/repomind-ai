import { defineConfig, devices } from "@playwright/test";

// Targets the full Docker Compose stack the CI docker-smoke job already
// builds and waits healthy for (frontend published on :3000, backend on
// :8000 - see docker/docker-compose.yml's `full` profile) - unlike
// playwright.config.ts, this has no `webServer` (the frontend is already
// running as a real container, not something to spawn via `next dev`) and
// no `globalSetup` (Postgres here is a disposable per-run container the
// docker-smoke job destroys with `down -v` afterward, so there's no
// leftover e2e_* test user to clean up the way tests-e2e/global-setup.ts
// does for the long-lived dev/CI database the regular suite runs against).
export default defineConfig({
  testDir: "./tests-e2e-docker",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { outputFolder: "playwright-report-docker", open: "never" }]],
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
