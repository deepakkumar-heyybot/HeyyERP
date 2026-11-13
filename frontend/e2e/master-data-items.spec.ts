import { expect, test } from '@playwright/test';
import { cors, InvitationResponse, mockInvitationAttempt, submitInvitationSignIn, submitPasswordRecovery, totpUri, backupCodes, openAuthenticatorSetup, mockTwoFactorSignIn, submitTwoFactorSignIn, MeFixture, UnitFixture, CountFixtures, BootstrapResponse, BootstrapFixtures, defaultUnits, mockSignInAndMe, grantedProfile, approvalOne, approvalTwo, mockApprovals, approvalDetail, mockApprovalDetail, notificationOne, notificationTwo, mockNotifications, publicApprovalToken, publicApproval, mockPublicApproval, plantAlpha, plantBeta, departmentProduction, departmentFinishing, mockDepartments, storeRaw, storeFinished, mockStores, paymentTermNet30, taxCodeGst18, categoryRubber, categoryNatural, uomKilogram, mockReferenceData, vendorOrbit, mockVendors, mockCustomerDetail, customerAcme, mockCustomers, mockVendorDetail, itemRubber, mockItems, itemFinished, itemPolicy, itemSpec, itemBom, itemChange, itemPart, mockItemDetail, mockBoms, bomDraft, bomPrevious, mockBomDetail, routeMouldV2, routeMouldV1, routeMetal, mockRoutes, indentProfile, indentAlpha, indentBeta, mockIndentRegister, indentDetailFixture, indentPutCors, mockOwnedIndent, rfqProfile, rfqAlpha, rfqBeta, rfqLineFixture, rfqInvitations, rfqDetailFixture, mockRfqRegister, poAlpha, poBeta, poLine, poDetail, mockPoRegister, mockPoEditCatalog, inventoryProfile, grnAlpha, grnBeta, grnDetail, mockGrnRegister, mockGrnCreationSources, stockAlpha, stockBeta, stockTrace, mockStockSources, issueProfile, issuePlan, postedIssue, issueSummary, mockIssueHistory } from './support/fixtures';

test("item readers see code, type, UoM, category, HSN, and status without write actions", async ({ page }) => {
  const reader = { ...grantedProfile, permissions: grantedProfile.permissions.filter(permission => permission !== "item.manage") };
  await mockSignInAndMe(page, { status: 200, body: reader }, [plantAlpha]);
  await mockItems(page);
  await page.goto("/app/master-data/engineering");
  const table = page.getByRole("table", { name: "Item results" });
  await expect(table.getByText(itemRubber.code)).toBeVisible();
  await expect(table.getByText(itemRubber.name)).toBeVisible();
  await expect(table.getByText("Raw material")).toBeVisible();
  await expect(table.getByText("KG")).toBeVisible();
  await expect(table.getByText(categoryRubber.name)).toBeVisible();
  await expect(table.getByText(itemRubber.hsn_code)).toBeVisible();
  await expect(page.getByRole("button", { name: "Create item" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /delete/i })).toHaveCount(0);
});

test("item search, type, category, status, and page use only backend-supported parameters", async ({ page }) => {
  const items = Array.from({ length: 22 }, (_, index) => ({ ...itemRubber, item_id: `${String(index + 1).padStart(8, "0")}-5555-5555-5555-555555555555`, code: `RM-${index + 1}`, name: `Rubber item ${index + 1}`, active: index !== 21 }));
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  const requests = await mockItems(page, items);
  await page.goto("/app/master-data/engineering");
  await expect(page.getByText("Showing 1–20 of 22 records")).toBeVisible();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect.poll(() => requests.some(url => url.includes("offset=20") && url.includes("limit=20"))).toBe(true);
  await page.getByLabel("Search items").fill("Rubber item 2");
  await page.getByLabel("Item type").selectOption("RM");
  await page.getByLabel("Category filter").selectOption(categoryRubber.category_id);
  await page.getByLabel("Status filter").selectOption("true");
  await expect(page).not.toHaveURL(/page=2/);
  await expect.poll(() => requests.some(url => { const params = new URL(url).searchParams; return params.get("q") === "Rubber item 2" && params.get("item_type") === "RM" && params.get("category_id") === categoryRubber.category_id && params.get("active") === "true" && params.get("offset") === "0"; })).toBe(true);
  expect(requests.every(url => !new URL(url).searchParams.has("unit_id"))).toBe(true);
});

