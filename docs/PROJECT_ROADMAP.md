# heyyERP Project Roadmap

This is the shared project checklist for the heyyERP backend. Work proceeds in phase order; a module is checked off only after its acceptance checks pass and its change is merged to `main`. Push each feature branch and open a pull request so the team can review it before merge.

## Progress key

- `[x]` Complete and confirmed.
- `[ ]` Not complete, or not yet verified.

## Module status board

Status as of 2026-10-27, from reading the repository (not from memory). `Done` = built and confirmed; `Partial` = some of it exists; `Not started` = only the database tables exist in `heyyERP_postgresql_schema.sql` (126 tables, tenant RLS policy generated for every `tenant_id` table). Update this table in the same change that ticks a phase item.

| Module | Phase | Schema tables it will use | Status | Done so far | Next step |
|---|---|---|---|---|---|
| Project foundation | 0 | all | Partial | Git + `origin`, `.gitignore`, `CONTRIBUTING.md`, FastAPI scaffold with `/health/live` and `/health/ready`, schema applied in pgAdmin | Review schema vs BRD; add migrations (no Alembic yet); agree branch rules |
| Authentication (Better Auth) | 1 | `auth.*` (Better Auth) | Partial | Email/password, email verification, reset, TOTP, organization plugin, Resend mailer, web sign-in screens, one Playwright spec | Record e2e results; fix invitation-expiry mismatch; decide MFA policy |
| API tenant security | 1 | `tenant`, `app_user` (`auth_subject`) | Partial | `get_tenant_session` sets `app.tenant_id` for RLS; `src/auth-contract.ts` defines the principal | Build the API session middleware (nothing sets `request.state.tenant_id` yet); map organization to tenant; isolation tests |
| Organization and master data | 2 | `unit`, `department`, `store`, `uom`, `item_category`, `payment_term`, `tax_code`, `customer`, `vendor` | Not started | — | Start after Phase 1 gate |
| Roles, permissions, approvals | 3 | `role`, `permission`, `role_permission`, `role_module`, `user_unit_scope`, `approval_*`, `sla_policy` | Not started | — | — |
| Product engineering | 4 | `item`, `item_unit_policy`, `bom`, `bom_line`, `route`, `route_stage` | Not started | — | — |
| Procurement | 5 | `indent*`, `rfq*`, `vendor_quote*`, `purchase_order`, `po_*`, `grn*`, `purchase_return*`, `cash_purchase`, `vendor_rating` | Not started | — | — |
| Inventory and stores | 6 | `lot`, `stock_ledger`, `stock_balance`, `cost_layer*`, `stock_transfer*`, `stock_count*` | Not started | — | — |
| Manufacturing and production | 7 | `compound*`, `compound_batch`, `batch_weighing_line`, `prod_lot*`, `lot_consumption`, `work_order`, `shift_production`, `packing` | Not started | — | — |
| Quality | 8 | `qa_inspection*`, `qa_measurement`, `ncr*`, `instrument`, `calibration` | Not started | — | — |
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
- [ ] Establish repeatable database migrations and a clean-database setup check: built on `feature/db-migrations` (Alembic: `0001` baseline, `0002` app-role grants; tests build a scratch database from nothing). The live `HeyyERP` database was verified identical to a fresh migration (1,006 columns, 1,646 constraints, 285 indexes, 127 tables/views), stamped `0001` and upgraded to `0002`. Tick after review and merge.

## Phase 1 — Authentication and tenant security

