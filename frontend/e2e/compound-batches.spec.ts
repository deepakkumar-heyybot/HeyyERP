import { expect, test, type Page } from "@playwright/test";
import { cors, grantedProfile, mockSignInAndMe, plantAlpha, plantBeta } from "./support/fixtures";

const path = "/app/manufacturing/batches";
const compoundA = "cccccccc-1111-1111-1111-111111111111";
const compoundB = "cccccccc-2222-2222-2222-222222222222";
const beta = { ...plantBeta, active: true };
const statuses = ["WEIGHED", "MIXING", "MATURING", "AWAITING_QA", "READY", "CONSUMED", "EXPIRED", "REJECTED", "ON_HOLD"] as const;
const baseBatch = {
  batch_id: "dddddddd-1111-1111-1111-111111111111", batch_no: "BAT-000", unit_id: plantAlpha.unit_id,
  compound_id: compoundA, compound_code: "CMP-01", compound_name: "Rubber compound", status: "READY",
  total_kg: "123.450", remaining_kg: "0.125", weighed_at: "2026-11-01T12:00:00Z", mixed_at: "2026-12-23T12:00:00Z",
  age_hours: "30.50", maturation_min_hours: "24.50", maximum_age_hours: "120.75", qa_status: "PASSED", hardness: "60.00",
};
type Batch = typeof baseBatch;
const compounds = [
  { compound_id: compoundA, code: "CMP-01", name: "Rubber compound", lane: "RUBBER", maturation_min_hours: "24.50", maximum_age_hours: "120.75", hardness_min: null, hardness_max: null, output_item_id: null, active: true, recipe: [] },
  { compound_id: compoundB, code: "CMP-02", name: "Second compound", lane: "EXTRUSION", maturation_min_hours: "12.00", maximum_age_hours: "72.00", hardness_min: null, hardness_max: null, output_item_id: null, active: true, recipe: [] },
];
const headers = { ...cors, "access-control-allow-methods": "GET,POST,OPTIONS" };
const json = (body: unknown, status = 200, extra: Record<string, string> = {}) => ({ status, headers: { ...headers, ...extra }, contentType: "application/json", body: JSON.stringify(body) });

async function setup(page: Page, options: { manufacturing?: boolean; operator?: boolean; twoPlants?: boolean; batches?: Batch[]; delayedError?: boolean } = {}) {
  const requests: URLSearchParams[] = [];
  const writes: string[] = [];
  const units = options.twoPlants ? [plantAlpha, beta] : [plantAlpha];
  const unitIds = units.map(unit => unit.unit_id);
  let shouldFail = Boolean(options.delayedError);
  let releaseResponse!: () => void;
  const responseGate = new Promise<void>(resolve => { releaseResponse = resolve; });
  if (!options.delayedError) releaseResponse();
  await page.route("**/*", route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith("/api/")) return route.fulfill(json({ detail: "Unmocked API request" }, 404));
    if (url.origin === "http://127.0.0.1:5174") return route.continue();
    return route.abort();
  });
  await mockSignInAndMe(page, { status: 200, body: {
    ...grantedProfile,
    modules: options.manufacturing === false ? grantedProfile.modules.filter(module => module !== "manufacturing") : [...grantedProfile.modules, "manufacturing"],
    permissions: options.operator ? [...grantedProfile.permissions, "route.move"] : grantedProfile.permissions.filter(permission => permission !== "route.move"),
    unit_ids: unitIds,
  } }, units);
  await page.route(/\/api\/v1\/manufacturing\/compounds(?:\?.*)?$/, route => {
    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    if (route.request().method() !== "GET") return route.fulfill(json({ detail: "Unexpected compound write" }, 405));
    const params = new URL(route.request().url()).searchParams;
    const matching = compounds.filter(item => (!params.has("active") || String(item.active) === params.get("active")) && (!params.has("lane") || item.lane === params.get("lane")));
    const limit = Number(params.get("limit") ?? 50), offset = Number(params.get("offset") ?? 0);
    return route.fulfill(json({ items: matching.slice(offset, offset + limit), total: matching.length, limit, offset }));
  });
  await page.route(/\/api\/v1\/manufacturing\/batches(?:\?.*)?$/, async route => {
    const method = route.request().method();
    if (method === "OPTIONS") return route.fulfill({ status: 204, headers });
    if (method !== "GET") { writes.push(method); return route.fulfill(json({ detail: "Weighing is outside this register test" }, 405)); }
    const params = new URL(route.request().url()).searchParams;
    requests.push(new URLSearchParams(params));
    await responseGate;
    if (shouldFail) return route.fulfill(json({ detail: "Batch register unavailable" }, 503, { "X-Request-ID": "req-batches-503" }));
    // Match the backend's authorized-plant scope and supported server filters.
    const matching = (options.batches ?? [baseBatch]).filter(item => unitIds.includes(item.unit_id) && (!params.has("status") || item.status === params.get("status")?.toUpperCase()) && (!params.has("compound_id") || item.compound_id === params.get("compound_id")));
    const limit = Number(params.get("limit") ?? 50), offset = Number(params.get("offset") ?? 0);
    return route.fulfill(json({ items: matching.slice(offset, offset + limit), total: matching.length, limit, offset }));
  });
  return { requests, writes, release: () => releaseResponse(), recover: () => { shouldFail = false; } };
}

