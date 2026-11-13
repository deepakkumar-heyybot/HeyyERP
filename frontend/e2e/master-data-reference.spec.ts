import { expect, test } from '@playwright/test';
import { cors, InvitationResponse, mockInvitationAttempt, submitInvitationSignIn, submitPasswordRecovery, totpUri, backupCodes, openAuthenticatorSetup, mockTwoFactorSignIn, submitTwoFactorSignIn, MeFixture, UnitFixture, CountFixtures, BootstrapResponse, BootstrapFixtures, defaultUnits, mockSignInAndMe, grantedProfile, approvalOne, approvalTwo, mockApprovals, approvalDetail, mockApprovalDetail, notificationOne, notificationTwo, mockNotifications, publicApprovalToken, publicApproval, mockPublicApproval, plantAlpha, plantBeta, departmentProduction, departmentFinishing, mockDepartments, storeRaw, storeFinished, mockStores, paymentTermNet30, taxCodeGst18, categoryRubber, categoryNatural, uomKilogram, mockReferenceData, vendorOrbit, mockVendors, mockCustomerDetail, customerAcme, mockCustomers, mockVendorDetail, itemRubber, mockItems, itemFinished, itemPolicy, itemSpec, itemBom, itemChange, itemPart, mockItemDetail, mockBoms, bomDraft, bomPrevious, mockBomDetail, routeMouldV2, routeMouldV1, routeMetal, mockRoutes, indentProfile, indentAlpha, indentBeta, mockIndentRegister, indentDetailFixture, indentPutCors, mockOwnedIndent, rfqProfile, rfqAlpha, rfqBeta, rfqLineFixture, rfqInvitations, rfqDetailFixture, mockRfqRegister, poAlpha, poBeta, poLine, poDetail, mockPoRegister, mockPoEditCatalog, inventoryProfile, grnAlpha, grnBeta, grnDetail, mockGrnRegister, mockGrnCreationSources, stockAlpha, stockBeta, stockTrace, mockStockSources, issueProfile, issuePlan, postedIssue, issueSummary, mockIssueHistory } from './support/fixtures';

test("all four reference types render and keyboard tab changes persist in the URL", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockReferenceData(page);
  await page.goto("/app/master-data/reference-data");
  await expect(page.getByRole("heading", { name: "Reference data" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Payment term results" }).getByText(paymentTermNet30.name)).toBeVisible();

  const paymentTab = page.getByRole("tab", { name: "Payment terms" });
  await paymentTab.focus();
  await paymentTab.press("ArrowRight");
  await expect(page).toHaveURL(/type=tax-codes/);
  await expect(page.getByRole("table", { name: "Tax code results" }).getByText(taxCodeGst18.description)).toBeVisible();

  await page.getByRole("tab", { name: "Item categories" }).click();
  await expect(page).toHaveURL(/type=item-categories/);
  await expect(page.getByRole("table", { name: "Item category results" }).getByText(categoryNatural.name)).toBeVisible();

  await page.getByRole("tab", { name: "Units of measure" }).click();
  await expect(page).toHaveURL(/type=uom/);
  await expect(page.getByRole("table", { name: "Unit of measure results" }).getByText(uomKilogram.name)).toBeVisible();
});

test("UoM is explicitly read-only and readers never receive reference write actions", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockReferenceData(page);
  await page.goto("/app/master-data/reference-data?type=uom");
  await expect(page.getByText("Units of measure are seeded globally and are read-only.")).toBeVisible();
  await expect(page.getByRole("button", { name: /create/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /edit/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /delete/i })).toHaveCount(0);

  await page.getByRole("tab", { name: "Payment terms" }).click();
  await expect(page.getByRole("button", { name: "Create payment term" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: `Edit payment term ${paymentTermNet30.code}` })).toHaveCount(0);
});

test("a reference manager creates and edits payment terms with exact backend fields", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "refdata.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockReferenceData(page);
  let created: Record<string, unknown> | null = null;
  let updated: Record<string, unknown> | null = null;
  await page.route("**/api/v1/ref/payment-terms", route => {
    if (route.request().method() !== "POST") return route.fallback();
    created = route.request().postDataJSON();
    return route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify({ ...created, payment_term_id: "eeeeeeee-1111-1111-1111-111111111111" }) });
  });
  await page.route(`**/api/v1/ref/payment-terms/${paymentTermNet30.payment_term_id}`, route => {
    updated = route.request().postDataJSON();
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...paymentTermNet30, ...updated }) });
  });
  await page.goto("/app/master-data/reference-data");
  await page.getByRole("button", { name: "Create payment term" }).click();
  await page.getByLabel("Payment term code").fill("NET45");
  await page.getByLabel("Payment term name").fill("Net 45 days");
  await page.getByLabel("Due days").fill("45");
  await page.getByRole("button", { name: "Save payment term" }).click();
  await expect(page.getByText("Payment term created.", { exact: true })).toBeVisible();
  expect(created).toEqual({ code: "NET45", name: "Net 45 days", due_days: 45 });

  await page.getByRole("button", { name: `Edit payment term ${paymentTermNet30.code}` }).click();
  await page.getByLabel("Payment term name").fill("Standard net 30");
  await page.getByLabel("Due days").fill("30");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Payment term updated.", { exact: true })).toBeVisible();
  expect(updated).toEqual({ name: "Standard net 30", due_days: 30 });
});

