import { expect, test } from '@playwright/test';
import { cors, InvitationResponse, mockInvitationAttempt, submitInvitationSignIn, submitPasswordRecovery, totpUri, backupCodes, openAuthenticatorSetup, mockTwoFactorSignIn, submitTwoFactorSignIn, MeFixture, UnitFixture, CountFixtures, BootstrapResponse, BootstrapFixtures, defaultUnits, mockSignInAndMe, grantedProfile, approvalOne, approvalTwo, mockApprovals, approvalDetail, mockApprovalDetail, notificationOne, notificationTwo, mockNotifications, publicApprovalToken, publicApproval, mockPublicApproval, plantAlpha, plantBeta, departmentProduction, departmentFinishing, mockDepartments, storeRaw, storeFinished, mockStores, paymentTermNet30, taxCodeGst18, categoryRubber, categoryNatural, uomKilogram, mockReferenceData, vendorOrbit, mockVendors, mockCustomerDetail, customerAcme, mockCustomers, mockVendorDetail, itemRubber, mockItems, itemFinished, itemPolicy, itemSpec, itemBom, itemChange, itemPart, mockItemDetail, mockBoms, bomDraft, bomPrevious, mockBomDetail, routeMouldV2, routeMouldV1, routeMetal, mockRoutes, indentProfile, indentAlpha, indentBeta, mockIndentRegister, indentDetailFixture, indentPutCors, mockOwnedIndent, rfqProfile, rfqAlpha, rfqBeta, rfqLineFixture, rfqInvitations, rfqDetailFixture, mockRfqRegister, poAlpha, poBeta, poLine, poDetail, mockPoRegister, mockPoEditCatalog, inventoryProfile, grnAlpha, grnBeta, grnDetail, mockGrnRegister, mockGrnCreationSources, stockAlpha, stockBeta, stockTrace, mockStockSources, issueProfile, issuePlan, postedIssue, issueSummary, mockIssueHistory } from './support/fixtures';

test("sign-in, recovery and verification screens are reachable", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await page.getByRole("link", { name: "Forgot password?" }).click();
  await expect(page.getByRole("heading", { name: "Reset your password" })).toBeVisible();
  await page.getByRole("link", { name: /back to sign in/i }).click();
  await page.getByRole("link", { name: "Resend verification link" }).click();
  await expect(page.getByRole("heading", { name: "Verify your email" })).toBeVisible();
});

