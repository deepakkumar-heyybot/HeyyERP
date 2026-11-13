import { expect, test } from '@playwright/test';
import { cors, InvitationResponse, mockInvitationAttempt, submitInvitationSignIn, submitPasswordRecovery, totpUri, backupCodes, openAuthenticatorSetup, mockTwoFactorSignIn, submitTwoFactorSignIn, MeFixture, UnitFixture, CountFixtures, BootstrapResponse, BootstrapFixtures, defaultUnits, mockSignInAndMe, grantedProfile, approvalOne, approvalTwo, mockApprovals, approvalDetail, mockApprovalDetail, notificationOne, notificationTwo, mockNotifications, publicApprovalToken, publicApproval, mockPublicApproval, plantAlpha, plantBeta, departmentProduction, departmentFinishing, mockDepartments, storeRaw, storeFinished, mockStores, paymentTermNet30, taxCodeGst18, categoryRubber, categoryNatural, uomKilogram, mockReferenceData, vendorOrbit, mockVendors, mockCustomerDetail, customerAcme, mockCustomers, mockVendorDetail, itemRubber, mockItems, itemFinished, itemPolicy, itemSpec, itemBom, itemChange, itemPart, mockItemDetail, mockBoms, bomDraft, bomPrevious, mockBomDetail, routeMouldV2, routeMouldV1, routeMetal, mockRoutes, indentProfile, indentAlpha, indentBeta, mockIndentRegister, indentDetailFixture, indentPutCors, mockOwnedIndent, rfqProfile, rfqAlpha, rfqBeta, rfqLineFixture, rfqInvitations, rfqDetailFixture, mockRfqRegister, poAlpha, poBeta, poLine, poDetail, mockPoRegister, mockPoEditCatalog, inventoryProfile, grnAlpha, grnBeta, grnDetail, mockGrnRegister, mockGrnCreationSources, stockAlpha, stockBeta, stockTrace, mockStockSources, issueProfile, issuePlan, postedIssue, issueSummary, mockIssueHistory } from './support/fixtures';

test("vendor readers see legal identity, MSME, terms, approval status, and no write actions", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockVendors(page);
  await page.goto("/app/master-data/parties/vendors");
  const table = page.getByRole("table", { name: "Vendor results" });
  await expect(table.getByText(vendorOrbit.name)).toBeVisible();
  await expect(table.getByText(vendorOrbit.gstin)).toBeVisible();
  await expect(table.locator("tbody").getByText("MSME", { exact: true })).toBeVisible();
  await expect(table.getByText("Net 30 days")).toBeVisible();
  await expect(table.getByText("Pending approval")).toBeVisible();
  await expect(page.getByRole("button", { name: "Create vendor" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /delete/i })).toHaveCount(0);
});

test("vendor search, workflow status, and pagination use exact supported parameters", async ({ page }) => {
  const vendors = Array.from({ length: 22 }, (_, index) => ({ ...vendorOrbit, vendor_id: `${String(index + 1).padStart(8, "0")}-4444-4444-4444-444444444444`, code: `VEND${index + 1}`, name: `Vendor ${index + 1}`, status: index === 21 ? "APPROVED" : "PENDING_APPROVAL" }));
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  const requests = await mockVendors(page, vendors);
  await page.goto("/app/master-data/parties/vendors");
  await expect(page.getByText("Showing 1–20 of 22 records")).toBeVisible();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect.poll(() => requests.some(url => url.includes("offset=20") && url.includes("limit=20"))).toBe(true);
  await page.getByLabel("Search vendors").fill("Vendor 2");
  await expect.poll(() => requests.some(url => url.includes("q=Vendor+2"))).toBe(true);
  await page.getByLabel("Approval status").selectOption("APPROVED");
  await expect.poll(() => requests.some(url => url.includes("vendor_status=APPROVED"))).toBe(true);
  expect(requests.every(url => !new URL(url).searchParams.has("active"))).toBe(true);
});

