# Running the backend locally so the frontend can log in

For the frontend developer on branch `Akshita-Dev` who only has `apps/web` and needs the backend (`apps/api`) and
the auth service (`apps/auth`) from `dev`, a working database connection, and a legitimate development user.

Two people are involved: **you** (the frontend developer) and **the backend owner** (Deepak). Steps are marked
accordingly. Nothing secret is stored in this document or in git.

## 0. The picture in one minute

```
Your browser  ->  Web app (apps/web, port 5173, from Akshita-Dev)
                    |-- sign in / sign up ----> Auth service (apps/auth, port 3001)   [runs on your machine]
                    |-- everything else ------> ERP API      (apps/api,  port 8000)   [runs on your machine]
                                                   |
                                                   v
                              Shared development database (PostgreSQL on Railway)
```

- The backend code is **not** copied into the frontend branch. You clone `dev` into a **separate folder** and only
  run it. You do not edit it.
- The database is shared and holds **development data only**. It is not production.
- You sign in with your **own account**. The backend owner gives that account an ERP role and plant scope. No one
  shares an admin password.

## 1. Tools you need (you)

| Tool | Version | Check |
|---|---|---|
| Git | any recent | `git --version` |
| Python | 3.12 | `py -3.12 --version` |
| Node.js | 22 or newer | `node --version` |

Free ports: 3001 (auth), 8000 (API), 5173 (web). Close anything already using them.

## 2. Get the backend code (you)

Keep it in a different folder from your frontend checkout.

```powershell
cd D:\work                       # any folder that is NOT your frontend folder
git clone https://github.com/deepakkumar-heyybot/HeyyERP.git heyyERP-backend
cd heyyERP-backend
git switch dev
```

You need read access to the repository. If the clone is refused, ask the backend owner to add you as a
collaborator (GitHub, Settings, Collaborators, read access is enough).

Do not commit or push from this folder.

## 3. Receive the environment files securely (backend owner sends, you receive)

Three files are needed. They contain database credentials and an auth secret, so:

- The backend owner sends them **one to one** (a password manager share, or a private direct message), never in the
  repository, a group chat, an email thread, a screenshot or a ticket.
- You save them in the places below and **never commit them** (the repository ignores `.env` files already).
- If a file is ever pasted somewhere public by mistake, tell the backend owner at once so the credentials can be rotated.

| You receive | Save it as | Used by |
|---|---|---|
| API settings | `heyyERP-backend\apps\api\.env` | ERP API |
| Auth settings | `heyyERP-backend\apps\auth\.env` | Auth service |
| (nothing secret) | `heyyERP-frontend\apps\web\.env` | Web app, copy from `.env.example` |

What the files contain (names only, the owner fills in the values):

- `apps/api/.env`: `DATABASE_URL` (a limited login, `heyyerp_app`, that cannot bypass tenant security),
  `AUTH_BASE_URL=http://localhost:3001`, `WEB_BASE_URL=http://localhost:5173`, `REQUIRED_ORG_SLUG`, `ORG_TENANT_MAP`,
  `DATA_ENCRYPTION_KEY` (encrypts bank account numbers; the same value for everyone who shares the database).
- `apps/auth/.env`: `DATABASE_URL`, `BETTER_AUTH_SECRET` (the **same value for everyone** who shares the database),
  `AUTH_BASE_URL`, `WEB_BASE_URL`, `PORT=3001`, `NODE_ENV=development`,
  `AUTH_SKIP_EMAIL_VERIFICATION=true` (development only, no verification mail is needed), `V_RUB_ORG_ID`,
  `V_RUB_ORG_NAME`, `V_RUB_ORG_SLUG`. The mail settings stay empty for now.
- `apps/web/.env`: `VITE_AUTH_URL=http://localhost:3001` and `VITE_API_URL=http://localhost:8000`.

**Database access.** You do not need your own database. The shared Railway database is reached through the
connection string inside the two files above. It is reachable from the internet, so there is no VPN or IP list to
set up. Do not use that connection string in any other tool, and do not run the test suite against it (the tests
create and delete their own scratch database and need a **local** PostgreSQL).

## 4. Start the backend (you)

Open **two** terminals.

Terminal 1, the ERP API:

```powershell
cd heyyERP-backend\apps\api
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn heyyerp_api.main:app --reload --app-dir src
```

Check: open `http://localhost:8000/api/v1/health/ready`. It should show `{"status":"ready","database":"connected"}`.

Terminal 2, the auth service:

```powershell
cd heyyERP-backend\apps\auth
npm install
npm run dev
```

Check: it prints `heyyERP auth listening on 3001`.

Do **not** run database migrations. The shared database is updated only by the backend owner.

## 5. Start the web app (you)

In your frontend checkout (`Akshita-Dev`):

