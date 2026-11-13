import { expect, test } from '@playwright/test';

export const cors = {
  "access-control-allow-origin": "http://127.0.0.1:5174",
  "access-control-allow-credentials": "true",
  "access-control-allow-headers": "content-type",
  "access-control-allow-methods": "GET,POST,OPTIONS",
  "access-control-expose-headers": "X-Request-ID",
};

export type InvitationResponse = {
  status: number;
  body: Record<string, unknown>;
};

export async function mockInvitationAttempt(page: import("@playwright/test").Page, invitation: InvitationResponse) {
  await page.route("**/api/auth/sign-in/email", route => route.request().method() === "OPTIONS"
    ? route.fulfill({ status: 204, headers: cors })
    : route.fulfill({
      status: 200,
      headers: cors,
      contentType: "application/json",
      body: JSON.stringify({
        redirect: false,
        token: "test-session-token",
        user: { id: "u1", email: "invitee@vrub.example", emailVerified: true, name: "Invitee" },
      }),
    }));
  await page.route("**/api/auth/organization/accept-invitation**", route => route.request().method() === "OPTIONS"
    ? route.fulfill({ status: 204, headers: cors })
    : route.fulfill({
      status: invitation.status,
      headers: cors,
      contentType: "application/json",
      body: JSON.stringify(invitation.body),
    }));
}

export async function submitInvitationSignIn(page: import("@playwright/test").Page, invitationId: string) {
  await page.goto(`/accept-invitation/${invitationId}`);
  await page.getByLabel("Email address").fill("invitee@vrub.example");
  await page.getByRole("textbox", { name: "Password" }).fill("Strong-password-123");
  await page.getByRole("button", { name: "Sign in and accept invitation" }).click();
}

export async function submitPasswordRecovery(page: import("@playwright/test").Page, email = "person@vrub.example") {
  await page.goto("/forgot-password");
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Send reset link" }).click();
}

export const totpUri = "otpauth://totp/heyyERP%20%C2%B7%20V-Rub%20Suspensions:mfa%40vrub.example?secret=JBSWY3DPEHPK3PXP&issuer=heyyERP%20%C2%B7%20V-Rub%20Suspensions";

export const backupCodes = ["alpha-1111", "bravo-2222", "charlie-3333"];

export async function openAuthenticatorSetup(page: import("@playwright/test").Page) {
  await page.route("**/api/auth/two-factor/enable", route => route.request().method() === "OPTIONS"
    ? route.fulfill({ status: 204, headers: cors })
    : route.fulfill({
      status: 200,
      headers: cors,
      contentType: "application/json",
      body: JSON.stringify({ method: "totp", totpURI: totpUri, backupCodes }),
    }));
  await page.goto("/totp-setup");
  await page.getByLabel("Confirm your password").fill("Strong-password-123");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Secure your account" })).toBeVisible();
  await expect(page.getByLabel("Authenticator QR code")).toBeVisible();
}

export async function mockTwoFactorSignIn(page: import("@playwright/test").Page) {
  await page.route("**/api/auth/sign-in/email", route => route.request().method() === "OPTIONS"
    ? route.fulfill({ status: 204, headers: cors })
    : route.fulfill({
      status: 200,
      headers: cors,
      contentType: "application/json",
      body: JSON.stringify({ twoFactorRedirect: true }),
    }));
  await page.route("**/api/auth/two-factor/verify-totp", route => route.request().method() === "OPTIONS"
    ? route.fulfill({ status: 204, headers: cors })
    : route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ status: true }) }));
}