test("tax status uses the backend filter and GST rate strings survive create and edit", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "refdata.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  const requests = await mockReferenceData(page);
  let created: Record<string, unknown> | null = null;
  let updated: Record<string, unknown> | null = null;
  await page.route("**/api/v1/ref/tax-codes", route => {
    if (route.request().method() !== "POST") return route.fallback();
    created = route.request().postDataJSON();
    return route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify({ ...created, tax_code_id: "ffffffff-1111-1111-1111-111111111111", active: true }) });
  });
  await page.route(`**/api/v1/ref/tax-codes/${taxCodeGst18.tax_code_id}`, route => {
    updated = route.request().postDataJSON();
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...taxCodeGst18, ...updated }) });
  });
  await page.goto("/app/master-data/reference-data?type=tax-codes");
  await page.getByLabel("Status filter").selectOption("false");
  await expect(page).toHaveURL(/active=false/);
  await expect.poll(() => requests.some(url => url.includes("/ref/tax-codes?") && url.includes("active=false"))).toBe(true);

  await page.getByRole("button", { name: "Create tax code" }).click();
  await page.getByLabel("Tax code", { exact: true }).fill("GST5");
  await page.getByLabel("Description").fill("GST at five point one two five percent");
  await page.getByLabel("GST rate").fill("5.1250");
  await page.getByRole("button", { name: "Save tax code" }).click();
  expect(created).toEqual({ code: "GST5", description: "GST at five point one two five percent", gst_rate: "5.1250" });

  await page.getByLabel("Status filter").selectOption("");
  await page.getByRole("button", { name: `Edit tax code ${taxCodeGst18.code}` }).click();
  await expect(page.getByLabel("GST rate")).toHaveValue("18.0000");
  await page.getByLabel("Active tax code").uncheck();
  await page.getByRole("button", { name: "Save changes" }).click();
  expect(updated).toEqual({ description: taxCodeGst18.description, gst_rate: "18.0000", active: false });
});

test("category hierarchy writes use exact IDs and validation errors preserve edits", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "refdata.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockReferenceData(page);
  let created: Record<string, unknown> | null = null;
  await page.route("**/api/v1/ref/item-categories", route => {
    if (route.request().method() !== "POST") return route.fallback();
    created = route.request().postDataJSON();
    return route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify({ ...created, category_id: "12121212-1111-1111-1111-111111111111" }) });
  });
  await page.route(`**/api/v1/ref/item-categories/${categoryRubber.category_id}`, route => route.fulfill({ status: 422, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "A category cannot be its own ancestor" }) }));
  await page.goto("/app/master-data/reference-data?type=item-categories");
  await page.getByRole("button", { name: "Create item category" }).click();
  await page.getByLabel("Category code").fill("SYNTHETIC");
  await page.getByLabel("Category name").fill("Synthetic rubber");
  await page.getByLabel("Parent category").selectOption(categoryRubber.category_id);
  await page.getByRole("button", { name: "Save item category" }).click();
  expect(created).toEqual({ code: "SYNTHETIC", name: "Synthetic rubber", parent_category_id: categoryRubber.category_id });

  await page.getByRole("button", { name: `Edit item category ${categoryRubber.code}` }).click();
  await page.getByLabel("Category name").fill("Rubber material families");
  await page.getByLabel("Parent category").selectOption(categoryNatural.category_id);
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("alert")).toContainText("A category cannot be its own ancestor");
  await expect(page.getByLabel("Category name")).toHaveValue("Rubber material families");
  await expect(page.getByLabel("Parent category")).toHaveValue(categoryNatural.category_id);
});

