import { expect, test } from '@playwright/test';
import { cors, InvitationResponse, mockInvitationAttempt, submitInvitationSignIn, submitPasswordRecovery, totpUri, backupCodes, openAuthenticatorSetup, mockTwoFactorSignIn, submitTwoFactorSignIn, MeFixture, UnitFixture, CountFixtures, BootstrapResponse, BootstrapFixtures, defaultUnits, mockSignInAndMe, grantedProfile, approvalOne, approvalTwo, mockApprovals, approvalDetail, mockApprovalDetail, notificationOne, notificationTwo, mockNotifications, publicApprovalToken, publicApproval, mockPublicApproval, plantAlpha, plantBeta, departmentProduction, departmentFinishing, mockDepartments, storeRaw, storeFinished, mockStores, paymentTermNet30, taxCodeGst18, categoryRubber, categoryNatural, uomKilogram, mockReferenceData, vendorOrbit, mockVendors, mockCustomerDetail, customerAcme, mockCustomers, mockVendorDetail, itemRubber, mockItems, itemFinished, itemPolicy, itemSpec, itemBom, itemChange, itemPart, mockItemDetail, mockBoms, bomDraft, bomPrevious, mockBomDetail, routeMouldV2, routeMouldV1, routeMetal, mockRoutes, indentProfile, indentAlpha, indentBeta, mockIndentRegister, indentDetailFixture, indentPutCors, mockOwnedIndent, rfqProfile, rfqAlpha, rfqBeta, rfqLineFixture, rfqInvitations, rfqDetailFixture, mockRfqRegister, poAlpha, poBeta, poLine, poDetail, mockPoRegister, mockPoEditCatalog, inventoryProfile, grnAlpha, grnBeta, grnDetail, mockGrnRegister, mockGrnCreationSources, stockAlpha, stockBeta, stockTrace, mockStockSources, issueProfile, issuePlan, postedIssue, issueSummary, mockIssueHistory } from './support/fixtures';

test("BOM readers see item, revision, status, and validity without creation controls", async ({ page }) => {
  const reader = { ...grantedProfile, permissions: grantedProfile.permissions.filter(permission => permission !== "bom.manage") };
  await mockSignInAndMe(page, { status: 200, body: reader }, [plantAlpha]);
  await mockBoms(page);
  await page.goto("/app/master-data/engineering/boms");
  await expect(page.getByRole("navigation", { name: "Engineering masters" }).getByRole("link", { name: "Items" })).toHaveAttribute("href", "/app/master-data/engineering");
  const table = page.getByRole("table", { name: "BOM results" });
  await expect(table.getByText(itemBom.item_code)).toBeVisible();
  await expect(table.getByText(itemBom.item_name)).toBeVisible();
  await expect(table.getByText("Revision 2")).toBeVisible();
  await expect(table.getByText("Approved")).toBeVisible();
  await expect(table.getByText(itemBom.valid_from)).toBeVisible();
  await expect(page.getByRole("button", { name: "Create BOM" })).toHaveCount(0);
});

test("a draft with no end date is not described as currently effective", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockBoms(page, [{ ...itemBom, status: "DRAFT", valid_to: null }]);
  await page.goto("/app/master-data/engineering/boms");
  const table = page.getByRole("table", { name: "BOM results" });
  await expect(table.getByText("Draft")).toBeVisible();
  await expect(table.getByText("No end date")).toBeVisible();
  await expect(table.getByText("Current / open")).toHaveCount(0);
});

test("BOM item and status filters use the backend; valid-from dates filter all revisions locally", async ({ page }) => {
  const boms = Array.from({ length: 205 }, (_, index) => ({ ...itemBom, bom_id: `aaaaaaaa-${String(index + 1).padStart(4, "0")}-4444-4444-444444444444`, version: index + 1, valid_from: index === 204 ? "2026-11-01" : "2026-10-01" }));
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  const requests = await mockBoms(page, boms);
  await page.goto("/app/master-data/engineering/boms");
  await page.getByLabel("BOM item").selectOption(itemFinished.item_id);
  await page.getByLabel("BOM status").selectOption("APPROVED");
  await page.getByLabel("Valid from start").fill("2026-11-01");
  await page.getByLabel("Valid from end").fill("2026-11-30");
  await expect(page).toHaveURL(/from=2026-11-01.*through=2026-11-30/);
  await expect(page.getByRole("table", { name: "BOM results" }).getByText("Revision 205")).toBeVisible();
  await expect(page.getByText("Showing 1–1 of 1 records")).toBeVisible();
  await expect.poll(() => requests.some(raw => new URL(raw).searchParams.get("offset") === "200")).toBe(true);
  expect(requests.filter(raw => new URL(raw).searchParams.get("status") === "APPROVED").every(raw => new URL(raw).searchParams.get("item_id") === itemFinished.item_id && !new URL(raw).searchParams.has("from") && !new URL(raw).searchParams.has("to"))).toBe(true);
});