export async function submitTwoFactorSignIn(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByLabel("Email address").fill("mfa@vrub.example");
  await page.getByRole("textbox", { name: "Password" }).fill("Strong-password-123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/two-factor$/);
  await page.getByLabel("Authenticator code").fill("123456");
  await page.getByRole("button", { name: "Verify code" }).click();
}

export type MeFixture = {
  status: number;
  body: Record<string, unknown>;
};

export type UnitFixture = { unit_id: string; code: string; name: string; gstin: null; state_code: null; active: boolean };

export type CountFixtures = {
  approval?: { pending: number; overdue: number };
  notification?: { unread: number } | "error";
  onNotificationRequest?: () => void;
};

export type BootstrapResponse = { status: number; body: unknown; requestId?: string };

export type BootstrapFixtures = {
  settings?: BootstrapResponse[];
};

export const defaultUnits: UnitFixture[] = [
  { unit_id: "11111111-1111-1111-1111-111111111111", code: "VR-01", name: "Plant Alpha", gstin: null, state_code: null, active: true },
];

export async function mockSignInAndMe(
  page: import("@playwright/test").Page,
  me: MeFixture,
  units: UnitFixture[] = defaultUnits,
  counts: CountFixtures = {},
  bootstrap: BootstrapFixtures = {},
) {
  await page.route("**/api/auth/sign-in/email", route => route.request().method() === "OPTIONS"
    ? route.fulfill({ status: 204, headers: cors })
    : route.fulfill({
      status: 200,
      headers: cors,
      contentType: "application/json",
      body: JSON.stringify({
        redirect: false,
        token: "test-session-token",
        user: { id: "u1", email: "a@vrub.example", emailVerified: true, name: "Akshita K." },
      }),
    }));
  await page.route("**/api/v1/me", route => route.fulfill({
    status: me.status,
    headers: cors,
    contentType: "application/json",
    body: JSON.stringify(me.body),
  }));
  let settingsRequest = 0;
  await page.route("**/api/v1/settings", route => {
    const responses = bootstrap.settings ?? [{ status: 200, body: [] }];
    const response = responses[Math.min(settingsRequest, responses.length - 1)];
    settingsRequest += 1;
    return route.fulfill({
      status: response.status,
      headers: { ...cors, ...(response.requestId ? { "X-Request-ID": response.requestId } : {}) },
      contentType: "application/json",
      body: JSON.stringify(response.body),
    });
  });
  await page.route("**/api/v1/org/units**", route => route.fulfill({
    status: 200,
    headers: cors,
    contentType: "application/json",
    body: JSON.stringify({
      items: units.slice(
        Number(new URL(route.request().url()).searchParams.get("offset") ?? 0),
        Number(new URL(route.request().url()).searchParams.get("offset") ?? 0)
          + Number(new URL(route.request().url()).searchParams.get("limit") ?? 100),
      ),
      total: units.length,
      limit: Number(new URL(route.request().url()).searchParams.get("limit") ?? 100),
      offset: Number(new URL(route.request().url()).searchParams.get("offset") ?? 0),
    }),
  }));
  await page.route("**/api/v1/approvals/count", route => route.fulfill({
    status: 200,
    headers: cors,
    contentType: "application/json",
    body: JSON.stringify(counts.approval ?? { pending: 4, overdue: 1 }),
  }));
  await page.route("**/api/v1/notifications/count", route => {
    counts.onNotificationRequest?.();
    return route.fulfill({
      status: counts.notification === "error" ? 503 : 200,
      headers: cors,
      contentType: "application/json",
      body: JSON.stringify(counts.notification === "error" ? { detail: "Notifications are temporarily unavailable" } : counts.notification ?? { unread: 3 }),
    });
  });
  await page.goto("/");
  await page.getByLabel("Email address").fill("a@vrub.example");
  await page.getByRole("textbox", { name: "Password" }).fill("Strong-password-123");
  await page.getByRole("button", { name: "Sign in" }).click();
}

export const grantedProfile = {
  user_id: "u1",
  email: "a@vrub.example",
  display_name: "Akshita K.",
  role: "STORES",
  roles: ["STORES"],
  permissions: ["indent.raise", "grn.post", "inv.issue", "inv.transfer", "inv.count", "challan", "cash.purchase", "kaizen.submit", "leave.apply", "item.manage"],
  modules: ["home", "inventory"],
  all_plants: false,
  unit_ids: ["11111111-1111-1111-1111-111111111111"],
  mfa_required: false,
  two_factor_enabled: false,
  access_granted: true,
};

export const approvalOne = {
  approval_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  document_type: "PURCHASE_ORDER",
  document_id: "11111111-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  title: "PO-2026-0042 · Steel fasteners",
  amount: "21863.00",
  status: "PENDING",
  current_step: 2,
  step_label: "Plant head review",
  step_role: "PLANT_HEAD",
  submitted_by_name: "Ravi Kumar",
  submitted_at: "2026-11-03T09:30:00Z",
  due_at: "2026-11-03T17:30:00Z",
  overdue: true,
};

export const approvalTwo = {
  ...approvalOne,
  approval_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  document_id: "22222222-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  document_type: "PURCHASE_INDENT",
  title: "Indent IND-0098",
  amount: null,
  current_step: 1,
  step_label: "Department approval",
  step_role: "PLANT_HEAD",
  submitted_by_name: "Meera Shah",
  submitted_at: "2026-11-04T07:15:00Z",
  due_at: "2026-11-05T07:15:00Z",
  overdue: false,
};

export async function mockApprovals(page: import("@playwright/test").Page, handler?: (url: URL) => { items: unknown[]; total: number }) {
  await page.route("**/api/v1/approvals?**", route => {
    const url = new URL(route.request().url());
    const result = handler?.(url) ?? { items: [approvalOne, approvalTwo], total: 2 };
    return route.fulfill({
      status: 200,
      headers: cors,
      contentType: "application/json",
      body: JSON.stringify({ items: result.items, total: result.total, limit: Number(url.searchParams.get("limit")), offset: Number(url.searchParams.get("offset")) }),
    });
  });
}

export const approvalDetail = {
  ...approvalOne,
  rule: "Purchase order · Plant head then finance",
  steps: [
    { seq: 1, label: "Department approval", role: "DEPARTMENT_HEAD", state: "APPROVED", decided_by: "Meera Shah", decision: "APPROVE", decided_at: "2026-11-03T10:00:00Z", note: "Budget confirmed" },
    { seq: 2, label: "Plant head review", role: "PLANT_HEAD", state: "CURRENT", decided_by: null, decision: null, decided_at: null, note: null },
    { seq: 3, label: "Finance review", role: "FINANCE_MANAGER", state: "WAITING", decided_by: null, decision: null, decided_at: null, note: null },
  ],
};

export async function mockApprovalDetail(
  page: import("@playwright/test").Page,
  handler?: (request: import("@playwright/test").Request) => Promise<{ status?: number; body: unknown; requestId?: string }> | { status?: number; body: unknown; requestId?: string },
) {
  await page.route(`**/api/v1/approvals/${approvalDetail.approval_id}**`, async route => {
    const response = await handler?.(route.request()) ?? { status: 200, body: approvalDetail };
    return route.fulfill({
      status: response.status ?? 200,
      headers: { ...cors, ...(response.requestId ? { "X-Request-ID": response.requestId } : {}) },
      contentType: "application/json",
      body: JSON.stringify(response.body),
    });
  });
}

export const notificationOne = {
  notification_id: "11111111-aaaa-bbbb-cccc-111111111111",
  template_code: "APPROVAL_PENDING:2",
  subject: "Approval needed: PO-2026-0042",
  body: "Steel fasteners is waiting for your Plant head review.",
  reference_type: "approval",
  reference_id: approvalDetail.approval_id,
  created_at: "2026-11-04T08:30:00Z",
  read_at: null,
};

export const notificationTwo = {
  notification_id: "22222222-aaaa-bbbb-cccc-222222222222",
  template_code: "SHIFT_NO_ENTRY:A",
  subject: "Shift entry missing",
  body: "Press 7 has no production entry for shift A.",
  reference_type: "shift",
  reference_id: "33333333-aaaa-bbbb-cccc-333333333333",
  created_at: "2026-11-04T07:30:00Z",
  read_at: "2026-11-04T08:00:00Z",
};

export async function mockNotifications(
  page: import("@playwright/test").Page,
  handler?: (url: URL) => unknown[],
) {
  await page.route("**/api/v1/notifications?**", route => {
    const url = new URL(route.request().url());
    const items = handler?.(url) ?? (url.searchParams.get("unread") === "true" ? [notificationOne] : [notificationOne, notificationTwo]);
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(items) });
  });
}

export const publicApprovalToken = "public-approval-secret-token-92c431";

export const publicApproval = {
  title: "PO-2026-0042 · Steel fasteners",
  document_type: "PURCHASE_ORDER",
  amount: "21863.00",
  step_label: "Plant head review",
  requested_by: "Ravi Kumar",
  expires_at: "2026-11-04T18:30:00Z",
};

