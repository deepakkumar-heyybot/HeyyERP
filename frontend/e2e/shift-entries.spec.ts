import { expect, test, type Page } from "@playwright/test";
import { cors, grantedProfile, mockSignInAndMe, plantAlpha, plantBeta } from "./support/fixtures";

test.use({ serviceWorkers: "block" });

const registerPath = "/app/manufacturing/shift-entries";
const createPath = `${registerPath}/new`;
const webOrigin = "http://127.0.0.1:5174";
const allowedOrigins = new Set([
  webOrigin,
  "http://localhost:8000",
  "http://127.0.0.1:8000",
  "http://localhost:3001",
  "http://127.0.0.1:3001",
]);
const beta = { ...plantBeta, active: true };
const workOrderId = "aaaaaaaa-1111-1111-1111-111111111111";
const completedOrderId = "aaaaaaaa-2222-2222-2222-222222222222";
const machineId = "bbbbbbbb-1111-1111-1111-111111111111";
const betaMachineId = "bbbbbbbb-2222-2222-2222-222222222222";
const mouldId = "cccccccc-1111-1111-1111-111111111111";

const workOrder = {
  work_order_id: workOrderId,
  work_order_no: "WO-0042",
  unit_id: plantAlpha.unit_id,
  item_id: "dddddddd-1111-1111-1111-111111111111",
  item_code: "FG-042",
  item_name: "Suspension bush",
  bom_id: "eeeeeeee-1111-1111-1111-111111111111",
  route_id: "ffffffff-1111-1111-1111-111111111111",
  route_code: "ROUTE-042",
  planned_qty: "1000.000",
  started_qty: "123.450",
  packed_qty: "0.000",
  status: "RELEASED",
  planned_start: null,
  planned_finish: null,
};
const machine = {
  machine_id: machineId,
  unit_id: plantAlpha.unit_id,
  code: "PRESS-01",
  name: "Hydraulic press 1",
  machine_type: "PRESS",
  active: true,
  hourly_rate: "750.00",
  labour_per_hour: "2.00",
};
const mould = {
  mould_id: mouldId,
  unit_id: plantAlpha.unit_id,
  code: "MLD-042",
  part_item_id: workOrder.item_id,
  part_code: workOrder.item_code,
  shots_limit: 1000000,
  active: true,
};
const baseEntry = {
  shift_entry_id: "99999999-1111-1111-1111-111111111111",
  unit_id: plantAlpha.unit_id,
  work_order_id: workOrderId,
  work_order_no: "WO-0042",
  machine_id: machineId,
  machine_code: "PRESS-01",
  operator: "Asha Operator",
  work_date: "2026-11-05",
  shift_code: "A",
  good_qty: "123.450",
  reject_qty: "2.300",
  downtime_minutes: 17,
  mould_code: "MLD-042",
  shots: 3456,
  losses: [
    { kind: "REJECT", reason: "Surface mark", quantity: "2.300", minutes: 0 },
    { kind: "DOWNTIME", reason: "Mould change", quantity: "0.000", minutes: 17 },
  ],
};
type ShiftEntry = typeof baseEntry;
type PostMode = "success" | "pending" | "conflict" | "server" | "transport";
type SetupOptions = {
  manufacturing?: boolean;
  enter?: boolean;
  twoPlants?: boolean;
  entries?: ShiftEntry[];
  delayedError?: boolean;
  postMode?: PostMode;
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
  const listRequests: URLSearchParams[] = [];
  const missingRequests: URLSearchParams[] = [];
  const posts: unknown[] = [];
  const unexpected: string[] = [];
  const blockedOrigins: string[] = [];
  const units = (options.twoPlants ? [plantAlpha, beta] : [plantAlpha])
    .map(unit => ({ ...unit, gstin: null, state_code: null }));
  const authorized = units.map(unit => unit.unit_id);
  let failing = Boolean(options.delayedError);
  let releaseList!: () => void;
  const listGate = new Promise<void>(resolve => { releaseList = resolve; });
  if (!options.delayedError) releaseList();
  let releasePost!: () => void;
  const postGate = new Promise<void>(resolve => { releasePost = resolve; });
  if (options.postMode !== "pending") releasePost();

  // A missed mock must never reach a live backend; only local Vite assets may continue.
  await page.route("**/*", route => {
    const request = route.request();
    const url = new URL(request.url());
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
    permissions: options.enter === false
      ? grantedProfile.permissions.filter(permission => permission !== "mfg.entry")
      : [...grantedProfile.permissions, "mfg.entry"],
    unit_ids: authorized,
    all_plants: false,
  } }, units);

  await page.route(/\/api\/v1\/manufacturing\/work-orders(?:\?.*)?$/, route => {
    const request = route.request();
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    if (request.method() !== "GET") return route.fulfill(json({ detail: "Unexpected write" }, 405));
    const orders = [
      workOrder,
      { ...workOrder, work_order_id: completedOrderId, work_order_no: "WO-0043", status: "COMPLETED" },
      { ...workOrder, work_order_id: "aaaaaaaa-3333-3333-3333-333333333333", work_order_no: "WO-PLANNED", status: "PLANNED" },
      { ...workOrder, work_order_id: "aaaaaaaa-4444-4444-4444-444444444444", work_order_no: "WO-OUTSIDE", unit_id: plantBeta.unit_id },
    ];
    const params = new URL(request.url()).searchParams;
    const matching = orders.filter(order => authorized.includes(order.unit_id)
      && (!params.has("status") || order.status === params.get("status")?.toUpperCase()));
    const limit = Number(params.get("limit") ?? 50), offset = Number(params.get("offset") ?? 0);
    return route.fulfill(json({ items: matching.slice(offset, offset + limit), total: matching.length, limit, offset }));
  });
  await page.route(/\/api\/v1\/manufacturing\/(machines|moulds)(?:\?.*)?$/, route => {
    const request = route.request();
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    if (request.method() !== "GET") return route.fulfill(json({ detail: "Unexpected write" }, 405));
    const params = new URL(request.url()).searchParams;
    const isMachine = new URL(request.url()).pathname.endsWith("/machines");
    const rows = isMachine
      ? [machine,
          { ...machine, machine_id: betaMachineId, unit_id: plantBeta.unit_id, code: "PRESS-B", name: "Beta press" },
          { ...machine, machine_id: "bbbbbbbb-3333-3333-3333-333333333333", code: "PRESS-OLD", active: false }]
      : [mould,
          { ...mould, mould_id: "cccccccc-2222-2222-2222-222222222222", unit_id: plantBeta.unit_id, code: "MLD-B" },
          { ...mould, mould_id: "cccccccc-3333-3333-3333-333333333333", code: "MLD-OLD", active: false }];
    const matching = rows.filter(row => authorized.includes(row.unit_id)
      && (!params.has("unit_id") || row.unit_id === params.get("unit_id"))
      && (!params.has("active") || String(row.active) === params.get("active")));
    const limit = Number(params.get("limit") ?? 50), offset = Number(params.get("offset") ?? 0);
    return route.fulfill(json({ items: matching.slice(offset, offset + limit), total: matching.length, limit, offset }));
  });
  await page.route(/\/api\/v1\/manufacturing\/shift-entries\/missing(?:\?.*)?$/, route => {
    const request = route.request();
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    const params = new URL(request.url()).searchParams;
    missingRequests.push(new URLSearchParams(params));
    const missing = [{ unit_id: plantAlpha.unit_id, machine_id: machineId, machine_code: "PRESS-01",
      work_date: params.get("work_date") ?? "2026-11-05", shift_code: params.get("shift_code") ?? "B" }];
    return route.fulfill(json(missing));
  });
  await page.route(/\/api\/v1\/manufacturing\/shift-entries(?:\?.*)?$/, async route => {
    const request = route.request();
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    if (request.method() === "POST") {
      posts.push(request.postDataJSON());
      await postGate;
      if (options.postMode === "transport") return route.abort("failed");
      if (options.postMode === "conflict") return route.fulfill(json(
        { detail: "This machine already has an entry for that shift and work order" },
        409,
        { "X-Request-ID": "req-shift-conflict" },
      ));
      if (options.postMode === "server") return route.fulfill(json(
        { detail: "Shift service unavailable" },
        503,
        { "X-Request-ID": "req-shift-unknown" },
      ));
      return route.fulfill(json(baseEntry, 201));
    }
    if (request.method() !== "GET") return route.fulfill(json({ detail: "Unsupported method" }, 405));
    const params = new URL(request.url()).searchParams;
    listRequests.push(new URLSearchParams(params));
    await listGate;
    if (failing) return route.fulfill(json(
      { detail: "Shift entries temporarily unavailable" },
      503,
      { "X-Request-ID": "req-shifts-503" },
    ));
    const matching = (options.entries ?? [baseEntry]).filter(entry => authorized.includes(entry.unit_id)
      && (!params.has("work_order_id") || entry.work_order_id === params.get("work_order_id"))
      && (!params.has("machine_id") || entry.machine_id === params.get("machine_id"))
      && (!params.has("date_from") || entry.work_date >= params.get("date_from")!)
      && (!params.has("date_to") || entry.work_date <= params.get("date_to")!));
    const limit = Number(params.get("limit") ?? 50), offset = Number(params.get("offset") ?? 0);
    return route.fulfill(json({ items: matching.slice(offset, offset + limit), total: matching.length, limit, offset }));
  });
  // Runs first. No browser request may leave the explicitly allowed local origins.
  await page.route("**/*", route => {
    const origin = new URL(route.request().url()).origin;
    if (allowedOrigins.has(origin)) return route.fallback();
    blockedOrigins.push(origin);
    return route.abort();
  });
  return {
    listRequests,
    missingRequests,
    posts,
    unexpected,
    blockedOrigins,
    releaseList,
    releasePost,
    recover: () => { failing = false; },
  };
}

