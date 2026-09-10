import { test, expect, type Page } from "@playwright/test";
import { uniqueEmail } from "./helpers";
import { seedRepository } from "./seed";

// Day 45: GET /repositories now returns a paginated envelope (default
// page_size 10). Repositories are seeded directly (same technique as
// rag-gated-features.spec.ts/file-scoped-ai-features.spec.ts) rather than
// added through the real GitHub-backed flow repository.spec.ts already
// covers - pagination is purely a listing concern, so it doesn't need
// real ingestion, ownership of a real GitHub repo, or any AI provider at
// all, and creating a dozen real repositories per test would also burn
// through the GitHub API rate limit for no benefit.

const PASSWORD = "TestPass123!";

async function registerAndLogin(page: Page, email: string): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL("/dashboard");
}

function seedRepositories(email: string, count: number): void {
  for (let i = 0; i < count; i++) {
    seedRepository(email, `e2e/paginate-repo-${i}`);
  }
}

// Scoped to <main> throughout: Next.js's own dev-mode overlay renders a
// floating "Open Next.js Dev Tools" button outside it, and getByRole's
// name matching is substring-by-default - "Next" alone matches "Next.js"
// too, a real strict-mode violation caught by actually running this.
function main(page: Page) {
  return page.locator("main");
}

test.describe("dashboard pagination", () => {
  test("shows no pagination controls when everything fits on one page", async ({ page }) => {
    const email = uniqueEmail("e2e_page_one");
    await registerAndLogin(page, email);
    seedRepositories(email, 3);

    await page.reload();
    await expect(page.getByText(/^e2e\/paginate-repo-/).first()).toBeVisible();
    await expect(main(page).getByRole("button", { name: "Previous", exact: true })).not.toBeVisible();
    await expect(main(page).getByRole("button", { name: "Next", exact: true })).not.toBeVisible();
    await expect(page.getByText(/Page \d+ of \d+/)).not.toBeVisible();
  });

  test("navigates between pages and disables the boundary buttons", async ({ page }) => {
    const email = uniqueEmail("e2e_page_nav");
    await registerAndLogin(page, email);
    seedRepositories(email, 12);

    await page.goto("/dashboard");
    await expect(page.getByText("Page 1 of 2")).toBeVisible();
    // Button asChild renders the enabled state as a real <a> (role "link"),
    // and only the disabled state as a native <button> - same distinction
    // rag-gated-features.spec.ts/file-scoped-ai-features.spec.ts already
    // rely on for the Chat/Search/Debug buttons.
    await expect(main(page).getByRole("button", { name: "Previous", exact: true })).toBeDisabled();
    await expect(main(page).getByRole("link", { name: "Next", exact: true })).toBeVisible();
    await expect(main(page).getByRole("link", { name: /e2e\/paginate-repo-/ })).toHaveCount(10);

    await main(page).getByRole("link", { name: "Next", exact: true }).click();
    await expect(page).toHaveURL(/\/dashboard\?page=2$/);
    await expect(page.getByText("Page 2 of 2")).toBeVisible();
    await expect(main(page).getByRole("button", { name: "Next", exact: true })).toBeDisabled();
    await expect(main(page).getByRole("link", { name: "Previous", exact: true })).toBeVisible();
    await expect(main(page).getByRole("link", { name: /e2e\/paginate-repo-/ })).toHaveCount(2);

    await main(page).getByRole("link", { name: "Previous", exact: true }).click();
    await expect(page).toHaveURL("/dashboard");
    await expect(page.getByText("Page 1 of 2")).toBeVisible();
  });

  test("a page past the end shows a graceful message instead of a blank list", async ({ page }) => {
    const email = uniqueEmail("e2e_page_overflow");
    await registerAndLogin(page, email);
    seedRepositories(email, 3);

    await page.goto("/dashboard?page=5");
    await expect(page.getByText("Nothing on page 5.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Back to page 1" })).toBeVisible();
    await expect(page.getByText("No repositories yet")).not.toBeVisible();
  });
});
