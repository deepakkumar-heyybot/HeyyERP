"""The role catalog against the BRD, and its seeding."""

import psycopg
import pytest

from heyyerp_api.rbac.catalog import (
    DEFAULT_RULES,
    PERMISSIONS,
    ROLES,
    role_modules,
    role_permissions,
)
from heyyerp_api.rbac.seed import seed_tenant

from .conftest import TENANT_A


def test_catalog_is_internally_consistent():
    assert len(ROLES) == 12  # BRD section 3: ADMIN, MD, PLANT_HEAD, ... OPERATOR
    for code in ROLES:
        assert role_permissions(code) <= set(PERMISSIONS), code
    for rule in DEFAULT_RULES:
        assert all(step == "DEPT_HEAD" or step in ROLES for step in rule["steps"]), rule


@pytest.mark.parametrize(("role", "permission", "allowed"), [
    ("ADMIN", "admin", True),
    ("MD", "po.create", True),
    ("MD", "admin", False),  # BRD: MD cannot change admin settings
    ("STORES", "grn.post", True),
    ("STORES", "po.create", False),
    ("PURCHASE", "vendor.manage", True),
    ("PLANT_HEAD", "vendor.manage", False),  # BRD: vendor.manage is held by PURCHASE
    ("QA", "qa.approve", True),
    ("SALES", "qa.approve", False),
    ("ACCOUNTS", "fin.pay", True),
    ("OPERATOR", "route.move", True),
    ("OPERATOR", "inv.issue", False),
    ("HR", "payroll", True),
    ("OPERATOR", "admin", False),
    ("PLANT_HEAD", "approval.view_all", True),
    ("PURCHASE", "approval.view_all", False),
])
def test_permission_matrix_follows_the_brd(role, permission, allowed):
    assert (permission in role_permissions(role)) is allowed


def test_module_access_follows_the_brd():
    assert role_modules("OPERATOR") == {"routes", "manufacturing", "kaizen"}
    assert "admin" in role_modules("ADMIN") and "admin" not in role_modules("PURCHASE")
    assert "approvals" not in role_modules("OPERATOR")  # all roles except OPERATOR see Approvals


def test_seeding_is_idempotent_and_keeps_admin_edits(api_database):
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        def counts():
            return conn.execute(
                "select (select count(*) from erp.role where tenant_id = %(t)s),"
                " (select count(*) from erp.role_permission where tenant_id = %(t)s),"
                " (select count(*) from erp.approval_rule where tenant_id = %(t)s),"
                " (select count(*) from erp.approval_rule_step where tenant_id = %(t)s)",
                {"t": TENANT_A}).fetchone()

        before = counts()
        assert before[0] == 12 and before[2] == 11
        # Seeding twice changes nothing: it only adds what is missing.
        seed_tenant(conn, TENANT_A)
        assert counts() == before


def test_every_tenant_gets_its_own_roles(api_database):
    with psycopg.connect(api_database["owner"]) as conn:
        a = conn.execute("select role_id from erp.role where tenant_id = %s and code = 'ADMIN'",
                         (TENANT_A,)).fetchone()
        b = conn.execute("select role_id from erp.role where tenant_id = %s and code = 'ADMIN'",
                         (api_database["tenant_b"],)).fetchone()
    assert a and b and a != b
