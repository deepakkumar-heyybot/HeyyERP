# heyyERP frontend handover

Prepared 14 November 2026 for the Akshita-Dev branch. This standalone frontend is
added under frontend/; the branch's existing apps/ and backend files are unchanged.
The original development workspace and its unfinished work are preserved locally.
API contracts were checked against the separate backend dev checkout at
e055814e4367c9c69a5b5e1421ff2109175089b9. This frontend-only commit does not
upgrade the older backend already present on Akshita-Dev; use the compatible dev
backend separately for integration and confirm deployed contracts before live use.

## Delivered scope

The saved acceptance checkpoint records 45 of 121 pages (37.2%). This handover
includes their implementation and relevant tests. Register/create/detail/editor
states can share a component; page IDs follow the approved roadmap rather than
counting each URL as another completed page.

| Page | Included implementation |
|---|---|
| FND-01 | Sign in |
| FND-02 | Registration and invitation |
| FND-03 | Recovery and reset |
| FND-04 | Email verification |
| FND-05 | MFA setup and challenge |
| FND-06 | ERP access gate |
| FND-07 | Permission-aware shell and supported dashboard |
| FND-08 | Standard error pages and request-ID handling |
| APR-01 | Approval inbox |
| APR-02 | Approval detail |
| NOT-01 | Notifications |
| APR-04 | Public approval |
| ORG-02 | Departments |
| ORG-03 | Stores |
| REF-01 | Reference Data |
| PTY-01 | Customers |
| PTY-03 | Vendors |
| PTY-04 | Vendor detail |
| ENG-01 | Items |
| ENG-02 | Item detail |
| ENG-03 | BOM list |
| ENG-04 | BOM detail / revision |
| ENG-05 | Route list |
| ENG-06 | Route detail |
| PRC-01 | Indent list |
| PRC-02 | Indent detail |
| PRC-03 | RFQ list |
| PRC-04 | RFQ comparison |
| PRC-05 | Purchase order list |
| PRC-06 | Purchase order detail |
| INV-01 | Goods receipt register/create |
| INV-02 | GRN detail / incoming QA |
| INV-03 | Stock explorer |
| INV-04 | Lot detail / trace |
| INV-05 | Issue planner |
| INV-07 | Reservations |
| MFG-04 | Batch execution |
| MFG-08 | Production-lot register/create |
| MFG-09 | Production-lot detail/execution |
| MFG-10 | Shift entries |
| MFG-11 | WIP / OEE |
| QLT-01 | QA queue |
| QLT-02 | Inspection plans |
| QLT-03 | Inspections |
| QLT-04 | Quality holds |

Shared support includes AuthCard, Brand, access gate/AppShell, permission-aware
navigation, active-plant context, the five PageTemplates, RequestError, domain
tabs, the existing typed ERP/Auth clients, decimal-preserving forms and dialogs,
reference selectors, styles/tokens, Vite/TypeScript setup and the locked dependency tree.
Read-only batch selection is retained to reach completed MFG-04 execution;
read-only customer master summary supports completed PTY-01 navigation/creation.
These supporting views do not claim MFG-03 or PTY-02 full-page completion.

## Run locally

From this frontend directory, use the existing package lock with npm ci on a new
colleague checkout, then npm run dev. The dev server uses port 5173. No dependency
installation was performed during preparation of this handover.

The only browser configuration variable names are VITE_API_URL and VITE_AUTH_URL.
They have local defaults in the clients. No environment file or credential is included.
For real login and data, configure approved API/Auth origins, compatible cookies/CORS,
running backend services and an authorized development account with the required
module, permission and plant grants. No test fixture is a real development account.

## Verification

The browser suite uses backend-contract network fixtures; it does not establish
live database readiness, email delivery or live ERP mutations. No database action
or backend service startup was performed for this handover. Synthetic fixture
accounts and tokens are test data only.

- npm run typecheck
- npm run build
- npm run test:e2e -- --workers=4

Playwright's bundled Chromium is absent on this laptop. Verification instead uses
the already installed Microsoft Edge runtime, with this PowerShell setting:

    $env:PLAYWRIGHT_CHANNEL = 'msedge'
    npm run test:e2e -- --workers=4

