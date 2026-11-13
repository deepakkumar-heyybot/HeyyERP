import { expect, test } from '@playwright/test';
import { cors, InvitationResponse, mockInvitationAttempt, submitInvitationSignIn, submitPasswordRecovery, totpUri, backupCodes, openAuthenticatorSetup, mockTwoFactorSignIn, submitTwoFactorSignIn, MeFixture, UnitFixture, CountFixtures, BootstrapResponse, BootstrapFixtures, defaultUnits, mockSignInAndMe, grantedProfile, approvalOne, approvalTwo, mockApprovals, approvalDetail, mockApprovalDetail, notificationOne, notificationTwo, mockNotifications, publicApprovalToken, publicApproval, mockPublicApproval, plantAlpha, plantBeta, departmentProduction, departmentFinishing, mockDepartments, storeRaw, storeFinished, mockStores, paymentTermNet30, taxCodeGst18, categoryRubber, categoryNatural, uomKilogram, mockReferenceData, vendorOrbit, mockVendors, mockCustomerDetail, customerAcme, mockCustomers, mockVendorDetail, itemRubber, mockItems, itemFinished, itemPolicy, itemSpec, itemBom, itemChange, itemPart, mockItemDetail, mockBoms, bomDraft, bomPrevious, mockBomDetail, routeMouldV2, routeMouldV1, routeMetal, mockRoutes, indentProfile, indentAlpha, indentBeta, mockIndentRegister, indentDetailFixture, indentPutCors, mockOwnedIndent, rfqProfile, rfqAlpha, rfqBeta, rfqLineFixture, rfqInvitations, rfqDetailFixture, mockRfqRegister, poAlpha, poBeta, poLine, poDetail, mockPoRegister, mockPoEditCatalog, inventoryProfile, grnAlpha, grnBeta, grnDetail, mockGrnRegister, mockGrnCreationSources, stockAlpha, stockBeta, stockTrace, mockStockSources, issueProfile, issuePlan, postedIssue, issueSummary, mockIssueHistory } from './support/fixtures';

test("department plant, status, and search filters persist and use supported backend filters", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, all_plants: true, unit_ids: [] } }, [plantAlpha, plantBeta]);
  const requests = await mockDepartments(page);
  await page.getByRole("link", { name: "Organization" }).click();
  await page.getByRole("link", { name: "Departments", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Departments" })).toBeVisible();
  await page.getByLabel("Plant filter").selectOption(plantAlpha.unit_id);
  await page.getByLabel("Status filter").selectOption("false");
  await page.getByLabel("Search departments").fill("finish");
  await expect(page).toHaveURL(/unit_id=11111111-1111-1111-1111-111111111111/);
  await expect(page).toHaveURL(/active=false/);
  await expect(page).toHaveURL(/q=finish/);
  await expect.poll(() => requests.some(url => url.includes(`unit_id=${plantAlpha.unit_id}`) && url.includes("active=false"))).toBe(true);
  const results = page.getByRole("table", { name: "Department results" });
  await expect(results.getByText("Finishing")).toBeVisible();
  await expect(results.locator("tbody td:nth-child(2)").getByText("Production", { exact: true })).toHaveCount(0);
});

test("department writes are hidden without org.manage and no delete action is invented", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockDepartments(page, [departmentProduction]);
  await page.goto("/app/master-data/organization/departments");
  await expect(page.getByRole("button", { name: "Create department" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /delete/i })).toHaveCount(0);
  await page.getByRole("link", { name: `Open department ${departmentProduction.name}` }).click();
  await expect(page.getByRole("heading", { name: departmentProduction.name })).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit department" })).toHaveCount(0);
});

test("an org manager creates a department with hierarchy and head-role fields", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "org.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockDepartments(page, [departmentProduction]);
  let submitted: Record<string, unknown> | null = null;
  await page.route("**/api/v1/org/departments", route => {
    if (route.request().method() !== "POST") return route.fallback();
    submitted = route.request().postDataJSON();
    return route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify({ ...submitted, department_id: departmentFinishing.department_id, active: true }) });
  });
  await page.goto("/app/master-data/organization/departments");
  await page.getByRole("button", { name: "Create department" }).click();
  await page.getByLabel("Department code").fill("FINISH");
  await page.getByLabel("Department name").fill("Finishing");
  await page.getByLabel("Plant", { exact: true }).selectOption(plantAlpha.unit_id);
  await page.getByLabel("Parent department").selectOption(departmentProduction.department_id);
  await page.getByLabel("Head role code").fill("PROD");
  await page.getByRole("button", { name: "Save department" }).click();
  await expect(page.getByText("Department created.", { exact: true })).toBeVisible();
  expect(submitted).toEqual({ code: "FINISH", name: "Finishing", unit_id: plantAlpha.unit_id, parent_department_id: departmentProduction.department_id, head_role_code: "PROD" });
});

test("department deactivation conflicts preserve edits and backend detail", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "org.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockDepartments(page, [departmentProduction]);
  await page.route(`**/api/v1/org/departments/${departmentProduction.department_id}`, route => {
    if (route.request().method() === "GET") return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(departmentProduction) });
    return route.fulfill({ status: 409, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "Deactivate the sub-departments first" }) });
  });
  await page.goto(`/app/master-data/organization/departments/${departmentProduction.department_id}`);
  await page.getByRole("button", { name: "Edit department" }).click();
  await page.getByLabel("Department name").fill("Production and Planning");
  await page.getByLabel("Active department").uncheck();
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("alert")).toContainText("Deactivate the sub-departments first");
  await expect(page.getByLabel("Department name")).toHaveValue("Production and Planning");
});

