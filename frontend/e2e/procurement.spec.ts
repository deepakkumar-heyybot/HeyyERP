import { expect, test } from '@playwright/test';
import { cors, InvitationResponse, mockInvitationAttempt, submitInvitationSignIn, submitPasswordRecovery, totpUri, backupCodes, openAuthenticatorSetup, mockTwoFactorSignIn, submitTwoFactorSignIn, MeFixture, UnitFixture, CountFixtures, BootstrapResponse, BootstrapFixtures, defaultUnits, mockSignInAndMe, grantedProfile, approvalOne, approvalTwo, mockApprovals, approvalDetail, mockApprovalDetail, notificationOne, notificationTwo, mockNotifications, publicApprovalToken, publicApproval, mockPublicApproval, plantAlpha, plantBeta, departmentProduction, departmentFinishing, mockDepartments, storeRaw, storeFinished, mockStores, paymentTermNet30, taxCodeGst18, categoryRubber, categoryNatural, uomKilogram, mockReferenceData, vendorOrbit, mockVendors, mockCustomerDetail, customerAcme, mockCustomers, mockVendorDetail, itemRubber, mockItems, itemFinished, itemPolicy, itemSpec, itemBom, itemChange, itemPart, mockItemDetail, mockBoms, bomDraft, bomPrevious, mockBomDetail, routeMouldV2, routeMouldV1, routeMetal, mockRoutes, indentProfile, indentAlpha, indentBeta, mockIndentRegister, indentDetailFixture, indentPutCors, mockOwnedIndent, rfqProfile, rfqAlpha, rfqBeta, rfqLineFixture, rfqInvitations, rfqDetailFixture, mockRfqRegister, poAlpha, poBeta, poLine, poDetail, mockPoRegister, mockPoEditCatalog, inventoryProfile, grnAlpha, grnBeta, grnDetail, mockGrnRegister, mockGrnCreationSources, stockAlpha, stockBeta, stockTrace, mockStockSources, issueProfile, issuePlan, postedIssue, issueSummary, mockIssueHistory } from './support/fixtures';

test("indent register shows the real summary fields, status filtering and server paging", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  const records = Array.from({ length: 21 }, (_, index) => ({ ...indentAlpha, indent_id: `aaaaaaaa-1111-1111-1111-${String(index).padStart(12, "0")}`, indent_no: `IND-${String(index + 1).padStart(3, "0")}` }));
  records.push(indentBeta);
  const queries: URLSearchParams[] = [];
  await mockIndentRegister(page, records, params => queries.push(new URLSearchParams(params)));
  await page.goto("/app/procurement/indents");
  const table = page.getByRole("table", { name: "Indent results" });
  await expect(table).toContainText("IND-001");
  await expect(table).toContainText("VR-01");
  await expect(table).toContainText("BOM");
  await expect(table).toContainText("2026-12-10");
  await expect(page.getByText("Showing 1–20 of 22 records")).toBeVisible();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(table).toContainText("IND-021");
  await expect(page.getByText("Showing 21–22 of 22 records")).toBeVisible();
  await page.getByLabel("Indent status").selectOption("APPROVED");
  await expect(table).toContainText("IND-002");
  await expect(table).not.toContainText("IND-001");
  await expect(page.getByText("Showing 1–1 of 1 records")).toBeVisible();
  expect(queries.some(params => params.get("offset") === "20" && params.get("limit") === "20")).toBe(true);
  expect(queries.some(params => params.get("status") === "APPROVED" && params.get("offset") === "0")).toBe(true);
});

test("indent department, requester and needed-by filters examine all backend pages without unsupported query keys", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  const others = Array.from({ length: 205 }, (_, index) => ({ ...indentBeta, indent_id: `bbbbbbbb-1111-1111-1111-${String(index).padStart(12, "0")}`, indent_no: `IND-X${index}` }));
  const queries: URLSearchParams[] = [];
  await mockIndentRegister(page, [...others, indentAlpha], params => queries.push(new URLSearchParams(params)));
  await page.route("**/api/v1/org/departments?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ department_id: indentAlpha.department_id, code: "PROD", name: "Production", unit_id: defaultUnits[0].unit_id, parent_department_id: null, head_role_code: null, active: true }], total: 1, limit: 200, offset: 0 }) }));
  await page.goto("/app/procurement/indents");
  await page.getByLabel("Department").selectOption(indentAlpha.department_id);
  await page.getByLabel("Requester name").fill("asha");
  await page.getByLabel("Needed by from").fill("2026-12-01");
  await page.getByLabel("Needed by through").fill("2026-12-31");
  await expect(page.getByRole("table", { name: "Indent results" })).toContainText("IND-001");
  await expect(page.getByText("Showing 1–1 of 1 records")).toBeVisible();
  expect(queries.some(params => params.get("limit") === "200" && params.get("offset") === "200")).toBe(true);
  expect(queries.every(params => !params.has("department_id") && !params.has("requester") && !params.has("from") && !params.has("through"))).toBe(true);
});

test("global departments remain available when filtering a selected plant's indents", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  const globalId = "aaaaaaaa-4444-4444-4444-444444444444";
  await mockIndentRegister(page, [{ ...indentAlpha, department_id: globalId }]);
  let requestedUnit: string | null = null;
  await page.route("**/api/v1/org/departments?**", route => {
    requestedUnit = new URL(route.request().url()).searchParams.get("unit_id");
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ department_id: globalId, code: "CORP", name: "Corporate", unit_id: null, parent_department_id: null, head_role_code: null, active: true }], total: 1, limit: 200, offset: 0 }) });
  });
  await page.goto("/app/procurement/indents");
  await page.getByLabel("Department").selectOption(globalId);
  await expect(page.getByRole("table", { name: "Indent results" })).toContainText("IND-001");
  expect(requestedUnit).toBeNull();
});

test("indent readers without raise permission can browse but cannot create", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...indentProfile, permissions: [] } });
  await mockIndentRegister(page, [indentAlpha]);
  await page.goto("/app/procurement/indents");
  await expect(page.getByRole("table", { name: "Indent results" })).toContainText("IND-001");
  await expect(page.getByRole("button", { name: "New indent" })).toHaveCount(0);
  await page.goto("/app/procurement/indents/new");
  await expect(page.getByRole("heading", { name: "New indent" })).toHaveCount(0);
});

test("a requester without the procurement module can open their own indent workspace", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile });
  await mockIndentRegister(page, []);
  await expect(page.getByRole("navigation", { name: "Primary navigation" }).getByRole("link", { name: "Procurement" })).toBeVisible();
  await page.goto("/app/procurement/indents");
  await expect(page.getByRole("heading", { name: "Purchase indents" })).toBeVisible();
  await expect(page.getByRole("button", { name: "New indent" })).toBeVisible();
});