test("invalid credentials remain on sign-in with a neutral recovery message", async ({ page }) => {
  await page.route("**/api/auth/sign-in/email", route => route.fulfill({
    status: 401,
    contentType: "application/json",
    body: JSON.stringify({ message: "Invalid email or password" }),
  }));
  await page.goto("/");
  await page.getByLabel("Email address").fill("user@vrub.example");
  await page.getByRole("textbox", { name: "Password" }).fill("wrong-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toContainText("Email or password is incorrect");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
});

test("authentication network failure explains recovery without technical error text", async ({ page }) => {
  await page.route("**/api/auth/sign-in/email", route => route.abort("failed"));
  await page.goto("/");
  await page.getByLabel("Email address").fill("user@vrub.example");
  await page.getByRole("textbox", { name: "Password" }).fill("Strong-password-123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toContainText("could not reach the authentication service");
  await expect(page.getByRole("alert")).not.toContainText(/fetch|networkerror|ECONN/i);
});

test("duplicate email sign-up stays privacy-safe", async ({ page }) => {
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

test("registration gives a generic verification response", async ({ page }) => {
  await page.route("**/api/auth/sign-up/email", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({}),
  }));
  await page.goto("/register");
  await page.getByLabel("Full name").fill("New User");
  await page.getByLabel("Email address").fill("new@vrub.example");
  await page.getByRole("textbox", { name: "Password" }).fill("Strong-password-123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("status")).toContainText("Links expire in 10 minutes");
});

test("open registration and invitation onboarding present distinct tasks", async ({ page }) => {
  await page.goto("/register");
  await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();
  await expect(page.getByText("Invitation to V-Rub Suspensions")).toHaveCount(0);

  await page.goto("/accept-invitation/invite-context-123");
  await expect(page.getByRole("heading", { name: "Accept your invitation" })).toBeVisible();
  await expect(page.getByText("Invitation to V-Rub Suspensions")).toBeVisible();
  await page.getByRole("link", { name: "Create an account" }).click();
  await expect(page.getByRole("heading", { name: "Create an account for your invitation" })).toBeVisible();
});

test("invitation identifiers never appear in visible copy or console output", async ({ page }) => {
  const invitationId = "private-invitation-id-92c431";
  const consoleMessages: string[] = [];
  page.on("console", message => consoleMessages.push(message.text()));
  await page.goto(`/accept-invitation/${invitationId}`);
  await expect(page.locator("body")).not.toContainText(invitationId);
  expect(consoleMessages.join("\n")).not.toContain(invitationId);
});

test("invited registration redacts invitation identifiers from Auth errors", async ({ page }) => {
  const invitationId = "private-signup-invitation-38";
  await page.route("**/api/auth/sign-up/email", route => route.fulfill({
    status: 400,
    contentType: "application/json",
    body: JSON.stringify({ message: `Callback for ${invitationId} was rejected` }),
  }));
  await page.goto(`/register?invitationId=${invitationId}`);
  await page.getByLabel("Full name").fill("New Invitee");
  await page.getByLabel("Email address").fill("new-invitee@vrub.example");
  await page.getByRole("textbox", { name: "Password" }).fill("Strong-password-123");
  await page.getByRole("button", { name: /create account/i }).click();
  await expect(page.getByRole("alert")).toContainText("We could not create your account for this invitation");
  await expect(page.locator("body")).not.toContainText(invitationId);
});

test("an invalid invitation recovers without exposing the backend token", async ({ page }) => {
  const invitationId = "private-invalid-invitation-71";
  await mockInvitationAttempt(page, {
    status: 400,
    body: { message: `Invitation ${invitationId} not found` },
  });
  await submitInvitationSignIn(page, invitationId);
  await expect(page.getByRole("heading", { name: "Invitation unavailable" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("This invitation is no longer available");
  await expect(page.locator("body")).not.toContainText(invitationId);
  const continueLink = page.getByRole("link", { name: "Continue to heyyERP" });
  await expect(continueLink).toHaveAttribute("href", "/app");
  const contrast = await continueLink.evaluate(element => {
    const parse = (value: string) => value.match(/\d+/g)!.slice(0, 3).map(Number);
    const luminance = ([r, g, b]: number[]) => {
      const channels = [r, g, b].map(channel => {
        const normalized = channel / 255;
        return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
    };
    const style = getComputedStyle(element);
    const foreground = luminance(parse(style.color));
    const background = luminance(parse(style.backgroundColor));
    return (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05);
  });
  expect(contrast).toBeGreaterThanOrEqual(4.5);
});

test("an expired invitation can restart safely with another account", async ({ page }) => {
  await mockInvitationAttempt(page, {
    status: 400,
    body: { message: "Invitation has expired" },
  });
  let signedOut = false;
  await page.route("**/api/auth/sign-out", route => {
    signedOut = route.request().method() !== "OPTIONS" || signedOut;
    return route.fulfill({
      status: route.request().method() === "OPTIONS" ? 204 : 200,
      headers: cors,
      contentType: "application/json",
      body: route.request().method() === "OPTIONS" ? "" : JSON.stringify({ success: true }),
    });
  });
  await submitInvitationSignIn(page, "expired-invitation-44");
  await page.getByRole("button", { name: "Use another account" }).click();
  await expect.poll(() => signedOut).toBe(true);
  await expect(page.getByRole("heading", { name: "Accept your invitation" })).toBeVisible();
});

test("a valid existing-user invitation continues to ERP access resolution", async ({ page }) => {
  await mockInvitationAttempt(page, { status: 200, body: { invitation: { status: "accepted" } } });
  await page.route("**/api/v1/me", route => route.fulfill({
    status: 403,
    headers: cors,
    contentType: "application/json",
    body: JSON.stringify({ detail: "ERP access is still being provisioned" }),
  }));
  await submitInvitationSignIn(page, "valid-invitation-55");
  await expect(page).toHaveURL(/\/app/);
});

test("registration blocks passwords shorter than 12 characters before contacting Auth", async ({ page }) => {
  let signUpRequests = 0;
  await page.route("**/api/auth/sign-up/email", route => {
    signUpRequests += 1;
    return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
  await page.goto("/register");
  await page.getByLabel("Full name").fill("New User");
  await page.getByLabel("Email address").fill("new@vrub.example");
  await page.getByRole("textbox", { name: "Password" }).fill("too-short");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("textbox", { name: "Password" })).toBeFocused();
  expect(signUpRequests).toBe(0);
});

for (const scenario of [
  { name: "known account", status: 200, body: { status: true, message: "Check your email" } },
  { name: "unknown account response", status: 404, body: { message: "User not found" } },
  { name: "mail delivery failure", status: 500, body: { message: "SMTP delivery failed for existing user" } },
]) {
  test(`password recovery stays account-agnostic for a ${scenario.name}`, async ({ page }) => {
    await page.route("**/api/auth/request-password-reset", route => route.fulfill({
      status: scenario.status,
      contentType: "application/json",
      body: JSON.stringify(scenario.body),
    }));
    await submitPasswordRecovery(page);
    await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();
    await expect(page.getByRole("status")).toContainText("If an account matches that email");
    await expect(page.locator("body")).not.toContainText(/user not found|unknown account|smtp|delivery failed/i);
    await expect(page.getByRole("link", { name: "Back to sign in" })).toHaveAttribute("href", "/");
  });
}

for (const url of ["/reset-password", "/reset-password?error=INVALID_TOKEN"]) {
  test(`reset recovery is immediate when the callback is unavailable: ${url}`, async ({ page }) => {
    await page.goto(url);
    await expect(page).toHaveURL(/\/reset-password$/);
    await expect(page.getByRole("heading", { name: "Reset link unavailable" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "New password" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Request a new reset link" })).toHaveAttribute("href", "/forgot-password");
    await expect(page.getByRole("link", { name: "Back to sign in" })).toHaveAttribute("href", "/");
  });
}

for (const scenario of [
  { name: "expired", message: (token: string) => `Reset token ${token} expired` },
  { name: "already used", message: (token: string) => `Reset token ${token} has already been used` },
]) {
  test(`${scenario.name} reset tokens recover safely without rendering the token`, async ({ page }) => {
    const token = `private-${scenario.name.replace(" ", "-")}-reset-token`;
    const consoleOutput: string[] = [];
    page.on("console", message => consoleOutput.push(message.text()));
    await page.route("**/api/auth/reset-password", route => route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({ message: scenario.message(token), code: "INVALID_TOKEN" }),
    }));
    await page.goto(`/reset-password?token=${token}`);
    await expect(page).toHaveURL(/\/reset-password$/);
    await page.getByRole("textbox", { name: "New password" }).fill("Strong-password-123");
    await page.getByRole("button", { name: "Update password" }).click();
    await expect(page.getByRole("heading", { name: "Reset link unavailable" })).toBeVisible();
    await expect(page.getByRole("alert")).toContainText(/expired.*already been used/i);
    await expect(page.locator("body")).not.toContainText(token);
    expect(consoleOutput.join("\n")).not.toContain(token);
    await expect(page.getByRole("link", { name: "Back to sign in" })).toHaveAttribute("href", "/");
  });
}

for (const scenario of [
  { name: "shorter than 12 characters", password: "short-pass" },
  { name: "longer than 128 characters", password: "x".repeat(129) },
]) {
  test(`reset rejects a password ${scenario.name} before contacting Auth`, async ({ page }) => {
    let resetRequests = 0;
    await page.route("**/api/auth/reset-password", route => {
      resetRequests += 1;
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: true }) });
    });
    await page.goto("/reset-password?token=valid-reset-token");
    await page.getByRole("textbox", { name: "New password" }).fill(scenario.password);
    await page.getByRole("button", { name: "Update password" }).click();
    await expect(page.getByRole("alert")).toContainText("12 to 128 characters");
    expect(resetRequests).toBe(0);
  });
}

test("a successful password reset ends with a clear sign-in action", async ({ page }) => {
  const token = "private-success-reset-token";
  const consoleOutput: string[] = [];
  page.on("console", message => consoleOutput.push(message.text()));
  await page.route("**/api/auth/reset-password", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ status: true }),
  }));
  await page.goto(`/reset-password?token=${token}`);
  await expect(page).toHaveURL(/\/reset-password$/);
  await page.getByRole("textbox", { name: "New password" }).fill("Strong-password-123");
  await page.getByRole("button", { name: "Update password" }).click();
  await expect(page.getByRole("heading", { name: "Password updated" })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("sign in with your new password");
  await expect(page.getByRole("textbox", { name: "New password" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/");
  await expect(page.locator("body")).not.toContainText(token);
  expect(consoleOutput.join("\n")).not.toContain(token);
});

for (const scenario of [
  { name: "successful", url: "/verify-email?status=success", heading: "Email verified", message: /email address is verified/i },
  { name: "expired", url: "/verify-email?status=success&error=TOKEN_EXPIRED", heading: "Verification link expired", message: /expired/i },
  { name: "invalid", url: "/verify-email?status=success&error=INVALID_TOKEN&token=private-verification-token", heading: "Verification link unavailable", message: /invalid|cannot be used/i },
]) {
  test(`email verification presents a safe ${scenario.name} callback result`, async ({ page }) => {
    const consoleOutput: string[] = [];
    page.on("console", message => consoleOutput.push(message.text()));
    await page.goto(scenario.url);
    await expect(page).toHaveURL(/\/verify-email$/);
    await expect(page.getByRole("heading", { name: scenario.heading })).toBeVisible();
    await expect(page.getByRole(scenario.name === "successful" ? "status" : "alert")).toContainText(scenario.message);
    await expect(page.getByLabel("Email address")).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/");
    await expect(page.locator("body")).not.toContainText(/TOKEN_EXPIRED|INVALID_TOKEN|private-verification-token/);
    expect(consoleOutput.join("\n")).not.toMatch(/TOKEN_EXPIRED|INVALID_TOKEN|private-verification-token/);
  });
}

for (const scenario of [
  { name: "eligible account", status: 200, body: { status: true } },
  { name: "unknown account", status: 404, body: { message: "User not found" } },
  { name: "already verified account", status: 400, body: { message: "Email already verified" } },
  { name: "mail delivery failure", status: 500, body: { message: "SMTP delivery failed for existing user" } },
  { name: "network-like mail delivery failure", status: 500, body: { message: "ECONNREFUSED while sending verification email" } },
]) {
  test(`verification resend remains privacy-safe for an ${scenario.name}`, async ({ page }) => {
    let callbackUrl = "";
    await page.route("**/api/auth/send-verification-email", route => {
      if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
      const requestBody = route.request().postDataJSON() as { callbackURL?: string };
      callbackUrl = requestBody.callbackURL ?? "";
      return route.fulfill({
        status: scenario.status,
        headers: cors,
        contentType: "application/json",
        body: JSON.stringify(scenario.body),
      });
    });
    await page.goto("/verify-email");
    await page.getByLabel("Email address").fill("person@vrub.example");
    await page.getByRole("button", { name: "Resend verification link" }).click();
    await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();
    await expect(page.getByRole("status")).toContainText(/if the account is awaiting verification/i);
    await expect(page.locator("body")).not.toContainText(/user not found|already verified|smtp|delivery failed/i);
    await expect(page.getByRole("link", { name: "Back to sign in" })).toHaveAttribute("href", "/");
    const callback = new URL(callbackUrl);
    expect(callback.pathname).toBe("/verify-email");
    expect(callback.searchParams.get("status")).toBe("success");
  });
}

test("verification resend exposes progress and prevents duplicate requests", async ({ page }) => {
  let requests = 0;
  let releaseRequest: (() => void) | undefined;
  const pendingResponse = new Promise<void>(resolve => { releaseRequest = resolve; });
  await page.route("**/api/auth/send-verification-email", async route => {
    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    requests += 1;
    await pendingResponse;
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ status: true }) });
  });
  await page.goto("/verify-email");
  await page.getByLabel("Email address").fill("person@vrub.example");
  await page.getByRole("button", { name: "Resend verification link" }).click();
  await expect(page.getByRole("button", { name: "Sending verification link…" })).toBeDisabled();
  await page.getByLabel("Email address").press("Enter");
  expect(requests).toBe(1);
  releaseRequest?.();
  await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();
});

test("verification resend network failure remains actionable and preserves sign-in recovery", async ({ page }) => {
  await page.route("**/api/auth/send-verification-email", route => route.abort("failed"));
  await page.goto("/verify-email");
  await page.getByLabel("Email address").fill("person@vrub.example");
  await page.getByRole("button", { name: "Resend verification link" }).click();
  await expect(page.getByRole("alert")).toContainText("could not reach the authentication service");
  await expect(page.getByLabel("Email address")).toHaveValue("person@vrub.example");
  await expect(page.getByRole("link", { name: "Back to sign in" })).toHaveAttribute("href", "/");
});

test("open registration verification returns to the frontend success state", async ({ page }) => {
  let callbackUrl = "";
  await page.route("**/api/auth/sign-up/email", route => {
    const requestBody = route.request().postDataJSON() as { callbackURL?: string };
    callbackUrl = requestBody.callbackURL ?? "";
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ user: { id: "u-new" } }) });
  });
  await page.goto("/register");
  await page.getByLabel("Full name").fill("New User");
  await page.getByLabel("Email address").fill("new@vrub.example");
  await page.getByRole("textbox", { name: "Password" }).fill("Strong-password-123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect.poll(() => callbackUrl).not.toBe("");
  const callback = new URL(callbackUrl);
  expect(callback.pathname).toBe("/verify-email");
  expect(callback.searchParams.get("status")).toBe("success");
});

