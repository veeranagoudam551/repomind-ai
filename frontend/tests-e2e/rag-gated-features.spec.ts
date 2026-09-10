import { test, expect, type Page } from "@playwright/test";
import { uniqueEmail } from "./helpers";
import { seedRepository, seedRepositoryFiles, setRepositoryStatus } from "./seed";

// Permanent e2e coverage for the three pages gated on
// `status === "completed"` (Chat, Search, Debug) rather than just
// `file_count > 0` - closing the same "only ever throwaway-verified" gap
// file-scoped-ai-features.spec.ts closes for explain/review/architecture/
// security/agent. Real ingestion can't reach "completed" without a real
// OPENAI_API_KEY (Day 15), so the repository's status is flipped directly
// after seeding - the same trick Days 19/21/27 used manually to test
// these pages without one, now made permanent via ./seed.
//
// All three end up calling the embedding step before anything else, so
// they all hit the same documented OpenAI gap - these tests assert that
// graceful inline error, same outcome Days 19/21/27 found. The gap's
// exact shape can change independently of any code here though: earlier
// in this project OPENAI_API_KEY was simply unset ("OPENAI_API_KEY is
// not configured"), but a real key was later added to .env with no
// billing credits behind it, which instead surfaces as a real OpenAI 429
// "insufficient_quota" error - so this matches either, rather than
// assuming one specific account state.
const OPENAI_GAP_PATTERN = /OPENAI_API_KEY is not configured|insufficient_quota/;

const PASSWORD = "TestPass123!";

async function registerAndLogin(page: Page, email: string): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL("/dashboard");
}

function setUpCompletedRepository(email: string, name: string): string {
  const repoId = seedRepository(email, name);
  seedRepositoryFiles(repoId, [
    { path: "app/main.py", content: "def create_app():\n    return {}\n", language: "python" },
  ]);
  setRepositoryStatus(repoId, "completed");
  return repoId;
}

test.describe("RAG-gated pages (chat, search, debug)", () => {
  test("chat sends a message and shows it alongside a graceful error", async ({ page }) => {
    const email = uniqueEmail("e2e_chat");
    await registerAndLogin(page, email);
    const repoId = setUpCompletedRepository(email, "e2e/chat-repo");

    await page.goto(`/dashboard/${repoId}`);
    await expect(page.getByRole("link", { name: "Chat", exact: true })).toBeVisible();
    await page.getByRole("link", { name: "Chat", exact: true }).click();

    await expect(page).toHaveURL(`/dashboard/${repoId}/chat`);
    await page.getByRole("button", { name: "New chat" }).click();
    await expect(page).toHaveURL(new RegExp(`/dashboard/${repoId}/chat/.+`));

    await page.getByPlaceholder("Ask a question about this repository…").fill("what is this repo?");
    await page.getByRole("button", { name: "Send" }).click();

    // The backend persists the user's turn before the embedding call that
    // then fails (Day 19's finding), so both should show up together.
    await expect(page.getByText("what is this repo?")).toBeVisible();
    await expect(page.getByText(OPENAI_GAP_PATTERN)).toBeVisible();
  });

  test("search submits a query and shows it alongside a graceful error", async ({ page }) => {
    const email = uniqueEmail("e2e_search");
    await registerAndLogin(page, email);
    const repoId = setUpCompletedRepository(email, "e2e/search-repo");

    await page.goto(`/dashboard/${repoId}`);
    await page.getByRole("link", { name: "Search", exact: true }).click();

    await expect(page).toHaveURL(`/dashboard/${repoId}/search`);
    await page.getByPlaceholder("e.g. where is the JWT verified?").fill("where is the app created");
    await page.getByRole("button", { name: "Search" }).click();

    await expect(page).toHaveURL(/\?q=/);
    await expect(page.getByText(OPENAI_GAP_PATTERN)).toBeVisible();
    await expect(page.getByPlaceholder("e.g. where is the JWT verified?")).toHaveValue(
      "where is the app created"
    );
  });

  test("debug submits a description and shows a graceful error", async ({ page }) => {
    const email = uniqueEmail("e2e_debug");
    await registerAndLogin(page, email);
    const repoId = setUpCompletedRepository(email, "e2e/debug-repo");

    await page.goto(`/dashboard/${repoId}`);
    await page.getByRole("link", { name: "Debug", exact: true }).click();

    await expect(page).toHaveURL(`/dashboard/${repoId}/debug`);
    await page
      .getByPlaceholder("Describe the bug or paste an error message…")
      .fill("startup crashes with AttributeError");
    await page.getByRole("button", { name: "Diagnose" }).click();

    await expect(page).toHaveURL(/\?description=/);
    await expect(page.getByText(OPENAI_GAP_PATTERN)).toBeVisible();
    await expect(page.getByPlaceholder("Describe the bug or paste an error message…")).toHaveValue(
      "startup crashes with AttributeError"
    );
  });
});
