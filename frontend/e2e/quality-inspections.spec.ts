import { expect, test, type Page } from "@playwright/test";
import { cors, grantedProfile, mockSignInAndMe, plantAlpha } from "./support/fixtures";

test.use({ serviceWorkers: "block" });

const registerPath = "/app/quality/inspections";
const inspectionId = "11111111-aaaa-aaaa-aaaa-111111111111";
const subjectId = "22222222-bbbb-bbbb-bbbb-222222222222";
const okInstrumentId = "33333333-cccc-cccc-cccc-333333333333";
const overdueInstrumentId = "44444444-dddd-dddd-dddd-444444444444";
const neverInstrumentId = "55555555-eeee-eeee-eeee-555555555555";

const summary = {
  inspection_id: inspectionId,
  inspection_no: "QA-00241",
  kind: "INCOMING",
  subject_type: "GRN_LINE",
  subject_id: subjectId,
  result: "ACCEPT",
  source: "MANUAL",
  inspected_by_name: "Quality Inspector",
  inspected_at: "2026-11-07T09:15:00+05:30",
  notes: "Incoming material conforms",
  unit_id: plantAlpha.unit_id,
};

const inspection = {
  ...summary,
  plan_name: "Incoming rubber inspection",
  instrument_code: "VERNIER-01",
  measurements: [
    { parameter_code: "MOISTURE", measured_value: "0.750000", unit: "%", min_value: null, max_value: "1.000000", result: "PASS" },
    { parameter_code: "PURITY", measured_value: "96.250000", unit: "%", min_value: "95.000000", max_value: null, result: "PASS" },
  ],
};

const instruments = [
  { instrument_id: okInstrumentId, unit_id: plantAlpha.unit_id, code: "VERNIER-01", name: "Digital vernier", active: true, last_calibrated_at: "2026-10-20", due_at: "2027-04-20", last_result: "PASS", calibration_status: "OK", days_to_due: 164 },
  { instrument_id: overdueInstrumentId, unit_id: plantAlpha.unit_id, code: "GAUGE-09", name: "Thickness gauge", active: true, last_calibrated_at: "2025-10-01", due_at: "2026-10-01", last_result: "PASS", calibration_status: "OVERDUE", days_to_due: -37 },
  { instrument_id: neverInstrumentId, unit_id: plantAlpha.unit_id, code: "METER-NEW", name: "New meter", active: true, last_calibrated_at: null, due_at: null, last_result: null, calibration_status: "NEVER", days_to_due: null },
];

type SeenRequest = { method: string; pathname: string; params: URLSearchParams; body?: unknown };
type SetupOptions = {
  quality?: boolean;
  manage?: boolean;
  rows?: typeof summary[];
  total?: number;
  delayedList?: boolean;
  failList?: boolean;
  rejectCreate?: boolean;
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
    modules: options.quality === false ? ["home"] : ["home", "quality"],
    permissions: options.manage ? ["qa.approve"] : [],
    all_plants: true,
    unit_ids: [],
  } }, [plantAlpha]);

  await page.route(/\/api\/v1\/quality\/inspections(?:\?.*)?$/, async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    const recorded: SeenRequest = { method: request.method(), pathname: url.pathname, params: new URLSearchParams(url.searchParams) };
    if (request.method() === "POST") recorded.body = request.postDataJSON();
    seen.push(recorded);
    if (request.method() === "POST") {
      if (options.rejectCreate) return route.fulfill(json({ detail: "Measurements outside the limits (MOISTURE) cannot be accepted; record REJECT or HOLD" }, 422, { "X-Request-ID": "req-inspection-limit" }));
      return route.fulfill(json(inspection, 201));
    }
    await gate;
    if (failList) return route.fulfill(json({ detail: "Inspections unavailable" }, 503, { "X-Request-ID": "req-inspections-503" }));
    const rows = options.rows ?? [summary];
    return route.fulfill(json({ items: rows, total: options.total ?? rows.length, limit: Number(url.searchParams.get("limit") ?? 50), offset: Number(url.searchParams.get("offset") ?? 0) }));
  });

  await page.route(new RegExp(`/api/v1/quality/inspections/${inspectionId}$`), route => {
    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    const url = new URL(route.request().url());
    seen.push({ method: route.request().method(), pathname: url.pathname, params: new URLSearchParams(url.searchParams) });
    return route.fulfill(json(inspection));
  });

  await page.route(/\/api\/v1\/quality\/instruments(?:\?.*)?$/, route => {
    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    const url = new URL(route.request().url());
    seen.push({ method: route.request().method(), pathname: url.pathname, params: new URLSearchParams(url.searchParams) });
    return route.fulfill(json({ items: instruments, total: instruments.length, limit: 200, offset: 0 }));
  });

  return { seen, unexpected, release, recover: () => { failList = false; } };
}

