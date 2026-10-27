# heyyERP Project Roadmap

This is the shared project checklist for the heyyERP backend. Work proceeds in phase order; a module is checked off only after its acceptance checks pass and its change is merged to `main`. Push each feature branch and open a pull request so the team can review it before merge.

## Progress key

- `[x]` Complete and confirmed.
- `[ ]` Not complete, or not yet verified.

## Module status board

Status as of 2026-10-27 (all work lives on the single branch `dev`; a tick means built, tested and pushed there). `Done` = built and confirmed; `Partial` = some of it exists; `Not started` = only the database tables exist in `heyyERP_postgresql_schema.sql` (126 tables, tenant RLS policy generated for every `tenant_id` table). Update this table in the same change that ticks a phase item.

| Module | Phase | Schema tables it will use | Status | Done so far | Next step |
|---|---|---|---|---|---|
| Project foundation | 0 | all | Partial | Git + `origin`, `.gitignore`, `CONTRIBUTING.md`, FastAPI scaffold with `/health/live` and `/health/ready`, schema applied in pgAdmin | Review schema vs BRD; add migrations (no Alembic yet); agree branch rules |
| Authentication (Better Auth) | 1 | `auth.*` (Better Auth) | Partial | Email/password, email verification, reset, TOTP, organization plugin, Resend mailer, web sign-in screens, one Playwright spec | Record e2e results; fix invitation-expiry mismatch; decide MFA policy |
| API tenant security | 1 | `tenant`, `app_user` (`auth_subject`) | Done | Session middleware, tenant from server config, ERP profile and role/permissions from the database, MFA by ERP role, non-owner login, RLS and isolation tests | Production tenant mapping table when a second tenant arrives |
| Organization and master data | 2 | `unit`, `department`, `store`, `uom`, `item_category`, `payment_term`, `tax_code`, `customer`, `vendor`, `business_setting` | Done | Plants, departments, stores, reference data, customers, vendors and the business rules: APIs, validation, audit, tests | V-Rub to confirm the real plants, GSTINs, departments, stores and units of measure |
| Roles, permissions, approvals | 3 | `role`, `permission`, `role_permission`, `role_module`, `user_unit_scope`, `approval_*`, `sla_policy` | Done | 12 roles, 37 permissions, module access, plant scope, admin users API, approval engine with rules, SLA, escalation and vendor onboarding | Delegation (FR-WF-03) and approving by e-mail/WhatsApp (FR-WF-04) are later; PO, indent and bill register with the engine when built |
| Product engineering | 4 | `item`, `item_unit_policy`, `item_specification`, `bom`, `bom_line`, `engineering_change`, `route`, `route_stage`, `customer_part_xref` | Done | Items, plant policies, specifications, customer part numbers, versioned routes, BOMs with revisions, engineering changes and effective dates | Compound-to-item link (Phase 7), item cost, BOM explosion (MRP) |
| Procurement | 5 | `indent*`, `rfq*`, `vendor_quote*`, `purchase_order`, `po_*`, `document_sequence` | Done | Indents, RFQs with quotes and comparison, purchase orders with approval, rules and receiving handoff | Goods receipt (Phase 6), purchase returns, cash purchase, vendor rating (BRD "should"), bill matching (Phase 10) |
| Inventory and stores | 6 | `lot`, `stock_ledger`, `stock_balance`, `cost_layer*`, `stock_issue`, `stock_reservation`, `stock_transfer*`, `stock_count*` | Done | Goods receipts, QA release, FEFO/FIFO issue, reservations, transfers between stores and plants, counts with approval, FIFO valuation, lot trace, reconciliation | Serial numbers, labels/barcodes, purchase-return debit notes (Phase 10), short receipt on transfers |
| Manufacturing and production | 7 | `compound*`, `compound_batch`, `batch_weighing_line`, `prod_lot*`, `lot_consumption`, `work_order`, `shift_production`, `shift_loss`, `rework_record`, `packing`, `machine`, `mould` | Done | Compounds and batches with derived status, work orders, lots through routes with QA gates, FIFO compound and insert consumption, packing to FG, shift entries, downtime, OEE (availability and quality), rework, returns, WIP, tracing | MRP from sales schedules (Phase 9), costed production (Phase 10), OEE performance (needs ideal cycle time), batch and lot labels, voice entry (BRD "could"), maintenance on machines and moulds (Phase 11) |
| Quality | 8 | `inspection_plan*`, `qa_inspection*`, `qa_measurement`, `quality_hold`, `ncr*`, `instrument`, `calibration` | Done | Inspection plans, inspections against limits, QA queue, calibration with due alerts, holds on lots, batches and stage lots, NCR with 8D, dispositions | PPAP/APQP tracker and SPC charts (BRD "could"), file storage for evidence (Phase 12), customer dispatch check (Phase 9) |
| Sales and dispatch | 9 | `customer_rfq*`, `customer_quote*`, `price_list*`, `sales_order`, `so_*`, `schedule_import`, `dispatch*`, `challan*`, `customer_part_xref`, `scrap_sale` | Not started | — | — |
| Finance and GST | 10 | `fiscal_period`, `journal*`, `supplier_bill*`, `bill_match`, `customer_invoice`, `inter_unit_invoice`, `payment*`, `gst_return_data`, `tds_entry` | Not started | — | — |
| Maintenance, HR, Kaizen | 11 | `machine`, `mould`, `pm_*`, `breakdown`, `fixed_asset`, `depreciation*`, `employee`, `attendance`, `leave_*`, `appraisal`, `kaizen` | Not started | — | Payroll tables exist but payroll is out of BRD phase 1 |
| Reports, integrations, AI | 12 | `integration_job`, `notification`, `outbox_event`, `idempotency_record`, `stored_file`, `audit_log` | Not started | — | — |

