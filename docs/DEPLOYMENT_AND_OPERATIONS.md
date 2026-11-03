# Deployment and operations

For whoever runs heyyERP in production (V-Rub IT or the hosting partner) and for the backend owner.
Targets that depend on V-Rub's answers (backup retention, who administers the server, domain) are marked
**[confirm S1/S2/S3]**; they are the items in `docs/VRUB_DECISIONS_AND_INPUTS.md`.

## 1. What runs

| Part | What | Port | Health |
|---|---|---|---|
| API | FastAPI (`apps/api`), image from `apps/api/Dockerfile` | 8000 | `GET /api/v1/health/live` (process is up), `GET /api/v1/health/ready` (database reachable) |
| Auth | Better Auth service (`apps/auth`) | 3001 | its own sign-in page |
| Web | React app (`apps/web`), static files | 80/443 | the page loads |
| Database | PostgreSQL 16 | 5432 | `pg_isready` |

Put the three web-facing parts behind one HTTPS reverse proxy with a valid certificate. The API trusts
the proxy headers (`--proxy-headers`). Only the proxy is reachable from outside; the database is not.

## 2. Settings (environment variables)

Secrets are marked **secret**: they live only in the host's secret store or a git-ignored `.env`, never in
git, chat or tickets.

| Variable | Meaning |
|---|---|
| `APP_ENV` | `production` in production (turns on HSTS, refuses the mock GST provider) |
| `DATABASE_URL` **secret** | API login `heyyerp_app` (not the owner), so row-level security applies |
| `MIGRATION_DATABASE_URL` **secret** | the schema owner; used only by `alembic`, never by the running API |
| `DATA_ENCRYPTION_KEY` **secret** | encrypts vendor bank account numbers; losing it makes them unreadable (section 6) |
| `ORG_TENANT_MAP`, `REQUIRED_ORG_SLUG` | which company the API serves |
| `AUTH_BASE_URL`, `WEB_BASE_URL` | public addresses; `WEB_BASE_URL` is also the only origin allowed by CORS and the base of links in messages |
| `LOG_LEVEL` | `INFO` normally |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | connection pool, 5 and 10 by default |
| `BACKGROUND_WORKER` | `true` on **one or more** API instances: runs notifications, SLA reminders, shift alerts and GST provider retries (a database lock makes sure only one worker works at a time) |
| `MAIL_PROVIDER`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` **secret**, `MAIL_FROM` | e-mail (`console` only logs) |
| `WHATSAPP_PROVIDER`, `WHATSAPP_TOKEN` **secret**, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_TEMPLATE` | WhatsApp Business (`console` only logs) |
| `GSP_PROVIDER`, `GSP_BASE_URL`, `GSP_TOKEN` **secret**, `GSP_IRN_PATH`, `GSP_IRN_CANCEL_PATH`, `GSP_EWB_PATH` | GST provider for e-invoice and e-way bill (`mock` is refused when `APP_ENV=production`) |

## 3. First deployment

1. Create the database and two logins: the owner (migrations) and `heyyerp_app` (the API, created as
   described in `apps/api/README.md`: no superuser, no bypass of row-level security).
2. Set the variables above on the host.
3. `alembic upgrade head` with `MIGRATION_DATABASE_URL` (from a machine that can reach the database).
4. Start the API image; check `/api/v1/health/ready`. Start auth and web.
5. Seed the company: the tenant row, roles and permissions come from the migrations; then the
   administrator's account, the plants, users and the master data in the decisions document.
6. Run the smoke test (section 8).

## 4. Releasing a new version

1. CI is green on the commit (lint, types, tests; `.github/workflows`). Tag it: `git tag v0.x.y`.
2. **Back up the database** (section 6) and note the current Alembic revision (`alembic current`).
3. Run `alembic upgrade head` with the owner login. Migrations are written to be applied while the old
   version is still running (they add, they do not remove); a migration that cannot be is announced in the
   release notes with a short maintenance window.
4. Deploy the new API image (one instance at a time if there are several), then auth and web.
5. Smoke test (section 8). Watch the logs and the job monitor for 30 minutes.

## 5. Rolling back

- **Code only (no migration in this release, or the migration only added things):** redeploy the previous
  image tag. The old code runs fine on a database that has extra columns and tables. This is the normal
  rollback.
- **A migration went wrong while running:** PostgreSQL runs the whole `alembic upgrade` in one
  transaction, so a failed run leaves the database as it was. Fix forward on a new revision.
