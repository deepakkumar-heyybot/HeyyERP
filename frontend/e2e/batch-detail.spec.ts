import { expect, test, type Page } from "@playwright/test";
import { cors, grantedProfile, mockSignInAndMe, plantAlpha } from "./support/fixtures";

const batchId = "dddddddd-1111-1111-1111-111111111111";
const compoundId = "cccccccc-1111-1111-1111-111111111111";
const productionLotId = "eeeeeeee-1111-1111-1111-111111111111";
const path = `/app/manufacturing/batches/${batchId}`;
const headers = { ...cors, "access-control-allow-methods": "GET,POST,OPTIONS" };
const json = (body: unknown, status = 200, extra: Record<string, string> = {}) => ({
  status, headers: { ...headers, ...extra }, contentType: "application/json", body: JSON.stringify(body),
});

type BatchStatus = "WEIGHED" | "MIXING" | "MATURING" | "AWAITING_QA" | "READY" | "REJECTED" | "EXPIRED" | "CONSUMED" | "ON_HOLD";
type Batch = {
  batch_id: string; batch_no: string; unit_id: string; compound_id: string; compound_code: string;
  compound_name: string; status: BatchStatus; total_kg: string; remaining_kg: string;
  weighed_at: string | null; mixed_at: string | null; age_hours: string | null;
  maturation_min_hours: string; maximum_age_hours: string; qa_status: string;
  hardness: string | null; qa_note: string | null; qa_at: string | null;
  weighing: { item_code: string; item_name: string; lot_no: string; target_qty: string; actual_qty: string; variance: string }[];
};
const weighed: Batch = {
  batch_id: batchId, batch_no: "BAT-0042", unit_id: plantAlpha.unit_id,
  compound_id: compoundId, compound_code: "CMP-01", compound_name: "Rubber compound",
  status: "WEIGHED", total_kg: "0.195", remaining_kg: "0.195",
  weighed_at: "2026-11-05T08:15:00Z", mixed_at: null, age_hours: null,
  maturation_min_hours: "24.50", maximum_age_hours: "120.75", qa_status: "PENDING",
  hardness: null, qa_note: null, qa_at: null,
  weighing: [
    { item_code: "RM-01", item_name: "Natural rubber", lot_no: "LOT-A", target_qty: "0.120", actual_qty: "0.125", variance: "0.005" },
    { item_code: "RM-01", item_name: "Natural rubber", lot_no: "LOT-B", target_qty: "0.050", actual_qty: "0.045", variance: "-0.005" },
    { item_code: "RM-02", item_name: "Curing agent", lot_no: "LOT-C", target_qty: "0.025", actual_qty: "0.025", variance: "0.000" },
  ],
};
const mixing: Batch = { ...weighed, status: "MIXING" };
const maturing: Batch = { ...weighed, status: "MATURING", mixed_at: "2026-11-05T10:00:00Z", age_hours: "0.00" };
const awaitingQa: Batch = { ...maturing, status: "AWAITING_QA", age_hours: "24.50" };
const ready: Batch = { ...awaitingQa, status: "READY", qa_status: "PASSED", hardness: "60.25", qa_at: "2026-11-06T11:00:00Z" };
const rejected: Batch = { ...awaitingQa, status: "REJECTED", qa_status: "FAILED", hardness: null, qa_note: "Hardness failed", qa_at: "2026-11-06T11:00:00Z" };
const compound = {
  compound_id: compoundId, code: "CMP-01", name: "Rubber compound", lane: "RUBBER",
  maturation_min_hours: "24.50", maximum_age_hours: "120.75", hardness_min: "55.00", hardness_max: "65.00",
  output_item_id: "bbbbbbbb-1111-1111-1111-111111111111", active: true, recipe: [],
};
const usedIn = [{
  prod_lot_id: productionLotId, lot_no: "PROD-009", unit_id: plantAlpha.unit_id,
  work_order_id: "ffffffff-1111-1111-1111-111111111111", work_order_no: "WO-009",
  item_id: "bbbbbbbb-2222-2222-2222-222222222222", item_code: "FG-01",
  route_code: "ROUTE-01", stage_code: "MIX", stage_name: "Mixing", is_qa_gate: false,
  is_end: false, quantity: "0.075", status: "IN_PROCESS", created_at: "2026-11-06T12:00:00Z",
}];
type Action = "start-mixing" | "mixed" | "qa";
type Sent = { action: Action; method: string; body: string | null };
type Reply = { status: number; body: unknown; requestId?: string };

