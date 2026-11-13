import { expect, test } from '@playwright/test';
import { cors, mockSignInAndMe, grantedProfile, plantAlpha, paymentTermNet30, mockCustomerDetail, customerAcme, mockCustomers } from './support/fixtures';

test("customer detail renders exact master data and hides unsupported tabs", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockCustomerDetail(page);
  await page.goto(`/app/master-data/parties/customers/${customerAcme.customer_id}`);
  await expect(page.getByRole("heading", { name: customerAcme.name })).toBeVisible();
  await expect(page.getByText(customerAcme.gstin)).toBeVisible();
  await expect(page.getByText("Net 30 days")).toBeVisible();
  await expect(page.getByText("₹250000.00")).toBeVisible();
  await expect(page.getByRole("link", { name: "Credit" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Statement" })).toHaveCount(0);
  await expect(page.getByText(/customer parts/i)).toHaveCount(0);
  await expect(page.getByRole("button", { name: /edit customer/i })).toHaveCount(0);
});

test("customer detail remains readable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const salesReader = { ...grantedProfile, modules: [...grantedProfile.modules, "sales"] };
  await mockSignInAndMe(page, { status: 200, body: salesReader }, [plantAlpha]);
  await mockCustomerDetail(page);
  await page.goto(`/app/master-data/parties/customers/${customerAcme.customer_id}`);
  await expect(page.getByRole("heading", { name: customerAcme.name })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Customer sections" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);
});

test("customer readers get a server-paged identity, terms, status, and backend credit view", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  await mockCustomers(page);
  await page.goto("/app/master-data/parties");
  const table = page.getByRole("table", { name: "Customer results" });
  await expect(table.getByText(customerAcme.name)).toBeVisible();
  await expect(table.getByText(customerAcme.gstin)).toBeVisible();
  await expect(table.getByText("Net 30 days")).toBeVisible();
  await expect(table.getByText("₹250000.00")).toBeVisible();
  await expect(table.getByText("Active", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Create customer" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /delete/i })).toHaveCount(0);
});

test("customer search, status, and pagination use only supported server parameters", async ({ page }) => {
  const customers = Array.from({ length: 22 }, (_, index) => ({ ...customerAcme, customer_id: `${String(index + 1).padStart(8, "0")}-3333-3333-3333-333333333333`, code: `CUST${index + 1}`, name: `Customer ${index + 1}`, active: index !== 21 }));
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  const requests = await mockCustomers(page, customers);
  await page.goto("/app/master-data/parties");
  await expect(page.getByText("Showing 1–20 of 22 records")).toBeVisible();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page).toHaveURL(/page=2/);
  await expect.poll(() => requests.some(url => url.includes("offset=20") && url.includes("limit=20"))).toBe(true);
  await page.getByLabel("Search customers").fill("Customer 2");
  await expect(page).toHaveURL(/q=Customer\+2/);
  await expect(page).not.toHaveURL(/page=2/);
  await expect.poll(() => requests.some(url => url.includes("q=Customer+2"))).toBe(true);
  await page.getByLabel("Status filter").selectOption("false");
  await expect(page).toHaveURL(/active=false/);
  await expect.poll(() => requests.some(url => url.includes("active=false"))).toBe(true);
});

test("a customer manager creates a customer on a dedicated route with exact decimal fields", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "customer.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockCustomers(page);
  let created: Record<string, unknown> | null = null;
  await page.route("**/api/v1/parties/customers", route => {
    if (route.request().method() !== "POST") return route.fallback();
    created = route.request().postDataJSON();
    return route.fulfill({ status: 201, headers: cors, contentType: "application/json", body: JSON.stringify({ ...created, customer_id: "dddddddd-2222-2222-2222-222222222222", active: true }) });
  });
  await page.goto("/app/master-data/parties");
  await page.getByRole("button", { name: "Create customer" }).click();
  await expect(page).toHaveURL(/\/app\/master-data\/parties\/customers\/new$/);
  await page.getByLabel("Customer code").fill("vrub-auto");
  await page.getByLabel("Customer name").fill("V-Rub Automotive");
  await page.getByLabel("GSTIN").fill("27AAAAA0000A1Z5");
  await page.getByLabel("Payment term").selectOption(paymentTermNet30.payment_term_id);
  await page.getByLabel("Credit limit").fill("125000.50");
  await page.getByRole("button", { name: "Save customer" }).click();
  expect(created).toEqual({ code: "VRUB-AUTO", name: "V-Rub Automotive", gstin: "27AAAAA0000A1Z5", payment_term_id: paymentTermNet30.payment_term_id, credit_limit: "125000.50" });
  await expect(page).toHaveURL(new RegExp(`/app/master-data/parties/customers/dddddddd-2222-2222-2222-222222222222$`));
});

test("customer validation errors preserve the dedicated creation form", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "customer.manage"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockCustomers(page);
  await page.route("**/api/v1/parties/customers", route => route.request().method() === "POST" ? route.fulfill({ status: 422, headers: cors, contentType: "application/json", body: JSON.stringify({ detail: "Customer code already exists" }) }) : route.fallback());
  await page.goto("/app/master-data/parties/customers/new");
  await page.getByLabel("Customer code").fill("ACME");
  await page.getByLabel("Customer name").fill("Acme revised");
  await page.getByRole("button", { name: "Save customer" }).click();
  await expect(page.getByRole("alert")).toContainText("Customer code already exists");
  await expect(page.getByLabel("Customer name")).toHaveValue("Acme revised");
});

