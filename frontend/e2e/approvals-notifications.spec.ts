import { expect, test } from '@playwright/test';
import { cors, InvitationResponse, mockInvitationAttempt, submitInvitationSignIn, submitPasswordRecovery, totpUri, backupCodes, openAuthenticatorSetup, mockTwoFactorSignIn, submitTwoFactorSignIn, MeFixture, UnitFixture, CountFixtures, BootstrapResponse, BootstrapFixtures, defaultUnits, mockSignInAndMe, grantedProfile, approvalOne, approvalTwo, mockApprovals, approvalDetail, mockApprovalDetail, notificationOne, notificationTwo, mockNotifications, publicApprovalToken, publicApproval, mockPublicApproval, plantAlpha, plantBeta, departmentProduction, departmentFinishing, mockDepartments, storeRaw, storeFinished, mockStores, paymentTermNet30, taxCodeGst18, categoryRubber, categoryNatural, uomKilogram, mockReferenceData, vendorOrbit, mockVendors, mockCustomerDetail, customerAcme, mockCustomers, mockVendorDetail, itemRubber, mockItems, itemFinished, itemPolicy, itemSpec, itemBom, itemChange, itemPart, mockItemDetail, mockBoms, bomDraft, bomPrevious, mockBomDetail, routeMouldV2, routeMouldV1, routeMetal, mockRoutes, indentProfile, indentAlpha, indentBeta, mockIndentRegister, indentDetailFixture, indentPutCors, mockOwnedIndent, rfqProfile, rfqAlpha, rfqBeta, rfqLineFixture, rfqInvitations, rfqDetailFixture, mockRfqRegister, poAlpha, poBeta, poLine, poDetail, mockPoRegister, mockPoEditCatalog, inventoryProfile, grnAlpha, grnBeta, grnDetail, mockGrnRegister, mockGrnCreationSources, stockAlpha, stockBeta, stockTrace, mockStockSources, issueProfile, issuePlan, postedIssue, issueSummary, mockIssueHistory } from './support/fixtures';

test("approval inbox loads the mine pending queue and explains overdue state", async ({ page }) => {
  let requestedUrl = "";
  await mockApprovals(page, url => {
    requestedUrl = url.toString();
    return { items: [approvalOne, approvalTwo], total: 2 };
  });
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, modules: [...grantedProfile.modules, "approvals"] } });
  await page.locator(".app-nav").getByRole("link", { name: "Approvals", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Approval inbox" })).toBeVisible();
  await expect.poll(() => requestedUrl).toContain("scope=mine");
  expect(requestedUrl).toContain("state=PENDING");
  expect(requestedUrl).toContain("limit=20");
  expect(requestedUrl).toContain("offset=0");
  await expect(page.getByRole("button", { name: "All approvals" })).toHaveCount(0);
  const results = page.getByRole("table", { name: "Approval results" });
  await expect(results.getByText("PO-2026-0042 · Steel fasteners")).toBeVisible();
  await expect(results.getByText("Overdue", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open approval PO-2026-0042 · Steel fasteners" })).toHaveAttribute("href", `/app/approvals/${approvalOne.approval_id}`);
});

test("approval all-scope is visible only with approval.view_all and is URL backed", async ({ page }) => {
  const requests: string[] = [];
  await mockApprovals(page, url => { requests.push(url.toString()); return { items: [approvalOne], total: 1 }; });
  await mockSignInAndMe(page, {
    status: 200,
    body: { ...grantedProfile, modules: [...grantedProfile.modules, "approvals"], permissions: [...grantedProfile.permissions, "approval.view_all"] },
  });
  await page.locator(".app-nav").getByRole("link", { name: "Approvals", exact: true }).click();
  await page.getByRole("button", { name: "All approvals" }).click();
  await expect(page).toHaveURL(/scope=all/);
  await expect.poll(() => requests.some(url => url.includes("scope=all"))).toBe(true);
});

test("an unauthorized all-scope URL is normalized to mine before loading", async ({ page }) => {
  const requests: string[] = [];
  await mockApprovals(page, url => { requests.push(url.toString()); return { items: [approvalOne], total: 1 }; });
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, modules: [...grantedProfile.modules, "approvals"] } });
  requests.length = 0;
  await page.goto("/app/approvals?scope=all&state=PENDING");
  await expect(page.getByRole("heading", { name: "Approval inbox" })).toBeVisible();
  await expect(page).toHaveURL(/scope=mine/);
  await expect.poll(() => requests.length).toBeGreaterThan(0);
  expect(requests.every(url => url.includes("scope=mine"))).toBe(true);
});

