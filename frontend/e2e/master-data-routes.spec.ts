import { expect, test } from '@playwright/test';
import { cors, InvitationResponse, mockInvitationAttempt, submitInvitationSignIn, submitPasswordRecovery, totpUri, backupCodes, openAuthenticatorSetup, mockTwoFactorSignIn, submitTwoFactorSignIn, MeFixture, UnitFixture, CountFixtures, BootstrapResponse, BootstrapFixtures, defaultUnits, mockSignInAndMe, grantedProfile, approvalOne, approvalTwo, mockApprovals, approvalDetail, mockApprovalDetail, notificationOne, notificationTwo, mockNotifications, publicApprovalToken, publicApproval, mockPublicApproval, plantAlpha, plantBeta, departmentProduction, departmentFinishing, mockDepartments, storeRaw, storeFinished, mockStores, paymentTermNet30, taxCodeGst18, categoryRubber, categoryNatural, uomKilogram, mockReferenceData, vendorOrbit, mockVendors, mockCustomerDetail, customerAcme, mockCustomers, mockVendorDetail, itemRubber, mockItems, itemFinished, itemPolicy, itemSpec, itemBom, itemChange, itemPart, mockItemDetail, mockBoms, bomDraft, bomPrevious, mockBomDetail, routeMouldV2, routeMouldV1, routeMetal, mockRoutes, indentProfile, indentAlpha, indentBeta, mockIndentRegister, indentDetailFixture, indentPutCors, mockOwnedIndent, rfqProfile, rfqAlpha, rfqBeta, rfqLineFixture, rfqInvitations, rfqDetailFixture, mockRfqRegister, poAlpha, poBeta, poLine, poDetail, mockPoRegister, mockPoEditCatalog, inventoryProfile, grnAlpha, grnBeta, grnDetail, mockGrnRegister, mockGrnCreationSources, stockAlpha, stockBeta, stockTrace, mockStockSources, issueProfile, issuePlan, postedIssue, issueSummary, mockIssueHistory } from './support/fixtures';

