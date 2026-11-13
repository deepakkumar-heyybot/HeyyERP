import { expect, test } from '@playwright/test';
import { cors, InvitationResponse, mockInvitationAttempt, submitInvitationSignIn, submitPasswordRecovery, totpUri, backupCodes, openAuthenticatorSetup, mockTwoFactorSignIn, submitTwoFactorSignIn, MeFixture, UnitFixture, CountFixtures, BootstrapResponse, BootstrapFixtures, defaultUnits, mockSignInAndMe, grantedProfile, approvalOne, approvalTwo, mockApprovals, approvalDetail, mockApprovalDetail, notificationOne, notificationTwo, mockNotifications, publicApprovalToken, publicApproval, mockPublicApproval, plantAlpha, plantBeta, departmentProduction, departmentFinishing, mockDepartments, storeRaw, storeFinished, mockStores, paymentTermNet30, taxCodeGst18, categoryRubber, categoryNatural, uomKilogram, mockReferenceData, vendorOrbit, mockVendors, mockCustomerDetail, customerAcme, mockCustomers, mockVendorDetail, itemRubber, mockItems, itemFinished, itemPolicy, itemSpec, itemBom, itemChange, itemPart, mockItemDetail, mockBoms, bomDraft, bomPrevious, mockBomDetail, routeMouldV2, routeMouldV1, routeMetal, mockRoutes, indentProfile, indentAlpha, indentBeta, mockIndentRegister, indentDetailFixture, indentPutCors, mockOwnedIndent, rfqProfile, rfqAlpha, rfqBeta, rfqLineFixture, rfqInvitations, rfqDetailFixture, mockRfqRegister, poAlpha, poBeta, poLine, poDetail, mockPoRegister, mockPoEditCatalog, inventoryProfile, grnAlpha, grnBeta, grnDetail, mockGrnRegister, mockGrnCreationSources, stockAlpha, stockBeta, stockTrace, mockStockSources, issueProfile, issuePlan, postedIssue, issueSummary, mockIssueHistory } from './support/fixtures';

test("GRN register shows backend PO/vendor/date/status and filters plant, vendor and date across all pages", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...inventoryProfile, all_plants: true, unit_ids: [] } }, [plantAlpha, plantBeta]);
  const requests: URLSearchParams[] = [];
  await mockGrnRegister(page, [grnAlpha, grnBeta], params => requests.push(params));
  await page.goto("/app/inventory/grns?from=2026-11-02&through=2026-11-02");
  await expect(page.getByRole("heading", { name: "Goods receipts" })).toBeVisible();
  await page.getByLabel("Plant filter").selectOption(grnAlpha.unit_id);
  await page.getByLabel("Vendor filter").selectOption(grnAlpha.vendor_code);
  const table = page.getByRole("table", { name: "Goods receipt results" });
  await expect(table).toContainText(grnAlpha.grn_no);
  await expect(table).toContainText(grnAlpha.po_no);
  await expect(table).toContainText(grnAlpha.vendor_code);
  await expect(table).not.toContainText(grnBeta.grn_no);
  expect(requests.some(params => params.get("limit") === "200")).toBe(true);
  expect(requests.every(params => !params.has("unit_id") && !params.has("vendor_id") && !params.has("from") && !params.has("through"))).toBe(true);
});

test("GRN creation posts exact PO line, store, decimal quantity, and supplier lot", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockGrnRegister(page);
  await mockGrnCreationSources(page);
  let body: Record<string, unknown> | null = null;
  await page.route("**/api/v1/inventory/grns", route => route.request().method() === "POST" ? (body = route.request().postDataJSON(), route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify(grnDetail) })) : route.fallback());
  await page.goto(`/app/inventory/grns/new?po_id=${poAlpha.po_id}`);
  await expect(page.getByLabel("Purchase order")).toHaveValue(poAlpha.po_id);
  await expect(page.locator(".grn-create__context")).toContainText(poAlpha.vendor_code);
  await page.getByLabel("Supplier delivery challan").fill("DC-778");
  await page.getByLabel("Receive line 1").check();
  await page.getByLabel("Store for line 1").selectOption("66666666-6666-6666-6666-666666666666");
  await page.getByLabel("Received quantity for line 1").fill("7.500");
  await page.getByLabel("Supplier lot for line 1").fill("LOT-88");
  await page.getByRole("button", { name: "Post goods receipt" }).click();
  expect(body).toEqual({ po_id: poAlpha.po_id, supplier_dc_no: "DC-778", received_at: null, lines: [{ po_line_id: poLine.po_line_id, store_id: "66666666-6666-6666-6666-666666666666", received_qty: "7.500", supplier_lot_no: "LOT-88" }] });
  await expect(page).toHaveURL(new RegExp(`/app/inventory/grns/${grnAlpha.grn_id}$`));
});

test("GRN posting conflict preserves the challan and selected line", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockGrnRegister(page);
  await mockGrnCreationSources(page);
  await page.route("**/api/v1/inventory/grns", route => route.request().method() === "POST" ? route.fulfill({ status: 409, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "Delivery challan DC-778 was already received on this order" }) }) : route.fallback());
  await page.goto(`/app/inventory/grns/new?po_id=${poAlpha.po_id}`);
  await page.getByLabel("Supplier delivery challan").fill("DC-778");
  await page.getByLabel("Receive line 1").check();
  await page.getByLabel("Store for line 1").selectOption("66666666-6666-6666-6666-666666666666");
  await page.getByLabel("Received quantity for line 1").fill("7.500");
  await page.getByRole("button", { name: "Post goods receipt" }).click();
  await expect(page.getByRole("alert")).toContainText("already received on this order");
  await expect(page.getByLabel("Supplier delivery challan")).toHaveValue("DC-778");
  await expect(page.getByLabel("Receive line 1")).toBeChecked();
});

