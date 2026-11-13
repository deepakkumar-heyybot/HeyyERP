import { expect, test } from "@playwright/test";
import { categoryRubber, grantedProfile, mockItems, mockSignInAndMe, plantAlpha } from "./support/fixtures";

test("item filters preserve restored history when changed together", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  const requests = await mockItems(page);
  await page.goto("/app/master-data/engineering?q=Rubber&type=RM&page=2");
  await expect(page.getByLabel("Search items")).toHaveValue("Rubber");
  await expect(page.getByLabel("Category filter").locator("option")).toHaveCount(3);

  await page.getByRole("navigation", { name: "Engineering masters" }).getByRole("link", { name: "Items" }).click();
  await expect(page.getByLabel("Search items")).toHaveValue("");
  await page.goBack();
  await expect(page.getByLabel("Search items")).toHaveValue("Rubber");
  await expect(page.getByLabel("Item type")).toHaveValue("RM");
  await page.goForward();
  await expect(page.getByLabel("Search items")).toHaveValue("");
  await page.getByLabel("Status filter").selectOption("true");
  await expect.poll(() => {
    const params = new URL(page.url()).searchParams;
    return [params.get("q"), params.get("type"), params.get("active")];
  }).toEqual([null, null, "true"]);
  await page.goBack();
  await expect(page.getByLabel("Search items")).toHaveValue("Rubber");
  await expect(page.getByLabel("Status filter")).toHaveValue("");
  await expect(page.getByLabel("Category filter").locator("option")).toHaveCount(3);

  // Both changes occur before React can render a new router location.
  await page.evaluate(categoryId => {
    const category = document.querySelector<HTMLSelectElement>("#item-category-filter")!;
    const status = document.querySelector<HTMLSelectElement>("#item-status-filter")!;
    category.value = categoryId;
    category.dispatchEvent(new Event("change", { bubbles: true }));
    status.value = "true";
    status.dispatchEvent(new Event("change", { bubbles: true }));
  }, categoryRubber.category_id);

  await expect(page.getByLabel("Category filter")).toHaveValue(categoryRubber.category_id);
  await expect(page.getByLabel("Status filter")).toHaveValue("true");
  await expect.poll(() => {
    const params = new URL(page.url()).searchParams;
    return [params.get("q"), params.get("type"), params.get("category"), params.get("active"), params.get("page")];
  }).toEqual(["Rubber", "RM", categoryRubber.category_id, "true", null]);
  await expect.poll(() => requests.some(url => {
    const params = new URL(url).searchParams;
    return params.get("q") === "Rubber" && params.get("item_type") === "RM" && params.get("category_id") === categoryRubber.category_id && params.get("active") === "true" && params.get("offset") === "0";
  })).toBe(true);
});