const heading = (page: Page) => page.getByRole("heading", { name: /^(compound )?batches$/i });
const table = (page: Page) => page.getByRole("table", { name: "Batch results" });
function assertSupported(requests: URLSearchParams[]) {
  expect(requests.length).toBeGreaterThan(0);
  expect(requests.every(params => [...params.keys()].every(key => ["status", "compound_id", "limit", "offset"].includes(key)))).toBe(true);
  expect(requests.every(params => Number(params.get("limit") ?? 50) >= 1 && Number(params.get("limit") ?? 50) <= 200 && Number(params.get("offset") ?? 0) >= 0)).toBe(true);
}

test("batch register displays every backend status, exact quantities and labeled timestamps", async ({ page }) => {
  const rows = statuses.map((status, index) => ({ ...baseBatch, batch_id: `dddddddd-1111-1111-1111-${String(index).padStart(12, "0")}`, batch_no: `BAT-${String(index).padStart(3, "0")}`, status }));
  const { requests } = await setup(page, { batches: rows });
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  const results = table(page);
  await expect(results.getByRole("columnheader", { name: /weighed/i })).toBeVisible();
  await expect(results.getByRole("columnheader", { name: /mixed/i })).toBeVisible();
  const labels = await results.getByRole("columnheader").allTextContents();
  const weighedColumn = labels.findIndex(label => /weighed/i.test(label));
  const mixedColumn = labels.findIndex(label => /mixed/i.test(label));
  expect(weighedColumn).not.toBe(mixedColumn);
  const first = results.getByRole("row", { name: /BAT-000/ });
  await expect(first).toContainText("123.450");
  await expect(first).toContainText("0.125");
  await expect(first.getByRole("cell").nth(weighedColumn)).toContainText(/(?:Nov(?:ember)?|11)/i);
  await expect(first.getByRole("cell").nth(mixedColumn)).toContainText(/(?:Dec(?:ember)?|12)/i);
  for (const [index, status] of statuses.entries()) {
    const row = results.getByRole("row", { name: new RegExp(`BAT-${String(index).padStart(3, "0")}`) });
    await expect(row).toContainText(new RegExp(status.replaceAll("_", "[ _-]+"), "i"));
  }
  const statusFilter = page.getByLabel("Status filter");
  for (const status of statuses) await expect(statusFilter.locator(`option[value="${status}"]`)).toHaveCount(1);
  assertSupported(requests);
});

