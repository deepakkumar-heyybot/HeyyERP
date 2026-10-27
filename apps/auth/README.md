# heyyERP authentication (V-Rub organization)

Better Auth is the central identity provider for the single V-Rub ERP organization. It
does not let users create organizations. Users are global identities (unique email) and
ERP access is granted through an invitation and organization membership/role. A
previously registered email must sign in or use password recovery; it must not create a
second identity.

## Security and flow

- Email/password sign-up is email-verified; verification and reset links expire after
  600 seconds. Verification can be resent through Better Auth's
  `/send-verification-email` endpoint.
- TOTP 2FA with backup codes is enabled by the Better Auth plugin. Product onboarding
  must require successful TOTP enrollment before ERP routes are accessible; the API
  must verify email, V-Rub membership, active organization, and `twoFactorEnabled` on
  every session. Frontend redirects alone are not an authorization control.
- Org creation is disabled. Admins invite members and assign roles; invitations stay
  valid for 7 days (604800 seconds) so a person can accept them in practice. Existing users accept the invitation with their existing account.
- Database tables live in PostgreSQL schema `auth`, isolated from the ERP `erp` schema.
- While the mail domain is not set up, set `AUTH_SKIP_EMAIL_VERIFICATION=true` in `.env`: new accounts
  are marked verified when they are created and no email is needed. The service refuses to start with
  this flag when `NODE_ENV=production`. Accounts created before the flag was turned on stay
  unverified; verify them with `make-admin --verify-email`.
- In local development, if `RESEND_SMTP_PASSWORD` is empty the service does not fail: it prints each
  email (including its verification or reset link) in the auth terminal so the link can be opened.
  In production the missing key is an error.
- Email uses Resend SMTP; no API key is stored in source. The sender address must be
  verified in the Resend account. Set all values from `.env.example` in an ignored `.env`.

## Local setup

Requires Node 20+ and PostgreSQL 16. Start PostgreSQL, create database `heyyerp`,
copy `.env.example` to `.env`, set a unique `BETTER_AUTH_SECRET`, then:

```powershell
npm install
npm run db:generate
npm run db:migrate
npm run dev
```

The auth server listens on `http://localhost:3001`; API routes are under `/api/auth/*`.
The React app should use `createAuthClient` with `organizationClient` and
`twoFactorClient`, `baseURL: http://localhost:3001`, and `credentials: "include"`.

## Important deployment inputs

Set `RESEND_SMTP_PASSWORD` to a Resend API key and verify `heyybot.com`/the exact
`deepak.kumar@heyybot.com` sender in Resend. Configure `AUTH_BASE_URL`, `WEB_BASE_URL`,
and trusted origins to the deployed HTTPS origins. The example local secret is for
development only and must never be deployed.
