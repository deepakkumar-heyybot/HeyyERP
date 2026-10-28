"""Regression guards: things that must not change by accident as the ERP grows.

If one of these fails after a deliberate change, regenerate the snapshots (`python -m
tests.snapshots`) and review the diff in the pull request. If it fails after an unrelated change,
the change broke something that used to work.
"""

import re
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from heyyerp_api.main import app
from heyyerp_api.rbac.catalog import (
    ALL_MODULES,
    DEFAULT_RULES,
    PERMISSIONS,
    ROLES,
    role_permissions,
)
from heyyerp_api.security import MFA_REQUIRED_ROLES
from heyyerp_api.workflow.registry import KNOWN_TYPES, REGISTRY

from . import snapshots
from .dbutil import alembic_config

SRC = Path(snapshots.__file__).parents[1] / "src" / "heyyerp_api"
HELP = "If this is intended, run `python -m tests.snapshots` and review the diff."


# ------------------------------------------------------------------ the API surface
def test_the_api_surface_matches_the_snapshot():
    now, before = set(snapshots.routes()), set(snapshots.load("routes"))
    assert not before - now, f"Endpoints disappeared: {sorted(before - now)}. {HELP}"
    assert not now - before, f"New endpoints without a snapshot entry: {sorted(now - before)}. {HELP}"


def test_every_endpoint_except_health_rejects_an_anonymous_caller():
    client = TestClient(app)
    answers = {}
    for operation in snapshots.routes():
        method, path = operation.split(" ", 1)
        url = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000001", path)
        body = {} if method in ("POST", "PUT", "PATCH") else None
        answers[operation] = client.request(method, url, json=body).status_code
    open_routes = {op for op, code in answers.items() if code != 401}
    assert open_routes == {"GET /api/v1/health/live", "GET /api/v1/health/ready"}, open_routes


def test_the_openapi_document_builds_with_unique_operation_ids():
    spec = app.openapi()
    ids = [op["operationId"] for ops in spec["paths"].values() for op in ops.values()
           if isinstance(op, dict) and "operationId" in op]
    assert len(ids) == len(set(ids)) and len(ids) >= 200


# ------------------------------------------------------------------ permissions, roles, settings
def test_permissions_roles_settings_and_numbering_match_the_snapshot():
    assert snapshots.catalog() == snapshots.load("catalog"), HELP


def test_every_permission_and_module_named_in_the_code_exists():
    code = "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py"))
    used = set(re.findall(r'requires\("([^"]+)"\)', code))
    used |= set(re.findall(r'has_permission\([^,]+,\s*"([^"]+)"\)', code))
    used |= set(re.findall(r'needed = "[^"]*" if [^\n]* else "([^"]+)"', code))
    assert used, "the scan found nothing; the pattern is stale"
    assert used - set(PERMISSIONS) == set(), f"Unknown permissions in code: {used - set(PERMISSIONS)}"
    modules = set(re.findall(r'requires_module\("([^"]+)"\)', code))
    assert modules - set(ALL_MODULES) == set()


def test_role_catalog_is_consistent():
    for role, spec in ROLES.items():
        assert role_permissions(role) <= set(PERMISSIONS), role
        modules = ALL_MODULES if spec["modules"] == "*" else spec["modules"]
        assert set(modules) <= set(ALL_MODULES), role
    assert role_permissions("ADMIN") == set(PERMISSIONS)
    assert "admin" not in role_permissions("MD") or role_permissions("MD") == set(PERMISSIONS)
    assert MFA_REQUIRED_ROLES == {"ADMIN", "MD", "ACCOUNTS"}  # BRD: MFA only for these roles


def test_default_approval_rules_use_known_roles_and_document_types():
    for rule in DEFAULT_RULES:
        assert rule["document_type"] in KNOWN_TYPES, rule["name"]
        assert rule["steps"], rule["name"]
        assert all(step in ROLES or step == "DEPT_HEAD" for step in rule["steps"]), rule["name"]


