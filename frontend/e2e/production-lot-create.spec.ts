import { expect, test, type Page } from "@playwright/test";
import { cors, grantedProfile, mockSignInAndMe, plantAlpha, plantBeta } from "./support/fixtures";

test.use({ serviceWorkers: "block" });

const path = "/app/manufacturing/lots/new";
const webOrigin = "http://127.0.0.1:5174";
const origins = new Set([webOrigin, "http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:3001", "http://127.0.0.1:3001"]);
const orderId = "dddddddd-1111-1111-1111-111111111111";
const compoundOrderId = "dddddddd-2222-2222-2222-222222222222";
const storeId = "eeeeeeee-1111-1111-1111-111111111111";
const order = { work_order_id: orderId, work_order_no: "WO-001", unit_id: plantAlpha.unit_id,
  item_id: "aaaaaaaa-1111-1111-1111-111111111111", item_code: "FG-01", item_name: "Bearing",
  bom_id: "bbbbbbbb-1111-1111-1111-111111111111", bom_version: 1,
  route_id: "cccccccc-1111-1111-1111-111111111111", route_code: "R-01",
  planned_qty: "200.000", started_qty: "20.000", packed_qty: "0.000", status: "RELEASED",
  planned_start: null, planned_finish: null };
const component = { component_item_id: "aaaaaaaa-2222-2222-2222-222222222222", component_code: "RM-01",
  component_name: "Material", kind: "MATERIAL", quantity_per: "1.000", scrap_factor: "0.000", required_qty: "200.000" };
const lot = { prod_lot_id: "ffffffff-1111-1111-1111-111111111111", lot_no: "PL-001", unit_id: plantAlpha.unit_id,
  work_order_id: orderId, work_order_no: "WO-001", item_id: order.item_id, item_code: order.item_code,
  route_code: "R-01", stage_code: "MIX", stage_name: "Mix", is_qa_gate: false, is_end: false,
  quantity: "12.340", status: "IN_PROCESS", created_at: "2026-11-06T10:00:00Z", sources: [], moves: [], packing: null };
const headers = { ...cors, "access-control-expose-headers": "X-Request-ID" };
const json = (body: unknown, status = 200, extra: Record<string, string> = {}) => ({ status, headers: { ...headers, ...extra }, contentType: "application/json", body: JSON.stringify(body) });
type Options = { permission?: boolean; module?: boolean; mode?: "success" | "pending" | "conflict" | "server" | "transport" };

async function setup(page: Page, options: Options = {}) {
  const posts: unknown[] = [], unexpected: string[] = [], blocked: string[] = [];
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  if (options.mode !== "pending") release();
  await page.route("**/*", route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith("/api/")) {
      unexpected.push(`${route.request().method()} ${url.pathname}`);
      return route.fulfill(json({ detail: "Unmocked API" }, 404));
    }
    return url.origin === webOrigin ? route.continue() : route.abort();
  });
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile,
    modules: options.module === false ? grantedProfile.modules.filter(x => x !== "manufacturing") : [...grantedProfile.modules, "manufacturing"],
    permissions: options.permission === false ? grantedProfile.permissions.filter(x => x !== "route.move") : [...grantedProfile.permissions, "route.move"],
    unit_ids: [plantAlpha.unit_id], all_plants: false,
  } }, [{ ...plantAlpha, gstin: null, state_code: null }, { ...plantBeta, gstin: null, state_code: null }]);
  await page.route(/\/api\/v1\/manufacturing\/work-orders(?:\?.*)?$/, route => {
    if (route.request().method() !== "GET") return route.fulfill(json({}, 405));
    const orders = [order, { ...order, work_order_id: compoundOrderId, work_order_no: "WO-002", status: "IN_PROGRESS" },
      { ...order, work_order_id: "dddddddd-3333-3333-3333-333333333333", work_order_no: "WO-PLANNED", status: "PLANNED" },
      { ...order, work_order_id: "dddddddd-4444-4444-4444-444444444444", work_order_no: "WO-OUTSIDE", unit_id: plantBeta.unit_id }];
    return route.fulfill(json({ items: orders, total: orders.length, limit: 200, offset: 0 }));
  });
  await page.route(/\/api\/v1\/manufacturing\/work-orders\/[0-9a-f-]+$/, route => {
    const id = new URL(route.request().url()).pathname.split("/").pop();
    return route.fulfill(json({ ...order, work_order_id: id, work_order_no: id === compoundOrderId ? "WO-002" : "WO-001",
      status: id === compoundOrderId ? "IN_PROGRESS" : "RELEASED", materials: id === compoundOrderId ? [{ ...component, kind: "COMPOUND" }] : [component] }));
  });
  await page.route(/\/api\/v1\/org\/stores(?:\?.*)?$/, route => route.fulfill(json({ items: [
    { store_id: storeId, unit_id: plantAlpha.unit_id, code: "RM-A", name: "Active store", store_type: "RAW_MATERIAL", active: true },
    { store_id: "eeeeeeee-2222-2222-2222-222222222222", unit_id: plantAlpha.unit_id, code: "OLD", name: "Inactive", store_type: "RAW_MATERIAL", active: false },
    { store_id: "eeeeeeee-3333-3333-3333-333333333333", unit_id: plantBeta.unit_id, code: "OTHER", name: "Other plant", store_type: "RAW_MATERIAL", active: true },
  ], total: 3, limit: 200, offset: 0 })));
  await page.route(/\/api\/v1\/manufacturing\/lots(?:\?.*)?$/, async route => {
    const request = route.request();
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    if (request.method() !== "POST") return route.fulfill(json({ items: [], total: 0, limit: 200, offset: 0 }));
    posts.push(request.postDataJSON());
    await gate;
    if (options.mode === "transport") return route.abort("failed");
    if (options.mode === "conflict") return route.fulfill(json({ detail: "Not enough stock of RM-01" }, 409, { "X-Request-ID": "req-conflict" }));
    if (options.mode === "server") return route.fulfill(json({ detail: "Unavailable" }, 503, { "X-Request-ID": "req-unknown" }));
    return route.fulfill(json(lot, 201));
  });
  // Runs first, including before auth mocks: no outside origin is contacted.
  await page.route("**/*", route => {
    const origin = new URL(route.request().url()).origin;
    if (origins.has(origin)) return route.fallback();
    blocked.push(origin); return route.abort();
  });
  return { posts, unexpected, blocked, release };
}

