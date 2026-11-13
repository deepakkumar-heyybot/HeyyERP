import { expect, test, type Page } from "@playwright/test";
import { cors, grantedProfile, mockSignInAndMe } from "./support/fixtures";

test.use({ serviceWorkers: "block" });

const registerPath = "/app/quality/plans";
const activePlanId = "11111111-aaaa-aaaa-aaaa-111111111111";
const retiredPlanId = "22222222-bbbb-bbbb-bbbb-222222222222";
const itemRubberId = "33333333-cccc-cccc-cccc-333333333333";
const itemFinishedId = "44444444-dddd-dddd-dddd-444444444444";

const activePlan = {
  plan_id: activePlanId,
  item_id: itemRubberId,
  item_code: "RM-RUBBER",
  kind: "INCOMING",
  name: "Incoming rubber inspection",
  active: true,
  created_at: "2026-11-06T08:15:00+05:30",
  params: [
    { parameter_code: "MOISTURE", name: "Moisture", unit: "%", min_value: null, max_value: "1.000000", is_key: false },
    { parameter_code: "PURITY", name: "Purity", unit: "%", min_value: "95.000000", max_value: null, is_key: true },
  ],
};

const retiredPlan = {
  plan_id: retiredPlanId,
  item_id: itemFinishedId,
  item_code: "FG-MOUNT",
  kind: "FINAL",
  name: "Final mount inspection",
  active: false,
  created_at: "2026-10-20T11:30:00+05:30",
  params: [
    { parameter_code: "HARDNESS", name: "Hardness", unit: "ShA", min_value: "55.000000", max_value: "65.000000", is_key: true },
  ],
};

const items = [
  { item_id: itemRubberId, code: "RM-RUBBER", name: "Natural rubber", item_type: "RM", uom_code: "KG", category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true },
  { item_id: itemFinishedId, code: "FG-MOUNT", name: "Engine mount", item_type: "FG", uom_code: "EA", category_id: null, hsn_code: null, shelf_life_days: null, route_code: "RT-MOUNT", active: true },
];

type SeenRequest = { method: string; pathname: string; params: URLSearchParams; body?: unknown };
type SetupOptions = {
  module?: boolean;
  manage?: boolean;
  plans?: typeof activePlan[];
  total?: number;
  failList?: boolean;
  delayedList?: boolean;
};

const headers = {
  ...cors,
  "access-control-allow-methods": "GET,POST,OPTIONS",
  "access-control-expose-headers": "X-Request-ID",
};
const json = (body: unknown, status = 200, extra: Record<string, string> = {}) => ({
  status,
  headers: { ...headers, ...extra },
  contentType: "application/json",
  body: JSON.stringify(body),
});

async function setup(page: Page, options: SetupOptions = {}) {
  const seen: SeenRequest[] = [];
  const unexpected: string[] = [];
  let failList = options.failList ?? false;
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  if (!options.delayedList) release();

  await page.route("**/api/v1/**", route => {
    unexpected.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
    return route.fulfill(json({ detail: "Unmocked API request" }, 404));
  });
  await mockSignInAndMe(page, { status: 200, body: {
    ...grantedProfile,
    modules: options.module === false ? ["home"] : ["home", "quality"],
    permissions: options.manage ? ["qa.approve"] : [],
    all_plants: true,
    unit_ids: [],
  } });

  await page.route(/\/api\/v1\/product\/items(?:\?.*)?$/, route => {
    const request = route.request();
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    seen.push({ method: request.method(), pathname: new URL(request.url()).pathname, params: new URL(request.url()).searchParams });
    return route.fulfill(json({ items, total: items.length, limit: 200, offset: 0 }));
  });

  await page.route(/\/api\/v1\/quality\/plans(?:\?.*)?$/, async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    const recorded: SeenRequest = { method: request.method(), pathname: url.pathname, params: new URLSearchParams(url.searchParams) };
    if (request.method() === "POST") recorded.body = request.postDataJSON();
    seen.push(recorded);
    if (request.method() === "POST") return route.fulfill(json({ ...activePlan, ...(recorded.body as object), plan_id: "55555555-eeee-eeee-eeee-555555555555", active: true, created_at: "2026-11-07T09:00:00+05:30", params: (recorded.body as { params: unknown[] }).params }, 201));
    await gate;
    if (failList) return route.fulfill(json({ detail: "Inspection plans unavailable" }, 503, { "X-Request-ID": "req-plans-503" }));
    const source = options.plans ?? [activePlan, retiredPlan];
    return route.fulfill(json({ items: source, total: options.total ?? source.length, limit: Number(url.searchParams.get("limit") ?? 50), offset: Number(url.searchParams.get("offset") ?? 0) }));
  });

  await page.route(/\/api\/v1\/quality\/plans\/([0-9a-f-]+)(?:\/retire)?$/, route => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    seen.push({ method: request.method(), pathname: url.pathname, params: new URLSearchParams(url.searchParams) });
    const isRetire = url.pathname.endsWith("/retire");
    const plan = url.pathname.includes(retiredPlanId) ? retiredPlan : activePlan;
    if (isRetire && request.method() === "POST") return route.fulfill(json({ ...plan, active: false }));
    return route.fulfill(json(plan));
  });

  return { seen, unexpected, release, recover: () => { failList = false; } };
}