- [x] Implement the Better Auth organization-level authentication features (confirmed complete by the project owner; implementation is present in `apps/auth`).
- [x] Configure email/password, email verification, password reset, TOTP two-factor authentication, and the Resend SMTP mailer in the auth service.
- [ ] Run and record end-to-end tests for sign-up, duplicate email, verification expiry/resend, sign-in, MFA, password reset, and sign-out.
- [x] Reconcile invitation expiry: decided 7 days (604800 s) for invitations, since 10 minutes is too short to accept one; verification and reset links stay at 10 minutes. `apps/auth/README.md` and `apps/auth/src/auth.ts` now agree (2026-10-27).
- [ ] Connect verified Better Auth organization membership to the ERP `tenant_id` in trusted API session context.
- [ ] Enforce verified email, active organization membership, MFA status, role/permission, and tenant isolation on protected API requests; reject tenant IDs supplied directly by clients.
- [ ] Build the FastAPI session middleware that validates the Better Auth session and sets `request.state.tenant_id`. Built on `feature/api-session-middleware` (`security.py`, `GET /api/v1/me`, 11 unit tests, ruff and mypy clean); tick after review, merge and the live check below.
- [x] Live-check the middleware (2026-10-27, local `HeyyERP` database): ran the auth migration, signed in a temporary user and called `GET /api/v1/me` against the real auth service and PostgreSQL. Verified: no cookie 401; forged cookie 401; unverified email blocked at sign-in; MD without 2FA gets `access_granted:false`; STORES gets `access_granted:true`; removing the membership gives 403. Test user deleted afterwards.
- [x] Create the V-Rub `tenant` row and set `ORG_TENANT_MAP` in the API `.env` (done locally; repeat for staging and production). The `v-rub-suspensions` organization row was also created in `auth.organization`; there is still no seed script for it.
- [x] Make the API connect as a non-owner PostgreSQL role: local role `heyyerp_app` (no superuser, no BYPASSRLS) created with table DML on `erp` and SELECT on `auth.member` / `auth.organization` only. Tested: without a tenant set 0 rows are visible, the own tenant shows 1, another tenant 0, and inserting a row for another tenant is rejected. Role grants are now scripted in migration `0002` (group role `heyyerp_app_rw`); creating the login itself is a documented per-environment step in `apps/api/README.md`.
- [x] Decide the MFA policy: follow BRD section 9.4 — TOTP is mandatory only for ADMIN, MD and ACCOUNTS (decided by the project owner, 2026-10-27). `src/auth-contract.ts` now encodes this.
- [ ] Align the web onboarding flow with the MFA policy: built on `feature/web-mfa-flow` — after sign-in the web app calls `GET /api/v1/me` and only sends roles that must use MFA and have not enrolled to `/totp-setup`; `/app` re-checks access; API now allows CORS from `WEB_BASE_URL`. Tick after review and merge.
- [ ] Add shop-floor OTP / PIN sign-in (BRD FR-ADM-06 and FR-ADM-07) before shop-floor users are onboarded; until then they use verified email and password.
- [ ] Verify tenant isolation with automated API/database tests before allowing business-data writes: database-level tests built on `feature/db-migrations` (RLS and a policy on every `tenant_id` table; a non-owner login sees only its own tenant, cannot update, delete or insert across tenants, cannot disable RLS). Still to add: an API-level test once the first tenant-scoped endpoint exists. Tick after review and merge.

## Phase 2 — Organization and master data

- [ ] Define the approved organization-to-tenant mapping and organization settings.
- [ ] Implement plants/business units with GSTIN, state, active status, and tenant-scoped unique codes: built on `feature/master-org` (`/api/v1/org/units`; GSTIN format and check-digit validation; state derived from GSTIN; cannot deactivate a plant with active stores or departments). Tick after review and merge.
- [ ] Implement departments, parent-department relationships, and plant association: built on `feature/master-org` (`/api/v1/org/departments`; no cycles, no inactive parent or plant). Tick after review and merge.
- [ ] Implement stores/warehouses under plants, including store type and active status: built on `feature/master-org` (`/api/v1/org/stores`; code unique per plant; no stores on inactive plants). Phase 6 must add the check that blocks deactivating a store that holds stock. Tick after review and merge.
- [ ] Implement shared reference data required by the BRD (for example UOM, item categories, payment terms, and tax codes) after scope review.
- [ ] Implement customer and vendor master records with validated identifiers and status; keep procurement/sales transaction behavior in their own modules.
- [ ] Add tenant-safe CRUD/list APIs, validation, permission checks, and audit events for approved master-data entities: pattern and shared helpers built on `feature/master-org` (interim role-to-permission map in `security.py`, audit rows in `erp.audit_log`, paging, constraint errors as 4xx). Applied so far to plants, departments and stores; reference data, customers and vendors still to do.
- [ ] Add tests for uniqueness, invalid references, inactive records, authorization, and cross-tenant access.
- [ ] Confirm real plant names, GSTINs, department list, and store list with V-Rub before loading production master data.