test("route readers see active revisions and stages without creation controls", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  const requests = await mockRoutes(page);
  await page.goto("/app/master-data/engineering/routes");
  const table = page.getByRole("table", { name: "Route results" });
  await expect(table.getByText("MOULD", { exact: true })).toBeVisible();
  await expect(table.getByText("Moulding and finishing")).toBeVisible();
  await expect(table.getByText("Revision 2")).toBeVisible();
  await expect(table.getByRole("row", { name: /MOULD.*Revision 2/ }).getByText("3 stages")).toBeVisible();
  await expect(table.getByRole("link", { name: "Open route MOULD revision 1" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Create route" })).toHaveCount(0);
  expect(requests.every(raw => new URL(raw).searchParams.get("active") !== "false")).toBe(true);
  await page.goto("/app/master-data/engineering/routes/new");
  await expect(page.getByRole("heading", { name: "Create route" })).toHaveCount(0);
});

test("opening a route reads its version and ordered stages without write controls", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockRoutes(page);
  await page.route(`**/api/v1/product/routes/${routeMouldV2.route_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(routeMouldV2) }));
  await page.goto("/app/master-data/engineering/routes");
  await page.getByRole("link", { name: "Open route MOULD revision 2" }).first().click();
  await expect(page.getByRole("heading", { name: routeMouldV2.name })).toBeVisible();
  await expect(page.getByText("Mix compound")).toBeVisible();
  await expect(page.getByText("Finished goods store")).toBeVisible();
  await expect(page.getByRole("button", { name: "Revise route" })).toHaveCount(0);
});

test("route status and local search expose the correct active and inactive revisions", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  const requests = await mockRoutes(page);
  await page.goto("/app/master-data/engineering/routes");
  await page.getByLabel("Route status").selectOption("all");
  await page.getByLabel("Route search").fill("mould");
  const table = page.getByRole("table", { name: "Route results" });
  await expect(table.getByText("Revision 2")).toBeVisible();
  await expect(table.getByRole("row", { name: /MOULD.*Revision 1/ }).getByText("Revision 1")).toBeVisible();
  await expect(table.getByText("Metal fabrication")).toHaveCount(0);
  await expect(page).toHaveURL(/status=all.*q=mould/);
  expect(requests.some(raw => new URL(raw).searchParams.get("active") === "true")).toBe(true);
  expect(requests.some(raw => new URL(raw).searchParams.get("active") === "false")).toBe(true);
  expect(requests.every(raw => !new URL(raw).searchParams.has("q") && !new URL(raw).searchParams.has("limit"))).toBe(true);
});

test("exact route code lookup uses by-code and explains an absent active route", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockRoutes(page);
  const codes: string[] = [];
  await page.route("**/api/v1/product/routes/by-code/**", route => { codes.push(new URL(route.request().url()).pathname.split("/").pop() ?? ""); return route.fallback(); });
  await page.goto("/app/master-data/engineering/routes");
  await page.getByLabel("Exact route code").fill("mould");
  await page.getByRole("button", { name: "Find active route" }).click();
  await expect(page.getByRole("region", { name: "Exact code result" })).toContainText("Revision 2");
  await page.getByLabel("Exact route code").fill("unknown");
  await page.getByRole("button", { name: "Find active route" }).click();
  await expect(page.getByRole("heading", { name: "No active route found" })).toBeVisible();
  expect(codes).toContain("MOULD");
  expect(codes).toContain("UNKNOWN");
});

test("route manager creates ordered stages with one final end and preserves the new code in list search", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "route.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  const routes = [routeMouldV2];
  await mockRoutes(page, routes);
  let body: Record<string, unknown> | null = null;
  await page.route("**/api/v1/product/routes", route => route.request().method() === "POST" ? (body = route.request().postDataJSON(), routes.push({ ...routeMouldV2, route_id: "aaaaaaaa-9999-9999-9999-999999999999", code: "ASSEMBLY", name: "Final assembly", version: 1, stages: [{ sequence_no: 1, stage_code: "CUT", stage_name: "Cutting", is_qa_gate: true, is_end: false }, { sequence_no: 2, stage_code: "STORE", stage_name: "Finished store", is_qa_gate: false, is_end: true }] }), route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify(routes[routes.length - 1]) })) : route.fallback());
  await page.goto("/app/master-data/engineering/routes");
  await page.getByRole("button", { name: "Create route" }).click();
  await expect(page.getByRole("heading", { name: "Create route" })).toBeVisible();
  await page.getByLabel("Route code").fill("assembly");
  await expect(page.getByLabel("Route code")).toHaveValue("assembly");
  await page.getByLabel("Route name").fill("Final assembly");
  await page.getByLabel("Stage code").first().fill("CUT");
  await page.getByLabel("Stage name").first().fill("Cutting");
  await page.getByLabel("QA gate").first().check();
  await page.getByRole("button", { name: "Add stage" }).click();
  await expect(page.getByLabel("Route code")).toHaveValue("assembly");
  await page.getByLabel("Stage code").last().fill("STORE");
  await page.getByLabel("Stage name").last().fill("Finished store");
  await expect(page.getByLabel("Route code")).toHaveValue("assembly");
  await page.getByRole("button", { name: "Save route" }).click();
  expect(body).toEqual({ code: "ASSEMBLY", name: "Final assembly", stages: [{ stage_code: "CUT", stage_name: "Cutting", is_qa_gate: true, is_end: false }, { stage_code: "STORE", stage_name: "Finished store", is_qa_gate: false, is_end: true }] });
  await expect(page).toHaveURL(/\/engineering\/routes\?q=ASSEMBLY/);
  await expect(page.getByRole("status", { name: "Route creation status" })).toContainText("Route ASSEMBLY created");
  await expect(page.getByRole("table", { name: "Route results" }).getByText("ASSEMBLY", { exact: true })).toBeVisible();
});

test("route create 409 preserves entered stages and shows backend detail", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "route.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockRoutes(page);
  await page.route("**/api/v1/product/routes", route => route.request().method() === "POST" ? route.fulfill({ status: 409, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "Route code already exists" }) }) : route.fallback());
  await page.goto("/app/master-data/engineering/routes/new");
  await page.getByLabel("Route code").fill("MOULD");
  await page.getByLabel("Route name").fill("New moulding route");
  await page.getByLabel("Stage code").fill("QA");
  await page.getByLabel("Stage name").fill("Quality check");
  await page.getByRole("button", { name: "Save route" }).click();
  await expect(page.getByRole("alert")).toContainText("Route code already exists");
  await expect(page.getByLabel("Route name")).toHaveValue("New moulding route");
  await expect(page.getByLabel("Stage code")).toHaveValue("QA");
});

test("route register shows request IDs and retries a server error", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  let recover = false;
  await page.route("**/api/v1/product/routes?**", route => route.fulfill(recover ? { status: 200, headers: cors, contentType: "application/json", body: JSON.stringify([routeMouldV2]) } : { status: 503, headers: { ...cors, "X-Request-ID": "req-routes-503" }, contentType: "application/json", body: JSON.stringify({ detail: "Route service unavailable" }) }));
  await page.goto("/app/master-data/engineering/routes");
  await expect(page.getByRole("alert")).toContainText("Route service unavailable");
  await expect(page.getByText("Request ID: req-routes-503")).toBeVisible();
  recover = true;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("table", { name: "Route results" })).toContainText("MOULD");
});

test("route register has an honest loading and empty state", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/v1/product/routes?**", async route => { await pending; return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: "[]" }); });
  await page.goto("/app/master-data/engineering/routes");
  await expect(page.getByText("Loading routes…")).toBeVisible();
  release();
  await expect(page.getByRole("heading", { name: "No routes found" })).toBeVisible();
  await expect(page.getByText("No active routes were returned.")).toBeVisible();
});

test("route creation rejects duplicate stage codes before sending a request", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "route.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockRoutes(page);
  let writes = 0;
  await page.route("**/api/v1/product/routes", route => { writes += 1; return route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify(routeMouldV2) }); });
  await page.goto("/app/master-data/engineering/routes/new");
  await page.getByLabel("Route code").fill("ASSEMBLY");
  await page.getByLabel("Route name").fill("Final assembly");
  await page.getByLabel("Stage code").first().fill("CUT");
  await page.getByLabel("Stage name").first().fill("Cutting");
  await page.getByRole("button", { name: "Add stage" }).click();
  await page.getByLabel("Stage code").last().fill("cut");
  await page.getByLabel("Stage name").last().fill("Second cut");
  await page.getByRole("button", { name: "Save route" }).click();
  await expect(page.getByRole("alert")).toContainText("Stage codes must be unique");
  expect(writes).toBe(0);
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }]) {
  test(`route register and creation fit ${viewport.width}px without document overflow`, async ({ page }) => {
    const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "route.manage"] };
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
    await mockRoutes(page);
    await page.goto("/app/master-data/engineering/routes");
    await expect(page.getByRole(viewport.width < 560 ? "list" : "table", { name: "Route results" })).toContainText("MOULD");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    if (viewport.width === 768) { const region = page.getByRole("region", { name: "Scrollable route results" }); await region.focus(); await expect(region).toBeFocused(); }
    await page.getByRole("button", { name: "Create route" }).click();
    await expect(page.getByRole("heading", { name: "Create route" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
}

test("active route manager revises ordered stages through a new version", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "route.manage"] };
  const revised = { ...routeMouldV2, route_id: "aaaaaaaa-aaaa-6666-6666-666666666666", version: 3, name: "Moulding with earlier QA", stages: [{ ...routeMouldV2.stages[1], sequence_no: 1 }, { ...routeMouldV2.stages[0], sequence_no: 2 }, routeMouldV2.stages[2]] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await page.route(`**/api/v1/product/routes/${routeMouldV2.route_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(routeMouldV2) }));
  await page.route(`**/api/v1/product/routes/${revised.route_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(revised) }));
  let body: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/product/routes/${routeMouldV2.route_id}/revise`, route => route.request().method() === "POST" ? (body = route.request().postDataJSON(), route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify(revised) })) : route.fallback());
  await page.goto(`/app/master-data/engineering/routes/${routeMouldV2.route_id}?status=all`);
  await page.getByRole("button", { name: "Revise route" }).click();
  await expect(page.getByRole("heading", { name: "Revise route" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Move stage 1 up" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Move stage 3 down" })).toHaveCount(0);
  await expect(page.getByLabel("Route name")).toHaveValue(routeMouldV2.name);
  await page.getByLabel("Route name").fill(revised.name);
  await page.getByRole("button", { name: "Move stage 2 up" }).click();
  await expect(page.getByLabel("Stage code").first()).toHaveValue("QA");
  await page.getByRole("button", { name: "Save revision" }).click();
  expect(body).toEqual({ name: revised.name, stages: [{ stage_code: "QA", stage_name: "Quality check", is_qa_gate: true, is_end: false }, { stage_code: "MIX", stage_name: "Mix compound", is_qa_gate: false, is_end: false }, { stage_code: "FG_STORE", stage_name: "Finished goods store", is_qa_gate: false, is_end: true }] });
  await expect(page).toHaveURL(new RegExp(`/routes/${revised.route_id}\\?status=all`));
  await expect(page.getByRole("heading", { name: revised.name })).toBeVisible();
  await expect(page.getByText("Revision 3")).toBeVisible();
});