test("quality readers see persisted plan records, parameters and exact decimal limits", async ({ page }) => {
  const state = await setup(page);
  await page.goto(registerPath);

  await expect(page.getByRole("heading", { name: "Inspection plans" })).toBeVisible();
  const table = page.getByRole("table", { name: "Inspection plans" });
  await expect(table).toContainText("RM-RUBBER");
  await expect(table).toContainText("Incoming rubber inspection");
  await expect(table).toContainText("2 parameters");
  await expect(table).toContainText("Active");
  await expect(table).toContainText("Retired");
  await page.getByRole("link", { name: /open incoming rubber inspection/i }).click();
  await expect(page).toHaveURL(`${registerPath}/${activePlanId}`);
  await expect(page.getByRole("heading", { name: "Incoming rubber inspection" })).toBeVisible();
  const parameters = page.getByRole("table", { name: "Inspection parameters" });
  await expect(parameters).toContainText("MOISTURE");
  await expect(parameters).toContainText("1.000000");
  await expect(parameters).toContainText("PURITY");
  await expect(parameters).toContainText("95.000000");
  await expect(parameters).toContainText("Key characteristic");
  expect(state.unexpected).toEqual([]);
});

test("item, type and status filters use only the supported list query and paging contract", async ({ page }) => {
  const state = await setup(page, { total: 75 });
  await page.goto(registerPath);
  await page.getByLabel("Item filter").selectOption(itemRubberId);
  await page.getByLabel("Inspection type").selectOption("INCOMING");
  await page.getByLabel("Plan status").selectOption("active");
  await expect(page).toHaveURL(new RegExp(`item_id=${itemRubberId}`));
  await expect(page).toHaveURL(/kind=INCOMING/);
  await expect(page).toHaveURL(/active=true/);
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page).toHaveURL(/offset=50/);
  await expect(page.getByText("Showing 51–75 of 75 records")).toBeVisible();

  const listRequests = state.seen.filter(request => request.pathname.endsWith("/quality/plans") && request.method === "GET");
  expect(listRequests.some(request => request.params.get("item_id") === itemRubberId && request.params.get("kind") === "INCOMING" && request.params.get("active") === "true")).toBe(true);
  expect(listRequests.some(request => request.params.get("offset") === "50")).toBe(true);
  expect(listRequests.every(request => [...request.params.keys()].every(key => ["item_id", "kind", "active", "limit", "offset"].includes(key)))).toBe(true);
});

test("read-only users can inspect active and retired revisions but cannot create or retire", async ({ page }) => {
  const state = await setup(page);
  await page.goto(`${registerPath}/${retiredPlanId}`);
  await expect(page.getByText("Retired", { exact: true })).toBeVisible();
  await expect(page.getByText(/retired plans are read-only/i)).toBeVisible();
  await expect(page.getByRole("button", { name: /retire plan/i })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /create inspection plan/i })).toHaveCount(0);
  expect(state.seen.every(request => request.method === "GET")).toBe(true);
});

test("qa.approve users create a plan with ordered parameters and exact string limits", async ({ page }) => {
  const state = await setup(page, { manage: true });
  await page.goto(`${registerPath}/new`);
  await page.getByLabel("Item").selectOption(itemFinishedId);
  await page.getByLabel("Inspection type").selectOption("FINAL");
  await page.getByLabel("Plan name").fill("Final dimensional inspection");
  await page.getByLabel("Parameter code 1").fill("length");
  await page.getByLabel("Parameter name 1").fill("Overall length");
  await page.getByLabel("Unit 1").fill("mm");
  await page.getByLabel("Minimum 1").fill("99.500000");
  await page.getByLabel("Maximum 1").fill("100.500000");
  await page.getByLabel("Key characteristic 1").check();
  await page.getByRole("button", { name: /add parameter/i }).click();
  await page.getByLabel("Parameter code 2").fill("weight");
  await page.getByLabel("Parameter name 2").fill("Finished weight");
  await page.getByLabel("Unit 2").fill("kg");
  await page.getByLabel("Maximum 2").fill("3.250000");
  await page.getByRole("button", { name: /create plan/i }).click();

  const create = state.seen.find(request => request.method === "POST" && request.pathname.endsWith("/quality/plans"));
  expect(create?.body).toEqual({
    item_id: itemFinishedId,
    kind: "FINAL",
    name: "Final dimensional inspection",
    params: [
      { parameter_code: "length", name: "Overall length", unit: "mm", min_value: "99.500000", max_value: "100.500000", is_key: true },
      { parameter_code: "weight", name: "Finished weight", unit: "kg", min_value: null, max_value: "3.250000", is_key: false },
    ],
  });
  await expect(page.getByText(/plan created/i)).toBeVisible();
});

