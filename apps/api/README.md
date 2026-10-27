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

## Next backend milestones

1. Confirm authentication/session architecture and install local PostgreSQL.
2. Convert the reviewed SQL baseline into Alembic migrations and add migration checks.
3. Implement tenant, unit, users, roles, permissions, settings, and audit services.
4. Implement workflow decisions and inventory ledger posting as transactional services.
5. Add procurement, quality, production, sales, and finance modules in dependency order.