export async function mockPublicApproval(
  page: import("@playwright/test").Page,
  getResponse: { status: number; body: unknown },
  decideResponse: { status: number; body: unknown } = { status: 200, body: { status: "APPROVED" } },
) {
  const requests: Array<{ method: string; body: string | null }> = [];
  await page.route(`**/api/v1/public/approval-links/${publicApprovalToken}`, route => {
    requests.push({ method: route.request().method(), body: route.request().postData() });
    return route.fulfill({
      status: getResponse.status,
      headers: cors,
      contentType: "application/json",
      body: JSON.stringify(getResponse.body),
    });
  });
  await page.route(`**/api/v1/public/approval-links/${publicApprovalToken}/decide`, route => {
    requests.push({ method: route.request().method(), body: route.request().postData() });
    return route.fulfill({
      status: decideResponse.status,
      headers: cors,
      contentType: "application/json",
      body: JSON.stringify(decideResponse.body),
    });
  });
  return requests;
}

export const plantAlpha = {
  unit_id: "11111111-1111-1111-1111-111111111111",
  code: "VR-01",
  name: "V-Rub Plant Alpha",
  gstin: "27AAAAA0000A1Z5",
  state_code: "27",
  active: true,
};

export const plantBeta = {
  unit_id: "22222222-2222-2222-2222-222222222222",
  code: "VR-02",
  name: "V-Rub Plant Beta",
  gstin: null,
  state_code: "06",
  active: false,
};

export const departmentProduction = {
  department_id: "33333333-3333-3333-3333-333333333333",
  code: "PROD",
  name: "Production",
  unit_id: plantAlpha.unit_id,
  parent_department_id: null,
  head_role_code: "PLANT_HEAD",
  active: true,
};

export const departmentFinishing = {
  department_id: "44444444-4444-4444-4444-444444444444",
  code: "FINISH",
  name: "Finishing",
  unit_id: plantAlpha.unit_id,
  parent_department_id: departmentProduction.department_id,
  head_role_code: "PROD",
  active: false,
};

export async function mockDepartments(page: import("@playwright/test").Page, items = [departmentProduction, departmentFinishing]) {
  const requests: string[] = [];
  await page.route("**/api/v1/org/departments?**", route => {
    requests.push(route.request().url());
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items, total: items.length, limit: 200, offset: 0 }) });
  });
  for (const item of items) {
    await page.route(`**/api/v1/org/departments/${item.department_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(item) }));
  }
  return requests;
}

export const storeRaw = {
  store_id: "66666666-6666-6666-6666-666666666666",
  unit_id: plantAlpha.unit_id,
  code: "RM-01",
  name: "Raw material main store",
  store_type: "RAW_MATERIAL",
  active: true,
};

export const storeFinished = {
  store_id: "77777777-7777-7777-7777-777777777777",
  unit_id: plantAlpha.unit_id,
  code: "FG-01",
  name: "Finished goods store",
  store_type: "FINISHED_GOODS",
  active: false,
};

export async function mockStores(page: import("@playwright/test").Page, items = [storeRaw, storeFinished]) {
  const requests: string[] = [];
  await page.route("**/api/v1/org/stores?**", route => {
    requests.push(route.request().url());
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items, total: items.length, limit: 200, offset: 0 }) });
  });
  for (const item of items) {
    await page.route(`**/api/v1/org/stores/${item.store_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(item) }));
  }
  return requests;
}

export const paymentTermNet30 = {
  payment_term_id: "99999999-9999-9999-9999-999999999999",
  code: "NET30",
  name: "Net 30 days",
  due_days: 30,
};

export const taxCodeGst18 = {
  tax_code_id: "aaaaaaaa-1111-1111-1111-111111111111",
  code: "GST18",
  description: "GST at eighteen percent",
  gst_rate: "18.0000",
  active: true,
};

export const categoryRubber = {
  category_id: "bbbbbbbb-1111-1111-1111-111111111111",
  code: "RUBBER",
  name: "Rubber compounds",
  parent_category_id: null,
};

export const categoryNatural = {
  category_id: "cccccccc-1111-1111-1111-111111111111",
  code: "NATURAL",
  name: "Natural rubber",
  parent_category_id: categoryRubber.category_id,
};

export const uomKilogram = {
  uom_id: "dddddddd-1111-1111-1111-111111111111",
  code: "KG",
  name: "Kilogram",
  decimal_places: 3,
};

export async function mockReferenceData(page: import("@playwright/test").Page, data: {
  paymentTerms?: typeof paymentTermNet30[];
  taxCodes?: typeof taxCodeGst18[];
  categories?: typeof categoryRubber[];
  uom?: typeof uomKilogram[];
} = {}) {
  const paymentTerms = data.paymentTerms ?? [paymentTermNet30];
  const taxCodes = data.taxCodes ?? [taxCodeGst18];
  const categories = data.categories ?? [categoryRubber, categoryNatural];
  const uom = data.uom ?? [uomKilogram];
  const requests: string[] = [];
  const pageBody = <T,>(items: T[]) => JSON.stringify({ items, total: items.length, limit: 200, offset: 0 });
  await page.route("**/api/v1/ref/payment-terms?**", route => {
    if (route.request().method() !== "GET") return route.fallback();
    requests.push(route.request().url());
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: pageBody(paymentTerms) });
  });
  await page.route("**/api/v1/ref/tax-codes?**", route => {
    if (route.request().method() !== "GET") return route.fallback();
    requests.push(route.request().url());
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: pageBody(taxCodes) });
  });
  await page.route("**/api/v1/ref/item-categories?**", route => {
    if (route.request().method() !== "GET") return route.fallback();
    requests.push(route.request().url());
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: pageBody(categories) });
  });
  await page.route("**/api/v1/ref/uom", route => {
    requests.push(route.request().url());
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(uom) });
  });
  return requests;
}

export const vendorOrbit = {
  vendor_id: "eeeeeeee-1111-1111-1111-111111111111",
  code: "ORBIT",
  name: "Orbit Rubber Supplies",
  gstin: "27ABCDE1234F1Z5",
  pan: "ABCDE1234F",
  msme_flag: true,
  payment_term_id: paymentTermNet30.payment_term_id,
  status: "PENDING_APPROVAL",
};