test("GRN paging and status filter use only supported backend query fields", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...inventoryProfile, all_plants: true, unit_ids: [] } });
  const items = Array.from({ length: 25 }, (_, index) => ({ ...grnAlpha, grn_id: `88888888-1111-1111-1111-${String(index + 1).padStart(12, "0")}`, grn_no: `GRN-${String(index + 1).padStart(4, "0")}`, status: index === 0 ? "DRAFT" : "POSTED" }));
  const requests: URLSearchParams[] = [];
  await mockGrnRegister(page, items, params => requests.push(params));
  await page.goto("/app/inventory/grns");
  await expect(page.getByText("Showing 1–20 of 25 records")).toBeVisible();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByText("Showing 21–25 of 25 records")).toBeVisible();
  await page.getByLabel("GRN status").selectOption("DRAFT");
  await expect(page.getByText("Showing 1–1 of 1 records")).toBeVisible();
  expect(requests.some(params => params.get("offset") === "20" && params.get("limit") === "20")).toBe(true);
  expect(requests.some(params => params.get("status") === "DRAFT" && params.get("offset") === "0")).toBe(true);
  expect(requests.every(params => [...params.keys()].every(key => ["limit", "offset", "status", "po_id"].includes(key)))).toBe(true);
});

test("GRN create carries selected purchase order and vendor context from register", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockGrnRegister(page);
  await mockGrnCreationSources(page);
  await page.goto(`/app/inventory/grns?po_id=${poAlpha.po_id}`);
  await expect(page.getByRole("table", { name: "Goods receipt results" })).toContainText(grnAlpha.grn_no);
  await page.getByRole("button", { name: "New goods receipt" }).click();
  await expect(page).toHaveURL(new RegExp(`po_id=${poAlpha.po_id}`));
  await expect(page.getByLabel("Purchase order")).toHaveValue(poAlpha.po_id);
  await expect(page.locator(".grn-create__context")).toContainText(poAlpha.vendor_code);
});

test("GRN read access does not expose posting without grn.post", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...inventoryProfile, permissions: inventoryProfile.permissions.filter(permission => permission !== "grn.post") } });
  await mockGrnRegister(page);
  await page.goto("/app/inventory/grns");
  await expect(page.getByRole("heading", { name: "Goods receipts" })).toBeVisible();
  await expect(page.getByRole("button", { name: "New goods receipt" })).toHaveCount(0);
  await page.goto("/app/inventory/grns/new");
  await expect(page.getByText("Goods receipt creation unavailable")).toBeVisible();
  await expect(page.getByRole("button", { name: "Post goods receipt" })).toHaveCount(0);
});

test("GRN list shows loading, empty, backend error request ID and retry", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockGrnRegister(page, []);
  let fail = true;
  await page.route("**/api/v1/inventory/grns?**", async route => {
    await new Promise(resolve => setTimeout(resolve, 300));
    return fail
      ? route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-grn-list" }, contentType: "application/json", body: JSON.stringify({ detail: "GRN listing unavailable" }) })
      : route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 20, offset: 0 }) });
  });
  await page.goto("/app/inventory/grns");
  await expect(page.getByRole("status").filter({ hasText: "Loading goods receipts" })).toBeVisible();
  await expect(page.getByText("Request ID: req-grn-list")).toBeVisible();
  fail = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "No goods receipts found" })).toBeVisible();
});

test("GRN create rejects excessive quantity without posting", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockGrnCreationSources(page);
  let posts = 0;
  await page.route("**/api/v1/inventory/grns", route => route.request().method() === "POST" ? (posts += 1, route.fulfill({ status: 500 })) : route.fallback());
  await page.goto(`/app/inventory/grns/new?po_id=${poAlpha.po_id}`);
  await page.getByLabel("Supplier delivery challan").fill("DC-779");
  await page.getByLabel("Receive line 1").check();
  await page.getByLabel("Store for line 1").selectOption("66666666-6666-6666-6666-666666666666");
  await page.getByLabel("Received quantity for line 1").fill("8.126");
  await page.getByRole("button", { name: "Post goods receipt" }).click();
  await expect(page.getByRole("alert")).toContainText("valid store and receipt quantity");
  expect(posts).toBe(0);
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }, { width: 1280, height: 800 }]) {
  test(`GRN register and creation fit ${viewport.width}px with keyboard access`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
    await mockGrnRegister(page);
    await mockGrnCreationSources(page);
    await page.goto("/app/inventory/grns");
    await expect(page.getByRole("heading", { name: "Goods receipts" })).toBeVisible();
    await expect(page.getByLabel("Goods receipt results").last()).toContainText(grnAlpha.grn_no);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.getByRole("button", { name: "New goods receipt" }).click();
    await page.getByLabel("Purchase order").selectOption(poAlpha.po_id);
    await expect(page.locator(".grn-create__context")).toContainText(poAlpha.vendor_code);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.getByLabel("Supplier delivery challan").focus();
    await expect(page.getByLabel("Supplier delivery challan")).toBeFocused();
  });
}

