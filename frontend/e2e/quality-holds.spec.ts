import { expect, test, type Page } from "@playwright/test";
import { cors, grantedProfile, mockSignInAndMe, plantAlpha, plantBeta } from "./support/fixtures";

test.use({ serviceWorkers: "block" });

const registerPath = "/app/quality/holds";
const activeHoldId = "11111111-aaaa-aaaa-aaaa-111111111111";
const releasedHoldId = "22222222-bbbb-bbbb-bbbb-222222222222";
const stockLotId = "33333333-cccc-cccc-cccc-333333333333";
const productionLotId = "44444444-dddd-dddd-dddd-444444444444";
const ncrId = "55555555-eeee-eeee-eeee-555555555555";

const activeHold = {
  hold_id: activeHoldId,
  unit_id: plantAlpha.unit_id,
  subject_type: "STOCK_LOT",
  subject_id: stockLotId,
  subject_label: "GRN-0042/1",
  reason: "Incoming material awaiting supplier disposition",
  ncr_id: ncrId,
  placed_by_name: "Quality Inspector",
  placed_at: "2026-11-06T09:15:00+05:30",
  released_by_name: null,
  released_at: null,
  release_note: null,
  active: true,
};

const releasedHold = {
  hold_id: releasedHoldId,
  unit_id: plantBeta.unit_id,
  subject_type: "PROD_LOT",
  subject_id: productionLotId,
  subject_label: "PL-0047",
  reason: "Final dimensions require review",
  ncr_id: null,
  placed_by_name: "QA Lead",
  placed_at: "2026-11-04T08:00:00+05:30",
  released_by_name: "Plant Head",
  released_at: "2026-11-05T16:30:00+05:30",
  release_note: "Dimensional recheck passed",
  active: false,
};

type Hold = typeof activeHold;
type SeenRequest = { method: string; pathname: string; params: URLSearchParams; body?: unknown };
type SetupOptions = {
  module?: boolean;
  manage?: boolean;
  rows?: Hold[];
  total?: number;
  delayedList?: boolean;
  failList?: boolean;
  rejectCreate?: boolean;
  rejectRelease?: boolean;
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
  let releaseList!: () => void;
  const listGate = new Promise<void>(resolve => { releaseList = resolve; });
  if (!options.delayedList) releaseList();

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
  } }, [plantAlpha, { ...plantBeta, active: true }]);

  await page.route(/\/api\/v1\/quality\/holds(?:\?.*)?$/, async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    const recorded: SeenRequest = { method: request.method(), pathname: url.pathname, params: new URLSearchParams(url.searchParams) };
    if (request.method() === "POST") recorded.body = request.postDataJSON();
    seen.push(recorded);
    if (request.method() === "POST") {
      if (options.rejectCreate) return route.fulfill(json({ detail: "The lot is quarantine, not available" }, 409, { "X-Request-ID": "req-hold-create-409" }));
      return route.fulfill(json({ ...activeHold, ...(recorded.body as object), hold_id: "66666666-ffff-ffff-ffff-666666666666", placed_at: "2026-11-07T10:00:00+05:30" }, 201));
    }
    await listGate;
    if (failList) return route.fulfill(json({ detail: "Quality holds unavailable" }, 503, { "X-Request-ID": "req-holds-503" }));
    const source = options.rows ?? [activeHold, releasedHold];
    const active = url.searchParams.get("active");
    const subjectType = url.searchParams.get("subject_type");
    const subjectId = url.searchParams.get("subject_id");
    const filtered = source.filter(row => (active === null || row.active === (active === "true")) && (!subjectType || row.subject_type === subjectType) && (!subjectId || row.subject_id === subjectId));
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 50);
    return route.fulfill(json({ items: filtered.slice(offset, offset + limit), total: options.total ?? filtered.length, limit, offset }));
  });

  await page.route(/\/api\/v1\/quality\/holds\/([0-9a-f-]+)(?:\/release)?$/, route => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    const recorded: SeenRequest = { method: request.method(), pathname: url.pathname, params: new URLSearchParams(url.searchParams) };
    if (request.method() === "POST") recorded.body = request.postDataJSON();
    seen.push(recorded);
    if (url.pathname.endsWith("/release") && request.method() === "POST") {
      if (options.rejectRelease) return route.fulfill(json({ detail: "The hold was already released" }, 409, { "X-Request-ID": "req-hold-release-409" }));
      return route.fulfill(json({ ...activeHold, active: false, released_by_name: "Plant Head", released_at: "2026-11-07T10:30:00+05:30", release_note: (recorded.body as { note: string }).note }));
    }
    return route.fulfill(json(url.pathname.includes(releasedHoldId) ? releasedHold : activeHold));
  });

  return { seen, unexpected, releaseList, recover: () => { failList = false; } };
}

