import { expect, test, type Page } from "@playwright/test";
import { cors, grantedProfile, mockSignInAndMe, plantAlpha, plantBeta } from "./support/fixtures";

test.use({ serviceWorkers: "block" });

const path = "/app/manufacturing/performance";
const machineAlpha = "aaaaaaaa-1111-1111-1111-111111111111";
const machineBeta = "aaaaaaaa-2222-2222-2222-222222222222";
const wip = [
  { route_code: "RT-MIX", stage_code: "MIX", stage_name: "Mixing", item_code: "CMP-01", lots: 2, quantity: "0.125" },
  { route_code: "RT-CURE", stage_code: "CURE", stage_name: "Curing", item_code: "FG-42", lots: 3, quantity: "1200.500" },
];
const oee = [
  { machine_id: machineAlpha, machine_code: "PRESS-01", shifts: 2, planned_minutes: 960, downtime_minutes: 48, availability: "0.9500", good_qty: "123.450", reject_qty: "2.300", quality: "0.9817", performance: null },
  { machine_id: machineBeta, machine_code: "PRESS-02", shifts: 1, planned_minutes: 480, downtime_minutes: 0, availability: "1.0000", good_qty: "0.000", reject_qty: "0.000", quality: null, performance: null },
];
const missing = [
  { unit_id: plantAlpha.unit_id, machine_id: machineAlpha, machine_code: "PRESS-01", work_date: "2026-11-05", shift_code: "A" },
  { unit_id: plantBeta.unit_id, machine_id: machineBeta, machine_code: "PRESS-02", work_date: "2026-11-05", shift_code: "B" },
];

type Endpoint = "wip" | "oee" | "missing";
type Seen = { endpoint: Endpoint; method: string; params: URLSearchParams };
type SetupOptions = {
  manufacturing?: boolean;
  rows?: { wip?: unknown[]; oee?: unknown[]; missing?: unknown[] };
  fail?: Endpoint;
  delayed?: Endpoint;
};

const headers = { ...cors, "access-control-allow-methods": "GET,OPTIONS", "access-control-expose-headers": "X-Request-ID" };
const json = (body: unknown, status = 200, extra: Record<string, string> = {}) => ({
  status, headers: { ...headers, ...extra }, contentType: "application/json", body: JSON.stringify(body),
});

async function setup(page: Page, options: SetupOptions = {}) {
  const seen: Seen[] = [];
  const unexpected: string[] = [];
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  if (!options.delayed) release();

  await page.route("**/api/v1/**", route => {
    unexpected.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
    return route.fulfill(json({ detail: "Unmocked API request" }, 404));
  });
  await mockSignInAndMe(page, { status: 200, body: {
    ...grantedProfile,
    modules: options.manufacturing === false ? ["home"] : ["home", "manufacturing"],
    permissions: [],
    all_plants: true,
    unit_ids: [],
  } }, [plantAlpha, { ...plantBeta, active: true }]);

  await page.route(/\/api\/v1\/manufacturing\/(wip|oee|shift-entries\/missing)(?:\?.*)?$/, async route => {
    const request = route.request();
    const url = new URL(request.url());
    const endpoint: Endpoint = url.pathname.endsWith("/wip") ? "wip" : url.pathname.endsWith("/oee") ? "oee" : "missing";
    seen.push({ endpoint, method: request.method(), params: new URLSearchParams(url.searchParams) });
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    if (request.method() !== "GET") return route.fulfill(json({ detail: "Manufacturing performance is read-only" }, 405));
    if (options.delayed === endpoint) await gate;
    if (options.fail === endpoint) return route.fulfill(json({ detail: `${endpoint.toUpperCase()} unavailable` }, 503, { "X-Request-ID": `req-${endpoint}-503` }));
    const body = endpoint === "wip" ? options.rows?.wip ?? wip : endpoint === "oee" ? options.rows?.oee ?? oee : options.rows?.missing ?? missing;
    return route.fulfill(json(body));
  });
  return { seen, unexpected, release };
}

