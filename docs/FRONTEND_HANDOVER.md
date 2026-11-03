# heyyERP: hand-over to the frontend (for Akshita)

Written 3 Nov 2026 by the backend side (Deepak). Read this first; it tells you what is finished in the
backend, what is new since the last guide, and the order in which to build the screens. The detailed
reference is `docs/FRONTEND_BACKEND_GUIDE.md` (section numbers below point into it) and the live API
documentation is Swagger at `http://localhost:8000/api/v1/docs`.

## 1. In one paragraph

The backend is about 90% of the BRD. Everything a plant does every day (buying, stores, production,
quality, selling, dispatching, invoicing, accounts, maintenance, HR, kaizen, approvals) works and is
tested (423 endpoints, 514 tests). The web app has only the sign-in screens, so **all the work screens are
still to be built**. You never need to re-implement a rule: the backend refuses anything not allowed and
tells you why in plain words (`detail`). Your job is to show data, collect input, show those messages.

## 2. What is finished in the backend

| Module (menu name) | API prefix | Backend | Main screens to build |
|---|---|---|---|
| Sign-in, who am I | `/me` | Done | menu, buttons by permission, "waiting for access" |
| Organization and master data | `/org`, `/parties`, `/ref`, `/product` | Done | plants, departments, stores, customers, vendors, items, BOMs, routes, tax codes, payment terms |
| Approvals | `/approvals` | Done, with **delegation** and **approve by link** (new) | inbox, detail with route and history, approve/reject, delegation, public link page |
| Procurement | `/procurement` | Done | indent, RFQ and quotes, purchase order, schedules |
| Inventory and stores | `/inventory` | Done | GRN, QA release, stock, lots, issue, transfer, reservation, counts, expiry, reorder, valuation |
| Manufacturing | `/manufacturing`, `/routes` | Done | compound batches, work orders, production lots (stage board), machines, moulds, shift entry, OEE, WIP |
| Quality | `/quality` | Done | QA queue, inspection plans and inspections, holds, NCR with 8D, instruments and calibration |
| Sales and dispatch | `/sales` | Done, with **e-invoice, e-way bill and invoice PDF** (new) | prices, quotes, orders and schedules, schedule import, dispatch and packing list, invoices, challans, scrap sales, inter-unit invoices |
| Finance | `/finance`, `/gst` | Done (new since the last guide) | chart of accounts, journals, periods, supplier bills, payments, receipts and ageing, GST returns data, TDS, costing, fixed assets and depreciation, consolidation |
| Maintenance | `/maintenance` | Done (new) | PM plans and due list, breakdowns with MTTR, mould life, reliability |
| HR | `/hr` | Done (new) | employees, leave (self-service first), attendance, appraisals, holidays, shifts |
| Kaizen | `/kaizen` | Done (new) | submit, evaluate, savings, rewards, yearly summary |
| Attachments | `/attachments` | Done (new) | photos and PDFs on kaizen and breakdowns |
| Reports | `/reports` | Done (new) | report list, filters, run, download CSV / Excel / PDF |
| Notifications | `/notifications` | Done (new) | bell with unread count, inbox |
| Company settings and admin | `/settings`, `/admin` | Done | settings, users and roles, approval rules, audit log, job monitor |

Not built in the backend yet (do not make screens): payroll and recruitment (BRD phase 2), mailbox
watcher, barcode and label printing, AI copilot and OCR agents, customer portal, credit notes.

## 3. What is new since the last guide (28 Oct)

The last guide covered the backend up to sales and dispatch. Since then, in order:

1. **Finance, GST and TDS, costing, fixed assets, consolidation** (guide 8.10 and 8.11).
2. **Maintenance, HR, kaizen** (8.12 to 8.14), and attachments (8.15).
3. **Company settings** that change how screens must behave (8.16). Read `GET /settings` at start-up and
   follow it; for example `eInvoice` decides whether the e-invoice buttons exist, `invoiceCopies` how many
   copies print, `approvalByLink` whether link messages exist. Never hard-code such choices.
4. **Reports** (8.17), **notifications** (8.18), **approve by link and delegation** (8.19),
   **e-invoice and e-way bill** (8.20), **invoice printing** (8.21), **shift alert** (8.22).
5. Every response now has an `X-Request-ID` header, and a server error body carries `request_id`. Show it in
   the error message ("reference 3f2a...") so a problem can be traced.

## 4. Before you start (once)

1. `git pull` the backend branch `dev` into your separate backend folder (the frontend branch stays
   `Akshita-Dev`, frontend only).
2. Reinstall the Python packages, because new ones were added (PDF fonts and shaping, QR codes):
   `python -m pip install -e ".[dev]"` inside `apps/api`.
3. Restart the API. The shared development database is already at migration `0036`; the API from `dev`
   matches it. An older checkout of the API still runs but misses the new endpoints.
4. Your `.env` files come privately from Deepak (never from git, never by chat). Change the password you
   used earlier; it was visible in a screenshot.
5. Sign in, then `GET /api/v1/me`. If it answers 403, ask Deepak to give your account an ERP role. For
   testing screens of other roles, ask for several accounts (one per role); the guide's section 7 lists
   what each role sees.

## 5. Foundation to build first (about a week)

Build these once; every screen uses them.

