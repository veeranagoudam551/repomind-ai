import { defineConfig, devices } from "@playwright/test";

// Targets the full Docker Compose stack the CI docker-smoke job already
// builds and waits healthy for - see docker/docker-compose.yml's `full`
// profile. Unlike playwright.config.ts, this has no `webServer` (the
// frontend is already running as a real container, not something to
// spawn via `next dev`) and no `globalSetup` (Postgres here is a
// disposable per-run container the docker-smoke job destroys with
// `down -v` afterward, so there's no leftover e2e_* test user to clean up
// the way tests-e2e/global-setup.ts does for the long-lived dev/CI
// database the regular suite runs against).
//
// baseURL is Caddy (port 80), not the frontend container directly
// (deployment-hardening pass): frontend/api no longer publish host ports
// at all (docker/docker-compose.yml's own top-of-file comment) - Caddy is
// now the only way anything outside the Compose network, including this
// Playwright run on the CI runner, can reach the stack. This drives the
// exact same real request path a real browser would use in production:
// runner -> Caddy -> frontend (and, server-side inside that container,
// frontend -> api) - not a shortcut around Caddy. CI sets
// PRODUCTION_DOMAIN to a bare port (":80") for this job specifically, so
// Caddy serves plain HTTP here rather than attempting real ACME/TLS
// (impossible in CI - no real public domain/DNS) or its own local-CA
// HTTPS (which would need a trusted cert Playwright doesn't have) - see
// docker/Caddyfile's own comment and the docker-smoke CI job.
export default defineConfig({
  testDir: "./tests-e2e-docker",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { outputFolder: "playwright-report-docker", open: "never" }]],
  use: {
    baseURL: "http://localhost",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
