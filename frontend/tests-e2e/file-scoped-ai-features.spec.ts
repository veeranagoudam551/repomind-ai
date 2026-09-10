import { test, expect, type Page } from "@playwright/test";
import { uniqueEmail } from "./helpers";
import { seedRepository, seedRepositoryFiles } from "./seed";

// Permanent e2e coverage for the pages Days 22-36 built that only need
// scanned files (not embeddings/Qdrant) - explain, review, architecture,
// security, and the agent's file-scoped tools - closing the gap those
// days left: every one of them was only ever checked with a throwaway
// Playwright script deleted right after, per docs/architecture.md's own
// entries. Repositories here are seeded directly (see ./seed) rather
// than added through the real GitHub-backed form, since none of these
// pages need real ingestion and a real run isn't reliable in this
// environment anyway (no Qdrant/Redis guaranteed running).
//
// ANTHROPIC_API_KEY is a documented, ongoing gap in this environment (see
// docs/architecture.md) - explain/review/architecture/agent all end up
// calling it, so these tests assert the graceful inline error, the same
// outcome every prior day's manual verification found. security_scan is
// the exception: it calls no external API at all, so its test asserts a
// real, deterministic result instead - same as Day 31's live check.

const PASSWORD = "TestPass123!";

async function registerAndLogin(page: Page, email: string): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL("/dashboard");
}

const CLEAN_FILE = {
  path: "app/main.py",
  content: "def create_app():\n    return {}\n",
  language: "python",
};
const README_FILE = {
  path: "README.md",
  content: "# Demo\nA small demo Flask app.\n",
  language: "markdown",
};
const UNSAFE_FILE = {
  path: "app/config.py",
  content: "DB_PASSWORD = 'reallysecretpass123'\nresult = eval(user_input)\n",
  language: "python",
};

test.describe("file-scoped AI features (explain, review, architecture, security, agent)", () => {
  test("explain renders the file path and a graceful error without a real ANTHROPIC_API_KEY", async ({
    page,
  }) => {
    const email = uniqueEmail("e2e_explain");
    await registerAndLogin(page, email);
    const repoId = seedRepository(email, "e2e/explain-repo");
    seedRepositoryFiles(repoId, [CLEAN_FILE]);

    await page.goto(`/dashboard/${repoId}`);
    await page.getByRole("link", { name: "Explain", exact: true }).click();

    await expect(page).toHaveURL(new RegExp(`/dashboard/${repoId}/files/.+/explain$`));
    await expect(page.getByText("Explain file", { exact: true })).toBeVisible();
    await expect(page.getByText(CLEAN_FILE.path, { exact: true })).toBeVisible();
    await expect(page.getByText("ANTHROPIC_API_KEY is not configured")).toBeVisible();
  });

  test("review renders the file path and a graceful error without a real ANTHROPIC_API_KEY", async ({
    page,
  }) => {
    const email = uniqueEmail("e2e_review");
    await registerAndLogin(page, email);
    const repoId = seedRepository(email, "e2e/review-repo");
    seedRepositoryFiles(repoId, [CLEAN_FILE]);

    await page.goto(`/dashboard/${repoId}`);
    await page.getByRole("link", { name: "Review", exact: true }).click();

    await expect(page.getByText("Review file", { exact: true })).toBeVisible();
    await expect(page.getByText(CLEAN_FILE.path, { exact: true })).toBeVisible();
    await expect(page.getByText("ANTHROPIC_API_KEY is not configured")).toBeVisible();
  });

  test("architecture works before ingestion completes and shows a graceful error", async ({
    page,
  }) => {
    const email = uniqueEmail("e2e_architecture");
    await registerAndLogin(page, email);
    const repoId = seedRepository(email, "e2e/architecture-repo");
    seedRepositoryFiles(repoId, [CLEAN_FILE, README_FILE]);

    await page.goto(`/dashboard/${repoId}`);
    // Repository is still "pending" (never ingested), so Chat/Search/Debug
    // stay disabled while Architecture is enabled - it only needs files.
    await expect(page.getByRole("button", { name: "Chat" })).toBeDisabled();
    await page.getByRole("link", { name: "Architecture", exact: true }).click();

    await expect(page.getByText("Architecture", { exact: true })).toBeVisible();
    await expect(page.getByText("Based on 2 scanned files", { exact: true })).toBeVisible();
    await expect(page.getByText("ANTHROPIC_API_KEY is not configured")).toBeVisible();
  });

  test("security scan finds real issues in an unsafe file and none in a clean one", async ({
    page,
  }) => {
    const email = uniqueEmail("e2e_security");
    await registerAndLogin(page, email);
    const repoId = seedRepository(email, "e2e/security-repo");
    seedRepositoryFiles(repoId, [CLEAN_FILE, UNSAFE_FILE]);

    await page.goto(`/dashboard/${repoId}`);
    await page.getByRole("link", { name: "Security", exact: true }).click();

    await expect(page.getByText("Security scan", { exact: true })).toBeVisible();
    // security_scan needs no API key at all, so this is a real result, not
    // a graceful-failure check like the other tests in this file.
    await expect(page.getByText(`${UNSAFE_FILE.path}:1`)).toBeVisible();
    await expect(page.getByText("Possible hardcoded credential or secret")).toBeVisible();
    await expect(page.getByText(`${UNSAFE_FILE.path}:2`)).toBeVisible();
    await expect(
      page.getByText("Use of eval()/exec() on potentially untrusted input")
    ).toBeVisible();
    await expect(page.getByText(CLEAN_FILE.path)).toHaveCount(0);
  });

  test("agent works before ingestion completes and shows a graceful error", async ({ page }) => {
    const email = uniqueEmail("e2e_agent");
    await registerAndLogin(page, email);
    const repoId = seedRepository(email, "e2e/agent-repo");
    seedRepositoryFiles(repoId, [CLEAN_FILE]);

    await page.goto(`/dashboard/${repoId}`);
    await expect(page.getByRole("button", { name: "Chat" })).toBeDisabled();
    await page.getByRole("link", { name: "Agent", exact: true }).click();

    await expect(page.getByText("Agent", { exact: true })).toBeVisible();
    await page
      .getByPlaceholder("e.g. figure out what this repo does and summarize its entry point")
      .fill("what does this repository do?");
    await page.getByRole("button", { name: "Run" }).click();

    await expect(page).toHaveURL(/\?goal=/);
    await expect(page.getByText("ANTHROPIC_API_KEY is not configured")).toBeVisible();
  });
});