test("an item manager creates a valid item with referenced UoM and category IDs", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "item.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockItems(page);
  let created: Record<string, unknown> | null = null;
  await page.route("**/api/v1/product/items", route => {
    if (route.request().method() !== "POST") return route.fallback();
    created = route.request().postDataJSON();
    return route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify({ ...created, item_id: "ffffffff-2222-2222-2222-222222222222", active: true }) });
  });
  await page.goto("/app/master-data/engineering");
  await page.getByRole("button", { name: "Create item" }).click();
  await expect(page).toHaveURL(/\/app\/master-data\/engineering\/items\/new$/);
  await page.getByLabel("Item code").fill("rm-rubber-02");
  await page.getByLabel("Item name").fill("Processed rubber compound");
  await page.getByLabel("Item type").selectOption("RM");
  await page.getByLabel("Unit of measure").selectOption("KG");
  await page.getByLabel("Category").selectOption(categoryRubber.category_id);
  await page.getByLabel("HSN code").fill("40059990");
  await page.getByLabel("Shelf life (days)").fill("180");
  await page.getByRole("button", { name: "Save item" }).click();
  expect(created).toEqual({ code: "RM-RUBBER-02", name: "Processed rubber compound", item_type: "RM", uom_code: "KG", category_id: categoryRubber.category_id, hsn_code: "40059990", shelf_life_days: 180, route_code: null });
  await expect(page).toHaveURL(/\/app\/master-data\/engineering\/items\/ffffffff-2222-2222-2222-222222222222$/);
});

test("item creation only offers the route field for WIP and finished goods", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "item.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockItems(page);
  await page.goto("/app/master-data/engineering/items/new");
  await expect(page.getByLabel("Production route code")).toHaveCount(0);
  await page.getByLabel("Item type").selectOption("FG");
  await expect(page.getByLabel("Production route code")).toBeVisible();
  await page.getByLabel("Item type").selectOption("SPARE");
  await expect(page.getByLabel("Production route code")).toHaveCount(0);
});

test("item validation errors keep the creation form and selected references", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "item.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockItems(page);
  await page.route("**/api/v1/product/items", route => route.request().method() === "POST" ? route.fulfill({ status: 422, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "Item code or category is invalid" }) }) : route.fallback());
  await page.goto("/app/master-data/engineering/items/new");
  await page.getByLabel("Item code").fill("RM-BAD");
  await page.getByLabel("Item name").fill("Invalid item");
  await page.getByLabel("Unit of measure").selectOption("KG");
  await page.getByLabel("Category").selectOption(categoryRubber.category_id);
  await page.getByRole("button", { name: "Save item" }).click();
  await expect(page.getByRole("alert")).toContainText("Item code or category is invalid");
  await expect(page.getByLabel("Item name")).toHaveValue("Invalid item");
  await expect(page.getByLabel("Category")).toHaveValue(categoryRubber.category_id);
});

test("item list has loading, empty, and request-ID error recovery", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockReferenceData(page);
  let attempts = 0;
  await page.route("**/api/v1/product/items?**", async route => { attempts += 1; await new Promise(resolve => setTimeout(resolve, 200)); return route.fulfill({ status: attempts === 1 ? 503 : 200, headers: { ...cors, ...(attempts === 1 ? { "X-Request-ID": "req-items-503" } : {}) }, contentType: "application/json", body: JSON.stringify(attempts === 1 ? { detail: "Item master is temporarily unavailable" } : { items: [], total: 0, limit: 20, offset: 0 }) }); });
  await page.goto("/app/master-data/engineering");
  await expect(page.getByText("Loading items…")).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Item master is temporarily unavailable");
  await expect(page.getByText("Request ID: req-items-503")).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "No items found" })).toBeVisible();
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }]) {
  test(`item results preserve identity and status without overflow at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
    await mockItems(page);
    await page.goto("/app/master-data/engineering");
    if (viewport.width === 360) {
      const cards = page.getByRole("list", { name: "Item results" });
      await expect(cards.getByText(itemRubber.name)).toBeVisible();
      await expect(cards.getByText("Raw material")).toBeVisible();
      await expect(cards.getByText("Active", { exact: true })).toBeVisible();
    } else {
      await expect(page.getByRole("table", { name: "Item results" })).toBeVisible();
      await expect(page.getByText("Scroll table horizontally to view more columns.")).toBeVisible();
      const scrollingResults = page.getByRole("region", { name: "Scrollable item results" });
      await scrollingResults.focus();
      await page.keyboard.press("ArrowRight");
      await expect.poll(() => scrollingResults.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
    }
    const noDocumentOverflow = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);
    expect(noDocumentOverflow).toBe(true);
  });
}

test("item detail reader sees master and contract-backed child sections without write or removal controls", async ({ page }) => {
  const reader = { ...grantedProfile, permissions: grantedProfile.permissions.filter(permission => permission !== "item.manage" && permission !== "customer.manage") };
  await mockSignInAndMe(page, { status: 200, body: reader }, [plantAlpha]);
  await mockItemDetail(page);
  await page.goto(`/app/master-data/engineering/items/${itemFinished.item_id}`);
  await expect(page.getByRole("heading", { name: itemFinished.name })).toBeVisible();
  await expect(page.getByText(itemFinished.code).first()).toBeVisible();
  await expect(page.getByText("ASSY-01")).toBeVisible();
  const sections = [
    ["Plant policies", "125.500"], ["Specifications", "Hardness"], ["Effective BOM", "2.500000"],
    ["Engineering changes", "Updated rubber formulation"], ["Where used", itemBom.item_code], ["Customer parts", itemPart.customer_part_no],
  ];
  for (const [name, value] of sections) {
    await page.getByRole("link", { name }).click();
    await expect(page.getByText(value).first()).toBeVisible();
  }
  await expect(page.getByRole("button", { name: /edit|set|add|delete|remove|deactivate/i })).toHaveCount(0);
});

test("item manager edits only mutable master fields and receives updated backend state", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockItemDetail(page);
  let patch: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/product/items/${itemFinished.item_id}`, route => route.request().method() === "PATCH" ? (patch = route.request().postDataJSON(), route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...itemFinished, ...patch }) })) : route.fallback());
  await page.goto(`/app/master-data/engineering/items/${itemFinished.item_id}`);
  await page.getByRole("button", { name: "Edit item" }).click();
  await expect(page.getByLabel("Item type")).toHaveCount(0);
  await expect(page.getByLabel("Unit of measure")).toHaveCount(0);
  await page.getByLabel("Item name").fill("Revised finished mount");
  await page.getByRole("button", { name: "Save changes" }).click();
  expect(patch).toEqual({ name: "Revised finished mount", category_id: itemFinished.category_id, hsn_code: itemFinished.hsn_code, shelf_life_days: itemFinished.shelf_life_days, route_code: "ASSY-01" });
  await expect(page.getByRole("heading", { name: "Revised finished mount" })).toBeVisible();
});