test("BOM manager creates an empty draft for a made item and preserves list filters on return", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "bom.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockBoms(page);
  let body: Record<string, unknown> | null = null;
  const created = { ...itemBom, status: "DRAFT", version: 1, lines: [] };
  await page.route("**/api/v1/product/boms", route => route.request().method() === "POST" ? (body = route.request().postDataJSON(), route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify(created) })) : route.fallback());
  await page.route(`**/api/v1/product/boms/${created.bom_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(created) }));
  await page.goto(`/app/master-data/engineering/boms?status=DRAFT`);
  await page.getByRole("button", { name: "Create BOM" }).click();
  await expect(page).toHaveURL(/\/boms\/new\?status=DRAFT/);
  await expect(page.getByRole("heading", { name: "Create BOM" })).toBeVisible();
  await page.locator("form").getByLabel("BOM item").selectOption(itemFinished.item_id);
  await expect(page.locator("form").getByLabel("BOM item").locator("option", { hasText: itemRubber.code })).toHaveCount(0);
  await page.getByRole("button", { name: "Create draft" }).click();
  expect(body).toEqual({ item_id: itemFinished.item_id, lines: [] });
  await expect(page).toHaveURL(/\/boms\/aaaaaaaa-4444-4444-4444-444444444444\?status=DRAFT/);
  await expect(page.getByRole("link", { name: "Back to BOMs" })).toHaveAttribute("href", "/app/master-data/engineering/boms?status=DRAFT");
});

test("BOM 409 creation conflict preserves the selected item and backend detail", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "bom.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockBoms(page);
  await page.route("**/api/v1/product/boms", route => route.request().method() === "POST" ? route.fulfill({ status: 409, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "This item already has a BOM; revise it to make a new version" }) }) : route.fallback());
  await page.goto("/app/master-data/engineering/boms/new");
  await page.getByLabel("BOM item").selectOption(itemFinished.item_id);
  await page.getByRole("button", { name: "Create draft" }).click();
  await expect(page.getByRole("alert")).toContainText("already has a BOM");
  await expect(page.getByLabel("BOM item")).toHaveValue(itemFinished.item_id);
});

test("BOM list shows loading, empty, and request-ID error recovery", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockBoms(page, []);
  let attempts = 0;
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/v1/product/boms?**", async route => {
    attempts += 1;
    if (attempts === 1) await pending;
    return route.fulfill(attempts === 1 ? { status: 503, headers: { ...cors, "X-Request-ID": "req-bom-503" }, contentType: "application/json", body: JSON.stringify({ detail: "BOM register temporarily unavailable" }) } : { status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 20, offset: 0 }) });
  });
  await page.goto("/app/master-data/engineering/boms");
  await expect(page.getByText("Loading BOMs…")).toBeVisible();
  release();
  await expect(page.getByRole("alert")).toContainText("BOM register temporarily unavailable");
  await expect(page.getByText("Request ID: req-bom-503")).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "No BOMs found" })).toBeVisible();
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }]) {
  test(`BOM results preserve item, revision, and status without overflow at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
    await mockBoms(page);
    await page.goto("/app/master-data/engineering/boms");
    const results = page.getByRole(viewport.width === 360 ? "list" : "table", { name: "BOM results" });
    await expect(results.getByText(itemBom.item_code)).toBeVisible();
    await expect(results.getByText("Approved")).toBeVisible();
    if (viewport.width === 768) {
      await expect(page.getByText("Scroll table horizontally to view more columns.")).toBeVisible();
      const scrollingResults = page.getByRole("region", { name: "Scrollable BOM results" });
      await scrollingResults.focus();
      await page.keyboard.press("ArrowRight");
      await expect.poll(() => scrollingResults.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
}

test("BOM readers see revision, validity, components, and history without write controls", async ({ page }) => {
  const reader = { ...grantedProfile, permissions: grantedProfile.permissions.filter(permission => permission !== "bom.manage") };
  await mockSignInAndMe(page, { status: 200, body: reader }, [plantAlpha]);
  await mockBomDetail(page);
  await page.goto(`/app/master-data/engineering/boms/${itemBom.bom_id}?status=APPROVED`);
  await expect(page.getByRole("heading", { name: itemBom.item_name })).toBeVisible();
  await expect(page.getByText("Revision 2").first()).toBeVisible();
  await expect(page.getByText(itemBom.valid_from).first()).toBeVisible();
  await expect(page.getByText(itemBom.lines[0].component_name)).toBeVisible();
  await expect(page.getByText(itemBom.lines[0].quantity_per)).toBeVisible();
  await expect(page.getByRole("link", { name: "Revision 1" })).toBeVisible();
  await expect(page.getByRole("button", { name: /edit lines|submit|revise/i })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Back to BOMs" })).toHaveAttribute("href", "/app/master-data/engineering/boms?status=APPROVED");
});

test("approved BOM manager may revise through the backend but cannot edit frozen lines", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "bom.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockBomDetail(page);
  let reviseCalls = 0;
  await page.route(`**/api/v1/product/boms/${itemBom.bom_id}/revise`, route => { reviseCalls += 1; return route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify(bomDraft) }); });
  await page.route(`**/api/v1/product/boms/${bomDraft.bom_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(bomDraft) }));
  await page.goto(`/app/master-data/engineering/boms/${itemBom.bom_id}?status=APPROVED`);
  await expect(page.getByRole("button", { name: "Edit lines" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Submit for approval" })).toHaveCount(0);
  await page.getByRole("button", { name: "Revise BOM" }).click();
  await expect(page).toHaveURL(new RegExp(`/boms/${bomDraft.bom_id}\\?status=APPROVED`));
  await expect(page.getByText("Revision 3").first()).toBeVisible();
  expect(reviseCalls).toBe(1);
});

test("draft BOM line editor keeps quantity and scrap as strings in PUT", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "bom.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockBomDetail(page, bomDraft, [bomDraft, itemBom]);
  let body: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/product/boms/${bomDraft.bom_id}/lines`, route => route.request().method() === "PUT" ? (body = route.request().postDataJSON(), route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...bomDraft, lines: [{ ...bomDraft.lines[0], quantity_per: "3.125000" }] }) })) : route.fallback());
  await page.goto(`/app/master-data/engineering/boms/${bomDraft.bom_id}`);
  await page.getByRole("button", { name: "Edit lines" }).click();
  const editor = page.getByRole("dialog", { name: "Edit draft lines" });
  await expect(editor).toBeVisible();
  await editor.getByLabel("Quantity per").fill("3.125000");
  await editor.getByRole("button", { name: "Save lines" }).click();
  expect(body).toEqual({ lines: [{ component_item_id: itemRubber.item_id, quantity_per: "3.125000", scrap_factor: "0.010000" }] });
  await expect(page.getByText("3.125000")).toBeVisible();
  await expect(editor).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Edit lines" })).toBeFocused();
});