test("indent list opens a read-only backend detail with line quantities", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  await mockIndentRegister(page, [indentAlpha]);
  await page.route(`**/api/v1/procurement/indents/${indentAlpha.indent_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...indentAlpha, capital_total: null, lines: [{ line_no: 1, item_id: "cccccccc-1111-1111-1111-111111111111", item_code: "RM-01", item_name: "Rubber", uom_code: "KG", requested_qty: "12.500", purpose: null, capital_description: null, capital_value: null }] }) }));
  await page.goto("/app/procurement/indents");
  await page.getByRole("table", { name: "Indent results" }).getByRole("link", { name: "Open indent IND-001" }).click();
  await expect(page.getByRole("heading", { name: "IND-001" })).toBeVisible();
  await expect(page.getByText("RM-01 — Rubber")).toBeVisible();
  await expect(page.getByText("12.500 KG")).toBeVisible();
  await expect(page.getByRole("button", { name: /delete/i })).toHaveCount(0);
});

test("indent creation requires a valid line and sends the exact capital contract with decimal strings", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  await mockIndentRegister(page, []);
  await page.route("**/api/v1/org/departments?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/product/items?**", route => {
    const kind = new URL(route.request().url()).searchParams.get("item_type");
    const items = kind === "CAPITAL" ? [{ item_id: "cccccccc-1111-1111-1111-111111111111", code: "MACHINE", name: "Press machine", item_type: "CAPITAL", uom_code: "EA", category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true }] : [];
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items, total: items.length, limit: 200, offset: 0 }) });
  });
  let body: Record<string, unknown> | null = null;
  await page.route("**/api/v1/procurement/indents", route => route.request().method() === "POST" ? (body = route.request().postDataJSON(), route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify({ ...indentAlpha, indent_type: "CAPITAL", lines: [], capital_total: "1250.50" }) })) : route.fallback());
  await page.goto("/app/procurement/indents");
  await page.getByRole("button", { name: "New indent" }).click();
  await expect(page.getByRole("heading", { name: "New indent" })).toBeVisible();
  await page.getByLabel("Indent type").selectOption("CAPITAL");
  await page.getByLabel("Item").selectOption("cccccccc-1111-1111-1111-111111111111");
  await page.getByLabel("Requested quantity").fill("2.500");
  await page.getByLabel("Capital description").fill("Additional press");
  await page.getByLabel("Capital value").fill("1250.50");
  await page.getByLabel("Needed by").fill("2026-12-10");
  await page.getByRole("button", { name: "Create draft indent" }).click();
  expect(body).toEqual({ unit_id: defaultUnits[0].unit_id, department_id: null, indent_type: "CAPITAL", needed_by: "2026-12-10", lines: [{ item_id: "cccccccc-1111-1111-1111-111111111111", requested_qty: "2.500", purpose: null, capital_description: "Additional press", capital_value: "1250.50" }] });
  await expect(page).toHaveURL(/\/app\/procurement\/indents\//);
});

test("indent creation blocks duplicate items and keeps values after a server error", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  await mockIndentRegister(page, []);
  await page.route("**/api/v1/product/items?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ item_id: "cccccccc-1111-1111-1111-111111111111", code: "RM-01", name: "Rubber", item_type: "RM", uom_code: "KG", category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true }], total: 1, limit: 200, offset: 0 }) }));
  let writes = 0;
  await page.route("**/api/v1/procurement/indents", route => { writes += 1; return route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-indent-create-503" }, contentType: "application/json", body: JSON.stringify({ detail: "Indent write unavailable" }) }); });
  await page.goto("/app/procurement/indents/new");
  await page.getByLabel("Item").selectOption("cccccccc-1111-1111-1111-111111111111");
  await page.getByLabel("Requested quantity").fill("3.250");
  await page.getByRole("button", { name: "Add line" }).click();
  await page.getByLabel("Item").last().selectOption("cccccccc-1111-1111-1111-111111111111");
  await page.getByLabel("Requested quantity").last().fill("2");
  await page.getByRole("button", { name: "Create draft indent" }).click();
  await expect(page.getByRole("alert")).toContainText("distinct items");
  expect(writes).toBe(0);
  await page.getByRole("button", { name: "Remove line" }).last().click();
  await page.getByRole("button", { name: "Create draft indent" }).click();
  await expect(page.getByRole("alert")).toContainText("Indent write unavailable");
  await expect(page.getByText("Request ID: req-indent-create-503")).toBeVisible();
  await expect(page.getByLabel("Requested quantity")).toHaveValue("3.250");
  expect(writes).toBe(1);
});

test("indent register has loading, empty, error, request ID and retry states", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  let recovered = false;
  await page.route("**/api/v1/procurement/indents?**", async route => {
    if (!recovered) { await pending; return route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-indent-503" }, contentType: "application/json", body: JSON.stringify({ detail: "Indent service unavailable" }) }); }
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 20, offset: 0 }) });
  });
  await page.goto("/app/procurement/indents");
  await expect(page.getByText("Loading indents…")).toBeVisible();
  release();
  await expect(page.getByRole("alert").filter({ hasText: "Indent service unavailable" })).toBeVisible();
  await expect(page.getByText("Request ID: req-indent-503")).toBeVisible();
  recovered = true;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "No indents found" })).toBeVisible();
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }]) {
  test(`indent register fits ${viewport.width}px and keeps results keyboard accessible`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: indentProfile });
    await mockIndentRegister(page, [indentAlpha]);
    await page.goto("/app/procurement/indents");
    await expect(page.getByRole(viewport.width < 560 ? "list" : "table", { name: "Indent results" })).toContainText("IND-001");
    const overflow = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, client: document.documentElement.clientWidth, elements: [...document.querySelectorAll("body *")].filter(element => element.getBoundingClientRect().right > innerWidth + 1).slice(0, 12).map(element => ({ tag: element.tagName, className: element.className, right: Math.round(element.getBoundingClientRect().right) })) }));
    expect(overflow.width, JSON.stringify(overflow)).toBeLessThanOrEqual(overflow.client);
    if (viewport.width === 768) { const region = page.getByRole("region", { name: "Scrollable indent results" }); await region.focus(); await expect(region).toBeFocused(); }
  });
}

test("owned draft indent editor preserves line order and sends exact PUT fields", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  const second = { ...indentDetailFixture.lines[0], line_no: 2, item_id: "dddddddd-1111-1111-1111-111111111111", item_code: "RM-02", item_name: "Steel", requested_qty: "3.000", purpose: "Press mount" };
  await mockOwnedIndent(page, { ...indentDetailFixture, lines: [...indentDetailFixture.lines, second] });
  await page.route("**/api/v1/org/departments?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ department_id: indentAlpha.department_id, code: "PROD", name: "Production", unit_id: defaultUnits[0].unit_id, parent_department_id: null, head_role_code: null, active: true }], total: 1, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/product/items?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ item_id: indentDetailFixture.lines[0].item_id, code: "RM-01", name: "Rubber", item_type: "RM", uom_code: "KG", category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true }, { item_id: second.item_id, code: "RM-02", name: "Steel", item_type: "RM", uom_code: "KG", category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true }], total: 2, limit: 200, offset: 0 }) }));
  let body: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/procurement/indents/${indentAlpha.indent_id}`, route => route.request().method() === "PUT" ? (body = route.request().postDataJSON(), route.fulfill({ status: 200, headers: indentPutCors, contentType: "application/json", body: JSON.stringify({ ...indentDetailFixture, needed_by: "2026-12-18", lines: [{ ...indentDetailFixture.lines[0], requested_qty: "14.250" }] }) })) : route.fallback());
  await page.goto(`/app/procurement/indents/${indentAlpha.indent_id}`);
  await page.getByRole("button", { name: "Edit draft" }).click();
  const editor = page.getByRole("dialog", { name: "Edit draft indent" });
  await expect(editor).toBeVisible();
  await expect(editor.getByLabel("Requested quantity").first()).toHaveValue("12.500");
  await expect(editor.getByLabel("Requested quantity").last()).toHaveValue("3.000");
  await editor.getByLabel("Requested quantity").first().fill("14.250");
  await editor.getByLabel("Needed by").fill("2026-12-18");
  await editor.getByRole("button", { name: "Save draft" }).click();
  expect(body).toEqual({ department_id: indentAlpha.department_id, needed_by: "2026-12-18", lines: [{ item_id: indentDetailFixture.lines[0].item_id, requested_qty: "14.250", purpose: "Press run", capital_description: null, capital_value: null }, { item_id: second.item_id, requested_qty: "3.000", purpose: "Press mount", capital_description: null, capital_value: null }] });
  await expect(editor).toHaveCount(0);
  await expect(page.getByText("14.250 KG")).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit draft" })).toBeFocused();
});

test("indent draft save rejects duplicate items and preserves edits on a server conflict", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  await mockOwnedIndent(page, indentDetailFixture);
  await page.route("**/api/v1/org/departments?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/product/items?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ item_id: indentDetailFixture.lines[0].item_id, code: "RM-01", name: "Rubber", item_type: "RM", uom_code: "KG", category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true }], total: 1, limit: 200, offset: 0 }) }));
  let writes = 0;
  await page.route(`**/api/v1/procurement/indents/${indentAlpha.indent_id}`, route => route.request().method() === "PUT" ? (writes += 1, route.fulfill({ status: 409, headers: indentPutCors, contentType: "application/json", body: JSON.stringify({ detail: "The indent is pending approval" }) })) : route.fallback());
  await page.goto(`/app/procurement/indents/${indentAlpha.indent_id}`);
  await page.getByRole("button", { name: "Edit draft" }).click();
  const editor = page.getByRole("dialog", { name: "Edit draft indent" });
  await editor.getByRole("button", { name: "Add line" }).click();
  await editor.getByLabel("Item").last().selectOption(indentDetailFixture.lines[0].item_id);
  await editor.getByLabel("Requested quantity").last().fill("1");
  await editor.getByRole("button", { name: "Save draft" }).click();
  await expect(editor.getByRole("alert")).toContainText("distinct items");
  expect(writes).toBe(0);
  await editor.getByRole("button", { name: "Remove line" }).last().click();
  await editor.getByLabel("Requested quantity").fill("16.500");
  await editor.getByRole("button", { name: "Save draft" }).click();
  await expect(editor.getByRole("alert")).toContainText("The indent is pending approval");
  await expect(editor.getByLabel("Requested quantity")).toHaveValue("16.500");
  expect(writes).toBe(1);
});

test("indent draft shows backend 422 field detail without erasing the edited quantity", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  await mockOwnedIndent(page, indentDetailFixture);
  await page.route("**/api/v1/org/departments?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/product/items?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ item_id: indentDetailFixture.lines[0].item_id, code: "RM-01", name: "Rubber", item_type: "RM", uom_code: "KG", category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true }], total: 1, limit: 200, offset: 0 }) }));
  await page.route(`**/api/v1/procurement/indents/${indentAlpha.indent_id}`, route => route.request().method() === "PUT" ? route.fulfill({ status: 422, headers: indentPutCors, contentType: "application/json", body: JSON.stringify({ detail: [{ loc: ["body", "lines", 0, "requested_qty"], msg: "Quantity exceeds available precision" }] }) }) : route.fallback());
  await page.goto(`/app/procurement/indents/${indentAlpha.indent_id}`);
  await page.getByRole("button", { name: "Edit draft" }).click();
  const editor = page.getByRole("dialog", { name: "Edit draft indent" });
  await editor.getByLabel("Requested quantity").fill("14.250");
  await editor.getByRole("button", { name: "Save draft" }).click();
  await expect(editor.getByRole("alert")).toContainText("lines.0.requested_qty");
  await expect(editor.getByRole("alert")).toContainText("Quantity exceeds available precision");
  await expect(editor.getByLabel("Requested quantity")).toHaveValue("14.250");
});