const heading = (page: Page) => page.getByRole("heading", { name: /^shift entries$/i });
const createHeading = (page: Page) => page.getByRole("heading", { name: /^(new|record) shift entry$/i });
const results = (page: Page, mobile = false) => page.getByRole(mobile ? "list" : "table", { name: "Shift entry results" });
const createEntry = (page: Page) => page.getByRole("link", { name: /^(new|record) shift entry$/i })
  .or(page.getByRole("button", { name: /^(new|record) shift entry$/i }));
const submit = (page: Page) => page.getByRole("button", { name: /^(record|save) shift entry$/i });

function assertSupportedListQueries(requests: URLSearchParams[]) {
  expect(requests.length).toBeGreaterThan(0);
  for (const params of requests) {
    expect([...params.keys()].every(key => [
      "work_order_id", "machine_id", "date_from", "date_to", "limit", "offset",
    ].includes(key))).toBe(true);
    expect(params.has("shift_code")).toBe(false);
    expect(params.has("unit_id")).toBe(false);
    expect(Number(params.get("limit") ?? 50)).toBeGreaterThanOrEqual(1);
    expect(Number(params.get("limit") ?? 50)).toBeLessThanOrEqual(200);
    expect(Number(params.get("offset") ?? 0)).toBeGreaterThanOrEqual(0);
  }
}