export async function mockVendors(page: import("@playwright/test").Page, vendors = [vendorOrbit]) {
  const requests: string[] = [];
  await page.route("**/api/v1/parties/vendors?**", route => {
    requests.push(route.request().url());
    const url = new URL(route.request().url());
    const q = (url.searchParams.get("q") ?? "").toLowerCase();
    const status = url.searchParams.get("vendor_status");
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 20);
    const filtered = vendors.filter(vendor => (!q || `${vendor.code} ${vendor.name}`.toLowerCase().includes(q)) && (!status || vendor.status === status));
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: filtered.slice(offset, offset + limit), total: filtered.length, limit, offset }) });
  });
  await page.route("**/api/v1/ref/payment-terms?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [paymentTermNet30], total: 1, limit: 200, offset: 0 }) }));
  return requests;
}

export async function mockCustomerDetail(page: import("@playwright/test").Page, customer = customerAcme) {
  await page.route(`**/api/v1/parties/customers/${customer.customer_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(customer) }));
  await page.route("**/api/v1/ref/payment-terms?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [paymentTermNet30], total: 1, limit: 200, offset: 0 }) }));
}

export const customerAcme = {
  customer_id: "dddddddd-1111-1111-1111-111111111111",
  code: "ACME",
  name: "Acme Suspension Components",
  gstin: "27AAAAA0000A1Z5",
  payment_term_id: paymentTermNet30.payment_term_id,
  credit_limit: "250000.00",
  active: true,
};

export async function mockCustomers(page: import("@playwright/test").Page, customers = [customerAcme]) {
  const requests: string[] = [];
  await page.route("**/api/v1/parties/customers?**", route => {
    requests.push(route.request().url());
    const url = new URL(route.request().url());
    const q = (url.searchParams.get("q") ?? "").toLowerCase();
    const active = url.searchParams.get("active");
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 20);
    const filtered = customers.filter(customer => (!q || `${customer.code} ${customer.name}`.toLowerCase().includes(q)) && (!active || String(customer.active) === active));
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: filtered.slice(offset, offset + limit), total: filtered.length, limit, offset }) });
  });
  await page.route("**/api/v1/ref/payment-terms?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [paymentTermNet30], total: 1, limit: 200, offset: 0 }) }));
  return requests;
}

export async function mockVendorDetail(page: import("@playwright/test").Page, vendor = vendorOrbit) {
  await page.route(`**/api/v1/parties/vendors/${vendor.vendor_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(vendor) }));
  await page.route("**/api/v1/ref/payment-terms?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [paymentTermNet30], total: 1, limit: 200, offset: 0 }) }));
}

export const itemRubber = {
  item_id: "ffffffff-1111-1111-1111-111111111111",
  code: "RM-RUBBER-01",
  name: "Natural rubber compound",
  item_type: "RM",
  uom_code: "KG",
  category_id: categoryRubber.category_id,
  hsn_code: "40059990",
  shelf_life_days: 365,
  route_code: null,
  active: true,
};

type FixtureItem = Omit<typeof itemRubber, "route_code"> & { route_code: string | null };
export async function mockItems(page: import("@playwright/test").Page, items: FixtureItem[] = [itemRubber]) {
  const requests: string[] = [];
  await page.route("**/api/v1/product/items?**", route => {
    const url = new URL(route.request().url());
    requests.push(url.toString());
    const q = (url.searchParams.get("q") ?? "").toLowerCase();
    const type = url.searchParams.get("item_type");
    const category = url.searchParams.get("category_id");
    const active = url.searchParams.get("active");
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 20);
    const filtered = items.filter(item => (!q || `${item.code} ${item.name}`.toLowerCase().includes(q)) && (!type || item.item_type === type) && (!category || item.category_id === category) && (!active || String(item.active) === active));
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: filtered.slice(offset, offset + limit), total: filtered.length, limit, offset }) });
  });
  await mockReferenceData(page);
  return requests;
}

export const itemFinished = { ...itemRubber, item_id: "ffffffff-2222-2222-2222-222222222222", code: "FG-MOUNT-01", name: "Finished engine mount", item_type: "FG", route_code: "ASSY-01" };

export const itemPolicy = { unit_id: plantAlpha.unit_id, unit_code: plantAlpha.code, reorder_point: "125.500", reorder_qty: "500.000", default_store_id: "66666666-6666-6666-6666-666666666666" };

export const itemSpec = { name: "Hardness", unit: "Shore A", target_value: "65.000000", min_value: "60.000000", max_value: "70.000000", text_value: null, note: "Cured compound" };

export const itemBom = { bom_id: "aaaaaaaa-4444-4444-4444-444444444444", item_id: itemFinished.item_id, item_code: itemFinished.code, item_name: itemFinished.name, version: 2, status: "APPROVED", valid_from: "2026-11-01", valid_to: null, approval_id: null, lines: [{ line_no: 1, component_item_id: itemRubber.item_id, component_code: itemRubber.code, component_name: itemRubber.name, uom_code: "KG", quantity_per: "2.500000", scrap_factor: "0.010000" }] };

export const itemChange = { version: 2, from_version: 1, bom_id: itemBom.bom_id, status: "APPROVED", reason: "Updated rubber formulation", effective_from: "2026-11-01", requested_by: "Akshita K.", requested_at: "2026-10-28T09:00:00Z", approval_id: null, approval_status: "APPROVED" };

export const itemPart = { xref_id: "bbbbbbbb-4444-4444-4444-444444444444", customer_id: "dddddddd-1111-1111-1111-111111111111", customer_code: "ACME", customer_part_no: "OEM-MOUNT-42" };

