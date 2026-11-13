import { expect, test, type Page } from "@playwright/test";
import { cors, grantedProfile, mockSignInAndMe, plantAlpha, plantBeta } from "./support/fixtures";

test.use({ serviceWorkers: "block" });

const path = "/app/manufacturing/lots";
const webOrigin = "http://127.0.0.1:5174";
const allowedOrigins = new Set([
  webOrigin, "http://localhost:8000", "http://127.0.0.1:8000",
  "http://localhost:3001", "http://127.0.0.1:3001",
]);
const beta = { ...plantBeta, active: true };
const outsideUnit = "33333333-3333-3333-3333-333333333333";
const workOrderId = "dddddddd-1111-1111-1111-111111111111";
const otherWorkOrderId = "dddddddd-2222-2222-2222-222222222222";
const itemId = "aaaaaaaa-1111-1111-1111-111111111111";
const otherItemId = "aaaaaaaa-2222-2222-2222-222222222222";
const customStage = "CUSTOM_CURING";
const statuses = ["IN_PROCESS", "READY", "HOLD", "COMPLETED", "CONSUMED", "SCRAPPED"] as const;

const baseLot = {
  prod_lot_id: "eeeeeeee-1111-1111-1111-111111111111", lot_no: "PL-000",
  unit_id: plantAlpha.unit_id, work_order_id: workOrderId, work_order_no: "WO-000",
  item_id: itemId, item_code: "FG-01", route_code: "ROUTE-FG",
  stage_code: customStage, stage_name: "Custom curing", is_qa_gate: false, is_end: false,
  quantity: "123.450", status: "IN_PROCESS", created_at: "2026-11-05T10:00:00Z",
};
type Lot = typeof baseLot;

const order = {
  work_order_id: workOrderId, work_order_no: "WO-000", unit_id: plantAlpha.unit_id,
  item_id: itemId, item_code: "FG-01", item_name: "Finished bearing",
  bom_id: "bbbbbbbb-1111-1111-1111-111111111111",
  route_id: "cccccccc-1111-1111-1111-111111111111", route_code: "ROUTE-FG",
  planned_qty: "200.000", started_qty: "123.450", packed_qty: "0.000",
  status: "RELEASED", planned_start: null, planned_finish: null,
};
const routes = [{
  route_id: order.route_id, code: "ROUTE-FG", name: "Finished bearing route", version: 2,
  active: true, stages: [
    { sequence_no: 1, stage_code: customStage, stage_name: "Custom curing", is_qa_gate: false, is_end: false },
    { sequence_no: 2, stage_code: "PACKING", stage_name: "Packing", is_qa_gate: false, is_end: true },
  ],
}];
const retiredRoute = {
  ...routes[0], route_id: "cccccccc-2222-2222-2222-222222222222", version: 1, active: false,
  stages: [{ sequence_no: 1, stage_code: "RETIRED_STAGE", stage_name: "Retired stage", is_qa_gate: false, is_end: true }],
};
const headers = { ...cors, "access-control-allow-methods": "GET,POST,OPTIONS", "access-control-expose-headers": "X-Request-ID" };
const json = (body: unknown, status = 200, extra: Record<string, string> = {}) => ({
  status, headers: { ...headers, ...extra }, contentType: "application/json", body: JSON.stringify(body),
});

type Options = {
  manufacturing?: boolean;
  operator?: boolean;
  twoPlants?: boolean;
  lots?: Lot[];
  delayedError?: boolean;
};