test("unverified sign-in verification returns to the frontend success state", async ({ page }) => {
  let callbackUrl = "";
  await page.route("**/api/auth/sign-in/email", route => {
    const requestBody = route.request().postDataJSON() as { callbackURL?: string };
    callbackUrl = requestBody.callbackURL ?? "";
    return route.fulfill({ status: 403, headers: cors, contentType: "application/json", body: JSON.stringify({ message: "Email not verified" }) });
  });
  await page.goto("/");
  await page.getByLabel("Email address").fill("unverified@vrub.example");
  await page.getByRole("textbox", { name: "Password" }).fill("Strong-password-123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toContainText("We could not sign you in");
  await expect(page.getByRole("alert")).not.toContainText(/email not verified|EMAIL_NOT_VERIFIED/i);
  const callback = new URL(callbackUrl);
  expect(callback.pathname).toBe("/verify-email");
  expect(callback.searchParams.get("status")).toBe("success");
});

test("MFA setup confirms the current password without exposing backend errors", async ({ page }) => {
  await page.route("**/api/auth/two-factor/enable", route => route.fulfill({
    status: 400,
    headers: cors,
    contentType: "application/json",
    body: JSON.stringify({ message: "Invalid password", code: "INVALID_PASSWORD" }),
  }));
  await page.goto("/totp-setup");
  await expect(page.getByLabel("Confirm your password")).toHaveAttribute("autocomplete", "current-password");
  await page.getByLabel("Confirm your password").fill("wrong-password");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("alert")).toContainText("password confirmation did not match");
  await expect(page.getByRole("heading", { name: "Secure your account" })).toBeVisible();
});

