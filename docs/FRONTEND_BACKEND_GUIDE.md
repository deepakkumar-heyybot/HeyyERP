# heyyERP backend guide for the frontend team

For V-Rub Suspensions (multi-plant rubber and metal parts ERP). Written 28 Oct 2026. This is the
single document a frontend developer needs to start building screens against the backend.

## 1. Where things stand

| | |
|---|---|
| Backend built | Phases 0 to 9: sign-in security, organization and master data, approvals, product engineering, procurement, inventory, manufacturing, quality, sales and dispatch |
| Not built yet | Payroll and recruitment (BRD phase 2); Phase 12 reports, integrations, AI (see section 11) |
| API size | 261 endpoints under `/api/v1`, all documented live at `<API URL>/api/v1/docs` (Swagger) |
| Database | PostgreSQL, 16 migrations (`0001` to `0016`), row-level security on every tenant table |
| Tests | 360 automated tests, all passing; regression guards fail if an endpoint or permission changes by accident |
| Web app | `apps/web` has only the sign-in, sign-up, password reset and 2FA screens. Everything else is yours to build |

Rule of thumb: **the backend already enforces every rule** (permissions, plant scope, approvals,
stock rules, statuses). The frontend should show and hide things for convenience, never rely on
that for security, and always show the backend's error message (`detail`) when it refuses.

## 2. How the pieces fit

```
Browser (apps/web, port 5173)
   |-- sign in / sign up / 2FA ------> Auth service  (apps/auth, port 3001, Better Auth)
   |-- everything else (with cookie) -> ERP API       (apps/api,  port 8000, FastAPI)
                                            |  asks the auth service "is this session valid?"
                                            v
                                       PostgreSQL (shared development database on Railway)
```

- The **auth service** owns accounts, passwords, sessions, 2FA and the V-Rub organization.
- The **ERP API** owns everything else. It reads the session cookie, asks auth who it is, then
  loads the person's ERP profile (one role, plant scope) from its own tables.
- The browser must send cookies to both: use `credentials: "include"` on every `fetch`. CORS is
  already set for `http://localhost:5173` (`WEB_BASE_URL`).

## 3. Getting started

### 3.1 Branches

- `Akshita-Dev`: the frontend branch. Only `apps/web`, the BRD, the prototype and docs. Build here.
- `dev`: the backend (and everything). Clone it separately to **run** the backend; do not copy
  backend code into the frontend branch.
- Open pull requests into `dev` when a screen is ready.

### 3.2 Run the backend on your machine

Needs Python 3.12 and Node 22.

```powershell
git clone https://github.com/deepakkumar-heyybot/HeyyERP.git heyyERP-backend
cd heyyERP-backend
git switch dev

# ERP API
cd apps/api
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
# put the .env file here (section 4), then:
uvicorn heyyerp_api.main:app --reload --app-dir src      # http://localhost:8000/api/v1/docs

# Auth service (second terminal)
cd apps/auth
npm install
# put the .env file here (section 4), then:
npm run dev                                              # http://localhost:3001
```

