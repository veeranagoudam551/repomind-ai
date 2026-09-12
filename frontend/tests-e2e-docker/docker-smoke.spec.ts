import { test, expect, type Page } from "@playwright/test";
import { uniqueEmail } from "../tests-e2e/helpers";

// Production-like smoke test for the full Docker Compose stack (Day 55) -
// proves a real browser can drive the actual containerized frontend,
// which in turn talks to the actual containerized FastAPI backend and
// Postgres/Redis, over the same published ports CI's docker-smoke job
// already waits healthy for. Deliberately a single, small test, not a
// second copy of tests-e2e/'s full suite - see playwright.docker.config.ts
// for how this is kept isolated from that regular suite.
//
// No OPENAI_API_KEY/ANTHROPIC_API_KEY is configured anywhere in this CI
// job (same reasoning as the regular e2e job, README's CI section), so
// real ingestion of the repository below can only ever reach "failed"
// with that specific, expected error - never "completed". That's fine:
// this test only needs to prove the full request path (browser -> Next.js
// -> FastAPI -> Postgres/Redis) works end to end, not that ingestion
// itself succeeds, so it accepts either terminal outcome the same way
// tests-e2e/repository.spec.ts already does against the non-Dockerized
// stack.
const PASSWORD = "TestPass123!";
const INGESTION_FAILURE_PATTERN =
  /OPENAI_API_KEY is not configured|insufficient_quota|background worker is unreachable/;

async function waitForIngestionOutcome(page: Page): Promise<"completed" | "failed"> {
  let status: "completed" | "failed" | undefined;
  await expect(async () => {
    await page.reload();
    const badge = page.getByText(/^(completed|failed)$/);
    await expect(badge).toBeVisible();
    status = (await badge.textContent()) as "completed" | "failed";
  }).toPass({ timeout: 30_000 });
  return status!;
}

test("full Docker stack: register, add a repository, view it, and log out", async ({ page }) => {
  test.setTimeout(60_000);

  const email = uniqueEmail("e2e_docker");

  await page.goto("/register");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL("/dashboard");
  await expect(page.getByText(`Signed in as ${email}`)).toBeVisible();

  await page
    .getByPlaceholder("owner/repo or https://github.com/owner/repo")
    .fill("octocat/Hello-World");
  await page.getByRole("button", { name: "Add repository" }).click();

  const repoLink = page.getByRole("link", { name: "octocat/Hello-World" });
  await expect(repoLink).toBeVisible({ timeout: 15_000 });

  await repoLink.click();
  await expect(page).toHaveURL(/\/dashboard\/[0-9a-f-]{36}$/);
  await expect(page.getByText("octocat/Hello-World", { exact: true })).toBeVisible();

  const status = await waitForIngestionOutcome(page);
  if (status === "completed") {
    await expect(page.getByText("README")).toBeVisible();
  } else {
    await expect(page.getByText(INGESTION_FAILURE_PATTERN)).toBeVisible();
  }

  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL("/login");

  await page.goto("/dashboard");
  await expect(page).toHaveURL("/login");
});
