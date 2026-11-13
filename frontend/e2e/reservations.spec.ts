import { expect, test, type Page } from "@playwright/test";
import { cors, inventoryProfile, mockSignInAndMe, plantAlpha, plantBeta, stockAlpha } from "./support/fixtures";

const listPath = "/app/inventory/reservations";
const lotBeta = {
  ...stockAlpha,
  lot_id: "77777777-2222-2222-2222-222222222222",
  lot_no: "LOT-0043",
  unit_id: plantBeta.unit_id,
  unit_code: plantBeta.code,
  store_id: "66666666-2222-2222-2222-222222222222",
  // Same store code across plants must never be used as a scope key.
  store_code: stockAlpha.store_code,
  item_id: "ffffffff-2222-2222-2222-222222222222",
  item_code: "RM-02",
  item_name: "Rubber additive",
  expiry_at: null,
  expired: false,
  on_hand: "0.375",
  reserved: "0.375",
  available: "0.000",
};
const lotGamma = {
  ...stockAlpha,
  lot_id: "77777777-3333-3333-3333-333333333333",
  lot_no: "LOT-0044",
  on_hand: "0.050",
  reserved: "0.000",
  available: "0.050",
};
const reservationAlpha = {
  reservation_id: "88888888-1111-1111-1111-111111111111",
  lot_id: stockAlpha.lot_id,
  lot_no: stockAlpha.lot_no,
  item_code: stockAlpha.item_code,
  store_code: stockAlpha.store_code,
  quantity: "0.125",
  reference_type: "WORK_ORDER",
  reference: "WO-0042",
  status: "ACTIVE",
  created_at: "2026-11-05T10:00:00Z",
};
const reservationBeta = {
  ...reservationAlpha,
  reservation_id: "88888888-2222-2222-2222-222222222222",
  lot_id: lotBeta.lot_id,
  lot_no: lotBeta.lot_no,
  item_code: lotBeta.item_code,
  quantity: "0.375",
};
const releasedReservation = {
  ...reservationAlpha,
  reservation_id: "88888888-3333-3333-3333-333333333333",
  reference_type: "SALES_ORDER",
  reference: "SO-0099",
  status: "RELEASED",
};
const consumedReservation = {
  ...reservationAlpha,
  reservation_id: "88888888-5555-5555-5555-555555555555",
  reference_type: "DISPATCH",
  reference: "DIS-0042",
  status: "CONSUMED",
};
type Reservation = typeof reservationAlpha;
const createdSplit = [
  { ...reservationAlpha, quantity: "0.075" },
  { ...reservationAlpha, reservation_id: "88888888-4444-4444-4444-444444444444", lot_id: lotGamma.lot_id, lot_no: lotGamma.lot_no, quantity: "0.050" },
];

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return { status, headers: { ...cors, ...headers }, contentType: "application/json", body: JSON.stringify(body) };
}

async function signedIn(page: Page, canIssue = true) {
  // The catch-all makes these browser tests independent of a running API or Railway.
  await page.route("**/api/v1/**", route => route.fulfill(json({ detail: "Unmocked API request" }, 404)));
  const profile = {
    ...inventoryProfile,
    all_plants: true,
    unit_ids: [],
    permissions: canIssue ? inventoryProfile.permissions : inventoryProfile.permissions.filter(permission => permission !== "inv.issue"),
  };
  await mockSignInAndMe(page, { status: 200, body: profile }, [plantAlpha, { ...plantBeta, active: true }]);
}

async function mockLotDetails(page: Page, requested: string[] = []) {
  await page.route(/\/api\/v1\/inventory\/lots\/[^/?]+$/, route => {
    const id = new URL(route.request().url()).pathname.split("/").at(-1) ?? "";
    requested.push(id);
    const lot = [stockAlpha, lotBeta, lotGamma].find(item => item.lot_id === id);
    return route.fulfill(json(lot ?? { detail: "Lot not found" }, lot ? 200 : 404));
  });
}