test("MFA setup provides an accessible QR fallback and safe backup-code layout", async ({ page }) => {
  const consoleOutput: string[] = [];
  page.on("console", message => consoleOutput.push(message.text()));
  await openAuthenticatorSetup(page);
  await expect(page.getByText("Manual setup key")).toBeVisible();
  await expect(page.getByTestId("totp-manual-key")).toHaveText("JBSWY3DPEHPK3PXP");
  await expect(page.getByRole("list", { name: "Backup codes" }).getByRole("listitem")).toHaveCount(3);
  await expect(page.locator("body")).not.toContainText("otpauth://");
  expect(consoleOutput.join("\n")).not.toContain(totpUri);
  for (const code of backupCodes) expect(consoleOutput.join("\n")).not.toContain(code);
});

test("MFA enrollment announces the new setup stage and moves keyboard focus", async ({ page }) => {
  await openAuthenticatorSetup(page);
  const setupHeading = page.getByRole("heading", { name: "Authenticator app ready" });
  await expect(page.getByRole("region", { name: "Authenticator app ready" })).toBeVisible();
  await expect(setupHeading).toBeFocused();
});

test("MFA setup rejects non-six-digit codes before contacting Auth", async ({ page }) => {
  let verifyRequests = 0;
  await page.route("**/api/auth/two-factor/verify-totp", route => {
    verifyRequests += 1;
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ status: true }) });
  });
  await openAuthenticatorSetup(page);
  const codeField = page.getByLabel("Authenticator code");
  await codeField.fill("12ab34");
  await expect(codeField).toHaveValue("1234");
  await page.getByRole("button", { name: "Confirm and continue" }).click();
  await expect(page.getByRole("alert")).toContainText("six-digit code");
  expect(verifyRequests).toBe(0);
});