test("draft BOM allows component rows with supported types and blocks duplicate selection", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "bom.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockBomDetail(page, { ...bomDraft, lines: [] }, [bomDraft]);
  await page.goto(`/app/master-data/engineering/boms/${bomDraft.bom_id}`);
  await expect(page.getByRole("button", { name: "Submit for approval" })).toBeDisabled();
  await page.getByRole("button", { name: "Edit lines" }).click();
  const editor = page.getByRole("dialog", { name: "Edit draft lines" });
  await editor.getByRole("button", { name: "Add component" }).click();
  await editor.getByLabel("Component item").selectOption(itemRubber.item_id);
  await editor.getByRole("button", { name: "Add component" }).click();
  await expect(editor.getByLabel("Component item").nth(1).locator(`option[value="${itemRubber.item_id}"]`)).toHaveCount(0);
  await expect(editor.getByLabel("Component item").nth(1).locator(`option[value="${itemFinished.item_id}"]`)).toHaveCount(0);
});

test("draft submission validation keeps reason and effective date available for correction", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "bom.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockBomDetail(page, bomDraft, [bomDraft]);
  await page.route(`**/api/v1/product/boms/${bomDraft.bom_id}/submit`, route => route.fulfill({ status: 422, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "The effective date must be after version 2 takes effect" }) }));
  await page.goto(`/app/master-data/engineering/boms/${bomDraft.bom_id}`);
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await page.getByLabel("Change reason").fill("New compound mix");
  await page.getByLabel("Effective from").fill("2027-01-01");
  await page.getByRole("button", { name: "Confirm submission" }).click();
  await expect(page.getByRole("alert")).toContainText("effective date must be after version 2");
  await expect(page.getByLabel("Change reason")).toHaveValue("New compound mix");
  await expect(page.getByLabel("Effective from")).toHaveValue("2027-01-01");
});