## Phase 0 — Project and database foundation

- [x] Initialize Git on `main`, add the GitHub `origin`, and push the initial project snapshot.
- [x] Add a root `.gitignore` for dependencies, generated output, caches, and secrets.
- [x] Add the team contribution guide (`CONTRIBUTING.md`).
- [x] Add the FastAPI backend scaffold, PostgreSQL connection/session foundation, and health endpoints.
- [x] Apply the current PostgreSQL schema in pgAdmin (confirmed by the project owner).
- [ ] Review the current schema against BRD requirements; confirm each table, tenant boundary, and relationship before treating it as production-ready.
- [ ] Set up teammate repository access and agree branch protection / pull-request review rules.
- [x] Establish repeatable database migrations and a clean-database setup check: done on `dev` (Alembic: `0001` baseline, `0002` app-role grants; tests build a scratch database from nothing). The live `HeyyERP` database was verified identical to a fresh migration (1,006 columns, 1,646 constraints, 285 indexes, 127 tables/views), stamped `0001` and upgraded to `0002`.

## Phase 1 — Authentication and tenant security

- [x] Implement the Better Auth organization-level authentication features (confirmed complete by the project owner; implementation is present in `apps/auth`).
- [x] Configure email/password, email verification, password reset, TOTP two-factor authentication, and the Resend SMTP mailer in the auth service.
- [ ] Run and record end-to-end tests for sign-up, duplicate email, verification expiry/resend, sign-in, MFA, password reset, and sign-out.
- [x] Reconcile invitation expiry: decided 7 days (604800 s) for invitations, since 10 minutes is too short to accept one; verification and reset links stay at 10 minutes. `apps/auth/README.md` and `apps/auth/src/auth.ts` now agree (2026-10-27).
- [x] Connect verified Better Auth organization membership to the ERP `tenant_id` in trusted API session context.
- [x] Enforce verified email, active organization membership, MFA status, role/permission, and tenant isolation on protected API requests; reject tenant IDs supplied directly by clients.
- [x] Build the FastAPI session middleware that validates the Better Auth session and sets `request.state.tenant_id`. Built on `feature/api-session-middleware` (`security.py`, `GET /api/v1/me`, 11 unit tests, ruff and mypy clean); tick after review, merge and the live check below.
- [x] Live-check the middleware (2026-10-27, local `HeyyERP` database): ran the auth migration, signed in a temporary user and called `GET /api/v1/me` against the real auth service and PostgreSQL. Verified: no cookie 401; forged cookie 401; unverified email blocked at sign-in; MD without 2FA gets `access_granted:false`; STORES gets `access_granted:true`; removing the membership gives 403. Test user deleted afterwards.
- [x] Create the V-Rub `tenant` row and set `ORG_TENANT_MAP` in the API `.env` (done locally; repeat for staging and production). The `v-rub-suspensions` organization row was also created in `auth.organization`; there is still no seed script for it.
- [x] Make the API connect as a non-owner PostgreSQL role: local role `heyyerp_app` (no superuser, no BYPASSRLS) created with table DML on `erp` and SELECT on `auth.member` / `auth.organization` only. Tested: without a tenant set 0 rows are visible, the own tenant shows 1, another tenant 0, and inserting a row for another tenant is rejected. Role grants are now scripted in migration `0002` (group role `heyyerp_app_rw`); creating the login itself is a documented per-environment step in `apps/api/README.md`.
- [x] Decide the MFA policy: follow BRD section 9.4 — TOTP is mandatory only for ADMIN, MD and ACCOUNTS (decided by the project owner, 2026-10-27). `src/auth-contract.ts` now encodes this.
- [x] Align the web onboarding flow with the MFA policy: done on `dev` — after sign-in the web app calls `GET /api/v1/me` and only sends roles that must use MFA and have not enrolled to `/totp-setup`; `/app` re-checks access; API now allows CORS from `WEB_BASE_URL`.
- [ ] Add shop-floor OTP / PIN sign-in (BRD FR-ADM-06 and FR-ADM-07) before shop-floor users are onboarded; until then they use verified email and password.
- [x] Verify tenant isolation with automated API/database tests before allowing business-data writes: database-level tests done on `dev` (RLS and a policy on every `tenant_id` table; a non-owner login sees only its own tenant, cannot update, delete or insert across tenants, cannot disable RLS). Still to add: an API-level test once the first tenant-scoped endpoint exists.

