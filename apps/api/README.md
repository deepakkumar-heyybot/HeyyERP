# heyyERP API foundation

Initial FastAPI backend scaffold for the heyyERP project. It provides typed settings,
an async SQLAlchemy/PostgreSQL engine, tenant-scoped transaction setup for the planned
PostgreSQL RLS policy, versioned health routes, and the starting package structure.

## Local setup

Requires Python 3.12 and PostgreSQL 16. From this directory:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
uvicorn heyyerp_api.main:app --reload --app-dir src
```

Set `DATABASE_URL` in `.env` to the local PostgreSQL database. The default example is
for local development only. Create the `erp` schema using the project-level
`heyyERP_postgresql_schema.sql` before using the readiness endpoint.

## Initial routes

- `GET /api/v1/health/live` - process liveness; does not require PostgreSQL.
- `GET /api/v1/health/ready` - readiness check; verifies the PostgreSQL connection.
- `GET /api/v1/docs` - OpenAPI docs.

## Tenant security contract

Tenant-owned requests must use `get_tenant_session`. The authentication layer must set
`request.state.tenant_id` only after verifying the session and the user's tenant access.
The dependency starts a transaction and sets PostgreSQL's transaction-local
`app.tenant_id`, which is read by the RLS policies in the SQL schema. Do not accept a
tenant ID directly from an untrusted request header or query parameter.

## Database migrations

The schema is managed by Alembic (`apps/api/migrations`). Revision `0001` is a frozen copy of
`heyyERP_postgresql_schema.sql`; change the schema through new revisions, not by editing it.
Migrations run as the schema **owner** (`MIGRATION_DATABASE_URL`), never as the API's login.

```powershell
alembic upgrade head          # clean database: creates everything
alembic stamp 0001            # existing database that already has the baseline applied
```

Then create the API's non-owner login once per environment (revision `0002` creates the
`heyyerp_app_rw` group role with the grants; no passwords live in migrations):

```sql
CREATE ROLE heyyerp_app LOGIN PASSWORD '...' NOSUPERUSER NOBYPASSRLS IN ROLE heyyerp_app_rw;
```

If the Better Auth `auth` schema is created *after* `0002` ran, also run:
`GRANT USAGE ON SCHEMA auth TO heyyerp_app_rw; GRANT SELECT ON auth.member, auth.organization TO heyyerp_app_rw;`

## Roles, users and the first administrator

Roles, permissions, module access and the default approval matrix live in the database and are
written by a command that is safe to repeat (it only adds what is missing). After the auth
service has created its tables and `alembic upgrade head` has run:

```powershell
python -m heyyerp_api.cli seed --tenant v-rub-suspensions
python -m heyyerp_api.cli make-admin --tenant v-rub-suspensions --email person@vrub.example
```

`make-admin` needs the person to have signed up and joined the V-Rub organization first. After
that, administrators create other users through `POST /api/v1/admin/users` (email, role, plant
scope). A person without an ERP profile can sign in but every ERP endpoint answers 403.

## Tests

`pytest` runs the unit tests. The database tests (clean-database migration, tenant isolation
under row-level security) create and drop a scratch database and run only when
`TEST_ADMIN_DATABASE_URL` points at a login that may create databases and roles:

```powershell
$env:TEST_ADMIN_DATABASE_URL = "postgresql://postgres:<password>@localhost:5432/postgres"
pytest
```

## Next backend milestones

1. Implement tenant, unit, users, roles, permissions, settings, and audit services.
2. Implement workflow decisions and inventory ledger posting as transactional services.
3. Add procurement, quality, production, sales, and finance modules in dependency order.