test("an invalid setup code remains retryable and a valid code continues to ERP resolution", async ({ page }) => {
  let attempts = 0;
  await page.route("**/api/auth/two-factor/verify-totp", route => {
    attempts += 1;
    return route.fulfill({
      status: attempts === 1 ? 400 : 200,
      headers: cors,
      contentType: "application/json",
      body: JSON.stringify(attempts === 1 ? { message: "Invalid code", code: "INVALID_CODE" } : { status: true }),
    });
  });
  await openAuthenticatorSetup(page);
  const codeField = page.getByLabel("Authenticator code");
  await codeField.fill("123456");
  await page.getByRole("button", { name: "Confirm and continue" }).click();
  await expect(page.getByRole("alert")).toContainText("incorrect or expired");
  await expect(codeField).toHaveAttribute("aria-invalid", "true");
  await codeField.fill("654321");
  await page.getByRole("button", { name: "Confirm and continue" }).click();
  await expect(page).toHaveURL(/\/app/);
});

test("the MFA sign-in challenge is paste-friendly, retryable, and blocks duplicate verification", async ({ page }) => {
  let requests = 0;
  let releaseRequest: (() => void) | undefined;
  const pendingResponse = new Promise<void>(resolve => { releaseRequest = resolve; });
  await page.route("**/api/auth/two-factor/verify-totp", async route => {
    requests += 1;
    if (requests === 1) {
      return route.fulfill({ status: 400, headers: cors, contentType: "application/json", body: JSON.stringify({ message: "Invalid code", code: "INVALID_CODE" }) });
    }
    await pendingResponse;
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ status: true }) });
  });
  await page.goto("/two-factor");
  const codeField = page.getByLabel("Authenticator code");
  await expect(codeField).toHaveAttribute("inputmode", "numeric");
  await expect(codeField).toHaveAttribute("autocomplete", "one-time-code");
  await codeField.fill("12 34a56");
  await expect(codeField).toHaveValue("123456");
  await page.getByRole("button", { name: "Verify code" }).click();
  await expect(page.getByRole("alert")).toContainText("incorrect or expired");
  await codeField.fill("654321");
  await page.getByRole("button", { name: "Verify code" }).click();
  await expect(page.getByRole("button", { name: "Verifying code…" })).toBeDisabled();
  await codeField.press("Enter");
  expect(requests).toBe(2);
  releaseRequest?.();
  await expect(page).toHaveURL(/\/app/);
});

test("MFA sign-in reaches the ERP shell only after the final me gate grants access", async ({ page }) => {
  await mockTwoFactorSignIn(page);
  await page.route("**/api/v1/me", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(grantedProfile) }));
  await page.route("**/api/v1/settings", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: "[]" }));
  await page.route("**/api/v1/org/units**", route => route.fulfill({
    status: 200,
    headers: cors,
    contentType: "application/json",
    body: JSON.stringify({ items: defaultUnits, total: 1, limit: 100, offset: 0 }),
  }));
  await page.route("**/api/v1/approvals/count", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ pending: 0, overdue: 0 }) }));
  await page.route("**/api/v1/notifications/count", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ unread: 0 }) }));
  await submitTwoFactorSignIn(page);
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
});

test("MFA verification never bypasses a final me denial", async ({ page }) => {
  await mockTwoFactorSignIn(page);
  await page.route("**/api/v1/me", route => route.fulfill({
    status: 200,
    headers: cors,
    contentType: "application/json",
    body: JSON.stringify({ ...grantedProfile, mfa_required: true, two_factor_enabled: false, access_granted: false }),
  }));
  await submitTwoFactorSignIn(page);
  await expect(page).toHaveURL(/\/totp-setup$/);
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toHaveCount(0);
});