test("capital indent edits keep values as decimal strings and retain required descriptions", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  const capital = { ...indentDetailFixture, indent_type: "CAPITAL", capital_total: "1250.50", lines: [{ ...indentDetailFixture.lines[0], item_id: "eeeeeeee-1111-1111-1111-111111111111", item_code: "MACHINE", item_name: "Press machine", uom_code: "EA", requested_qty: "1.000", capital_description: "Initial press", capital_value: "1250.50" }] };
  await mockOwnedIndent(page, capital);
  await page.route("**/api/v1/org/departments?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/product/items?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ item_id: capital.lines[0].item_id, code: "MACHINE", name: "Press machine", item_type: "CAPITAL", uom_code: "EA", category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true }], total: 1, limit: 200, offset: 0 }) }));
  let body: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/procurement/indents/${indentAlpha.indent_id}`, route => route.request().method() === "PUT" ? (body = route.request().postDataJSON(), route.fulfill({ status: 200, headers: indentPutCors, contentType: "application/json", body: JSON.stringify(capital) })) : route.fallback());
  await page.goto(`/app/procurement/indents/${indentAlpha.indent_id}`);
  await page.getByRole("button", { name: "Edit draft" }).click();
  const editor = page.getByRole("dialog", { name: "Edit draft indent" });
  await editor.getByLabel("Capital description").fill("Replacement press");
  await editor.getByLabel("Capital value").fill("1000.25");
  await editor.getByRole("button", { name: "Save draft" }).click();
  expect(body).toEqual({ department_id: indentAlpha.department_id, needed_by: "2026-12-10", lines: [{ item_id: capital.lines[0].item_id, requested_qty: "1.000", purpose: "Press run", capital_description: "Replacement press", capital_value: "1000.25" }] });
});

test("owner can submit a draft once and then sees linked approval history", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...indentProfile, modules: [...indentProfile.modules, "approvals"] } });
  await mockOwnedIndent(page, indentDetailFixture);
  let posts = 0;
  await page.route(`**/api/v1/procurement/indents/${indentAlpha.indent_id}/submit`, route => { posts += 1; return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...indentDetailFixture, status: "PENDING_APPROVAL", approval_id: "dddddddd-1111-1111-1111-111111111111" }) }); });
  await page.goto(`/app/procurement/indents/${indentAlpha.indent_id}`);
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect(page.getByRole("status", { name: "Indent status update" })).toContainText("submitted");
  await expect(page.getByRole("button", { name: "Submit for approval" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Edit draft" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "View approval history" })).toHaveAttribute("href", "/app/approvals/dddddddd-1111-1111-1111-111111111111");
  expect(posts).toBe(1);
});

test("owned draft cancel requires keyboard-safe confirmation and removes actions", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  await mockOwnedIndent(page, indentDetailFixture);
  let posts = 0;
  await page.route(`**/api/v1/procurement/indents/${indentAlpha.indent_id}/cancel`, route => { posts += 1; return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...indentDetailFixture, status: "CANCELLED" }) }); });
  await page.goto(`/app/procurement/indents/${indentAlpha.indent_id}`);
  await page.getByRole("button", { name: "Cancel indent" }).click();
  const dialog = page.getByRole("dialog", { name: "Cancel this indent?" });
  await expect(dialog.getByRole("button", { name: "Keep indent" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "Confirm cancellation" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Keep indent" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Cancel indent" })).toBeFocused();
  expect(posts).toBe(0);
  await page.getByRole("button", { name: "Cancel indent" }).click();
  await dialog.getByRole("button", { name: "Confirm cancellation" }).click();
  await expect(page.getByText("Cancelled", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel indent" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /delete/i })).toHaveCount(0);
  expect(posts).toBe(1);
});

test("approved owned indents can cancel, but ordered indents and other requesters stay read only", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  const approved = { ...indentDetailFixture, status: "APPROVED" };
  await mockOwnedIndent(page, approved);
  await page.goto(`/app/procurement/indents/${indentAlpha.indent_id}`);
  await expect(page.getByRole("button", { name: "Cancel indent" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit draft" })).toHaveCount(0);
  await mockOwnedIndent(page, { ...indentDetailFixture, status: "ORDERED" });
  await page.reload();
  await expect(page.getByText("Ordered", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel indent" })).toHaveCount(0);
  await mockOwnedIndent(page, indentDetailFixture, false);
  await page.reload();
  await expect(page.getByRole("heading", { name: indentAlpha.indent_no })).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit draft" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Submit for approval" })).toHaveCount(0);
});

test("indent detail retries backend errors and shows server request ID", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  let recovered = false;
  await page.route(`**/api/v1/procurement/indents/${indentAlpha.indent_id}`, route => route.fulfill(recovered
    ? { status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(indentDetailFixture) }
    : { status: 503, headers: { ...cors, "X-Request-ID": "req-indent-detail-503" }, contentType: "application/json", body: JSON.stringify({ detail: "Indent detail unavailable" }) }));
  await page.goto(`/app/procurement/indents/${indentAlpha.indent_id}`);
  await expect(page.getByRole("alert")).toContainText("Indent detail unavailable");
  await expect(page.getByText("Request ID: req-indent-detail-503")).toBeVisible();
  recovered = true;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: indentAlpha.indent_no })).toBeVisible();
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }]) {
  test(`indent draft editor fits ${viewport.width}px and traps keyboard focus`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: indentProfile });
    await mockOwnedIndent(page, indentDetailFixture);
    await page.route("**/api/v1/org/departments?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
    await page.route("**/api/v1/product/items?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ item_id: indentDetailFixture.lines[0].item_id, code: "RM-01", name: "Rubber", item_type: "RM", uom_code: "KG", category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true }], total: 1, limit: 200, offset: 0 }) }));
    await page.goto(`/app/procurement/indents/${indentAlpha.indent_id}`);
    await page.getByRole("button", { name: "Edit draft" }).click();
    const editor = page.getByRole("dialog", { name: "Edit draft indent" });
    await expect(editor).toBeVisible();
    await expect(editor.getByRole("button", { name: "Close line editor" })).toBeFocused();
    await expect(editor.getByRole("button", { name: "Save draft" })).toBeEnabled();
    await page.keyboard.press("Shift+Tab");
    await expect(editor.getByRole("button", { name: "Save draft" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(editor.getByRole("button", { name: "Close line editor" })).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.keyboard.press("Escape");
    await expect(editor).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Edit draft" })).toBeFocused();
  });
}

test("RFQ register shows actual summary fields, backend paging, status and vendor count", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  const rows = Array.from({ length: 21 }, (_, index) => ({ ...rfqAlpha, rfq_id: `ffffffff-1111-1111-1111-${String(index).padStart(12, "0")}`, rfq_no: `RFQ-${String(index + 1).padStart(3, "0")}` }));
  rows[0] = rfqAlpha;
  rows.push(rfqBeta);
  const queries: URLSearchParams[] = [];
  await mockRfqRegister(page, rows, params => queries.push(new URLSearchParams(params)));
  await page.goto("/app/procurement/rfqs");
  await expect(page.getByRole("navigation", { name: "Procurement pages" }).getByRole("link", { name: "RFQs" })).toHaveAttribute("aria-current", "page");
  const table = page.getByRole("table", { name: "RFQ results" });
  await expect(table).toContainText("RFQ-001");
  await expect(table).toContainText("VR-01");
  await expect(table.getByRole("row").filter({ hasText: "RFQ-001" })).toContainText("2 vendors");
  await expect(table).toContainText("Response overdue");
  await expect(page.getByText("Showing 1–20 of 22 records")).toBeVisible();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(table).toContainText("RFQ-021");
  await page.getByLabel("RFQ status").selectOption("DRAFT");
  await expect(table).toContainText("RFQ-002");
  await expect(page.getByText("Showing 1–1 of 1 records")).toBeVisible();
  expect(queries.some(params => params.get("offset") === "20" && params.get("limit") === "20")).toBe(true);
  expect(queries.some(params => params.get("status") === "DRAFT" && params.get("offset") === "0")).toBe(true);
});

test("RFQ response date filtering covers all pages without invented backend query keys", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  const others = Array.from({ length: 205 }, (_, index) => ({ ...rfqAlpha, rfq_id: `ffffffff-3333-3333-3333-${String(index).padStart(12, "0")}`, rfq_no: `RFQ-X${index}` }));
  const queries: URLSearchParams[] = [];
  await mockRfqRegister(page, [...others, rfqBeta], params => queries.push(new URLSearchParams(params)));
  await page.goto("/app/procurement/rfqs");
  await page.getByLabel("Due from").fill("2099-01-01");
  await page.getByLabel("Due through").fill("2099-01-31");
  await expect(page.getByRole("table", { name: "RFQ results" })).toContainText("RFQ-002");
  await expect(page.getByText("Showing 1–1 of 1 records")).toBeVisible();
  expect(queries.some(params => params.get("limit") === "200" && params.get("offset") === "200")).toBe(true);
  expect(queries.every(params => !params.has("from") && !params.has("through") && !params.has("due_from"))).toBe(true);
});

test("RFQ deep link beyond the last page recovers to the final populated page", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  const rows = Array.from({ length: 22 }, (_, index) => ({ ...rfqAlpha,
    rfq_id: `ffffffff-4444-4444-4444-${String(index).padStart(12, "0")}`,
    rfq_no: `RFQ-${String(index + 1).padStart(3, "0")}` }));
  await mockRfqRegister(page, rows);
  await page.goto("/app/procurement/rfqs?page=99");
  await expect(page).toHaveURL(/\/app\/procurement\/rfqs\?page=2$/);
  await expect(page.getByRole("table", { name: "RFQ results" })).toContainText("RFQ-021");
  await expect(page.getByText("Showing 21–22 of 22 records")).toBeVisible();
});

test("an awarded RFQ keeps its response date without presenting an active deadline", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockRfqRegister(page, [{ ...rfqAlpha, status: "AWARDED" }]);
  await page.goto("/app/procurement/rfqs");
  const row = page.getByRole("table", { name: "RFQ results" }).getByRole("row").filter({ hasText: "RFQ-001" });
  await expect(row).toContainText("2020-01-01");
  await expect(row).toContainText("No active response deadline");
  await expect(row).not.toContainText("Response overdue");
});

test("a requester without the procurement module cannot read RFQs", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, permissions: [...grantedProfile.permissions, "po.create"] } });
  let reads = 0;
  await page.route("**/api/v1/procurement/rfqs?**", route => { reads += 1; return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 20, offset: 0 }) }); });
  await page.goto("/app/procurement/rfqs");
  await expect(page.getByRole("heading", { name: "RFQ access unavailable" })).toBeVisible();
  expect(reads).toBe(0);
});

test("procurement reader can open an RFQ but sees no creation control", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  await mockRfqRegister(page, [rfqAlpha]);
  await page.goto("/app/procurement/rfqs");
  await expect(page.getByRole("button", { name: "New RFQ" })).toHaveCount(0);
  await page.getByRole("table", { name: "RFQ results" }).getByRole("link", { name: "Open RFQ RFQ-001" }).click();
  await expect(page.getByRole("heading", { name: "RFQ-001" })).toBeVisible();
  await expect(page.getByText("ORBIT")).toBeVisible();
});

test("authorized buyer creates a draft RFQ with required line and decimal-string quantity", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockRfqRegister(page, []);
  await page.route("**/api/v1/product/items?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ item_id: "cccccccc-1111-1111-1111-111111111111", code: "RM-01", name: "Rubber", item_type: "RM", uom_code: "KG", category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true }], total: 1, limit: 200, offset: 0 }) }));
  let body: Record<string, unknown> | null = null;
  await page.route("**/api/v1/procurement/rfqs", route => route.request().method() === "POST" ? (body = route.request().postDataJSON(), route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify({ ...rfqAlpha, status: "DRAFT", lines: [], invitations: [] }) })) : route.fallback());
  await page.goto("/app/procurement/rfqs");
  await page.getByRole("button", { name: "New RFQ" }).click();
  await page.getByLabel("Item").selectOption("cccccccc-1111-1111-1111-111111111111");
  await page.getByLabel("Quantity").fill("12.500");
  await page.getByLabel("Required by").fill("2099-01-10");
  await page.getByRole("button", { name: "Create draft RFQ" }).click();
  expect(body).toEqual({ unit_id: defaultUnits[0].unit_id, response_due_at: null, vendor_ids: [], lines: [{ item_id: "cccccccc-1111-1111-1111-111111111111", quantity: "12.500", required_by: "2099-01-10" }] });
  await expect(page).toHaveURL(new RegExp(`/app/procurement/rfqs/${rfqAlpha.rfq_id}`));
});

test("RFQ creation keeps entered lines and request ID after a backend error", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await page.route("**/api/v1/product/items?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ item_id: "cccccccc-1111-1111-1111-111111111111", code: "RM-01", name: "Rubber", item_type: "RM", uom_code: "KG", category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true }], total: 1, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/procurement/rfqs", route => route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-rfq-create-503" }, contentType: "application/json", body: JSON.stringify({ detail: "RFQ write unavailable" }) }));
  await page.goto("/app/procurement/rfqs/new");
  await page.getByLabel("Item").selectOption("cccccccc-1111-1111-1111-111111111111");
  await page.getByLabel("Quantity").fill("12.500");
  await page.getByRole("button", { name: "Create draft RFQ" }).click();
  await expect(page.getByRole("alert")).toContainText("RFQ write unavailable");
  await expect(page.getByText("Request ID: req-rfq-create-503")).toBeVisible();
  await expect(page.getByLabel("Quantity")).toHaveValue("12.500");
});

test("RFQ creation explains item loading and an empty item catalog", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/v1/product/items?**", async route => { await pending; return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }); });
  await page.goto("/app/procurement/rfqs/new");
  await expect(page.getByText("Loading active items…")).toBeVisible();
  release();
  await expect(page.getByRole("heading", { name: "No active items" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Create draft RFQ" })).toBeDisabled();
});

test("RFQ register shows loading, error request ID, retry and empty states", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  let recovered = false;
  await page.route("**/api/v1/procurement/rfqs?**", async route => {
    if (!recovered) { await pending; return route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-rfq-503" }, contentType: "application/json", body: JSON.stringify({ detail: "RFQ service unavailable" }) }); }
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 20, offset: 0 }) });
  });
  await page.goto("/app/procurement/rfqs");
  await expect(page.getByText("Loading RFQs…")).toBeVisible();
  release();
  await expect(page.getByRole("alert")).toContainText("RFQ service unavailable");
  await expect(page.getByText("Request ID: req-rfq-503")).toBeVisible();
  recovered = true;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "No RFQs found" })).toBeVisible();
});

for (const viewport of [{ width: 360, height: 800 }, { width: 640, height: 800 }, { width: 768, height: 1024 }, { width: 1280, height: 800 }, { width: 1536, height: 960 }]) {
  test(`RFQ list fits ${viewport.width}px with accessible due state`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: rfqProfile });
    await mockRfqRegister(page, [rfqAlpha]);
    await page.goto("/app/procurement/rfqs");
    await expect(page.getByRole(viewport.width < 560 ? "list" : "table", { name: "RFQ results" })).toContainText("Response overdue");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    if (viewport.width === 768) { const region = page.getByRole("region", { name: "Scrollable RFQ results" }); await region.focus(); await expect(region).toBeFocused(); }
  });
}

test("RFQ detail presents backend comparison prices, coverage, and terms to a reader", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  await mockRfqRegister(page, [rfqAlpha]);
  await page.route(`**/api/v1/procurement/rfqs/${rfqAlpha.rfq_id}/comparison`, route => route.fulfill({
    status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({
      rfq_id: rfqAlpha.rfq_id, rfq_no: rfqAlpha.rfq_no,
      lines: [{ rfq_line_id: "eeeeeeee-3333-3333-3333-333333333333", line_no: 1, item_code: "RM-01", quantity: "12.500", offers: [{
        vendor_id: "eeeeeeee-1111-1111-1111-111111111111", vendor_code: "ORBIT", unit_price: "8.25", quoted_qty: "12.500", lead_days: 5,
        line_total: "103.13", valid_until: "2099-01-01", expired: false, covers_quantity: true, is_lowest: true,
      }] }],
      vendors: [{ vendor_id: "eeeeeeee-1111-1111-1111-111111111111", vendor_code: "ORBIT", vendor_name: "Orbit", lines_quoted: 1,
        covers_all_lines: true, has_expired_offer: false, total: "103.13", max_lead_days: 5 }],
      cheapest_vendor_id: "eeeeeeee-1111-1111-1111-111111111111",
    }),
  }));
  await page.goto(`/app/procurement/rfqs/${rfqAlpha.rfq_id}`);
  const comparison = page.getByRole("table", { name: "RFQ comparison" });
  await expect(comparison).toContainText("RM-01");
  await expect(comparison).toContainText("8.25");
  await expect(comparison).toContainText("103.13");
  await expect(comparison).toContainText("5 days");
  await expect(comparison).toContainText("2099-01-01");
  await expect(comparison).toContainText("Lowest eligible");
  await expect(page.getByRole("button", { name: "Award RFQ" })).toHaveCount(0);
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }]) {
  test(`RFQ comparison keeps vendor terms readable at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: indentProfile });
    await mockRfqRegister(page, [rfqAlpha]);
    await page.route(`**/api/v1/procurement/rfqs/${rfqAlpha.rfq_id}/comparison`, route => route.fulfill({
      status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({
        rfq_id: rfqAlpha.rfq_id, rfq_no: rfqAlpha.rfq_no,
        lines: [{ rfq_line_id: "eeeeeeee-3333-3333-3333-333333333333", line_no: 1, item_code: "RM-01", quantity: "12.500", offers: [
          { vendor_id: "eeeeeeee-1111-1111-1111-111111111111", vendor_code: "ORBIT", unit_price: "8.25", quoted_qty: "12.500", lead_days: 5, line_total: "103.13", valid_until: "2099-01-01", expired: false, covers_quantity: true, is_lowest: true },
          { vendor_id: "eeeeeeee-2222-2222-2222-222222222222", vendor_code: "METAL", unit_price: "7.50", quoted_qty: "10.000", lead_days: 7, line_total: "93.75", valid_until: "2099-01-01", expired: false, covers_quantity: false, is_lowest: false },
        ] }],
        vendors: [
          { vendor_id: "eeeeeeee-1111-1111-1111-111111111111", vendor_code: "ORBIT", vendor_name: "Orbit", lines_quoted: 1, covers_all_lines: true, has_expired_offer: false, total: "103.13", max_lead_days: 5 },
          { vendor_id: "eeeeeeee-2222-2222-2222-222222222222", vendor_code: "METAL", vendor_name: "Metal Co", lines_quoted: 1, covers_all_lines: false, has_expired_offer: false, total: "93.75", max_lead_days: 7 },
        ], cheapest_vendor_id: "eeeeeeee-1111-1111-1111-111111111111",
      }),
    }));
    await page.goto(`/app/procurement/rfqs/${rfqAlpha.rfq_id}`);
    if (viewport.width === 360) {
      const cards = page.getByRole("list", { name: "Vendor comparison cards" });
      await expect(cards).toContainText("Quantity shortfall");
      await expect(cards).toContainText("Lowest complete total");
      await expect(page.getByRole("table", { name: "RFQ comparison" })).toBeHidden();
    } else {
      const region = page.getByRole("region", { name: "Scrollable RFQ comparison" });
      await expect(region).toBeVisible();
      await region.focus();
      await expect(region).toBeFocused();
      expect(await region.evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
}

test("RFQ buyer actions follow draft and sent states while reader actions stay hidden", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockRfqRegister(page, [rfqBeta, rfqAlpha]);
  await page.goto(`/app/procurement/rfqs/${rfqBeta.rfq_id}`);
  await expect(page.getByRole("button", { name: "Edit draft" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Add vendors" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Send RFQ" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel RFQ" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Record quote" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Award RFQ" })).toHaveCount(0);
  await page.goto(`/app/procurement/rfqs/${rfqAlpha.rfq_id}`);
  await expect(page.getByRole("button", { name: "Edit draft" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Send RFQ" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Add vendors" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Record quote" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Award RFQ" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel RFQ" })).toBeVisible();
});

test("RFQ send confirms the action and preserves backend conflict detail", async ({ page }) => {
  const draft = { ...rfqAlpha, status: "DRAFT" };
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockRfqRegister(page, [draft]);
  let sends = 0;
  await page.route(`**/api/v1/procurement/rfqs/${draft.rfq_id}/send`, route => {
    sends += 1;
    return route.fulfill({ status: 409, headers: { ...cors, "X-Request-ID": "req-send-conflict" }, contentType: "application/json", body: JSON.stringify({ detail: "RFQ was changed by another buyer" }) });
  });
  await page.goto(`/app/procurement/rfqs/${draft.rfq_id}`);
  await page.getByRole("button", { name: "Send RFQ" }).click();
  const dialog = page.getByRole("dialog", { name: "Send this RFQ?" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("approved vendor");
  await dialog.getByRole("button", { name: "Confirm send" }).click();
  await expect(page.getByRole("alert")).toContainText("RFQ was changed by another buyer");
  await expect(page.getByRole("button", { name: "Send RFQ" })).toBeVisible();
  expect(sends).toBe(1);
});

test("RFQ cancellation confirms once, shows a conflict, then updates from the server", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockRfqRegister(page, [rfqBeta]);
  let attempts = 0;
  await page.route(`**/api/v1/procurement/rfqs/${rfqBeta.rfq_id}/cancel`, route => {
    attempts += 1;
    return route.fulfill(attempts === 1
      ? { status: 409, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "RFQ can no longer be cancelled" }) }
      : { status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(rfqDetailFixture({ ...rfqBeta, status: "CANCELLED" })) });
  });
  await page.goto(`/app/procurement/rfqs/${rfqBeta.rfq_id}`);
  await page.getByRole("button", { name: "Cancel RFQ" }).click();
  const dialog = page.getByRole("dialog", { name: "Cancel this RFQ?" });
  await dialog.getByRole("button", { name: "Confirm cancellation" }).click();
  await expect(dialog.getByRole("alert")).toContainText("RFQ can no longer be cancelled");
  await dialog.getByRole("button", { name: "Confirm cancellation" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("Cancelled", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel RFQ" })).toHaveCount(0);
  expect(attempts).toBe(2);
});

test("RFQ buyer invites an approved vendor through the exact vendor endpoint", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockRfqRegister(page, [rfqBeta]);
  const vendor = { vendor_id: "eeeeeeee-4444-4444-4444-444444444444", code: "NOVA", name: "Nova Metals", gstin: null, pan: null, msme_flag: false, payment_term_id: null, status: "APPROVED" };
  await page.route("**/api/v1/parties/vendors?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [vendor], total: 1, limit: 200, offset: 0 }) }));
  let body: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/procurement/rfqs/${rfqBeta.rfq_id}/vendors`, route => {
    body = route.request().postDataJSON();
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(rfqDetailFixture(rfqBeta, [{ vendor_id: vendor.vendor_id, vendor_code: vendor.code, vendor_name: vendor.name, response_status: "INVITED", invited_at: "2026-11-05T08:00:00Z", quotes_received: 0 }])) });
  });
  await page.goto(`/app/procurement/rfqs/${rfqBeta.rfq_id}`);
  await page.getByRole("button", { name: "Add vendors" }).click();
  await page.getByLabel("Approved vendor", { exact: true }).selectOption(vendor.vendor_id);
  await page.getByRole("button", { name: "Invite vendor" }).click();
  expect(body).toEqual({ vendor_ids: [vendor.vendor_id] });
  await expect(page.getByText("NOVA — Nova Metals")).toBeVisible();
});

test("RFQ draft edit replaces lines with decimal strings and preserves a validation error", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockRfqRegister(page, [rfqBeta]);
  let body: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/procurement/rfqs/${rfqBeta.rfq_id}`, route => route.request().method() === "PUT"
    ? (body = route.request().postDataJSON(), route.fulfill({ status: 422, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "The required-by date cannot be in the past" }) }))
    : route.fallback());
  await page.goto(`/app/procurement/rfqs/${rfqBeta.rfq_id}`);
  await page.getByRole("button", { name: "Edit draft" }).click();
  const editor = page.getByRole("dialog", { name: "Edit draft RFQ" });
  await editor.getByLabel("Quantity").fill("13.250");
  await editor.getByRole("button", { name: "Save draft" }).click();
  expect(body).toEqual({ response_due_at: rfqBeta.response_due_at, lines: [{ item_id: rfqLineFixture.item_id, quantity: "13.250", required_by: null }] });
  await expect(editor.getByRole("alert")).toContainText("required-by date cannot be in the past");
  await expect(editor.getByLabel("Quantity")).toHaveValue("13.250");
});

test("RFQ quote entry sends backend line IDs, terms, and decimal strings", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockRfqRegister(page, [rfqAlpha]);
  let body: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/procurement/rfqs/${rfqAlpha.rfq_id}/quotes`, route => {
    body = route.request().postDataJSON();
    return route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify(rfqDetailFixture(rfqAlpha, rfqInvitations.map(vendor => vendor.vendor_code === "ORBIT" ? { ...vendor, response_status: "QUOTED", quotes_received: 1 } : vendor))) });
  });
  await page.goto(`/app/procurement/rfqs/${rfqAlpha.rfq_id}`);
  await page.getByRole("button", { name: "Record quote" }).click();
  const quote = page.getByRole("dialog", { name: "Record vendor quote" });
  await quote.getByLabel("Quoted vendor").selectOption(rfqInvitations[0].vendor_id);
  await quote.getByLabel("Quote reference").fill("Q-22");
  await quote.getByLabel("Valid until").fill("2099-01-01");
  await quote.getByLabel("Unit price").fill("8.25");
  await quote.getByLabel("Quoted quantity").fill("12.500");
  await quote.getByLabel("Lead days").fill("5");
  await quote.getByRole("button", { name: "Save quote" }).click();
  expect(body).toEqual({ vendor_id: rfqInvitations[0].vendor_id, quote_reference: "Q-22", valid_until: "2099-01-01", lines: [{ rfq_line_id: rfqLineFixture.rfq_line_id, unit_price: "8.25", quoted_qty: "12.500", lead_days: 5 }] });
  await expect(page.getByText("Quote recorded.")).toBeVisible();
});

test("RFQ award preserves its form on conflict and creates a PO with exact fields", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockRfqRegister(page, [rfqAlpha]);
  let awarded = false;
  await page.route(`**/api/v1/procurement/rfqs/${rfqAlpha.rfq_id}`, route => route.request().method() === "GET"
    ? route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(rfqDetailFixture({ ...rfqAlpha, status: awarded ? "AWARDED" : "SENT" })) })
    : route.fallback());
  let attempts = 0;
  let body: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/procurement/rfqs/${rfqAlpha.rfq_id}/award`, route => {
    attempts += 1; body = route.request().postDataJSON();
    if (attempts === 1) return route.fulfill({ status: 409, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "Quote changed; review the comparison" }) });
    awarded = true;
    return route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify({ po_id: "99999999-1111-1111-1111-111111111111", po_no: "PO-0042", unit_id: rfqAlpha.unit_id, unit_code: rfqAlpha.unit_code, vendor_id: rfqInvitations[1].vendor_id, vendor_code: "METAL", vendor_name: "Metal Co", status: "DRAFT", currency_code: "INR", payment_term_id: null, rfq_id: rfqAlpha.rfq_id, rfq_no: rfqAlpha.rfq_no, created_by_name: "Asha Patel", created_at: "2026-11-05T08:00:00Z", approval_id: null, net_total: "100.00", gst_total: "0.00", gross_total: "100.00", indents: [], lines: [] }) });
  });
  await page.goto(`/app/procurement/rfqs/${rfqAlpha.rfq_id}`);
  await page.getByRole("button", { name: "Award RFQ" }).click();
  const dialog = page.getByRole("dialog", { name: "Award RFQ to vendor" });
  await dialog.getByLabel("Quoted vendor").selectOption(rfqInvitations[1].vendor_id);
  await dialog.getByLabel("Delivery due").fill("2099-01-10");
  await dialog.getByRole("button", { name: "Create purchase order" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Quote changed; review the comparison");
  await expect(dialog.getByLabel("Delivery due")).toHaveValue("2099-01-10");
  await dialog.getByRole("button", { name: "Create purchase order" }).click();
  expect(body).toEqual({ vendor_id: rfqInvitations[1].vendor_id, due_date: "2099-01-10", tax_code_id: null, payment_term_id: null });
  await expect(page.getByText("PO-0042")).toBeVisible();
  await expect(page.getByRole("button", { name: "Award RFQ" })).toHaveCount(0);
});

test("RFQ comparison shows loading, server request ID, retry, and no-quotes state", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  await mockRfqRegister(page, [rfqAlpha]);
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  let attempts = 0;
  let recovered = false;
  await page.route(`**/api/v1/procurement/rfqs/${rfqAlpha.rfq_id}/comparison`, async route => {
    attempts += 1;
    if (!recovered) {
      await pending;
      return route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-comparison-503" }, contentType: "application/json", body: JSON.stringify({ detail: "Comparison unavailable" }) });
    }
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ rfq_id: rfqAlpha.rfq_id, rfq_no: rfqAlpha.rfq_no, lines: [], vendors: [], cheapest_vendor_id: null }) });
  });
  await page.goto(`/app/procurement/rfqs/${rfqAlpha.rfq_id}`);
  await expect(page.getByText("Loading comparison…")).toBeVisible();
  release();
  await expect(page.getByRole("alert")).toContainText("Comparison unavailable");
  await expect(page.getByText("Request ID: req-comparison-503")).toBeVisible();
  recovered = true;
  await page.getByRole("button", { name: "Retry comparison" }).click();
  await expect(page.getByText("No vendor quotations recorded yet.")).toBeVisible();
  expect(attempts).toBeGreaterThanOrEqual(2);
});

