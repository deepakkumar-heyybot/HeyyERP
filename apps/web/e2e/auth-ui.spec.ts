import { expect, test } from "@playwright/test";

test("sign-in, recovery and verification screens are reachable", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await page.getByRole("link", { name: "Forgot password?" }).click();
  await expect(page.getByRole("heading", { name: "Reset your password" })).toBeVisible();
  await page.getByRole("link", { name: /back to sign in/i }).click();
  await page.getByRole("link", { name: "Resend verification link" }).click();
  await expect(page.getByRole("heading", { name: "Verify your email" })).toBeVisible();
});

test("duplicate email sign-up is rejected without exposing account existence", async ({ page }) => {
  await page.route("**/api/auth/sign-up/email", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({}),
  }));
  await page.goto("/register");
  await page.getByLabel("Full name").fill("Test User");
  await page.getByLabel("Email address").fill("already@vrub.example");
  await page.getByRole("textbox", { name: "Password" }).fill("Strong-password-123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("status")).toContainText("Already registered? Sign in or reset your password.");
});

test("registration response gives a generic verification message", async ({ page }) => {
  await page.route("**/api/auth/sign-up/email", route => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({}),
  }));
  await page.goto("/register");
  await page.getByLabel("Full name").fill("New User");
  await page.getByLabel("Email address").fill("new@vrub.example");
  await page.getByRole("textbox", { name: "Password" }).fill("Strong-password-123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("status")).toContainText("Links expire in 10 minutes");
});