test("a granted user reaches the permission-aware workspace", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile });
  const dashboardHeading = page.getByRole("heading", { name: "Dashboard" });
  await expect(dashboardHeading).toBeVisible();
  await expect(dashboardHeading).toBeFocused();
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
  await expect(page.getByText("Akshita K.").first()).toBeVisible();
  await expect(page.getByText("STORES").first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Inventory" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Finance and GST" })).toHaveCount(0);
});

test("dashboard navigation and route require the backend home module", async ({ page }) => {
  await mockSignInAndMe(page, {
    status: 200,
    body: { ...grantedProfile, modules: ["inventory"] },
  });
  await expect(page.getByRole("link", { name: "Dashboard" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Access denied" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Inventory" })).toBeVisible();
});

test("readable master-data destinations are present in the approved navigation group", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile });
  await expect(page.getByText("Master data", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Organization" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Parties" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Engineering" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Reference data" })).toBeVisible();
});

test("workspace loading blocks business navigation while me is unresolved", async ({ page }) => {
  let releaseMe: (() => void) | undefined;
  const pendingMe = new Promise<void>(resolve => { releaseMe = resolve; });
  await page.route("**/api/auth/sign-in/email", route => route.fulfill({
    status: 200,
    headers: cors,
    contentType: "application/json",
    body: JSON.stringify({ user: { id: "u1" } }),
  }));
  await page.route("**/api/v1/me", async route => {
    await pendingMe;
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(grantedProfile) });
  });
  await page.goto("/");
  await page.getByLabel("Email address").fill("a@vrub.example");
  await page.getByRole("textbox", { name: "Password" }).fill("Strong-password-123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Checking your workspace access…")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toHaveCount(0);
  releaseMe?.();
});