```powershell
cd apps\web
npm install
Copy-Item .env.example .env      # then make sure VITE_AUTH_URL and VITE_API_URL are as in section 3
npm run dev
```

Open `http://localhost:5173`.

## 6. Create your own account (you)

1. Open `http://localhost:5173/register`.
2. Enter your full name, your work e-mail and a password of **at least 12 characters**. Choose it yourself and do not
   share it.
3. Create the account. No verification e-mail is sent in development; the account is ready at once. The notice on the
   page ("a verification link has been sent") is a generic message.
4. Send the backend owner **only the e-mail address you used**.

Until the owner gives that account an ERP profile, you can sign in but every ERP request answers **403**. That is
expected.

## 7. Give the account a role and plant scope (backend owner)

The owner needs the API and auth services running against the same shared database (the same two `.env` files),
and must be signed in as an administrator.

1. Sign in at `http://localhost:5173` as the administrator.
2. Open `http://localhost:8000/api/v1/docs` (Swagger). The sign-in cookie is shared with `localhost`, so the calls
   run as the administrator.
3. Find `GET /api/v1/org/units` and note the plant ids you want to allow (or use all plants).
4. Call `POST /api/v1/admin/users` with:

```json
{
  "email": "frontend.developer@company.com",
  "role_code": "PLANT_HEAD",
  "display_name": "Frontend Developer",
  "all_plants": true
}
```

   To limit the account to some plants use `"all_plants": false, "unit_ids": ["<plant uuid>", "<plant uuid>"]`
   instead.

Which role to give: **`PLANT_HEAD`** is the best default for building screens. It shows almost every module in the
menu (sales, procurement, inventory, manufacturing, quality, finance, approvals, reports) and does not force
two-factor sign-in. It can approve documents, so only use it on the development database.

Other roles for checking how a screen looks for different people (create extra accounts, one per role, or change
the role with `PATCH /api/v1/admin/users/{user_id}`): `SALES`, `PURCHASE`, `STORES`, `QA`, `PROD`, `ACCOUNTS`,
`OPERATOR`. `ADMIN`, `MD` and `ACCOUNTS` require two-factor authentication (an authenticator app).

The profile is read on every request, so the change takes effect immediately. To remove access later, `PATCH` the
user with `"status": "DEACTIVATED"`.

## 8. Sign in and check (you)

1. Sign in at `http://localhost:5173`.
2. In the browser, open `http://localhost:8000/api/v1/me`. You should see your e-mail, `role`, the list of
   `permissions` and `modules`, `all_plants` or `unit_ids`, and `"access_granted": true`.
3. Use `modules` to build the menu and `permissions` to show or hide buttons. The full guide, with every screen's
   endpoints and rules, is `docs/heyyERP-Frontend-Backend-Guide.docx` (also on your branch).

## 9. If something goes wrong

| What you see | Cause | Fix |
|---|---|---|
| "Failed to fetch" after sign-in | the ERP API is not running | start Terminal 1 (section 4) |
| Sign-in cannot reach the server | the auth service is not running | start Terminal 2 |
| `EADDRINUSE ... 3001` | another auth service already uses the port | close it, or stop the old process |
| 403 on every ERP screen | the account has no ERP profile yet | ask the backend owner (section 7) |
| 401 | session expired | sign in again |
| "Too many requests" on sign-in | the auth service allows 10 attempts per minute | wait a minute |
| `/health/ready` says the database is not connected | wrong `DATABASE_URL`, or no internet | check the `.env` values with the owner |
| Some finance screens return 500 or 404 | the shared database is behind the code (see below) | ask the owner to update it |
| Two-factor screen after sign-in | your role requires it | set up an authenticator app, and never share the QR code or backup codes |

**Database version.** The shared database is updated by the backend owner after new backend work is merged. If
`dev` is ahead of the database, the newest modules (currently finance) can fail until the owner migrates it. Everything
up to sales works.

## 10. Rules of the road

- Work only in `apps/web` on `Akshita-Dev`. Do not copy backend code into the frontend branch.
- Never commit `.env` files, passwords, connection strings or screenshots that show them.
- Use only your own account. Do not ask for, or use, the administrator's account.
- The data in the shared database is test data. Add your own records and leave other people's alone.
- Report a backend problem to the owner with the request, the response and the time; do not patch the backend.

## 11. Backend owner checklist

- [ ] Add the developer as a repository collaborator (read access).
- [ ] Send `apps/api/.env` and `apps/auth/.env` privately (two files, or one message each). The auth secret must be the
  same value as the one the shared database's accounts were created with.
- [ ] Ask for the developer's e-mail after they register.
- [ ] Create the profile with `POST /api/v1/admin/users` (section 7).
- [ ] Confirm with the developer that `/api/v1/me` shows `access_granted: true`.
- [ ] Keep the shared database migrated to the version on `dev`, and tell the team when it changes.
- [ ] Deactivate the account when the work ends.