test("quality readers filter and page inspection history using only supported query fields", async ({ page }) => {
  const state = await setup(page, { total: 75 });
  await page.goto(registerPath);
  await expect(page.getByRole("heading", { name: "Inspections" })).toBeVisible();
  const table = page.getByRole("table", { name: "Inspections" });
  await expect(table).toContainText("QA-00241");
  await expect(table).toContainText("Quality Inspector");
  await page.getByLabel("Subject type").selectOption("GRN_LINE");
  await page.getByLabel("Inspection type").selectOption("INCOMING");
  await page.getByLabel("Result filter").selectOption("ACCEPT");
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page).toHaveURL(/subject_type=GRN_LINE/);
  await expect(page).toHaveURL(/kind=INCOMING/);
  await expect(page).toHaveURL(/result=ACCEPT/);
  await expect(page).toHaveURL(/offset=50/);
  await expect(page.getByText("Showing 51–75 of 75 records")).toBeVisible();
  const requests = state.seen.filter(request => request.pathname.endsWith("/quality/inspections") && request.method === "GET");
  expect(requests.some(request => request.params.get("subject_type") === "GRN_LINE" && request.params.get("kind") === "INCOMING" && request.params.get("result") === "ACCEPT")).toBe(true);
  expect(requests.every(request => [...request.params.keys()].every(key => ["subject_type", "subject_id", "kind", "result", "limit", "offset"].includes(key)))).toBe(true);
  expect(state.unexpected).toEqual([]);
});

test("inspection detail presents the backend-resolved plan, instrument and exact decimal measurements", async ({ page }) => {
  await setup(page);
  await page.goto(`${registerPath}/${inspectionId}`);
  await expect(page.getByRole("heading", { name: "QA-00241" })).toBeVisible();
  await expect(page.getByText("Incoming rubber inspection")).toBeVisible();
  await expect(page.getByText("VERNIER-01")).toBeVisible();
  const measurements = page.getByRole("table", { name: "Inspection measurements" });
  await expect(measurements).toContainText("0.750000");
  await expect(measurements).toContainText("1.000000");
  await expect(measurements).toContainText("96.250000");
  await expect(measurements).toContainText("95.000000");
  await expect(measurements).toContainText("PASS");
});

test("creation requires qa.approve and prefills only the verified subject identity query", async ({ page }) => {
  const reader = await setup(page);
  await page.goto(registerPath);
  await expect(page.getByRole("link", { name: /record inspection/i })).toHaveCount(0);
  expect(reader.seen.every(request => request.method === "GET")).toBe(true);

  await page.unrouteAll({ behavior: "wait" });
  await setup(page, { manage: true });
  await page.goto(`${registerPath}/new?subject_type=GRN_LINE&subject_id=${subjectId}`);
  await expect(page.getByRole("heading", { name: "Record inspection" })).toBeVisible();
  await expect(page.getByLabel("Subject type")).toHaveValue("GRN_LINE");
  await expect(page.getByLabel("Subject ID")).toHaveValue(subjectId);
});

test("recording submits exact measurement strings while the backend resolves and validates the active plan", async ({ page }) => {
  const state = await setup(page, { manage: true });
  await page.goto(`${registerPath}/new?subject_type=GRN_LINE&subject_id=${subjectId}`);
  await page.getByLabel("Inspection type").selectOption("INCOMING");
  await page.getByLabel("Result").selectOption("ACCEPT");
  await page.getByLabel("Instrument").selectOption(okInstrumentId);
  await page.getByLabel("Parameter code 1").fill("MOISTURE");
  await page.getByLabel("Measured value 1").fill("0.750000");
  await page.getByLabel("Unit 1").fill("%");
  await page.getByRole("button", { name: /add measurement/i }).click();
  await page.getByLabel("Parameter code 2").fill("PURITY");
  await page.getByLabel("Measured value 2").fill("96.250000");
  await page.getByLabel("Unit 2").fill("%");
  await page.getByRole("button", { name: /record inspection/i }).click();
  const create = state.seen.find(request => request.method === "POST" && request.pathname.endsWith("/quality/inspections"));
  expect(create?.body).toEqual({
    kind: "INCOMING",
    subject_type: "GRN_LINE",
    subject_id: subjectId,
    result: "ACCEPT",
    notes: null,
    instrument_id: okInstrumentId,
    measurements: [
      { parameter_code: "MOISTURE", value: "0.750000", unit: "%" },
      { parameter_code: "PURITY", value: "96.250000", unit: "%" },
    ],
  });
  await expect(page).toHaveURL(`${registerPath}/${inspectionId}?created=1`);
  await expect(page.getByText(/inspection recorded/i)).toBeVisible();
});