## Phase 2 — Organization and master data

- [x] Define the approved organization-to-tenant mapping and organization settings: done on `dev` — the mapping is the `ORG_TENANT_MAP` setting (organization slug to tenant id), and the 17 BRD business rules plus two stated defaults are typed settings (`/api/v1/settings` readable by every member, `/api/v1/admin/settings/{key}` changeable by ADMIN only, with full history). Rules V-Rub has not answered (cash limit, minimum quotes, MD margin approval) show `AWAITING_DECISION` and are not enforced. Other modules read a rule with `business_rules.service.get_value`.
- [x] Implement plants/business units with GSTIN, state, active status, and tenant-scoped unique codes: done on `dev` (`/api/v1/org/units`; GSTIN format and check-digit validation; state derived from GSTIN; cannot deactivate a plant with active stores or departments).
- [x] Implement departments, parent-department relationships, and plant association: done on `dev` (`/api/v1/org/departments`; no cycles, no inactive parent or plant).
- [x] Implement stores/warehouses under plants, including store type and active status: done on `dev` (`/api/v1/org/stores`; code unique per plant; no stores on inactive plants). Phase 6 must add the check that blocks deactivating a store that holds stock.
- [x] Implement shared reference data required by the BRD (for example UOM, item categories, payment terms, and tax codes) after scope review: done on `dev` (`/api/v1/ref/payment-terms`, `tax-codes`, `item-categories`, read-only `uom` seeded by migration `0003` with 14 common units; V-Rub must confirm the real unit list).
- [x] Implement customer and vendor master records with validated identifiers and status; keep procurement/sales transaction behavior in their own modules: done on `dev` (`/api/v1/parties/customers` and `/vendors`; GSTIN check digit, PAN format, PAN must match the GSTIN; vendors always start PENDING_APPROVAL and the client cannot set status; migration `0004` allows one vendor per GSTIN per tenant). Vendor approval and reactivation arrive with the Phase 3 workflow.
- [x] Add tenant-safe CRUD/list APIs, validation, permission checks, and audit events for approved master-data entities: built for plants, departments, stores, reference data, customers and vendors (interim role-to-permission map in `security.py`; audit rows in `erp.audit_log`; paging; constraint errors as 4xx).
- [x] Add tests for uniqueness, invalid references, inactive records, authorization, and cross-tenant access: done in `tests/test_org_api.py`, `test_reference_api.py` and `test_parties_api.py` (54 API tests in total pass against a scratch database with two tenants and a non-owner login).
- [ ] Confirm real plant names, GSTINs, department list, and store list with V-Rub before loading production master data. Note: the BRD's demo GSTINs (for example `06AAKCV4123R1ZQ`) fail the real check-digit test, so they cannot be loaded; real values are needed. Also still open: the 14 seeded units of measure and the vendor bank-account fields (BRD FR-PRC-04 asks for bank checks, but `erp.vendor` has no bank columns).

