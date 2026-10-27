# heyyERP web authentication UI

Run `npm install`, then start the auth API in `apps/auth` and this Vite app here.
The UI uses the Better Auth client for email/password, verification resend, password
recovery, organization invitations, and TOTP.

For the UI E2E suite, keep the web app running at `http://127.0.0.1:5173` in one
terminal, then run `npm run test:e2e` in another. Auth endpoint responses are mocked in
these browser tests; they verify UI behavior and do not replace DB/provider-backed auth
integration tests.

The registration confirmation uses generic wording even for duplicate email attempts:
Better Auth intentionally prevents account enumeration while `requireEmailVerification`
is enabled. Duplicate identities are not created; existing users should sign in or use
password reset. This avoids exposing whether a particular email belongs to an account.
