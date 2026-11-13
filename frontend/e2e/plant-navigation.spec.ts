import { expect, test } from "@playwright/test";
import { cors, inventoryProfile, mockSignInAndMe, mockStockSources, plantAlpha, plantBeta } from "./support/fixtures";

test("multi-plant selection survives inventory navigation without restarting access bootstrap", async ({ page }) => {
  const profile = { ...inventoryProfile, all_plants: false, unit_ids: [plantAlpha.unit_id, plantBeta.unit_id] };
  await mockSignInAndMe(page, { status: 200, body: profile }, [plantAlpha, { ...plantBeta, active: true }]);
  await mockStockSources(page);
  await page.route("**/api/v1/inventory/reservations?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: "[]" }));
  await expect(page.getByRole("heading", { name: "Select an active plant" })).toBeVisible();
  let bootstrapReads = 0;
  await page.route("**/api/v1/me", route => { bootstrapReads += 1; return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(profile) }); });
  const plant = page.getByRole("combobox", { name: "Active plant" });
  await plant.selectOption(plantAlpha.unit_id);
  await page.getByRole("link", { name: "Inventory", exact: true }).click();
  await expect(plant).toHaveValue(plantAlpha.unit_id);
  await page.getByRole("link", { name: "Reservations", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Reservations", exact: true })).toBeVisible();
  await expect(plant).toHaveValue(plantAlpha.unit_id);
  await page.getByRole("button", { name: "New reservation" }).click();
  await expect(page.getByRole("heading", { name: "New reservation" })).toBeVisible();
  await expect(plant).toHaveValue(plantAlpha.unit_id);
  await page.getByRole("link", { name: "Back to reservations" }).click();
  await expect(page.getByRole("heading", { name: "Reservations", exact: true })).toBeVisible();
  await expect(plant).toHaveValue(plantAlpha.unit_id);
  expect(bootstrapReads).toBe(0);
});
