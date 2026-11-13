import { expect, test } from "@playwright/test";
import { cors, inventoryProfile, mockSignInAndMe, mockStockSources, stockAlpha } from "./support/fixtures";

test("reservation table retains readable columns and action at desktop width", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockStockSources(page);
  await page.route("**/api/v1/inventory/reservations?**", route => route.fulfill({
    status: 200, headers: cors, contentType: "application/json", body: JSON.stringify([{
      reservation_id: "88888888-1111-1111-1111-111111111111", lot_id: stockAlpha.lot_id,
      lot_no: stockAlpha.lot_no, item_code: stockAlpha.item_code, store_code: stockAlpha.store_code,
      quantity: "5.000", reference_type: "WORK_ORDER", reference: "WO-0042", status: "ACTIVE", created_at: "2026-11-05T10:00:00Z",
    }]),
  }));
  await page.goto("/app/inventory/reservations");
  const table = page.getByRole("table", { name: "Reservation results" });
  await expect(table).toBeVisible();
  const button = table.getByRole("button", { name: "Release reference" });
  await expect(button).toBeVisible();
  await expect.poll(() => button.evaluate(element => element.getBoundingClientRect().height)).toBeLessThan(70);
  await expect.poll(() => table.getByRole("columnheader", { name: "Lot expiry" }).evaluate(element => element.getBoundingClientRect().width)).toBeGreaterThan(70);
  await expect(page.getByText("Scroll the table horizontally to view all reservation fields.")).toBeVisible();
});

test("reservation creation keeps heading and selected store readable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockStockSources(page);
  await page.goto("/app/inventory/reservations/new");
  const title = page.getByRole("heading", { name: "New reservation" });
  const description = page.getByText("Reserve stock for a reference. The backend selects eligible lots.");
  await expect(title).toBeVisible();
  await expect.poll(async () => {
    const heading = await title.boundingBox();
    const detail = await description.boundingBox();
    return Boolean(heading && detail && detail.y >= heading.y + heading.height - 1);
  }).toBe(true);
  await page.getByLabel("Store", { exact: true }).selectOption(stockAlpha.store_id);
  await expect(page.getByText(/Selected store:.*Raw material store.*Plant Alpha/)).toBeVisible();
});