test("a vendor manager creates a pending vendor with exact legal and commercial fields", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "vendor.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockVendors(page);
  let created: Record<string, unknown> | null = null;
  await page.route("**/api/v1/parties/vendors", route => {
    if (route.request().method() !== "POST") return route.fallback();
    created = route.request().postDataJSON();
    return route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify({ ...created, vendor_id: "eeeeeeee-2222-2222-2222-222222222222", status: "PENDING_APPROVAL" }) });
  });
  await page.goto("/app/master-data/parties/vendors");
  await page.getByRole("button", { name: "Create vendor" }).click();
  await expect(page).toHaveURL(/\/app\/master-data\/parties\/vendors\/new$/);
  await page.getByLabel("Vendor code").fill("vrub-supply");
  await page.getByLabel("Vendor name").fill("V-Rub Supply Partners");
  await page.getByLabel("GSTIN").fill("27ABCDE1234F1Z5");
  await page.getByLabel("PAN").fill("ABCDE1234F");
  await page.getByLabel("MSME registered").check();
  await page.getByLabel("Payment term").selectOption(paymentTermNet30.payment_term_id);
  await page.getByRole("button", { name: "Save vendor" }).click();
  expect(created).toEqual({ code: "VRUB-SUPPLY", name: "V-Rub Supply Partners", gstin: "27ABCDE1234F1Z5", pan: "ABCDE1234F", msme_flag: true, payment_term_id: paymentTermNet30.payment_term_id });
  await expect(page).toHaveURL(/\/app\/master-data\/parties\/vendors\/eeeeeeee-2222-2222-2222-222222222222$/);
});

test("vendor validation errors preserve legal identity input", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "vendor.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockVendors(page);
  await page.route("**/api/v1/parties/vendors", route => route.request().method() === "POST" ? route.fulfill({ status: 422, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "PAN must match the PAN embedded in GSTIN" }) }) : route.fallback());
  await page.goto("/app/master-data/parties/vendors/new");
  await page.getByLabel("Vendor code").fill("ORBIT");
  await page.getByLabel("Vendor name").fill("Orbit revised");
  await page.getByRole("button", { name: "Save vendor" }).click();
  await expect(page.getByRole("alert")).toContainText("PAN must match the PAN embedded in GSTIN");
  await expect(page.getByLabel("Vendor name")).toHaveValue("Orbit revised");
});

test("vendor list exposes loading, empty, and recoverable request-ID errors", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  let attempts = 0;
  await page.route("**/api/v1/parties/vendors?**", async route => {
    attempts += 1; await new Promise(resolve => setTimeout(resolve, 200));
    return route.fulfill({ status: attempts === 1 ? 503 : 200, headers: { ...cors, ...(attempts === 1 ? { "X-Request-ID": "req-vendors-503" } : {}) }, contentType: "application/json", body: JSON.stringify(attempts === 1 ? { detail: "Vendor master is temporarily unavailable" } : { items: [], total: 0, limit: 20, offset: 0 }) });
  });
  await page.route("**/api/v1/ref/payment-terms?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.goto("/app/master-data/parties/vendors");
  await expect(page.getByText("Loading vendors…")).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Vendor master is temporarily unavailable");
  await expect(page.getByText("Request ID: req-vendors-503")).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "No vendors found" })).toBeVisible();
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }]) {
  test(`vendor results preserve MSME and status without overflow at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
    await mockVendors(page);
    await page.goto("/app/master-data/parties/vendors");
    if (viewport.width === 360) {
      const cards = page.getByRole("list", { name: "Vendor results" });
      await expect(cards.getByText(vendorOrbit.name)).toBeVisible();
      await expect(cards.locator("span.msme-badge").getByText("MSME", { exact: true })).toBeVisible();
      await expect(cards.getByText("Pending approval")).toBeVisible();
    } else await expect(page.getByRole("table", { name: "Vendor results" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);
  });
}

test("vendor detail renders legal data and hides unauthorized maintenance and bank controls", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockVendorDetail(page);
  await page.goto(`/app/master-data/parties/vendors/${vendorOrbit.vendor_id}`);
  await expect(page.getByRole("heading", { name: vendorOrbit.name })).toBeVisible();
  await expect(page.getByText(vendorOrbit.gstin)).toBeVisible();
  await expect(page.getByText(vendorOrbit.pan, { exact: true })).toBeVisible();
  await expect(page.getByText("Net 30 days")).toBeVisible();
  await expect(page.locator("span.status-badge").getByText("Pending approval", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit vendor" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Bank account" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /delete/i })).toHaveCount(0);
});

test("vendor managers edit exact supported fields without inventing status updates", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "vendor.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockVendorDetail(page);
  let updated: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/parties/vendors/${vendorOrbit.vendor_id}`, route => {
    if (route.request().method() !== "PATCH") return route.fallback();
    updated = route.request().postDataJSON();
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...vendorOrbit, ...updated }) });
  });
  await page.goto(`/app/master-data/parties/vendors/${vendorOrbit.vendor_id}`);
  await page.getByRole("button", { name: "Edit vendor" }).click();
  await page.getByLabel("Vendor name").fill("Orbit Rubber Industries");
  await page.getByLabel("MSME registered").uncheck();
  await page.getByRole("button", { name: "Save changes" }).click();
  expect(updated).toEqual({ name: "Orbit Rubber Industries", gstin: vendorOrbit.gstin, pan: vendorOrbit.pan, msme_flag: false, payment_term_id: paymentTermNet30.payment_term_id });
  await expect(page.getByText("Vendor updated.", { exact: true })).toBeVisible();
});

