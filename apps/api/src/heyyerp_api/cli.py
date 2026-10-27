"""Operator commands, run as the schema owner (MIGRATION_DATABASE_URL).

  python -m heyyerp_api.cli seed --tenant v-rub-suspensions
  python -m heyyerp_api.cli make-admin --tenant v-rub-suspensions --email you@vrub.example

make-admin needs the person to have signed up first. For the very first administrator nobody
can invite them yet, so two explicit options exist: --join-org adds them to the V-Rub
organization, and --verify-email marks their address verified (for setups where the mail
service is not configured yet). Both are meant for bootstrapping only.
"""

import argparse
from typing import Any

import psycopg

from heyyerp_api.config import get_settings
from heyyerp_api.rbac.seed import apply_auth_grants, create_app_user, seed_tenant

ORG_ID, ORG_NAME = "vrub", "V-Rub Suspensions"


def connect() -> psycopg.Connection[Any]:
    url = get_settings().migration_database_url
    if not url:
        raise SystemExit("Set MIGRATION_DATABASE_URL to a role that owns the erp schema.")
    return psycopg.connect(url.replace("+asyncpg", ""))


def tenant_id_for(conn: psycopg.Connection[Any], code: str) -> str:
    row = conn.execute("SELECT tenant_id FROM erp.tenant WHERE code = %s", (code,)).fetchone()
    if row is None:
        raise SystemExit(f"No tenant with code {code!r}")
    return str(row[0])


def make_admin(
    conn: psycopg.Connection[Any],
    tenant_id: str,
    email: str,
    join_org: bool = False,
    verify_email: bool = False,
) -> list[str]:
    """Give an existing sign-in the ADMIN role. Returns what was done, for the operator."""
    done: list[str] = []
    slug = get_settings().required_org_slug
    user = conn.execute(
        'SELECT id, name FROM auth."user" WHERE lower(email) = lower(%s)', (email,)
    ).fetchone()
    if user is None:
        raise SystemExit("No sign-in with that email. The person must sign up first.")
    if verify_email:
        conn.execute('UPDATE auth."user" SET "emailVerified" = true WHERE id = %s', (user[0],))
        done.append("email marked as verified")
    org = conn.execute("SELECT id FROM auth.organization WHERE slug = %s", (slug,)).fetchone()
    if org is None and join_org:
        conn.execute(
            'INSERT INTO auth.organization(id, name, slug, "createdAt") VALUES (%s, %s, %s, now())',
            (ORG_ID, ORG_NAME, slug),
        )
        org = (ORG_ID,)
        done.append(f"organization {slug} created")
    member = org is not None and conn.execute(
        'SELECT 1 FROM auth.member WHERE "userId" = %s AND "organizationId" = %s', (user[0], org[0])
    ).fetchone()
    if not member:
        if not join_org or org is None:
            raise SystemExit(
                "That person is not a member of the V-Rub organization yet "
                "(add --join-org to add them as its first member)."
            )
        conn.execute(
            'INSERT INTO auth.member(id, "organizationId", "userId", role, "createdAt") '
            "VALUES (%s, %s, %s, 'owner', now())",
            (f"member-{user[0]}", org[0], user[0]),
        )
        done.append("added to the organization as owner")
    create_app_user(conn, tenant_id, user[0], "ADMIN", user[1], email)
    done.append("given the ADMIN role")
    return done


def main() -> None:
    parser = argparse.ArgumentParser(prog="heyyerp_api.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed", help="Create roles, permissions, default rules")
    seed.add_argument("--tenant", required=True, help="tenant code")
    admin = commands.add_parser("make-admin", help="Give an existing sign-in the ADMIN role")
    admin.add_argument("--tenant", required=True)
    admin.add_argument("--email", required=True)
    admin.add_argument("--join-org", action="store_true",
                       help="add them to the V-Rub organization (first administrator only)")
    admin.add_argument("--verify-email", action="store_true",
                       help="mark their email verified (when the mail service is not set up)")
    args = parser.parse_args()

    with connect() as conn:
        tenant_id = tenant_id_for(conn, args.tenant)
        if args.command == "seed":
            seed_tenant(conn, tenant_id)
            apply_auth_grants(conn)
            print(f"Seeded roles, permissions and default approval rules for {args.tenant}.")
            return
        for step in make_admin(conn, tenant_id, args.email, args.join_org, args.verify_email):
            print(f"- {step}")
        print(f"{args.email} is now an ADMIN. Next: sign in at the web app and set up 2FA.")


if __name__ == "__main__":
    main()