test("desktop batch table explains horizontal scrolling when results are wider than the viewport", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await setup(page);
  await page.goto(path);
  const tableRegion = page.getByRole("region", { name: "Scrollable batch results" });
  await expect(tableRegion).toBeVisible();
  expect(await tableRegion.evaluate(node => node.scrollWidth > node.clientWidth)).toBe(true);
  await expect(page.getByText("Scroll the table horizontally to view all batch fields.")).toBeVisible();
});

test("status and compound use supported server parameters and paging reaches later results", async ({ page }) => {
  const rows = Array.from({ length: 35 }, (_, index) => ({ ...baseBatch, batch_id: `dddddddd-2222-2222-2222-${String(index).padStart(12, "0")}`, batch_no: `BAT-${String(index).padStart(3, "0")}`, compound_id: index === 1 ? compoundB : compoundA, compound_code: index === 1 ? "CMP-02" : "CMP-01", status: index === 1 ? "ON_HOLD" : "READY" }));
  const { requests } = await setup(page, { batches: rows });
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  await expect(table(page)).toContainText("BAT-000");
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(table(page)).toContainText("BAT-020");
  await expect.poll(() => requests.some(params => Number(params.get("offset") ?? 0) > 0)).toBe(true);
  await page.getByLabel("Status filter").selectOption("ON_HOLD");
  await page.getByLabel("Compound filter").selectOption(compoundB);
  await expect(table(page)).toContainText("BAT-001");
  await expect(table(page)).not.toContainText("BAT-000");
  await expect.poll(() => requests.some(params => params.get("status") === "ON_HOLD" && params.get("compound_id") === compoundB)).toBe(true);
  assertSupported(requests);
});

test("selected plant filters all backend pages without sending an unsupported plant parameter", async ({ page }) => {
  const rows = Array.from({ length: 225 }, (_, index) => ({ ...baseBatch, batch_id: `dddddddd-3333-3333-3333-${String(index).padStart(12, "0")}`, batch_no: `BAT-${String(index).padStart(3, "0")}`, unit_id: index < 220 ? plantAlpha.unit_id : beta.unit_id }));
  const { requests } = await setup(page, { twoPlants: true, batches: rows });
  await page.goto(path);
  await page.getByRole("banner").getByLabel("Active plant").selectOption(beta.unit_id);
  await expect(heading(page)).toBeVisible();
  await expect(table(page)).toContainText("BAT-220");
  await expect(table(page)).toContainText("BAT-224");
  await expect(table(page)).not.toContainText("BAT-000");
  await expect.poll(() => requests.some(params => Number(params.get("offset") ?? 0) > 0)).toBe(true);
  assertSupported(requests);
});

test("without the manufacturing module the register is denied without batch requests", async ({ page }) => {
  const { requests, writes } = await setup(page, { manufacturing: false, operator: true });
  await page.goto(path);
  await expect(heading(page)).toHaveCount(0);
  await expect(page.getByText(/access denied|outside your current module access|manufacturing.*unavailable/i).first()).toBeVisible();
  expect(requests).toHaveLength(0);
  expect(writes).toHaveLength(0);
});

test("loading and server error show the request ID and retry recovers to an empty register", async ({ page }) => {
  const state = await setup(page, { batches: [], delayedError: true });
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: /loading.*batches/i })).toBeVisible();
  state.release();
  await expect(page.getByRole("alert")).toContainText("Batch register unavailable");
  await expect(page.getByText("Request ID: req-batches-503")).toBeVisible();
  state.recover();
  await page.getByRole("button", { name: /try again/i }).click();
  await expect(page.getByText(/no batches found/i)).toBeVisible();
});

test("mobile batch cards are keyboard usable without document overflow", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await setup(page);
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  const status = page.getByLabel("Status filter");
  await status.focus();
  await expect(status).toBeFocused();
  const results = page.getByRole("list", { name: "Batch results" });
  await expect(results).toContainText("BAT-000");
  await expect(results).toContainText("123.450");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
});
