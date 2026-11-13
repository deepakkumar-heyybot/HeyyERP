import { expect, test, type Page } from "@playwright/test";
import { cors, grantedProfile, mockSignInAndMe, plantAlpha, plantBeta } from "./support/fixtures";

test.use({ serviceWorkers: "block" });

const lotId = "eeeeeeee-1111-1111-1111-111111111111";
const path = `/app/manufacturing/lots/${lotId}`;
const fgStoreId = "ffffffff-1111-1111-1111-111111111111";
const webOrigin = "http://127.0.0.1:5174";
const apiOrigins = new Set([
  webOrigin,
  "http://localhost:8000",
  "http://127.0.0.1:8000",
  "http://localhost:3001",
  "http://127.0.0.1:3001",
]);
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

const baseLot = {
  prod_lot_id: lotId,
  lot_no: "PL-0042",
  unit_id: plantAlpha.unit_id,
  work_order_id: "dddddddd-1111-1111-1111-111111111111",
  work_order_no: "WO-0042",
  item_id: "aaaaaaaa-1111-1111-1111-111111111111",
  item_code: "FG-BUSH-01",
  route_code: "MOULD-01",
  stage_code: "MOULDING",
  stage_name: "Moulding",
  is_qa_gate: false,
  is_end: false,
  quantity: "12.340",
  status: "IN_PROCESS",
  created_at: "2026-11-06T09:30:00Z",
  sources: [
    { consumption_type: "RAW_MATERIAL", quantity: "6.170", source: "RM-LOT-009", item_code: "RM-RUBBER", consumed_at: "2026-11-06T09:31:00Z" },
    { consumption_type: "COMPOUND_BATCH", quantity: "2.468", source: "BAT-0019", item_code: null, consumed_at: "2026-11-06T09:31:10Z" },
    { consumption_type: "METAL_PROD_LOT", quantity: "12.340", source: "PL-METAL-0007", item_code: "WIP-INSERT", consumed_at: "2026-11-06T09:31:20Z" },
  ],
  moves: [
    { from_stage: null, to_stage: "CUTTING", actor: "Asha Patel", quantity_in: "12.340", quantity_out: "12.340", rejects: "0.000", moved_at: "2026-11-06T09:32:00Z", notes: null },
    { from_stage: "CUTTING", to_stage: "MOULDING", actor: "Ravi Kumar", quantity_in: "12.340", quantity_out: "12.215", rejects: "0.125", moved_at: "2026-11-06T10:15:00Z", notes: "Surface defect" },
  ],
  packing: null,
};
type Lot = typeof baseLot;

const qaLot: Lot = {
  ...baseLot,
  stage_code: "FINAL_QA",
  stage_name: "Final quality inspection",
  is_qa_gate: true,
};
const packingLot: Lot = {
  ...baseLot,
  stage_code: "PACKING",
  stage_name: "Packing",
  quantity: "12.215",
};
const movedLot: Lot = {
  ...baseLot,
  stage_code: "FINAL_QA",
  stage_name: "Final quality inspection",
  is_qa_gate: true,
  quantity: "12.215",
  moves: [...baseLot.moves, {
    from_stage: "MOULDING",
    to_stage: "FINAL_QA",
    actor: "Akshita K.",
    quantity_in: "12.340",
    quantity_out: "12.215",
    rejects: "0.125",
    moved_at: "2026-11-06T11:00:00Z",
    notes: "Move after moulding",
  }],
};
const packedLot: Lot = {
  ...packingLot,
  stage_code: "FG_STORE",
  stage_name: "Finished-goods store",
  is_end: true,
  status: "COMPLETED",
  packing: {
    label_code: "PK-0042",
    packed_qty: "12.215",
    packed_at: "2026-11-06T12:00:00Z",
    fg_store_id: fgStoreId,
    fg_lot_no: "PL-0042",
  },
};