function supportedQueries(seen: Seen[]) {
  const allowed: Record<Endpoint, string[]> = {
    wip: ["unit_id"],
    oee: ["date_from", "date_to", "unit_id"],
    missing: ["work_date", "shift_code"],
  };
  expect(seen.every(request => request.method === "GET"
    && [...request.params.keys()].every(key => allowed[request.endpoint].includes(key)))).toBe(true);
}

test("loads for a manufacturing reader without write permissions and uses only supported queries", async ({ page }) => {
  const { seen, unexpected } = await setup(page);
  await page.goto(`${path}?date_from=2026-11-01&date_to=2026-11-05&missing_date=2026-11-05`);
  await expect(page.getByRole("heading", { name: "WIP & OEE" })).toBeVisible();
  await expect(page.getByRole("table", { name: /machine performance/i }).getByText("PRESS-01", { exact: true })).toBeVisible();
  expect(seen.some(row => row.endpoint === "oee" && row.params.get("date_from") === "2026-11-01" && row.params.get("date_to") === "2026-11-05")).toBe(true);
  expect(seen.some(row => row.endpoint === "missing" && row.params.get("work_date") === "2026-11-05")).toBe(true);
  supportedQueries(seen);
  expect(unexpected).toEqual([]);
});

test("denies users without the manufacturing module before report data is requested", async ({ page }) => {
  const { seen } = await setup(page, { manufacturing: false });
  await page.goto(path);
  await expect(page.getByText(/access denied/i)).toBeVisible();
  expect(seen).toEqual([]);
});

test("preserves exact quantities and ratios without inventing an OEE score, performance, or trend", async ({ page }) => {
  await setup(page);
  await page.goto(`${path}?date_from=2026-11-01&date_to=2026-11-05&missing_date=2026-11-05`);
  const wipTable = page.getByRole("table", { name: /WIP/i });
  const machineTable = page.getByRole("table", { name: /machine performance/i });
  await expect(wipTable).toContainText("0.125");
  await expect(wipTable).toContainText("1200.500");
  await expect(machineTable).toContainText("0.9500");
  await expect(machineTable).toContainText("123.450");
  await expect(machineTable).toContainText("Not available");
  await expect(page.getByText(/^OEE score$/i)).toHaveCount(0);
  await expect(page.getByText(/trend/i)).toHaveCount(0);
});

test("keeps plant and date filters in the URL while machine and stage filters remain client-side", async ({ page }) => {
  const { seen } = await setup(page);
  await page.goto(`${path}?date_from=2026-11-01&date_to=2026-11-05&missing_date=2026-11-05`);
  await page.getByLabel("Plant filter").selectOption(plantAlpha.unit_id);
  await page.getByLabel("Stage filter").selectOption("MIX");
  await page.getByLabel("Machine filter").selectOption(machineAlpha);
  await expect(page).toHaveURL(new RegExp(`unit_id=${plantAlpha.unit_id}`));
  await expect(page).toHaveURL(/stage=MIX/);
  await expect(page).toHaveURL(new RegExp(`machine_id=${machineAlpha}`));
  await expect(page.getByRole("table", { name: /WIP/i })).toContainText("Mixing");
  await expect(page.getByRole("table", { name: /WIP/i })).not.toContainText("Curing");
  await expect(page.getByRole("table", { name: /machine performance/i })).not.toContainText("PRESS-02");
  supportedQueries(seen);
  expect(seen.every(row => !row.params.has("machine_id") && !row.params.has("stage") && !row.params.has("limit") && !row.params.has("offset"))).toBe(true);
});

test("rejects an unauthorized URL plant instead of widening scope", async ({ page }) => {
  const { seen } = await setup(page);
  await page.goto(`${path}?unit_id=ffffffff-ffff-ffff-ffff-ffffffffffff`);
  await expect(page.getByText(/plant.*not available|invalid plant/i)).toBeVisible();
  expect(seen.every(request => request.params.get("unit_id") !== "ffffffff-ffff-ffff-ffff-ffffffffffff")).toBe(true);
});