test("an inactive ERP profile has a distinct blocked state", async ({ page }) => {
  await mockSignInAndMe(page, { status: 403, body: { detail: "Your ERP account is deactivated" } });
  await expect(page.getByRole("heading", { name: "ERP account unavailable" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Your ERP account is deactivated");
  await expect(page.getByRole("button", { name: "Check access again" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toHaveCount(0);
});

test("an identity without an ERP profile sees a provisioning state", async ({ page }) => {
  await mockSignInAndMe(page, { status: 403, body: { detail: "No ERP profile for this account; ask an administrator" } });
  await expect(page.getByRole("heading", { name: "ERP access not provisioned" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("No ERP profile for this account");
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toHaveCount(0);
});

test("dashboard hides approval work when the approvals module is not granted", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile });
  await expect(page.getByText("Pending approvals")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Open approvals" })).toHaveCount(0);
  await expect(page.getByText("Unread notifications", { exact: true })).toBeVisible();
});

test("administration navigation requires both its module and permission", async ({ page }) => {
  await mockSignInAndMe(page, {
    status: 200,
    body: { ...grantedProfile, modules: [...grantedProfile.modules, "admin"], permissions: grantedProfile.permissions },
  });
  await expect(page.getByRole("link", { name: "Administration" })).toHaveCount(0);

  await page.unrouteAll({ behavior: "wait" });
  await mockSignInAndMe(page, {
    status: 200,
    body: { ...grantedProfile, modules: [...grantedProfile.modules, "admin"], permissions: [...grantedProfile.permissions, "admin"] },
  });
  await expect(page.getByRole("link", { name: "Administration" })).toBeVisible();
});

test("dashboard counts refresh when the page becomes visible again", async ({ page }) => {
  const counts: CountFixtures = { approval: { pending: 4, overdue: 1 }, notification: { unread: 3 } };
  await mockSignInAndMe(page, {
    status: 200,
    body: { ...grantedProfile, modules: [...grantedProfile.modules, "approvals"] },
  }, defaultUnits, counts);
  await expect(page.getByText("Pending approvals").locator("..").getByText("4", { exact: true })).toBeVisible();
  await expect(page.getByLabel("3 unread notifications")).toBeVisible();
  counts.approval = { pending: 7, overdue: 2 };
  counts.notification = { unread: 5 };
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.waitForTimeout(300);
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(page.getByText("Pending approvals").locator("..").getByText("7", { exact: true })).toBeVisible();
  await expect(page.getByLabel("5 unread notifications")).toBeVisible();
});

test("dashboard counts poll once per minute while the page is visible", async ({ page }) => {
  await page.clock.install();
  const counts: CountFixtures = { approval: { pending: 2, overdue: 0 }, notification: { unread: 1 } };
  await mockSignInAndMe(page, {
    status: 200,
    body: { ...grantedProfile, modules: [...grantedProfile.modules, "approvals"] },
  }, defaultUnits, counts);
  await expect(page.getByLabel("1 unread notifications")).toBeVisible();
  counts.approval = { pending: 6, overdue: 1 };
  counts.notification = { unread: 4 };
  await page.clock.fastForward(60_000);
  await expect(page.getByText("Pending approvals").locator("..").getByText("6", { exact: true })).toBeVisible();
  await expect(page.getByLabel("4 unread notifications")).toBeVisible();
});

test("dashboard uses only verified count sources and omits unsupported reference metrics", async ({ page }) => {
  await mockSignInAndMe(page, {
    status: 200,
    body: { ...grantedProfile, modules: [...grantedProfile.modules, "approvals"] },
  });
  await expect(page.getByText("Pending approvals")).toBeVisible();
  await expect(page.getByText("Unread notifications", { exact: true })).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/total products|total orders|total revenue|best selling|barcode scanner/i);
});

test("multiple authorized plants require an explicit active-plant selection", async ({ page }) => {
  const secondUnit = "22222222-2222-2222-2222-222222222222";
  await mockSignInAndMe(page, {
    status: 200,
    body: { ...grantedProfile, unit_ids: [grantedProfile.unit_ids[0], secondUnit] },
  }, [
    { unit_id: grantedProfile.unit_ids[0], code: "VR-01", name: "Plant Alpha", gstin: null, state_code: null, active: true },
    { unit_id: secondUnit, code: "VR-02", name: "Plant Beta", gstin: null, state_code: null, active: true },
  ]);
  const selector = page.getByRole("combobox", { name: "Active plant" });
  await expect(selector).toHaveValue("");
  await expect(page.getByRole("heading", { name: "Select an active plant" })).toBeVisible();
  await expect(page.getByText("Pending approvals")).toHaveCount(0);
  await selector.selectOption(secondUnit);
  await expect(selector).toHaveValue(secondUnit);
  await expect(page.getByRole("heading", { name: "Select an active plant" })).toHaveCount(0);
  await expect(page.getByText("Unread notifications", { exact: true })).toBeVisible();
});

test("mobile navigation provides the active-plant selector inside the drawer", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const secondUnit = "22222222-2222-2222-2222-222222222222";
  await mockSignInAndMe(page, {
    status: 200,
    body: { ...grantedProfile, unit_ids: [grantedProfile.unit_ids[0], secondUnit] },
  }, [
    ...defaultUnits,
    { unit_id: secondUnit, code: "VR-02", name: "Plant Beta", gstin: null, state_code: null, active: true },
  ]);
  await expect(page.getByRole("heading", { name: "Select an active plant" })).toBeVisible();
  await page.getByRole("button", { name: "Open navigation" }).click();
  const drawer = page.getByRole("dialog", { name: "Application navigation" });
  const selector = drawer.getByRole("combobox", { name: "Active plant" });
  await expect(selector).toBeVisible();
  await selector.selectOption(secondUnit);
  await expect(selector).toHaveValue(secondUnit);
});

test("plant bootstrap follows every units page instead of truncating at the first page", async ({ page }) => {
  const units = Array.from({ length: 205 }, (_, index) => ({
    unit_id: `${String(index + 1).padStart(8, "0")}-1111-1111-1111-111111111111`,
    code: `VR-${String(index + 1).padStart(3, "0")}`,
    name: `Plant ${index + 1}`,
    gstin: null,
    state_code: null,
    active: true,
  }));
  await mockSignInAndMe(page, {
    status: 200,
    body: { ...grantedProfile, all_plants: true, unit_ids: [] },
  }, units);
  const selector = page.getByRole("combobox", { name: "Active plant" });
  await expect(selector.locator("option")).toHaveCount(206);
  await expect(selector.locator("option").last()).toHaveText("VR-205 · Plant 205");
});

test("plant selector excludes active units outside the me scope", async ({ page }) => {
  const unauthorizedUnit = "33333333-3333-3333-3333-333333333333";
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [
    ...defaultUnits,
    { unit_id: unauthorizedUnit, code: "VR-99", name: "Unauthorized Plant", gstin: null, state_code: null, active: true },
  ]);
  await expect(page.getByText("Plant Alpha")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Unauthorized Plant");
});

test("single-plant access shows the authorized plant name", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile });
  await expect(page.getByText("Plant Alpha")).toBeVisible();
});

test("zero-plant access stops before business navigation", async ({ page }) => {
  await mockSignInAndMe(page, {
    status: 200,
    body: grantedProfile,
  }, []);
  await expect(page.getByRole("heading", { name: "No plant access assigned" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Check access again" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toHaveCount(0);
});

test("all-plant access starts in the explicit all-plants scope", async ({ page }) => {
  await mockSignInAndMe(page, {
    status: 200,
    body: { ...grantedProfile, all_plants: true, unit_ids: [] },
  }, [
    ...defaultUnits,
    { unit_id: "22222222-2222-2222-2222-222222222222", code: "VR-02", name: "Plant Beta", gstin: null, state_code: null, active: true },
  ]);
  await expect(page.getByRole("combobox", { name: "Active plant" })).toHaveValue("all");
});

test("authorized navigation identifies unfinished modules without inventing a workspace", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, modules: [...grantedProfile.modules, "manufacturing"] } });
  await page.getByRole("link", { name: "Manufacturing" }).click();
  await expect(page.getByRole("heading", { name: "Manufacturing" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Batches", exact: true })).toBeVisible();
  await page.goto("/app/manufacturing/future-workspace");
  await expect(page.getByRole("heading", { name: "Manufacturing" })).toBeVisible();
  await expect(page.getByText("scheduled in a later implementation wave")).toBeVisible();
});

test("unauthorized known deep links show forbidden rather than not found", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile });
  await page.goto("/app/finance");
  await expect(page.getByRole("heading", { name: "Access denied" })).toBeVisible();
  await expect(page.getByText("outside your current module access")).toBeVisible();
  await expect(page.getByRole("link", { name: "Finance and GST" })).toHaveCount(0);
});

test("unknown private routes remain a distinct not-found state", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile });
  await page.goto("/app/not-a-real-page");
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  await expect(page.getByText("could not be found")).toBeVisible();
});