test("out-of-limit values cannot be accepted and server safety details preserve entered measurements", async ({ page }) => {
  await setup(page, { manage: true, rejectCreate: true });
  await page.goto(`${registerPath}/new?subject_type=GRN_LINE&subject_id=${subjectId}`);
  await page.getByLabel("Inspection type").selectOption("INCOMING");
  await page.getByLabel("Result").selectOption("ACCEPT");
  await page.getByLabel("Parameter code 1").fill("MOISTURE");
  await page.getByLabel("Measured value 1").fill("1.250000");
  await page.getByRole("button", { name: /record inspection/i }).click();
  await expect(page.getByRole("alert")).toContainText("cannot be accepted");
  await expect(page.getByText("Request ID: req-inspection-limit")).toBeVisible();
  await expect(page.getByLabel("Measured value 1")).toHaveValue("1.250000");
  await expect(page.getByLabel("Result")).toHaveValue("ACCEPT");
});

test("instrument choices expose calibration status and prevent contract-defined unusable instruments", async ({ page }) => {
  const state = await setup(page, { manage: true });
  await page.goto(`${registerPath}/new?subject_type=GRN_LINE&subject_id=${subjectId}`);
  const instrument = page.getByLabel("Instrument");
  await expect(instrument.locator(`option[value="${okInstrumentId}"]`)).toContainText("OK");
  await expect(instrument.locator(`option[value="${overdueInstrumentId}"]`)).toBeDisabled();
  await expect(instrument.locator(`option[value="${overdueInstrumentId}"]`)).toContainText("OVERDUE");
  await expect(instrument.locator(`option[value="${neverInstrumentId}"]`)).toBeDisabled();
  await expect(instrument.locator(`option[value="${neverInstrumentId}"]`)).toContainText("NEVER");
  const list = state.seen.find(request => request.pathname.endsWith("/quality/instruments"));
  expect(list?.params.get("active")).toBe("true");
  expect([...list!.params.keys()].every(key => ["unit_id", "calibration_status", "active", "limit", "offset"].includes(key))).toBe(true);
});

test("loading, request-ID error, retry and empty inspection states remain distinct", async ({ page }) => {
  const state = await setup(page, { delayedList: true, failList: true });
  await page.goto(registerPath);
  await expect(page.getByRole("status").filter({ hasText: /loading inspections/i })).toBeVisible();
  state.release();
  await expect(page.getByRole("alert")).toContainText("Inspections unavailable");
  await expect(page.getByText("Request ID: req-inspections-503")).toBeVisible();
  state.recover();
  await page.getByRole("button", { name: /try again/i }).click();
  await expect(page.getByRole("table", { name: "Inspections" })).toBeVisible();

  await page.unrouteAll({ behavior: "wait" });
  await setup(page, { rows: [], total: 0 });
  await page.goto(registerPath);
  await expect(page.getByText(/no inspections/i)).toBeVisible();
});

test("users without the quality module are denied before inspection data is requested", async ({ page }) => {
  const state = await setup(page, { quality: false, manage: true });
  await page.goto(registerPath);
  await expect(page.getByText(/access denied|outside your current module access/i).first()).toBeVisible();
  expect(state.seen.filter(request => request.pathname.includes("/quality/inspections"))).toEqual([]);
});

test("mobile inspection register and detail are keyboard usable without document overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page, { manage: true });
  await page.goto(registerPath);
  await expect(page.getByRole("heading", { name: "Inspections" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  const open = page.getByRole("link", { name: /open inspection QA-00241/i });
  await open.focus();
  await expect(open).toBeFocused();
  await open.click();
  await expect(page.getByRole("table", { name: "Inspection measurements" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
});

test("desktop inspection history keeps inspector and timestamp columns readable", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await setup(page);
  await page.goto(registerPath);
  const row = page.getByRole("table", { name: "Inspections" }).getByRole("row").filter({ hasText: "QA-00241" });
  const rowBox = await row.boundingBox();
  const inspectorBox = await row.getByText("Quality Inspector", { exact: true }).boundingBox();
  expect(rowBox?.height ?? 999).toBeLessThan(100);
  expect(inspectorBox?.width ?? 0).toBeGreaterThan(90);
});