test("approval state filters are URL backed and use the exact backend enum", async ({ page }) => {
  const requests: string[] = [];
  await mockApprovals(page, url => { requests.push(url.toString()); return { items: [], total: 0 }; });
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, modules: [...grantedProfile.modules, "approvals"] } });
  await page.locator(".app-nav").getByRole("link", { name: "Approvals", exact: true }).click();
  await page.getByRole("button", { name: "Approved" }).click();
  await expect(page).toHaveURL(/state=APPROVED/);
  await expect.poll(() => requests.some(url => url.includes("state=APPROVED"))).toBe(true);
});

test("approval pagination uses backend offset and reports the visible record range", async ({ page }) => {
  const requests: string[] = [];
  await mockApprovals(page, url => {
    requests.push(url.toString());
    const offset = Number(url.searchParams.get("offset") ?? 0);
    return {
      items: offset === 20
        ? Array.from({ length: 5 }, (_, index) => ({ ...approvalTwo, approval_id: `00000000-0000-0000-0000-${String(index + 21).padStart(12, "0")}`, title: `Approval record ${index + 21}` }))
        : Array.from({ length: 20 }, (_, index) => ({ ...approvalOne, approval_id: `00000000-0000-0000-0000-${String(index + 1).padStart(12, "0")}`, title: `Approval record ${index + 1}` })),
      total: 25,
    };
  });
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, modules: [...grantedProfile.modules, "approvals"] } });
  await page.locator(".app-nav").getByRole("link", { name: "Approvals", exact: true }).click();
  await expect(page.getByText("Showing 1–20 of 25 approvals")).toBeVisible();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page).toHaveURL(/offset=20/);
  await expect(page.getByRole("table", { name: "Approval results" }).getByText("Approval record 21")).toBeVisible();
  await expect(page.getByText("Showing 21–25 of 25 approvals")).toBeVisible();
  expect(requests.some(url => url.includes("offset=20"))).toBe(true);
});

test("approval inbox has a clear filtered-empty state", async ({ page }) => {
  await mockApprovals(page, () => ({ items: [], total: 0 }));
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, modules: [...grantedProfile.modules, "approvals"] } });
  await page.locator(".app-nav").getByRole("link", { name: "Approvals", exact: true }).click();
  await page.getByRole("button", { name: "Rejected" }).click();
  await expect(page.getByRole("heading", { name: "No rejected approvals" })).toBeVisible();
  await expect(page.getByText("Change the state filter or refresh the inbox.")).toBeVisible();
});

test("approval inbox becomes readable priority cards on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApprovals(page);
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, modules: [...grantedProfile.modules, "approvals"] } });
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.locator(".app-nav").getByRole("link", { name: "Approvals", exact: true }).click();
  await expect(page.getByRole("list", { name: "Approval results" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Approval results" })).toBeHidden();
  await expect(page.getByRole("list", { name: "Approval results" }).getByText("Overdue", { exact: true })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
});

test("approval detail renders the backend route and decision history", async ({ page }) => {
  await mockApprovalDetail(page);
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, role: "PLANT_HEAD", roles: ["PLANT_HEAD"], modules: [...grantedProfile.modules, "approvals"] } });
  await page.goto(`/app/approvals/${approvalDetail.approval_id}`);
  await expect(page.getByRole("heading", { name: approvalDetail.title })).toBeVisible();
  await expect(page.getByText("Purchase order · Plant head then finance")).toBeVisible();
  const route = page.getByRole("list", { name: "Approval route" });
  await expect(route.getByText("Department approval")).toBeVisible();
  await expect(route.getByText("Budget confirmed")).toBeVisible();
  await expect(route.getByText("Current", { exact: true })).toBeVisible();
  await expect(page.getByText("₹21863.00")).toBeVisible();
});