test("item deactivation is confirmed and 409 blockers preserve active status", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockItemDetail(page);
  await page.route(`**/api/v1/product/items/${itemFinished.item_id}`, route => route.request().method() === "PATCH" ? route.fulfill({ status: 409, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "This item is a component of a live bill of material; revise those first" }) }) : route.fallback());
  await page.goto(`/app/master-data/engineering/items/${itemFinished.item_id}`);
  await page.getByRole("button", { name: "Deactivate item" }).click();
  await expect(page.getByRole("dialog", { name: "Deactivate this item?" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Deactivate this item?" })).toHaveCount(0);
  await page.getByRole("button", { name: "Deactivate item" }).click();
  await page.getByRole("button", { name: "Confirm deactivation" }).click();
  await expect(page.getByRole("alert")).toContainText("component of a live bill of material");
  await expect(page.getByText("Active", { exact: true }).first()).toBeVisible();
});

test("item policy form offers only active authorized plants", async ({ page }) => {
  const plantOutside = { ...plantBeta, unit_id: "22222222-9999-9999-9999-999999999999", code: "VR-03", active: true };
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha, plantBeta, plantOutside]);
  await mockItemDetail(page);
  await page.goto(`/app/master-data/engineering/items/${itemFinished.item_id}/policies`);
  await page.getByRole("button", { name: "Set plant policy" }).click();
  await expect(page.getByLabel("Plant").locator("option")).toHaveCount(2);
  await expect(page.getByLabel("Plant").locator("option", { hasText: plantAlpha.name })).toHaveAttribute("value", plantAlpha.unit_id);
});

test("item policy upsert sends decimal strings and a store from the chosen plant", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockItemDetail(page);
  let body: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/product/items/${itemFinished.item_id}/policies/${plantAlpha.unit_id}`, route => route.request().method() === "PUT" ? (body = route.request().postDataJSON(), route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...itemPolicy, ...body }) })) : route.fallback());
  await page.goto(`/app/master-data/engineering/items/${itemFinished.item_id}/policies`);
  await page.getByRole("button", { name: "Set plant policy" }).click();
  await page.getByLabel("Plant").selectOption(plantAlpha.unit_id);
  await page.getByLabel("Reorder point").fill("150.125");
  await page.getByLabel("Reorder quantity").fill("600.500");
  await page.getByLabel("Default store").selectOption(itemPolicy.default_store_id);
  await page.getByRole("button", { name: "Save policy" }).click();
  expect(body).toEqual({ reorder_point: "150.125", reorder_qty: "600.500", default_store_id: itemPolicy.default_store_id });
  await expect(page.getByText("150.125")).toBeVisible();
});

test("item specification upsert preserves decimal strings and form on 422", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockItemDetail(page);
  let body: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/product/items/${itemFinished.item_id}/specs/Hardness`, route => route.request().method() === "PUT" ? (body = route.request().postDataJSON(), route.fulfill({ status: 422, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "target_value must lie within min_value and max_value" }) })) : route.fallback());
  await page.goto(`/app/master-data/engineering/items/${itemFinished.item_id}/specs`);
  await page.getByRole("button", { name: "Edit Hardness" }).click();
  await page.getByLabel("Target value").fill("75.250000");
  await page.getByRole("button", { name: "Save specification" }).click();
  expect(body).toMatchObject({ target_value: "75.250000", min_value: "60.000000", max_value: "70.000000" });
  await expect(page.getByRole("alert")).toContainText("target_value must lie within");
  await expect(page.getByLabel("Target value")).toHaveValue("75.250000");
  await expect(page.getByRole("button", { name: /remove|delete/i })).toHaveCount(0);
});