test("RFQ send explains configured minQuotes without making up a different rule", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile }, defaultUnits, {}, { settings: [{ status: 200, body: [{ key: "minQuotes", value: 3 }] }] });
  await mockRfqRegister(page, [rfqBeta]);
  await page.goto(`/app/procurement/rfqs/${rfqBeta.rfq_id}`);
  await page.getByRole("button", { name: "Send RFQ" }).click();
  await expect(page.getByRole("dialog", { name: "Send this RFQ?" })).toContainText("at least 3 approved vendors");
});

test("RFQ quote server failure retains entered terms and reports request ID", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockRfqRegister(page, [rfqAlpha]);
  await page.route(`**/api/v1/procurement/rfqs/${rfqAlpha.rfq_id}/quotes`, route => route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-quote-503" }, contentType: "application/json", body: JSON.stringify({ detail: "Quote service unavailable" }) }));
  await page.goto(`/app/procurement/rfqs/${rfqAlpha.rfq_id}`);
  await page.getByRole("button", { name: "Record quote" }).click();
  const dialog = page.getByRole("dialog", { name: "Record vendor quote" });
  await dialog.getByLabel("Quoted vendor").selectOption(rfqInvitations[0].vendor_id);
  await dialog.getByLabel("Unit price").fill("8.25");
  await dialog.getByRole("button", { name: "Save quote" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Quote service unavailable");
  await expect(dialog.getByText("Request ID: req-quote-503")).toBeVisible();
  await expect(dialog.getByLabel("Unit price")).toHaveValue("8.25");
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }]) {
  test(`RFQ buyer dialogs fit ${viewport.width}px and return keyboard focus`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: rfqProfile });
    await mockRfqRegister(page, [rfqBeta]);
    await page.goto(`/app/procurement/rfqs/${rfqBeta.rfq_id}`);
    await page.getByRole("button", { name: "Edit draft" }).click();
    const dialog = page.getByRole("dialog", { name: "Edit draft RFQ" });
    await expect(dialog.getByRole("button", { name: "Close RFQ dialog" })).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(dialog.getByRole("button", { name: "Save draft" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(dialog.getByRole("button", { name: "Close RFQ dialog" })).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Edit draft" })).toBeFocused();
  });
}

test("PO register shows backend value, line receipts, late lines, and linked approval without buyer actions", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  await mockPoRegister(page);
  await page.goto("/app/procurement/purchase-orders");
  await expect(page.getByRole("heading", { name: "Purchase orders" })).toBeVisible();
  const table = page.getByRole("table", { name: "Purchase order results" });
  await expect(table).toContainText("PO-0042");
  await expect(table).toContainText("Orbit Rubber");
  await expect(table).toContainText("206.26");
  await expect(table).toContainText("1 of 2 lines with receipts");
  await expect(table).toContainText("1 late line");
  await expect(table.getByRole("row", { name: /PO-0042/ })).toContainText("Approved");
  await expect(page.getByRole("button", { name: "New purchase order" })).toHaveCount(0);
});