export async function mockItemDetail(page: import("@playwright/test").Page, item = itemFinished) {
  await mockItems(page, [item]);
  const base = `**/api/v1/product/items/${item.item_id}`;
  const json = (value: unknown) => ({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(value) });
  await page.route(base, route => route.fulfill(json(item)));
  await page.route(`${base}/policies`, route => route.fulfill(json([itemPolicy])));
  await page.route(`${base}/specs`, route => route.fulfill(json([itemSpec])));
  await page.route(`${base}/customer-parts`, route => route.fulfill(json([itemPart])));
  await page.route(`${base}/effective-bom`, route => route.fulfill(json(itemBom)));
  await page.route(`${base}/engineering-changes`, route => route.fulfill(json([itemChange])));
  await page.route(`${base}/where-used`, route => route.fulfill(json([itemBom])));
  await page.route("**/api/v1/org/stores?**", route => route.fulfill(json({ items: [{ store_id: itemPolicy.default_store_id, unit_id: plantAlpha.unit_id, code: "RM-01", name: "Raw material store", store_type: "RAW_MATERIAL", active: true }], total: 1, limit: 200, offset: 0 })));
  await page.route("**/api/v1/parties/customers?**", route => route.fulfill(json({ items: [{ customer_id: itemPart.customer_id, code: "ACME", name: "ACME Components", gstin: null, payment_term_id: null, credit_limit: null, active: true }], total: 1, limit: 200, offset: 0 })));
}

type FixtureBom = Omit<typeof itemBom, "valid_to"> & { valid_to: string | null };
export async function mockBoms(page: import("@playwright/test").Page, boms: FixtureBom[] = [itemBom]) {
  const requests: string[] = [];
  await mockItems(page, [itemFinished, itemRubber]);
  await page.route("**/api/v1/product/boms?**", route => {
    const url = new URL(route.request().url());
    requests.push(url.toString());
    const itemId = url.searchParams.get("item_id");
    const status = url.searchParams.get("status");
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 20);
    const filtered = boms.filter(bom => (!itemId || bom.item_id === itemId) && (!status || bom.status === status));
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: filtered.slice(offset, offset + limit), total: filtered.length, limit, offset }) });
  });
  return requests;
}

export const bomDraft = { ...itemBom, bom_id: "aaaaaaaa-5555-5555-5555-555555555555", version: 3, status: "DRAFT", valid_from: "2026-11-04", lines: [{ ...itemBom.lines[0] }] };

export const bomPrevious = { ...itemBom, bom_id: "aaaaaaaa-3333-3333-3333-333333333333", version: 1, status: "SUPERSEDED", valid_from: "2026-10-01", valid_to: "2026-10-31" };