test("quality readers filter and page holds using only verified server query fields", async ({ page }) => {
  const rows = Array.from({ length: 75 }, (_, index) => ({ ...activeHold,
    hold_id: `aaaaaaaa-aaaa-aaaa-aaaa-${String(index).padStart(12, "0")}`,
    subject_id: index === 0 ? stockLotId : `bbbbbbbb-bbbb-bbbb-bbbb-${String(index).padStart(12, "0")}`,
    subject_label: index === 0 ? activeHold.subject_label : `LOT-${index + 1}`,
  }));
  const state = await setup(page, { rows });
  await page.goto(registerPath);
  await expect(page.getByRole("heading", { name: "Quality holds" })).toBeVisible();
  const table = page.getByRole("table", { name: "Quality holds" });
  await expect(table).toContainText("GRN-0042/1");
  await expect(table).toContainText("Incoming material awaiting supplier disposition");
  await expect(table).toContainText("Active");
  await page.getByLabel("Entity type").selectOption("STOCK_LOT");
  await page.getByLabel("Hold status").selectOption("active");
  await expect(page.getByText("Showing 1–50 of 75 records")).toBeVisible();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page).toHaveURL(/subject_type=STOCK_LOT/);
  await expect(page).toHaveURL(/active=true/);
  await expect(page).toHaveURL(/offset=50/);
  await expect(page.getByText("Showing 51–75 of 75 records")).toBeVisible();
  await expect(table.getByRole("row")).toHaveCount(26);
  await expect(table).toContainText("LOT-75");
  await page.getByLabel("Entity ID").fill(stockLotId);
  await expect(page).toHaveURL(new RegExp(`subject_id=${stockLotId}`));
  await expect(page).not.toHaveURL(/offset=/);
  await expect(page.getByText("Showing 1–1 of 1 records")).toBeVisible();
  const listRequests = state.seen.filter(request => request.method === "GET" && request.pathname.endsWith("/quality/holds"));
  expect(listRequests.some(request => request.params.get("active") === "true" && request.params.get("subject_type") === "STOCK_LOT" && request.params.get("subject_id") === stockLotId)).toBe(true);
  expect(listRequests.every(request => [...request.params.keys()].every(key => ["active", "subject_type", "subject_id", "limit", "offset"].includes(key)))).toBe(true);
  expect(state.unexpected).toEqual([]);
});

test("an empty offset does not claim displayed records and offers first-page recovery", async ({ page }) => {
  await setup(page);
  await page.goto(`${registerPath}?offset=50`);
  await expect(page.getByText("No quality holds", { exact: true })).toBeVisible();
  await expect(page.getByText(/Showing 51/)).toHaveCount(0);
  await page.getByRole("button", { name: "Return to first page" }).click();
  await expect(page.getByRole("table", { name: "Quality holds" })).toContainText("GRN-0042/1");
  await expect(page).not.toHaveURL(/offset=/);
});

