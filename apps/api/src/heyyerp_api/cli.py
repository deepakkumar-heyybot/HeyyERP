"""Operator commands, run as the schema owner (MIGRATION_DATABASE_URL).

  python -m heyyerp_api.cli seed --tenant v-rub-suspensions
  python -m heyyerp_api.cli make-admin --tenant v-rub-suspensions --email someone@vrub.example
"""

import argparse

import psycopg

from heyyerp_api.config import get_settings
from heyyerp_api.rbac.seed import apply_auth_grants, create_app_user, seed_tenant


def connect() -> psycopg.Connection:
    url = get_settings().migration_database_url
    if not url:
        raise SystemExit("Set MIGRATION_DATABASE_URL to a role that owns the erp schema.")
    return psycopg.connect(url.replace("+asyncpg", ""))


def tenant_id_for(conn: psycopg.Connection, code: str) -> str:
    row = conn.execute("SELECT tenant_id FROM erp.tenant WHERE code = %s", (code,)).fetchone()
    if row is None:
        raise SystemExit(f"No tenant with code {code!r}")
    return str(row[0])


def main() -> None:
    parser = argparse.ArgumentParser(prog="heyyerp_api.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed", help="Create roles, permissions, default rules")
    seed.add_argument("--tenant", required=True, help="tenant code")
    admin = commands.add_parser("make-admin", help="Give an existing sign-in the ADMIN role")
    admin.add_argument("--tenant", required=True)
    admin.add_argument("--email", required=True)
    args = parser.parse_args()

    with connect() as conn:
        tenant_id = tenant_id_for(conn, args.tenant)
        if args.command == "seed":
            seed_tenant(conn, tenant_id)
            apply_auth_grants(conn)
            print(f"Seeded roles, permissions and default approval rules for {args.tenant}.")
            return
        user = conn.execute(
            'SELECT id, name FROM auth."user" WHERE lower(email) = lower(%s)', (args.email,)
        ).fetchone()
        if user is None:
            raise SystemExit("No sign-in with that email. The person must sign up first.")
        member = conn.execute(
            'SELECT 1 FROM auth.member m JOIN auth.organization o ON o.id = m."organizationId" '
            'WHERE m."userId" = %s AND o.slug = %s',
            (user[0], get_settings().required_org_slug),
        ).fetchone()
        if member is None:
            raise SystemExit("That person is not a member of the V-Rub organization yet.")
        create_app_user(conn, tenant_id, user[0], "ADMIN", user[1], args.email)
        print(f"{args.email} is now an ADMIN.")


if __name__ == "__main__":
    main()