for (const route of [
  { path: "/app/finance", heading: "Access denied", detail: "outside your current module access" },
  { path: "/app/not-a-real-page", heading: "Page not found", detail: "could not be found" },
]) {
  test(`multi-plant deep link preserves ${route.heading} before plant selection`, async ({ page }) => {
    const secondUnit = "22222222-2222-2222-2222-222222222222";
    await mockSignInAndMe(page, {
      status: 200,
      body: { ...grantedProfile, unit_ids: [grantedProfile.unit_ids[0], secondUnit] },
    }, [
      ...defaultUnits,
      { unit_id: secondUnit, code: "VR-02", name: "Plant Beta", gstin: null, state_code: null, active: true },
    ]);
    await page.goto(route.path);
    await expect(page.getByRole("heading", { name: route.heading })).toBeVisible();
    await expect(page.getByText(route.detail)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Select an active plant" })).toHaveCount(0);
  });
}

test("a no-home role recovers from an authorized placeholder to an authorized route", async ({ page }) => {
  await mockSignInAndMe(page, {
    status: 200,
    body: { ...grantedProfile, role: "OPERATOR", roles: ["OPERATOR"], modules: ["manufacturing", "routes", "kaizen"], permissions: ["mfg.entry", "route.move", "kaizen.submit", "mnt.breakdown", "leave.apply"] },
  });
  await page.goto("/app/manufacturing/future-workspace");
  await expect(page.getByRole("heading", { name: "Manufacturing" })).toBeVisible();
  const recovery = page.getByRole("link", { name: "Open an authorized workspace" });
  await expect(recovery).toBeVisible();
  await expect(recovery).not.toHaveAttribute("href", "/app");
});

test("an unknown public URL has a distinct 404 with back and sign-in recovery", async ({ page }) => {
  await page.goto("/this-page-does-not-exist");
  const heading = page.getByRole("heading", { name: "Page not found" });
  await expect(heading).toBeVisible();
  await expect(heading).toBeFocused();
  await expect(page.getByText("could not be found", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Go back" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Go to sign in" })).toHaveAttribute("href", "/");
});

test("a bootstrap conflict preserves backend detail and recovers by refreshing", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, defaultUnits, {}, {
    settings: [
      { status: 409, body: { detail: "Workspace settings changed while access was loading" } },
      { status: 200, body: [] },
    ],
  });
  const heading = page.getByRole("heading", { name: "Workspace changed" });
  await expect(heading).toBeVisible();
  await expect(heading).toBeFocused();
  await expect(page.getByRole("alert")).toContainText("Workspace settings changed while access was loading");
  await page.getByRole("button", { name: "Refresh workspace" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
});

test("a server failure exposes and copies its request ID", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, defaultUnits, {}, {
    settings: [{ status: 503, body: { detail: "Workspace configuration is temporarily unavailable" }, requestId: "req-fnd08-503" }],
  });
  const heading = page.getByRole("heading", { name: "Service temporarily unavailable" });
  await expect(heading).toBeVisible();
  await expect(heading).toBeFocused();
  await expect(page.getByRole("alert")).toContainText("Workspace configuration is temporarily unavailable");
  await expect(page.getByText("req-fnd08-503", { exact: true })).toBeVisible();
  const copy = page.getByRole("button", { name: "Copy request ID" });
  await copy.click();
  await expect(copy).toHaveText("Copied");
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe("req-fnd08-503");
});

test("server-error recovery actions stack on mobile without horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, defaultUnits, {}, {
    settings: [{ status: 500, body: { detail: "The workspace could not be prepared" }, requestId: "req-mobile-fnd08-very-long-request-identifier" }],
  });
  await expect(page.getByRole("heading", { name: "Service temporarily unavailable" })).toBeVisible();
  const actions = page.locator(".system-state__actions");
  const retry = actions.getByRole("button", { name: "Try again" });
  const signOut = actions.getByRole("button", { name: "Sign out" });
  await expect(retry).toBeVisible();
  await expect(signOut).toBeVisible();
  const retryBox = await retry.boundingBox();
  const signOutBox = await signOut.boundingBox();
  expect(Math.abs((retryBox?.width ?? 0) - (signOutBox?.width ?? 0))).toBeLessThan(2);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
});