test("plant filtering uses returned unit identity without sending an unsupported backend parameter", async ({ page }) => {
  const alphaRows = Array.from({ length: 50 }, (_, index) => ({ ...activeHold, hold_id: `aaaaaaaa-aaaa-aaaa-aaaa-${String(index).padStart(12, "0")}`, subject_id: `bbbbbbbb-bbbb-bbbb-bbbb-${String(index).padStart(12, "0")}`, subject_label: `ALPHA-${index + 1}` }));
  const state = await setup(page, { rows: [...alphaRows, releasedHold], total: 51 });
  await page.goto(registerPath);
  await page.getByLabel("Plant filter").selectOption(plantBeta.unit_id);
  await expect(page).toHaveURL(new RegExp(`unit_id=${plantBeta.unit_id}`));
  await expect(page.getByRole("table", { name: "Quality holds" }).getByText("PL-0047")).toBeVisible();
  await expect(page.getByText("ALPHA-1")).toHaveCount(0);
  const listRequests = state.seen.filter(request => request.method === "GET" && request.pathname.endsWith("/quality/holds"));
  expect(listRequests.some(request => request.params.get("offset") === "50")).toBe(true);
  expect(listRequests.every(request => !request.params.has("unit_id"))).toBe(true);
});

test("hold detail shows the persisted lifecycle, linked NCR and no invented disposition action", async ({ page }) => {
  const state = await setup(page, { manage: true });
  await page.goto(`${registerPath}/${activeHoldId}`);
  await expect(page.getByRole("heading", { name: "GRN-0042/1" })).toBeVisible();
  await expect(page.getByText(activeHold.reason)).toBeVisible();
  await expect(page.getByText("Quality Inspector")).toBeVisible();
  await expect(page.getByText("2026-11-06")).toBeVisible();
  await expect(page.getByRole("link", { name: /open linked NCR/i })).toHaveAttribute("href", `/app/quality/ncrs/${ncrId}`);
  await expect(page.getByRole("button", { name: /dispose|scrap|return to supplier/i })).toHaveCount(0);

  await page.goto(`${registerPath}/${releasedHoldId}`);
  await expect(page.getByText("Dimensional recheck passed")).toBeVisible();
  await expect(page.getByText("Plant Head")).toBeVisible();
  await expect(page.getByText("Released", { exact: true })).toBeVisible();
  expect(state.seen.every(request => !request.pathname.includes("/history"))).toBe(true);
});

test("read-only quality users can inspect holds but cannot place or release them", async ({ page }) => {
  const state = await setup(page);
  await page.goto(registerPath);
  await expect(page.getByRole("link", { name: /place quality hold/i })).toHaveCount(0);
  await page.goto(`${registerPath}/${activeHoldId}`);
  await expect(page.getByText(activeHold.reason)).toBeVisible();
  await expect(page.getByRole("button", { name: /release hold/i })).toHaveCount(0);
  expect(state.seen.every(request => request.method === "GET")).toBe(true);
});

test("qa.approve users place a hold with the exact supported payload", async ({ page }) => {
  const state = await setup(page, { manage: true });
  await page.goto(`${registerPath}/new`);
  await page.getByLabel("Entity type").selectOption("PROD_LOT");
  await page.getByLabel("Entity ID").fill(productionLotId);
  await page.getByLabel("Reason").fill("Final dimensions require review");
  await page.getByRole("button", { name: /place hold/i }).click();
  const create = state.seen.find(request => request.method === "POST" && request.pathname.endsWith("/quality/holds"));
  expect(create?.body).toEqual({ subject_type: "PROD_LOT", subject_id: productionLotId, reason: "Final dimensions require review" });
  await expect(page).toHaveURL(/\/app\/quality\/holds\/66666666-ffff-ffff-ffff-666666666666/);
});