test("a submitter cannot use decision actions on their own approval", async ({ page }) => {
  await mockApprovalDetail(page, request => ({ status: 200, body: { ...approvalDetail, submitted_by_name: request.method() === "GET" ? "Akshita K." : approvalDetail.submitted_by_name } }));
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, role: "PLANT_HEAD", roles: ["PLANT_HEAD"], modules: [...grantedProfile.modules, "approvals"] } });
  await page.goto(`/app/approvals/${approvalDetail.approval_id}`);
  await expect(page.getByText("You cannot decide your own submission.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve request" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Reject request" })).toHaveCount(0);
});

test("rejection requires a note before the decision request is sent", async ({ page }) => {
  let posts = 0;
  await mockApprovalDetail(page, request => {
    if (request.method() === "POST") posts += 1;
    return { status: 200, body: approvalDetail };
  });
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, role: "PLANT_HEAD", roles: ["PLANT_HEAD"], modules: [...grantedProfile.modules, "approvals"] } });
  await page.goto(`/app/approvals/${approvalDetail.approval_id}`);
  await page.getByRole("button", { name: "Reject request" }).click();
  await page.getByRole("button", { name: "Confirm rejection" }).click();
  await expect(page.getByText("Explain why this request is being rejected.")).toBeVisible();
  expect(posts).toBe(0);
  await page.getByLabel("Decision note").fill("Price exceeds the approved quotation");
  await page.getByRole("button", { name: "Confirm rejection" }).click();
  await expect.poll(() => posts).toBe(1);
});

test("approval decisions send the current step once with an idempotency key", async ({ page }) => {
  let posts = 0;
  let postedBody: Record<string, unknown> | null = null;
  let idempotencyKey = "";
  let releaseResponse = () => {};
  const responseGate = new Promise<void>(resolve => { releaseResponse = resolve; });
  const approved = { ...approvalDetail, status: "APPROVED", overdue: false, steps: approvalDetail.steps.map(step => step.seq === 2 ? { ...step, state: "APPROVED", decided_by: "Akshita K.", decision: "APPROVE", decided_at: "2026-11-04T08:00:00Z" } : step) };
  await mockApprovalDetail(page, async request => {
    if (request.method() === "GET") return { body: approvalDetail };
    posts += 1;
    postedBody = request.postDataJSON();
    idempotencyKey = request.headers()["idempotency-key"] ?? "";
    await responseGate;
    return { body: approved };
  });
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, role: "PLANT_HEAD", roles: ["PLANT_HEAD"], modules: [...grantedProfile.modules, "approvals"] } });
  await page.goto(`/app/approvals/${approvalDetail.approval_id}`);
  await page.getByRole("button", { name: "Approve request" }).click();
  const confirm = page.getByRole("button", { name: "Confirm approval" });
  const firstClick = confirm.click();
  await expect.poll(() => posts).toBe(1);
  const recording = page.getByRole("button", { name: "Recording…" });
  await expect(recording).toBeDisabled();
  await recording.click({ force: true });
  releaseResponse();
  await firstClick;
  await expect(page.getByText("Approval recorded")).toBeVisible();
  expect(posts).toBe(1);
  expect(postedBody).toEqual({ decision: "APPROVE", note: null, step: 2 });
  expect(idempotencyKey.length).toBeGreaterThan(10);
});