async function fill(page: Page, id = orderId) {
  await page.goto(path);
  await expect(page.getByRole("heading", { name: "New production lot" })).toBeVisible();
  await page.getByLabel("Work order", { exact: true }).selectOption(id);
  await page.getByLabel("Quantity", { exact: true }).fill("12.340");
  if (id === orderId) await page.getByLabel("Issue store").selectOption(storeId);
}
const confirm = (page: Page) => page.getByLabel(/I understand.*stock.*accounting/i);
const submit = (page: Page) => page.getByRole("button", { name: "Start production lot" });
function noLeaks(state: Awaited<ReturnType<typeof setup>>) { expect(state.unexpected).toEqual([]); expect(state.blocked).toEqual([]); }

test("permission and module gate direct access", async ({ page }) => {
  const state = await setup(page, { permission: false });
  await page.goto(path);
  await expect(page.getByText(/route\.move.*required/i)).toBeVisible();
  expect(state.posts).toEqual([]); noLeaks(state);
});

test("selects eligible scoped orders and active plant stores and sends the exact decimal payload after confirmation", async ({ page }) => {
  const state = await setup(page);
  await fill(page);
  await expect(page.getByLabel("Work order", { exact: true }).locator("option")).not.toContainText(["WO-PLANNED", "WO-OUTSIDE"]);
  await expect(page.getByLabel("Issue store").locator("option")).toHaveCount(2);
  await expect(page.getByText(/consum.*stock/i).first()).toBeVisible();
  await expect(page.getByText(/accounting/i).first()).toBeVisible();
  await submit(page).click();
  expect(state.posts).toEqual([]);
  await confirm(page).check(); await submit(page).click();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0]).toEqual({ work_order_id: orderId, quantity: "12.340", store_id: storeId });
  await expect(page.getByText(/PL-001/).first()).toBeVisible(); noLeaks(state);
});

test("a compound-only in-progress order needs no issue store", async ({ page }) => {
  const state = await setup(page);
  await fill(page, compoundOrderId);
  await expect(page.getByLabel("Issue store")).toHaveCount(0);
  await confirm(page).check(); await submit(page).click();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0]).toEqual({ work_order_id: compoundOrderId, quantity: "12.340", store_id: null }); noLeaks(state);
});

test("a pending request cannot be submitted twice", async ({ page }) => {
  const state = await setup(page, { mode: "pending" });
  await fill(page); await confirm(page).check(); await submit(page).click();
  await expect.poll(() => state.posts.length).toBe(1);
  await expect(submit(page)).toBeDisabled();
  await submit(page).evaluate(button => button.form?.requestSubmit());
  expect(state.posts).toHaveLength(1); state.release();
  await expect(page.getByText(/PL-001/).first()).toBeVisible(); noLeaks(state);
});

test("conflict is a definite rejection and can be corrected", async ({ page }) => {
  const state = await setup(page, { mode: "conflict" });
  await fill(page); await confirm(page).check(); await submit(page).click();
  await expect(page.getByRole("alert")).toContainText("Not enough stock of RM-01");
  await expect(page.getByText("Request ID: req-conflict")).toBeVisible();
  await expect(submit(page)).toBeEnabled(); expect(state.posts).toHaveLength(1); noLeaks(state);
});

for (const mode of ["server", "transport"] as const) test(`${mode} outcome remains blocked across reload until register checked`, async ({ page }) => {
  const state = await setup(page, { mode });
  await fill(page); await confirm(page).check(); await submit(page).click();
  await expect(page.getByRole("alert")).toContainText(/outcome unknown/i);
  await expect(submit(page)).toBeDisabled();
  if (mode === "server") await expect(page.getByText("Request ID: req-unknown")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("alert")).toContainText(/outcome unknown/i);
  await expect(page.getByLabel("Quantity", { exact: true })).toHaveValue("12.340");
  await expect(page.getByRole("link", { name: /open production lot register/i })).toHaveAttribute("target", "_blank");
  await page.getByRole("button", { name: /I checked the production lot register/i }).click();
  await expect(submit(page)).toBeEnabled(); expect(state.posts).toHaveLength(1); noLeaks(state);
});

test("keyboard access and narrow viewport keep the form usable", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const state = await setup(page);
  await fill(page);
  const checkbox = confirm(page); await checkbox.focus(); await expect(checkbox).toBeFocused(); await page.keyboard.press("Space");
  await expect(checkbox).toBeChecked();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await submit(page).focus(); await page.keyboard.press("Enter");
  await expect.poll(() => state.posts.length).toBe(1); noLeaks(state);
});