export async function mockBomDetail(page: import("@playwright/test").Page, current = itemBom, history = [itemBom, bomPrevious]) {
  await mockBoms(page, history);
  await page.route(`**/api/v1/product/boms/${current.bom_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(current) }));
}

export const routeMouldV2 = { route_id: "aaaaaaaa-6666-6666-6666-666666666666", code: "MOULD", name: "Moulding and finishing", version: 2, active: true, stages: [{ sequence_no: 1, stage_code: "MIX", stage_name: "Mix compound", is_qa_gate: false, is_end: false }, { sequence_no: 2, stage_code: "QA", stage_name: "Quality check", is_qa_gate: true, is_end: false }, { sequence_no: 3, stage_code: "FG_STORE", stage_name: "Finished goods store", is_qa_gate: false, is_end: true }] };

export const routeMouldV1 = { ...routeMouldV2, route_id: "aaaaaaaa-7777-7777-7777-777777777777", version: 1, active: false };

export const routeMetal = { ...routeMouldV2, route_id: "aaaaaaaa-8888-8888-8888-888888888888", code: "METAL", name: "Metal fabrication", version: 1 };

export async function mockRoutes(page: import("@playwright/test").Page, routes = [routeMouldV2, routeMouldV1, routeMetal]) {
  const requests: string[] = [];
  await page.route("**/api/v1/product/routes?**", route => {
    const url = new URL(route.request().url());
    requests.push(url.toString());
    const active = url.searchParams.get("active") !== "false";
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(routes.filter(candidate => candidate.active === active)) });
  });
  await page.route("**/api/v1/product/routes/by-code/**", route => {
    const code = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop() ?? "").toUpperCase();
    const match = routes.filter(candidate => candidate.active && candidate.code === code).sort((a, b) => b.version - a.version)[0];
    return route.fulfill({ status: match ? 200 : 404, headers: cors, contentType: "application/json", body: JSON.stringify(match ?? { detail: "Active route not found" }) });
  });
  return requests;
}

export const indentProfile = { ...grantedProfile, modules: [...grantedProfile.modules, "procurement"] };

export const indentAlpha = {
  indent_id: "aaaaaaaa-1111-1111-1111-111111111111", indent_no: "IND-001", unit_id: defaultUnits[0].unit_id,
  unit_code: "VR-01", department_id: "aaaaaaaa-2222-2222-2222-222222222222",
  requester_id: "aaaaaaaa-3333-3333-3333-333333333333", requester_name: "Asha Patel",
  indent_type: "BOM", status: "DRAFT", needed_by: "2026-12-10", created_at: "2026-11-04T10:00:00Z", approval_id: null,
};

export const indentBeta = {
  ...indentAlpha, indent_id: "bbbbbbbb-1111-1111-1111-111111111111", indent_no: "IND-002", department_id: null,
  requester_id: "bbbbbbbb-3333-3333-3333-333333333333", requester_name: "Ravi Shah",
  indent_type: "CAPITAL", status: "APPROVED", needed_by: null, created_at: "2026-11-03T10:00:00Z",
};

export async function mockIndentRegister(page: import("@playwright/test").Page, items: typeof indentAlpha[], onQuery?: (params: URLSearchParams) => void) {
  await page.route("**/api/v1/org/departments?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/procurement/indents?**", route => {
    const params = new URL(route.request().url()).searchParams;
    onQuery?.(params);
    const found = items.filter(item => (!params.has("status") || item.status === params.get("status"))
      && (!params.has("indent_type") || item.indent_type === params.get("indent_type"))
      && (!params.has("unit_id") || item.unit_id === params.get("unit_id"))
      && (!params.has("mine") || item.requester_id === grantedProfile.user_id));
    const offset = Number(params.get("offset") ?? 0);
    const limit = Number(params.get("limit") ?? 20);
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: found.slice(offset, offset + limit), total: found.length, limit, offset }) });
  });
}

export const indentDetailFixture = { ...indentAlpha, lines: [{ line_no: 1, item_id: "cccccccc-1111-1111-1111-111111111111", item_code: "RM-01", item_name: "Rubber", uom_code: "KG", requested_qty: "12.500", purpose: "Press run", capital_description: null, capital_value: null }], capital_total: null };

export const indentPutCors = { ...cors, "access-control-allow-methods": "GET,POST,PUT,OPTIONS" };

export async function mockOwnedIndent(page: import("@playwright/test").Page, indent: typeof indentDetailFixture, mine = true) {
  await page.route("**/api/v1/procurement/indents?**", route => {
    const params = new URL(route.request().url()).searchParams;
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: params.get("mine") === "true" && mine ? [indent] : [], total: params.get("mine") === "true" && mine ? 1 : 0, limit: Number(params.get("limit")), offset: Number(params.get("offset")) }) });
  });
  await page.route(`**/api/v1/procurement/indents/${indent.indent_id}`, route => route.request().method() === "GET"
    ? route.fulfill({ status: 200, headers: indentPutCors, contentType: "application/json", body: JSON.stringify(indent) }) : route.fallback());
}

export const rfqProfile = { ...indentProfile, permissions: [...indentProfile.permissions, "po.create"] };

export const rfqAlpha = { rfq_id: "ffffffff-1111-1111-1111-111111111111", rfq_no: "RFQ-001", unit_id: defaultUnits[0].unit_id, unit_code: "VR-01", status: "SENT", response_due_at: "2020-01-01T12:00:00Z", created_by_name: "Asha Patel", created_at: "2020-01-01T08:00:00Z" };

export const rfqBeta = { ...rfqAlpha, rfq_id: "ffffffff-2222-2222-2222-222222222222", rfq_no: "RFQ-002", status: "DRAFT", response_due_at: "2099-01-05T12:00:00Z" };

export const rfqLineFixture = { rfq_line_id: "eeeeeeee-3333-3333-3333-333333333333", line_no: 1, item_id: "cccccccc-1111-1111-1111-111111111111", item_code: "RM-01", item_name: "Rubber", uom_code: "KG", quantity: "12.500", required_by: null };

export const rfqInvitations = [
  { vendor_id: "eeeeeeee-1111-1111-1111-111111111111", vendor_code: "ORBIT", vendor_name: "Orbit", response_status: "INVITED", invited_at: "2020-01-01T08:00:00Z", quotes_received: 0 },
  { vendor_id: "eeeeeeee-2222-2222-2222-222222222222", vendor_code: "METAL", vendor_name: "Metal Co", response_status: "QUOTED", invited_at: "2020-01-01T08:00:00Z", quotes_received: 1 },
];

export const rfqDetailFixture = (item: typeof rfqAlpha, invitations = item.rfq_id === rfqAlpha.rfq_id ? rfqInvitations : []) => ({ ...item, lines: [rfqLineFixture], invitations });

export async function mockRfqRegister(page: import("@playwright/test").Page, items: typeof rfqAlpha[], observe?: (params: URLSearchParams) => void) {
  await page.route("**/api/v1/product/items?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ item_id: rfqLineFixture.item_id, code: rfqLineFixture.item_code, name: rfqLineFixture.item_name, item_type: "RM", uom_code: rfqLineFixture.uom_code, category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true }], total: 1, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/ref/tax-codes?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/ref/payment-terms?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/procurement/rfqs?**", route => {
    const params = new URL(route.request().url()).searchParams;
    observe?.(params);
    const found = items.filter(item => (!params.has("status") || item.status === params.get("status")) && (!params.has("unit_id") || item.unit_id === params.get("unit_id")));
    const offset = Number(params.get("offset") ?? 0), limit = Number(params.get("limit") ?? 20);
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: found.slice(offset, offset + limit), total: found.length, limit, offset }) });
  });
  await page.route("**/api/v1/procurement/rfqs/**", route => {
    const segments = new URL(route.request().url()).pathname.split("/");
    const isComparison = segments.at(-1) === "comparison";
    const id = segments.at(isComparison ? -2 : -1);
    const item = items.find(value => value.rfq_id === id) ?? rfqAlpha;
    if (isComparison) return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({
      rfq_id: item.rfq_id, rfq_no: item.rfq_no,
      lines: [{ rfq_line_id: "eeeeeeee-3333-3333-3333-333333333333", line_no: 1, item_code: "RM-01", quantity: "12.500", offers: [] }],
      vendors: [], cheapest_vendor_id: null,
    }) });
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(rfqDetailFixture(item)) });
  });
}

export const poAlpha = { po_id: "99999999-1111-1111-1111-111111111111", po_no: "PO-0042", unit_id: defaultUnits[0].unit_id, unit_code: "VR-01", vendor_id: rfqInvitations[0].vendor_id, vendor_code: "ORBIT", vendor_name: "Orbit Rubber", status: "PARTIALLY_RECEIVED", currency_code: "INR", payment_term_id: null, rfq_id: null, rfq_no: null, created_by_name: "Asha Patel", created_at: "2026-11-01T10:00:00Z", approval_id: "77777777-1111-1111-1111-111111111111" };

export const poBeta = { ...poAlpha, po_id: "99999999-2222-2222-2222-222222222222", po_no: "PO-0043", status: "DRAFT", created_at: "2026-11-02T10:00:00Z", approval_id: null };

export const poLine = { po_line_id: "99999999-3333-3333-3333-333333333333", line_no: 1, item_id: rfqLineFixture.item_id, item_code: "RM-01", item_name: "Rubber", uom_code: "KG", ordered_qty: "12.500", unit_price: "8.25", tax_code_id: null, tax_code: null, gst_rate: null, net: "103.13", gst: "0.00", gross: "103.13", received_qty: "5.000", schedule: [{ due_date: "2026-10-30", scheduled_qty: "12.500", received_qty: "5.000" }] };

type FixturePo = Omit<typeof poAlpha, "approval_id"> & { approval_id: string | null };
export const poDetail = (po: FixturePo) => ({ ...po, net_total: "206.26", gst_total: "0.00", gross_total: "206.26", indents: [], lines: [poLine, { ...poLine, po_line_id: "99999999-4444-4444-4444-444444444444", item_id: "cccccccc-2222-2222-2222-222222222222", line_no: 2, item_code: "RM-02", received_qty: "0.000" }] });

export async function mockPoRegister(page: import("@playwright/test").Page, items = [poAlpha, poBeta], observe?: (params: URLSearchParams) => void) {
  await page.route("**/api/v1/parties/vendors?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ vendor_id: poAlpha.vendor_id, code: poAlpha.vendor_code, name: poAlpha.vendor_name, gstin: null, pan: null, msme_flag: false, payment_term_id: null, status: "APPROVED" }], total: 1, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/procurement/purchase-orders?**", route => {
    const params = new URL(route.request().url()).searchParams;
    observe?.(params);
    const filtered = items.filter(item => (!params.has("status") || item.status === params.get("status")) && (!params.has("vendor_id") || item.vendor_id === params.get("vendor_id")) && (!params.has("unit_id") || item.unit_id === params.get("unit_id")));
    const limit = Number(params.get("limit") ?? 20), offset = Number(params.get("offset") ?? 0);
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: filtered.slice(offset, offset + limit), total: filtered.length, limit, offset }) });
  });
  await page.route("**/api/v1/procurement/purchase-orders/*", route => {
    const id = new URL(route.request().url()).pathname.split("/").at(-1);
    if (id === "late-lines") return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify([{ po_id: poAlpha.po_id, po_no: poAlpha.po_no, vendor_code: poAlpha.vendor_code, vendor_name: poAlpha.vendor_name, item_code: "RM-01", due_date: "2026-10-30", days_late: 2, pending_qty: "7.500" }]) });
    const po = items.find(item => item.po_id === id) ?? poAlpha;
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(poDetail(po)) });
  });
  await page.route("**/api/v1/procurement/purchase-orders/*/history", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ events: [], approval: { status: "APPROVED" } }) }));
}

export async function mockPoEditCatalog(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/product/items?**", route => {
    const type = new URL(route.request().url()).searchParams.get("item_type");
    const items = type === "RM" ? [poLine, { ...poLine, item_id: "cccccccc-2222-2222-2222-222222222222", item_code: "RM-02", item_name: "Steel" }, { ...poLine, item_id: "cccccccc-3333-3333-3333-333333333333", item_code: "RM-03", item_name: "Primer" }].map(line => ({ item_id: line.item_id, code: line.item_code, name: line.item_name, item_type: "RM", uom_code: "KG", category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true })) : [];
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items, total: items.length, limit: 200, offset: 0 }) });
  });
  await page.route("**/api/v1/ref/payment-terms?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ payment_term_id: "99999999-9999-9999-9999-999999999999", code: "NET30", name: "Net 30", due_days: 30 }], total: 1, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/ref/tax-codes?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ tax_code_id: "aaaaaaaa-1111-1111-1111-111111111111", code: "GST18", description: "GST 18%", gst_rate: "18.0000", active: true }], total: 1, limit: 200, offset: 0 }) }));
}

export const inventoryProfile = { ...grantedProfile, modules: [...grantedProfile.modules, "procurement"] };

export const grnAlpha = { grn_id: "88888888-1111-1111-1111-111111111111", grn_no: "GRN-0042", unit_id: defaultUnits[0].unit_id, unit_code: "VR-01", po_id: poAlpha.po_id, po_no: poAlpha.po_no, vendor_code: poAlpha.vendor_code, supplier_dc_no: "DC-778", received_at: "2026-11-02T09:00:00Z", status: "POSTED" };

export const grnBeta = { ...grnAlpha, grn_id: "88888888-2222-2222-2222-222222222222", grn_no: "GRN-0043", unit_id: "22222222-2222-2222-2222-222222222222", unit_code: "VR-02", vendor_code: "METALCO", received_at: "2026-11-03T09:00:00Z" };

export const grnDetail = { ...grnAlpha, lines: [{ grn_line_id: "88888888-3333-3333-3333-333333333333", po_line_id: poLine.po_line_id, item_id: poLine.item_id, item_code: poLine.item_code, store_id: "66666666-6666-6666-6666-666666666666", store_code: "RM-01", lot_id: "88888888-4444-4444-4444-444444444444", lot_no: "GRN-0042/1", lot_status: "QA_HOLD", expiry_at: null, received_qty: "7.500", accepted_qty: "0.000", rejected_qty: "0.000", inspected: false }] };

export async function mockGrnRegister(page: import("@playwright/test").Page, items = [grnAlpha, grnBeta], observe?: (params: URLSearchParams) => void) {
  await page.route("**/api/v1/parties/vendors?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [
    { vendor_id: poAlpha.vendor_id, code: grnAlpha.vendor_code, name: poAlpha.vendor_name, gstin: null, pan: null, msme_flag: false, payment_term_id: null, status: "APPROVED" },
    { vendor_id: "99999999-2222-2222-2222-222222222222", code: grnBeta.vendor_code, name: "Metal Company", gstin: null, pan: null, msme_flag: false, payment_term_id: null, status: "APPROVED" },
  ], total: 2, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/inventory/grns?**", route => {
    const params = new URL(route.request().url()).searchParams;
    observe?.(new URLSearchParams(params));
    const filtered = items.filter(item => (!params.has("status") || item.status === params.get("status")) && (!params.has("po_id") || item.po_id === params.get("po_id")));
    const limit = Number(params.get("limit") ?? 20), offset = Number(params.get("offset") ?? 0);
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: filtered.slice(offset, offset + limit), total: filtered.length, limit, offset }) });
  });
  await page.route("**/api/v1/inventory/grns/*", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(grnDetail) }));
}

export async function mockGrnCreationSources(page: import("@playwright/test").Page) {
  await mockPoRegister(page, [poAlpha]);
  await page.route(`**/api/v1/procurement/purchase-orders/${poAlpha.po_id}/receiving`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify([{ po_line_id: poLine.po_line_id, line_no: 1, item_code: poLine.item_code, ordered_qty: "12.500", received_qty: "5.000", pending_qty: "7.500", max_receivable_qty: "8.125" }]) }));
  await page.route("**/api/v1/org/stores?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ store_id: "66666666-6666-6666-6666-666666666666", unit_id: poAlpha.unit_id, code: "RM-01", name: "Raw material store", store_type: "RAW_MATERIAL", active: true }], total: 1, limit: 200, offset: 0 }) }));
}

export const stockAlpha = { lot_id: "77777777-1111-1111-1111-111111111111", lot_no: "LOT-0042", lot_status: "AVAILABLE", item_id: poLine.item_id, item_code: "RM-01", item_name: "Rubber", uom_code: "KG", store_id: "66666666-6666-6666-6666-666666666666", store_code: "RM-01", unit_id: defaultUnits[0].unit_id, unit_code: "VR-01", received_at: "2026-11-02T09:00:00Z", expiry_at: "2027-05-01T00:00:00Z", expired: false, on_hand: "0.125", reserved: "0.025", available: "0.100" };

export const stockBeta = { ...stockAlpha, lot_id: "77777777-2222-2222-2222-222222222222", lot_no: "LOT-0043", lot_status: "BLOCKED", on_hand: "0.000", reserved: "0.000", available: "0.000", expiry_at: null };

export const stockTrace = {
  lot_id: stockAlpha.lot_id, lot_no: stockAlpha.lot_no, item_code: stockAlpha.item_code, store_code: stockAlpha.store_code,
  status: "AVAILABLE", received_at: stockAlpha.received_at, expiry_at: stockAlpha.expiry_at, on_hand: stockAlpha.on_hand,
  origin: { grn_no: "GRN-0042", supplier_dc_no: "DC-778", po_no: "PO-0042", vendor: "Metalco Components", received_at: stockAlpha.received_at, received_qty: "0.250", accepted_qty: "0.250", rejected_qty: "0.000" },
  moves: [{ movement_type: "GRN_RECEIPT", quantity: "0.250", reference_type: "GRN", reference_id: grnAlpha.grn_id, document_no: "GRN-0042", occurred_at: stockAlpha.received_at }, { movement_type: "ISSUE", quantity: "-0.125", reference_type: "ISSUE", reference_id: "99999999-5555-5555-5555-555555555555", document_no: "ISS-0007", occurred_at: "2026-11-03T11:00:00Z" }],
  links: [{ direction: "to", lot_id: "77777777-3333-3333-3333-333333333333", lot_no: "LOT-0042", store_code: "FG-01", transfer_no: "TR-0008", quantity: "0.050" }],
};

export async function mockStockSources(page: import("@playwright/test").Page, items = [stockAlpha, stockBeta], observe?: (params: URLSearchParams) => void) {
  await page.route("**/api/v1/org/stores?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ store_id: stockAlpha.store_id, code: stockAlpha.store_code, name: "Raw material store", unit_id: stockAlpha.unit_id, store_type: "RAW_MATERIAL", active: true }], total: 1, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/product/items?**", route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: [{ item_id: stockAlpha.item_id, code: stockAlpha.item_code, name: stockAlpha.item_name, item_type: "RM", uom_code: "KG", category_id: null, hsn_code: null, shelf_life_days: null, route_code: null, active: true }], total: 1, limit: 200, offset: 0 }) }));
  await page.route("**/api/v1/inventory/stock?**", route => {
    const params = new URL(route.request().url()).searchParams;
    observe?.(new URLSearchParams(params));
    const filtered = items.filter(item => (!params.has("unit_id") || item.unit_id === params.get("unit_id")) && (!params.has("store_id") || item.store_id === params.get("store_id")) && (!params.has("item_id") || item.item_id === params.get("item_id")) && (!params.has("lot_status") || item.lot_status === params.get("lot_status")) && (params.get("in_stock_only") === "false" || item.on_hand !== "0.000"));
    const limit = Number(params.get("limit") ?? 20), offset = Number(params.get("offset") ?? 0);
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: filtered.slice(offset, offset + limit), total: filtered.length, limit, offset }) });
  });
  await page.route(`**/api/v1/inventory/lots/${stockAlpha.lot_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(stockAlpha) }));
  await page.route(`**/api/v1/inventory/lots/${stockAlpha.lot_id}/trace`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(stockTrace) }));
}