const trace = {
  lot_no: "PL-0042",
  quantity: "12.340",
  status: "IN_PROCESS",
  item_code: "FG-BUSH-01",
  work_order_no: "WO-0042",
  raw_material: [{ lot_no: "RM-LOT-009", item_code: "RM-RUBBER", quantity: "6.170" }],
  compound_batches: [{
    batch_no: "BAT-0019",
    compound_code: "CMP-01",
    quantity: "2.468",
    raw_material: [{ lot_no: "CMP-RM-001", item_code: "RM-CHEM", quantity: "0.617" }],
  }],
  metal_lots: [{
    lot_no: "PL-METAL-0007",
    quantity: "12.340",
    quantity_used: "12.340",
    status: "READY",
    item_code: "WIP-INSERT",
    work_order_no: "WO-METAL-0007",
    raw_material: [],
    compound_batches: [],
    metal_lots: [],
  }],
  packing: null,
};

type Action = "move" | "pack" | "rework" | "return-material";
type Reply = { status: number; body: unknown; requestId?: string };
type Sent = { action: Action; method: string; body: unknown };
type SetupOptions = {
  lot?: Lot;
  manufacturing?: boolean;
  canMove?: boolean;
  canQa?: boolean;
  delayDetail?: boolean;
  detailReply?: Reply;
  traceReply?: Reply;
  actionReply?: Partial<Record<Action, Reply>>;
};

async function setup(page: Page, options: SetupOptions = {}) {
  let current = options.lot ?? baseLot;
  const sent: Sent[] = [];
  const unexpected: string[] = [];
  const blockedOrigins: string[] = [];
  let detailGets = 0;
  let traceGets = 0;
  let releaseDetail!: () => void;
  const detailGate = new Promise<void>(resolve => { releaseDetail = resolve; });
  if (!options.delayDetail) releaseDetail();

  // Any API not explicitly mocked below is a test failure and never reaches a backend.
  await page.route("**/*", route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/")) {
      unexpected.push(`${request.method()} ${url.pathname}`);
      return route.fulfill(json({ detail: "Unmocked API request" }, 404));
    }
    return url.origin === webOrigin ? route.continue() : route.abort();
  });

  const permissions = grantedProfile.permissions.filter(permission => permission !== "route.move" && permission !== "qa.approve");
  if (options.canMove) permissions.push("route.move");
  if (options.canQa) permissions.push("qa.approve");
  await mockSignInAndMe(page, { status: 200, body: {
    ...grantedProfile,
    modules: options.manufacturing === false
      ? grantedProfile.modules.filter(module => module !== "manufacturing")
      : [...new Set([...grantedProfile.modules, "manufacturing"])],
    permissions,
    unit_ids: [plantAlpha.unit_id],
    all_plants: false,
  } }, [{ ...plantAlpha, gstin: null, state_code: null }]);

  await page.route(new RegExp(`/api/v1/manufacturing/lots/${lotId}$`), async route => {
    if (route.request().method() !== "GET") return route.fulfill(json({ detail: "Method not allowed" }, 405));
    detailGets += 1;
    await detailGate;
    const reply = options.detailReply;
    return route.fulfill(json(reply?.body ?? current, reply?.status ?? 200,
      reply?.requestId ? { "X-Request-ID": reply.requestId } : {}));
  });
  await page.route(new RegExp(`/api/v1/manufacturing/lots/${lotId}/trace$`), route => {
    if (route.request().method() !== "GET") return route.fulfill(json({ detail: "Method not allowed" }, 405));
    traceGets += 1;
    const reply = options.traceReply;
    return route.fulfill(json(reply?.body ?? trace, reply?.status ?? 200,
      reply?.requestId ? { "X-Request-ID": reply.requestId } : {}));
  });
  await page.route(/\/api\/v1\/org\/stores(?:\?.*)?$/, route => {
    if (route.request().method() !== "GET") return route.fulfill(json({ detail: "Method not allowed" }, 405));
    const stores = [
      { store_id: fgStoreId, unit_id: plantAlpha.unit_id, code: "FG-A", name: "Finished goods", store_type: "FINISHED_GOODS", active: true },
      { store_id: "ffffffff-2222-2222-2222-222222222222", unit_id: plantAlpha.unit_id, code: "FG-OLD", name: "Inactive FG", store_type: "FINISHED_GOODS", active: false },
      { store_id: "ffffffff-3333-3333-3333-333333333333", unit_id: plantBeta.unit_id, code: "FG-B", name: "Other plant FG", store_type: "FINISHED_GOODS", active: true },
    ];
    return route.fulfill(json({ items: stores, total: stores.length, limit: 200, offset: 0 }));
  });
  await page.route(new RegExp(`/api/v1/manufacturing/lots/${lotId}/(move|pack|rework|return-material)$`), route => {
    const request = route.request();
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    const action = new URL(request.url()).pathname.split("/").at(-1) as Action;
    sent.push({ action, method: request.method(), body: request.postDataJSON() });
    if (request.method() !== "POST") return route.fulfill(json({ detail: "Method not allowed" }, 405));
    const reply = options.actionReply?.[action];
    if (reply) return route.fulfill(json(reply.body, reply.status,
      reply.requestId ? { "X-Request-ID": reply.requestId } : {}));
    current = action === "move" ? movedLot
      : action === "pack" ? packedLot
      : action === "rework" ? { ...current, quantity: "12.465" }
      : current;
    return route.fulfill(json(current));
  });

  // Runs first: permit only local app/API traffic and block accidental external calls.
  await page.route("**/*", route => {
    const request = route.request();
    const url = new URL(request.url());
    if (apiOrigins.has(url.origin) && url.pathname.startsWith("/api/") && request.method() === "OPTIONS")
      return route.fulfill({ status: 204, headers });
    if (apiOrigins.has(url.origin) && (url.pathname.startsWith("/api/") || url.origin === webOrigin))
      return route.fallback();
    blockedOrigins.push(url.origin);
    return route.abort();
  });

  return {
    sent,
    unexpected,
    blockedOrigins,
    detailGets: () => detailGets,
    traceGets: () => traceGets,
    releaseDetail,
  };
}