test("offers only safe contextual drill links", async ({ page }) => {
  await setup(page);
  await page.goto(`${path}?unit_id=${plantAlpha.unit_id}&missing_date=2026-11-05`);
  await expect(page.getByRole("link", { name: /enter shift.*PRESS-01/i })).toHaveAttribute("href", new RegExp("/app/manufacturing/shift-entries/new\\?.*machine_id="));
  await expect(page.getByRole("link", { name: /view equipment.*PRESS-01/i })).toHaveAttribute("href", new RegExp("/app/manufacturing/equipment\\?.*q=PRESS-01"));
  await expect(page.getByRole("link", { name: /view lots.*Mixing/i })).toHaveAttribute("href", new RegExp("/app/manufacturing/lots\\?.*stage=MIX"));
  await expect(page.getByRole("link", { name: /enter shift.*PRESS-01/i })).not.toHaveAttribute("href", /work_order_id=/);
});

test("shows an accessible loading state while an individual report is pending", async ({ page }) => {
  const { release } = await setup(page, { delayed: "oee" });
  await page.goto(path);
  await expect(page.getByRole("status").filter({ hasText: /loading machine performance/i })).toBeVisible();
  release();
  await expect(page.getByRole("table", { name: /machine performance/i })).toBeVisible();
});

test("isolates report errors, displays request IDs, and retries only the failed report", async ({ page }) => {
  const state = await setup(page, { fail: "oee" });
  await page.goto(path);
  await expect(page.getByText("OEE unavailable")).toBeVisible();
  await expect(page.getByText("req-oee-503")).toBeVisible();
  await expect(page.getByRole("table", { name: /WIP/i })).toBeVisible();
  const before = state.seen.filter(row => row.endpoint === "wip").length;
  await page.getByRole("button", { name: /retry machine performance/i }).click();
  expect(state.seen.filter(row => row.endpoint === "wip")).toHaveLength(before);
  expect(state.seen.filter(row => row.endpoint === "oee").length).toBeGreaterThan(1);
});

test("renders useful independent empty states", async ({ page }) => {
  await setup(page, { rows: { wip: [], oee: [], missing: [] } });
  await page.goto(path);
  await expect(page.getByText(/no WIP/i)).toBeVisible();
  await expect(page.getByText(/no machine performance/i)).toBeVisible();
  await expect(page.getByText(/no missing shift entries/i)).toBeVisible();
});

test("pairs every visualization with an accessible data table and keyboard-operable controls", async ({ page }) => {
  await setup(page);
  await page.goto(path);
  await expect(page.getByRole("table", { name: /WIP/i })).toBeVisible();
  await expect(page.getByRole("table", { name: /machine performance/i })).toBeVisible();
  await expect(page.getByRole("table", { name: /missing shift/i })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.locator(":focus")).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Availability" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Quality" })).toBeVisible();
});

test("restores URL-backed filters with browser back and forward", async ({ page }) => {
  await setup(page);
  await page.goto(`${path}?date_from=2026-11-01&date_to=2026-11-05`);
  await page.getByLabel("Stage filter").selectOption("MIX");
  await expect(page).toHaveURL(/stage=MIX/);
  await page.getByLabel("Stage filter").selectOption("CURE");
  await expect(page).toHaveURL(/stage=CURE/);
  await page.goBack();
  await expect(page.getByLabel("Stage filter")).toHaveValue("MIX");
  await page.goForward();
  await expect(page.getByLabel("Stage filter")).toHaveValue("CURE");
});

test("fits a mobile viewport without horizontal page overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page);
  await page.goto(path);
  await expect(page.getByRole("heading", { name: "WIP & OEE" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await expect(page.getByRole("table", { name: /machine performance/i }).getByText("PRESS-01", { exact: true })).toBeVisible();
  await expect(page.getByRole("table", { name: /WIP/i })).toContainText("Mixing");
});