function assertNoLeaks(state: Awaited<ReturnType<typeof setup>>) {
  expect(state.unexpected).toEqual([]);
  expect(state.blockedOrigins).toEqual([]);
}

async function openCreate(page: Page) {
  await page.goto(createPath);
  await expect(createHeading(page)).toBeVisible();
}

async function fillValidCreate(page: Page) {
  await page.getByLabel("Work order", { exact: true }).selectOption(workOrderId);
  await page.getByLabel("Machine", { exact: true }).selectOption(machineId);
  await page.getByLabel("Work date", { exact: true }).fill("2026-11-05");
  await page.getByLabel("Shift", { exact: true }).selectOption("B");
  await page.getByLabel("Good quantity", { exact: true }).fill("123.450");
  await page.getByRole("button", { name: /add reject/i }).click();
  await page.getByLabel("Reject reason 1").fill("Surface mark");
  await page.getByLabel("Reject quantity 1").fill("2.300");
  await page.getByRole("button", { name: /add downtime/i }).click();
  await page.getByLabel("Downtime reason 1").fill("Mould change");
  await page.getByLabel("Downtime minutes 1").fill("17");
  await page.getByLabel("Mould", { exact: true }).selectOption(mouldId);
  await page.getByLabel("Shots", { exact: true }).fill("3456");
}