async function setup(page: Page, options: {
  batch?: Batch; canMove?: boolean; canQa?: boolean; manufacturing?: boolean;
  detailReply?: Reply; actionReply?: Partial<Record<Action, Reply>>; usedIn?: typeof usedIn;
  delayDetail?: boolean;
} = {}) {
  let current = options.batch ?? weighed;
  const sent: Sent[] = [];
  let detailGets = 0;
  let usedInGets = 0;
  let unblockDetail!: () => void;
  const detailGate = new Promise<void>(resolve => { unblockDetail = resolve; });
  if (!options.delayDetail) unblockDetail();

  // Allow only the local Vite assets. Every API request is fulfilled by a
  // fixture or the explicit mock 404; any other origin is aborted.
  await page.route("**/*", route => {
    const url = new URL(route.request().url());
    const localApi = ["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:3001", "http://127.0.0.1:3001"].includes(url.origin);
    if ((localApi || url.origin === "http://127.0.0.1:5174") && url.pathname.startsWith("/api/"))
      return route.fulfill(json({ detail: "Unmocked API request" }, 404));
    if (url.origin === "http://127.0.0.1:5174") return route.continue();
    return route.abort();
  });
  const permissions = grantedProfile.permissions.filter(permission => permission !== "route.move" && permission !== "qa.approve");
  if (options.canMove) permissions.push("route.move");
  if (options.canQa) permissions.push("qa.approve");
  await mockSignInAndMe(page, { status: 200, body: {
    ...grantedProfile,
    modules: options.manufacturing === false ? grantedProfile.modules : [...grantedProfile.modules, "manufacturing", "quality"],
    permissions,
  } }, [plantAlpha]);
  await page.route(new RegExp(`/api/v1/manufacturing/batches/${batchId}(?:\\?.*)?$`), async route => {
    if (route.request().method() !== "GET") return route.fulfill(json({ detail: "Method not allowed" }, 405));
    detailGets += 1;
    await detailGate;
    const reply = options.detailReply;
    return route.fulfill(json(reply?.body ?? current, reply?.status ?? 200,
      reply?.requestId ? { "X-Request-ID": reply.requestId } : {}));
  });
  await page.route(new RegExp(`/api/v1/manufacturing/batches/${batchId}/(start-mixing|mixed|qa)$`), route => {
    const action = route.request().url().split("/").at(-1) as Action;
    sent.push({ action, method: route.request().method(), body: route.request().postData() });
    if (route.request().method() !== "POST") return route.fulfill(json({ detail: "Method not allowed" }, 405));
    const reply = options.actionReply?.[action];
    if (reply) return route.fulfill(json(reply.body, reply.status,
      reply.requestId ? { "X-Request-ID": reply.requestId } : {}));
    if (action === "qa") {
      const body = route.request().postDataJSON() as { result: string; hardness?: string | null; note?: string | null };
      if (body.result === "PASSED" && !body.hardness)
        return route.fulfill(json({ detail: "Enter the measured hardness to approve a batch" }, 422));
      if (body.result === "FAILED" && !body.note)
        return route.fulfill(json({ detail: "A failed batch needs a note explaining why" }, 422));
      current = body.result === "PASSED" ? ready : rejected;
    } else current = action === "start-mixing" ? mixing : maturing;
    return route.fulfill(json(current));
  });
  await page.route(new RegExp(`/api/v1/manufacturing/batches/${batchId}/used-in(?:\\?.*)?$`), route => {
    if (route.request().method() !== "GET") return route.fulfill(json({ detail: "Method not allowed" }, 405));
    usedInGets += 1;
    return route.fulfill(json(options.usedIn ?? usedIn));
  });
  await page.route(new RegExp(`/api/v1/manufacturing/compounds/${compoundId}(?:\\?.*)?$`), route =>
    route.fulfill(route.request().method() === "GET" ? json(compound) : json({ detail: "Method not allowed" }, 405)));
  await page.route(/\/api\/v1\/quality\/inspections(?:\?.*)?$/, route => {
    const params = new URL(route.request().url()).searchParams;
    const limit = Number(params.get("limit") ?? 50), offset = Number(params.get("offset") ?? 0);
    return route.fulfill(json({ items: [], total: 0, limit, offset }));
  });
  return {
    sent, detailGets: () => detailGets, usedInGets: () => usedInGets,
    unblockDetail: () => unblockDetail(), current: () => current,
  };
}