async function mockCatalog(page: Page) {
  const stores = [
    { store_id: stockAlpha.store_id, unit_id: stockAlpha.unit_id, code: stockAlpha.store_code, name: "Raw material store A", store_type: "RAW_MATERIAL", active: true },
    { store_id: lotBeta.store_id, unit_id: lotBeta.unit_id, code: lotBeta.store_code, name: "Raw material store B", store_type: "RAW_MATERIAL", active: true },
  ];
  const items = [stockAlpha, lotBeta].map(lot => ({ item_id: lot.item_id, code: lot.item_code, name: lot.item_name, item_type: "RM", uom_code: "KG", category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true }));
  const pageOf = (values: typeof stores | typeof items, url: URL) => {
    const limit = Number(url.searchParams.get("limit") ?? 200);
    const offset = Number(url.searchParams.get("offset") ?? 0);
    return { items: values.slice(offset, offset + limit), total: values.length, limit, offset };
  };
  await page.route(/\/api\/v1\/org\/stores(?:\?.*)?$/, route => {
    const url = new URL(route.request().url());
    const filtered = stores.filter(store => (!url.searchParams.has("unit_id") || store.unit_id === url.searchParams.get("unit_id"))
      && (!url.searchParams.has("active") || String(store.active) === url.searchParams.get("active")));
    return route.fulfill(json(pageOf(filtered, url)));
  });
  await page.route(/\/api\/v1\/product\/items(?:\?.*)?$/, route => {
    const url = new URL(route.request().url());
    const filtered = items.filter(item => !url.searchParams.has("active") || String(item.active) === url.searchParams.get("active"));
    return route.fulfill(json(pageOf(filtered, url)));
  });
  await page.route(/\/api\/v1\/inventory\/stock(?:\?.*)?$/, route => {
    const url = new URL(route.request().url());
    const filtered = [{ ...stockAlpha, reserved: "0.050", available: "0.075" }, lotBeta, lotGamma].filter(lot => (!url.searchParams.has("unit_id") || lot.unit_id === url.searchParams.get("unit_id"))
      && (!url.searchParams.has("store_id") || lot.store_id === url.searchParams.get("store_id"))
      && (!url.searchParams.has("item_id") || lot.item_id === url.searchParams.get("item_id")));
    return route.fulfill(json(pageOf(filtered, url)));
  });
}

async function mockReservationList(page: Page, rows: Reservation[] = [reservationAlpha, reservationBeta], requests: URLSearchParams[] = []) {
  await page.route(/\/api\/v1\/inventory\/reservations(?:\?.*)?$/, route => {
    if (route.request().method() !== "GET") return route.fallback();
    const params = new URL(route.request().url()).searchParams;
    requests.push(new URLSearchParams(params));
    const status = params.get("status") ?? "ACTIVE";
    const filtered = rows.filter(row => row.status === status
      && (!params.has("reference_type") || row.reference_type === params.get("reference_type"))
      && (!params.has("reference") || row.reference === params.get("reference")));
    return route.fulfill(json(filtered));
  });
}

test("Reservations register reads the plain API array and enriches lot expiry without changing decimal strings", async ({ page }) => {
  await signedIn(page);
  const requestedLots: string[] = [];
  await mockLotDetails(page, requestedLots);
  await mockReservationList(page);
  await page.goto(listPath);
  await expect(page.getByRole("heading", { name: "Reservations" })).toBeVisible();
  const results = page.getByRole("table", { name: "Reservation results" });
  await expect(results).toContainText("WO-0042");
  await expect(results).toContainText("0.125");
  await expect(results).toContainText("0.375");
  await expect(results).toContainText("2027-05-01");
  await expect(results).toContainText("Not set");
  expect(requestedLots).toEqual(expect.arrayContaining([stockAlpha.lot_id, lotBeta.lot_id]));
  await expect(page.getByRole("button", { name: /delete/i })).toHaveCount(0);
});

