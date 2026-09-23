import { test, expect, type Page } from "@playwright/test";
import { AI_ROUND_TRIP_TIMEOUT_MS, expectGracefulOrRealResult, uniqueEmail } from "./helpers";
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
// ANTHROPIC_API_KEY-not-configured is a documented gap when no LLM
// provider is usable (see docs/architecture.md) - explain/review/
// architecture/agent all end up calling the LLM, so these tests
// originally only asserted that graceful inline error (every prior day's
// manual verification found the same thing). security_scan is the
// exception: it calls no external API at all, so its test asserts a
// real, deterministic result instead - same as Day 31's live check.
//
// Environment-aware (Groq stabilization work): CI's workflow never sets
// LLM_PROVIDER or either provider's key, so it always defaults to
// LLM_PROVIDER=anthropic with no usable key, hitting the gap above. A
// developer's own local backend may instead intentionally run
// LLM_PROVIDER=groq with a real, working GROQ_API_KEY (this project's
// own local dev setup during Agent stabilization work), in which case
// the same call succeeds with a real answer instead. Both are legitimate
// outcomes of the same, unmodified backend behavior -
// expectGracefulOrRealResult (./helpers) waits for whichever one this
// run's backend actually produces and returns which it was, so each test
// below can assert the right shape for that outcome. This keeps every
// other meaningful behavioral assertion (no premature API call, URL
// changes, disabled buttons, etc.) unconditional - only the "did the AI
// call itself succeed or fail" half is environment-aware, same reasoning
// as rag-gated-features.spec.ts's own note for EMBEDDING_PROVIDER.

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
  test("explain renders the file path and a real explanation or a graceful error", async ({
    page,
  }) => {
    const email = uniqueEmail("e2e_explain");
    await registerAndLogin(page, email);
    const repoId = seedRepository(email, "e2e/explain-repo");
    seedRepositoryFiles(repoId, [CLEAN_FILE]);

    await page.goto(`/dashboard/${repoId}`);
    await page.getByRole("link", { name: "Explain", exact: true }).click();

    // explain calls the LLM synchronously during this navigation's SSR
    // (no "run" gate like review/architecture/agent below) - the URL
    // itself won't change until that call resolves, so this needs the
    // same generous, real-provider-sized bound as the content wait below.
    await expect(page).toHaveURL(new RegExp(`/dashboard/${repoId}/files/.+/explain$`), {
      timeout: AI_ROUND_TRIP_TIMEOUT_MS,
    });
    await expect(page.getByText("Explain file", { exact: true })).toBeVisible();
    await expect(page.getByText(CLEAN_FILE.path, { exact: true })).toBeVisible();

    // app/dashboard/[id]/files/[fileId]/explain renders a real
    // explanation in this exact paragraph, distinct from the
    // destructive-styled error paragraph.
    const explanation = page.locator("p.whitespace-pre-wrap.text-sm");
    const outcome = await expectGracefulOrRealResult(
      page,
      /ANTHROPIC_API_KEY is not configured/,
      explanation
    );
    if (outcome === "success") {
      await expect(explanation).not.toBeEmpty();
    }
  });

  test("review does not call the API until the user clicks Review file, then shows a real review or a graceful error", async ({
    page,
  }) => {
    const email = uniqueEmail("e2e_review");
    await registerAndLogin(page, email);
    const repoId = seedRepository(email, "e2e/review-repo");
    seedRepositoryFiles(repoId, [CLEAN_FILE]);

    await page.goto(`/dashboard/${repoId}`);
    await page.getByRole("link", { name: "Review", exact: true }).click();

    // Scoped to the CardTitle's own [data-slot="card-title"], exact-text
    // filtered, rather than a bare getByText(..., {exact:true}): the page
    // legitimately has two "Review file" text nodes now (this heading and
    // the explicit-run button below), so a plain exact-text locator is
    // ambiguous (Playwright strict-mode violation). CardTitle renders a
    // plain <div>, not a semantic heading element, so getByRole("heading",
    // ...) can't match it either. [data-slot="card-title"] alone isn't
    // unique either - the surrounding dashboard layout renders its own
    // CardTitles (repository name, "Files (1)") - so the exact-text
    // filter is what actually narrows this to the one Review Card's own
    // title, production-code-untouched.
    await expect(
      page.locator('[data-slot="card-title"]').filter({ hasText: /^Review file$/ })
    ).toBeVisible();
    await expect(page.getByText(CLEAN_FILE.path, { exact: true })).toBeVisible();
    // Opening the page must not call the review API on its own (the bug
    // this test guards against: a Server Component firing the AI call
    // unconditionally during render).
    await expect(page.getByText("ANTHROPIC_API_KEY is not configured")).toHaveCount(0);

    await page.getByRole("button", { name: "Review file" }).click();

    // The "?run=1" navigation's SSR blocks on the LLM call itself.
    await expect(page).toHaveURL(/\?run=1/, { timeout: AI_ROUND_TRIP_TIMEOUT_MS });

    // app/dashboard/[id]/files/[fileId]/review renders a real review in
    // this exact paragraph - same class combo as explain's success case,
    // distinct from both the placeholder and the error paragraph.
    const review = page.locator("p.whitespace-pre-wrap.text-sm");
    const outcome = await expectGracefulOrRealResult(
      page,
      /ANTHROPIC_API_KEY is not configured/,
      review
    );
    if (outcome === "success") {
      await expect(review).not.toBeEmpty();
    }
  });

  test("architecture does not call the API until the user clicks Analyze architecture, then shows a real analysis or a graceful error", async ({
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
    // Opening the page must not call the architecture API on its own (the
    // bug this test guards against: a Server Component firing the AI call
    // unconditionally during render).
    await expect(page.getByText("ANTHROPIC_API_KEY is not configured")).toHaveCount(0);

    await page.getByRole("button", { name: "Analyze architecture" }).click();

    // The "?run=1" navigation's SSR blocks on the LLM call itself.
    await expect(page).toHaveURL(/\?run=1/, { timeout: AI_ROUND_TRIP_TIMEOUT_MS });

    // app/dashboard/[id]/architecture renders a real analysis in this
    // exact paragraph - same class combo as explain/review's success
    // case, distinct from both the placeholder and the error paragraph.
    const analysis = page.locator("p.whitespace-pre-wrap.text-sm");
    const outcome = await expectGracefulOrRealResult(
      page,
      /ANTHROPIC_API_KEY is not configured/,
      analysis
    );
    if (outcome === "success") {
      await expect(analysis).not.toBeEmpty();
    }
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

  test("agent works before ingestion completes and shows a real answer or a graceful error", async ({
    page,
  }) => {
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

    // The "?goal=" navigation's SSR blocks on the full Agent run.
    await expect(page).toHaveURL(/\?goal=/, { timeout: AI_ROUND_TRIP_TIMEOUT_MS });

    // app/dashboard/[id]/agent renders a real final answer in this exact
    // bordered/muted paragraph. A working agent that legitimately
    // finishes without producing an answer is a real, distinct outcome
    // the page supports (its own "completed without producing an
    // answer" copy) - but it is not a successful AI response, and
    // silently accepting it here would let a real Agent regression (e.g.
    // a broken forced-finish path) pass as if nothing were wrong. Only a
    // real answer or the documented config gap are accepted; anything
    // else - including that no-answer outcome - times out and fails.
    const answer = page.locator("p.rounded-md.bg-muted");
    const outcome = await expectGracefulOrRealResult(
      page,
      /ANTHROPIC_API_KEY is not configured/,
      answer
    );
    if (outcome === "success") {
      await expect(answer).not.toBeEmpty();
    }
  });
});