test("item specification creation uses the supported upsert endpoint without numeric conversion", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockItemDetail(page);
  let body: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/product/items/${itemFinished.item_id}/specs/Compression`, route => route.request().method() === "PUT" ? (body = route.request().postDataJSON(), route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ name: "Compression", ...body }) })) : route.fallback());
  await page.goto(`/app/master-data/engineering/items/${itemFinished.item_id}/specs`);
  await page.getByRole("button", { name: "Add specification" }).click();
  await page.getByLabel("Specification name").fill("Compression");
  await page.getByLabel("Target value").fill("0.125500");
  await page.getByLabel("Unit", { exact: true }).fill("mm");
  await page.getByRole("button", { name: "Save specification" }).click();
  expect(body).toEqual({ unit: "mm", target_value: "0.125500", min_value: null, max_value: null, text_value: null, note: null });
  await expect(page.getByText("Compression", { exact: true })).toBeVisible();
});

test("customer part creation requires customer.manage and a finished or WIP item", async ({ page }) => {
  const customerManager = { ...grantedProfile, permissions: [...grantedProfile.permissions.filter(permission => permission !== "item.manage"), "customer.manage"] };
  await mockSignInAndMe(page, { status: 200, body: customerManager }, [plantAlpha]);
  await mockItemDetail(page);
  let body: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/product/items/${itemFinished.item_id}/customer-parts`, route => route.request().method() === "POST" ? (body = route.request().postDataJSON(), route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify({ ...itemPart, ...body }) })) : route.fallback());
  await page.goto(`/app/master-data/engineering/items/${itemFinished.item_id}/customer-parts`);
  await page.getByRole("button", { name: "Add customer part" }).click();
  await page.getByLabel("Customer", { exact: true }).selectOption(itemPart.customer_id);
  await page.getByLabel("Customer part number").fill("OEM-MOUNT-43");
  await page.getByRole("button", { name: "Save customer part" }).click();
  expect(body).toEqual({ customer_id: itemPart.customer_id, customer_part_no: "OEM-MOUNT-43" });
  await expect(page.getByText("OEM-MOUNT-43")).toBeVisible();
});

test("customer part action is unavailable for raw material even with customer.manage", async ({ page }) => {
  const customerManager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "customer.manage"] };
  await mockSignInAndMe(page, { status: 200, body: customerManager }, [plantAlpha]);
  await mockItemDetail(page, itemRubber);
  await page.goto(`/app/master-data/engineering/items/${itemRubber.item_id}/customer-parts`);
  await expect(page.getByText(itemPart.customer_part_no)).toBeVisible();
  await expect(page.getByRole("button", { name: "Add customer part" })).toHaveCount(0);
});

test("item effective BOM 404 is an empty state and server failures retain request IDs", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockItemDetail(page);
  await page.route(`**/api/v1/product/items/${itemFinished.item_id}/effective-bom`, route => route.fulfill({ status: 404, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "Effective bill of material not found" }) }));
  await page.route(`**/api/v1/product/items/${itemFinished.item_id}/specs`, route => route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-spec-503" }, contentType: "application/json", body: JSON.stringify({ detail: "Specifications unavailable" }) }));
  await page.goto(`/app/master-data/engineering/items/${itemFinished.item_id}/effective-bom`);
  await expect(page.getByRole("heading", { name: "No effective BOM" })).toBeVisible();
  await page.getByRole("link", { name: "Specifications" }).click();
  await expect(page.getByRole("alert")).toContainText("Specifications unavailable");
  await expect(page.getByText("Request ID: req-spec-503")).toBeVisible();
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }]) {
  test(`item detail tabs and master remain usable without document overflow at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
    await mockItemDetail(page);
    await page.goto(`/app/master-data/engineering/items/${itemFinished.item_id}`);
    await expect(page.getByRole("heading", { name: itemFinished.name })).toBeVisible();
    await page.getByRole("link", { name: "Plant policies" }).click();
    await expect(page.getByText("125.500")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
}