test("register shows exact decimal strings, operator, losses, mould and shots", async ({ page }) => {
  const state = await setup(page);
  await page.goto(registerPath);
  await expect(heading(page)).toBeVisible();
  const row = results(page).getByRole("row", { name: /WO-0042/ });
  await expect(row).toContainText("PRESS-01");
  await expect(row).toContainText("Asha Operator");
  await expect(row).toContainText("123.450");
  await expect(row).toContainText("2.300");
  await expect(row).toContainText("17");
  await expect(row).toContainText("MLD-042");
  await expect(row).toContainText("3456");
  await expect(page.getByText("Surface mark")).toBeVisible();
  await expect(page.getByText("Mould change")).toBeVisible();
  assertSupportedListQueries(state.listRequests);
  assertNoLeaks(state);
});

test("supported filters are sent to the server and pagination reaches later rows", async ({ page }) => {
  const entries = Array.from({ length: 225 }, (_, index) => ({
    ...baseEntry,
    shift_entry_id: `99999999-2222-2222-2222-${String(index).padStart(12, "0")}`,
    work_order_no: `WO-${String(index).padStart(4, "0")}`,
    work_date: index < 200 ? "2026-11-05" : "2026-11-04",
  }));
  const state = await setup(page, { entries });
  await page.goto(registerPath);
  await expect(heading(page)).toBeVisible();
  await page.getByLabel("Work order filter").selectOption(workOrderId);
  await page.getByLabel("Machine filter").selectOption(machineId);
  await page.getByLabel("From date").fill("2026-11-04");
  await page.getByLabel("To date").fill("2026-11-05");
  await expect.poll(() => state.listRequests.some(params => params.get("work_order_id") === workOrderId
    && params.get("machine_id") === machineId
    && params.get("date_from") === "2026-11-04"
    && params.get("date_to") === "2026-11-05")).toBe(true);
  await page.getByRole("button", { name: /next page/i }).click();
  await expect.poll(() => state.listRequests.some(params => Number(params.get("offset") ?? 0) > 0)).toBe(true);
  assertSupportedListQueries(state.listRequests);
  assertNoLeaks(state);
});

test("shift and plant filters exhaust all server pages before filtering locally", async ({ page }) => {
  const entries = Array.from({ length: 225 }, (_, index) => ({
    ...baseEntry,
    shift_entry_id: `99999999-3333-3333-3333-${String(index).padStart(12, "0")}`,
    work_order_no: `WO-${String(index).padStart(4, "0")}`,
    ...(index === 221 ? {
      unit_id: plantBeta.unit_id,
      machine_id: betaMachineId,
      machine_code: "PRESS-B",
      shift_code: "C",
      good_qty: "987654321.123",
    } : {}),
  }));
  const state = await setup(page, { twoPlants: true, entries });
  await page.goto(registerPath);
  await expect(heading(page)).toBeVisible();
  await page.getByLabel("Plant filter").selectOption(plantBeta.unit_id);
  await page.getByLabel("Shift filter").selectOption("C");
  await expect(results(page)).toContainText("WO-0221");
  await expect(results(page)).toContainText("987654321.123");
  await expect(results(page)).not.toContainText("WO-0000");
  await expect.poll(() => state.listRequests.some(params => Number(params.get("offset") ?? 0) >= 200)).toBe(true);
  assertSupportedListQueries(state.listRequests);
  assertNoLeaks(state);
});