const title = (page: Page) => page.getByRole("heading", { name: weighed.batch_no });
const startButton = (page: Page) => page.getByRole("button", { name: /^start mixing$/i });
const mixedButton = (page: Page) => page.getByRole("button", { name: /^(finish mixing|mark mixed)$/i });
const qaButton = (page: Page) => page.getByRole("button", { name: /^(record|submit|save) qa( decision)?$/i });
async function clickAction(page: Page, button: ReturnType<Page["getByRole"]>) {
  await button.click();
  const dialog = page.getByRole("dialog");
  if (await dialog.isVisible()) await dialog.getByRole("button", { name: /confirm|yes/i }).click();
}
async function setQa(page: Page, result: "PASSED" | "FAILED", hardness?: string, note?: string) {
  await page.getByRole("combobox", { name: /qa result/i }).selectOption(result);
  if (hardness !== undefined) await page.getByLabel(/hardness/i).fill(hardness);
  if (note !== undefined) await page.getByLabel(/note/i).fill(note);
}

test("batch GET renders each source lot with exact target, actual and signed variance strings", async ({ page }) => {
  const api = await setup(page);
  await page.goto(path);
  await expect(title(page)).toBeVisible();
  const main = page.getByRole("main");
  await expect(main).toContainText("CMP-01");
  await expect(main).toContainText("0.195");
  for (const row of weighed.weighing) {
    const line = main.getByRole("row").filter({ hasText: row.lot_no });
    await expect(line).toContainText(row.item_code);
    await expect(line).toContainText(row.target_qty);
    await expect(line).toContainText(row.actual_qty);
    await expect(line).toContainText(row.variance);
  }
  expect(api.detailGets()).toBeGreaterThan(0);
  expect(api.sent).toHaveLength(0);
  // Batch GET exposes lot numbers, but no source_lot_id for a reliable link.
  await expect(main.getByRole("link", { name: /LOT-[ABC]/i })).toHaveCount(0);
});

test("batch timestamps retain their backend timezone designation", async ({ page }) => {
  await setup(page);
  await page.goto(path);
  await expect(title(page)).toBeVisible();
  await expect(page.getByText(weighed.weighed_at!, { exact: true })).toBeVisible();
});

test("desktop batch weighing table gives ingredient names readable space", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await setup(page);
  await page.goto(path);
  const ingredient = page.getByRole("table", { name: "Batch weighing lines" }).getByRole("columnheader", { name: "Ingredient" });
  await expect(ingredient).toBeVisible();
  expect((await ingredient.boundingBox())!.width).toBeGreaterThanOrEqual(180);
});

test("route.move starts and finishes mixing in sequence with bodyless POSTs", async ({ page }) => {
  const api = await setup(page, { canMove: true });
  await page.goto(path);
  await expect(startButton(page)).toBeVisible();
  await expect(mixedButton(page)).toHaveCount(0);
  await clickAction(page, startButton(page));
  await expect(page.getByRole("main")).toContainText(/mixing/i);
  await expect(mixedButton(page)).toBeVisible();
  await expect(startButton(page)).toHaveCount(0);
  await clickAction(page, mixedButton(page));
  await expect(page.getByRole("main")).toContainText(/maturing/i);
  await expect(startButton(page)).toHaveCount(0);
  await expect(mixedButton(page)).toHaveCount(0);
  expect(api.sent).toEqual([
    { action: "start-mixing", method: "POST", body: null },
    { action: "mixed", method: "POST", body: null },
  ]);
});

test("only an awaiting-QA batch offers the QA decision, and it does not offer mixing actions", async ({ page }) => {
  const api = await setup(page, { batch: awaitingQa, canMove: true, canQa: true });
  await page.goto(path);
  await expect(title(page)).toBeVisible();
  await expect(qaButton(page)).toBeVisible();
  await expect(startButton(page)).toHaveCount(0);
  await expect(mixedButton(page)).toHaveCount(0);
  expect(api.sent).toHaveLength(0);
});