test("PO desktop register explains horizontal table scrolling when later columns extend beyond the viewport", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  await mockPoRegister(page);
  await page.goto("/app/procurement/purchase-orders");
  const region = page.getByRole("region", { name: "Scrollable purchase order results" });
  await expect(region).toBeVisible();
  expect(await region.evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true);
  await expect(page.getByText("Scroll table horizontally to view more columns.")).toBeVisible();
});

test("PO filters send supported status, vendor and plant keys, with dates filtered across pages", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  const requests: URLSearchParams[] = [];
  await mockPoRegister(page, [poAlpha, poBeta], params => requests.push(new URLSearchParams(params)));
  await page.goto("/app/procurement/purchase-orders");
  await expect(page.getByRole("heading", { name: "Purchase orders" })).toBeVisible();
  await page.getByLabel("PO status").selectOption("PARTIALLY_RECEIVED");
  await page.getByLabel("Vendor").selectOption(poAlpha.vendor_id);
  await page.getByLabel("Created from").fill("2026-11-01");
  await page.getByLabel("Created through").fill("2026-11-01");
  await expect(page.getByRole("table", { name: "Purchase order results" })).toContainText("PO-0042");
  await expect(page.getByRole("table", { name: "Purchase order results" })).not.toContainText("PO-0043");
  expect(requests.some(params => params.get("status") === "PARTIALLY_RECEIVED" && params.get("vendor_id") === poAlpha.vendor_id && params.get("unit_id") === poAlpha.unit_id)).toBe(true);
  expect(requests.every(params => !params.has("from") && !params.has("through"))).toBe(true);
});