test("inactive route versions and readers have no revise or retire controls", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "route.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await page.route(`**/api/v1/product/routes/${routeMouldV1.route_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(routeMouldV1) }));
  await page.goto(`/app/master-data/engineering/routes/${routeMouldV1.route_id}`);
  await expect(page.getByRole("heading", { name: routeMouldV1.name })).toBeVisible();
  await expect(page.getByRole("button", { name: "Revise route" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retire route" })).toHaveCount(0);
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await page.route(`**/api/v1/product/routes/${routeMouldV2.route_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(routeMouldV2) }));
  await page.goto(`/app/master-data/engineering/routes/${routeMouldV2.route_id}`);
  await expect(page.getByRole("heading", { name: routeMouldV2.name })).toBeVisible();
  await expect(page.getByRole("button", { name: "Revise route" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retire route" })).toHaveCount(0);
});

test("retire route requires confirmation and shows active-item 409 blocker", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "route.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await page.route(`**/api/v1/product/routes/${routeMouldV2.route_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(routeMouldV2) }));
  let calls = 0;
  await page.route(`**/api/v1/product/routes/${routeMouldV2.route_id}/retire`, route => { calls += 1; return route.fulfill({ status: 409, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "4 active item(s) still follow this route" }) }); });
  await page.goto(`/app/master-data/engineering/routes/${routeMouldV2.route_id}`);
  await page.getByRole("button", { name: "Retire route" }).click();
  const dialog = page.getByRole("dialog", { name: "Retire this route?" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "Confirm retirement" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retire route" })).toBeFocused();
  expect(calls).toBe(0);
  await page.getByRole("button", { name: "Retire route" }).click();
  await dialog.getByRole("button", { name: "Confirm retirement" }).click();
  await expect(page.getByRole("alert")).toContainText("4 active item(s) still follow this route");
  await expect(page.getByText("Active", { exact: true })).toBeVisible();
  expect(calls).toBe(1);
});

test("successful route retirement updates status and removes write actions", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "route.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await page.route(`**/api/v1/product/routes/${routeMouldV2.route_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(routeMouldV2) }));
  await page.route(`**/api/v1/product/routes/${routeMouldV2.route_id}/retire`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ ...routeMouldV2, active: false }) }));
  await page.goto(`/app/master-data/engineering/routes/${routeMouldV2.route_id}`);
  await page.getByRole("button", { name: "Retire route" }).click();
  await page.getByRole("dialog", { name: "Retire this route?" }).getByRole("button", { name: "Confirm retirement" }).click();
  await expect(page.getByText("Inactive", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retire route" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Revise route" })).toHaveCount(0);
  await expect(page.getByRole("status", { name: "Route status update" })).toContainText("retired");
});

