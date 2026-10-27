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

// --- MFA policy (BRD 9.4): the ERP API decides who must enrol in two-factor auth ---
const cors = {
  "access-control-allow-origin": "http://127.0.0.1:5173",
  "access-control-allow-credentials": "true",
  "access-control-allow-headers": "content-type",
  "access-control-allow-methods": "GET,POST,OPTIONS",
};

async function mockSignInAndMe(page: import("@playwright/test").Page, me: { status: number; body: object }) {
  await page.route("**/api/auth/sign-in/email", route => route.request().method() === "OPTIONS"
    ? route.fulfill({ status: 204, headers: cors })
    : route.fulfill({
      status: 200, headers: cors, contentType: "application/json",
      body: JSON.stringify({ redirect: false, token: "t", user: { id: "u1", email: "a@vrub.example", emailVerified: true, name: "A" } }),
    }));
  await page.route("**/api/v1/me", route => route.fulfill({
    status: me.status, headers: cors, contentType: "application/json", body: JSON.stringify(me.body),
  }));
  await page.goto("/");
  await page.getByLabel("Email address").fill("a@vrub.example");
  await page.getByRole("textbox", { name: "Password" }).fill("Strong-password-123");
  await page.getByRole("button", { name: "Sign in" }).click();
}

const profile = { user_id: "u1", email: "a@vrub.example", two_factor_enabled: false };

test("a role without an MFA requirement goes straight into the workspace", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...profile, roles: ["STORES"], mfa_required: false, access_granted: true } });
  await expect(page.getByRole("heading", { name: "Identity verified" })).toBeVisible();
});

test("an MD who has not enrolled in two-factor is sent to authenticator setup", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...profile, roles: ["MD"], mfa_required: true, access_granted: false } });
  await expect(page.getByRole("heading", { name: "Secure your account" })).toBeVisible();
});

test("a user outside the V-Rub organization sees the API's reason", async ({ page }) => {
  await mockSignInAndMe(page, { status: 403, body: { detail: "Not a member of the V-Rub organization" } });
  await expect(page.getByRole("alert")).toContainText("Not a member of the V-Rub organization");
});

test("Sign out ends the auth session before returning to sign-in", async ({ page }) => {
  let signedOut = false;
  await page.route("**/api/auth/sign-out", route => {
    signedOut = route.request().method() !== "OPTIONS" || signedOut;
    return route.fulfill({ status: route.request().method() === "OPTIONS" ? 204 : 200, headers: cors, contentType: "application/json", body: route.request().method() === "OPTIONS" ? "" : JSON.stringify({ success: true }) });
  });
  await mockSignInAndMe(page, { status: 200, body: { ...profile, roles: ["STORES"], mfa_required: false, access_granted: true } });
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  expect(signedOut).toBe(true);
});