## Phase 3 — Roles, permissions, and approval workflow

- [x] Define ERP roles, module access, action permissions, and plant/unit scope from the BRD: done on `dev` — 12 roles, 37 permissions and module access (`rbac/catalog.py`, seeded by `python -m heyyerp_api.cli seed`); admin API manages users and their plant scope (`/api/v1/admin/users`); plant scope (FR-ADM-04) is enforced on plant, department and store reads and writes (an out-of-scope record is 404, a write outside scope is 403).
- [x] Enforce permissions in the API (not only in the frontend): done on `dev` — roles, permissions and modules are rows in the database (`erp.role`, `role_permission`, `role_module`), loaded per request from the caller's ERP profile (`erp.app_user`); no permission is taken from the browser or Better Auth. Every existing endpoint uses it, and `/me` shows the caller their permissions and modules. MFA is keyed to the ERP role.
- [x] Implement configurable approval rules, approval instances/actions, SLA tracking, and audit history: done on `dev` (`/api/v1/approvals`, `/api/v1/admin/approval-rules`). Rules are data: value band, priority, steps by role, named user or department head, and an SLA; the route is frozen onto each document at submission. Leading steps held by the requester's role are skipped (all skipped = approved on the spot); decisions lock the row, are idempotent by `Idempotency-Key`, and refuse a stale step; only ADMIN may override a step (logged); nobody decides their own submission; a rejection needs a note. SLA is tracked per step (`due_at`, `overdue`); `POST /approvals/sla-sweep` queues reminders at 50% and 90% and escalates past 100% (to the Plant Head, or the MD if it is already with the Plant Head), once per step. Events go to the outbox table and every action to the audit log. First real consumer: vendor onboarding (`vendor` documents). PO, indent, bill and the other types register the same way when their modules are built.
- [x] Test approver routing, role-based decisions, rejection, escalation, and rule changes while documents are in flight: done in `tests/test_approvals.py` (30 tests: value-band boundaries, department head and fallback, skip and collapse, wrong role, override, rejection, idempotent replay, stale step, a real two-approver race, rule edit and retire while in flight, named-user approver, SLA reminders and escalation sent once, vendor approve and reject, tenant isolation).
- [x] Phase 3 re-check (2026-10-27): found and fixed two gaps - an approval was readable by every tenant member (now only its requester, people on its route, and those with `approval.view_all` or `approval.override`; everyone else gets 404), and there was no way to read the audit log (`GET /api/v1/admin/audit-log`, admin only, filters by entity, action prefix and actor).
- [ ] Phase 3 gaps against the BRD, to schedule: write audit rows for sign-in and sign-out (FR-ADM-05; needs Better Auth hooks); admin-editable roles and permissions (FR-ADM-02, "should"; roles are read-only through the API today); approval delegation (FR-WF-03), approving by e-mail or WhatsApp link (FR-WF-04) and bulk approval under 50,000 (FR-APR-02), all "should"; resubmitting a document after a rejection (needs a round number on `approval_instance`); an API to read a user's own notifications.

## Phase 4 — Product engineering