test("department results use mobile cards with plant, code, and status", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockDepartments(page, [departmentProduction]);
  await page.goto("/app/master-data/organization/departments");
  const cards = page.getByRole("list", { name: "Department results" });
  await expect(cards).toBeVisible();
  await expect(cards.getByText("PROD", { exact: true })).toBeVisible();
  await expect(cards.getByText(plantAlpha.name, { exact: true })).toBeVisible();
  await expect(cards.getByText("Active", { exact: true })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
});

test("store type, plant, status, and search filters persist and use exact backend enums", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: { ...grantedProfile, all_plants: true, unit_ids: [] } }, [plantAlpha, plantBeta]);
  const requests = await mockStores(page);
  await page.goto("/app/master-data/organization/stores");
  await expect(page.getByRole("heading", { name: "Stores" })).toBeVisible();
  await page.getByLabel("Store type filter").selectOption("FINISHED_GOODS");
  await page.getByLabel("Plant filter").selectOption(plantAlpha.unit_id);
  await page.getByLabel("Status filter").selectOption("false");
  await page.getByLabel("Search stores").fill("finished");
  await expect(page).toHaveURL(/store_type=FINISHED_GOODS/);
  await expect(page).toHaveURL(/unit_id=11111111-1111-1111-1111-111111111111/);
  await expect(page).toHaveURL(/active=false/);
  await expect(page).toHaveURL(/q=finished/);
  await expect.poll(() => requests.some(url => url.includes("store_type=FINISHED_GOODS") && url.includes(`unit_id=${plantAlpha.unit_id}`) && url.includes("active=false"))).toBe(true);
  const results = page.getByRole("table", { name: "Store results" });
  await expect(results.getByText(storeFinished.name)).toBeVisible();
  await expect(results.locator("tbody td:nth-child(2)").getByText(storeRaw.name, { exact: true })).toHaveCount(0);
});

test("store writes are hidden without org.manage and no delete action is invented", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockStores(page, [storeRaw]);
  await page.goto("/app/master-data/organization/stores");
  await expect(page.getByRole("button", { name: "Create store" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /delete/i })).toHaveCount(0);
  await page.getByRole("link", { name: `Open store ${storeRaw.name}` }).click();
  await expect(page.getByRole("heading", { name: storeRaw.name })).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit store" })).toHaveCount(0);
});

test("an org manager creates a store with the exact plant and type contract", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "org.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockStores(page, [storeRaw]);
  let submitted: Record<string, unknown> | null = null;
  await page.route("**/api/v1/org/stores", route => {
    if (route.request().method() !== "POST") return route.fallback();
    submitted = route.request().postDataJSON();
    return route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify({ ...submitted, store_id: storeFinished.store_id, active: true }) });
  });
  await page.goto("/app/master-data/organization/stores");
  await page.getByRole("button", { name: "Create store" }).click();
  await page.getByLabel("Store code").fill("WIP-01");
  await page.getByLabel("Store name").fill("Work in progress store");
  await page.getByLabel("Plant", { exact: true }).selectOption(plantAlpha.unit_id);
  await page.getByLabel("Store type", { exact: true }).selectOption("WIP");
  await page.getByRole("button", { name: "Save store" }).click();
  await expect(page.getByText("Store created.", { exact: true })).toBeVisible();
  expect(submitted).toEqual({ code: "WIP-01", name: "Work in progress store", unit_id: plantAlpha.unit_id, store_type: "WIP" });
});

test("store deactivation conflicts explain why it remains active and preserve edits", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "org.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockStores(page, [storeRaw]);
  await page.route(`**/api/v1/org/stores/${storeRaw.store_id}`, route => {
    if (route.request().method() === "GET") return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(storeRaw) });
    return route.fulfill({ status: 409, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "This store still holds stock, has a transfer on the way or a count open" }) });
  });
  await page.goto(`/app/master-data/organization/stores/${storeRaw.store_id}`);
  await page.getByRole("button", { name: "Edit store" }).click();
  await page.getByLabel("Store name").fill("Main raw material store");
  await page.getByLabel("Active store").uncheck();
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("alert")).toContainText("This store still holds stock, has a transfer on the way or a count open");
  await expect(page.getByRole("alert")).toContainText("The store remains active");
  await expect(page.getByLabel("Store name")).toHaveValue("Main raw material store");
  await expect(page.getByLabel("Active store")).not.toBeChecked();
});

test("store results use readable mobile cards without horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockStores(page, [storeRaw]);
  await page.goto("/app/master-data/organization/stores");
  const cards = page.getByRole("list", { name: "Store results" });
  await expect(cards).toBeVisible();
  await expect(cards.getByText(storeRaw.code, { exact: true })).toBeVisible();
  await expect(cards.getByText(plantAlpha.name, { exact: true })).toBeVisible();
  await expect(cards.getByText("Raw material", { exact: true })).toBeVisible();
  await expect(cards.getByText("Active", { exact: true })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
});
