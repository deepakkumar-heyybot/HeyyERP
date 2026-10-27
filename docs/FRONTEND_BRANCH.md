# Frontend branch (`Akshita-Dev`)

This branch holds only the web application (`apps/web`), the BRD, the prototype
(`tmp/heyyERP · V-Rub Suspensions.html`) and the project documents. The backend (API, auth service,
database migrations) lives on the `dev` branch and is not duplicated here, so the two never drift.

## Running the web app

```powershell
cd apps/web
npm install
Copy-Item .env.example .env      # VITE_AUTH_URL (auth service) and VITE_API_URL (ERP API)
npm run dev                      # http://localhost:5173
```

## Getting a backend to talk to

Ask the backend owner for the shared development environment (the API and auth services running
against the shared development database). Point `VITE_API_URL` and `VITE_AUTH_URL` in `apps/web/.env`
at them. The API's interactive documentation is at `<API URL>/api/v1/docs`; use it as the contract.

## Working rules

- Keep changes inside `apps/web/**`. Do not copy backend code into this branch.
- Open pull requests into `dev` when a screen is ready; the backend owner merges.
- Never commit `.env` files or credentials.