const heading = (page: Page) => page.getByRole("heading", { name: /PL-0042/i });
const action = (page: Page, name: RegExp) => page.getByRole("button", { name });

async function confirmIfShown(page: Page, button: ReturnType<typeof action>) {
  await button.click();
  const dialog = page.getByRole("dialog");
  if (await dialog.isVisible()) await dialog.getByRole("button", { name: /confirm|continue|yes/i }).click();
}

function expectNoLeaks(state: Awaited<ReturnType<typeof setup>>) {
  expect(state.unexpected).toEqual([]);
  expect(state.blockedOrigins).toEqual([]);
}

test("detail and trace render only backend-returned summary, movement, consumption and genealogy decimal strings", async ({ page }) => {
  const state = await setup(page);
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  const main = page.getByRole("main");
  for (const value of ["WO-0042", "FG-BUSH-01", "MOULD-01", "Moulding", "12.340", "6.170", "2.468", "0.125", "RM-LOT-009", "BAT-0019", "PL-METAL-0007", "CMP-RM-001"])
    await expect(main).toContainText(value);
  await expect(main).toContainText("Asha Patel");
  await expect(main).toContainText("Ravi Kumar");
  expect(state.detailGets()).toBeGreaterThan(0);
  expect(state.traceGets()).toBeGreaterThan(0);
  await expect(action(page, /^delete/i)).toHaveCount(0);
  await expect(action(page, /^hold/i)).toHaveCount(0);
  await expect(action(page, /^edit quantity/i)).toHaveCount(0);
  for (const name of [/move to next stage/i, /pack.*finished goods|complete packing/i, /record rework/i, /return material/i])
    await expect(action(page, name)).toHaveCount(0);
  expectNoLeaks(state);
});

test("movement history preserves readable timestamp and notes columns on desktop", async ({ page }) => {
  const state = await setup(page);
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  await expect(page.getByText(/scroll the movement history horizontally/i)).toBeVisible();
  const moved = page.getByRole("columnheader", { name: "Moved" });
  const notes = page.getByRole("columnheader", { name: "Notes" });
  expect((await moved.boundingBox())?.width ?? 0).toBeGreaterThanOrEqual(130);
  expect((await notes.boundingBox())?.width ?? 0).toBeGreaterThanOrEqual(110);
  expectNoLeaks(state);
});

