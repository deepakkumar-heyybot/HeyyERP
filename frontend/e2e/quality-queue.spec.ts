import { expect, test, type Page } from "@playwright/test";
import { cors, grantedProfile, mockSignInAndMe, plantAlpha, plantBeta } from "./support/fixtures";

test.use({ serviceWorkers: "block" });
const path = "/app/quality";
const subjects = [
  { subject_type: "GRN_LINE", subject_id: "11111111-1111-1111-1111-111111111111", reference: "GRN-0042/LOT-9", item_code: "RM-01", quantity: "12.500", stage: null, waiting_since: "2026-11-05T08:00:00+05:30", unit_id: plantAlpha.unit_id, inspected: false },
  { subject_type: "COMPOUND_BATCH", subject_id: "22222222-2222-2222-2222-222222222222", reference: "BAT-0019", item_code: "CMP-01", quantity: "80.125", stage: "AWAITING_QA", waiting_since: "2026-11-04T07:30:00+05:30", unit_id: plantBeta.unit_id, inspected: true },
  { subject_type: "PROD_LOT", subject_id: "33333333-3333-3333-3333-333333333333", reference: "PL-0047", item_code: "FG-42", quantity: "1200.500", stage: "QA_MOULDING", waiting_since: "2026-11-03T11:00:00+05:30", unit_id: plantAlpha.unit_id, inspected: false },
];

async function setup(page: Page, options: { quality?: boolean; rows?: unknown[]; fail?: boolean; delayed?: boolean } = {}) {
  const seen: URLSearchParams[] = [], unexpected: string[] = [];
  let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve; }); if (!options.delayed) release();
  await page.route("**/api/v1/**", route => { unexpected.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`); return route.fulfill({ status: 404, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "Unmocked" }) }); });
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, modules: options.quality === false ? ["home"] : ["home", "quality"], permissions: [], all_plants: true, unit_ids: [] } }, [plantAlpha, { ...plantBeta, active: true }]);
  await page.route(/\/api\/v1\/quality\/queue(?:\?.*)?$/, async route => {
    const request = route.request(), url = new URL(request.url()); seen.push(new URLSearchParams(url.searchParams));
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    await gate;
    if (options.fail) return route.fulfill({ status: 503, headers: { ...cors, "access-control-expose-headers": "X-Request-ID", "X-Request-ID": "req-qa-503" }, contentType: "application/json", body: JSON.stringify({ detail: "QA queue unavailable" }) });
    const kind = url.searchParams.get("subject_type"); const rows = (options.rows ?? subjects).filter((row: any) => !kind || row.subject_type === kind);
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(rows) });
  });
  return { seen, unexpected, release, recover: () => { options.fail = false; } };
}

test("quality readers see exact queue fields without write permission", async ({ page }) => {
  const state = await setup(page); await page.goto(path);
  await expect(page.getByRole("heading", { name: "QA queue" })).toBeVisible();
  const table = page.getByRole("table", { name: "QA queue results" });
  await expect(table).toContainText("GRN-0042/LOT-9"); await expect(table).toContainText("12.500");
  await expect(table).toContainText("BAT-0019"); await expect(table).toContainText("Inspection recorded");
  expect(state.seen.every(params => [...params.keys()].every(key => key === "subject_type"))).toBe(true); expect(state.unexpected).toEqual([]);
});

test("kind uses the supported server filter while plant and age remain client filters", async ({ page }) => {
  const state = await setup(page); await page.goto(path);
  await page.getByLabel("Queue kind").selectOption("PROD_LOT");
  await page.getByLabel("Plant filter").selectOption(plantAlpha.unit_id);
  await page.getByLabel("Age filter").selectOption("48");
  await expect(page).toHaveURL(/subject_type=PROD_LOT/); await expect(page).toHaveURL(new RegExp(`unit_id=${plantAlpha.unit_id}`)); await expect(page).toHaveURL(/age_hours=48/);
  await expect(page.getByRole("table", { name: "QA queue results" })).toContainText("PL-0047");
  expect(state.seen.some(params => params.get("subject_type") === "PROD_LOT")).toBe(true);
  expect(state.seen.every(params => !params.has("unit_id") && !params.has("age_hours"))).toBe(true);
});

test("every item opens the inspection workflow using only returned subject identity", async ({ page }) => {
  await setup(page); await page.goto(path);
  for (const row of subjects) {
    const link = page.getByRole("link", { name: `Inspect ${row.reference}` });
    await expect(link).toHaveAttribute("href", new RegExp(`/app/quality/inspections/new\\?.*subject_type=${row.subject_type}.*subject_id=${row.subject_id}`));
  }
});

test("users without quality module cannot request the queue", async ({ page }) => {
  const state = await setup(page, { quality: false }); await page.goto(path);
  await expect(page.getByText(/access denied|outside your current module access/i).first()).toBeVisible(); expect(state.seen).toEqual([]);
});

test("loading, request-ID error, retry and empty states are distinct", async ({ page }) => {
  const state = await setup(page, { delayed: true, fail: true }); await page.goto(path);
  await expect(page.getByRole("status").filter({ hasText: /loading QA queue/i })).toBeVisible(); state.release();
  await expect(page.getByRole("alert")).toContainText("QA queue unavailable"); await expect(page.getByText("Request ID: req-qa-503")).toBeVisible();
  state.recover(); await page.getByRole("button", { name: /try again/i }).click();
  await expect(page.getByRole("table", { name: "QA queue results" })).toBeVisible();
});

test("empty queue remains honest", async ({ page }) => { await setup(page, { rows: [] }); await page.goto(path); await expect(page.getByText(/no QA work waiting/i)).toBeVisible(); });

test("waiting age and inspection readiness use text rather than color alone", async ({ page }) => {
  await setup(page); await page.goto(path); const table = page.getByRole("table", { name: "QA queue results" });
  await expect(table).toContainText(/Waiting since/i); await expect(table).toContainText("Inspection required"); await expect(table).toContainText("Inspection recorded");
});

test("URL-backed filters restore with browser back and forward", async ({ page }) => {
  await setup(page); await page.goto(path); await page.getByLabel("Queue kind").selectOption("GRN_LINE"); await expect(page).toHaveURL(/GRN_LINE/);
  await page.getByLabel("Queue kind").selectOption("PROD_LOT"); await page.goBack(); await expect(page.getByLabel("Queue kind")).toHaveValue("GRN_LINE"); await page.goForward(); await expect(page.getByLabel("Queue kind")).toHaveValue("PROD_LOT");
});

test("mobile queue is keyboard usable without document overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 }); await setup(page); await page.goto(path);
  await expect(page.getByRole("heading", { name: "QA queue" })).toBeVisible(); expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await page.getByRole("link", { name: "Inspect GRN-0042/LOT-9" }).focus(); await expect(page.getByRole("link", { name: "Inspect GRN-0042/LOT-9" })).toBeFocused();
});