test("a stale approval decision refreshes the detail and preserves the backend reason", async ({ page }) => {
  let getCount = 0;
  let posted = false;
  const alreadyApproved = { ...approvalDetail, status: "APPROVED", overdue: false, steps: approvalDetail.steps.map(step => step.seq === 2 ? { ...step, state: "APPROVED", decided_by: "Another approver", decision: "APPROVE", decided_at: "2026-11-04T08:01:00Z" } : step) };
  await mockApprovalDetail(page, request => {
    if (request.method() === "POST") {
      posted = true;
      return { status: 409, body: { detail: "Already decided" } };
    }
    getCount += 1;
    return { body: posted ? alreadyApproved : approvalDetail };
  });
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, role: "PLANT_HEAD", roles: ["PLANT_HEAD"], modules: [...grantedProfile.modules, "approvals"] } });
  await page.goto(`/app/approvals/${approvalDetail.approval_id}`);
  await page.getByRole("button", { name: "Approve request" }).click();
  await page.getByRole("button", { name: "Confirm approval" }).click();
  await expect(page.getByRole("heading", { name: "Approval changed" })).toBeVisible();
  await expect(page.getByText("Already decided")).toBeVisible();
  await expect.poll(() => getCount).toBeGreaterThan(1);
  await expect(page.getByText("Approved", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve request" })).toHaveCount(0);
});

test("approval detail keeps valid actions reachable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApprovalDetail(page);
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, role: "PLANT_HEAD", roles: ["PLANT_HEAD"], modules: [...grantedProfile.modules, "approvals"] } });
  await page.goto(`/app/approvals/${approvalDetail.approval_id}`);
  const actions = page.getByRole("region", { name: "Approval actions" });
  await expect(actions.getByRole("button", { name: "Approve request" })).toBeVisible();
  await expect(actions.getByRole("button", { name: "Reject request" })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
});

test("notifications use URL-backed unread and all filters with the exact backend query", async ({ page }) => {
  const requests: string[] = [];
  await mockNotifications(page, url => {
    requests.push(url.toString());
    return url.searchParams.get("unread") === "true" ? [notificationOne] : [notificationOne, notificationTwo];
  });
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, defaultUnits, { notification: { unread: 1 } });
  await page.locator(".app-nav").getByRole("link", { name: "Notifications", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Notifications" }).last()).toBeVisible();
  await expect.poll(() => requests.some(url => url.includes("unread=true") && url.includes("limit=30"))).toBe(true);
  await expect(page.getByRole("list", { name: "Notification results" }).getByText(notificationOne.subject)).toBeVisible();
  await page.getByRole("button", { name: "All notifications" }).click();
  await expect(page).toHaveURL(/filter=all/);
  await expect.poll(() => requests.some(url => url.includes("unread=false"))).toBe(true);
  await expect(page.getByText(notificationTwo.subject)).toBeVisible();
});

test("marking one notification read reconciles the item and global unread count", async ({ page }) => {
  let read = false;
  const counts: CountFixtures = { notification: { unread: 2 } };
  await mockNotifications(page, () => [{ ...notificationOne, read_at: read ? "2026-11-04T09:00:00Z" : null }, { ...notificationTwo, read_at: null }]);
  await page.route(`**/api/v1/notifications/${notificationOne.notification_id}/read`, route => {
    read = true;
    counts.notification = { unread: 1 };
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...notificationOne, read_at: "2026-11-04T09:00:00Z" }) });
  });
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, defaultUnits, counts);
  await page.locator(".app-nav").getByRole("link", { name: "Notifications", exact: true }).click();
  await page.getByRole("button", { name: `Mark ${notificationOne.subject} as read` }).click();
  await expect(page.getByLabel("1 unread notifications")).toBeVisible();
  await expect(page.getByRole("button", { name: `Mark ${notificationOne.subject} as read` })).toHaveCount(0);
});

test("mark all read empties the unread view and reconciles the bell", async ({ page }) => {
  let readAll = false;
  const counts: CountFixtures = { notification: { unread: 2 } };
  await mockNotifications(page, url => url.searchParams.get("unread") === "true" && readAll ? [] : [notificationOne, { ...notificationTwo, read_at: null }]);
  await page.route("**/api/v1/notifications/read-all", route => {
    readAll = true;
    counts.notification = { unread: 0 };
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ unread: 0 }) });
  });
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, defaultUnits, counts);
  await page.locator(".app-nav").getByRole("link", { name: "Notifications", exact: true }).click();
  await page.getByRole("button", { name: "Mark all as read" }).click();
  await expect(page.getByRole("heading", { name: "No unread notifications" })).toBeVisible();
  await expect(page.getByLabel("0 unread notifications")).toBeVisible();
});