for (const state of [
  maturing, ready, rejected,
  { ...awaitingQa, status: "EXPIRED" as const, age_hours: "121.00" },
  { ...ready, status: "CONSUMED" as const, remaining_kg: "0.000" },
  { ...awaitingQa, status: "ON_HOLD" as const },
]) {
  test(`${state.status} batch cannot show mixing or QA transition controls`, async ({ page }) => {
    const api = await setup(page, { batch: state, canMove: true, canQa: true });
    await page.goto(path);
    await expect(title(page)).toBeVisible();
    await expect(startButton(page)).toHaveCount(0);
    await expect(mixedButton(page)).toHaveCount(0);
    await expect(qaButton(page)).toHaveCount(0);
    expect(api.sent).toHaveLength(0);
  });
}

test("QA pass sends exact hardness string and uses the returned READY status", async ({ page }) => {
  const api = await setup(page, { batch: awaitingQa, canQa: true });
  await page.goto(path);
  await setQa(page, "PASSED", "60.25");
  await clickAction(page, qaButton(page));
  await expect(page.getByRole("main")).toContainText(/ready/i);
  await expect(page.getByRole("main")).toContainText("60.25");
  await expect(qaButton(page)).toHaveCount(0);
  expect(api.sent).toHaveLength(1);
  expect(api.sent[0].action).toBe("qa");
  expect(api.sent[0].method).toBe("POST");
  expect(JSON.parse(api.sent[0].body!)).toMatchObject({ result: "PASSED", hardness: "60.25" });
});

test("QA failure sends the required note and renders the returned REJECTED state", async ({ page }) => {
  const api = await setup(page, { batch: awaitingQa, canQa: true });
  await page.goto(path);
  await setQa(page, "FAILED", undefined, "Hardness failed");
  await clickAction(page, qaButton(page));
  await expect(page.getByRole("main")).toContainText(/rejected/i);
  await expect(page.getByRole("main")).toContainText("Hardness failed");
  await expect(qaButton(page)).toHaveCount(0);
  expect(api.sent).toHaveLength(1);
  expect(JSON.parse(api.sent[0].body!)).toMatchObject({ result: "FAILED", note: "Hardness failed" });
});

test("missing passing hardness and missing failure note cannot claim a QA decision", async ({ page }) => {
  const api = await setup(page, { batch: awaitingQa, canQa: true });
  await page.goto(path);
  await setQa(page, "PASSED");
  await clickAction(page, qaButton(page));
  await expect(page.getByText(/enter.*hardness|hardness.*required/i).first()).toBeVisible();
  await expect(page.getByRole("main")).toContainText(/awaiting[ _-]+qa/i);
  await setQa(page, "FAILED");
  await clickAction(page, qaButton(page));
  await expect(page.getByText(/note.*required|provide.*(?:note|reason)|failed batch needs a note/i).first()).toBeVisible();
  await expect(page.getByRole("main")).toContainText(/awaiting[ _-]+qa/i);
  expect(api.sent.every(request => request.action === "qa" && request.method === "POST")).toBe(true);
});

test("409 missing manual inspection explains the QA prerequisite without claiming approval", async ({ page }) => {
  const reason = "Record the in-process inspection first: the item has an inspection plan";
  const api = await setup(page, { batch: awaitingQa, canQa: true,
    actionReply: { qa: { status: 409, body: { detail: reason } } } });
  await page.goto(path);
  await setQa(page, "PASSED", "60.25");
  await clickAction(page, qaButton(page));
  await expect(page.getByRole("alert")).toContainText(reason);
  await expect(page.getByRole("main")).toContainText(/awaiting[ _-]+qa/i);
  await expect(qaButton(page)).toBeVisible();
  expect(api.sent).toHaveLength(1);
});

test("422 changed hardness specification preserves the server explanation and pending QA state", async ({ page }) => {
  // The compound specification can change between GET and QA POST.
  const detail = "Hardness 60.25 is outside the spec 61.00 to 65.00; the batch cannot be approved";
  const api = await setup(page, { batch: awaitingQa, canQa: true,
    actionReply: { qa: { status: 422, body: { detail } } } });
  await page.goto(path);
  await setQa(page, "PASSED", "60.25");
  await clickAction(page, qaButton(page));
  await expect(page.getByRole("alert")).toContainText(detail);
  await expect(page.getByRole("main")).toContainText(/awaiting[ _-]+qa/i);
  expect(api.sent).toHaveLength(1);
  expect(JSON.parse(api.sent[0].body!)).toMatchObject({ result: "PASSED", hardness: "60.25" });
});

