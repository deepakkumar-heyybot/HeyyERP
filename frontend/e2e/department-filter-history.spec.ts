import { expect, test } from "@playwright/test";
import { grantedProfile, mockDepartments, mockSignInAndMe, plantAlpha, plantBeta } from "./support/fixtures";

test("department filters preserve restored history during rapid changes", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, all_plants: true, unit_ids: [] } }, [plantAlpha, plantBeta]);
  const requests = await mockDepartments(page);
  await page.goto(`/app/master-data/organization/departments?unit_id=${plantAlpha.unit_id}&q=prod`);
  await expect(page.getByLabel("Search departments")).toHaveValue("prod");
  await expect(page.getByLabel("Plant filter")).toHaveValue(plantAlpha.unit_id);

  await page.getByRole("navigation", { name: "Organization master sections" }).getByRole("link", { name: "Departments" }).click();
  await expect(page.getByLabel("Search departments")).toHaveValue("");
  await page.goBack();
  await expect(page.getByLabel("Search departments")).toHaveValue("prod");
  await page.goForward();
  await expect(page.getByLabel("Search departments")).toHaveValue("");
  await page.getByLabel("Status filter").selectOption("true");
  await expect.poll(() => {
    const params = new URL(page.url()).searchParams;
    return [params.get("unit_id"), params.get("q"), params.get("active")];
  }).toEqual([null, null, "true"]);
  await page.goBack();
  await expect(page.getByLabel("Search departments")).toHaveValue("prod");
  await expect(page.getByLabel("Status filter")).toHaveValue("");

  // Update both controls before the router can render the first navigation.
  await page.evaluate(() => {
    const status = document.querySelector<HTMLSelectElement>("#department-status-filter")!;
    const search = document.querySelector<HTMLInputElement>("#department-search")!;
    status.value = "false";
    status.dispatchEvent(new Event("change", { bubbles: true }));
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(search, "finish");
    search.dispatchEvent(new Event("input", { bubbles: true }));
  });

  await expect(page.getByLabel("Status filter")).toHaveValue("false");
  await expect(page.getByLabel("Search departments")).toHaveValue("finish");
  await expect.poll(() => {
    const params = new URL(page.url()).searchParams;
    return [params.get("unit_id"), params.get("active"), params.get("q")];
  }).toEqual([plantAlpha.unit_id, "false", "finish"]);
  const results = page.getByRole("table", { name: "Department results" });
  await expect(results.getByText("Finishing")).toBeVisible();
  await expect(results.locator("tbody td:nth-child(2)").getByText("Production", { exact: true })).toHaveCount(0);
  await expect.poll(() => requests.some(url => {
    const params = new URL(url).searchParams;
    return params.get("unit_id") === plantAlpha.unit_id && params.get("active") === "false" && !params.has("q");
  })).toBe(true);
  expect(requests.every(url => !new URL(url).searchParams.has("q"))).toBe(true);
});
