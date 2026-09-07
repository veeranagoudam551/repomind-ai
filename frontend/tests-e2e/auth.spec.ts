import { test, expect } from "@playwright/test";
import { uniqueEmail } from "./helpers";

const PASSWORD = "TestPass123!";

test.describe("authentication", () => {
  test("register, land on dashboard, log out, log back in", async ({ page }) => {
    const email = uniqueEmail("e2e_register");

    await page.goto("/register");
    await page.getByLabel("Name").fill("E2E Tester");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
    await page.getByRole("button", { name: "Create account" }).click();

    await expect(page).toHaveURL("/dashboard");
    await expect(page.getByText(`Signed in as ${email}`)).toBeVisible();

    await page.getByRole("button", { name: "Log out" }).click();
    await expect(page).toHaveURL("/login");

    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Log in" }).click();

    await expect(page).toHaveURL("/dashboard");
    await expect(page.getByText(`Signed in as ${email}`)).toBeVisible();
  });

  test("shows an error on wrong password", async ({ page }) => {
    const email = uniqueEmail("e2e_wrongpass");

    await page.goto("/register");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page).toHaveURL("/dashboard");
    await page.getByRole("button", { name: "Log out" }).click();
    await expect(page).toHaveURL("/login");

    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill("WrongPassword1!");
    await page.getByRole("button", { name: "Log in" }).click();

    await expect(page.getByText("Incorrect email or password")).toBeVisible();
    await expect(page).toHaveURL("/login");
  });

  test("redirects unauthenticated visitors away from the dashboard", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page).toHaveURL("/login");
  });

  test("redirects an authenticated visitor away from /login", async ({ page }) => {
    const email = uniqueEmail("e2e_authredirect");

    await page.goto("/register");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page).toHaveURL("/dashboard");

    await page.goto("/login");
    await expect(page).toHaveURL("/dashboard");
  });
});