Without that optional setting, the config retains the default Playwright Chromium
runtime. No browser download, optional package or unrelated dependency was installed.
The verified toolchain is Node 24.18.0, npm 11.16.0 and Playwright 1.64.1.
The test server builds and serves the production app with Vite preview, avoiding
development-module loading overhead. Test and assertion timeouts remain unchanged;
the server startup budget accommodates the build on a fresh checkout.

Final run results are recorded in VERIFICATION.md after the checks finish.

## Included test files

- e2e/approvals-notifications.spec.ts
- e2e/auth-ui.spec.ts
- e2e/batch-detail.spec.ts
- e2e/compound-batches.spec.ts
- e2e/department-filter-history.spec.ts
- e2e/handover-scope.spec.ts
- e2e/inspection-plans.spec.ts
- e2e/inventory.spec.ts
- e2e/item-filter-history.spec.ts
- e2e/manufacturing-performance.spec.ts
- e2e/master-data-boms.spec.ts
- e2e/master-data-customers.spec.ts
- e2e/master-data-items.spec.ts
- e2e/master-data-organization.spec.ts
- e2e/master-data-reference.spec.ts
- e2e/master-data-routes.spec.ts
- e2e/master-data-vendors.spec.ts
- e2e/plant-navigation.spec.ts
- e2e/procurement.spec.ts
- e2e/production-lot-create.spec.ts
- e2e/production-lot-detail.spec.ts
- e2e/production-lots.spec.ts
- e2e/quality-holds.spec.ts
- e2e/quality-inspections.spec.ts
- e2e/quality-queue.spec.ts
- e2e/request-errors.spec.ts
- e2e/reservation-access.spec.ts
- e2e/reservation-layout.spec.ts
- e2e/reservations.spec.ts
- e2e/shift-entries.spec.ts

Existing retained test bodies/assertions are preserved except one foundation
navigation assertion points to the included Batches view instead of the excluded
Compounds view; its module-access/navigation checks remain. Shared request-error
tests use the included Quality Holds endpoint instead of excluded NCRs, keeping
their copy/error assertions unchanged. New handover-scope tests verify excluded
workflows make no feature requests. Tests for omitted workflows remain intact in
the original development workspace; none are skipped or expected-failure in this suite.
Two Auth fixtures are corrected for production testing: callback assertions wait
for the actual request, and conflict recovery uses one failure then success rather
than assuming development StrictMode's duplicate initial requests. Existing outcome
assertions and timeouts are retained.
Shared fixture nullability annotations were corrected; the emitted JavaScript runtime
tokens are identical to the original (formatting differs). Quality inspection/hold forms explicitly retain
client-error request IDs through the existing shared error component.

## Not delivered / remaining work

76 roadmap pages are not claimed complete. This commit intentionally excludes:

- ORG-01 plant/GST/address editing and PTY-02 full customer detail: missing saved-address read contracts.
- INV-06 issue history/detail: missing lot IDs and typed source-reference targets.
- INV-08/09 transfers: missing date/discovery requirements; INV-10/11 count workflows,
  including the explicitly blocked count editor/navigation warning; INV-12 inventory reports.
- MFG-01/02 compound workflows; MFG-03 weighing and full register acceptance;
  MFG-05 equipment; MFG-06/07 work orders; MFG-12 missing date/severity requirements.
- QLT-05 NCR/8D and QLT-06 instruments/calibration: incomplete history requirements.
- SAL-01 through SAL-06: partial implementation/acceptance. Native zoom checks were
  pending; order warnings lack a read contract. SAL-06 has a backend plant-scope gap;
  the approved all-plants-only frontend restriction is not implemented in this handover.
- Remaining Sales/dispatch, Finance/GST, HR, Maintenance, Kaizen, reports and administration waves.

Excluded known routes show an explicit handover-scope notice. No unfinished editor,
Sales mutation, unsupported Delete control or OpenDesign experiment is included.
Some reference links identify future workflows; those routes do not claim completion.
Keep backend contracts, exact decimal strings and backend permission/settings decisions
authoritative when continuing development. Obtain representative live accounts and
confirm API/Auth/database readiness separately before live acceptance.

## Preservation and branch safety

Only frontend files are added to Akshita-Dev. No dev/main merge, reset, clean,
force-push, backend/configuration/migration change, environment file, secret,
node_modules, build output, test artifact or experimental design is included.
Local preparation and raw test reports remain outside the commit. Original source,
tests and configuration were checked against 167 SHA256 snapshots before publication.