test("PO buyer direct creation sends exact decimal-string lines and opens the created order", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockPoRegister(page);
  let body: Record<string, unknown> | null = null;
  await page.route("**/api/v1/procurement/purchase-orders", route => route.request().method() === "POST"
    ? (body = route.request().postDataJSON(), route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify(poDetail(poBeta)) }))
    : route.fallback());
  await page.route("**/api/v1/product/items?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ item_id: poLine.item_id, code: "RM-01", name: "Rubber", item_type: "RM", uom_code: "KG", category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true }], total: 1, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/ref/payment-terms?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/ref/tax-codes?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.goto("/app/procurement/purchase-orders/new");
  await page.getByLabel("Approved vendor").selectOption(poAlpha.vendor_id);
  await page.getByLabel("Item").selectOption(poLine.item_id);
  await page.getByLabel("Ordered quantity").fill("12.500");
  await page.getByLabel("Unit price").fill("8.25");
  await page.getByLabel("Delivery due").fill("2099-01-10");
  await page.getByRole("button", { name: "Create purchase order" }).click();
  expect(body).toEqual({ unit_id: poAlpha.unit_id, vendor_id: poAlpha.vendor_id, payment_term_id: null, rfq_id: null, indent_ids: [], lines: [{ item_id: poLine.item_id, ordered_qty: "12.500", unit_price: "8.25", tax_code_id: null, due_date: "2099-01-10", schedule: [] }] });
  await expect(page).toHaveURL(new RegExp(`/app/procurement/purchase-orders/${poBeta.po_id}$`));
  await expect(page.getByRole("heading", { name: poBeta.po_no })).toBeVisible();
});