test("customer list exposes loading, empty, and recoverable request-ID errors", async ({ page }) => {
  await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
  let attempts = 0;
  await page.route("**/api/v1/parties/customers?**", async route => {
    attempts += 1;
    await new Promise(resolve => setTimeout(resolve, 200));
    return route.fulfill({ status: attempts === 1 ? 503 : 200, headers: { ...cors, ...(attempts === 1 ? { "X-Request-ID": "req-customers-503" } : {}) }, contentType: "application/json", body: JSON.stringify(attempts === 1 ? { detail: "Customer master is temporarily unavailable" } : { items: [], total: 0, limit: 20, offset: 0 }) });
  });
  await page.route("**/api/v1/ref/payment-terms?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.goto("/app/master-data/parties");
  await expect(page.getByText("Loading customers…")).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Customer master is temporarily unavailable");
  await expect(page.getByText("Request ID: req-customers-503")).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "No customers found" })).toBeVisible();
});

for (const viewport of [{ width: 360, height: 800 }, { width: 768, height: 1024 }]) {
  test(`customer results remain usable without overflow at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockSignInAndMe(page, { status: 200, body: grantedProfile }, [plantAlpha]);
    await mockCustomers(page);
    await page.goto("/app/master-data/parties");
    if (viewport.width === 360) await expect(page.getByRole("list", { name: "Customer results" }).getByText(customerAcme.name)).toBeVisible();
    else await expect(page.getByRole("table", { name: "Customer results" })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(overflow).toBe(false);
  });
}

test("customer managers retain a read-only master summary with unfinished workflows hidden", async ({ page }) => {
  const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "customer.manage"], modules: [...grantedProfile.modules, "sales", "finance"] };
  await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
  await mockCustomerDetail(page);
  await page.goto(`/app/master-data/parties/customers/${customerAcme.customer_id}`);
  const details = page.getByRole("region", { name: customerAcme.name, exact: true });
  await expect(details.getByRole("heading", { name: customerAcme.name })).toBeVisible();
  await expect(details.getByText(customerAcme.gstin)).toBeVisible();
  await expect(details.getByText("Net 30 days")).toBeVisible();
  await expect(details.getByText("₹250000.00")).toBeVisible();
  await expect(details.getByRole("link", { name: "Master details" })).toBeVisible();
  await expect(details.getByRole("link", { name: /^(credit|statement)$/i })).toHaveCount(0);
  await expect(details.getByRole("button", { name: /edit customer|update address|save changes|save address/i })).toHaveCount(0);
  await expect(details.getByRole("textbox")).toHaveCount(0);
});

for (const section of ["credit", "statement"]) {
  test(`excluded customer ${section} deep links explain the handover scope without customer API calls`, async ({ page }) => {
    const manager = { ...grantedProfile, permissions: [...grantedProfile.permissions, "customer.manage"], modules: [...grantedProfile.modules, "sales", "finance"] };
    await mockSignInAndMe(page, { status: 200, body: manager }, [plantAlpha]);
    await mockCustomerDetail(page);
    const customerRequests: string[] = [];
    page.on("request", request => {
      if (/\/api\/v1\/(parties|sales|finance)\/customers(?:\/|\?|$)/.test(new URL(request.url()).pathname)) customerRequests.push(request.url());
    });
    await page.goto(`/app/master-data/parties/customers/${customerAcme.customer_id}/${section}`);
    await expect(page.getByRole("heading", { name: `Customer ${section} not included` })).toBeVisible();
    expect(customerRequests).toEqual([]);
    await page.getByRole("link", { name: "View customer master" }).click();
    await expect(page).toHaveURL(new RegExp(`/app/master-data/parties/customers/${customerAcme.customer_id}$`));
    await expect(page.getByRole("heading", { name: customerAcme.name })).toBeVisible();
  });
}