test("GRN detail reconciles exact received, accepted, rejected and inspection state", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockGrnRegister(page);
  const inspected = { ...grnDetail.lines[0], grn_line_id: "88888888-5555-5555-5555-555555555555", lot_id: "88888888-6666-6666-6666-666666666666", lot_no: "GRN-0042/2", received_qty: "2.125", accepted_qty: "1.125", rejected_qty: "1.000", inspected: true, lot_status: "AVAILABLE", expiry_at: "2027-01-01T00:00:00Z" };
  await page.route(`**/api/v1/inventory/grns/${grnAlpha.grn_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...grnDetail, lines: [grnDetail.lines[0], inspected] }) }));
  await page.goto(`/app/inventory/grns/${grnAlpha.grn_id}?status=POSTED`);
  await expect(page.getByRole("heading", { name: grnAlpha.grn_no })).toBeVisible();
  const totals = page.getByRole("region", { name: "Quantity totals" });
  await expect(totals).toContainText("9.625");
  await expect(totals).toContainText("1.125");
  await expect(totals).toContainText("1.000");
  const lines = page.getByRole("list", { name: "Received lines" });
  await expect(lines).toContainText("GRN-0042/1");
  await expect(lines).toContainText("GRN-0042/2");
  await expect(lines).toContainText("2027-01-01");
  await expect(lines).toContainText("Awaiting inspection");
  await expect(lines).toContainText("Inspected");
  await expect(page.getByRole("link", { name: "Back to goods receipts" })).toHaveAttribute("href", /status=POSTED/);
  await expect(page.getByRole("button", { name: "Inspect line" })).toHaveCount(0);
});

for (const scenario of [
  { choice: "Accept all", accepted: "7.500", rejected: "0.000", status: "AVAILABLE" },
  { choice: "Partial acceptance", accepted: "3.250", rejected: "4.250", status: "AVAILABLE" },
  { choice: "Reject all", accepted: "0", rejected: "7.500", status: "BLOCKED" },
]) {
  test(`GRN incoming QA ${scenario.choice.toLowerCase()} posts exact accepted quantity`, async ({ page }) => {
    await mockSignInAndMe(page, { status: 200, body: { ...inventoryProfile, permissions: [...inventoryProfile.permissions, "qa.approve"] } });
    await mockGrnRegister(page);
    let body: Record<string, unknown> | null = null;
    await page.route(`**/api/v1/inventory/grn-lines/${grnDetail.lines[0].grn_line_id}/inspect`, route => {
      body = route.request().postDataJSON();
      return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...grnDetail, lines: [{ ...grnDetail.lines[0], accepted_qty: scenario.accepted, rejected_qty: scenario.rejected, inspected: true, lot_status: scenario.status }] }) });
    });
    await page.goto(`/app/inventory/grns/${grnAlpha.grn_id}`);
    await page.getByRole("button", { name: "Inspect line 1" }).click();
    const dialog = page.getByRole("dialog", { name: "Inspect received line" });
    await dialog.getByLabel("Disposition").selectOption({ label: scenario.choice });
    if (scenario.choice === "Partial acceptance") await dialog.getByLabel("Accepted quantity").fill(scenario.accepted);
    await dialog.getByLabel("Inspection note").fill("Visual check complete");
    await dialog.getByRole("button", { name: "Record inspection" }).click();
    expect(body).toEqual({ accepted_qty: scenario.accepted, note: "Visual check complete" });
    await expect(dialog).toHaveCount(0);
    const values = page.getByRole("list", { name: "Received lines" });
    await expect(values.locator('dt:has-text("Accepted") + dd')).toHaveText(scenario.accepted);
    await expect(values.locator('dt:has-text("Rejected") + dd')).toHaveText(scenario.rejected);
    await expect(page.getByRole("button", { name: "Inspect line 1" })).toHaveCount(0);
  });
}

test("GRN incoming QA rejects invalid partial quantity without writing", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...inventoryProfile, permissions: [...inventoryProfile.permissions, "qa.approve"] } });
  await mockGrnRegister(page);
  let writes = 0;
  await page.route(`**/api/v1/inventory/grn-lines/${grnDetail.lines[0].grn_line_id}/inspect`, route => { writes += 1; return route.fulfill({ status: 500 }); });
  await page.goto(`/app/inventory/grns/${grnAlpha.grn_id}`);
  await page.getByRole("button", { name: "Inspect line 1" }).click();
  const dialog = page.getByRole("dialog", { name: "Inspect received line" });
  await dialog.getByLabel("Disposition").selectOption({ label: "Partial acceptance" });
  await dialog.getByLabel("Accepted quantity").fill("7.501");
  await dialog.getByRole("button", { name: "Record inspection" }).click();
  await expect(dialog.getByRole("alert")).toContainText("less than the received quantity");
  await expect(dialog.getByLabel("Accepted quantity")).toHaveValue("7.501");
  expect(writes).toBe(0);
});

test("GRN incoming QA preserves the decision and request ID on server error", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...inventoryProfile, permissions: [...inventoryProfile.permissions, "qa.approve"] } });
  await mockGrnRegister(page);
  await page.route(`**/api/v1/inventory/grn-lines/${grnDetail.lines[0].grn_line_id}/inspect`, route => route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-grn-inspect" }, contentType: "application/json", body: JSON.stringify({ detail: "Inspection service unavailable" }) }));
  await page.goto(`/app/inventory/grns/${grnAlpha.grn_id}`);
  await page.getByRole("button", { name: "Inspect line 1" }).click();
  const dialog = page.getByRole("dialog", { name: "Inspect received line" });
  await dialog.getByLabel("Disposition").selectOption({ label: "Reject all" });
  await dialog.getByLabel("Inspection note").fill("Damaged lot");
  await dialog.getByRole("button", { name: "Record inspection" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Inspection service unavailable");
  await expect(dialog.getByText("Request ID: req-grn-inspect")).toBeVisible();
  await expect(dialog.getByLabel("Inspection note")).toHaveValue("Damaged lot");
  await expect(dialog.getByLabel("Disposition")).toHaveValue("reject");
});

test("GRN incoming QA explains stale inspection conflicts without discarding inputs", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...inventoryProfile, permissions: [...inventoryProfile.permissions, "qa.approve"] } });
  await mockGrnRegister(page);
  await page.route(`**/api/v1/inventory/grn-lines/${grnDetail.lines[0].grn_line_id}/inspect`, route => route.fulfill({ status: 409, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "This line has already been inspected" }) }));
  await page.goto(`/app/inventory/grns/${grnAlpha.grn_id}`);
  await page.getByRole("button", { name: "Inspect line 1" }).click();
  const dialog = page.getByRole("dialog", { name: "Inspect received line" });
  await dialog.getByLabel("Disposition").selectOption({ label: "Partial acceptance" });
  await dialog.getByLabel("Accepted quantity").fill("3.250");
  await dialog.getByRole("button", { name: "Record inspection" }).click();
  await expect(dialog.getByRole("alert")).toContainText("already been inspected");
  await expect(dialog.getByLabel("Accepted quantity")).toHaveValue("3.250");
});

test("GRN detail loading, empty and backend errors remain distinct", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockGrnRegister(page);
  let error = true;
  await page.route(`**/api/v1/inventory/grns/${grnAlpha.grn_id}`, async route => {
    await new Promise(resolve => setTimeout(resolve, 250));
    return error ? route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-grn-detail" }, contentType: "application/json", body: JSON.stringify({ detail: "GRN unavailable" }) }) : route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...grnDetail, lines: [] }) });
  });
  await page.goto(`/app/inventory/grns/${grnAlpha.grn_id}`);
  await expect(page.getByRole("status").filter({ hasText: "Loading goods receipt" })).toBeVisible();
  await expect(page.getByText("Request ID: req-grn-detail")).toBeVisible();
  error = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByText("No received lines")).toBeVisible();
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }]) {
  test(`GRN detail and QA dialog fit ${viewport.width}px with keyboard focus`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: { ...inventoryProfile, permissions: [...inventoryProfile.permissions, "qa.approve"] } });
    await mockGrnRegister(page);
    await page.goto(`/app/inventory/grns/${grnAlpha.grn_id}`);
    await expect(page.getByRole("heading", { name: "Received lines" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.getByRole("button", { name: "Inspect line 1" }).click();
    const dialog = page.getByRole("dialog", { name: "Inspect received line" });
    await expect(dialog.getByRole("button", { name: "Close inspection dialog" })).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(dialog.getByRole("button", { name: "Record inspection" })).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Inspect line 1" })).toBeFocused();
  });
}

test("Stock explorer maps plant, store, item, status and in-stock filters to backend and keeps decimal strings", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  const requests: URLSearchParams[] = [];
  await mockStockSources(page, [stockAlpha, stockBeta], params => requests.push(params));
  await page.goto(`/app/inventory/stock?plant=${stockAlpha.unit_id}&store=${stockAlpha.store_id}&item=${stockAlpha.item_id}&status=AVAILABLE&in_stock=false`);
  await expect(page.getByRole("heading", { name: "Stock explorer" })).toBeVisible();
  const table = page.getByRole("table", { name: "Stock balances" });
  await expect(table).toContainText("0.125");
  await expect(table).toContainText("0.025");
  await expect(table).toContainText("0.100");
  await expect(table).toContainText(stockAlpha.lot_no);
  expect(requests.some(params => params.get("unit_id") === stockAlpha.unit_id && params.get("store_id") === stockAlpha.store_id && params.get("item_id") === stockAlpha.item_id && params.get("lot_status") === "AVAILABLE" && params.get("in_stock_only") === "false")).toBe(true);
  expect(requests.every(params => [...params.keys()].every(key => ["unit_id", "store_id", "item_id", "lot_status", "in_stock_only", "limit", "offset"].includes(key)))).toBe(true);
});

test("Stock desktop balance columns remain readable without digit wrapping", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockStockSources(page, [stockAlpha]);
  await page.goto("/app/inventory/stock");
  const table = page.getByRole("table", { name: "Stock balances" });
  await expect(table).toContainText(stockAlpha.lot_no);
  for (const heading of ["On hand", "Reserved", "Available"]) {
    const box = await table.getByRole("columnheader", { name: heading }).boundingBox();
    expect(box?.width).toBeGreaterThanOrEqual(105);
  }
});

test("Stock explorer pages server results and searches lots across all matching pages", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...inventoryProfile, all_plants: true, unit_ids: [] } });
  const items = Array.from({ length: 25 }, (_, index) => ({ ...stockAlpha, lot_id: `77777777-1111-1111-1111-${String(index + 1).padStart(12, "0")}`, lot_no: `LOT-${String(index + 1).padStart(4, "0")}` }));
  const requests: URLSearchParams[] = [];
  await mockStockSources(page, items, params => requests.push(params));
  await page.goto("/app/inventory/stock");
  await expect(page.getByText("Showing 1–20 of 25 records")).toBeVisible();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByText("Showing 21–25 of 25 records")).toBeVisible();
  await page.getByLabel("Lot filter").fill("LOT-0025");
  await expect(page).toHaveURL(/lot=LOT-0025/);
  await expect(page.getByText("Showing 1–1 of 1 records")).toBeVisible();
  await expect(page.getByRole("table", { name: "Stock balances" })).toContainText("LOT-0025");
  await expect(page.getByRole("table", { name: "Stock balances" })).not.toContainText("LOT-0001");
  expect(requests.some(params => params.get("limit") === "20" && params.get("offset") === "20")).toBe(true);
  expect(requests.some(params => params.get("limit") === "200")).toBe(true);
  expect(requests.every(params => !params.has("lot"))).toBe(true);
});

test("Stock balance and lot-status controls reveal zero-balance blocked lots", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockStockSources(page);
  await page.goto("/app/inventory/stock");
  const table = page.getByRole("table", { name: "Stock balances" });
  await expect(table).toContainText(stockAlpha.lot_no);
  await expect(table).not.toContainText(stockBeta.lot_no);
  await page.getByLabel("Balance filter").selectOption("false");
  await expect(table).toContainText(stockBeta.lot_no);
  await page.getByLabel("Lot status").selectOption("BLOCKED");
  await expect(table).not.toContainText(stockAlpha.lot_no);
  await expect(table).toContainText("0.000 KG");
});

test("Stock explorer requires inventory module without leaking stock reads", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...inventoryProfile, modules: ["home", "procurement"] } });
  let stockRequests = 0;
  await page.route("**/api/v1/inventory/stock?**", route => { stockRequests += 1; return route.abort(); });
  await page.goto("/app/inventory/stock");
  await expect(page.getByRole("heading", { name: "Access denied" })).toBeVisible();
  expect(stockRequests).toBe(0);
});

test("Stock lot drill preserves register context and shows backend balance", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockStockSources(page);
  await page.goto(`/app/inventory/stock?status=AVAILABLE&plant=${stockAlpha.unit_id}`);
  await page.getByRole("link", { name: `Open lot ${stockAlpha.lot_no}` }).click();
  await expect(page).toHaveURL(new RegExp(`/app/inventory/lots/${stockAlpha.lot_id}`));
  await expect(page.getByRole("heading", { name: stockAlpha.lot_no })).toBeVisible();
  await expect(page.getByRole("definition").filter({ hasText: "0.100 KG" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to stock" })).toHaveAttribute("href", /status=AVAILABLE/);
});

test("Stock explorer exposes loading, empty and request-ID error recovery", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockStockSources(page, []);
  let fail = true;
  await page.route("**/api/v1/inventory/stock?**", async route => {
    await new Promise(resolve => setTimeout(resolve, 250));
    return fail ? route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-stock" }, contentType: "application/json", body: JSON.stringify({ detail: "Stock unavailable" }) }) : route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 20, offset: 0 }) });
  });
  await page.goto("/app/inventory/stock");
  await expect(page.getByRole("status").filter({ hasText: "Loading stock" })).toBeVisible();
  await expect(page.getByText("Request ID: req-stock")).toBeVisible();
  fail = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "No stock balances found" })).toBeVisible();
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }, { width: 1280, height: 800 }]) {
  test(`Stock explorer and lot detail fit ${viewport.width}px and remain keyboard reachable`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
    await mockStockSources(page);
    await page.goto("/app/inventory/stock");
    await expect(page.getByRole("heading", { name: "Stock explorer" })).toBeVisible();
    await expect(page.getByLabel("Stock balances").last()).toContainText(stockAlpha.lot_no);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.getByRole("link", { name: `Open lot ${stockAlpha.lot_no}` }).last().focus();
    await expect(page.getByRole("link", { name: `Open lot ${stockAlpha.lot_no}` }).last()).toBeFocused();
  });
}

test("Lot detail loads origin, chronological ledger and downstream transfer links", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockStockSources(page);
  await page.goto(`/app/inventory/lots/${stockAlpha.lot_id}?status=AVAILABLE`);
  await expect(page.getByRole("heading", { name: "Origin" })).toBeVisible();
  await expect(page.getByText("Metalco Components")).toBeVisible();
  await expect(page.getByText("DC-778")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Movement history" })).toBeVisible();
  await expect(page.getByText("ISS-0007")).toBeVisible();
  await expect(page.getByText("-0.125")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Transfer trace" })).toBeVisible();
  await expect(page.getByText("TR-0008")).toBeVisible();
  await expect(page.getByRole("link", { name: /FG-01/ })).toHaveAttribute("href", /\/app\/inventory\/lots\/77777777-3333/);
  await expect(page.getByRole("link", { name: "Back to stock" })).toHaveAttribute("href", /status=AVAILABLE/);
});

test("Lot detail explains empty origin, movements and links without fabricating history", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockStockSources(page);
  await page.route(`**/api/v1/inventory/lots/${stockAlpha.lot_id}/trace`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...stockTrace, origin: null, moves: [], links: [] }) }));
  await page.goto(`/app/inventory/lots/${stockAlpha.lot_id}`);
  await expect(page.getByText("No supplier receipt origin is recorded for this lot.")).toBeVisible();
  await expect(page.getByText("No stock movements are recorded for this lot.")).toBeVisible();
  await expect(page.getByText("No received transfer links are recorded for this lot.")).toBeVisible();
});

test("Lot quarantine controls require qa.approve and supported status", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockStockSources(page);
  let posts = 0;
  await page.route(`**/api/v1/inventory/lots/${stockAlpha.lot_id}/quarantine`, route => { posts += 1; return route.abort(); });
  await page.goto(`/app/inventory/lots/${stockAlpha.lot_id}`);
  await expect(page.getByRole("heading", { name: stockAlpha.lot_no })).toBeVisible();
  await expect(page.getByRole("button", { name: "Quarantine lot" })).toHaveCount(0);
  expect(posts).toBe(0);
});

test("Blocked lots cannot expose quarantine actions even to QA approvers", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...inventoryProfile, permissions: [...inventoryProfile.permissions, "qa.approve"] } });
  await mockStockSources(page);
  await page.route(`**/api/v1/inventory/lots/${stockAlpha.lot_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...stockAlpha, lot_status: "BLOCKED" }) }));
  await page.goto(`/app/inventory/lots/${stockAlpha.lot_id}`);
  await expect(page.getByRole("heading", { name: stockAlpha.lot_no })).toBeVisible();
  await expect(page.getByRole("button", { name: "Quarantine lot" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Release quarantine" })).toHaveCount(0);
});

test("Lot quarantine requires reason and updates status only after confirmed response", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...inventoryProfile, permissions: [...inventoryProfile.permissions, "qa.approve"] } });
  await mockStockSources(page);
  const sent: unknown[] = [];
  await page.route(`**/api/v1/inventory/lots/${stockAlpha.lot_id}/quarantine`, async route => {
    sent.push(route.request().postDataJSON());
    await new Promise(resolve => setTimeout(resolve, 300));
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...stockAlpha, lot_status: "QUARANTINE" }) });
  });
  await page.goto(`/app/inventory/lots/${stockAlpha.lot_id}`);
  await page.getByRole("button", { name: "Quarantine lot" }).click();
  const dialog = page.getByRole("dialog", { name: "Quarantine lot" });
  await expect(dialog.getByRole("button", { name: "Close lot action" })).toBeFocused();
  await dialog.getByLabel("Reason").fill("ab");
  await dialog.getByRole("button", { name: "Confirm quarantine" }).click();
  await expect(dialog.getByText(/at least 3/)).toBeVisible();
  expect(sent).toHaveLength(0);
  await dialog.getByLabel("Reason").fill("Suspected contamination");
  await dialog.getByRole("button", { name: "Confirm quarantine" }).click();
  await expect(dialog.getByRole("button", { name: "Recording…" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Release quarantine" })).toHaveCount(0);
  await expect(dialog).toHaveCount(0);
  expect(sent).toEqual([{ reason: "Suspected contamination" }]);
  await expect(page.getByRole("button", { name: "Release quarantine" })).toBeVisible();
});

test("Lot release uses the unquarantine contract and keeps reason exact", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...inventoryProfile, permissions: [...inventoryProfile.permissions, "qa.approve"] } });
  await mockStockSources(page);
  await page.route(`**/api/v1/inventory/lots/${stockAlpha.lot_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...stockAlpha, lot_status: "QUARANTINE" }) }));
  await page.route(`**/api/v1/inventory/lots/${stockAlpha.lot_id}/trace`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...stockTrace, status: "QUARANTINE" }) }));
  let body: unknown;
  await page.route(`**/api/v1/inventory/lots/${stockAlpha.lot_id}/unquarantine`, route => { body = route.request().postDataJSON(); return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(stockAlpha) }); });
  await page.goto(`/app/inventory/lots/${stockAlpha.lot_id}`);
  await page.getByRole("button", { name: "Release quarantine" }).click();
  await page.getByRole("dialog", { name: "Release quarantine" }).getByLabel("Reason").fill("QA review complete");
  await page.getByRole("button", { name: "Confirm release" }).click();
  await expect(page.getByRole("button", { name: "Quarantine lot" })).toBeVisible();
  expect(body).toEqual({ reason: "QA review complete" });
});

test("Lot action conflicts preserve reason and surface backend detail", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...inventoryProfile, permissions: [...inventoryProfile.permissions, "qa.approve"] } });
  await mockStockSources(page);
  await page.route(`**/api/v1/inventory/lots/${stockAlpha.lot_id}/quarantine`, route => route.fulfill({ status: 409, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "The lot is BLOCKED, not AVAILABLE" }) }));
  await page.goto(`/app/inventory/lots/${stockAlpha.lot_id}`);
  await page.getByRole("button", { name: "Quarantine lot" }).click();
  const dialog = page.getByRole("dialog", { name: "Quarantine lot" });
  await dialog.getByLabel("Reason").fill("Suspected contamination");
  await dialog.getByRole("button", { name: "Confirm quarantine" }).click();
  await expect(dialog.getByText("The lot is BLOCKED, not AVAILABLE")).toBeVisible();
  await expect(dialog.getByLabel("Reason")).toHaveValue("Suspected contamination");
});

test("Lot action server failure preserves reason and reports request ID", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...inventoryProfile, permissions: [...inventoryProfile.permissions, "qa.approve"] } });
  await mockStockSources(page);
  await page.route(`**/api/v1/inventory/lots/${stockAlpha.lot_id}/quarantine`, route => route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-lot-action" }, contentType: "application/json", body: JSON.stringify({ detail: "Database unavailable" }) }));
  await page.goto(`/app/inventory/lots/${stockAlpha.lot_id}`);
  await page.getByRole("button", { name: "Quarantine lot" }).click();
  const dialog = page.getByRole("dialog", { name: "Quarantine lot" });
  await dialog.getByLabel("Reason").fill("Suspected contamination");
  await dialog.getByRole("button", { name: "Confirm quarantine" }).click();
  await expect(dialog.getByText("Database unavailable")).toBeVisible();
  await expect(dialog.getByText("Request ID: req-lot-action")).toBeVisible();
  await expect(dialog.getByLabel("Reason")).toHaveValue("Suspected contamination");
});

test("Lot trace failure shows request ID and retries independently of balance", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockStockSources(page);
  let fail = true;
  await page.route(`**/api/v1/inventory/lots/${stockAlpha.lot_id}/trace`, route => fail ? route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-lot-trace" }, contentType: "application/json", body: JSON.stringify({ detail: "Trace unavailable" }) }) : route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(stockTrace) }));
  await page.goto(`/app/inventory/lots/${stockAlpha.lot_id}`);
  await expect(page.getByText("Request ID: req-lot-trace")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Quantity balance" })).toBeVisible();
  fail = false;
  await page.getByRole("button", { name: "Retry trace" }).click();
  await expect(page.getByRole("heading", { name: "Movement history" })).toBeVisible();
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }, { width: 1280, height: 800 }]) {
  test(`Lot trace and action dialog fit ${viewport.width}px with keyboard recovery`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: { ...inventoryProfile, permissions: [...inventoryProfile.permissions, "qa.approve"] } });
    await mockStockSources(page);
    await page.goto(`/app/inventory/lots/${stockAlpha.lot_id}`);
    await expect(page.getByRole("heading", { name: "Movement history" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.getByRole("button", { name: "Quarantine lot" }).click();
    const dialog = page.getByRole("dialog", { name: "Quarantine lot" });
    await expect(dialog.getByRole("button", { name: "Close lot action" })).toBeFocused();
    if (viewport.width === 360) expect((await dialog.boundingBox())?.height).toBeLessThan(viewport.height / 2);
    await page.keyboard.press("Shift+Tab");
    await expect(dialog.getByRole("button", { name: "Confirm quarantine" })).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Quarantine lot" })).toBeFocused();
  });
}

test("Issue planner requires inv.issue and hides navigation for readers", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...inventoryProfile, permissions: inventoryProfile.permissions.filter(permission => permission !== "inv.issue") } });
  await mockStockSources(page);
  let planRequests = 0;
  await page.route("**/api/v1/inventory/issues/plan", route => { planRequests += 1; return route.abort(); });
  await page.goto("/app/inventory/grns");
  await expect(page.getByRole("link", { name: "Issue planner" })).toHaveCount(0);
  await page.goto("/app/inventory/issues/new");
  await expect(page.getByRole("heading", { name: "Issue planning unavailable" })).toBeVisible();
  expect(planRequests).toBe(0);
});

test("Issue preview sends exact store/item/decimal inputs and displays backend rule, picks and shortfall", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: issueProfile }, defaultUnits, {}, { settings: [{ status: 200, body: [{ key: "issueRule", value: "FIFO" }] }] });
  await mockStockSources(page);
  const sent: unknown[] = [];
  await page.route("**/api/v1/inventory/issues/plan", route => { sent.push(route.request().postDataJSON()); return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(issuePlan) }); });
  let posts = 0;
  await page.route("**/api/v1/inventory/issues", route => { posts += 1; return route.abort(); });
  await page.goto("/app/inventory/grns");
  await page.getByRole("link", { name: "Issue planner" }).click();
  await page.getByLabel("Store").selectOption(stockAlpha.store_id);
  await page.getByLabel("Item for line 1").selectOption(stockAlpha.item_id);
  await page.getByLabel("Quantity for line 1").fill("0.200");
  await page.getByLabel("Purpose").fill("Production supply");
  await page.getByLabel("Reference (optional)").fill("WO-0042");
  await page.getByRole("button", { name: "Preview issue" }).click();
  await expect(page.getByRole("heading", { name: "Issue preview" })).toBeVisible();
  await expect(page.getByText("FEFO", { exact: true })).toBeVisible();
  await expect(page.getByText("LOT-EXPIRED")).toBeVisible();
  await expect(page.getByText("Expired", { exact: true })).toBeVisible();
  await expect(page.getByText("0.100", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/Shortfall/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirm issue" })).toBeDisabled();
  expect(sent).toEqual([{ store_id: stockAlpha.store_id, lines: [{ item_id: stockAlpha.item_id, quantity: "0.200" }] }]);
  expect(posts).toBe(0);
});

test("Issue planner confirms only a successful preview and renders returned issue and cost", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: issueProfile });
  await mockStockSources(page);
  await page.route("**/api/v1/inventory/issues/plan", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...issuePlan, can_issue: true, lines: [{ ...issuePlan.lines[0], picks: [{ ...issuePlan.lines[0].picks[0], quantity: "0.200" }], shortfall: "0.000", blocked: [] }] }) }));
  const sent: unknown[] = [];
  await page.route("**/api/v1/inventory/issues", async route => { sent.push(route.request().postDataJSON()); await new Promise(resolve => setTimeout(resolve, 200)); return route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify(postedIssue) }); });
  await page.goto("/app/inventory/issues/new");
  await page.getByLabel("Store").selectOption(stockAlpha.store_id);
  await page.getByLabel("Item for line 1").selectOption(stockAlpha.item_id);
  await page.getByLabel("Quantity for line 1").fill("0.200");
  await page.getByLabel("Purpose").fill("Production supply");
  await page.getByLabel("Reference (optional)").fill("WO-0042");
  await expect(page.getByRole("button", { name: "Confirm issue" })).toBeDisabled();
  await page.getByRole("button", { name: "Preview issue" }).click();
  await expect(page.getByRole("button", { name: "Confirm issue" })).toBeEnabled();
  await page.getByRole("button", { name: "Confirm issue" }).click();
  await expect(page.getByRole("button", { name: "Posting…" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Issue posted" })).toBeVisible();
  await expect(page.getByText("ISS-0042")).toBeVisible();
  await expect(page.getByText("14.375", { exact: true }).first()).toBeVisible();
  expect(sent).toEqual([{ store_id: stockAlpha.store_id, lines: [{ item_id: stockAlpha.item_id, quantity: "0.200" }], purpose: "Production supply", reference: "WO-0042", use_reservation: null }]);
});

test("Editing issue inputs invalidates prior preview before another post", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: issueProfile });
  await mockStockSources(page);
  await page.route("**/api/v1/inventory/issues/plan", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...issuePlan, can_issue: true, lines: [{ ...issuePlan.lines[0], shortfall: "0.000" }] }) }));
  await page.goto("/app/inventory/issues/new");
  await page.getByLabel("Store").selectOption(stockAlpha.store_id);
  await page.getByLabel("Item for line 1").selectOption(stockAlpha.item_id);
  await page.getByLabel("Quantity for line 1").fill("0.200");
  await page.getByLabel("Purpose").fill("Production supply");
  await page.getByRole("button", { name: "Preview issue" }).click();
  await expect(page.getByRole("button", { name: "Confirm issue" })).toBeEnabled();
  await page.getByLabel("Quantity for line 1").fill("0.300");
  await expect(page.getByRole("button", { name: "Confirm issue" })).toBeDisabled();
  await expect(page.getByRole("heading", { name: "Issue preview" })).toHaveCount(0);
});

test("Issue planner rejects invalid or duplicate lines before preview", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: issueProfile });
  await mockStockSources(page);
  let requests = 0;
  await page.route("**/api/v1/inventory/issues/plan", route => { requests += 1; return route.abort(); });
  await page.goto("/app/inventory/issues/new");
  await page.getByLabel("Store").selectOption(stockAlpha.store_id);
  await page.getByLabel("Item for line 1").selectOption(stockAlpha.item_id);
  await page.getByLabel("Quantity for line 1").fill("0.0001");
  await page.getByRole("button", { name: "Preview issue" }).click();
  await expect(page.getByText(/valid quantity/)).toBeVisible();
  await page.getByLabel("Quantity for line 1").fill("0.200");
  await page.getByRole("button", { name: "Add item" }).click();
  await page.getByLabel("Item for line 2").selectOption(stockAlpha.item_id);
  await page.getByLabel("Quantity for line 2").fill("0.100");
  await page.getByRole("button", { name: "Preview issue" }).click();
  await expect(page.getByText(/distinct items/)).toBeVisible();
  expect(requests).toBe(0);
});

test("Issue preview failure reports request ID and preserves entered fields", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: issueProfile });
  await mockStockSources(page);
  await page.route("**/api/v1/inventory/issues/plan", route => route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-issue-plan" }, contentType: "application/json", body: JSON.stringify({ detail: "Planning unavailable" }) }));
  await page.goto("/app/inventory/issues/new");
  await page.getByLabel("Store").selectOption(stockAlpha.store_id);
  await page.getByLabel("Item for line 1").selectOption(stockAlpha.item_id);
  await page.getByLabel("Quantity for line 1").fill("0.200");
  await page.getByLabel("Purpose").fill("Production supply");
  await page.getByRole("button", { name: "Preview issue" }).click();
  await expect(page.getByText("Request ID: req-issue-plan")).toBeVisible();
  await expect(page.getByLabel("Quantity for line 1")).toHaveValue("0.200");
  await expect(page.getByRole("button", { name: "Confirm issue" })).toBeDisabled();
});

test("Issue planner distinguishes loading and empty active store/item sources", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: issueProfile });
  await mockStockSources(page);
  await page.route("**/api/v1/org/stores?**", async route => { await new Promise(resolve => setTimeout(resolve, 200)); return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }); });
  await page.route("**/api/v1/product/items?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.goto("/app/inventory/issues/new");
  await expect(page.getByRole("status").filter({ hasText: "Loading active stores" })).toBeVisible();
  await expect(page.getByText("No active stores are available in the selected plant.")).toBeVisible();
  await expect(page.getByText("No active items are available for an issue.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Preview issue" })).toBeDisabled();
});

test("Issue plan validation from backend preserves input and does not permit posting", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: issueProfile });
  await mockStockSources(page);
  await page.route("**/api/v1/inventory/issues/plan", route => route.fulfill({ status: 422, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "An item does not exist" }) }));
  await page.goto("/app/inventory/issues/new");
  await page.getByLabel("Store").selectOption(stockAlpha.store_id);
  await page.getByLabel("Item for line 1").selectOption(stockAlpha.item_id);
  await page.getByLabel("Quantity for line 1").fill("0.200");
  await page.getByLabel("Purpose").fill("Production supply");
  await page.getByRole("button", { name: "Preview issue" }).click();
  await expect(page.getByText("An item does not exist")).toBeVisible();
  await expect(page.getByLabel("Quantity for line 1")).toHaveValue("0.200");
  await expect(page.getByRole("button", { name: "Confirm issue" })).toBeDisabled();
});

test("Posting stock conflict preserves form and allows fresh preview", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: issueProfile });
  await mockStockSources(page);
  let previews = 0;
  await page.route("**/api/v1/inventory/issues/plan", route => { previews += 1; return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...issuePlan, can_issue: true, lines: [{ ...issuePlan.lines[0], shortfall: "0.000" }] }) }); });
  await page.route("**/api/v1/inventory/issues", route => route.fulfill({ status: 409, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "Not enough stock of RM-01: 0.100 can be issued, 0.200 needed" }) }));
  await page.goto("/app/inventory/issues/new");
  await page.getByLabel("Store").selectOption(stockAlpha.store_id);
  await page.getByLabel("Item for line 1").selectOption(stockAlpha.item_id);
  await page.getByLabel("Quantity for line 1").fill("0.200");
  await page.getByLabel("Purpose").fill("Production supply");
  await page.getByRole("button", { name: "Preview issue" }).click();
  await page.getByRole("button", { name: "Confirm issue" }).click();
  await expect(page.getByText("Not enough stock of RM-01: 0.100 can be issued, 0.200 needed")).toBeVisible();
  await expect(page.getByLabel("Quantity for line 1")).toHaveValue("0.200");
  await expect(page.getByRole("button", { name: "Confirm issue" })).toBeDisabled();
  await page.getByRole("button", { name: "Preview issue" }).click();
  expect(previews).toBe(2);
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }, { width: 1280, height: 800 }]) {
  test(`Issue planner form and pick preview fit ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: issueProfile });
    await mockStockSources(page);
    await page.route("**/api/v1/inventory/issues/plan", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(issuePlan) }));
    await page.goto("/app/inventory/issues/new");
    await expect(page.getByRole("heading", { name: "Issue planner" })).toBeVisible();
    if (viewport.width === 360) {
      const heading = await page.getByRole("heading", { name: "Issue planner" }).boundingBox();
      const description = await page.getByText("Preview backend-selected lots before posting. Stock and issue rules can change before confirmation.").boundingBox();
      expect(description?.y).toBeGreaterThanOrEqual((heading?.y ?? 0) + (heading?.height ?? 0));
    }
    await page.getByLabel("Store").selectOption(stockAlpha.store_id);
    await page.getByLabel("Item for line 1").selectOption(stockAlpha.item_id);
    await page.getByLabel("Quantity for line 1").fill("0.200");
    await page.getByLabel("Purpose").fill("Production supply");
    await page.getByRole("button", { name: "Preview issue" }).click();
    await expect(page.getByText("LOT-EXPIRED")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.getByLabel("Quantity for line 1").focus();
    await expect(page.getByLabel("Quantity for line 1")).toBeFocused();
  });
}