test("PO from approved indent uses backend indent lines and sends only item prices", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockPoRegister(page);
  const approved = { ...indentAlpha, status: "APPROVED", needed_by: "2099-01-01" };
  await page.route("**/api/v1/procurement/indents?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [approved], total: 1, limit: 200, offset: 0 }) }));
  await page.route(`**/api/v1/procurement/indents/${approved.indent_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...indentDetailFixture, ...approved }) }));
  await page.route("**/api/v1/ref/payment-terms?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/ref/tax-codes?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  let body: Record<string, unknown> | null = null;
  await page.route("**/api/v1/procurement/purchase-orders/from-indents", route => {
    body = route.request().postDataJSON();
    return route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify(poDetail(poBeta)) });
  });
  await page.goto("/app/procurement/purchase-orders/from-indents");
  await page.getByLabel("Select IND-001").check();
  await page.getByLabel("Approved vendor").selectOption(poAlpha.vendor_id);
  await page.getByLabel("Unit price for RM-01").fill("8.25");
  await page.getByLabel("Delivery due").fill("2099-01-10");
  await page.getByRole("button", { name: "Create from indents" }).click();
  expect(body).toEqual({ indent_ids: [approved.indent_id], vendor_id: poAlpha.vendor_id, payment_term_id: null, due_date: "2099-01-10", prices: [{ item_id: rfqLineFixture.item_id, unit_price: "8.25", tax_code_id: null }] });
  await expect(page).toHaveURL(new RegExp(`/app/procurement/purchase-orders/${poBeta.po_id}$`));
});

test("PO date filter reads every backend page before applying creation dates", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  const orders = Array.from({ length: 201 }, (_, index) => ({ ...poAlpha, po_id: `99999999-1111-1111-1111-${String(index).padStart(12, "0")}`, po_no: `PO-${String(index + 1).padStart(4, "0")}`, created_at: index === 200 ? "2026-11-02T10:00:00Z" : "2026-11-01T10:00:00Z", approval_id: null }));
  const queries: URLSearchParams[] = [];
  await mockPoRegister(page, orders, params => queries.push(new URLSearchParams(params)));
  await page.goto("/app/procurement/purchase-orders?from=2026-11-02&through=2026-11-02");
  await expect(page.getByRole("table", { name: "Purchase order results" })).toContainText("PO-0201");
  await expect(page.getByRole("table", { name: "Purchase order results" })).not.toContainText("PO-0001");
  expect(queries.some(params => params.get("limit") === "200" && params.get("offset") === "200")).toBe(true);
});

test("PO register preserves error detail and request ID, retries to an honest empty state", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  await mockPoRegister(page);
  let recovered = false;
  await page.route("**/api/v1/procurement/purchase-orders?**", route => route.fulfill(recovered
    ? { status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 20, offset: 0 }) }
    : { status: 503, headers: { ...cors, "X-Request-ID": "req-po-list-503" }, contentType: "application/json", body: JSON.stringify({ detail: "Order register unavailable" }) }));
  await page.goto("/app/procurement/purchase-orders");
  await expect(page.getByRole("alert")).toContainText("Order register unavailable");
  await expect(page.getByText("Request ID: req-po-list-503")).toBeVisible();
  recovered = true;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "No purchase orders found" })).toBeVisible();
});

test("PO buyer direct creation keeps decimal input on backend validation failure", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockPoRegister(page);
  await page.route("**/api/v1/product/items?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ item_id: poLine.item_id, code: "RM-01", name: "Rubber", item_type: "RM", uom_code: "KG", category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true }], total: 1, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/ref/payment-terms?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/ref/tax-codes?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/procurement/purchase-orders", route => route.request().method() === "POST"
    ? route.fulfill({ status: 422, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "The vendor is no longer approved" }) }) : route.fallback());
  await page.goto("/app/procurement/purchase-orders/new");
  await page.getByLabel("Approved vendor").selectOption(poAlpha.vendor_id);
  await page.getByLabel("Item").selectOption(poLine.item_id);
  await page.getByLabel("Ordered quantity").fill("12.500");
  await page.getByLabel("Unit price").fill("8.25");
  await page.getByLabel("Delivery due").fill("2099-01-10");
  await page.getByRole("button", { name: "Create purchase order" }).click();
  await expect(page.getByRole("alert")).toContainText("The vendor is no longer approved");
  await expect(page.getByLabel("Ordered quantity")).toHaveValue("12.500");
  await expect(page.getByLabel("Unit price")).toHaveValue("8.25");
});

test("PO from-indent conflict preserves selected approved indent and item price", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockPoRegister(page);
  const approved = { ...indentAlpha, status: "APPROVED", needed_by: "2099-01-01" };
  await page.route("**/api/v1/procurement/indents?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [approved], total: 1, limit: 200, offset: 0 }) }));
  await page.route(`**/api/v1/procurement/indents/${approved.indent_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...indentDetailFixture, ...approved }) }));
  await page.route("**/api/v1/ref/payment-terms?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/ref/tax-codes?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/procurement/purchase-orders/from-indents", route => route.fulfill({ status: 422, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "Indent IND-001 is ordered; only approved indents can be ordered" }) }));
  await page.goto("/app/procurement/purchase-orders/from-indents");
  await page.getByLabel("Select IND-001").check();
  await page.getByLabel("Approved vendor").selectOption(poAlpha.vendor_id);
  await page.getByLabel("Unit price for RM-01").fill("8.25");
  await page.getByLabel("Delivery due").fill("2099-01-10");
  await page.getByRole("button", { name: "Create from indents" }).click();
  await expect(page.getByRole("alert")).toContainText("only approved indents can be ordered");
  await expect(page.getByLabel("Select IND-001")).toBeChecked();
  await expect(page.getByLabel("Unit price for RM-01")).toHaveValue("8.25");
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }, { width: 1280, height: 800 }, { width: 1536, height: 960 }]) {
  test(`PO list remains usable at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: indentProfile });
    await mockPoRegister(page);
    await page.goto("/app/procurement/purchase-orders");
    await expect(page.getByRole(viewport.width <= 560 ? "list" : "table", { name: "Purchase order results" })).toContainText("PO-0042");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    if (viewport.width === 768) { const region = page.getByRole("region", { name: "Scrollable purchase order results" }); await region.focus(); await expect(region).toBeFocused(); }
  });
}

test("PO creation explains an empty active item catalog and blocks submission", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockPoRegister(page);
  await page.route("**/api/v1/product/items?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/ref/payment-terms?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/ref/tax-codes?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.goto("/app/procurement/purchase-orders/new");
  await expect(page.getByRole("heading", { name: "No active items" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Create purchase order" })).toBeDisabled();
});

test("PO register reports approval lookup failure instead of inventing approval state", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  await mockPoRegister(page);
  await page.route(`**/api/v1/procurement/purchase-orders/${poAlpha.po_id}/history`, route => route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-po-history-503" }, contentType: "application/json", body: JSON.stringify({ detail: "Approval state unavailable" }) }));
  await page.goto("/app/procurement/purchase-orders");
  await expect(page.getByRole("alert")).toContainText("Approval state unavailable");
  await expect(page.getByText("Request ID: req-po-history-503")).toBeVisible();
  await expect(page.getByRole("table", { name: "Purchase order results" }).getByRole("row", { name: /PO-0042/ })).toContainText("Approval unavailable");
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }]) {
  test(`PO direct creation fits ${viewport.width}px and keeps labeled fields`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: rfqProfile });
    await mockPoRegister(page);
    await page.route("**/api/v1/product/items?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ item_id: poLine.item_id, code: "RM-01", name: "Rubber", item_type: "RM", uom_code: "KG", category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true }], total: 1, limit: 200, offset: 0 }) }));
    await page.route("**/api/v1/ref/payment-terms?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
    await page.route("**/api/v1/ref/tax-codes?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
    await page.goto("/app/procurement/purchase-orders/new");
    await expect(page.getByLabel("Ordered quantity")).toBeVisible();
    await expect(page.getByLabel("Unit price")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
}

test("PO from multiple approved indents sends one price per distinct backend item", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockPoRegister(page);
  const first = { ...indentAlpha, status: "APPROVED", needed_by: "2099-01-01" };
  const second = { ...indentBeta, status: "APPROVED", indent_type: "BOM", needed_by: "2099-01-02" };
  await page.route("**/api/v1/procurement/indents?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [first, second], total: 2, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/procurement/indents/*", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...indentDetailFixture, ...(route.request().url().includes(second.indent_id) ? second : first) }) }));
  await page.route("**/api/v1/ref/payment-terms?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/ref/tax-codes?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  let body: Record<string, unknown> | null = null;
  await page.route("**/api/v1/procurement/purchase-orders/from-indents", route => {
    body = route.request().postDataJSON();
    return route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify(poDetail(poBeta)) });
  });
  await page.goto("/app/procurement/purchase-orders/from-indents");
  await page.getByLabel("Select IND-001").check();
  await page.getByLabel("Select IND-002").check();
  await page.getByLabel("Approved vendor").selectOption(poAlpha.vendor_id);
  await page.getByLabel("Unit price for RM-01").fill("8.25");
  await page.getByLabel("Delivery due").fill("2099-01-10");
  await page.getByRole("button", { name: "Create from indents" }).click();
  expect(body).toEqual({ indent_ids: [first.indent_id, second.indent_id], vendor_id: poAlpha.vendor_id, payment_term_id: null, due_date: "2099-01-10", prices: [{ item_id: rfqLineFixture.item_id, unit_price: "8.25", tax_code_id: null }] });
});

test("PO creation routes remain read-only for a procurement reader without po.create", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  await mockPoRegister(page);
  await page.goto("/app/procurement/purchase-orders/new");
  await expect(page.getByRole("heading", { name: "Purchase orders" })).toBeVisible();
  await expect(page.getByRole("button", { name: "New purchase order" })).toHaveCount(0);
  await expect(page.getByLabel("Ordered quantity")).toHaveCount(0);
  await page.goto("/app/procurement/purchase-orders/from-indents");
  await expect(page.getByRole("heading", { name: "Purchase orders" })).toBeVisible();
  await expect(page.getByLabel("Select IND-001")).toHaveCount(0);
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }]) {
  test(`PO from-indent form fits ${viewport.width}px with accessible selection`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: rfqProfile });
    await mockPoRegister(page);
    const approved = { ...indentAlpha, status: "APPROVED", needed_by: "2099-01-01" };
    await page.route("**/api/v1/procurement/indents?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [approved], total: 1, limit: 200, offset: 0 }) }));
    await page.route(`**/api/v1/procurement/indents/${approved.indent_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...indentDetailFixture, ...approved }) }));
    await page.route("**/api/v1/ref/payment-terms?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
    await page.route("**/api/v1/ref/tax-codes?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
    await page.goto("/app/procurement/purchase-orders/from-indents");
    await page.getByLabel("Select IND-001").check();
    await expect(page.getByLabel("Unit price for RM-01")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
}

test("PO detail reconciles backend lines, schedules, receiving, sources, totals, and history", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  await mockPoRegister(page);
  await page.route(`**/api/v1/procurement/purchase-orders/${poAlpha.po_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...poDetail(poAlpha), rfq_id: rfqAlpha.rfq_id, rfq_no: rfqAlpha.rfq_no, indents: [{ indent_id: indentAlpha.indent_id, indent_no: indentAlpha.indent_no }] }) }));
  await page.route(`**/api/v1/procurement/purchase-orders/${poAlpha.po_id}/receiving`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify([{ po_line_id: poLine.po_line_id, line_no: 1, item_code: "RM-01", ordered_qty: "12.500", received_qty: "5.000", pending_qty: "7.500", max_receivable_qty: "8.125" }]) }));
  await page.route(`**/api/v1/procurement/purchase-orders/${poAlpha.po_id}/history`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ events: [{ occurred_at: "2026-11-01T10:00:00Z", action: "po.create", actor: "Asha Patel", actor_role_code: "BUYER", details: { lines: 2 } }], approval: { status: "APPROVED" } }) }));
  await page.goto(`/app/procurement/purchase-orders/${poAlpha.po_id}`);
  await expect(page.getByRole("heading", { name: poAlpha.po_no })).toBeVisible();
  await expect(page.getByRole("link", { name: rfqAlpha.rfq_no })).toHaveAttribute("href", `/app/procurement/rfqs/${rfqAlpha.rfq_id}`);
  await expect(page.getByRole("link", { name: indentAlpha.indent_no })).toHaveAttribute("href", `/app/procurement/indents/${indentAlpha.indent_id}`);
  await expect(page.getByRole("heading", { name: "Order lines" })).toBeVisible();
  await expect(page.getByText("Ordered 12.500 KG").first()).toBeVisible();
  await expect(page.getByText("Scheduled 12.500 KG").first()).toBeVisible();
  await expect(page.getByText("Pending 7.500 KG")).toBeVisible();
  await expect(page.getByText("Maximum receivable 8.125 KG")).toBeVisible();
  await expect(page.getByText("INR 206.26").first()).toBeVisible();
  await expect(page.getByText("po.create")).toBeVisible();
  await expect(page.getByText("Approval: Approved")).toBeVisible();
});

test("PO draft buyer replaces lines and schedule with exact decimal strings", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockPoRegister(page);
  await mockPoEditCatalog(page);
  const draft = poDetail(poBeta);
  draft.lines = draft.lines.map(line => ({ ...line, schedule: [{ due_date: "2099-01-10", scheduled_qty: line.ordered_qty, received_qty: "0.000" }] }));
  await page.route(`**/api/v1/procurement/purchase-orders/${poBeta.po_id}`, route => {
    if (route.request().method() === "PUT") return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(draft) });
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(draft) });
  });
  let body: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/procurement/purchase-orders/${poBeta.po_id}`, route => route.request().method() === "PUT" ? (body = route.request().postDataJSON(), route.fallback()) : route.fallback());
  await page.goto(`/app/procurement/purchase-orders/${poBeta.po_id}`);
  await page.getByRole("button", { name: "Edit draft" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit purchase order" });
  await dialog.getByLabel("Ordered quantity for line 1").fill("12.500");
  await dialog.getByLabel("Unit price for line 1").fill("8.25");
  await dialog.getByRole("button", { name: "Add delivery" }).first().click();
  await dialog.getByLabel("Delivery 1 date for line 1").fill("2099-01-10");
  await dialog.getByLabel("Delivery 1 quantity for line 1").fill("5.000");
  await dialog.getByLabel("Delivery 2 date for line 1").fill("2099-01-20");
  await dialog.getByLabel("Delivery 2 quantity for line 1").fill("7.500");
  await dialog.getByRole("button", { name: "Save draft" }).click();
  expect(body).toMatchObject({ payment_term_id: null, lines: [{ item_id: poLine.item_id, ordered_qty: "12.500", unit_price: "8.25", due_date: null, schedule: [{ due_date: "2099-01-10", quantity: "5.000" }, { due_date: "2099-01-20", quantity: "7.500" }] }, { item_id: draft.lines[1].item_id, due_date: "2099-01-10", schedule: [] }] });
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Edit draft" })).toBeFocused();
});

test("PO draft editor adds a distinct active item with payment term and tax code", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockPoRegister(page);
  await mockPoEditCatalog(page);
  let body: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/procurement/purchase-orders/${poBeta.po_id}`, route => route.request().method() === "PUT" ? (body = route.request().postDataJSON(), route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(poDetail(poBeta)) })) : route.fallback());
  await page.goto(`/app/procurement/purchase-orders/${poBeta.po_id}`);
  await page.getByRole("button", { name: "Edit draft" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit purchase order" });
  await dialog.getByLabel("Payment term").selectOption("99999999-9999-9999-9999-999999999999");
  await dialog.getByRole("button", { name: "Add line" }).click();
  await dialog.getByLabel("Item for line 3").selectOption("cccccccc-3333-3333-3333-333333333333");
  await dialog.getByLabel("Ordered quantity for line 3").fill("2.250");
  await dialog.getByLabel("Unit price for line 3").fill("0.00");
  await dialog.getByLabel("Tax code for line 3").selectOption("aaaaaaaa-1111-1111-1111-111111111111");
  await dialog.getByLabel("Delivery due for line 3").fill("2099-02-10");
  await dialog.getByRole("button", { name: "Save draft" }).click();
  expect(body).toMatchObject({ payment_term_id: "99999999-9999-9999-9999-999999999999", lines: [expect.any(Object), expect.any(Object), { item_id: "cccccccc-3333-3333-3333-333333333333", ordered_qty: "2.250", unit_price: "0.00", tax_code_id: "aaaaaaaa-1111-1111-1111-111111111111", due_date: "2099-02-10", schedule: [] }] });
});

test("PO lifecycle exposes only backend-supported actions for buyer and state", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockPoRegister(page);
  await page.goto(`/app/procurement/purchase-orders/${poBeta.po_id}`);
  await expect(page.getByRole("button", { name: "Edit draft" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Submit for approval" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel order" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Close order" })).toHaveCount(0);
  await page.goto(`/app/procurement/purchase-orders/${poAlpha.po_id}`);
  await expect(page.getByRole("button", { name: "Close order" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel order" })).toHaveCount(0);
});

test("PO cancel conflict preserves the order and shows the backend reason", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockPoRegister(page);
  await page.route(`**/api/v1/procurement/purchase-orders/${poBeta.po_id}/cancel`, route => route.fulfill({ status: 409, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "Goods were already received on this order" }) }));
  await page.goto(`/app/procurement/purchase-orders/${poBeta.po_id}`);
  await page.getByRole("button", { name: "Cancel order" }).click();
  const dialog = page.getByRole("dialog", { name: "Cancel purchase order" });
  await dialog.getByRole("button", { name: "Confirm cancellation" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Goods were already received on this order");
  await expect(page.getByRole("heading", { name: poBeta.po_no })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Confirm cancellation" })).toBeEnabled();
});

test("PO procurement reader can inspect a draft without buyer lifecycle controls", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  await mockPoRegister(page);
  await page.goto(`/app/procurement/purchase-orders/${poBeta.po_id}`);
  await expect(page.getByRole("heading", { name: poBeta.po_no })).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit draft" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Submit for approval" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Cancel order" })).toHaveCount(0);
});

test("PO submission keeps the dialog and backend rule detail on validation failure", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockPoRegister(page);
  let submitted = 0;
  await page.route(`**/api/v1/procurement/purchase-orders/${poBeta.po_id}/submit`, route => { submitted += 1; return route.fulfill({ status: 422, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "Every line needs a tax code before the order is submitted" }) }); });
  await page.goto(`/app/procurement/purchase-orders/${poBeta.po_id}`);
  await page.getByRole("button", { name: "Submit for approval" }).click();
  const dialog = page.getByRole("dialog", { name: "Submit purchase order" });
  await dialog.getByRole("button", { name: "Confirm submission" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Every line needs a tax code");
  await expect(dialog.getByRole("button", { name: "Confirm submission" })).toBeEnabled();
  expect(submitted).toBe(1);
  await expect(page.getByRole("heading", { name: poBeta.po_no })).toBeVisible();
});

test("PO draft submission sends one POST and shows the returned approval state", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockPoRegister(page);
  let submitted = 0;
  await page.route(`**/api/v1/procurement/purchase-orders/${poBeta.po_id}/submit`, route => { submitted += 1; return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...poDetail(poBeta), status: "PENDING_APPROVAL", approval_id: "77777777-1111-1111-1111-111111111111" }) }); });
  await page.goto(`/app/procurement/purchase-orders/${poBeta.po_id}`);
  await page.getByRole("button", { name: "Submit for approval" }).click();
  const dialog = page.getByRole("dialog", { name: "Submit purchase order" });
  await dialog.getByRole("button", { name: "Confirm submission" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("Pending approval", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit draft" })).toHaveCount(0);
  expect(submitted).toBe(1);
});

test("PO close uses the supported POST and removes lifecycle controls after success", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockPoRegister(page);
  let closed = 0;
  await page.route(`**/api/v1/procurement/purchase-orders/${poAlpha.po_id}/close`, route => { closed += 1; return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...poDetail(poAlpha), status: "CLOSED" }) }); });
  await page.goto(`/app/procurement/purchase-orders/${poAlpha.po_id}`);
  await page.getByRole("button", { name: "Close order" }).click();
  await page.getByRole("dialog", { name: "Close purchase order" }).getByRole("button", { name: "Confirm closure" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Close order" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: poAlpha.po_no })).toBeFocused();
  expect(closed).toBe(1);
});

test("PO cancellation sends one POST and retains the returned cancelled detail", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockPoRegister(page);
  let cancelled = 0;
  await page.route(`**/api/v1/procurement/purchase-orders/${poBeta.po_id}/cancel`, route => { cancelled += 1; return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...poDetail(poBeta), status: "CANCELLED" }) }); });
  await page.goto(`/app/procurement/purchase-orders/${poBeta.po_id}`);
  await page.getByRole("button", { name: "Cancel order" }).click();
  await page.getByRole("dialog", { name: "Cancel purchase order" }).getByRole("button", { name: "Confirm cancellation" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByText("Cancelled", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel order" })).toHaveCount(0);
  expect(cancelled).toBe(1);
});

test("PO draft edit conflict preserves every input and the backend reason", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockPoRegister(page);
  await mockPoEditCatalog(page);
  await page.route(`**/api/v1/procurement/purchase-orders/${poBeta.po_id}`, route => route.request().method() === "PUT" ? route.fulfill({ status: 409, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "The order is open" }) }) : route.fallback());
  await page.goto(`/app/procurement/purchase-orders/${poBeta.po_id}`);
  await page.getByRole("button", { name: "Edit draft" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit purchase order" });
  await dialog.getByLabel("Unit price for line 1").fill("9.75");
  await dialog.getByRole("button", { name: "Save draft" }).click();
  await expect(dialog.getByRole("alert")).toContainText("The order is open");
  await expect(dialog.getByLabel("Unit price for line 1")).toHaveValue("9.75");
  await expect(dialog.getByRole("button", { name: "Save draft" })).toBeEnabled();
});

test("PO editor rejects schedule quantities that do not equal the ordered decimal quantity", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: rfqProfile });
  await mockPoRegister(page);
  await mockPoEditCatalog(page);
  let writes = 0;
  await page.route(`**/api/v1/procurement/purchase-orders/${poBeta.po_id}`, route => route.request().method() === "PUT" ? (writes += 1, route.fallback()) : route.fallback());
  await page.goto(`/app/procurement/purchase-orders/${poBeta.po_id}`);
  await page.getByRole("button", { name: "Edit draft" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit purchase order" });
  await dialog.getByRole("button", { name: "Add delivery" }).first().click();
  await dialog.getByLabel("Delivery 2 date for line 1").fill("2099-01-20");
  await dialog.getByLabel("Delivery 2 quantity for line 1").fill("1.000");
  await dialog.getByRole("button", { name: "Save draft" }).click();
  await expect(dialog.getByRole("alert")).toContainText("delivery quantities that exactly match");
  expect(writes).toBe(0);
});

test("PO receiving and history failures show request IDs without inventing state", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: indentProfile });
  await mockPoRegister(page);
  await page.route(`**/api/v1/procurement/purchase-orders/${poBeta.po_id}/receiving`, route => route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-po-receiving" }, contentType: "application/json", body: JSON.stringify({ detail: "Receiving unavailable" }) }));
  await page.route(`**/api/v1/procurement/purchase-orders/${poBeta.po_id}/history`, route => route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-po-history" }, contentType: "application/json", body: JSON.stringify({ detail: "History unavailable" }) }));
  await page.goto(`/app/procurement/purchase-orders/${poBeta.po_id}`);
  await expect(page.getByText("Request ID: req-po-receiving")).toBeVisible();
  await expect(page.getByText("Request ID: req-po-history")).toBeVisible();
  await expect(page.getByText("Approval: Approved")).toHaveCount(0);
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }]) {
  test(`PO detail and editor fit ${viewport.width}px with keyboard-safe dialog`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: rfqProfile });
    await mockPoRegister(page);
    await mockPoEditCatalog(page);
    await page.goto(`/app/procurement/purchase-orders/${poBeta.po_id}`);
    await expect(page.getByRole("heading", { name: "Order lines" })).toBeVisible();
    await page.getByRole("button", { name: "Edit draft" }).click();
    const dialog = page.getByRole("dialog", { name: "Edit purchase order" });
    await expect(dialog.getByRole("button", { name: "Close purchase order dialog" })).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(dialog.getByRole("button", { name: "Save draft" })).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Edit draft" })).toBeFocused();
  });
}