test("known notification references navigate only after being marked read", async ({ page }) => {
  let marked = false;
  await mockNotifications(page, () => [notificationOne, notificationTwo]);
  await mockApprovalDetail(page);
  await page.route(`**/api/v1/notifications/${notificationOne.notification_id}/read`, route => {
    marked = true;
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...notificationOne, read_at: "2026-11-04T09:00:00Z" }) });
  });
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, role: "PLANT_HEAD", roles: ["PLANT_HEAD"], modules: [...grantedProfile.modules, "approvals"] } }, defaultUnits, { notification: { unread: 1 } });
  await page.locator(".app-nav").getByRole("link", { name: "Notifications", exact: true }).click();
  await page.getByRole("button", { name: `Open ${notificationOne.subject}` }).click();
  await expect.poll(() => marked).toBe(true);
  await expect(page).toHaveURL(new RegExp(`/app/approvals/${approvalDetail.approval_id}$`));
  await expect(page.getByRole("heading", { name: approvalDetail.title })).toBeVisible();
});

test("notification polling replaces by identifier instead of duplicating records", async ({ page }) => {
  await page.clock.install();
  let requests = 0;
  await mockNotifications(page, () => {
    requests += 1;
    return [notificationOne, notificationOne];
  });
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, defaultUnits, { notification: { unread: 1 } });
  await page.locator(".app-nav").getByRole("link", { name: "Notifications", exact: true }).click();
  await expect(page.getByRole("list", { name: "Notification results" }).getByRole("listitem")).toHaveCount(1);
  await page.clock.fastForward(60_000);
  await expect.poll(() => requests).toBeGreaterThan(1);
  await expect(page.getByRole("list", { name: "Notification results" }).getByRole("listitem")).toHaveCount(1);
});

test("notification list expands its backend limit without inventing offset pagination", async ({ page }) => {
  const limits: number[] = [];
  await mockNotifications(page, url => {
    const limit = Number(url.searchParams.get("limit"));
    limits.push(limit);
    return Array.from({ length: Math.min(limit, 45) }, (_, index) => ({ ...notificationOne, notification_id: `00000000-1111-2222-3333-${String(index + 1).padStart(12, "0")}`, subject: `Notification ${index + 1}` }));
  });
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, defaultUnits, { notification: { unread: 45 } });
  await page.locator(".app-nav").getByRole("link", { name: "Notifications", exact: true }).click();
  await page.getByRole("button", { name: "Show more notifications" }).click();
  await expect.poll(() => limits).toContain(60);
  await expect(page.getByRole("list", { name: "Notification results" }).getByRole("listitem")).toHaveCount(45);
});

test("notifications remain readable cards without mobile overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockNotifications(page);
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, defaultUnits, { notification: { unread: 1 } });
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("link", { name: "Notifications", exact: true }).click();
  const list = page.getByRole("list", { name: "Notification results" });
  await expect(list).toBeVisible();
  await expect(list.getByText(notificationOne.body)).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
});

