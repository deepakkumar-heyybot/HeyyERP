# heyyERP team contribution contract

## Module ownership

- Authentication: `apps/auth/**` and `src/auth-contract.ts`.
- ERP HTTP API: `apps/api/**`.
- Web application: `apps/web/**`.
- Canonical ERP schema/migrations: `heyyERP_postgresql_schema.sql` and future
  `apps/api/migrations/**` (coordinate changes with API/database owner).
- Do not edit another module's package/config files in parallel without coordinating.

## Before starting a module

Write down its owner, user stories/BRD scope, files it owns, API request/response
contract, tables and migration owner, authorization roles, tenant/unit boundaries,
acceptance criteria, and test command. Use a separate feature branch and pull request
once Git is initialized. Never commit `.env`, API keys, SMTP secrets, user data, or
production database dumps. Do not make concurrent edits to a shared migration or root
config; ask the schema owner to sequence those changes.

## Completion checklist

Run the module's type/lint/unit/integration tests, document env vars and migrations,
review tenant/RLS enforcement, and describe any unimplemented contract in the PR.