def test_every_module_that_needs_approval_registers_its_document_type():
    assert {"vendor", "bom", "indent", "po", "count"} <= set(REGISTRY)
    for doc_type in REGISTRY.values():
        assert doc_type.submit_permission in PERMISSIONS, doc_type.code


# ------------------------------------------------------------------ database structure
def test_migrations_form_one_unbroken_chain():
    script = ScriptDirectory.from_config(alembic_config())
    heads = script.get_heads()
    assert len(heads) == 1, f"Parallel migrations: {heads}"
    chain = []
    revision = script.get_revision(heads[0])
    while revision is not None:
        chain.append(revision.revision)
        revision = script.get_revision(revision.down_revision) if revision.down_revision else None
    chain.reverse()
    numbers = [int(r) for r in chain]
    assert numbers == list(range(1, len(chain) + 1)), f"Gap or reorder in {chain}"
    assert len(chain) >= 13


@pytest.mark.usefixtures("api_database")
class TestDatabaseStructure:
    def query(self, api_database, sql, params=None):
        import psycopg

        with psycopg.connect(api_database["owner"]) as conn:
            return conn.execute(sql, params).fetchall()

    def test_every_tenant_table_has_row_level_security_and_a_policy(self, api_database):
        missing = self.query(
            api_database,
            "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'erp' and c.relkind = 'r' and exists (select 1 from "
            "information_schema.columns k where k.table_schema = 'erp' and k.table_name = "
            "c.relname and k.column_name = 'tenant_id') and (not c.relrowsecurity or not exists "
            "(select 1 from pg_policy p where p.polrelid = c.oid)) order by 1")
        assert missing == [], f"Tables without row-level security: {missing}"

    def test_the_ledger_and_audit_log_are_append_only(self, api_database):
        tables = self.query(
            api_database,
            "select c.relname from pg_trigger t join pg_class c on c.oid = t.tgrelid join "
            "pg_proc p on p.oid = t.tgfoid where p.proname = 'prevent_immutable_row_change' "
            "and not t.tgisinternal order by 1")
        assert [t[0] for t in tables] == ["audit_log", "stock_ledger"]

    def test_posted_journals_are_protected_by_the_database(self, api_database):
        triggers = self.query(
            api_database,
            "select c.relname, t.tgname from pg_trigger t join pg_class c on c.oid = t.tgrelid "
            "where t.tgname in ('journal_guard', 'journal_line_guard') and not t.tgisinternal "
            "order by 1")
        assert triggers == [("journal", "journal_guard"), ("journal_line", "journal_line_guard")]

    def test_stock_cannot_go_negative_in_the_database_itself(self, api_database):
        checks = self.query(
            api_database,
            "select pg_get_constraintdef(oid) from pg_constraint where conrelid = "
            "'erp.stock_balance'::regclass and contype = 'c'")
        text = " ".join(c[0] for c in checks)
        assert "quantity_on_hand >= (0)" in text and "quantity_reserved <= quantity_on_hand" in text

    def test_views_respect_row_level_security(self, api_database):
        views = self.query(
            api_database,
            "select c.relname, coalesce(c.reloptions::text, '') from pg_class c join pg_namespace "
            "n on n.oid = c.relnamespace where n.nspname = 'erp' and c.relkind = 'v' and "
            "c.relname like 'v\\_%'")
        assert views and all("security_invoker=true" in options for _, options in views)

    def test_the_api_login_cannot_bypass_row_level_security(self, api_database):
        from .conftest import API_ROLE

        flags = self.query(api_database, "select rolsuper, rolbypassrls from pg_roles where "
                           "rolname = %s", (API_ROLE,))
        assert flags == [(False, False)]
        owner = self.query(api_database, "select count(*) from pg_tables where schemaname = 'erp' "
                           "and tableowner = %s", (API_ROLE,))
        assert owner == [(0,)]  # the API never owns a table, so RLS applies to it