| Piece | What it must do |
|---|---|
| API client | one `fetch` wrapper: base URL from `VITE_API_URL`, `credentials: "include"`, JSON in and out, throws a typed error with `status`, `detail` and `request_id` |
| Error handling | 401: go to sign-in. 403 and 409: show `detail` to the person as it is (it is written for them). 404: "not found". 422: `detail` is a list of `{loc, msg}`: mark those fields. 5xx: "something went wrong, reference <request_id>" |
| Session store | load `/me` once after sign-in: role, `permissions`, `modules`, plants. Show a menu item only if its module is in `modules`; show a button only if its permission is in `permissions`. This is for convenience; the backend enforces it |
| Layout | menu from modules, plant picker when the user has several plants (`unit_ids`), top bar with the notifications bell |
| List page | table with `limit` / `offset` paging (`{items, total}`), filters from the Swagger query parameters, status badge |
| Detail page | header with status, lines table, history, action buttons shown by status |
| Form helpers | decimals are **strings** (`"100.000"`): keep them as strings in forms, send strings. Dates `YYYY-MM-DD` |
| File helpers | download a blob (reports, PDFs) with the filename from `Content-Disposition`; upload as JSON `content_base64` (attachments) |
| Idempotency | send an `Idempotency-Key` header (a fresh UUID per click) on `POST /approvals/{id}/decide` |
| Polling | unread count (`GET /notifications/count`) every 60 seconds. There is no push yet |

## 6. Build order (waves)

Each wave is usable on its own. Do them in this order; the earlier ones are what the others lean on.

| Wave | Screens | Endpoints to start with (guide section) |
|---|---|---|
| 0 | Foundation above | `/me`, `/settings` (5, 8.16) |
| 1 | Approvals inbox and detail, notifications bell | `/approvals`, `/approvals/{id}`, `/approvals/{id}/decide`, `/notifications` (8.3, 8.18) |
| 2 | Master data: plants, departments, stores, customers, vendors, items, tax codes, payment terms | `/org/*`, `/parties/*`, `/ref/*`, `/product/items` (8.2) |
| 3 | Procurement then inventory: indent, RFQ, PO; GRN, QA release, stock, issue, transfer, counts | `/procurement/*`, `/inventory/*` (8.5, 8.6) |
| 4 | Production: work orders, compound batches, **stage board** for production lots, shift entry, machines and moulds | `/manufacturing/*` (8.7) |
| 5 | Quality: QA queue, inspections, holds, NCR (8D steps), instruments | `/quality/*` (8.8) |
| 6 | Sales: orders and schedules, dispatch with packing list, invoice; then the **e-invoice panel** and **print** button on the invoice | `/sales/*` (8.9, 8.20, 8.21) |
| 7 | Finance: supplier bills and payments, receipts and ageing, journals and periods, GST and TDS, costing, assets | `/finance/*`, `/gst/*` (8.10, 8.11) |
| 8 | HR self-service first (apply for leave, see balance), then HR manager screens; kaizen; maintenance | `/hr/*`, `/kaizen`, `/maintenance/*` (8.12 to 8.15) |
| 9 | Reports, company settings, user and role admin, approval rules, audit log, job monitor, delegation | `/reports`, `/admin/*`, `/approvals/delegations` (8.17, 8.19) |
| Anytime | The public **approve page** `/approve/{token}` | `/public/approval-links/{token}` (8.19) |

## 7. Special pages and how to build them

- **Approve page `/approve/{token}` (public, no sign-in):** `GET /api/v1/public/approval-links/{token}` shows
  title, amount, step, requester, expiry. Show two buttons, Approve and Reject (with a note box).
  `POST .../decide` with `{decision, note}`. The page must never decide on opening: mail scanners open
  links. 404 means an unknown link; 410 means used, expired or decided elsewhere: show `detail`.
  This page lives outside the signed-in layout.
- **Invoice screen:** show an "E-invoice" panel only when `GET /settings` says `eInvoice` is not `OFF` and
  the customer has a GSTIN. States: NONE, QUEUED, GENERATED, FAILED, CANCELLED. A FAILED state shows
  `gsp_error` (often missing address or pincode: link to the plant or customer address form). The
  "E-way bill" button asks for vehicle number, distance and mode. "Print" calls `/pdf` and downloads.
- **Reports screen:** do not hard-code reports. `GET /reports` returns each report's filters and
  columns; build the filter form and the table from that, and offer CSV / Excel / PDF downloads.
- **Settings-driven screens:** copies, channels, e-invoice mode and similar come from `/settings`.
- **Delegation:** a small form (colleague, from, to) on the approvals page, list of the ones given and
  received, a revoke button.
- **Notifications:** the bell shows the unread count; the inbox lists `subject` and `body`; clicking one
  marks it read and goes to the record (`reference_type` + `reference_id`).

## 8. Rules of the road

1. Never rely on hiding a button for security, and never re-implement a business rule in the browser.
2. Show the backend's `detail` text; do not replace it with a generic message.
3. Money and quantities are strings; do not do arithmetic with floating point in the browser.
4. Nothing is deleted in this system: show status (cancelled, inactive, reversed), do not offer "Delete".
5. 404 can also mean "outside your plant"; do not say "deleted".
6. Take statuses from the response (some are worked out by the server); do not recompute them.
7. Do not send fields an endpoint does not list; extra fields answer 422.
8. Do not build screens for things listed as "not built yet".

## 9. When something looks wrong

Send Deepak: the screen, the exact URL and method, what you sent, the response (status and body), and the
`request_id` from the error. The backend log has one line for every request with that id.

## 10. Where to read more

| File | For |
|---|---|
| `docs/FRONTEND_BACKEND_GUIDE.md` (and the `.docx`) | every module screen by screen, every endpoint, roles and permissions |
| `docs/FRONTEND_LOCAL_SETUP.md` | running the backend on your machine |
| `docs/TEAM_SETUP.md` | the shared database and its rules |
| `docs/VRUB_DECISIONS_AND_INPUTS.md` | what V-Rub still has to decide; some answers will change settings |
| `docs/PROJECT_ROADMAP.md` | what was built when |