async function setup(page: Page, options: Options = {}) {
  const requests: URLSearchParams[] = [];
  const writes: string[] = [];
  const unexpected: string[] = [];
  const blockedOrigins: string[] = [];
  const units = [plantAlpha, beta].map(unit => ({ ...unit, gstin: null, state_code: null }));
  const authorized = options.twoPlants ? [plantAlpha.unit_id, beta.unit_id] : [plantAlpha.unit_id];
  let failing = Boolean(options.delayedError);
  let releaseResponse!: () => void;
  const responseGate = new Promise<void>(resolve => { releaseResponse = resolve; });
  if (!options.delayedError) releaseResponse();

  // A missed mock must never reach a live API; external origins are blocked below.
  await page.route("**/*", route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.startsWith("/api/")) {
      unexpected.push(`${request.method()} ${url.pathname}`);
      return route.fulfill(json({ detail: "Unmocked API request" }, 404));
    }
    return url.origin === webOrigin ? route.continue() : route.abort();
  });
  await mockSignInAndMe(page, { status: 200, body: {
    ...grantedProfile,
    modules: options.manufacturing === false
      ? grantedProfile.modules.filter(module => module !== "manufacturing")
      : [...grantedProfile.modules, "manufacturing"],
    permissions: options.operator
      ? [...grantedProfile.permissions, "route.move"]
      : grantedProfile.permissions.filter(permission => permission !== "route.move"),
    unit_ids: authorized,
  } }, units);
  await page.route(/\/api\/v1\/product\/routes(?:\?.*)?$/, route => {
    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    const active = new URL(route.request().url()).searchParams.get("active");
    return route.fulfill(json(active === "false" ? [retiredRoute] : routes));
  });
  await page.route(/\/api\/v1\/product\/items(?:\?.*)?$/, route => {
    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    const params = new URL(route.request().url()).searchParams;
    const limit = Number(params.get("limit") ?? 50), offset = Number(params.get("offset") ?? 0);
    const items = [{ item_id: itemId, code: "FG-01", name: "Finished bearing", item_type: "FG",
      uom_code: "NOS", category_id: null, hsn_code: null, shelf_life_days: null,
      route_code: "ROUTE-FG", active: true },
    { item_id: otherItemId, code: "WIP-02", name: "Moulded insert", item_type: "WIP",
      uom_code: "NOS", category_id: null, hsn_code: null, shelf_life_days: null,
      route_code: "ROUTE-FG", active: true }];
    return route.fulfill(json({ items: items.slice(offset, offset + limit), total: items.length, limit, offset }));
  });
  await page.route(/\/api\/v1\/manufacturing\/work-orders(?:\?.*)?$/, route => {
    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    const params = new URL(route.request().url()).searchParams;
    const orders = [order].filter(value => authorized.includes(value.unit_id)
      && (!params.has("status") || value.status === params.get("status")?.toUpperCase()));
    const limit = Number(params.get("limit") ?? 50), offset = Number(params.get("offset") ?? 0);
    return route.fulfill(json({ items: orders.slice(offset, offset + limit), total: orders.length, limit, offset }));
  });
  await page.route(/\/api\/v1\/manufacturing\/lots(?:\?.*)?$/, async route => {
    const request = route.request();
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    if (request.method() !== "GET") {
      writes.push(`${request.method()} /manufacturing/lots`);
      return route.fulfill(json({ detail: "Creation is outside the register tests" }, 405));
    }
    const params = new URL(request.url()).searchParams;
    requests.push(new URLSearchParams(params));
    await responseGate;
    if (failing) return route.fulfill(json({ detail: "Production lots temporarily unavailable" }, 503,
      { "X-Request-ID": "req-production-lots-503" }));
    // Mirror the backend's tenant/plant scope and only its supported list filters.
    const matching = (options.lots ?? [baseLot]).filter(lot => authorized.includes(lot.unit_id)
      && (!params.has("work_order_id") || lot.work_order_id === params.get("work_order_id"))
      && (!params.has("status") || lot.status === params.get("status")?.toUpperCase())
      && (!params.has("stage") || lot.stage_code === params.get("stage")?.toUpperCase()))
      .sort((a, b) => b.lot_no.localeCompare(a.lot_no));
    const limit = Number(params.get("limit") ?? 50), offset = Number(params.get("offset") ?? 0);
    return route.fulfill(json({ items: matching.slice(offset, offset + limit), total: matching.length, limit, offset }));
  });
  // Runs before specific mocks. No remote origin may be fulfilled or contacted.
  await page.route("**/*", route => {
    const origin = new URL(route.request().url()).origin;
    if (allowedOrigins.has(origin)) return route.fallback();
    blockedOrigins.push(origin);
    return route.abort();
  });
  return { requests, writes, unexpected, blockedOrigins, release: releaseResponse, recover: () => { failing = false; } };
}

const heading = (page: Page) => page.getByRole("heading", { name: /^production lots$/i });
const results = (page: Page, mobile = false) => page.getByRole(mobile ? "list" : "table", { name: "Production lot results" });
const createEntry = (page: Page) => page.getByRole("button", { name: /^(new|create|start) production lot$/i })
  .or(page.getByRole("link", { name: /^(new|create|start) production lot$/i }));

