import { test, expect, type Page } from "@playwright/test";
import { AI_ROUND_TRIP_TIMEOUT_MS, expectGracefulOrRealResult, uniqueEmail } from "./helpers";
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
// with no usable embedding provider they all hit the same documented
// OpenAI gap - these tests originally only asserted that graceful inline
// error (Days 19/21/27's manual finding). The gap's exact shape can
// change independently of any code here though: earlier in this project
// OPENAI_API_KEY was simply unset ("OPENAI_API_KEY is not configured"),
// but a real key was later added to .env with no billing credits behind
// it, which instead surfaces as a real OpenAI 429 "insufficient_quota"
// error - so this matches either, rather than assuming one specific
// account state.
const OPENAI_GAP_PATTERN = /OPENAI_API_KEY is not configured|insufficient_quota/;

// Environment-aware (Groq/local-embedding stabilization work): CI's
// workflow never sets EMBEDDING_PROVIDER or OPENAI_API_KEY, so it always
// hits OPENAI_GAP_PATTERN above. A developer's own local backend may
// instead intentionally run EMBEDDING_PROVIDER=local
// (app/services/embedding_providers.py, no OpenAI cost) for day-to-day
// development, in which case the same call succeeds with a real result
// instead of erroring. Both are legitimate outcomes of the same,
// unmodified backend behavior - expectGracefulOrRealResult (./helpers)
// waits for whichever one this run's backend actually produces and
// returns which it was, so each test below can assert the right shape
// for that outcome rather than assuming one specific environment. This
// keeps every meaningful behavioral assertion (the user's own message
// persisting, the query staying in the input, no premature API call,
// etc.) unconditional - only the "did the AI call itself succeed or fail"
// half is environment-aware.

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
  test("chat sends a message and shows it alongside a real answer or a graceful error", async ({
    page,
  }) => {
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

    // The backend persists the user's turn before the embedding/LLM calls
    // that may then fail (Day 19's finding), so this shows up either way -
    // but the send form is a server action that doesn't resolve until the
    // whole round trip (including a real provider's LLM call) finishes,
    // so this needs the same generous, real-provider-sized bound as the
    // reply wait below.
    await expect(page.getByText("what is this repo?")).toBeVisible({
      timeout: AI_ROUND_TRIP_TIMEOUT_MS,
    });

    // MessageBubble (components/send-message-form.tsx's page) renders the
    // assistant's own reply with this exact class combo, distinct from
    // the user's bubble (bg-primary) - see ./helpers.
    const assistantReply = page.locator("div.bg-muted.text-foreground");
    const outcome = await expectGracefulOrRealResult(page, OPENAI_GAP_PATTERN, assistantReply);
    if (outcome === "success") {
      await expect(assistantReply).not.toBeEmpty();
    }
  });

  test("search submits a query and shows real results or a graceful error", async ({ page }) => {
    const email = uniqueEmail("e2e_search");
    await registerAndLogin(page, email);
    const repoId = setUpCompletedRepository(email, "e2e/search-repo");

    await page.goto(`/dashboard/${repoId}`);
    await page.getByRole("link", { name: "Search", exact: true }).click();

    await expect(page).toHaveURL(`/dashboard/${repoId}/search`);
    await page.getByPlaceholder("e.g. where is the JWT verified?").fill("where is the app created");
    await page.getByRole("button", { name: "Search" }).click();

    // The "?q=" navigation's SSR blocks on the embedding call itself.
    await expect(page).toHaveURL(/\?q=/, { timeout: AI_ROUND_TRIP_TIMEOUT_MS });

    // A working embedding provider returns either a real match list or a
    // deterministic "no matches" message (app/dashboard/[id]/search) -
    // both are a successful, non-error search, unlike OPENAI_GAP_PATTERN.
    const noMatches = page.getByText(/No matches found for/);
    const searchResults = page.locator("ul li");
    await expectGracefulOrRealResult(page, OPENAI_GAP_PATTERN, noMatches.or(searchResults.first()));

    await expect(page.getByPlaceholder("e.g. where is the JWT verified?")).toHaveValue(
      "where is the app created"
    );
  });

  test("debug submits a description and shows a real diagnosis or a graceful error", async ({
    page,
  }) => {
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

    // The "?description=" navigation's SSR blocks on the embedding + LLM
    // calls themselves.
    await expect(page).toHaveURL(/\?description=/, { timeout: AI_ROUND_TRIP_TIMEOUT_MS });

    // app/dashboard/[id]/debug renders a real diagnosis in this exact
    // bordered/muted paragraph, distinct from the plain-text placeholder
    // and the destructive-styled error paragraph.
    const diagnosis = page.locator("p.rounded-md.bg-muted");
    const outcome = await expectGracefulOrRealResult(page, OPENAI_GAP_PATTERN, diagnosis);
    if (outcome === "success") {
      await expect(diagnosis).not.toBeEmpty();
    }

    await expect(page.getByPlaceholder("Describe the bug or paste an error message…")).toHaveValue(
      "startup crashes with AttributeError"
    );
  });
});