- [x] Implement item/product definitions, UOM/category links, and item-to-plant policies: done on `dev` (`/api/v1/product/items`): items with type, UoM, category, HSN (4/6/8 digits), shelf life and production route; per-plant policies (reorder point and quantity, default store) that make an item applicable at a plant, limited by the user's plant scope; technical specifications (typed limits such as Hardness 60-70 Shore A); customer part-number cross-reference. Type and UoM never change after creation; an item that is a component of a live BOM cannot be deactivated.
- [x] Implement revisions, BOM headers/lines, routing/operations, and technical specifications: done on `dev`. BOM (`/api/v1/product/boms`): version per item, lines with quantity and scrap factor, drafts only editable, components must be active RM/WIP/FG/consumable items, no item inside itself, no circular nesting at any depth; where-used per item. Routing (`/api/v1/product/routes`): versioned stage lists with QA gates and one end stage; revising keeps old versions; METAL, MOULD and EXTR seeded from BRD 6.2; items follow a route by code. Specifications: typed rows per item (item section above).
- [x] Implement engineering change history and approval/effective-date behavior: done on `dev`. Submitting a draft records an engineering change (reason, requester, from-version, effective date) and starts an approval (document type `bom`, rule R-BOM: QA then Plant Head, a proposal). Approval makes the version effective on its date and marks the previous one SUPERSEDED with `valid_to` the day before; exactly one version is in force on any date (`/items/{id}/effective-bom?on=`); a late approval takes effect today, never in the past; rejection keeps the old version and allows another revision; history at `/items/{id}/engineering-changes`.
- [x] Test BOM integrity, revision history, and plant-specific applicability: done in `tests/test_bom.py` (13) and `tests/test_product_items.py` (14): integrity rules, circular chains, effective dates and switch-over, late approval, rejection and retry, QA skipping its own step, where-used and the deactivation guard, plant policies as applicability with plant-scope limits, permissions, tenant isolation, audit. 171 API tests pass in total.

## Phase 5 — Procurement

- [x] Implement purchase indents/requisitions and approval handoff: done on `dev` (`/api/v1/procurement/indents`): numbered `IND-26-0001` (financial year April to March, restarts each year, gap-free), types BOM / consumable / capital with the item types each may request, capital lines carry a description and value; draft, edit by requester only, submit to the approval engine (BOM and consumable by R-IND, capital banded by total value: R-IND-CAP-1 up to 25 lakh, R-IND-CAP-2 above, both proposals); approved, rejected, cancelled. Visibility: the procurement module sees all (within plant scope), others only their own. Migration `0007` adds the number counter, `purchase_order.rfq_id` and status checks.
- [x] Implement RFQs, supplier invitations/quotes, comparison, and purchase orders: done on `dev`. RFQs (`/api/v1/procurement/rfqs`, `RFQ-26-0001`): lines, invitations to APPROVED vendors only, quotes (a newer quote replaces the older one, kept for the record), comparison sheet marking the lowest valid full-quantity offer per line and the cheapest complete bidder, award. Purchase orders (`/api/v1/procurement/purchase-orders`, `PO-26-0861` style): direct, from approved indents (quantities added per item) or by awarding an RFQ at the quoted prices; only an APPROVED vendor can receive one; delivery schedule per line; approval band uses the value including GST (R-PO-1 up to 5 lakh, R-PO-2 to 25 lakh, R-PO-3 above), so every line needs a tax code; business rules `requireIndent` and `minQuotes` are enforced at submission.
- [x] Implement purchase order status/history and receiving handoff to inventory: done on `dev`. Statuses DRAFT, PENDING_APPROVAL, OPEN, PARTIALLY_RECEIVED, RECEIVED, CLOSED, REJECTED, CANCELLED (database CHECK); history endpoint (every change plus the approval steps); cancel and close; late-deliveries list for supplier follow-up. The handoff is `procurement.receiving.apply_receipt`, which the Phase 6 goods receipt calls inside its own transaction: it allows at most the pending quantity plus the `grnTolPct` tolerance (10%), fills the delivery schedule oldest first, and moves the order to PARTIALLY_RECEIVED or RECEIVED.
- [x] Test authorization, approval thresholds, duplicate submissions, and order-to-receipt references: done in `tests/test_indents.py` (12), `test_rfq.py` (10) and `test_purchase_orders.py` (21): permissions and module visibility, plant scope, tenant isolation, GST-inclusive band boundaries, rejection, duplicate submission, rules, indent and RFQ links, tolerance arithmetic, schedule allocation, late lines, history, audit.