test("revise conflicts retain the approved revision and explain the open draft", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "bom.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockBomDetail(page);
  await page.route(`**/api/v1/product/boms/${itemBom.bom_id}/revise`, route => route.fulfill({ status: 409, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "Version 3 is already being prepared" }) }));
  await page.goto(`/app/master-data/engineering/boms/${itemBom.bom_id}`);
  await page.getByRole("button", { name: "Revise BOM" }).click();
  await expect(page.getByRole("alert")).toContainText("Version 3 is already being prepared");
  await expect(page.getByText("Revision 2").first()).toBeVisible();
});

test("draft BOM submission sends reason and future date, then locks editing", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "bom.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockBomDetail(page, bomDraft, [bomDraft]);
  let body: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/product/boms/${bomDraft.bom_id}/submit`, route => route.request().method() === "POST" ? (body = route.request().postDataJSON(), route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...bomDraft, status: "PENDING_APPROVAL", valid_from: "2027-01-01", approval_id: "bbbbbbbb-5555-5555-5555-555555555555" }) })) : route.fallback());
  await page.goto(`/app/master-data/engineering/boms/${bomDraft.bom_id}`);
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await page.getByLabel("Change reason").fill("Updated compound mix");
  await page.getByLabel("Effective from").fill("2027-01-01");
  await page.getByRole("button", { name: "Confirm submission" }).click();
  expect(body).toEqual({ reason: "Updated compound mix", effective_from: "2027-01-01" });
  await expect(page.getByText("Pending approval").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit lines" })).toHaveCount(0);
});

test("draft BOM write conflict preserves editor input and backend detail", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "bom.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockBomDetail(page, bomDraft, [bomDraft]);
  await page.route(`**/api/v1/product/boms/${bomDraft.bom_id}/lines`, route => route.fulfill({ status: 409, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "Only a draft can be edited; this version is APPROVED" }) }));
  await page.goto(`/app/master-data/engineering/boms/${bomDraft.bom_id}`);
  await page.getByRole("button", { name: "Edit lines" }).click();
  const editor = page.getByRole("dialog", { name: "Edit draft lines" });
  await editor.getByLabel("Quantity per").fill("4.500000");
  await editor.getByRole("button", { name: "Save lines" }).click();
  await expect(editor.getByRole("alert")).toContainText("Only a draft can be edited");
  await expect(editor.getByLabel("Quantity per")).toHaveValue("4.500000");
});

test("BOM detail shows loading and request-ID errors with retry", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockBomDetail(page);
  let recover = false;
  let releaseInitial!: () => void;
  const initialResponse = new Promise<void>(resolve => { releaseInitial = resolve; });
  await page.route(`**/api/v1/product/boms/${itemBom.bom_id}`, async route => {
    if (!recover) await initialResponse;
    return route.fulfill(recover ? { status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(itemBom) } : { status: 503, headers: { ...cors, "X-Request-ID": "req-bom-detail-503" }, contentType: "application/json", body: JSON.stringify({ detail: "BOM detail unavailable" }) });
  });
  await page.goto(`/app/master-data/engineering/boms/${itemBom.bom_id}`);
  await expect(page.getByText("Loading BOM detail…")).toBeVisible();
  releaseInitial();
  await expect(page.getByRole("alert")).toContainText("BOM detail unavailable");
  await expect(page.getByText("Request ID: req-bom-detail-503")).toBeVisible();
  recover = true;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: itemBom.item_name })).toBeVisible();
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }]) {
  test(`BOM detail and draft editor fit ${viewport.width}px without document overflow`, async ({ page }) => {
    const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "bom.manage"] };
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
    await mockBomDetail(page, bomDraft, [bomDraft]);
    await page.goto(`/app/master-data/engineering/boms/${bomDraft.bom_id}`);
    await expect(page.getByRole("heading", { name: bomDraft.item_name })).toBeVisible();
    await page.getByRole("button", { name: "Edit lines" }).click();
    await expect(page.getByRole("dialog", { name: "Edit draft lines" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Edit draft lines" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Edit lines" })).toBeFocused();
  });
}