test("route.move sends the exact decimal move payload and refreshes from the returned lifecycle state", async ({ page }) => {
  const state = await setup(page, { canMove: true });
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  await page.getByLabel(/reject quantity/i).fill("0.125");
  await page.getByLabel(/move notes/i).fill("Move after moulding");
  await confirmIfShown(page, action(page, /move to next stage/i));
  await expect(page.getByRole("main")).toContainText("Final quality inspection");
  expect(state.sent).toEqual([{ action: "move", method: "POST", body: { rejects: "0.125", notes: "Move after moulding" } }]);
  expectNoLeaks(state);
});

test("a QA gate dynamically requires qa.approve rather than route.move", async ({ page }) => {
  const state = await setup(page, { lot: qaLot, canMove: true });
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  await expect(page.getByText(/qa\.approve.*required|required.*qa\.approve/i)).toBeVisible();
  await expect(action(page, /move to next stage/i)).toHaveCount(0);
  expect(state.sent).toEqual([]);
  expectNoLeaks(state);
});

test("qa.approve can leave a QA gate and preserves rejects as a decimal string", async ({ page }) => {
  const state = await setup(page, { lot: qaLot, canQa: true,
    actionReply: { move: { status: 200, body: { ...packingLot, quantity: "12.340" } } } });
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  await page.getByLabel(/reject quantity/i).fill("0.000");
  await confirmIfShown(page, action(page, /move to next stage/i));
  await expect(page.getByRole("main")).toContainText("Packing");
  expect(state.sent).toEqual([{ action: "move", method: "POST", body: { rejects: "0.000", notes: null } }]);
  expectNoLeaks(state);
});

test("packing is the only next action at PACKING and uses an active same-plant FG store", async ({ page }) => {
  const state = await setup(page, { lot: packingLot, canMove: true });
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  await expect(action(page, /move to next stage/i)).toHaveCount(0);
  const stores = page.getByLabel(/finished.goods store/i);
  await expect(stores.locator("option")).toHaveCount(2);
  await stores.selectOption(fgStoreId);
  await page.getByLabel(/packing notes/i).fill("Label checked");
  await confirmIfShown(page, action(page, /pack.*finished goods|complete packing/i));
  await expect(page.getByRole("main")).toContainText("COMPLETED");
  await expect(page.getByRole("main")).toContainText("PK-0042");
  await expect(page.getByRole("main")).toContainText("12.215");
  expect(state.sent).toEqual([{ action: "pack", method: "POST", body: { fg_store_id: fgStoreId, notes: "Label checked" } }]);
  expectNoLeaks(state);
});

test("rework and raw-material return post only verified fields without inventing available balances", async ({ page }) => {
  const state = await setup(page, { canMove: true });
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  await page.getByLabel(/rework quantity/i).fill("0.250");
  await page.getByLabel(/rework reason/i).fill("Recovered after inspection");
  await confirmIfShown(page, action(page, /record rework/i));
  await page.getByLabel(/source stock lot/i).selectOption("RM-LOT-009");
  await page.getByLabel(/return quantity/i).fill("0.125");
  await confirmIfShown(page, action(page, /return material/i));
  expect(state.sent).toEqual([
    { action: "rework", method: "POST", body: { quantity: "0.250", reason: "Recovered after inspection" } },
    { action: "return-material", method: "POST", body: { source_lot_no: "RM-LOT-009", quantity: "0.125" } },
  ]);
  await expect(page.getByText(/available to rework|remaining returnable/i)).toHaveCount(0);
  expectNoLeaks(state);
});

test("a lifecycle conflict keeps the server explanation and request ID without claiming success", async ({ page }) => {
  const detail = "Only 0.125 rejected pieces of this lot are waiting for rework";
  const state = await setup(page, { canMove: true,
    actionReply: { rework: { status: 409, body: { detail }, requestId: "req-lot-rework-409" } } });
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  await page.getByLabel(/rework quantity/i).fill("0.250");
  await page.getByLabel(/rework reason/i).fill("Recovered after inspection");
  await confirmIfShown(page, action(page, /record rework/i));
  await expect(page.getByRole("alert")).toContainText(detail);
  await expect(page.getByText("Request ID: req-lot-rework-409")).toBeVisible();
  await expect(page.getByRole("main")).toContainText("12.340");
  expect(state.sent).toHaveLength(1);
  expectNoLeaks(state);
});