- **Data is wrong after a release:** do not run `alembic downgrade` on live data (downgrades drop what the
  migration added, including data written since). Restore from the backup taken in step 2 of section 4
  into a new database, compare, and decide with the backend owner. Because the ledger and stock records
  are append-only, wrong postings are corrected by reversals, not by editing.

## 6. Backups and restore  **[confirm S2]**

Proposed until V-Rub decides: a full `pg_dump` every night, kept for 30 days, plus the first of each month
kept for 12 months, stored in a place other than the database server (another disk, another account or
region), with the managed database's own point-in-time recovery switched on if the host offers it
(this limits loss to minutes).

```text
pg_dump --format=custom --no-owner --file heyyerp-YYYYMMDD.dump "$BACKUP_DATABASE_URL"
pg_restore --no-owner --dbname "$SCRATCH_DATABASE_URL" heyyerp-YYYYMMDD.dump
```

- **Practice a restore every month** into a scratch database and check: `alembic current` equals production,
  the trial balance of the last closed month matches the report kept at month end, and stock on hand equals
  the sum of the stock ledger. A backup that has never been restored is not a backup.
- **Encryption key:** keep `DATA_ENCRYPTION_KEY` in the company's password vault, separately from the
  database backups. A restored database without the key opens, but vendor bank account numbers cannot be
  read.
- Uploaded files (kaizen photos, breakdown evidence) are stored in the database, so they are in the dump.
- Recovery targets (how much data may be lost, how long the system may be down) are V-Rub's call; the
  proposal above gives a loss of at most one day without point-in-time recovery.

## 7. Monitoring

What to watch and what each sign means:

| Where | Look for | Action |
|---|---|---|
| `/api/v1/health/ready` from the proxy or a monitoring service, every minute | not 200 | database unreachable: check the database, then the API |
| API log: one line per request `METHOD /path -> status ms rid=...` | many 5xx; slow requests (over 2000 ms) | find the `rid`, read the traceback logged with it; the user sees the same `request_id` in the error |
| `GET /api/v1/admin/jobs` (administrator) | notifications `FAILED`; relay failures; queue growing | wrong SMTP or WhatsApp credentials, or the worker is not running (`BACKGROUND_WORKER`) |
| `GET /api/v1/admin/gsp-jobs` | jobs `FAILED` or `RETRY` for long | the provider is down or rejects the data (the reason is on the job); fix and retry |
| Database | disk above 80%, connections near the limit, long-running queries | grow the disk; raise the pool or find the query |

Set alerts by e-mail or WhatsApp to the administrator for: readiness failing for 3 minutes, 5xx rate above
2% for 10 minutes, any `FAILED` GST job, disk above 80%, a failed nightly backup.

## 8. Smoke test after every release

1. `/api/v1/health/ready` answers 200; sign in; the menu loads.
2. Open the approvals inbox, a report (`/reports`) and download one as Excel.
3. Make a test purchase indent, submit it and check the approver is notified.
4. As the administrator open `/admin/jobs`: nothing failed in the last hour.
5. Check the release did not change the permission and route lists unexpectedly (the regression guards
   in CI cover this).

## 9. Security checklist before go-live  **[confirm S3]**

- [ ] HTTPS everywhere; HTTP redirects to HTTPS; the certificate renews itself.
- [ ] `APP_ENV=production`; `GSP_PROVIDER` is `http` (or e-invoice is off); no `console` mail provider.
- [ ] The API connects as `heyyerp_app`, not as the owner or `postgres`; the database is not reachable
      from the internet; the database password is not the development one.
- [ ] All secrets rotated from the development values; none in git (`git log -p | grep -i password`
      returns nothing real).
- [ ] Two-factor sign-in on for the Administrator, MD and Accounts roles (the rule is built in).
- [ ] Password policy and any IP restrictions set as decided in S3.
- [ ] `approvalByLink` stays off unless V-Rub accepts approving without signing in (W2).
- [ ] The development accounts and the test data are removed from the production database.
- [ ] A backup was restored successfully in the last 30 days.
- [ ] The decisions document is answered or its defaults accepted in writing.

## 10. What has not been tried yet

Be honest with go-live: these are written but could not be exercised without the real service.

- The Docker image build (no Docker engine was running when it was written).
- Sending through the company's SMTP server and WhatsApp Business account.
- The GST provider's real API (only the mock and a generic HTTP adapter exist; the exact paths follow the
  provider's document).
- Load beyond the test suite: no load test has been run yet (it is a release-check item still to do).