Then, in your frontend checkout (`Akshita-Dev`): `cd apps/web`, `npm install`, copy `.env.example`
to `.env`, `npm run dev` (http://localhost:5173).

## 4. Which files to share, and with whom

**Never put these in git, chat, e-mail or screenshots.** Send them one to one (password manager,
or a private message that you then delete). All `.env*` files are already git-ignored.

| File | Goes to | Contains (names only) | Who has it |
|---|---|---|---|
| `apps/api/.env` | every backend runner | `DATABASE_URL` (the limited `heyyerp_app` login to the shared database), `AUTH_BASE_URL`, `WEB_BASE_URL`, `REQUIRED_ORG_SLUG`, `ORG_TENANT_MAP`, `DATA_ENCRYPTION_KEY`, optional `APP_ENV`, `LOG_LEVEL` | the backend owner (Deepak): file `apps/api/.env.railway.team-api` |
| `apps/auth/.env` | every backend runner | `DATABASE_URL` (owner login, needed for the auth tables), `BETTER_AUTH_SECRET`, `AUTH_BASE_URL`, `WEB_BASE_URL`, `PORT`, `NODE_ENV`, `AUTH_SKIP_EMAIL_VERIFICATION=true`, `V_RUB_ORG_ID`, `V_RUB_ORG_NAME`, `V_RUB_ORG_SLUG`, mail settings (leave empty for now) | the backend owner: file `apps/auth/.env` plus `apps/api/.env.railway.team-auth` for the database line |
| `apps/web/.env` | the frontend developer | `VITE_AUTH_URL=http://localhost:3001`, `VITE_API_URL=http://localhost:8000` | nothing secret; copy from `apps/web/.env.example` |

What to know about them:

- `BETTER_AUTH_SECRET` must be **the same value for everyone** who shares the database. It also
  protects each person's stored 2FA key, so a different secret locks other people's 2FA out.
- `AUTH_SKIP_EMAIL_VERIFICATION=true` is a development switch: new accounts are verified without
  an e-mail (mail and DNS are not set up yet). It is refused when `NODE_ENV=production`.
- `DATA_ENCRYPTION_KEY` encrypts vendor bank account numbers (AES-256). It must be **the same value for everyone** who shares the database. Without it the bank-account endpoints answer 503; nothing else is affected.
- `ORG_TENANT_MAP` maps the organization slug `v-rub-suspensions` to the tenant id. The API
  refuses to start tenant work without it.
- The shared database is a **development** database on Railway. It is not the production one.
  Only the backend owner runs migrations on it.
- The shared database is at migration `0016`, the same as the backend code (updated on 28 Oct 2026),
  so every module in section 8, including sales, works against it. When the backend owner adds a
  migration later, he updates the shared database and tells the team; until then new endpoints that
  need new tables will fail.

## 5. Signing in and knowing who the user is

The web app already does this with the Better Auth client (`apps/web/src/auth-client.ts`):

1. **Sign up**: `authClient.signUp.email({ email, password, name })`. Password: at least 12 characters.
2. **Verify e-mail**: skipped in development (see 4). In production a link is mailed.
3. **Sign in**: `authClient.signIn.email({ email, password })`. If the answer contains
   `twoFactorRedirect`, show the 6-digit code screen: `authClient.twoFactor.verifyTotp({ code })`.
4. **2FA is required for ADMIN, MD and ACCOUNTS only** (BRD). Setup: `authClient.twoFactor.enable`
   (shows a QR code and backup codes), then `verifyTotp`.
5. **An administrator must give the account an ERP profile** (role and plant scope) with
   `POST /api/v1/admin/users`. Until then every ERP endpoint answers 403: the person can sign in
   but sees nothing. Show a friendly "waiting for access" screen.
6. **Ask the API who you are**: `GET /api/v1/me` (credentials included). It returns:

```json
{
  "user_id": "...", "email": "...", "display_name": "...",
  "role": "PROD", "roles": ["PROD"],
  "permissions": ["mfg.plan", "mfg.entry", "route.move", "..."],
  "modules": ["home", "manufacturing", "inventory", "..."],
  "all_plants": false, "unit_ids": ["<plant uuid>"],
  "mfa_required": false, "two_factor_enabled": false,
  "access_granted": true
}
```

Use it to build the menu and to show or hide buttons:

- Show a menu entry only if its module is in `modules` (`home`, `approvals`, `sales`, `procurement`,
  `inventory`, `routes`, `manufacturing`, `quality`, `maintenance`, `finance`, `gst`, `hr`, `kaizen`,
  `admin`, `integrations`, `reports`, `ai`, ...).
- Show a button only if its permission is in `permissions` (examples per module below).
- If `access_granted` is false, the person needs 2FA (`mfa_required` and not `two_factor_enabled`):
  send them to 2FA setup; the API refuses everything else until then.
- `all_plants=false` means the user only sees records of the plants in `unit_ids`. Outside it the
  API answers **404** (not 403), as if the record did not exist.

Sign-in and sign-up are rate-limited by the auth service (10 requests per minute per action);
handle the error message gracefully.

## 6. API conventions

| Topic | Rule |
|---|---|
| Base URL | `http://localhost:8000/api/v1` (set by `VITE_API_URL`) |
| Auth | session cookie, so `credentials: "include"` always |
| Ids | UUID strings |
| Numbers | quantities, prices and amounts are **strings** (`"100.000"`, `"1500.00"`) to keep decimals exact. Parse with a decimal library or `Number()` only for display |
| Dates | `YYYY-MM-DD`; timestamps are ISO 8601 with timezone |
| Lists | `GET` list endpoints return `{ "items": [...], "total": n, "limit": n, "offset": n }`. Query: `limit` (default 50, max 200), `offset`, and filters named in Swagger |
| Documents | numbered `PREFIX-YY-NNNN` where YY is the financial year starting April (for example `PO-26-0007`) |
| Deleting | nothing is deleted; things are deactivated, cancelled or reversed. Show status, do not offer "delete" |
| Unknown fields | request bodies reject extra fields with 422, so send only what the endpoint documents |
| Idempotency | `POST /approvals/{id}/decide` accepts an `Idempotency-Key` header; generate one per click so a double click does not decide twice |

Status codes and what to do:

| Code | Meaning | UI |
|---|---|---|
| 200 / 201 / 204 | done | |
| 401 | not signed in or session expired | go to sign-in |
| 403 | missing permission or module, or 2FA needed, or no ERP profile | show `detail`; hide the action next time |
| 404 | does not exist **or is outside your plant scope** | "not found" |
| 409 | a business rule refused it (wrong status, not enough stock, duplicate, locked month) | show `detail` as a message to the person; it is written for them |
| 422 | invalid input; `detail` is a list of `{loc, msg}` | highlight the fields |
| 503 | the auth service is unreachable | retry banner |

`detail` is a string for 403/404/409 and a list for 422.

**Derived statuses**: a few statuses are computed by the server from time and quantities, never
stored: compound batch (`WEIGHED`, `MIXING`, `MATURING`, `AWAITING_QA`, `READY`, `CONSUMED`,
`EXPIRED`, `REJECTED`, `ON_HOLD`), quotation `EXPIRED`, challan `overdue`, invoice `handoff_status`.
Always read them from the response; do not recompute in the browser.

## 7. Roles and the menu

Twelve roles, one per person. Modules decide the menu, permissions decide the buttons.

| Role | Code | Modules (menu) |
|---|---|---|
| System admin | `ADMIN` | everything |
| Managing Director | `MD` | everything |
| Plant Head | `PLANT_HEAD` | admin, ai, approvals, finance, gst, home, hr, inventory, kaizen, maintenance, manufacturing, procurement, quality, reports, routes, sales |
| Purchase Manager | `PURCHASE` | ai, approvals, home, inventory, kaizen, procurement, reports, routes |
| Stores In-charge | `STORES` | approvals, home, inventory, kaizen, procurement, reports, routes |
| Quality Head | `QA` | approvals, home, inventory, kaizen, manufacturing, procurement, quality, reports, routes |
| Production Manager | `PROD` | ai, approvals, home, inventory, kaizen, maintenance, manufacturing, quality, reports, routes |
| Sales & Dispatch | `SALES` | ai, approvals, home, inventory, kaizen, reports, sales |
| Finance Controller | `ACCOUNTS` | ai, approvals, finance, gst, home, inventory, kaizen, maintenance, procurement, reports, sales |
| HR Manager | `HR` | approvals, home, hr, kaizen, reports |
| Maintenance Engineer | `MAINT` | approvals, home, inventory, kaizen, maintenance, routes |
| Shop-floor Operator | `OPERATOR` | kaizen, manufacturing, routes |

Permission codes used by the screens built so far: `admin`, `customer.manage`, `item.manage`,
`bom.manage`, `route.manage`, `vendor.manage`, `indent.raise`, `po.create`, `grn.post`, `inv.issue`,
`inv.transfer`, `inv.count`, `qa.approve`, `ncr`, `mfg.plan`, `mfg.entry`, `route.move`,
`sales.order`, `sales.quote`, `sales.dispatch`, `challan`, `scrap.sale`, `gst`, `approval.view_all`,
`approval.override`. The full list with descriptions is in `GET /api/v1/admin/roles`.

## 8. The modules, screen by screen

Each module lists what it does, what screens it suggests, and the rules the screen should reflect.
The exact request and response fields are in Swagger (`/api/v1/docs`); section 9 lists every endpoint.

### 8.1 Administration (`admin`)
Users and the role catalog. Screens: Users list, Create user (pick a person who has signed up,
a role, optional department, plant scope), Role access (read only), Audit log, Business settings.
- `POST /admin/users` links a signed-up account to a role. Deactivating a user blocks them on
  their very next request.
- Business settings (`/admin/settings/{key}`): 17 BRD rules, for example `grnTolPct`, `issueRule`
  (FEFO or FIFO), `minQuotes`, `requireIndent`, `creditLimitBlock`, `lockDay`, `prodLockExtraDays`.
  Only ADMIN changes them; every change keeps its history.

### 8.2 Organization and master data
Plants (`/org/units`), departments, stores (`/org/stores`, store types such as RAW_MATERIAL and
FINISHED_GOODS), reference data (units of measure, payment terms, tax codes, item categories),
customers and vendors (`/parties/...`).
- A store holding stock, with a transfer on the way or an open count, cannot be deactivated (409).
- Vendors are onboarded through approval (status moves to APPROVED); only approved vendors can be
  invited to RFQs or ordered from.

### 8.3 Approvals (`approvals`)
One inbox for every document that needs approval: indents, purchase orders, BOM revisions, stock
counts, scrap sales, vendor onboarding. Screens: "Waiting for me", "All pending" (needs
`approval.view_all`), approval detail with its route and history.
- `GET /approvals?scope=mine|all&state=PENDING|APPROVED|REJECTED` and `GET /approvals/count` (badge).
- `POST /approvals/{id}/decide` with `{ "decision": "APPROVE" | "REJECT", "note": "..." }`. A
  rejection needs a note. Nobody decides their own submission. Send an `Idempotency-Key`.
- The route (who approves, in what order, by value band) is data set by the admin; the frontend
  only displays `step_label`, `step_role`, `due_at` and `overdue`.
- Each rule (`/admin/approval-rules`) has the option `skip_requester_role`. True (the default) skips a step
  held by the requester's own role; false makes another person of that role approve it. Supplier-bill
  exceptions (`R-BILL`) use false, so a second Accounts user must approve them.

### 8.4 Product engineering (`routes`, `manufacturing`)
Items (`/product/items`), plant policies, specifications, customer part numbers, bills of
material with revisions, production routes (METAL, MOULD, EXTR).
- A BOM version goes DRAFT, then PENDING_APPROVAL, then APPROVED (effective from a date),
  SUPERSEDED when a newer one takes over. Only a draft is editable; to change an approved one,
  use `POST /boms/{id}/revise`.
- `GET /product/items/{id}/effective-bom?on=YYYY-MM-DD` gives the BOM in force on a date.
- Routes are ordered stages; some are QA gates (`is_qa_gate`), the last is `is_end`.

### 8.5 Procurement (`procurement`)
Purchase indents, RFQs with vendor quotes and a comparison sheet, purchase orders with approval
by value band, receiving.
- Indent: DRAFT, PENDING_APPROVAL, APPROVED, REJECTED, ORDERED, CANCELLED.
- PO: DRAFT, PENDING_APPROVAL, OPEN, PARTIALLY_RECEIVED, RECEIVED, CLOSED, REJECTED, CANCELLED.
  Submitting a PO may be refused (409) by the rules `requireIndent` or `minQuotes`.
- RFQ comparison (`.../comparison`) marks the lowest full-quantity offer.

### 8.6 Inventory and stores (`inventory`)
Goods receipts, QA release of lots, stock, issues, reservations, transfers between stores and
plants, cycle counts, valuation, lot trace.
- **GRN** (`POST /inventory/grns`) creates lots in `QA_HOLD`. QA releases them with
  `POST /inventory/grn-lines/{id}/inspect` (accepted quantity; the rest becomes a purchase return).
- Lot statuses: `QA_HOLD`, `AVAILABLE`, `QUARANTINE`, `BLOCKED`, `CONSUMED`, `EXPIRED`. Only
  `AVAILABLE`, unexpired lots can be issued, transferred, put on a challan or dispatched.
- **Issue** (`POST /inventory/issues`): `POST /issues/plan` first to preview which lots will be
  taken (FEFO or FIFO by the `issueRule` setting, expired lots listed as blocked). Refused as a whole
  when stock is short (409): stock never goes negative.
- **Transfers**: DRAFT, then `dispatch`, then `receive`. Between plants the cost travels with it.
- **Counts**: `POST /inventory/counts` snapshots the store; enter counted quantities
  (`PUT .../lines`), `submit`. No differences posts at once; differences go to approval (Plant Head,
  then Accounts) and post only when approved; a rejected count becomes `RECOUNT`.
- Stock list `GET /inventory/stock` hides empty lots by default (`in_stock_only=false` shows them).
- `GET /inventory/lots/{id}/trace` shows where a lot came from and everything that happened to it.
- Reports: `/inventory/valuation` (FIFO), `/expiring`, `/reorder`, `/reconciliation` (ledger,
  balances and cost layers agree).

### 8.7 Manufacturing (`manufacturing`, `routes`)
Compounds and batches, machines and moulds, work orders, production lots through routes, shop-floor
entries.
- **Compound batch**: weigh (`POST /manufacturing/batches`, raw material issued first-expiry-first),
  `start-mixing`, `mixed`, then QA (`/qa`, hardness must be inside the compound's spec). The status
  is derived (see section 6). Only `READY` batches can be drawn by a lot.
- **Work order**: `PLANNED`, then `release`, `IN_PROGRESS` once a lot starts, then `complete` (or `cancel` before any lot).
  It takes the BOM in force today and the item's route.
- **Production lot** (`POST /manufacturing/lots`): starting it consumes compound (FIFO across ready
  batches), metal inserts and raw material from the BOM, all or nothing (409 says what is short).
  Move it stage by stage (`/move`); leaving a **QA gate needs `qa.approve`**, any other stage
  `route.move`. At `PACKING`, `/pack` books finished goods into a store with a label code.
- **Shop floor**: shift entries (A, B, C) with rejects and downtime by reason, OEE (availability and
  quality), WIP by stage, rework, return of unused material, lot and material reconciliation.
- A month's production postings lock after `lockDay` plus `prodLockExtraDays` (409 "locked").

### 8.8 Quality (`quality`)
QA queue, inspection plans, inspections, holds, NCR with 8D, instruments and calibration.
- `GET /quality/queue`: one list of everything waiting for QA (GRN lots, compound batches, stage lots).
- An inspection plan lists parameters with limits; a failing value cannot be recorded as ACCEPT.
  When an item has a plan, release steps need a passing inspection first (409 tells you).
- **Holds** (`/quality/holds`) freeze a lot: stock lot becomes QUARANTINE, compound batch ON_HOLD,
  production lot HOLD. Release restores it.
- **NCR**: 8D steps D1 to D8 completed in order, then a disposition (use as is, rework, sort, scrap,
  return to supplier), then close. Evidence is a text reference for now (no file upload yet).
- Instruments: calibration status NEVER, FAILED, OVERDUE, DUE_SOON, OK; overdue or failed ones
  cannot be used for an inspection. `GET /quality/calibration/due` is the alert list.

### 8.9 Sales and dispatch (`sales`)
Price lists, quotations, sales orders, schedule import, dispatch, invoices, challans, scrap sales,
inter-unit invoices. The shared database already has the sales tables (migration `0016`).
- **Price list** (`/sales/prices`): one open price per customer and item; a newer price closes the old.
- **Quotation**: DRAFT, SENT, ACCEPTED or REJECTED, then `convert` to a sales order (once). Expiry is
  derived from `valid_until`.
- **Sales order**: status OPEN, PARTIALLY_SHIPPED, SHIPPED, CLOSED, CANCELLED. Lines take the price
  list rate when none is typed. Each line has a delivery schedule that cannot exceed the ordered
  quantity. The response has `warnings` (credit limit); with the rule `creditLimitBlock` on, going
  over the limit is refused (409). `GET /sales/customers/{id}/credit` shows the position.
- **Schedule import** (`/sales/schedule-imports`): send `{customer_id, source_type, text}` (CSV or
  pasted e-mail) or `content_base64` (.xlsx). Columns: PO number, part number, delivery date,
  quantity. The response previews every row as CREATE, UPDATE or ERROR with the reason. Nothing
  changes until `.../apply` (`skip_errors` applies only the valid rows; applied once).
- **Dispatch**: `POST /sales/dispatches/plan` previews FIFO picking and shortfalls; `POST
  /sales/dispatches` picks for real (reserves lots; 409 if short or more than the order still
  needs); `/dispatch` confirms the goods left; `/cancel` releases a draft. `/packing-list` has
  customer part numbers, lots and label codes; `/trace` goes back to compound batches and raw
  material.
- **Invoice** from a dispatched dispatch: `POST /sales/dispatches/{id}/invoice`. IRN and e-way bill
  numbers are recorded by someone with `gst` through `.../gst-reference` (once). They are not
  generated automatically yet.
- **Challans**: returnable, non-returnable, free of cost, job work. Returnable and job-work ones
  need a customer and a due-back date and show `overdue` and `days_overdue` until returned.
- **Scrap sale**: DRAFT, `submit`, approval (Plant Head, then Accounts), then `invoice`.
- **Inter-unit invoice**: for dispatched or received transfers between plants;
  `/sales/inter-unit-invoices/pending` lists transfers waiting for one.

### 8.10 Finance (`finance`)
Every plant has its own chart of accounts and its own books. Nothing posts for a plant until its chart
exists: `POST /finance/accounts/seed-default` (adds only what is missing, so run it again after an
update). Posting roles (BANK, RECEIVABLE, SALES, ...) map the standard accounts.
- **Journals** (`/finance/journals`): DRAFT, POSTED, REVERSED. A posted journal is never edited; reverse
  it. `trial-balance` and `accounts/{id}/ledger` read the books. Debit and credit must balance.
- **Month lock** (`/finance/periods`): postings into a month past its lock date are refused (409,
  "locked"). `periods/close` shuts a month, only an administrator reopens it.
- **Supplier bills** (`/finance/bills`): entering a bill runs the 3-way match (quantity, rate, duplicate).
  Status MATCHED, EXCEPTION (needs `submit` and approval), APPROVED, BOOKED, PARTIALLY_PAID, PAID,
  REJECTED, CANCELLED. `book` posts it. Show `match_status` and the exception note per line.
- **Payments**: `payment-proposals` (what is due, MSME first), `payment-runs` (creates PROPOSED
  payments), `payments/{id}/pay`, `payment-runs/{id}/bank-file` (CSV). Vendor bank account numbers are
  stored encrypted and shown masked (`********1234`).
- **Receivables**: `receipts` (customer money, number RCT), `receipts/{id}/apply` to invoices,
  `.../reverse`; `receivables/ageing`, `receivables/collections`, `customers/{id}/statement`. Every
  invoice shows `paid_amount`, `outstanding` and `payment_status`.
- **Consolidation** (`/finance/consolidation`): plants added together, inter-unit accounts eliminated;
  a non-zero `receivable_payable_difference` means the two plants disagree.
- **Costing**: `costing/standard/{item_id}` sets an item's standard cost; `costing/lots/{id}` is a
  production lot's cost sheet (material, conversion, unit cost, basis ACTUAL or STANDARD).
- **Fixed assets** (`/finance/assets`, `/finance/depreciation-runs`): register, `dispose`, and monthly
  runs per book (COMPANIES_ACT is posted, INCOME_TAX is a schedule). A run is DRAFT until `post`; runs go
  in month order.
Many choices are company settings (section 8.16), not fixed: how finished goods are costed, when cost
of sales is booked, how transfers between plants are booked, depreciation conventions.

### 8.11 GST and TDS (`gst`, `finance`)
- **GST returns** (`/gst/returns`): `POST` with `{unit_id, month, return_type}` builds GSTR1 or GSTR3B
  data from the books (rebuilt each time until `.../file` marks it filed). `POST /gst/gstr2b/reconcile`
  takes what suppliers reported and returns matched, differing, missing-in-books and missing-in-2B lines
  with the credit at risk. Nothing is filed with the portal.
- **TDS** (`/finance/tds`): deduct on a booked bill (section, rate overridable), `cancel`, `deposit`
  with a challan number, and the month's `summary` by section.

### 8.12 Maintenance (`maintenance`)
- **PM plans** (`/maintenance/pm-plans`): by days for machines, by days or shots for moulds. The list is
  the schedule: `pm_status` is OVERDUE, DUE_SOON (window is a company setting) or OK. `.../done`
  records the work and restarts the interval; `.../history` lists it.
- **Mould shots**: `POST /maintenance/moulds/{id}/shots`, or give `mould_id` and `shots` on a shift
  entry. `moulds/{id}/life` shows the life used; `moulds-over-limit` lists worn moulds.
- **Breakdowns** (`/maintenance/breakdowns`): anyone on the shop floor reports one (BD number); one open
  ticket per machine; maintenance issues spares (`.../spares`, SPARE items only) and closes it with cause
  and action. `/maintenance/reliability` gives MTTR and MTBF per machine.

### 8.13 HR (`hr`)
Employees are never deleted. A person's own record, leave and appraisal need only being signed in; HR
screens need the `hr` module.
- **Employees** (`/hr/employees`): plant, department, reporting line (cannot loop), link to the sign-in
  user (`user_id`). `GET /hr/me` is the signed-in person's own record. `team` is everyone below.
- **Leave**: `GET /hr/leave-types`, `GET /hr/leave-balances`, `POST /hr/leaves` (goes straight to the
  department head's approval), `.../cancel` (withdraw a pending one or cancel an approved one that has
  not started). Balances show `accrued_to_date`, `pending_days` and `available_days`. HR can apply on
  someone's behalf and adjust balances. `GET /hr/leave-calendar` shows who is away.
- **Calendar and shifts** (`/hr/holidays`, `/hr/shifts`): kept by HR; holidays can be added one by one or
  in bulk; shifts have start, end and grace minutes.
- **Attendance**: `POST /hr/attendance`, `POST /hr/attendance/import` (device records, safe to repeat),
  `GET /hr/attendance/summary` (present, leave, absent, late and early-leaving days).
- **Appraisals**: HR `open`s a year; the employee scores themselves (`/self`); the manager scores and
  sends it for approval (`/manager`); HR may add its own score (`/hr-score`); a rejected one is reopened
  by HR (`/reopen`).

### 8.14 Kaizen (`kaizen`)
Anyone submits (`POST /kaizen`); it goes to the department head, then the Plant Head. `status` is
SUBMITTED, UNDER_EVALUATION (derived), APPROVED, IMPLEMENTED, REJECTED or CANCELLED. The submitter can
edit or withdraw it until the first step is decided, and `resubmit` a rejected one. Evaluators give a
score and a verified saving (`/evaluate`); approved ones are marked `/implemented` and the real saving is
recorded per month (`/savings`). `reward_amount` appears only when the company has a reward scheme;
`/reward-paid` records the payment. `GET /kaizen/summary/year` is the year's summary.

### 8.15 Attachments (`/attachments`)
Photos (PNG, JPEG, WebP) and PDFs for kaizen and breakdown records. Send JSON with `entity_type`,
`entity_id`, `file_name`, `content_type` and `content_base64`; the size limit is a company setting and the
content must match the type. List with `?entity_type=&entity_id=`; download from
`/attachments/{id}/content`; `.../remove` switches a file off.

### 8.16 Company settings (the admin changes these without a release)
Read all with `GET /api/v1/settings`, change with `PUT /api/v1/admin/settings/{key}`. Screens should read
these instead of assuming values. Recently added keys:

| Key | What it decides |
|---|---|
| `costMethod`, `overheadPct`, `costMachineLabour` | How finished goods are costed (actual or standard, overhead percentage, machine and labour time) |
| `cogsTiming`, `productionPosting` | When cost of sales is booked, and whether production is booked to the accounts |
| `transferScheme`, `interUnit` | How transfers between plants are booked, and whether they get an invoice |
| `scrapGstRate`, `scrapAmountIncludesGst` | GST on scrap sales |
| `depreciationConvention`, `salvageMaxPct`, `depreciationLateBooking`, `itHalfRateDays` | Asset depreciation |
| `pmDueSoonDays` | Days before a maintenance falls due that it shows as "due soon" |
| `leaveYearStartMonth`, `leaveCountSundays`, `leaveHolidaysCount`, `leaveAccrual` | How leave is counted and earned |
| `appraisalMaxScore`, `kaizenMaxScore` | Score scales |
| `kaizenRewardPct`, `kaizenRewardMax` | Kaizen reward scheme (0 means none) |
| `attachmentMaxKb` | Largest attachment |
| `notifyEmail`, `notifyWhatsapp`, `notifyMaxAttempts` | Channels used for alerts and how often a failed message is retried |
| `eInvoice`, `ewbThreshold`, `gspMaxAttempts` | E-invoice mode (OFF, MANUAL, AUTO), the value above which an e-way bill is expected, retries with the GST provider |
| `invoiceCopies` | Copies printed on a tax invoice (ONE or THREE) |
| `approvalByLink`, `approvalLinkHours`, `approvalLinkMaxAmount` | Approving from an e-mail or WhatsApp link |
| `delegationMaxDays` | Longest approval delegation |
| `shiftAlertAfterMinutes` | Wait before the "shift ended without production entry" alert (0 = off) |

### 8.17 Reports (`/reports`)
`GET /reports` lists the reports the signed-in role may run, with their filters (name, type, options) and
columns. `GET /reports/{key}?format=json|csv|xlsx|pdf&limit=` runs one; the filters are query parameters.
JSON for an on-screen table, the other three are downloads (set `Content-Disposition`). Files hold at most
50,000 rows (PDF 5,000); every run is audited. Hindi and English print together in the PDF.

### 8.18 Notifications (`/notifications`, admin: `/admin/jobs`)
`GET /notifications` is the signed-in person's inbox (newest first; `?unread=true`), `GET /notifications/count`
the badge number, `POST /notifications/{id}/read` and `/notifications/read-all`. Poll the count every minute
or so (no push yet). Each item has `subject`, `body`, `reference_type` and `reference_id` (an approval id, a
shift, ...). The administrator's monitor is `GET /admin/jobs` (queue state, failed messages), `POST
/admin/jobs/run` (run the background jobs now) and `POST /admin/notifications/{id}/retry`.

### 8.19 Approving from a link and delegation
- **Link page `/approve/{token}`** (the frontend builds this public page, no sign-in): `GET
  /api/v1/public/approval-links/{token}` shows `title`, `amount`, `step_label`, `requested_by`,
  `expires_at`; `POST .../{token}/decide` with `{decision: "APPROVE"|"REJECT", note}` decides. 404 means an
  unknown link; 410 means used, expired or already decided (show the `detail`). Opening the page must not
  decide anything: always ask the person to press a button. Links exist only when the company switched on
  `approvalByLink`.
- **Delegation** (`/approvals/delegations`): `POST` `{to_user_id, valid_from, valid_to}` hands your role's
  approvals to a colleague (limit: setting `delegationMaxDays`); `GET` lists the ones you gave and received
  (`?all_=true` for everyone, needs `approval.view_all`); `POST /{id}/revoke` ends one now. While in force,
  the stand-in sees those approvals in `GET /approvals?scope=mine` and decides them as usual; the audit
  shows "on behalf of".

### 8.20 E-invoice and e-way bill (sales invoice screen)
`GET /sales/invoices/{id}/einvoice` gives `irn_status` (NONE, QUEUED, GENERATED, FAILED, CANCELLED), the
IRN, acknowledgement, `signed_qr`, the e-way bill, `gsp_error`, `required` (the customer has a GSTIN and the
company uses e-invoices), `ewb_needed` (value is over the company threshold) and `pending_jobs`. Buttons:
`POST .../irn`, `.../irn/cancel` (`{reason: "1".."4", remark}`, within 24 hours, e-way bill first),
`.../ewb` (`{vehicle_no, distance_km, transport_mode}`), `.../ewb/vehicle` (`{vehicle_no, reason, remark}`),
`.../ewb/cancel`. A provider that is down leaves the job queued (status QUEUED, retried by the server); a
rejection shows FAILED with the reason in `gsp_error` (often a missing address, pincode or HSN: the message
lists them). Set the plant and customer address with `PUT /org/units/{id}/address` and `PUT
/parties/customers/{id}/address` (`address_line`, `city`, `pincode`). Admin: `GET /admin/gsp-jobs`, `POST
/admin/gsp-jobs/{id}/retry`. Setting `eInvoice` (OFF, MANUAL, AUTO) decides whether to show these buttons.

### 8.21 Printing the tax invoice
`GET /sales/invoices/{id}/pdf` downloads the invoice (QR code and IRN once the e-invoice exists, amount in
words, copies as per setting `invoiceCopies`). The wording of each plant is edited with `GET/PUT
/org/units/{id}/print-profile` (`legal_name`, `bank_details`, `terms`, `signatory`, `footer`; needs
`org.manage`).

### 8.22 Shift alert
Planners get an in-app notification "Shift A of <date> has no production entry" listing the machines. No
screen needed beyond the notification inbox; the older `GET /manufacturing/shift-entries/missing` still lists
them on demand.

## 9. Every endpoint

Generated from the code (`python apps/api/scripts/endpoint_inventory.py`). "Needs" is the permission
or module the caller must have; "signed in" means any signed-in person with an ERP profile.

### administration

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/admin/approval-rules` | `admin` |
| POST | `/api/v1/admin/approval-rules` | `admin` |
| GET | `/api/v1/admin/approval-rules/{rule_id}` | `admin` |
| PUT | `/api/v1/admin/approval-rules/{rule_id}` | `admin` |
| POST | `/api/v1/admin/approval-rules/{rule_id}/retire` | `admin` |
| GET | `/api/v1/admin/audit-log` | `admin` |
| GET | `/api/v1/admin/roles` | `admin` |
| GET | `/api/v1/admin/users` | `admin` |
| POST | `/api/v1/admin/users` | `admin` |
| GET | `/api/v1/admin/users/{user_id}` | `admin` |
| PATCH | `/api/v1/admin/users/{user_id}` | `admin` |

### approval delegation

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/approvals/delegations` | signed in |
| POST | `/api/v1/approvals/delegations` | signed in |
| POST | `/api/v1/approvals/delegations/{delegation_id}/revoke` | signed in |

### approval links

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/public/approval-links/{token}` | signed in |
| POST | `/api/v1/public/approval-links/{token}/decide` | signed in |

### approvals

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/approvals` | signed in |
| POST | `/api/v1/approvals` | signed in |
| GET | `/api/v1/approvals/count` | signed in |
| POST | `/api/v1/approvals/sla-sweep` | `admin` |
| GET | `/api/v1/approvals/{approval_id}` | signed in |
| POST | `/api/v1/approvals/{approval_id}/decide` | signed in |

### attachments

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/attachments` | `kaizen.submit` |
| POST | `/api/v1/attachments` | `kaizen.submit` |
| GET | `/api/v1/attachments/{attachment_id}/content` | `kaizen.submit` |
| POST | `/api/v1/attachments/{attachment_id}/remove` | `kaizen.submit` |

### business rules

| Method | Path | Needs |
|---|---|---|
| PUT | `/api/v1/admin/settings/{key}` | `admin` |
| GET | `/api/v1/admin/settings/{key}/history` | `admin` |
| GET | `/api/v1/settings` | signed in |
| GET | `/api/v1/settings/{key}` | signed in |

### customers and vendors

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/parties/customers` | signed in |
| POST | `/api/v1/parties/customers` | `customer.manage` |
| GET | `/api/v1/parties/customers/{customer_id}` | signed in |
| PATCH | `/api/v1/parties/customers/{customer_id}` | `customer.manage` |
| GET | `/api/v1/parties/vendors` | signed in |
| POST | `/api/v1/parties/vendors` | `vendor.manage` |
| GET | `/api/v1/parties/vendors/{vendor_id}` | signed in |
| PATCH | `/api/v1/parties/vendors/{vendor_id}` | `vendor.manage` |
| POST | `/api/v1/parties/vendors/{vendor_id}/deactivate` | `vendor.manage` |

### e-invoice and e-way bill

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/admin/gsp-jobs` | `admin` |
| POST | `/api/v1/admin/gsp-jobs/{job_id}/retry` | `admin` |
| PUT | `/api/v1/org/units/{unit_id}/address` | `org.manage` |
| PUT | `/api/v1/parties/customers/{customer_id}/address` | `customer.manage` |
| GET | `/api/v1/sales/invoices/{invoice_id}/einvoice` | module `sales` |
| POST | `/api/v1/sales/invoices/{invoice_id}/ewb` | module `sales` |
| POST | `/api/v1/sales/invoices/{invoice_id}/ewb/cancel` | module `sales` |
| POST | `/api/v1/sales/invoices/{invoice_id}/ewb/vehicle` | module `sales` |
| POST | `/api/v1/sales/invoices/{invoice_id}/irn` | module `sales` |
| POST | `/api/v1/sales/invoices/{invoice_id}/irn/cancel` | module `sales` |

### finance

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/finance/account-roles` | module `finance` |
| PUT | `/api/v1/finance/account-roles` | `fin.voucher` |
| GET | `/api/v1/finance/accounts` | module `finance` |
| POST | `/api/v1/finance/accounts` | `fin.voucher` |
| POST | `/api/v1/finance/accounts/seed-default` | `fin.voucher` |
| GET | `/api/v1/finance/accounts/{account_id}` | module `finance` |
| PATCH | `/api/v1/finance/accounts/{account_id}` | `fin.voucher` |
| GET | `/api/v1/finance/accounts/{account_id}/ledger` | module `finance` |
| GET | `/api/v1/finance/assets` | module `finance` |
| POST | `/api/v1/finance/assets` | `asset.manage` |
| GET | `/api/v1/finance/assets/{asset_id}` | module `finance` |
| PATCH | `/api/v1/finance/assets/{asset_id}` | `asset.manage` |
| POST | `/api/v1/finance/assets/{asset_id}/dispose` | `asset.manage` |
| GET | `/api/v1/finance/bills` | module `finance` |
| POST | `/api/v1/finance/bills` | `fin.bill` |
| GET | `/api/v1/finance/bills/{bill_id}` | module `finance` |
| POST | `/api/v1/finance/bills/{bill_id}/book` | `fin.bill` |
| POST | `/api/v1/finance/bills/{bill_id}/cancel` | `fin.bill` |
| POST | `/api/v1/finance/bills/{bill_id}/submit` | `fin.bill` |
| GET | `/api/v1/finance/consolidation` | module `finance` |
| GET | `/api/v1/finance/costing/lots/{prod_lot_id}` | module `finance` |
| PUT | `/api/v1/finance/costing/standard/{item_id}` | `fin.voucher` |
| GET | `/api/v1/finance/customers/{customer_id}/statement` | module `finance` |
| GET | `/api/v1/finance/depreciation-runs` | module `finance` |
| POST | `/api/v1/finance/depreciation-runs` | `asset.manage` |
| GET | `/api/v1/finance/depreciation-runs/{run_id}` | module `finance` |
| POST | `/api/v1/finance/depreciation-runs/{run_id}/cancel` | `asset.manage` |
| POST | `/api/v1/finance/depreciation-runs/{run_id}/post` | `asset.manage` |
| GET | `/api/v1/finance/journals` | module `finance` |
| POST | `/api/v1/finance/journals` | `fin.voucher` |
| DELETE | `/api/v1/finance/journals/{journal_id}` | `fin.voucher` |
| GET | `/api/v1/finance/journals/{journal_id}` | module `finance` |
| PUT | `/api/v1/finance/journals/{journal_id}` | `fin.voucher` |
| POST | `/api/v1/finance/journals/{journal_id}/post` | `fin.voucher` |
| POST | `/api/v1/finance/journals/{journal_id}/reverse` | `fin.voucher` |
| GET | `/api/v1/finance/payment-proposals` | module `finance` |
| POST | `/api/v1/finance/payment-runs` | `fin.pay` |
| GET | `/api/v1/finance/payment-runs/{run_id}/bank-file` | `fin.pay` |
| GET | `/api/v1/finance/payments` | module `finance` |
| POST | `/api/v1/finance/payments` | `fin.pay` |
| GET | `/api/v1/finance/payments/{payment_id}` | module `finance` |
| POST | `/api/v1/finance/payments/{payment_id}/cancel` | `fin.pay` |
| POST | `/api/v1/finance/payments/{payment_id}/pay` | `fin.pay` |
| GET | `/api/v1/finance/periods` | module `finance` |
| POST | `/api/v1/finance/periods/close` | `fin.voucher` |
| POST | `/api/v1/finance/periods/reopen` | `admin` |
| GET | `/api/v1/finance/receipts` | module `finance` |
| POST | `/api/v1/finance/receipts` | `fin.voucher` |
| GET | `/api/v1/finance/receipts/{payment_id}` | module `finance` |
| POST | `/api/v1/finance/receipts/{payment_id}/apply` | `fin.voucher` |
| POST | `/api/v1/finance/receipts/{payment_id}/reverse` | `fin.voucher` |
| GET | `/api/v1/finance/receivables/ageing` | module `finance` |
| GET | `/api/v1/finance/receivables/collections` | module `finance` |
| GET | `/api/v1/finance/tds` | module `finance` |
| POST | `/api/v1/finance/tds` | `fin.bill` |
| POST | `/api/v1/finance/tds/deposit` | `fin.bill` |
| GET | `/api/v1/finance/tds/summary` | module `finance` |
| POST | `/api/v1/finance/tds/summary/prepare` | `fin.bill` |
| POST | `/api/v1/finance/tds/{entry_id}/cancel` | `fin.bill` |
| GET | `/api/v1/finance/trial-balance` | module `finance` |
| GET | `/api/v1/finance/vendors/{vendor_id}/bank-account` | `fin.pay` |
| PUT | `/api/v1/finance/vendors/{vendor_id}/bank-account` | `fin.pay` |

### gst

| Method | Path | Needs |
|---|---|---|
| POST | `/api/v1/gst/gstr2b/reconcile` | `gst` |
| GET | `/api/v1/gst/returns` | module `gst` |
| POST | `/api/v1/gst/returns` | `gst` |
| GET | `/api/v1/gst/returns/{return_id}` | module `gst` |
| POST | `/api/v1/gst/returns/{return_id}/file` | `gst` |

### health

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/health/live` | signed in |
| GET | `/api/v1/health/ready` | signed in |

### hr

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/hr/appraisals` | `leave.apply` |
| POST | `/api/v1/hr/appraisals/open` | `hr.manage` |
| GET | `/api/v1/hr/appraisals/{appraisal_id}` | `leave.apply` |
| POST | `/api/v1/hr/appraisals/{appraisal_id}/hr-score` | `hr.manage` |
| POST | `/api/v1/hr/appraisals/{appraisal_id}/manager` | `leave.apply` |
| POST | `/api/v1/hr/appraisals/{appraisal_id}/reopen` | `hr.manage` |
| POST | `/api/v1/hr/appraisals/{appraisal_id}/self` | `leave.apply` |
| GET | `/api/v1/hr/attendance` | module `hr` |
| POST | `/api/v1/hr/attendance` | `hr.manage` |
| POST | `/api/v1/hr/attendance/import` | `hr.manage` |
| GET | `/api/v1/hr/attendance/summary` | module `hr` |
| GET | `/api/v1/hr/employees` | module `hr` |
| POST | `/api/v1/hr/employees` | `hr.manage` |
| GET | `/api/v1/hr/employees/{employee_id}` | module `hr` |
| PATCH | `/api/v1/hr/employees/{employee_id}` | `hr.manage` |
| POST | `/api/v1/hr/employees/{employee_id}/exit` | `hr.manage` |
| POST | `/api/v1/hr/employees/{employee_id}/reinstate` | `hr.manage` |
| GET | `/api/v1/hr/employees/{employee_id}/team` | `leave.apply` |
| GET | `/api/v1/hr/holidays` | `leave.apply` |
| POST | `/api/v1/hr/holidays` | `hr.manage` |
| POST | `/api/v1/hr/holidays/bulk` | `hr.manage` |
| PATCH | `/api/v1/hr/holidays/{holiday_id}` | `hr.manage` |
| GET | `/api/v1/hr/leave-balances` | `leave.apply` |
| POST | `/api/v1/hr/leave-balances/adjust` | `hr.manage` |
| GET | `/api/v1/hr/leave-calendar` | module `hr` |
| GET | `/api/v1/hr/leave-types` | `leave.apply` |
| POST | `/api/v1/hr/leave-types` | `hr.manage` |
| POST | `/api/v1/hr/leave-types/seed-default` | `hr.manage` |
| PATCH | `/api/v1/hr/leave-types/{leave_type_id}` | `hr.manage` |
| GET | `/api/v1/hr/leaves` | `leave.apply` |
| POST | `/api/v1/hr/leaves` | `leave.apply` |
| GET | `/api/v1/hr/leaves/{request_id}` | `leave.apply` |
| POST | `/api/v1/hr/leaves/{request_id}/cancel` | `leave.apply` |
| GET | `/api/v1/hr/me` | `leave.apply` |
| GET | `/api/v1/hr/shifts` | `leave.apply` |
| POST | `/api/v1/hr/shifts` | `hr.manage` |
| PATCH | `/api/v1/hr/shifts/{shift_id}` | `hr.manage` |

### inventory

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/inventory/counts` | module `inventory` |
| POST | `/api/v1/inventory/counts` | `inv.count` |
| GET | `/api/v1/inventory/counts/{count_id}` | module `inventory` |
| POST | `/api/v1/inventory/counts/{count_id}/cancel` | `inv.count` |
| PUT | `/api/v1/inventory/counts/{count_id}/lines` | `inv.count` |
| POST | `/api/v1/inventory/counts/{count_id}/recount` | `inv.count` |
| POST | `/api/v1/inventory/counts/{count_id}/submit` | `inv.count` |
| GET | `/api/v1/inventory/expiring` | module `inventory` |
| POST | `/api/v1/inventory/grn-lines/{grn_line_id}/inspect` | `qa.approve` |
| GET | `/api/v1/inventory/grns` | module `inventory` |
| POST | `/api/v1/inventory/grns` | `grn.post` |
| GET | `/api/v1/inventory/grns/{grn_id}` | module `inventory` |
| GET | `/api/v1/inventory/issues` | module `inventory` |
| POST | `/api/v1/inventory/issues` | `inv.issue` |
| POST | `/api/v1/inventory/issues/plan` | `inv.issue` |
| GET | `/api/v1/inventory/issues/{issue_id}` | module `inventory` |
| GET | `/api/v1/inventory/lots/{lot_id}` | module `inventory` |
| POST | `/api/v1/inventory/lots/{lot_id}/quarantine` | `qa.approve` |
| GET | `/api/v1/inventory/lots/{lot_id}/trace` | module `inventory` |
| POST | `/api/v1/inventory/lots/{lot_id}/unquarantine` | `qa.approve` |
| GET | `/api/v1/inventory/reconciliation` | `inv.count` |
| GET | `/api/v1/inventory/reorder` | module `inventory` |
| GET | `/api/v1/inventory/reservations` | module `inventory` |
| POST | `/api/v1/inventory/reservations` | `inv.issue` |
| POST | `/api/v1/inventory/reservations/release` | `inv.issue` |
| DELETE | `/api/v1/inventory/reservations/{reservation_id}` | `inv.issue` |
| GET | `/api/v1/inventory/stock` | module `inventory` |
| GET | `/api/v1/inventory/transfers` | module `inventory` |
| POST | `/api/v1/inventory/transfers` | `inv.transfer` |
| GET | `/api/v1/inventory/transfers/{transfer_id}` | module `inventory` |
| POST | `/api/v1/inventory/transfers/{transfer_id}/cancel` | `inv.transfer` |
| POST | `/api/v1/inventory/transfers/{transfer_id}/dispatch` | `inv.transfer` |
| POST | `/api/v1/inventory/transfers/{transfer_id}/receive` | `inv.transfer` |
| GET | `/api/v1/inventory/valuation` | module `inventory` |

### invoice printing

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/org/units/{unit_id}/print-profile` | `org.manage` |
| PUT | `/api/v1/org/units/{unit_id}/print-profile` | `org.manage` |
| GET | `/api/v1/sales/invoices/{invoice_id}/pdf` | module `sales` |

### kaizen

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/kaizen` | module `kaizen` |
| POST | `/api/v1/kaizen` | `kaizen.submit` |
| GET | `/api/v1/kaizen/summary/year` | module `kaizen` |
| GET | `/api/v1/kaizen/{kaizen_id}` | module `kaizen` |
| PATCH | `/api/v1/kaizen/{kaizen_id}` | `kaizen.submit` |
| POST | `/api/v1/kaizen/{kaizen_id}/cancel` | `kaizen.submit` |
| POST | `/api/v1/kaizen/{kaizen_id}/evaluate` | `kaizen.manage` |
| POST | `/api/v1/kaizen/{kaizen_id}/implemented` | `kaizen.submit` |
| POST | `/api/v1/kaizen/{kaizen_id}/resubmit` | `kaizen.submit` |
| POST | `/api/v1/kaizen/{kaizen_id}/reward-paid` | `kaizen.manage` |
| GET | `/api/v1/kaizen/{kaizen_id}/savings` | module `kaizen` |
| POST | `/api/v1/kaizen/{kaizen_id}/savings` | `kaizen.manage` |

### maintenance

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/maintenance/breakdowns` | module `maintenance` |
| POST | `/api/v1/maintenance/breakdowns` | `mnt.breakdown` |
| GET | `/api/v1/maintenance/breakdowns/{breakdown_id}` | module `maintenance` |
| POST | `/api/v1/maintenance/breakdowns/{breakdown_id}/close` | `mnt` |
| POST | `/api/v1/maintenance/breakdowns/{breakdown_id}/spares` | `mnt` |
| GET | `/api/v1/maintenance/moulds-over-limit` | module `maintenance` |
| GET | `/api/v1/maintenance/moulds/{mould_id}/life` | module `maintenance` |
| POST | `/api/v1/maintenance/moulds/{mould_id}/shots` | `mfg.entry` |
| GET | `/api/v1/maintenance/pm-plans` | module `maintenance` |
| POST | `/api/v1/maintenance/pm-plans` | `mnt` |
| GET | `/api/v1/maintenance/pm-plans/{plan_id}` | module `maintenance` |
| PATCH | `/api/v1/maintenance/pm-plans/{plan_id}` | `mnt` |
| POST | `/api/v1/maintenance/pm-plans/{plan_id}/done` | `mnt` |
| GET | `/api/v1/maintenance/pm-plans/{plan_id}/history` | module `maintenance` |
| GET | `/api/v1/maintenance/reliability` | module `maintenance` |

### manufacturing

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/manufacturing/batches` | module `manufacturing` |
| POST | `/api/v1/manufacturing/batches` | `route.move` |
| GET | `/api/v1/manufacturing/batches/{batch_id}` | module `manufacturing` |
| POST | `/api/v1/manufacturing/batches/{batch_id}/mixed` | `route.move` |
| POST | `/api/v1/manufacturing/batches/{batch_id}/qa` | `qa.approve` |
| POST | `/api/v1/manufacturing/batches/{batch_id}/start-mixing` | `route.move` |
| GET | `/api/v1/manufacturing/batches/{batch_id}/used-in` | module `manufacturing` |
| GET | `/api/v1/manufacturing/compounds` | module `manufacturing` |
| POST | `/api/v1/manufacturing/compounds` | `mfg.plan` |
| GET | `/api/v1/manufacturing/compounds/{compound_id}` | module `manufacturing` |
| PATCH | `/api/v1/manufacturing/compounds/{compound_id}` | `mfg.plan` |
| GET | `/api/v1/manufacturing/compounds/{compound_id}/available` | module `manufacturing` |
| GET | `/api/v1/manufacturing/lots` | module `manufacturing` |
| POST | `/api/v1/manufacturing/lots` | `route.move` |
| GET | `/api/v1/manufacturing/lots/{prod_lot_id}` | module `manufacturing` |
| POST | `/api/v1/manufacturing/lots/{prod_lot_id}/move` | signed in |
| POST | `/api/v1/manufacturing/lots/{prod_lot_id}/pack` | `route.move` |
| POST | `/api/v1/manufacturing/lots/{prod_lot_id}/return-material` | `route.move` |
| POST | `/api/v1/manufacturing/lots/{prod_lot_id}/rework` | `route.move` |
| GET | `/api/v1/manufacturing/lots/{prod_lot_id}/trace` | module `manufacturing` |
| GET | `/api/v1/manufacturing/machines` | module `manufacturing` |
| POST | `/api/v1/manufacturing/machines` | `mfg.plan` |
| GET | `/api/v1/manufacturing/machines/{machine_id}` | module `manufacturing` |
| PATCH | `/api/v1/manufacturing/machines/{machine_id}` | `mfg.plan` |
| GET | `/api/v1/manufacturing/moulds` | module `manufacturing` |
| POST | `/api/v1/manufacturing/moulds` | `mfg.plan` |
| GET | `/api/v1/manufacturing/moulds/{mould_id}` | module `manufacturing` |
| PATCH | `/api/v1/manufacturing/moulds/{mould_id}` | `mfg.plan` |
| GET | `/api/v1/manufacturing/oee` | module `manufacturing` |
| GET | `/api/v1/manufacturing/reconciliation` | module `manufacturing` |
| GET | `/api/v1/manufacturing/shift-entries` | module `manufacturing` |
| POST | `/api/v1/manufacturing/shift-entries` | `mfg.entry` |
| GET | `/api/v1/manufacturing/shift-entries/missing` | module `manufacturing` |
| GET | `/api/v1/manufacturing/wip` | module `manufacturing` |
| GET | `/api/v1/manufacturing/work-orders` | module `manufacturing` |
| POST | `/api/v1/manufacturing/work-orders` | `mfg.plan` |
| GET | `/api/v1/manufacturing/work-orders/{work_order_id}` | module `manufacturing` |
| POST | `/api/v1/manufacturing/work-orders/{work_order_id}/cancel` | `mfg.plan` |
| POST | `/api/v1/manufacturing/work-orders/{work_order_id}/complete` | `mfg.plan` |
| GET | `/api/v1/manufacturing/work-orders/{work_order_id}/material-variance` | module `manufacturing` |
| POST | `/api/v1/manufacturing/work-orders/{work_order_id}/release` | `mfg.plan` |

### notifications

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/admin/jobs` | `admin` |
| POST | `/api/v1/admin/jobs/run` | `admin` |
| POST | `/api/v1/admin/notifications/{notification_id}/retry` | `admin` |
| GET | `/api/v1/notifications` | signed in |
| GET | `/api/v1/notifications/count` | signed in |
| POST | `/api/v1/notifications/read-all` | signed in |
| POST | `/api/v1/notifications/{notification_id}/read` | signed in |

### organization

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/org/departments` | signed in |
| POST | `/api/v1/org/departments` | `org.manage` |
| GET | `/api/v1/org/departments/{department_id}` | signed in |
| PATCH | `/api/v1/org/departments/{department_id}` | `org.manage` |
| GET | `/api/v1/org/stores` | signed in |
| POST | `/api/v1/org/stores` | `org.manage` |
| GET | `/api/v1/org/stores/{store_id}` | signed in |
| PATCH | `/api/v1/org/stores/{store_id}` | `org.manage` |
| GET | `/api/v1/org/units` | signed in |
| POST | `/api/v1/org/units` | `org.manage` |
| GET | `/api/v1/org/units/{unit_id}` | signed in |
| PATCH | `/api/v1/org/units/{unit_id}` | `org.manage` |

### procurement

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/procurement/indents` | signed in |
| POST | `/api/v1/procurement/indents` | `indent.raise` |
| GET | `/api/v1/procurement/indents/{indent_id}` | signed in |
| PUT | `/api/v1/procurement/indents/{indent_id}` | `indent.raise` |
| POST | `/api/v1/procurement/indents/{indent_id}/cancel` | `indent.raise` |
| POST | `/api/v1/procurement/indents/{indent_id}/submit` | `indent.raise` |
| GET | `/api/v1/procurement/purchase-orders` | module `procurement` |
| POST | `/api/v1/procurement/purchase-orders` | `po.create` |
| POST | `/api/v1/procurement/purchase-orders/from-indents` | `po.create` |
| GET | `/api/v1/procurement/purchase-orders/late-lines` | module `procurement` |
| GET | `/api/v1/procurement/purchase-orders/{po_id}` | module `procurement` |
| PUT | `/api/v1/procurement/purchase-orders/{po_id}` | `po.create` |
| POST | `/api/v1/procurement/purchase-orders/{po_id}/cancel` | `po.create` |
| POST | `/api/v1/procurement/purchase-orders/{po_id}/close` | `po.create` |
| GET | `/api/v1/procurement/purchase-orders/{po_id}/history` | module `procurement` |
| GET | `/api/v1/procurement/purchase-orders/{po_id}/receiving` | module `procurement` |
| POST | `/api/v1/procurement/purchase-orders/{po_id}/submit` | `po.create` |
| GET | `/api/v1/procurement/rfqs` | module `procurement` |
| POST | `/api/v1/procurement/rfqs` | `po.create` |
| GET | `/api/v1/procurement/rfqs/{rfq_id}` | module `procurement` |
| PUT | `/api/v1/procurement/rfqs/{rfq_id}` | `po.create` |
| POST | `/api/v1/procurement/rfqs/{rfq_id}/award` | `po.create` |
| POST | `/api/v1/procurement/rfqs/{rfq_id}/cancel` | `po.create` |
| GET | `/api/v1/procurement/rfqs/{rfq_id}/comparison` | module `procurement` |
| POST | `/api/v1/procurement/rfqs/{rfq_id}/quotes` | `po.create` |
| POST | `/api/v1/procurement/rfqs/{rfq_id}/send` | `po.create` |
| POST | `/api/v1/procurement/rfqs/{rfq_id}/vendors` | `po.create` |

### product engineering

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/product/boms` | signed in |
| POST | `/api/v1/product/boms` | `bom.manage` |
| GET | `/api/v1/product/boms/{bom_id}` | signed in |
| PUT | `/api/v1/product/boms/{bom_id}/lines` | `bom.manage` |
| POST | `/api/v1/product/boms/{bom_id}/revise` | `bom.manage` |
| POST | `/api/v1/product/boms/{bom_id}/submit` | `bom.manage` |
| DELETE | `/api/v1/product/customer-parts/{xref_id}` | `customer.manage` |
| GET | `/api/v1/product/items` | signed in |
| POST | `/api/v1/product/items` | `item.manage` |
| GET | `/api/v1/product/items/{item_id}` | signed in |
| PATCH | `/api/v1/product/items/{item_id}` | `item.manage` |
| GET | `/api/v1/product/items/{item_id}/customer-parts` | signed in |
| POST | `/api/v1/product/items/{item_id}/customer-parts` | `customer.manage` |
| GET | `/api/v1/product/items/{item_id}/effective-bom` | signed in |
| GET | `/api/v1/product/items/{item_id}/engineering-changes` | signed in |
| GET | `/api/v1/product/items/{item_id}/policies` | signed in |
| DELETE | `/api/v1/product/items/{item_id}/policies/{unit_id}` | `item.manage` |
| PUT | `/api/v1/product/items/{item_id}/policies/{unit_id}` | `item.manage` |
| GET | `/api/v1/product/items/{item_id}/specs` | signed in |
| DELETE | `/api/v1/product/items/{item_id}/specs/{name}` | `item.manage` |
| PUT | `/api/v1/product/items/{item_id}/specs/{name}` | `item.manage` |
| GET | `/api/v1/product/items/{item_id}/where-used` | signed in |
| GET | `/api/v1/product/routes` | signed in |
| POST | `/api/v1/product/routes` | `route.manage` |
| GET | `/api/v1/product/routes/by-code/{code}` | signed in |
| GET | `/api/v1/product/routes/{route_id}` | signed in |
| POST | `/api/v1/product/routes/{route_id}/retire` | `route.manage` |
| POST | `/api/v1/product/routes/{route_id}/revise` | `route.manage` |

### quality

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/quality/calibration/due` | module `quality` |
| GET | `/api/v1/quality/holds` | module `quality` |
| POST | `/api/v1/quality/holds` | `qa.approve` |
| GET | `/api/v1/quality/holds/{hold_id}` | module `quality` |
| POST | `/api/v1/quality/holds/{hold_id}/release` | `qa.approve` |
| GET | `/api/v1/quality/inspections` | module `quality` |
| POST | `/api/v1/quality/inspections` | `qa.approve` |
| GET | `/api/v1/quality/inspections/{inspection_id}` | module `quality` |
| GET | `/api/v1/quality/instruments` | module `quality` |
| POST | `/api/v1/quality/instruments` | `qa.approve` |
| GET | `/api/v1/quality/instruments/{instrument_id}` | module `quality` |
| PATCH | `/api/v1/quality/instruments/{instrument_id}` | `qa.approve` |
| POST | `/api/v1/quality/instruments/{instrument_id}/calibrations` | `qa.approve` |
| GET | `/api/v1/quality/ncrs` | module `quality` |
| POST | `/api/v1/quality/ncrs` | `ncr` |
| GET | `/api/v1/quality/ncrs/{ncr_id}` | module `quality` |
| POST | `/api/v1/quality/ncrs/{ncr_id}/cancel` | `ncr` |
| POST | `/api/v1/quality/ncrs/{ncr_id}/close` | `ncr` |
| POST | `/api/v1/quality/ncrs/{ncr_id}/disposition` | `ncr` |
| PATCH | `/api/v1/quality/ncrs/{ncr_id}/steps/{step_code}` | `ncr` |
| GET | `/api/v1/quality/plans` | module `quality` |
| POST | `/api/v1/quality/plans` | `qa.approve` |
| GET | `/api/v1/quality/plans/{plan_id}` | module `quality` |
| POST | `/api/v1/quality/plans/{plan_id}/retire` | `qa.approve` |
| GET | `/api/v1/quality/queue` | module `quality` |

### reference data

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/ref/item-categories` | signed in |
| POST | `/api/v1/ref/item-categories` | `refdata.manage` |
| PATCH | `/api/v1/ref/item-categories/{category_id}` | `refdata.manage` |
| GET | `/api/v1/ref/payment-terms` | signed in |
| POST | `/api/v1/ref/payment-terms` | `refdata.manage` |
| PATCH | `/api/v1/ref/payment-terms/{term_id}` | `refdata.manage` |
| GET | `/api/v1/ref/tax-codes` | signed in |
| POST | `/api/v1/ref/tax-codes` | `refdata.manage` |
| PATCH | `/api/v1/ref/tax-codes/{tax_code_id}` | `refdata.manage` |
| GET | `/api/v1/ref/uom` | signed in |

### reports

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/reports` | module `reports` |
| GET | `/api/v1/reports/{key}` | module `reports` |

### sales

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/sales/challans` | module `sales` |
| POST | `/api/v1/sales/challans` | `challan` |
| GET | `/api/v1/sales/challans/{challan_id}` | module `sales` |
| POST | `/api/v1/sales/challans/{challan_id}/return` | `challan` |
| GET | `/api/v1/sales/customers/{customer_id}/credit` | module `sales` |
| GET | `/api/v1/sales/dispatches` | module `sales` |
| POST | `/api/v1/sales/dispatches` | `sales.dispatch` |
| POST | `/api/v1/sales/dispatches/plan` | `sales.dispatch` |
| GET | `/api/v1/sales/dispatches/{dispatch_id}` | module `sales` |
| POST | `/api/v1/sales/dispatches/{dispatch_id}/cancel` | `sales.dispatch` |
| POST | `/api/v1/sales/dispatches/{dispatch_id}/dispatch` | `sales.dispatch` |
| POST | `/api/v1/sales/dispatches/{dispatch_id}/invoice` | `sales.dispatch` |
| GET | `/api/v1/sales/dispatches/{dispatch_id}/packing-list` | module `sales` |
| GET | `/api/v1/sales/dispatches/{dispatch_id}/trace` | module `sales` |
| GET | `/api/v1/sales/inter-unit-invoices` | module `sales` |
| POST | `/api/v1/sales/inter-unit-invoices` | `gst` |
| GET | `/api/v1/sales/inter-unit-invoices/pending` | module `sales` |
| GET | `/api/v1/sales/inter-unit-invoices/{invoice_id}` | module `sales` |
| POST | `/api/v1/sales/inter-unit-invoices/{invoice_id}/cancel` | `gst` |
| POST | `/api/v1/sales/inter-unit-invoices/{invoice_id}/gst-reference` | `gst` |
| GET | `/api/v1/sales/invoices` | module `sales` |
| GET | `/api/v1/sales/invoices/{invoice_id}` | module `sales` |
| POST | `/api/v1/sales/invoices/{invoice_id}/cancel` | `sales.dispatch` |
| POST | `/api/v1/sales/invoices/{invoice_id}/gst-reference` | `gst` |
| GET | `/api/v1/sales/orders` | module `sales` |
| POST | `/api/v1/sales/orders` | `sales.order` |
| GET | `/api/v1/sales/orders/{so_id}` | module `sales` |
| POST | `/api/v1/sales/orders/{so_id}/cancel` | `sales.order` |
| POST | `/api/v1/sales/orders/{so_id}/close` | `sales.order` |
| PUT | `/api/v1/sales/orders/{so_id}/lines/{so_line_id}/schedule` | `sales.order` |
| GET | `/api/v1/sales/prices` | module `sales` |
| POST | `/api/v1/sales/prices` | `sales.quote` |
| GET | `/api/v1/sales/prices/effective` | module `sales` |
| GET | `/api/v1/sales/quotes` | module `sales` |
| POST | `/api/v1/sales/quotes` | `sales.quote` |
| GET | `/api/v1/sales/quotes/{quote_id}` | module `sales` |
| POST | `/api/v1/sales/quotes/{quote_id}/accept` | `sales.quote` |
| POST | `/api/v1/sales/quotes/{quote_id}/convert` | `sales.order` |
| POST | `/api/v1/sales/quotes/{quote_id}/reject` | `sales.quote` |
| POST | `/api/v1/sales/quotes/{quote_id}/send` | `sales.quote` |
| GET | `/api/v1/sales/schedule-imports` | module `sales` |
| POST | `/api/v1/sales/schedule-imports` | `sales.order` |
| GET | `/api/v1/sales/schedule-imports/{import_id}` | module `sales` |
| POST | `/api/v1/sales/schedule-imports/{import_id}/apply` | `sales.order` |
| POST | `/api/v1/sales/schedule-imports/{import_id}/discard` | `sales.order` |
| GET | `/api/v1/sales/scrap-sales` | module `sales` |
| POST | `/api/v1/sales/scrap-sales` | `scrap.sale` |
| GET | `/api/v1/sales/scrap-sales/{scrap_id}` | module `sales` |
| POST | `/api/v1/sales/scrap-sales/{scrap_id}/cancel` | `scrap.sale` |
| POST | `/api/v1/sales/scrap-sales/{scrap_id}/invoice` | `scrap.sale` |
| POST | `/api/v1/sales/scrap-sales/{scrap_id}/submit` | `scrap.sale` |

### session

| Method | Path | Needs |
|---|---|---|
| GET | `/api/v1/me` | signed in |

## 10. Suggested order to build the screens

1. Shell: sign-in flow (already there), `/me`, menu from `modules`, "no access yet" screen, error toast.
2. Approvals inbox (small, used by everything else) and the Admin users screen.
3. Master data lists (plants, stores, items, customers, vendors) with create and edit forms.
4. Procurement: indent, RFQ, PO. Then Inventory: GRN, QA release, stock, issue.
5. Manufacturing: batches, work orders, production lots (the stage board), shop-floor entry.
6. Quality: QA queue, inspections, holds, NCR.
7. Sales: orders, schedule import, dispatch, invoices, challans.
8. Finance: chart and journals, supplier bills and payments, receipts and ageing, GST and TDS.
9. Maintenance and HR (self-service leave first), kaizen, company settings.

For each screen: list endpoint, detail endpoint, the action buttons by status, and the refusal
messages.

## 11. Not built yet (do not build screens for these)

- **Payroll and recruitment**: BRD phase 2.
- **Phase 12, still to come**: mailbox watcher for customer schedules, barcode and label printing, the AI
  copilot and OCR agents, a real-time gateway (use polling for now). E-invoice for inter-unit invoices and
  credit notes (there is no credit note document yet).
- **GST filing**: the GST screens prepare data; nothing is filed with the portal.
- Smaller gaps: sign-in and sign-out audit rows, editable roles, bulk approve, shop-floor PIN or OTP
  sign-in, customer portal, vendor rating, cash purchase, PO amendment after approval.

## 12. Quick checks

- Open `http://localhost:8000/api/v1/docs` (Swagger) to try any endpoint; sign in on the web app
  first so the cookie is set.
- `GET /api/v1/health/ready` should answer `{"status":"ready","database":"connected"}`.
- A 403 on every endpoint after sign-in means the account has no ERP profile yet: ask an admin.
- Tests and regression guards: see `docs/TESTING.md` on the `dev` branch.

Questions about the backend go to the backend owner (Deepak).
