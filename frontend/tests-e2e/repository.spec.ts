import { test, expect, type Page } from "@playwright/test";
import { uniqueEmail } from "./helpers";

const PASSWORD = "TestPass123!";

async function registerAndLogin(page: Page, email: string): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL("/dashboard");
}

test.describe("repository management", () => {
  test("add, view, reindex, and delete a repository", async ({ page }) => {
    test.setTimeout(60_000);

    const email = uniqueEmail("e2e_repo");
    await registerAndLogin(page, email);

    await page
      .getByPlaceholder("owner/repo or https://github.com/owner/repo")
      .fill("octocat/Hello-World");
    await page.getByRole("button", { name: "Add repository" }).click();

    const repoLink = page.getByRole("link", { name: "octocat/Hello-World" });
    await expect(repoLink).toBeVisible();

    await repoLink.click();
    await expect(page).toHaveURL(/\/dashboard\/[0-9a-f-]{36}$/);
    await expect(page.getByText("octocat/Hello-World", { exact: true })).toBeVisible();

    // Ingestion runs as a background task on the server; the detail page
    // doesn't auto-refresh, so poll via reload until it's done.
    await expect(async () => {
      await page.reload();
      await expect(page.getByText("completed", { exact: true })).toBeVisible();
    }).toPass({ timeout: 20_000 });

    await expect(page.getByText("README")).toBeVisible();

    await page.getByRole("button", { name: "Reindex" }).click();
    await expect(async () => {
      await page.reload();
      await expect(page.getByText("completed", { exact: true })).toBeVisible();
    }).toPass({ timeout: 20_000 });
    await expect(page.getByText("README")).toBeVisible();

    await page.getByRole("button", { name: "Delete" }).click();
    await expect(page).toHaveURL("/dashboard");
    await expect(page.getByText("No repositories yet")).toBeVisible();
  });

  test("shows a validation error for an invalid GitHub URL", async ({ page }) => {
    const email = uniqueEmail("e2e_repo_invalid");
    await registerAndLogin(page, email);

    await page
      .getByPlaceholder("owner/repo or https://github.com/owner/repo")
      .fill("not a valid url");
    await page.getByRole("button", { name: "Add repository" }).click();

    await expect(page.getByText(/is not a valid GitHub repository URL/)).toBeVisible();
  });

  test("unknown repository id shows the not-found page", async ({ page }) => {
    const email = uniqueEmail("e2e_repo_404");
    await registerAndLogin(page, email);

    await page.goto("/dashboard/00000000-0000-0000-0000-000000000000");
    await expect(page.getByText("Repository not found")).toBeVisible();
    await expect(page.getByRole("link", { name: "Back to repositories" })).toBeVisible();
  });
});