test("create validation prevents missing limits, inverted limits and duplicate parameter codes", async ({ page }) => {
  const state = await setup(page, { manage: true });
  await page.goto(`${registerPath}/new`);
  await page.getByLabel("Item").selectOption(itemRubberId);
  await page.getByLabel("Inspection type").selectOption("INCOMING");
  await page.getByLabel("Plan name").fill("Incoming verification");
  await page.getByLabel("Parameter code 1").fill("MOISTURE");
  await page.getByLabel("Parameter name 1").fill("Moisture");
  await page.getByRole("button", { name: /create plan/i }).click();
  await expect(page.getByRole("alert")).toContainText(/minimum or maximum/i);
  await page.getByLabel("Minimum 1").fill("5");
  await page.getByLabel("Maximum 1").fill("1");
  await page.getByRole("button", { name: /create plan/i }).click();
  await expect(page.getByRole("alert")).toContainText(/maximum.*below|minimum.*greater/i);
  await page.getByLabel("Maximum 1").fill("6");
  await page.getByRole("button", { name: /add parameter/i }).click();
  await page.getByLabel("Parameter code 2").fill("moisture");
  await page.getByLabel("Parameter name 2").fill("Duplicate moisture");
  await page.getByLabel("Maximum 2").fill("7");
  await page.getByRole("button", { name: /create plan/i }).click();
  await expect(page.getByRole("alert")).toContainText(/parameter code.*only once|duplicate/i);
  expect(state.seen.filter(request => request.method === "POST" && request.pathname.endsWith("/quality/plans"))).toHaveLength(0);
});

test("qa.approve users explicitly confirm retirement and the returned plan becomes read-only", async ({ page }) => {
  const state = await setup(page, { manage: true });
  await page.goto(`${registerPath}/${activePlanId}`);
  await page.getByRole("button", { name: /retire plan/i }).click();
  await expect(page.getByRole("dialog", { name: /retire inspection plan/i })).toContainText(/cannot be used for new inspections/i);
  await page.getByRole("button", { name: /confirm retirement/i }).click();
  expect(state.seen.some(request => request.method === "POST" && request.pathname === `/api/v1/quality/plans/${activePlanId}/retire`)).toBe(true);
  await expect(page.getByText("Retired", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /retire plan/i })).toHaveCount(0);
});

test("loading, request-ID error, retry and empty states remain distinct", async ({ page }) => {
  const state = await setup(page, { delayedList: true, failList: true });
  await page.goto(registerPath);
  await expect(page.getByRole("status").filter({ hasText: /loading inspection plans/i })).toBeVisible();
  state.release();
  await expect(page.getByRole("alert")).toContainText("Inspection plans unavailable");
  await expect(page.getByText("Request ID: req-plans-503")).toBeVisible();
  state.recover();
  await page.getByRole("button", { name: /try again/i }).click();
  await expect(page.getByRole("table", { name: "Inspection plans" })).toBeVisible();

  await page.unrouteAll({ behavior: "wait" });
  await setup(page, { plans: [], total: 0 });
  await page.goto(registerPath);
  await expect(page.getByText(/no inspection plans/i)).toBeVisible();
});

test("users without the quality module are denied before plan data is requested", async ({ page }) => {
  const state = await setup(page, { module: false, manage: true });
  await page.goto(registerPath);
  await expect(page.getByText(/access denied|outside your current module access/i).first()).toBeVisible();
  expect(state.seen.filter(request => request.pathname.includes("/quality/plans"))).toEqual([]);
});

test("mobile plan register and detail are keyboard usable without document overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page, { manage: true });
  await page.goto(registerPath);
  await expect(page.getByRole("heading", { name: "Inspection plans" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  const open = page.getByRole("link", { name: /open incoming rubber inspection/i });
  await open.focus();
  await expect(open).toBeFocused();
  await open.click();
  await expect(page.getByRole("table", { name: "Inspection parameters" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
});
