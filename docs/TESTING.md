# Testing: how we keep old work from breaking

## The rule

Every change that adds or alters behaviour ships with tests in the same pull request. A module is
not "done" (and its roadmap items are not ticked) until its tests pass together with all earlier tests.

## What guards what

| Guard | File | Fails when |
|---|---|---|
| Module tests | `apps/api/tests/test_<module>.py` | that module's behaviour changes |
| API surface | `tests/test_regression_guards.py` + `tests/snapshots/routes.json` | an endpoint is removed, renamed or added without a snapshot update |
| Anonymous access | same file | any endpoint other than health answers a caller who is not signed in |
| Permissions, roles, settings, numbering | `tests/snapshots/catalog.json` | any of them changes without a reviewed snapshot update |
| Typos in permissions | same file | code names a permission or module that does not exist |
| Database structure | same file | a tenant table lacks row-level security, the ledger stops being append-only, stock can go negative, or migrations fork |
| Cross-module flow | `tests/test_golden_path.py` | modules stop handing over to each other (purchase to finished goods) |
| CI | `.github/workflows/*.yml` | lint, types, any test or the web build fails on push or pull request |

## Running the tests

```powershell
cd apps/api
$env:TEST_ADMIN_DATABASE_URL = "postgresql://postgres:<password>@localhost:5432/postgres"
pytest -q                                   # about 25 minutes for everything
pytest -q tests/test_regression_guards.py   # seconds: run this after any change
pytest -q --cov=heyyerp_api --cov-report=term-missing:skip-covered   # coverage
```

Use a **local** PostgreSQL for `TEST_ADMIN_DATABASE_URL`, never the shared development database.
If a run is killed half way, remove the leftover scratch database and role before running again
(`python -c "from tests.conftest import API_DB, API_ROLE; from tests.dbutil import drop_database; drop_database(API_DB, API_ROLE)"`).

## When you add a feature

1. Write the module tests: happy path, each rule that can refuse, permissions (who may, who may
   not), plant scope, and any concurrency or reconciliation property.
2. Run `python -m tests.snapshots`, read the diff of `tests/snapshots/*.json`, and commit it with
   the feature. A reviewer should see exactly which endpoints and permissions were added.
3. If the feature extends the purchase-to-sales chain, extend `tests/test_golden_path.py`.
4. Add a migration test only through `tests/test_db.py` (head revision and table count) and let
   the guards check row-level security.