test("Inventory read access preserves reservations but hides create and release without inv.issue", async ({ page }) => {
  await signedIn(page, false);
  await mockLotDetails(page);
  await mockReservationList(page);
  await page.goto(listPath);
  await expect(page.getByRole("heading", { name: "Reservations" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Reservation results" })).toContainText("WO-0042");
  await expect(page.getByRole("button", { name: "New reservation" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Release reference" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Create reservation" })).toHaveCount(0);
});

test("Plant, store and item filters use lot IDs for client-side scope, not unsupported reservation query keys", async ({ page }) => {
  await signedIn(page);
  await mockCatalog(page);
  await mockLotDetails(page);
  const requests: URLSearchParams[] = [];
  await mockReservationList(page, [reservationAlpha, reservationBeta], requests);
  await page.goto(listPath);
  const results = page.getByRole("table", { name: "Reservation results" });
  await expect(results).toContainText(stockAlpha.lot_no);
  await expect(results).toContainText(lotBeta.lot_no);
  await page.getByLabel("Plant filter").selectOption(plantAlpha.unit_id);
  await expect(results).toContainText(stockAlpha.lot_no);
  await expect(results).not.toContainText(lotBeta.lot_no);
  await page.getByLabel("Plant filter").selectOption("");
  await page.getByLabel("Store filter").selectOption(lotBeta.store_id);
  await expect(results).toContainText(lotBeta.lot_no);
  await expect(results).not.toContainText(stockAlpha.lot_no);
  await page.getByLabel("Store filter").selectOption("");
  await page.getByLabel("Item filter").selectOption(stockAlpha.item_id);
  await expect(results).toContainText(stockAlpha.lot_no);
  await expect(results).not.toContainText(lotBeta.lot_no);
  expect(requests.length).toBeGreaterThan(0);
  expect(requests.every(params => [...params.keys()].every(key => ["status", "reference_type", "reference"].includes(key)))).toBe(true);
});

test("Status and exact reference filters use only the three supported backend query fields", async ({ page }) => {
  await signedIn(page);
  await mockLotDetails(page);
  const requests: URLSearchParams[] = [];
  await mockReservationList(page, [reservationAlpha, reservationBeta, releasedReservation, consumedReservation], requests);
  await page.goto(listPath);
  const results = page.getByRole("table", { name: "Reservation results" });
  await expect(results).not.toContainText("SO-0099");
  await page.getByLabel("Reservation status").selectOption("RELEASED");
  await page.getByLabel("Reference type filter").selectOption("SALES_ORDER");
  await page.getByLabel("Reference filter").fill("SO-0099");
  await expect(results).toContainText("SO-0099");
  await expect(results).not.toContainText("WO-0042");
  expect(requests.some(params => params.get("status") === "RELEASED" && params.get("reference_type") === "SALES_ORDER" && params.get("reference") === "SO-0099")).toBe(true);
  expect(requests.every(params => [...params.keys()].every(key => ["status", "reference_type", "reference"].includes(key)))).toBe(true);
});

test("CONSUMED reservations remain visible through status filtering after a full issue", async ({ page }) => {
  await signedIn(page);
  await mockLotDetails(page);
  const requests: URLSearchParams[] = [];
  await mockReservationList(page, [reservationAlpha, consumedReservation], requests);
  await page.goto(listPath);
  await page.getByLabel("Reservation status").selectOption("CONSUMED");
  const results = page.getByRole("table", { name: "Reservation results" });
  await expect(results).toContainText("DIS-0042");
  await expect(results).toContainText(/consumed/i);
  await expect(results).not.toContainText("WO-0042");
  expect(requests.some(params => params.get("status") === "CONSUMED")).toBe(true);
  expect(requests.every(params => [...params.keys()].every(key => ["status", "reference_type", "reference"].includes(key)))).toBe(true);
});

test("Create submits exact decimal text and reference, then shows each backend-selected lot", async ({ page }) => {
  await signedIn(page);
  await mockCatalog(page);
  await mockLotDetails(page);
  const rows = [reservationAlpha];
  await mockReservationList(page, rows);
  let posted: unknown = null;
  await page.route(/\/api\/v1\/inventory\/reservations$/, route => {
    if (route.request().method() !== "POST") return route.fallback();
    posted = route.request().postDataJSON();
    rows.splice(0, rows.length, ...createdSplit);
    return route.fulfill(json(createdSplit, 201));
  });
  await page.goto(listPath);
  await page.getByRole("button", { name: "New reservation" }).click();
  await page.getByLabel("Store", { exact: true }).selectOption(stockAlpha.store_id);
  await page.getByLabel("Item", { exact: true }).selectOption(stockAlpha.item_id);
  await page.getByLabel("Quantity", { exact: true }).fill("0.125");
  await page.getByLabel("Reference type", { exact: true }).selectOption("WORK_ORDER");
  await page.getByLabel("Reference", { exact: true }).fill("WO-0042");
  await page.getByRole("button", { name: "Create reservation" }).click();
  expect(posted).toEqual({ store_id: stockAlpha.store_id, item_id: stockAlpha.item_id, quantity: "0.125", reference_type: "WORK_ORDER", reference: "WO-0042" });
  await expect(page.getByText("LOT-0044").first()).toBeVisible();
  await expect(page.getByText("0.075").first()).toBeVisible();
  await expect(page.getByText("0.050").first()).toBeVisible();
});

test("Create rejects non-positive or over-precise quantity without posting", async ({ page }) => {
  await signedIn(page);
  await mockCatalog(page);
  await mockLotDetails(page);
  await mockReservationList(page);
  let posts = 0;
  await page.route(/\/api\/v1\/inventory\/reservations$/, route => {
    if (route.request().method() !== "POST") return route.fallback();
    posts += 1;
    return route.fulfill(json({ detail: "Should not post an invalid quantity" }, 422));
  });
  await page.goto(listPath);
  await page.getByRole("button", { name: "New reservation" }).click();
  await page.getByLabel("Store", { exact: true }).selectOption(stockAlpha.store_id);
  await page.getByLabel("Item", { exact: true }).selectOption(stockAlpha.item_id);
  await page.getByLabel("Reference type", { exact: true }).selectOption("OTHER");
  await page.getByLabel("Reference", { exact: true }).fill("TEST-1");
  for (const bad of ["0", "0.1234", "1000000000.001"]) {
    await page.getByLabel("Quantity", { exact: true }).fill(bad);
    await page.getByRole("button", { name: "Create reservation" }).click();
    expect(posts).toBe(0);
    await expect(page.getByLabel("Quantity", { exact: true })).toHaveValue(bad);
  }
});

test("Reference release confirms its broad scope before POST and reports the backend count", async ({ page }) => {
  await signedIn(page);
  await mockLotDetails(page);
  const rows = [reservationAlpha, reservationBeta];
  await mockReservationList(page, rows);
  const releaseBodies: unknown[] = [];
  await page.route("**/api/v1/inventory/reservations/release", route => {
    if (route.request().method() !== "POST") return route.fallback();
    releaseBodies.push(route.request().postDataJSON());
    rows.splice(0, rows.length);
    return route.fulfill(json({ released: 2 }));
  });
  await page.goto(listPath);
  await expect(page.getByRole("table", { name: "Reservation results" })).toContainText(lotBeta.lot_no);
  await page.getByRole("button", { name: "Release reference" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Release reservations" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("WO-0042");
  await expect(dialog).toContainText(/all (active |matching )?reservations/i);
  expect(releaseBodies).toHaveLength(0);
  await dialog.getByRole("button", { name: "Confirm release" }).click();
  expect(releaseBodies).toEqual([{ reference_type: "WORK_ORDER", reference: "WO-0042" }]);
  await expect(page.getByText(/2 reservations released/i)).toBeVisible();
  await expect(page.getByRole("button", { name: /delete/i })).toHaveCount(0);
});

test("An empty or already released reference reports zero without inventing a deleted row", async ({ page }) => {
  await signedIn(page);
  await mockLotDetails(page);
  await mockReservationList(page, [reservationAlpha]);
  await page.route("**/api/v1/inventory/reservations/release", route => route.fulfill(json({ released: 0 })));
  await page.goto(listPath);
  await page.getByRole("button", { name: "Release reference" }).first().click();
  await page.getByRole("dialog", { name: "Release reservations" }).getByRole("button", { name: "Confirm release" }).click();
  await expect(page.getByText(/0 reservations released|no active reservations/i)).toBeVisible();
});

test("List shows loading, backend detail and request ID, then recovers to an empty state", async ({ page }) => {
  await signedIn(page);
  await mockCatalog(page);
  await mockReservationList(page, []);
  let fail = true;
  await page.route(/\/api\/v1\/inventory\/reservations(?:\?.*)?$/, async route => {
    if (route.request().method() !== "GET") return route.fallback();
    await new Promise(resolve => setTimeout(resolve, 300));
    return route.fulfill(fail
      ? json({ detail: "Reservation listing unavailable" }, 503, { "X-Request-ID": "req-reservations-list" })
      : json([]));
  });
  await page.goto(listPath);
  await expect(page.getByRole("status").filter({ hasText: /Loading reservations/i })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Reservation listing unavailable");
  await expect(page.getByText("Request ID: req-reservations-list")).toBeVisible();
  fail = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByText(/No reservations/i)).toBeVisible();
});

test("Create conflict retains the entered decimal and reference while showing backend detail and request ID", async ({ page }) => {
  await signedIn(page);
  await mockCatalog(page);
  await mockLotDetails(page);
  await mockReservationList(page);
  await page.route(/\/api\/v1\/inventory\/reservations$/, route => route.request().method() === "POST"
    ? route.fulfill(json({ detail: "Only 0.100 of RM-01 is free to reserve" }, 409, { "X-Request-ID": "req-reserve-shortfall" }))
    : route.fallback());
  await page.goto(listPath);
  await page.getByRole("button", { name: "New reservation" }).click();
  await page.getByLabel("Store", { exact: true }).selectOption(stockAlpha.store_id);
  await page.getByLabel("Item", { exact: true }).selectOption(stockAlpha.item_id);
  await page.getByLabel("Quantity", { exact: true }).fill("0.125");
  await page.getByLabel("Reference type", { exact: true }).selectOption("WORK_ORDER");
  await page.getByLabel("Reference", { exact: true }).fill("WO-0042");
  await page.getByRole("button", { name: "Create reservation" }).click();
  await expect(page.getByRole("alert")).toContainText("Only 0.100 of RM-01 is free to reserve");
  await expect(page.getByText("Request ID: req-reserve-shortfall")).toBeVisible();
  await expect(page.getByLabel("Quantity", { exact: true })).toHaveValue("0.125");
  await expect(page.getByLabel("Reference", { exact: true })).toHaveValue("WO-0042");
});

for (const width of [360, 768, 1280]) {
  test(`Reservation register fits ${width}px and keyboard cancellation cannot release stock`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 360 ? 800 : 900 });
    await signedIn(page);
    await mockLotDetails(page);
    await mockReservationList(page);
    let releases = 0;
    await page.route("**/api/v1/inventory/reservations/release", route => {
      releases += 1;
      return route.fulfill(json({ released: 2 }));
    });
    await page.goto(listPath);
    await expect(page.getByRole("heading", { name: "Reservations" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    const release = page.getByRole("button", { name: "Release reference" }).first();
    await release.focus();
    await expect(release).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog", { name: "Release reservations" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Release reservations" })).toHaveCount(0);
    await expect(release).toBeFocused();
    expect(releases).toBe(0);
  });
}