## Phase 3 — Roles, permissions, and approval workflow

- [ ] Define ERP roles, module access, action permissions, and plant/unit scope from the BRD.
- [ ] Enforce permissions in the API (not only in the frontend).
- [ ] Implement configurable approval rules, approval instances/actions, SLA tracking, and audit history.
- [ ] Test approver routing, role-based decisions, rejection, escalation, and rule changes while documents are in flight.

## Phase 4 — Product engineering

- [ ] Implement item/product definitions, UOM/category links, and item-to-plant policies.
- [ ] Implement revisions, BOM headers/lines, routing/operations, and technical specifications.
- [ ] Implement engineering change history and approval/effective-date behavior.
- [ ] Test BOM integrity, revision history, and plant-specific applicability.

## Phase 5 — Procurement

- [ ] Implement purchase indents/requisitions and approval handoff.
- [ ] Implement RFQs, supplier invitations/quotes, comparison, and purchase orders.
- [ ] Implement purchase order status/history and receiving handoff to inventory.
- [ ] Test authorization, approval thresholds, duplicate submissions, and order-to-receipt references.

## Phase 6 — Inventory and stores

- [ ] Implement stock ledger, store balances, lot/serial traceability, and stock reservations.
- [ ] Implement receipt, issue, inter-store/inter-plant transfer, and cycle count workflows.
- [ ] Enforce no-negative-stock, FIFO valuation, and FEFO issue rules required by the BRD.
- [ ] Test concurrent movements, stock reconciliation, lot genealogy, and tenant/plant access.

## Phase 7 — Manufacturing and production

- [ ] Implement production plans/work orders using approved BOM and routing revisions.
- [ ] Implement material issue/return, operation progress, WIP, output, downtime, and rework records.
- [ ] Enforce route gates and compound maturation/expiry rules.
- [ ] Test work-order material/output reconciliation and end-to-end lot traceability.

## Phase 8 — Quality

- [ ] Implement inspection plans and incoming, in-process, and final inspection records.
- [ ] Implement non-conformance, disposition, corrective action, and quality evidence.
- [ ] Connect quality holds/releases to inventory and production workflows.
- [ ] Test that rejected/held lots cannot be issued or dispatched contrary to policy.

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
- [ ] Implement changes on a focused branch (for example `feature/master-data-plants`).
- [ ] Add/update migrations, API contract, validation, authorization, tenant scoping, and audit behavior where applicable.
- [ ] Run module tests and relevant regression/end-to-end tests; record the commands and results in the pull request.
- [ ] Review the diff for secrets, unrelated files, migration conflicts, and compatibility with dependent modules.
- [ ] Push the feature branch and open a pull request; request teammate/senior review.
- [ ] Merge only after approval and passing checks; pull the merged `main` locally.
- [ ] Update this roadmap in the same reviewed change or a small follow-up PR, then push it.

## Current next work

Finish Phase 1 before any business module. In order:

1. Review and merge the four open branches, in this order: `docs/module-status-board`, `feature/api-session-middleware`, `feature/web-mfa-flow`, `feature/db-migrations`. Then tick the items they cover.
2. Run and record the auth end-to-end tests (needs a mail inbox or the auth service's test mail mode).
3. Add shop-floor OTP / PIN sign-in before shop-floor users are onboarded.

Then begin Phase 2 (organization and master data). Tick items module by module only after tests pass and the reviewed change is merged.

## Progress log

- 2026-10-27: Phase 2 started on `feature/master-org`: plants, departments and stores APIs with shared master-data helpers; 36 API tests pass against a scratch database (two tenants, non-owner login). Not ticked until merged.
- 2026-10-27: Full-stack check of the combined branches (auth + API + web, real browser, no mocks): STORES signs in without TOTP; a non-member is refused; MD is sent to authenticator setup, enrols with a real TOTP code, and is then asked for a code at the next sign-in (wrong code rejected). Found and fixed on `fix/web-sign-out`: the "Sign out" link did not end the session.
- 2026-10-27: MFA policy decided (BRD 9.4). API session middleware and `/me` built on `feature/api-session-middleware`, awaiting review; not yet ticked.
- 2026-10-27: Added the module status board and two Phase 1 items (API session middleware, MFA policy) after reviewing the repository. No items newly ticked.
