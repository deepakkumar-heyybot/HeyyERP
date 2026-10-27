"""Write the role catalog and default approval matrix into the database.

Synchronous psycopg on purpose: it runs from the CLI and from tests as the schema owner, never
inside the API. Everything is additive and idempotent, so it is safe to run again after the
catalog grows, and it never removes what an administrator changed.
"""

from typing import Any
from uuid import UUID

import psycopg

from heyyerp_api.rbac.catalog import (
    DEFAULT_COMPOUNDS,
    DEFAULT_ROUTES,
    DEFAULT_RULES,
    PERMISSIONS,
    ROLES,
    role_modules,
    role_permissions,
)


def seed_permissions(conn: psycopg.Connection[Any]) -> None:
    for code, description in PERMISSIONS.items():
        conn.execute(
            "INSERT INTO erp.permission(code, description) VALUES (%s, %s) "
            "ON CONFLICT (code) DO NOTHING",
            (code, description),
        )


def seed_roles(conn: psycopg.Connection[Any], tenant_id: UUID | str) -> dict[str, UUID]:
    seed_permissions(conn)
    role_ids: dict[str, UUID] = {}
    for code, spec in ROLES.items():
        row = conn.execute(
            "INSERT INTO erp.role(tenant_id, code, name, is_system) VALUES (%s, %s, %s, true) "
            "ON CONFLICT (tenant_id, code) DO UPDATE SET code = EXCLUDED.code RETURNING role_id",
            (tenant_id, code, spec["name"]),
        ).fetchone()
        assert row is not None
        role_ids[code] = row[0]
        conn.execute(
            "INSERT INTO erp.role_permission(tenant_id, role_id, permission_id) "
            "SELECT %s, %s, permission_id FROM erp.permission WHERE code = ANY(%s) "
            "ON CONFLICT DO NOTHING",
            (tenant_id, row[0], sorted(role_permissions(code))),
        )
        conn.execute(
            "INSERT INTO erp.role_module(tenant_id, role_id, module_code) "
            "SELECT %s, %s, unnest(%s::text[]) ON CONFLICT DO NOTHING",
            (tenant_id, row[0], sorted(role_modules(code))),
        )
    return role_ids


def seed_default_rules(
    conn: psycopg.Connection[Any], tenant_id: UUID | str, role_ids: dict[str, UUID]
) -> None:
    for rule in DEFAULT_RULES:
        if conn.execute(
            "SELECT 1 FROM erp.approval_rule WHERE tenant_id = %s AND name = %s",
            (tenant_id, rule["name"]),
        ).fetchone():
            continue
        sla = conn.execute(
            "INSERT INTO erp.sla_policy(tenant_id, name, hours, reminder_config, "
            "escalation_config) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb) RETURNING sla_policy_id",
            (tenant_id, rule["name"], rule["sla_hours"], '{"at_percent": [50, 90]}',
             '{"on_breach": "next_level"}'),
        ).fetchone()
        assert sla is not None
        created = conn.execute(
            "INSERT INTO erp.approval_rule(tenant_id, name, sla_policy_id, document_type, "
            "amount_min, amount_max, priority) VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "RETURNING rule_id",
            (tenant_id, rule["name"], sla[0], rule["document_type"], rule.get("amount_min"),
             rule.get("amount_max"), rule["priority"]),
        ).fetchone()
        assert created is not None
        for sequence, step in enumerate(rule["steps"], start=1):
            if step == "DEPT_HEAD":
                kind, role_id = "DEPARTMENT_HEAD", None
            else:
                kind, role_id = "ROLE", role_ids[step]
            conn.execute(
                "INSERT INTO erp.approval_rule_step(tenant_id, rule_id, sequence_no, "
                "approver_kind, role_id) VALUES (%s, %s, %s, %s, %s)",
                (tenant_id, created[0], sequence, kind, role_id),
            )


def seed_default_routes(conn: psycopg.Connection[Any], tenant_id: UUID | str) -> None:
    for code, spec in DEFAULT_ROUTES.items():
        if conn.execute(
            "SELECT 1 FROM erp.route WHERE tenant_id = %s AND code = %s", (tenant_id, code)
        ).fetchone():
            continue
        route = conn.execute(
            "INSERT INTO erp.route(tenant_id, code, name, version) VALUES (%s, %s, %s, 1) "
            "RETURNING route_id", (tenant_id, code, spec["name"]),
        ).fetchone()
        assert route is not None
        last = len(spec["stages"])
        for sequence, (stage_code, stage_name, qa_gate) in enumerate(spec["stages"], start=1):
            conn.execute(
                "INSERT INTO erp.route_stage(tenant_id, route_id, sequence_no, stage_code, "
                "stage_name, is_qa_gate, is_end) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (tenant_id, route[0], sequence, stage_code, stage_name, qa_gate, sequence == last),
            )


def seed_default_compounds(conn: psycopg.Connection[Any], tenant_id: UUID | str) -> None:
    for code, name, lane, minimum, maximum, low, high in DEFAULT_COMPOUNDS:
        conn.execute(
            "INSERT INTO erp.compound(tenant_id, code, name, lane, maturation_min_hours, "
            "maximum_age_hours, hardness_min, hardness_max) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (tenant_id, code) DO NOTHING",
            (tenant_id, code, name, lane, minimum, maximum, low, high),
        )


def seed_tenant(conn: psycopg.Connection[Any], tenant_id: UUID | str) -> None:
    role_ids = seed_roles(conn, tenant_id)
    seed_default_rules(conn, tenant_id, role_ids)
    seed_default_routes(conn, tenant_id)
    seed_default_compounds(conn, tenant_id)


def create_app_user(
    conn: psycopg.Connection[Any],
    tenant_id: UUID | str,
    auth_subject: str,
    role_code: str,
    display_name: str,
    email: str | None = None,
) -> UUID:
    """Give a Better Auth account an ERP profile (idempotent on the account)."""
    row = conn.execute(
        "INSERT INTO erp.app_user(tenant_id, auth_subject, role_id, display_name, email) "
        "SELECT %s, %s, role_id, %s, %s FROM erp.role WHERE tenant_id = %s AND code = %s "
        "ON CONFLICT (tenant_id, auth_subject) DO UPDATE SET role_id = EXCLUDED.role_id, "
        "status = 'ACTIVE' RETURNING user_id",
        (tenant_id, auth_subject, display_name, email, tenant_id, role_code),
    ).fetchone()
    if row is None:
        raise SystemExit(f"Role {role_code} does not exist for this tenant; run seed first.")
    return UUID(str(row[0]))


def apply_auth_grants(conn: psycopg.Connection[Any]) -> None:
    """Let the API login read what it needs from the Better Auth schema, if it exists yet.

    Migrations 0002 and 0005 do this only when the auth tables already exist. Run it again
    (the `seed` command does) after the auth service has created its tables.
    """
    conn.execute(
        """
        DO $$ BEGIN
          IF to_regclass('auth.member') IS NOT NULL AND to_regclass('auth.organization') IS NOT NULL
             AND to_regclass('auth."user"') IS NOT NULL THEN
            GRANT USAGE ON SCHEMA auth TO heyyerp_app_rw;
            GRANT SELECT ON auth.member, auth.organization TO heyyerp_app_rw;
            GRANT SELECT (id, email, name) ON auth."user" TO heyyerp_app_rw;
          END IF;
        END $$;
        """
    )