test("vendor deactivation is explicit, permission-gated, and never presented as delete", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "vendor.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockVendorDetail(page, { ...vendorOrbit, status: "APPROVED" });
  let calls = 0;
  await page.route(`**/api/v1/parties/vendors/${vendorOrbit.vendor_id}/deactivate`, route => { calls += 1; return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...vendorOrbit, status: "INACTIVE" }) }); });
  await page.goto(`/app/master-data/parties/vendors/${vendorOrbit.vendor_id}`);
  await page.getByRole("button", { name: "Deactivate vendor" }).click();
  await expect(page.getByRole("heading", { name: "Deactivate this vendor?" })).toBeVisible();
  expect(calls).toBe(0);
  await page.getByRole("button", { name: "Confirm deactivation" }).click();
  expect(calls).toBe(1);
  await expect(page.locator("span.status-badge").getByText("Inactive", { exact: true })).toBeVisible();
});

test("finance pay users see only masked bank data and can replace it safely", async ({ page }) => {
  const payUser = { ...grantedProfile, permissions: [...grantedProfile.permissions, "fin.pay"] };
  await mockSignInAndMe(page, { status: 200, body: payUser }, [plantAlpha]);
  await mockVendorDetail(page);
  await page.route(`**/api/v1/finance/vendors/${vendorOrbit.vendor_id}/bank-account`, route => {
    if (route.request().method() === "GET") return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ vendor_id: vendorOrbit.vendor_id, account_holder: "Orbit Rubber Supplies", account_no_masked: "********4321", ifsc: "HDFC0000123", bank_name: "HDFC Bank" }) });
    return route.fallback();
  });
  let update: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/finance/vendors/${vendorOrbit.vendor_id}/bank-account`, route => {
    if (route.request().method() !== "PUT") return route.fallback();
    update = route.request().postDataJSON();
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ vendor_id: vendorOrbit.vendor_id, account_holder: "Orbit Rubber Supplies", account_no_masked: "********6789", ifsc: "ICIC0000456", bank_name: "ICICI Bank" }) });
  });
  await page.goto(`/app/master-data/parties/vendors/${vendorOrbit.vendor_id}`);
  await expect(page.getByText("********4321")).toBeVisible();
  await page.getByRole("button", { name: "Update bank account" }).click();
  await page.getByLabel("Account holder").fill("Orbit Rubber Supplies");
  await page.getByLabel("Account number").fill("1234567890126789");
  await page.getByLabel("IFSC").fill("ICIC0000456");
  await page.getByLabel("Bank name").fill("ICICI Bank");
  await page.getByRole("button", { name: "Save bank account" }).click();
  expect(update).toEqual({ account_holder: "Orbit Rubber Supplies", account_no: "1234567890126789", ifsc: "ICIC0000456", bank_name: "ICICI Bank" });
  await expect(page.getByText("********6789")).toBeVisible();
  await expect(page.getByText("1234567890126789")).toHaveCount(0);
});

test("missing bank details are a clear empty state only for fin.pay users", async ({ page }) => {
  const payUser = { ...grantedProfile, permissions: [...grantedProfile.permissions, "fin.pay"] };
  await mockSignInAndMe(page, { status: 200, body: payUser }, [plantAlpha]);
  await mockVendorDetail(page);
  await page.route(`**/api/v1/finance/vendors/${vendorOrbit.vendor_id}/bank-account`, route => route.fulfill({ status: 404, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "Bank account not found" }) }));
  await page.goto(`/app/master-data/parties/vendors/${vendorOrbit.vendor_id}`);
  await expect(page.getByRole("heading", { name: "No bank account saved" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Add bank account" })).toBeVisible();
});

test("vendor detail stays readable and keeps masked bank data on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const payUser = { ...grantedProfile, permissions: [...grantedProfile.permissions, "fin.pay"] };
  await mockSignInAndMe(page, { status: 200, body: payUser }, [plantAlpha]);
  await mockVendorDetail(page);
  await page.route(`**/api/v1/finance/vendors/${vendorOrbit.vendor_id}/bank-account`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ vendor_id: vendorOrbit.vendor_id, account_holder: "Orbit Rubber Supplies", account_no_masked: "********4321", ifsc: "HDFC0000123", bank_name: "HDFC Bank" }) }));
  await page.goto(`/app/master-data/parties/vendors/${vendorOrbit.vendor_id}`);
  await expect(page.getByRole("heading", { name: vendorOrbit.name })).toBeVisible();
  await expect(page.getByText("********4321")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);
});

test("vendor deactivation confirms by keyboard and Escape returns focus without sending", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "vendor.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockVendorDetail(page);
  let calls = 0;
  await page.route(`**/api/v1/parties/vendors/${vendorOrbit.vendor_id}/deactivate`, route => { calls += 1; return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...vendorOrbit, status: "INACTIVE" }) }); });
  await page.goto(`/app/master-data/parties/vendors/${vendorOrbit.vendor_id}`);
  await page.getByRole("button", { name: "Deactivate vendor" }).click();
  await expect(page.getByRole("dialog", { name: "Deactivate this vendor?" }).getByRole("button", { name: "Cancel" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Deactivate this vendor?" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Deactivate vendor" })).toBeFocused();
  expect(calls).toBe(0);
});

test("vendor deactivation conflict preserves status and reports the backend reason", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "vendor.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockVendorDetail(page, { ...vendorOrbit, status: "APPROVED" });
  await page.route(`**/api/v1/parties/vendors/${vendorOrbit.vendor_id}/deactivate`, route => route.fulfill({ status: 409, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "Vendor status changed while this page was open" }) }));
  await page.goto(`/app/master-data/parties/vendors/${vendorOrbit.vendor_id}`);
  await page.getByRole("button", { name: "Deactivate vendor" }).click();
  await page.getByRole("button", { name: "Confirm deactivation" }).click();
  await expect(page.getByRole("alert")).toContainText("Vendor status changed while this page was open");
  await expect(page.locator("span.status-badge").getByText("Approved", { exact: true })).toBeVisible();
});

test("vendor bank server failure keeps the request ID and never shows a bank editor", async ({ page }) => {
  const payUser = { ...grantedProfile, permissions: [...grantedProfile.permissions, "fin.pay"] };
  await mockSignInAndMe(page, { status: 200, body: payUser }, [plantAlpha]);
  await mockVendorDetail(page);
  await page.route(`**/api/v1/finance/vendors/${vendorOrbit.vendor_id}/bank-account`, route => route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-bank-503" }, contentType: "application/json", body: JSON.stringify({ detail: "Bank account service is temporarily unavailable" }) }));
  await page.goto(`/app/master-data/parties/vendors/${vendorOrbit.vendor_id}`);
  await expect(page.getByRole("alert")).toContainText("Bank account service is temporarily unavailable");
  await expect(page.getByText("Request ID: req-bank-503")).toBeVisible();
  await expect(page.getByRole("button", { name: /bank account/i })).toHaveCount(0);
});

test("vendor edit stacks its heading and uses the full detail width on tablet", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "vendor.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockVendorDetail(page);
  await page.goto(`/app/master-data/parties/vendors/${vendorOrbit.vendor_id}`);
  await page.getByRole("button", { name: "Edit vendor" }).click();
  const back = await page.getByRole("link", { name: "Back to vendors" }).boundingBox();
  const heading = await page.getByRole("heading", { name: vendorOrbit.name }).boundingBox();
  const card = await page.getByRole("heading", { name: "Vendor master" }).locator("xpath=../..").boundingBox();
  expect(back && heading && card).toBeTruthy();
  expect(heading!.y).toBeGreaterThan(back!.y + back!.height);
  expect(card!.width).toBeGreaterThan(650);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);
});
