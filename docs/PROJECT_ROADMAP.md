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
- [ ] Establish repeatable database migrations and a clean-database setup check.

## Phase 1 — Authentication and tenant security

- [x] Implement the Better Auth organization-level authentication features (confirmed complete by the project owner; implementation is present in `apps/auth`).
- [x] Configure email/password, email verification, password reset, TOTP two-factor authentication, and the Resend SMTP mailer in the auth service.
- [ ] Run and record end-to-end tests for sign-up, duplicate email, verification expiry/resend, sign-in, MFA, password reset, and sign-out.
- [ ] Reconcile invitation expiry: `apps/auth/README.md` says 10 minutes, while `apps/auth/src/auth.ts` currently sets 7 days; confirm the intended rule and align implementation and documentation.
- [ ] Connect verified Better Auth organization membership to the ERP `tenant_id` in trusted API session context.
- [ ] Enforce verified email, active organization membership, MFA status, role/permission, and tenant isolation on protected API requests; reject tenant IDs supplied directly by clients.
- [ ] Build the FastAPI session middleware that validates the Better Auth session and sets `request.state.tenant_id`. Built on `feature/api-session-middleware` (`security.py`, `GET /api/v1/me`, 11 unit tests, ruff and mypy clean); tick after review, merge and the live check below.
- [ ] Live-check the middleware. Column names verified on 2026-10-27 against the schema generated by `auth generate` (`auth.member."userId"`, `"organizationId"`, `role`; `auth.organization.slug`; `auth.user."emailVerified"`, `"twoFactorEnabled"`) and they match `security.py`. Still to do: run `npm run db:migrate` in `apps/auth` (the `auth` schema does not exist yet in database `HeyyERP`), then call `/api/v1/me` with a real session.
- [ ] Create the V-Rub `tenant` row (`erp.tenant` has 0 rows) and set `ORG_TENANT_MAP` in the API `.env` (organization slug to tenant UUID).
- [ ] Make the API connect as a non-owner PostgreSQL role (or `FORCE ROW LEVEL SECURITY`): confirmed on 2026-10-27 that the only login in use is the superuser `postgres`, which owns the `erp` tables and bypasses RLS (RLS is enabled on 124 tables), so the tenant policies currently protect nothing.
- [x] Decide the MFA policy: follow BRD section 9.4 — TOTP is mandatory only for ADMIN, MD and ACCOUNTS (decided by the project owner, 2026-10-27). `src/auth-contract.ts` now encodes this.
- [ ] Align the web onboarding flow with the MFA policy: `apps/web/src/App.tsx` still sends every user to `/totp-setup` after sign-in; make TOTP enrollment required only for the three roles, and update the matching sentence in `apps/auth/README.md`.
- [ ] Add shop-floor OTP / PIN sign-in (BRD FR-ADM-06 and FR-ADM-07) before shop-floor users are onboarded; until then they use verified email and password.
- [ ] Verify tenant isolation with automated API/database tests before allowing business-data writes.

## Phase 2 — Organization and master data

- [ ] Define the approved organization-to-tenant mapping and organization settings.
- [ ] Implement plants/business units with GSTIN, state, active status, and tenant-scoped unique codes.
- [ ] Implement departments, parent-department relationships, and plant association.
- [ ] Implement stores/warehouses under plants, including store type and active status.
- [ ] Implement shared reference data required by the BRD (for example UOM, item categories, payment terms, and tax codes) after scope review.
- [ ] Implement customer and vendor master records with validated identifiers and status; keep procurement/sales transaction behavior in their own modules.
- [ ] Add tenant-safe CRUD/list APIs, validation, permission checks, and audit events for approved master-data entities.
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

1. Fix the invitation-expiry mismatch (10 minutes in the README, 7 days in `apps/auth/src/auth.ts`).
2. Align the web sign-in flow with the MFA policy (decided: BRD section 9.4).
3. Review and merge the API session middleware (built), then run the live checks and create the tenant mapping.
4. Write the tenant-isolation tests, and run and record the auth end-to-end tests.
5. Add database migrations so a clean database can be built from the repository.

Then begin Phase 2 (organization and master data). Tick items module by module only after tests pass and the reviewed change is merged.

## Progress log

- 2026-10-27: MFA policy decided (BRD 9.4). API session middleware and `/me` built on `feature/api-session-middleware`, awaiting review; not yet ticked.
- 2026-10-27: Added the module status board and two Phase 1 items (API session middleware, MFA policy) after reviewing the repository. No items newly ticked.
