import { expect, test } from "@playwright/test";
import { cors, inventoryProfile, mockSignInAndMe, mockStockSources } from "./support/fixtures";

test("inventory readers can open the empty reservations register", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: inventoryProfile });
  await mockStockSources(page);
  await page.route("**/api/v1/inventory/reservations?**", route => route.fulfill({
    status: 200, headers: cors, contentType: "application/json", body: "[]",
  }));
  await page.goto("/app/inventory/reservations");
  await expect(page.getByRole("heading", { name: "Reservations", exact: true })).toBeVisible();
  await expect(page.getByText("No reservations found")).toBeVisible();
});