test("reference search and client pagination persist without inventing backend search parameters", async ({ page }) => {
  const terms = Array.from({ length: 12 }, (_, index) => ({
    payment_term_id: `${String(index + 1).padStart(8, "0")}-2222-2222-2222-222222222222`,
    code: `NET${index + 1}`,
    name: `Term ${index + 1}`,
    due_days: index + 1,
  }));
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  const requests = await mockReferenceData(page, { paymentTerms: terms });
  await page.goto("/app/master-data/reference-data");
  await expect(page.getByText("Showing 1–10 of 12 records")).toBeVisible();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page).toHaveURL(/page=2/);
  await expect(page.getByRole("table", { name: "Payment term results" }).getByText("Term 11", { exact: true })).toBeVisible();
  await page.getByLabel("Search reference data").fill("Term 2");
  await expect(page).toHaveURL(/q=Term\+2/);
  await expect(page).not.toHaveURL(/page=2/);
  await expect(page.getByRole("table", { name: "Payment term results" }).getByText("Term 2", { exact: true })).toBeVisible();
  expect(requests.every(url => !url.includes("q="))).toBe(true);
});

test("reference data exposes loading and empty states", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await page.route("**/api/v1/ref/payment-terms?**", async route => {
    await new Promise(resolve => setTimeout(resolve, 350));
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) });
  });
  await page.goto("/app/master-data/reference-data");
  await expect(page.getByText("Loading payment terms…")).toBeVisible();
  await expect(page.getByRole("heading", { name: "No payment terms found" })).toBeVisible();
  await expect(page.getByText("Create the first payment term if you have permission.")).toBeVisible();
});

test("reference server errors retain detail and request IDs and recover on retry", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  let attempts = 0;
  await page.route("**/api/v1/ref/payment-terms?**", route => {
    attempts += 1;
    return route.fulfill({
      status: attempts === 1 ? 503 : 200,
      headers: { ...cors, ...(attempts === 1 ? { "X-Request-ID": "req-reference-503" } : {}) },
      contentType: "application/json",
      body: JSON.stringify(attempts === 1 ? { detail: "Reference data service is temporarily unavailable" } : { items: [paymentTermNet30], total: 1, limit: 200, offset: 0 }),
    });
  });
  await page.goto("/app/master-data/reference-data");
  await expect(page.getByRole("alert")).toContainText("Reference data service is temporarily unavailable");
  await expect(page.getByText("Request ID: req-reference-503")).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("table", { name: "Payment term results" }).getByText(paymentTermNet30.name)).toBeVisible();
  expect(attempts).toBe(2);
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }]) {
  test(`reference data reflows without overflow at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
    await mockReferenceData(page);
    await page.goto("/app/master-data/reference-data");
    if (viewport.width === 360) {
      await expect(page.getByLabel("Reference type")).toBeVisible();
      await expect(page.getByRole("tablist", { name: "Reference types" })).toBeHidden();
      await page.getByLabel("Reference type").selectOption("uom");
      await expect(page.getByRole("list", { name: "Unit of measure results" }).getByText(uomKilogram.name)).toBeVisible();
    } else {
      await expect(page.getByRole("tablist", { name: "Reference types" })).toBeVisible();
      await expect(page.getByRole("table", { name: "Payment term results" })).toBeVisible();
    }
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(overflow).toBe(false);
  });
}