export const issueProfile = inventoryProfile;

export const issuePlan = { store_id: stockAlpha.store_id, rule: "FEFO", can_issue: false, lines: [{ item_id: stockAlpha.item_id, item_code: stockAlpha.item_code, requested: "0.200", picks: [{ lot_id: stockAlpha.lot_id, lot_no: stockAlpha.lot_no, expiry_at: stockAlpha.expiry_at, received_at: stockAlpha.received_at, quantity: "0.100" }], shortfall: "0.100", blocked: [{ lot_id: stockBeta.lot_id, lot_no: "LOT-EXPIRED", expiry_at: "2026-10-01T00:00:00Z", quantity: "0.500", reason: "Expired" }] }] };

export const postedIssue = { issue_id: "99999999-6666-6666-6666-666666666666", issue_no: "ISS-0042", unit_id: stockAlpha.unit_id, store_id: stockAlpha.store_id, store_code: stockAlpha.store_code, purpose: "Production supply", reference: "WO-0042", issued_by_name: "Akshita K.", issued_at: "2026-11-05T10:00:00Z", total_cost: "14.375", lines: [{ item_code: stockAlpha.item_code, lot_no: stockAlpha.lot_no, quantity: "0.200", cost: "14.375" }] };

export const issueSummary = { issue_id: postedIssue.issue_id, issue_no: postedIssue.issue_no, unit_id: postedIssue.unit_id, store_id: postedIssue.store_id, store_code: postedIssue.store_code, purpose: postedIssue.purpose, reference: postedIssue.reference, issued_by_name: postedIssue.issued_by_name, issued_at: postedIssue.issued_at };

export async function mockIssueHistory(page: import("@playwright/test").Page, issues = [issueSummary], observe?: (params: URLSearchParams) => void) {
  await page.route("**/api/v1/inventory/issues?**", route => {
    const params = new URL(route.request().url()).searchParams;
    observe?.(new URLSearchParams(params));
    const filtered = issues.filter(issue => !params.has("store_id") || issue.store_id === params.get("store_id"));
    const limit = Number(params.get("limit") ?? 20), offset = Number(params.get("offset") ?? 0);
    return route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify({ items: filtered.slice(offset, offset + limit), total: filtered.length, limit, offset }) });
  });
  await page.route(`**/api/v1/inventory/issues/${postedIssue.issue_id}`, route => route.fulfill({ status: 200, headers: cors, contentType: "application/json", body: JSON.stringify(postedIssue) }));
}