function assertSupported(state: Awaited<ReturnType<typeof setup>>) {
  expect(state.requests.length).toBeGreaterThan(0);
  for (const params of state.requests) {
    expect([...params.keys()].every(key => ["work_order_id", "status", "stage", "limit", "offset"].includes(key))).toBe(true);
    expect(Number(params.get("limit") ?? 50)).toBeGreaterThanOrEqual(1);
    expect(Number(params.get("limit") ?? 50)).toBeLessThanOrEqual(200);
    expect(Number(params.get("offset") ?? 0)).toBeGreaterThanOrEqual(0);
  }
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
  expect(state.blockedOrigins).toEqual([]);
}

test("register shows actual lot, work order, item, stage, status and exact quantity strings", async ({ page }) => {
  const lots = statuses.map((status, index) => ({ ...baseLot,
    prod_lot_id: `eeeeeeee-1111-1111-1111-${String(index).padStart(12, "0")}`,
    lot_no: `PL-${String(index).padStart(3, "0")}`, status,
    quantity: index === 1 ? "0.125" : "123.450",
  }));
  const state = await setup(page, { lots });
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  const table = results(page);
  const first = table.getByRole("row", { name: /PL-000/ });
  await expect(first).toContainText("WO-000");
  await expect(first).toContainText("FG-01");
  await expect(first).toContainText(customStage);
  await expect(first).toContainText("Custom curing");
  await expect(first).toContainText("123.450");
  await expect(table.getByRole("row", { name: /PL-001/ })).toContainText("0.125");
  for (const [index, status] of statuses.entries()) {
    await expect(table.getByRole("row", { name: new RegExp(`PL-${String(index).padStart(3, "0")}`) }))
      .toContainText(new RegExp(status.replaceAll("_", "[ _-]+"), "i"));
    await expect(page.getByLabel("Status filter").locator(`option[value="${status}"]`)).toHaveCount(1);
  }
  await expect(page.getByLabel("Stage filter").locator(`option[value="${customStage}"]`)).toHaveCount(1);
  await expect(page.getByLabel("Stage filter").locator('option[value="RETIRED_STAGE"]')).toHaveCount(1);
  assertSupported(state);
});

test("plant and item filters inspect every server page without sending unsupported query fields", async ({ page }) => {
  const lots = Array.from({ length: 225 }, (_, index) => ({ ...baseLot,
    prod_lot_id: `eeeeeeee-5555-5555-5555-${String(index).padStart(12, "0")}`,
    lot_no: `PL-${String(index).padStart(3, "0")}`,
    unit_id: index >= 220 ? beta.unit_id : plantAlpha.unit_id,
    item_id: index >= 220 ? otherItemId : itemId,
    item_code: index >= 220 ? "WIP-02" : "FG-01",
  }));
  const state = await setup(page, { twoPlants: true, lots });
  await page.goto(path);
  await page.getByRole("banner").getByLabel("Active plant").selectOption(beta.unit_id);
  await expect(heading(page)).toBeVisible();
  await page.getByLabel("Item filter").selectOption(otherItemId);
  await expect(results(page)).toContainText("PL-224");
  await expect(results(page)).toContainText("PL-220");
  await expect(results(page)).not.toContainText("PL-219");
  await expect.poll(() => state.requests.some(params => Number(params.get("offset") ?? 0) >= 200)).toBe(true);
  assertSupported(state);
});

test("work order, status and route-defined stage filters persist in the URL and use only supported GET parameters", async ({ page }) => {
  const state = await setup(page, { lots: [baseLot,
    { ...baseLot, prod_lot_id: "eeeeeeee-2222-2222-2222-222222222222", lot_no: "PL-OTHER", work_order_id: otherWorkOrderId },
  ] });
  await page.goto(`${path}?work_order_id=${workOrderId}&status=IN_PROCESS&stage=${customStage}`);
  await expect(heading(page)).toBeVisible();
  await expect(page.getByLabel("Work order filter")).toHaveValue(workOrderId);
  await expect(page.getByLabel("Status filter")).toHaveValue("IN_PROCESS");
  await expect(page.getByLabel("Stage filter")).toHaveValue(customStage);
  await expect(results(page)).toContainText("PL-000");
  await expect(results(page)).not.toContainText("PL-OTHER");
  await expect.poll(() => state.requests.some(params => params.get("work_order_id") === workOrderId
    && params.get("status") === "IN_PROCESS" && params.get("stage") === customStage)).toBe(true);
  await page.reload();
  await expect(page.getByLabel("Work order filter")).toHaveValue(workOrderId);
  await expect(page.getByLabel("Status filter")).toHaveValue("IN_PROCESS");
  await expect(page.getByLabel("Stage filter")).toHaveValue(customStage);
  await expect(results(page)).toContainText("PL-000");
  await expect.poll(() => state.requests.filter(params => params.get("work_order_id") === workOrderId
    && params.get("status") === "IN_PROCESS" && params.get("stage") === customStage).length).toBeGreaterThanOrEqual(2);
  assertSupported(state);
});