test("qa.approve users release an active hold with a note and refreshed lifecycle context", async ({ page }) => {
  const state = await setup(page, { manage: true });
  await page.goto(`${registerPath}/${activeHoldId}`);
  await page.getByRole("button", { name: /release hold/i }).click();
  const dialog = page.getByRole("dialog", { name: /release quality hold/i });
  await expect(dialog).toContainText(/returns the entity to its previous usable status/i);
  await dialog.getByLabel("Release note").fill("Verification passed; return to available stock");
  await dialog.getByRole("button", { name: /confirm release/i }).click();
  const release = state.seen.find(request => request.method === "POST" && request.pathname.endsWith(`/quality/holds/${activeHoldId}/release`));
  expect(release?.body).toEqual({ note: "Verification passed; return to available stock" });
  await expect(page.getByText("Released", { exact: true })).toBeVisible();
  await expect(page.getByText("Verification passed; return to available stock")).toBeVisible();
  await expect(page.getByRole("button", { name: /release hold/i })).toHaveCount(0);
});

test("write conflicts show backend details and request IDs without claiming success", async ({ page }) => {
  await setup(page, { manage: true, rejectCreate: true });
  await page.goto(`${registerPath}/new`);
  await page.getByLabel("Entity type").selectOption("STOCK_LOT");
  await page.getByLabel("Entity ID").fill(stockLotId);
  await page.getByLabel("Reason").fill("Supplier quality concern");
  await page.getByRole("button", { name: /place hold/i }).click();
  await expect(page.getByRole("alert")).toContainText("The lot is quarantine, not available");
  await expect(page.getByText("Request ID: req-hold-create-409")).toBeVisible();
  await expect(page).toHaveURL(`${registerPath}/new`);

  await page.unrouteAll({ behavior: "wait" });
  await setup(page, { manage: true, rejectRelease: true });
  await page.goto(`${registerPath}/${activeHoldId}`);
  await page.getByRole("button", { name: /release hold/i }).click();
  await page.getByRole("dialog", { name: /release quality hold/i }).getByLabel("Release note").fill("Release after verification");
  await page.getByRole("button", { name: /confirm release/i }).click();
  await expect(page.getByRole("alert")).toContainText("The hold was already released");
  await expect(page.getByText("Request ID: req-hold-release-409")).toBeVisible();
  await expect(page.getByText("Active", { exact: true })).toBeVisible();
});

test("loading, request-ID error, retry and empty states remain distinct", async ({ page }) => {
  const state = await setup(page, { delayedList: true, failList: true });
  await page.goto(registerPath);
  await expect(page.getByRole("status").filter({ hasText: /loading quality holds/i })).toBeVisible();
  state.releaseList();
  await expect(page.getByRole("alert")).toContainText("Quality holds unavailable");
  await expect(page.getByText("Request ID: req-holds-503")).toBeVisible();
  state.recover();
  await page.getByRole("button", { name: /try again/i }).click();
  await expect(page.getByRole("table", { name: "Quality holds" })).toBeVisible();

  await page.unrouteAll({ behavior: "wait" });
  await setup(page, { rows: [], total: 0 });
  await page.goto(registerPath);
  await expect(page.getByText(/no quality holds/i)).toBeVisible();
});

test("users without the quality module are denied before hold data is requested", async ({ page }) => {
  const state = await setup(page, { module: false, manage: true });
  await page.goto(registerPath);
  await expect(page.getByText(/access denied|outside your current module access/i).first()).toBeVisible();
  expect(state.seen.filter(request => request.pathname.includes("/quality/holds"))).toEqual([]);
});

test("mobile register and detail are keyboard usable without document overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page, { manage: true });
  await page.goto(registerPath);
  await expect(page.getByRole("heading", { name: "Quality holds" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  const open = page.getByRole("link", { name: /open hold GRN-0042\/1/i });
  await open.focus();
  await expect(open).toBeFocused();
  await open.click();
  await expect(page.getByRole("heading", { name: "GRN-0042/1" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  const release = page.getByRole("button", { name: /release hold/i });
  await release.focus();
  await expect(release).toBeFocused();
});