## Phase 6 — Inventory and stores

- [x] Implement stock ledger, store balances, lot/serial traceability, and stock reservations: done on `dev` (`inventory/ledger.py`, `/inventory/stock`, `/lots/{id}/trace`, `/reservations`; append-only ledger with idempotency keys). Lot traceability is built; serial numbers and barcode labels are not.
- [x] Implement receipt, issue, inter-store/inter-plant transfer, and cycle count workflows: done on `dev` (`/inventory/grns`, `/issues`, `/transfers` DRAFT-DISPATCHED-RECEIVED, `/counts` with the `count` approval; migration `0008`). A store that holds stock, has a transfer on the way or an open count cannot be deactivated.
- [x] Enforce no-negative-stock, FIFO valuation, and FEFO issue rules required by the BRD: done (database CHECK plus row-locked balance; FIFO cost layers per plant and item, carried across plants by transfers; `issueRule` setting picks FEFO or FIFO; expired lots are blocked).
- [x] Test concurrent movements, stock reconciliation, lot genealogy, and tenant/plant access: done in `tests/test_inventory_receipt.py`, `test_inventory_issue.py` (including ten simultaneous issues) and `test_inventory_transfer_count.py`.

## Phase 7 — Manufacturing and production

- [x] Implement production plans/work orders using approved BOM and routing revisions: done on `dev` (`/manufacturing/work-orders`: BOM in force today, the item's active route, release, cancel, complete; compound master with recipe in phr, machines and moulds; migrations `0009`, `0010`). Planned lots cannot exceed the order quantity.
- [x] Implement material issue/return, operation progress, WIP, output, downtime, and rework records: done (`/manufacturing/lots` with stage moves recording who, when, quantity in/out and rejects; packing books FG stock; `/shift-entries` with rejects and downtime by reason; `/wip`, `/oee`, `/rework`, `/return-material`; migration `0011`). Finished goods enter stock at zero cost until Phase 10.
- [x] Enforce route gates and compound maturation/expiry rules: done (leaving a QA gate needs `qa.approve`, other stages `route.move`; batch status is derived by a database view; QA only after minimum maturation and only inside the hardness spec; expired, unapproved or maturing compound is never drawn; the production month locks by `lockDay` plus `prodLockExtraDays`).
- [x] Test work-order material/output reconciliation and end-to-end lot traceability: done in `tests/test_compound_batches.py` (including concurrent compound draws), `test_work_orders.py` and `test_shop_floor.py` (lot and material reconciliation, trace from finished lot to raw-material lot, forward trace from a batch).

## Phase 8 — Quality

- [x] Implement inspection plans and incoming, in-process, and final inspection records: done on `dev` (`/quality/plans` with limits and key characteristics, `/quality/inspections` judging every measurement against the plan, `/quality/queue` for GRN lots, compound batches and stage lots, `/quality/instruments` with calibration due alerts; migration `0012`). A GRN line, compound batch or stage lot with an inspection plan cannot be released without a passing inspection; QA decisions without a plan are recorded automatically.
- [x] Implement non-conformance, disposition, corrective action, and quality evidence: done (`/quality/ncrs` with 8D steps D1 to D8 completed in order, dispositions use-as-is, rework, sort, scrap and return-to-supplier, closure rules; migration `0013`). Evidence is stored as a reference text until file storage exists (Phase 12).
- [x] Connect quality holds/releases to inventory and production workflows: done (`/quality/holds` freeze a stock lot as QUARANTINE, a compound batch as ON_HOLD and a production lot as HOLD; release restores the previous state; scrap or return closes the hold with the goods blocked, failed or scrapped).
- [x] Test that rejected/held lots cannot be issued or dispatched contrary to policy: done in `tests/test_quality_inspections.py` and `test_quality_ncr.py` (issue, transfer, compound allocation, insert draw, stage moves and packing are all refused for held or rejected goods). Dispatch to customers is checked again in Phase 9.

## Phase 9 — Sales and dispatch

- [ ] Implement customer RFQs/quotes, price lists, sales orders, and delivery schedules.
- [ ] Implement customer schedule import with preview, validation, and apply workflow.
- [ ] Implement picking, dispatch, challans, shipment status, and invoice handoff.
- [ ] Test order-to-dispatch traceability, stock availability, customer references, and permissions.

## Phase 10 — Finance and costing

- [ ] Confirm finance, tax, GST, period-lock, inter-unit, and accounting integration rules with the business owner.
- [ ] Implement approved accounting references, 3-way match exceptions, payment proposals, receivables, and period locks.
- [ ] Implement unit costing and consolidated reporting inputs as approved.
- [ ] Reconcile financial postings/exports against their source business transactions.

## Phase 11 — Maintenance, HR, and Kaizen

- [ ] Implement maintenance tickets, equipment/service records, and spares requirements.
- [ ] Implement employee/department/reporting-line master data and leave workflow.
- [ ] Implement Kaizen submission, review, evaluation, and savings records.
- [ ] Keep payroll processing and recruitment out of the initial scope unless the owner approves the BRD phase-2 items.
- [ ] Test role-based access, approvals, and links to departments/plants.

## Phase 12 — Reports, integrations, AI, and release readiness

- [ ] Implement role- and plant-scoped report APIs and approved exports.
- [ ] Implement approved integrations and resilient background jobs with observable failures.
- [ ] Implement AI/copilot features only with the same authorization and data-scope rules as the underlying APIs.
- [ ] Complete performance, security, backup/restore, monitoring, deployment, and rollback checks.
- [ ] Run end-to-end tests across approved priority workflows and prepare the production release checklist.

## Module completion and push checklist

Use this checklist for every module before marking its phase item complete:

- [ ] Confirm the module scope against BRD requirements and document any approved assumptions.
- [ ] Commit the changes to the single working branch `dev` (project owner's decision, 2026-10-27: no more per-feature branches).
- [ ] Add/update migrations, API contract, validation, authorization, tenant scoping, and audit behavior where applicable.
- [ ] Run module tests and relevant regression/end-to-end tests; record the commands and results in the pull request.
- [ ] Review the diff for secrets, unrelated files, migration conflicts, and compatibility with dependent modules.
- [ ] Push `dev`; open a pull request from `dev` to `main` when a group of modules is ready for review.
- [ ] Merge to `main` only after approval and passing checks.
- [ ] Update this roadmap in the same reviewed change or a small follow-up PR, then push it.

## Current next work

Phases 1 to 3 are built on `dev`. Open items before business modules:

1. Review and merge the four open branches, in this order: `docs/module-status-board`, `feature/api-session-middleware`, `feature/web-mfa-flow`, `feature/db-migrations`. Then tick the items they cover.
2. Run and record the auth end-to-end tests (needs a mail inbox or the auth service's test mail mode).
3. Add shop-floor OTP / PIN sign-in before shop-floor users are onboarded.

Then begin Phase 2 (organization and master data). Tick items module by module only after tests pass and the reviewed change is merged.

## Progress log

- 2026-10-27: Phase 8 built on `dev`: inspection plans, inspections, QA queue, calibration, holds, NCR with 8D and dispositions; migrations `0012` and `0013`. Not built (BRD "could"): PPAP/APQP tracker and SPC charts (FR-QA-06), and batch t90 and SG are recorded as ordinary inspection parameters. Apply `alembic upgrade head` on existing databases.
- 2026-10-27: Phase 7 built on `dev`: compounds, batch weighing to QA with a derived status, work orders, lots through METAL, MOULD and EXTR routes, compound and insert consumption, packing, shift entries, OEE, rework, returns, WIP and tracing; migrations `0009` to `0011`. Open: costed production, OEE performance, labels for batches and lots, extrusion-specific steps beyond the route, MRP. Apply `alembic upgrade head` and re-run the seed on existing databases.
- 2026-10-27: Phase 6 built on `dev`: stock ledger, goods receipts with QA release, FEFO/FIFO issue, reservations, transfers (between stores and plants, cost carried), cycle counts with approval, valuation, expiry and reorder reports, lot trace and reconciliation; migration `0008`. Open: serial numbers, barcode labels, short receipt on a transfer, debit notes for purchase returns (Phase 10). Apply `alembic upgrade head` and re-run the seed on existing databases.
- 2026-10-27: Phase 5 built on `dev`: indents, RFQs, purchase orders and the receiving handoff; migration `0007`. Not built (BRD "should" or later phases): cash purchase (FR-PRC-09), purchase returns and debit notes (FR-PRC-08), vendor rating (FR-PRC-05), the goods receipt itself (Phase 6), PO amendment after approval.
- 2026-10-27: Phase 4 built on `dev`: BOMs with revisions, engineering changes, effective dates and approval (171 API tests pass). Open design notes: BOM is per item, not per plant (plant applicability is the item's plant policy); rubber compounds are not linked to items yet (planned with Phase 7); the schema has no item cost or rate.
- 2026-10-27: Phase 4 started: items, plant policies, specifications, customer part numbers and versioned production routes (METAL, MOULD, EXTR seeded from BRD 6.2); migration `0006`; 158 API tests pass.
- 2026-10-27: Business rules (BRD 6.1) built as typed, versioned settings; Phase 2 code complete (140 API tests pass).
- 2026-10-27: Live Phase 3 check (real sessions, real PostgreSQL, non-owner API login): 29 of 29 pass - first ADMIN through `cli make-admin`, 2FA enrolment, admin creates ERP profiles, role permissions and modules, plant scope, vendor approval and rejection through the engine, idempotent decisions, SLA overdue flag, reminders and escalation, deactivated user refused on the next request. Test data removed; test ERP profiles stay as DEACTIVATED because the append-only audit log references them.
- 2026-10-27: Phase 3 built on `dev`: database-driven roles and permissions, admin users, plant scope, and the approval engine (126 API tests pass). Commits now use deepak.kumar@heyybot.com.
- 2026-10-27: All earlier feature branches were combined into the single working branch `dev`; new work is committed and pushed there only.
- 2026-10-27: Live Phase 2 check (real sessions, real PostgreSQL, non-owner API login): 29 of 29 checks pass - plants, departments, stores, reference data, customers and vendors; role boundaries (STORES, SALES, PURCHASE, ADMIN with and without 2FA); tenant isolation against a second tenant; audit rows carry the real user; CORS preflight for PATCH. It also found a real bug, fixed on `fix/auth-session-ratelimit`: the auth service's 10-per-minute rate limit applied to the API's session lookups, so after 10 requests the API reported every user as signed out. Test data was deleted; 8 audit rows remain because the audit log is append-only.
- 2026-10-27: Customers and vendors built on `feature/master-parties`; 54 API tests pass. Phase 2 code complete, awaiting review and merge of three branches.
- 2026-10-27: Reference data (payment terms, tax codes, item categories, UOM) built on `feature/master-reference`; 43 API tests pass.
- 2026-10-27: Phase 2 started on `feature/master-org`: plants, departments and stores APIs with shared master-data helpers; 36 API tests pass against a scratch database (two tenants, non-owner login). Not ticked until merged.
- 2026-10-27: Full-stack check of the combined branches (auth + API + web, real browser, no mocks): STORES signs in without TOTP; a non-member is refused; MD is sent to authenticator setup, enrols with a real TOTP code, and is then asked for a code at the next sign-in (wrong code rejected). Found and fixed on `fix/web-sign-out`: the "Sign out" link did not end the session.
- 2026-10-27: MFA policy decided (BRD 9.4). API session middleware and `/me` built on `feature/api-session-middleware`, awaiting review; not yet ticked.
- 2026-10-27: Added the module status board and two Phase 1 items (API session middleware, MFA policy) after reviewing the repository. No items newly ticked.