test("stale mixing action 409 shows server state explanation and keeps the batch unconfirmed", async ({ page }) => {
  const detail = "The batch is mixing; this needs it to be weighed";
  const api = await setup(page, { canMove: true,
    actionReply: { "start-mixing": { status: 409, body: { detail } } } });
  await page.goto(path);
  await clickAction(page, startButton(page));
  await expect(page.getByRole("alert")).toContainText(detail);
  await expect(startButton(page)).toBeVisible();
  expect(api.sent).toHaveLength(1);
});

test("503 action failure shows request ID, retains state and keeps retry action available", async ({ page }) => {
  const api = await setup(page, { canMove: true,
    actionReply: { "start-mixing": { status: 503, body: { detail: "Database unavailable" }, requestId: "req-batch-mix-503" } } });
  await page.goto(path);
  await clickAction(page, startButton(page));
  await expect(page.getByRole("alert")).toContainText("Database unavailable");
  await expect(page.getByText("Request ID: req-batch-mix-503")).toBeVisible();
  await expect(startButton(page)).toBeVisible();
  expect(api.sent).toHaveLength(1);
});

test("detail loading and 503 error expose request ID without invented batch data", async ({ page }) => {
  const api = await setup(page, { delayDetail: true,
    detailReply: { status: 503, body: { detail: "Batch detail unavailable" }, requestId: "req-batch-get-503" } });
  await page.goto(path);
  await expect(page.getByRole("status").filter({ hasText: /loading.*batch/i })).toBeVisible();
  api.unblockDetail();
  await expect(page.getByRole("alert")).toContainText("Batch detail unavailable");
  await expect(page.getByText("Request ID: req-batch-get-503")).toBeVisible();
  await expect(title(page)).toHaveCount(0);
});

test("production used-in GET displays only returned lot and work-order details", async ({ page }) => {
  const api = await setup(page, { batch: { ...ready, remaining_kg: "0.120" }, usedIn });
  await page.goto(path);
  await expect(title(page)).toBeVisible();
  await expect(page.getByRole("main")).toContainText("PROD-009");
  await expect(page.getByRole("main")).toContainText("WO-009");
  await expect(page.getByRole("main")).toContainText("0.075");
  expect(api.usedInGets()).toBeGreaterThan(0);
});

test("manufacturing reader can inspect the batch but sees no operator or QA action", async ({ page }) => {
  const api = await setup(page, { batch: awaitingQa });
  await page.goto(path);
  await expect(title(page)).toBeVisible();
  await expect(startButton(page)).toHaveCount(0);
  await expect(mixedButton(page)).toHaveCount(0);
  await expect(qaButton(page)).toHaveCount(0);
  expect(api.sent).toHaveLength(0);
});

test("route.move alone does not grant QA approval", async ({ page }) => {
  const api = await setup(page, { batch: awaitingQa, canMove: true });
  await page.goto(path);
  await expect(title(page)).toBeVisible();
  await expect(qaButton(page)).toHaveCount(0);
  expect(api.sent).toHaveLength(0);
});

test("without manufacturing module access the batch GET and transitions are not requested", async ({ page }) => {
  const api = await setup(page, { manufacturing: false, canMove: true, canQa: true });
  await page.goto(path);
  await expect(page.getByText(/outside your current module access|access denied/i).first()).toBeVisible();
  expect(api.detailGets()).toBe(0);
  expect(api.sent).toHaveLength(0);
});

test("mobile batch execution remains readable and start action works from keyboard", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const api = await setup(page, { canMove: true });
  await page.goto(path);
  await expect(title(page)).toBeVisible();
  await expect(page.getByRole("main")).toContainText("LOT-A");
  await expect(page.getByRole("main")).toContainText("0.005");
  const start = startButton(page);
  await start.focus();
  await expect(start).toBeFocused();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  if (await dialog.isVisible()) await dialog.getByRole("button", { name: /confirm|yes/i }).click();
  await expect(page.getByRole("main")).toContainText(/mixing/i);
  expect(api.sent).toEqual([{ action: "start-mixing", method: "POST", body: null }]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
});