test("mobile navigation moves focus into the drawer and closes with Escape", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockSignInAndMe(page, { status: 200, body: grantedProfile });
  const sidebar = page.locator(".app-sidebar");
  await expect(sidebar).toHaveAttribute("aria-hidden", "true");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(sidebar).toHaveAttribute("aria-modal", "true");
  await expect(sidebar).toHaveClass(/app-sidebar--open/);
  await expect(page.getByRole("link", { name: "Dashboard" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(sidebar).not.toHaveClass(/app-sidebar--open/);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
});

test("collapsed desktop rail keeps accessible navigation names", async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 800 });
  await mockSignInAndMe(page, { status: 200, body: grantedProfile });
  await expect(page.getByRole("link", { name: "Dashboard" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Inventory" })).toBeVisible();
  const signOut = page.getByRole("button", { name: "Sign out" });
  await expect(signOut).toBeVisible();
  const box = await signOut.boundingBox();
  expect(box?.width).toBeGreaterThanOrEqual(44);
  expect(box?.height).toBeGreaterThanOrEqual(44);
});

test("an unavailable notification count is never announced as zero", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, defaultUnits, { notification: "error" });
  await expect(page.getByRole("link", { name: "Notification count unavailable" })).toBeVisible();
  await expect(page.getByRole("status", { name: "Notification count status" })).toHaveText("Notification count unavailable");
  await expect(page.getByText("Count temporarily unavailable")).toBeVisible();
});

test("count refresh pauses while hidden and coalesces a focus plus visibility return", async ({ page }) => {
  await page.clock.install();
  let notificationRequests = 0;
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, defaultUnits, {
    notification: { unread: 3 },
    onNotificationRequest: () => { notificationRequests += 1; },
  });
  await expect.poll(() => notificationRequests).toBeGreaterThan(0);
  const baselineRequests = notificationRequests;
  await page.evaluate(() => Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" }));
  await page.clock.fastForward(60_000);
  expect(notificationRequests).toBe(baselineRequests);
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
  });
  await expect.poll(() => notificationRequests).toBe(baselineRequests + 1);
  await page.clock.fastForward(200);
  expect(notificationRequests).toBe(baselineRequests + 1);
});

