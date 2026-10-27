# heyyERP Project Roadmap

This is the shared project checklist for the heyyERP backend. Work proceeds in phase order; a module is checked off only after its acceptance checks pass and its change is merged to `main`. Push each feature branch and open a pull request so the team can review it before merge.

## Progress key

- `[x]` Complete and confirmed.
- `[ ]` Not complete, or not yet verified.

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

Finish the Phase 1 security/integration checks first—especially the invitation-expiry mismatch, end-to-end auth verification, and trusted organization-to-tenant mapping. Then begin Phase 2 Organization and Master Data APIs. The Phase 2 checklist should be ticked module by module only after tests pass and the reviewed changes are merged.