test("module readers can view entries, while mfg.entry alone controls creation", async ({ page }) => {
  const reader = await setup(page, { enter: false });
  await page.goto(registerPath);
  await expect(heading(page)).toBeVisible();
  await expect(results(page)).toContainText("WO-0042");
  await expect(createEntry(page)).toHaveCount(0);
  await page.goto(createPath);
  await expect(page.getByText(/mfg\.entry.*required|requires permission mfg\.entry/i)).toBeVisible();
  expect(reader.posts).toEqual([]);
  assertNoLeaks(reader);
});

test("without the manufacturing module no shift data is requested", async ({ page }) => {
  const state = await setup(page, { manufacturing: false, enter: true });
  await page.goto(registerPath);
  await expect(heading(page)).toHaveCount(0);
  await expect(page.getByText(/access denied|outside your current module access|manufacturing.*unavailable/i).first()).toBeVisible();
  expect(state.listRequests).toHaveLength(0);
  expect(state.posts).toHaveLength(0);
});

test("loading, request-ID error, retry and empty states remain distinct", async ({ page }) => {
  const state = await setup(page, { entries: [], delayedError: true });
  await page.goto(registerPath);
  await expect(heading(page)).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: /loading.*shift entries/i })).toBeVisible();
  state.releaseList();
  await expect(page.getByRole("alert")).toContainText("Shift entries temporarily unavailable");
  await expect(page.getByText("Request ID: req-shifts-503")).toBeVisible();
  state.recover();
  await page.getByRole("button", { name: /try again/i }).click();
  await expect(page.getByText(/no shift entries found/i)).toBeVisible();
  assertSupportedListQueries(state.listRequests);
  assertNoLeaks(state);
});

test("create uses eligible scoped selectors and submits the exact nested payload", async ({ page }) => {
  const state = await setup(page);
  await openCreate(page);
  await expect(page.getByLabel("Work order", { exact: true }).locator("option")).not.toContainText(["WO-PLANNED", "WO-OUTSIDE"]);
  await expect(page.getByLabel("Machine", { exact: true }).locator("option")).not.toContainText(["PRESS-OLD", "PRESS-B"]);
  await expect(page.getByLabel("Mould", { exact: true }).locator("option")).not.toContainText(["MLD-OLD", "MLD-B"]);
  await fillValidCreate(page);
  await submit(page).click();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0]).toEqual({
    work_order_id: workOrderId,
    machine_id: machineId,
    work_date: "2026-11-05",
    shift_code: "B",
    good_qty: "123.450",
    rejects: [{ reason: "Surface mark", quantity: "2.300" }],
    downtime: [{ reason: "Mould change", minutes: 17 }],
    mould_id: mouldId,
    shots: 3456,
  });
  await expect(page.getByText(/shift entry.*recorded|WO-0042/i).first()).toBeVisible();
  assertNoLeaks(state);
});