for (const viewport of [
  { name: "360 mobile", width: 360, height: 800 },
  { name: "768 tablet", width: 768, height: 1024 },
  { name: "1280 desktop", width: 1280, height: 800 },
  { name: "1536 wide desktop", width: 1536, height: 960 },
  { name: "200 percent reflow equivalent", width: 640, height: 800 },
]) {
  test(`shell has no horizontal page overflow at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockSignInAndMe(page, { status: 200, body: grantedProfile });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(overflow).toBe(false);
  });
}

test("a role with an unmet MFA requirement is sent to authenticator setup", async ({ page }) => {
  await mockSignInAndMe(page, {
    status: 200,
    body: { ...grantedProfile, role: "MD", roles: ["MD"], mfa_required: true, access_granted: false },
  });
  await expect(page.getByRole("heading", { name: "Secure your account" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toHaveCount(0);
  await expect(page).toHaveURL(/\/totp-setup$/);
});

test("a user outside the V-Rub organization sees the API reason", async ({ page }) => {
  await mockSignInAndMe(page, {
    status: 403,
    body: { detail: "Not a member of the V-Rub organization" },
  });
  await expect(page.getByRole("heading", { name: "Workspace access denied" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Not a member of the V-Rub organization");
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toHaveCount(0);
});

test("a non-MFA access denial never renders the ERP shell", async ({ page }) => {
  await mockSignInAndMe(page, {
    status: 200,
    body: { ...grantedProfile, access_granted: false, mfa_required: false },
  });
  await expect(page.getByRole("heading", { name: "Workspace access denied" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("not active for workspace access");
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toHaveCount(0);
});

test("duplicate-like registration errors remain account-agnostic", async ({ page }) => {
  await page.route("**/api/auth/sign-up/email", route => route.fulfill({
    status: 409,
    contentType: "application/json",
    body: JSON.stringify({ message: "User already exists" }),
  }));
  await page.goto("/register");
  await page.getByLabel("Full name").fill("Existing User");
  await page.getByLabel("Email address").fill("existing@vrub.example");
  await page.getByRole("textbox", { name: "Password" }).fill("Strong-password-123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("status")).toContainText("If this email is eligible for registration");
  await expect(page.getByText(/already created an ID/i)).toHaveCount(0);
});

test("an expired ERP session signs out and returns to sign-in", async ({ page }) => {
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
  await mockSignInAndMe(page, { status: 401, body: { detail: "Session expired" } });
  await expect.poll(() => signedOut).toBe(true);
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("Your session expired. Sign in again to continue.");
});

test("Auth document starts with the task heading and small text meets AA contrast", async ({ page }) => {
  await page.goto("/");
  const headings = await page.locator("h1, h2").allTextContents();
  expect(headings[0]).toBe("Welcome back");
  await expect(page.getByText("Secure V-Rub workspace")).toHaveCount(0);
  const statementSize = await page.locator(".public-intro__statement").evaluate(element => parseFloat(getComputedStyle(element).fontSize));
  expect(statementSize).toBeGreaterThanOrEqual(36);

  const contrast = await page.evaluate(() => {
    const parse = (value: string) => value.match(/\d+/g)!.slice(0, 3).map(Number);
    const luminance = ([r, g, b]: number[]) => {
      const values = [r, g, b].map(channel => {
        const normalized = channel / 255;
        return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2];
    };
    const ratio = (foreground: string, background: string) => {
      const first = luminance(parse(foreground));
      const second = luminance(parse(background));
      return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
    };
    const input = document.querySelector<HTMLInputElement>("#email")!;
    const footer = document.querySelector<HTMLElement>(".public-footer")!;
    const inputStyle = getComputedStyle(input);
    const placeholderStyle = getComputedStyle(input, "::placeholder");
    const footerStyle = getComputedStyle(footer);
    return {
      input: ratio(placeholderStyle.color, inputStyle.backgroundColor),
      footer: ratio(footerStyle.color, getComputedStyle(document.querySelector(".public-shell")!).backgroundColor),
    };
  });
  expect(contrast.input).toBeGreaterThanOrEqual(4.5);
  expect(contrast.footer).toBeGreaterThanOrEqual(4.5);
});

test("sign out ends the Auth session before returning to sign-in", async ({ page }) => {
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
  await mockSignInAndMe(page, { status: 200, body: grantedProfile });
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  expect(signedOut).toBe(true);
});

test("public approval loads without login and GET remains view only", async ({ page }) => {
  const requests = await mockPublicApproval(page, { status: 200, body: publicApproval });
  let privateBootstrapRequested = false;
  await page.route("**/api/v1/me", route => {
    privateBootstrapRequested = true;
    return route.abort();
  });
  await page.goto(`/approve/${publicApprovalToken}`);

  await expect(page.getByRole("heading", { name: publicApproval.title })).toBeVisible();
  await expect(page.getByText("Purchase order", { exact: true })).toBeVisible();
  await expect(page.getByText("₹21863.00")).toBeVisible();
  await expect(page.getByText("Ravi Kumar")).toBeVisible();
  await expect(page.getByText("Plant head review")).toBeVisible();
  await expect(page.locator(".app-shell")).toHaveCount(0);
  expect(privateBootstrapRequested).toBe(false);
  expect(requests.length).toBeGreaterThan(0);
  expect(requests.every(request => request.method === "GET" && request.body === null)).toBe(true);
  await expect(page.locator("body")).not.toContainText(publicApprovalToken);
});

test("invalid, expired, and already-decided public links recover distinctly", async ({ page }) => {
  await mockPublicApproval(page, { status: 404, body: { detail: "This link is not valid" } });
  await page.goto(`/approve/${publicApprovalToken}`);
  await expect(page.getByRole("heading", { name: "Approval link not found" })).toBeVisible();
  await expect(page.getByText("This approval link is not valid.")).toBeVisible();

  await page.unrouteAll({ behavior: "wait" });
  await mockPublicApproval(page, { status: 410, body: { detail: "This link has expired" } });
  await page.goto(`/approve/${publicApprovalToken}`);
  await expect(page.getByRole("heading", { name: "Approval link expired" })).toBeVisible();
  await expect(page.getByText("Ask the requester for a new approval link.")).toBeVisible();

  await page.unrouteAll({ behavior: "wait" });
  await mockPublicApproval(page, { status: 410, body: { detail: "This step has already been decided" } });
  await page.goto(`/approve/${publicApprovalToken}`);
  await expect(page.getByRole("heading", { name: "Approval already decided" })).toBeVisible();
  await expect(page.getByText("No further action is available from this link.")).toBeVisible();
});

test("public approval requires an explicit confirmation and submits only once", async ({ page }) => {
  const requests = await mockPublicApproval(page, { status: 200, body: publicApproval });
  await page.goto(`/approve/${publicApprovalToken}`);
  await page.getByRole("button", { name: "Approve request" }).click();
  await expect(page.getByRole("heading", { name: "Confirm approval" })).toBeVisible();
  expect(requests.filter(request => request.method === "POST")).toHaveLength(0);

  await page.getByRole("button", { name: "Confirm approval" }).dblclick();
  await expect(page.getByRole("heading", { name: "Approval submitted" })).toBeVisible();
  const posts = requests.filter(request => request.method === "POST");
  expect(posts).toHaveLength(1);
  expect(JSON.parse(posts[0].body ?? "{}" )).toEqual({ decision: "APPROVE", note: null });
});

test("public rejection requires a note and sends the exact backend contract", async ({ page }) => {
  const requests = await mockPublicApproval(
    page,
    { status: 200, body: publicApproval },
    { status: 200, body: { status: "REJECTED" } },
  );
  await page.goto(`/approve/${publicApprovalToken}`);
  await page.getByRole("button", { name: "Reject request" }).click();
  await expect(page.getByRole("heading", { name: "Reject request" })).toBeVisible();
  await page.getByRole("button", { name: "Confirm rejection" }).click();
  await expect(page.getByRole("alert")).toContainText("Enter a reason for rejecting this request");
  expect(requests.filter(request => request.method === "POST")).toHaveLength(0);

  await page.getByLabel("Reason for rejection").fill("Quantity does not match the order");
  await page.getByRole("button", { name: "Confirm rejection" }).click();
  await expect(page.getByRole("heading", { name: "Request rejected" })).toBeVisible();
  const post = requests.find(request => request.method === "POST");
  expect(JSON.parse(post?.body ?? "{}" )).toEqual({ decision: "REJECT", note: "Quantity does not match the order" });
});

test("a public approval decision preserves a safe backend failure and allows retry", async ({ page }) => {
  let decisionAttempts = 0;
  await page.route(`**/api/v1/public/approval-links/${publicApprovalToken}`, route => route.fulfill({
    status: 200,
    headers: cors,
    contentType: "application/json",
    body: JSON.stringify(publicApproval),
  }));
  await page.route(`**/api/v1/public/approval-links/${publicApprovalToken}/decide`, route => {
    decisionAttempts += 1;
    return route.fulfill({
      status: decisionAttempts === 1 ? 503 : 200,
      headers: { ...cors, ...(decisionAttempts === 1 ? { "X-Request-ID": "req-public-approval-503" } : {}) },
      contentType: "application/json",
      body: JSON.stringify(decisionAttempts === 1
        ? { detail: "Approval service is temporarily unavailable" }
        : { status: "APPROVED" }),
    });
  });
  await page.goto(`/approve/${publicApprovalToken}`);
  await page.getByRole("button", { name: "Approve request" }).click();
  await page.getByRole("button", { name: "Confirm approval" }).click();
  await expect(page.getByRole("alert")).toContainText("Approval service is temporarily unavailable");
  await expect(page.getByText("Request ID: req-public-approval-503")).toBeVisible();
  await page.getByRole("button", { name: "Try approval again" }).click();
  await expect(page.getByRole("heading", { name: "Approval submitted" })).toBeVisible();
  expect(decisionAttempts).toBe(2);
});

test("public approval remains accessible and readable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await mockPublicApproval(page, { status: 200, body: publicApproval });
  await page.goto(`/approve/${publicApprovalToken}`);
  await expect(page.getByRole("heading", { name: publicApproval.title })).toBeVisible();
  const approve = page.getByRole("button", { name: "Approve request" });
  const box = await approve.boundingBox();
  expect(box?.height).toBeGreaterThanOrEqual(44);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
});