test("an ambiguous server failure blocks duplicate movement until the lot is refreshed", async ({ page }) => {
  const state = await setup(page, { canMove: true,
    actionReply: { move: { status: 503, body: { detail: "Database unavailable" }, requestId: "req-lot-move-503" } } });
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  await page.getByLabel(/reject quantity/i).fill("0.000");
  await confirmIfShown(page, action(page, /move to next stage/i));
  await expect(page.getByRole("alert")).toContainText(/outcome unknown|could not confirm/i);
  await expect(page.getByText("Request ID: req-lot-move-503")).toBeVisible();
  await expect(action(page, /move to next stage/i)).toBeDisabled();
  await page.getByRole("button", { name: /refresh production lot|check current lot/i }).click();
  await expect(action(page, /move to next stage/i)).toBeEnabled();
  expect(state.sent).toHaveLength(1);
  expectNoLeaks(state);
});

test("loading and detail errors expose request IDs without rendering invented lot data", async ({ page }) => {
  const state = await setup(page, { delayDetail: true,
    detailReply: { status: 503, body: { detail: "Production lot unavailable" }, requestId: "req-lot-get-503" } });
  await page.goto(path);
  await expect(page.getByRole("status").filter({ hasText: /loading.*production lot/i })).toBeVisible();
  state.releaseDetail();
  await expect(page.getByRole("alert")).toContainText("Production lot unavailable");
  await expect(page.getByText("Request ID: req-lot-get-503")).toBeVisible();
  await expect(heading(page)).toHaveCount(0);
  expectNoLeaks(state);
});

test("every non-IN_PROCESS lifecycle state is read-only", async ({ context }) => {
  for (const status of ["READY", "COMPLETED", "SCRAPPED", "CONSUMED", "HOLD"]) {
    const page = await context.newPage();
    const terminal = status === "COMPLETED" ? packedLot : { ...baseLot, status };
    const state = await setup(page, { lot: terminal, canMove: true, canQa: true });
    await page.goto(path);
    await expect(heading(page)).toBeVisible();
    await expect(page.getByRole("main")).toContainText(status);
    for (const name of [/move to next stage/i, /pack.*finished goods|complete packing/i, /record rework/i, /return material/i])
      await expect(action(page, name)).toHaveCount(0);
    expect(state.sent).toEqual([]);
    expectNoLeaks(state);
    await page.close();
  }
});

test("without manufacturing module access no lot or trace request is made", async ({ page }) => {
  const state = await setup(page, { manufacturing: false, canMove: true, canQa: true });
  await page.goto(path);
  await expect(page.getByText(/outside your current module access|access denied/i).first()).toBeVisible();
  expect(state.detailGets()).toBe(0);
  expect(state.traceGets()).toBe(0);
  expect(state.sent).toEqual([]);
  expectNoLeaks(state);
});

test("mobile lot execution remains readable and its primary action is keyboard operable", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const state = await setup(page, { canMove: true });
  await page.goto(path);
  await expect(heading(page)).toBeVisible();
  await expect(page.getByRole("main")).toContainText("RM-LOT-009");
  const history = page.getByRole("list", { name: "Movement history" });
  await expect(history).toContainText("Asha Patel");
  await expect(history).toContainText("Ravi Kumar");
  await expect(history).toContainText("0.125");
  const move = action(page, /move to next stage/i);
  await move.focus();
  await expect(move).toBeFocused();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  if (await dialog.isVisible()) {
    const confirm = dialog.getByRole("button", { name: /confirm|continue|yes/i });
    await confirm.focus();
    await page.keyboard.press("Enter");
  }
  await expect(page.getByRole("main")).toContainText("Final quality inspection");
  expect(state.sent).toHaveLength(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
  expectNoLeaks(state);
});