test("route revision error preserves edited stage values and request ID", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "route.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await page.route(`**/api/v1/product/routes/${routeMouldV2.route_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(routeMouldV2) }));
  await page.route(`**/api/v1/product/routes/${routeMouldV2.route_id}/revise`, route => route.fulfill({ status: 503, headers: { ...cors, "X-Request-ID": "req-route-revise-503" }, contentType: "application/json", body: JSON.stringify({ detail: "Revision could not be saved" }) }));
  await page.goto(`/app/master-data/engineering/routes/${routeMouldV2.route_id}`);
  await page.getByRole("button", { name: "Revise route" }).click();
  await page.getByLabel("Stage name").first().fill("Updated mixing");
  await page.getByRole("button", { name: "Save revision" }).click();
  await expect(page.getByRole("alert")).toContainText("Revision could not be saved");
  await expect(page.getByText("Request ID: req-route-revise-503")).toBeVisible();
  await expect(page.getByLabel("Stage name").first()).toHaveValue("Updated mixing");
});

test("route detail retries a server error with its request ID", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  let recover = false;
  await page.route(`**/api/v1/product/routes/${routeMouldV2.route_id}`, route => route.fulfill(recover ? { status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(routeMouldV2) } : { status: 503, headers: { ...cors, "X-Request-ID": "req-route-detail-503" }, contentType: "application/json", body: JSON.stringify({ detail: "Route detail unavailable" }) }));
  await page.goto(`/app/master-data/engineering/routes/${routeMouldV2.route_id}`);
  await expect(page.getByRole("alert")).toContainText("Route detail unavailable");
  await expect(page.getByText("Request ID: req-route-detail-503")).toBeVisible();
  recover = true;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: routeMouldV2.name })).toBeVisible();
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }]) {
  test(`route revision and retirement fit ${viewport.width}px with keyboard recovery`, async ({ page }) => {
    const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "route.manage"] };
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
    await page.route(`**/api/v1/product/routes/${routeMouldV2.route_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(routeMouldV2) }));
    await page.goto(`/app/master-data/engineering/routes/${routeMouldV2.route_id}`);
    await page.getByRole("button", { name: "Revise route" }).click();
    await expect(page.getByRole("heading", { name: "Revise route" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Revise route" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Retire route" })).toBeDisabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.getByRole("button", { name: "Cancel revision" }).click();
    await expect(page.getByRole("button", { name: "Revise route" })).toBeFocused();
    await page.getByRole("button", { name: "Retire route" }).click();
    const dialog = page.getByRole("dialog", { name: "Retire this route?" });
    await expect(dialog).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Retire route" })).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
}