test("client validation enforces future date, nonempty output, reason rows, downtime and mould-shot pairing", async ({ page }) => {
  const state = await setup(page);
  await openCreate(page);
  await page.getByLabel("Work order", { exact: true }).selectOption(workOrderId);
  await page.getByLabel("Machine", { exact: true }).selectOption(machineId);
  await page.getByLabel("Work date", { exact: true }).fill("2099-01-01");
  await page.getByLabel("Shift", { exact: true }).selectOption("A");
  await page.getByLabel("Good quantity", { exact: true }).fill("0");
  await page.getByRole("button", { name: /add reject/i }).click();
  await page.getByLabel("Reject reason 1").fill("X");
  await page.getByLabel("Reject quantity 1").fill("0");
  await page.getByRole("button", { name: /add downtime/i }).click();
  await page.getByLabel("Downtime reason 1").fill("Breakdown");
  await page.getByLabel("Downtime minutes 1").fill("481");
  await page.getByLabel("Mould", { exact: true }).selectOption(mouldId);
  await submit(page).click();
  await expect(page.getByText(/future/i).first()).toBeVisible();
  await expect(page.getByText(/reason.*2|at least 2/i).first()).toBeVisible();
  await expect(page.getByText(/reject.*greater than 0|positive reject/i).first()).toBeVisible();
  await expect(page.getByText(/downtime.*480/i).first()).toBeVisible();
  await expect(page.getByText(/mould.*shots.*together|shots.*required/i).first()).toBeVisible();
  expect(state.posts).toEqual([]);
  assertNoLeaks(state);
});

test("mobile missing-entry context prefills only machine, date and shift without overflow", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const state = await setup(page);
  await page.goto(`${createPath}?machine_id=${machineId}&work_date=2026-11-05&shift_code=B`);
  await expect(createHeading(page)).toBeVisible();
  await expect(page.getByLabel("Machine", { exact: true })).toHaveValue(machineId);
  await expect(page.getByLabel("Work date", { exact: true })).toHaveValue("2026-11-05");
  await expect(page.getByLabel("Shift", { exact: true })).toHaveValue("B");
  await expect(page.getByLabel("Work order", { exact: true })).toHaveValue("");
  await page.getByLabel("Work order", { exact: true }).focus();
  await expect(page.getByLabel("Work order", { exact: true })).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  expect(state.posts).toEqual([]);
  assertNoLeaks(state);
});

test("a pending request cannot be submitted twice", async ({ page }) => {
  const state = await setup(page, { postMode: "pending" });
  await openCreate(page);
  await fillValidCreate(page);
  await submit(page).click();
  await expect.poll(() => state.posts.length).toBe(1);
  await expect(submit(page)).toBeDisabled();
  await submit(page).evaluate(button => button.form?.requestSubmit());
  expect(state.posts).toHaveLength(1);
  state.releasePost();
  await expect(page.getByText(/shift entry.*recorded|WO-0042/i).first()).toBeVisible();
  assertNoLeaks(state);
});

test("a 409 is a definite rejection that preserves inputs for correction", async ({ page }) => {
  const state = await setup(page, { postMode: "conflict" });
  await openCreate(page);
  await fillValidCreate(page);
  await submit(page).click();
  await expect(page.getByRole("alert")).toContainText("This machine already has an entry for that shift and work order");
  await expect(page.getByText("Request ID: req-shift-conflict")).toBeVisible();
  await expect(page.getByLabel("Good quantity", { exact: true })).toHaveValue("123.450");
  await expect(submit(page)).toBeEnabled();
  expect(state.posts).toHaveLength(1);
  assertNoLeaks(state);
});

test("server unknown outcome blocks retry across reload until the register is checked", async ({ page }) => {
  const state = await setup(page, { postMode: "server" });
  await openCreate(page);
  await fillValidCreate(page);
  await submit(page).click();
  await expect(page.getByRole("alert")).toContainText(/outcome unknown/i);
  await expect(page.getByText("Request ID: req-shift-unknown")).toBeVisible();
  await expect(submit(page)).toBeDisabled();
  await page.reload();
  await expect(page.getByRole("alert")).toContainText(/outcome unknown/i);
  await expect(page.getByLabel("Good quantity", { exact: true })).toHaveValue("123.450");
  await expect(page.getByRole("link", { name: /open shift entry register/i })).toHaveAttribute("target", "_blank");
  await page.getByRole("button", { name: /I checked the shift entry register/i }).click();
  await expect(submit(page)).toBeEnabled();
  expect(state.posts).toHaveLength(1);
  assertNoLeaks(state);
});
