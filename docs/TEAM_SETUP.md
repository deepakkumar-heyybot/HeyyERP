# Team setup: run the backend against the shared development database

The shared PostgreSQL database lives on Railway (project `gallant-quietude`, environment
`development`). It holds test data only. Everyone runs the API and auth service on their own machine
and points them at that database. Never commit a password; the `.env` files are git-ignored.

## 1. Get the connection settings (privately)

Ask the backend owner for two small files and save them as:

- `apps/api/.env` (API: the non-owner login `heyyerp_app`, so row-level security applies)
- `apps/auth/.env` (auth service)

They also need `AUTH_BASE_URL`, `WEB_BASE_URL`, `REQUIRED_ORG_SLUG` and `ORG_TENANT_MAP`, which the
owner's API file already contains. Local-only values (`PORT`, `AUTH_SKIP_EMAIL_VERIFICATION=true`
while mail is not set up) can be copied from `.env.example`.

## 2. Start the services

```powershell
# API
cd apps/api
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn heyyerp_api.main:app --reload --app-dir src      # http://localhost:8000/api/v1/docs

# Auth service (second terminal)
cd apps/auth
npm install
npm run dev                                              # http://localhost:3001
```

The web app (`apps/web`) talks to these two. Do not run the test suite against the shared database:
`pytest` creates its own scratch database and needs `TEST_ADMIN_DATABASE_URL` pointed at a local server.

## 3. Rules for a shared database

- Schema changes go through new Alembic revisions on `dev`. Only the owner runs
  `alembic upgrade head` against Railway, after the revision is merged.
- Do not delete other people's test data; add your own. Records are deactivated, never deleted.
- The `production` Railway environment is separate and is not used for development.