test("an unrecognized status in the URL stays a narrowing server filter rather than broadening the list", async ({ page }) => {
  const state = await setup(page);
  await page.goto(`${path}?status=UNRECOGNIZED`);
  await expect(heading(page)).toBeVisible();
  await expect(page.getByLabel("Status filter")).toHaveValue("UNRECOGNIZED");
  await expect(page.getByText(/no production lots found/i)).toBeVisible();
  await expect.poll(() => state.requests.some(params => params.get("status") === "UNRECOGNIZED")).toBe(true);
  assertSupported(state);
});

test("server pagination advances by offset without an unsupported plant or item query", async ({ page }) => {
  const lots = Array.from({ length: 225 }, (_, index) => ({ ...baseLot,
    prod_lot_id: `eeeeeeee-3333-3333-3333-${String(index).padStart(12, "0")}`,
    lot_no: `PL-${String(index).padStart(3, "0")}`,
  }));
  const state = await setup(page, { lots });
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  await expect(results(page)).toContainText("PL-224");
  await page.getByRole("button", { name: /next page/i }).click();
  await expect(results(page)).not.toContainText("PL-224");
  await expect.poll(() => state.requests.some(params => Number(params.get("offset") ?? 0) > 0)).toBe(true);
  assertSupported(state);
});

test("plant scope excludes unassigned lots and unassigned plant choices", async ({ page }) => {
  const outside = { ...baseLot, prod_lot_id: "eeeeeeee-4444-4444-4444-444444444444",
    lot_no: "PL-OUTSIDE", unit_id: outsideUnit };
  const state = await setup(page, { lots: [baseLot, outside] });
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  await expect(results(page)).toContainText("PL-000");
  await expect(results(page)).not.toContainText("PL-OUTSIDE");
  await expect(page.getByRole("banner").getByLabel("Active plant").locator(`option[value="${beta.unit_id}"]`)).toHaveCount(0);
  assertSupported(state);
});

test("manufacturing readers can list lots but only route.move exposes creation", async ({ page }) => {
  const state = await setup(page);
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  await expect(results(page)).toContainText("PL-000");
  await expect(createEntry(page)).toHaveCount(0);
  assertSupported(state);
});

test("route.move exposes the creation entry without submitting a lot", async ({ page }) => {
  const state = await setup(page, { operator: true });
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  await expect(createEntry(page)).toBeVisible();
  assertSupported(state);
});

test("manufacturing module denial prevents lot requests even with route.move", async ({ page }) => {
  const state = await setup(page, { manufacturing: false, operator: true });
  await page.goto(path);
  await expect(heading(page)).toHaveCount(0);
  await expect(page.getByText(/access denied|outside your current module access|manufacturing.*unavailable/i).first()).toBeVisible();
  expect(state.requests).toHaveLength(0);
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
  expect(state.blockedOrigins).toEqual([]);
});

test("loading, request ID error, retry and empty state are distinct", async ({ page }) => {
  const state = await setup(page, { lots: [], delayedError: true });
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: /loading.*(production )?lots/i })).toBeVisible();
  state.release();
  await expect(page.getByRole("alert")).toContainText("Production lots temporarily unavailable");
  await expect(page.getByText("Request ID: req-production-lots-503")).toBeVisible();
  state.recover();
  await page.getByRole("button", { name: /try again/i }).click();
  await expect(page.getByText(/no production lots found/i)).toBeVisible();
  assertSupported(state);
});

test("mobile lot cards retain exact quantity and stage with keyboard-usable filters", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const state = await setup(page);
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  const filter = page.getByLabel("Stage filter");
  await expect(filter).toBeEnabled();
  await filter.focus();
  await expect(filter).toBeFocused();
  const cards = results(page, true);
  await expect(cards).toContainText("PL-000");
  await expect(cards).toContainText("WO-000");
  await expect(cards).toContainText(customStage);
  await expect(cards).toContainText("123.450");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  assertSupported(state);
});
