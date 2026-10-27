"""Administration of ERP users: profiles, roles, plant scope, deactivation."""

from uuid import uuid4

import psycopg
import pytest

pytestmark = pytest.mark.usefixtures("api_database")


def account(api_database, member=True):
    """A Better Auth sign-in (and optionally V-Rub membership) as the auth service would store it."""
    uid, email = f"auth-{uuid4().hex[:10]}", f"{uuid4().hex[:8]}@vrub.example"
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute('insert into auth."user"(id, name, email, "emailVerified") values (%s,%s,%s,true)',
                     (uid, "New Person", email))
        if member:
            conn.execute('insert into auth.member(id, "organizationId", "userId", role) '
                         "values (%s, 'org-1', %s, 'member')", (f"m-{uid}", uid))
    return uid, email


def code() -> str:
    return uuid4().hex[:10].upper()


def test_admin_gives_an_account_an_erp_profile(make_client, api_database):
    uid, email = account(api_database)
    admin = make_client("ADMIN")
    created = admin.post("/api/v1/admin/users", json={
        "email": email, "role_code": "stores", "all_plants": True, "phone": "9999900000"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["role"] == "STORES" and body["status"] == "ACTIVE" and body["all_plants"] is True
    assert body["display_name"] == "New Person" and body["email"] == email
    # The new person can now sign in to the ERP with exactly the STORES permissions.
    user = make_client("STORES", subject=uid, provision=False)
    me = user.get("/api/v1/me").json()
    assert me["role"] == "STORES" and "grn.post" in me["permissions"] and "admin" not in me["permissions"]
    assert user.get("/api/v1/admin/users").status_code == 403


def test_account_checks(make_client, api_database):
    admin = make_client("ADMIN")
    post = lambda **b: admin.post("/api/v1/admin/users", json={  # noqa: E731
        "role_code": "STORES", "all_plants": True, **b})
    assert post(email="nobody@vrub.example").status_code == 404
    _, outsider = account(api_database, member=False)
    assert post(email=outsider).status_code == 422
    _, email = account(api_database)
    assert post(email=email, role_code="WIZARD").status_code == 422
    assert post(email=email).status_code == 201
    assert post(email=email).status_code == 409  # one profile per account
    assert post(email="not-an-email").status_code == 422


def test_plant_scope_is_required_and_applied(make_client, api_database):
    admin = make_client("ADMIN")
    unit = admin.post("/api/v1/org/units", json={"code": code(), "name": "Scoped plant"}).json()
    _, email = account(api_database)
    body = {"email": email, "role_code": "STORES"}
    assert admin.post("/api/v1/admin/users", json=body).status_code == 422  # neither
    both = admin.post("/api/v1/admin/users", json={**body, "all_plants": True,
                                                   "unit_ids": [unit["unit_id"]]})
    assert both.status_code == 422
    bogus = admin.post("/api/v1/admin/users", json={**body, "unit_ids": [str(uuid4())]})
    assert bogus.status_code == 422
    created = admin.post("/api/v1/admin/users", json={**body, "unit_ids": [unit["unit_id"]]})
    assert created.status_code == 201 and created.json()["all_plants"] is False
    assert created.json()["unit_ids"] == [unit["unit_id"]]
    uid = created.json()["user_id"]
    widened = admin.patch(f"/api/v1/admin/users/{uid}", json={"all_plants": True})
    assert widened.json()["all_plants"] is True and widened.json()["unit_ids"] == []
    assert admin.patch(f"/api/v1/admin/users/{uid}", json={"all_plants": False}).status_code == 422


def test_role_change_takes_effect_on_the_next_request(make_client, api_database):
    uid, email = account(api_database)
    admin = make_client("ADMIN")
    user_id = admin.post("/api/v1/admin/users", json={
        "email": email, "role_code": "STORES", "all_plants": True}).json()["user_id"]
    assert make_client("STORES", subject=uid, provision=False).get(
        "/api/v1/me").json()["role"] == "STORES"
    admin = make_client("ADMIN")
    changed = admin.patch(f"/api/v1/admin/users/{user_id}", json={"role_code": "PURCHASE"})
    assert changed.status_code == 200 and changed.json()["role"] == "PURCHASE"
    assert admin.patch(f"/api/v1/admin/users/{user_id}",
                       json={"role_code": "WIZARD"}).status_code == 422
    me = make_client("PURCHASE", subject=uid, provision=False).get("/api/v1/me").json()
    assert me["role"] == "PURCHASE" and "vendor.manage" in me["permissions"]


def test_deactivated_user_is_refused_on_the_next_request(make_client, api_database):  # FR-ADM-03
    uid, email = account(api_database)
    admin = make_client("ADMIN")
    user_id = admin.post("/api/v1/admin/users", json={
        "email": email, "role_code": "STORES", "all_plants": True}).json()["user_id"]
    assert make_client("STORES", subject=uid, provision=False).get("/api/v1/me").status_code == 200
    admin = make_client("ADMIN")
    assert admin.patch(f"/api/v1/admin/users/{user_id}",
                       json={"status": "DEACTIVATED"}).json()["status"] == "DEACTIVATED"
    refused = make_client("STORES", subject=uid, provision=False).get("/api/v1/me")
    assert refused.status_code == 403 and "deactivated" in refused.json()["detail"]
    admin = make_client("ADMIN")
    admin.patch(f"/api/v1/admin/users/{user_id}", json={"status": "ACTIVE"})
    assert make_client("STORES", subject=uid, provision=False).get("/api/v1/me").status_code == 200


def test_an_administrator_cannot_lock_themselves_out(make_client):
    admin = make_client("ADMIN")
    me = admin.get("/api/v1/me").json()
    mine = [u for u in admin.get("/api/v1/admin/users?role=ADMIN&limit=200").json()["items"]
            if u["display_name"] == "ADMIN user"][0]["user_id"]
    assert me["role"] == "ADMIN"
    assert admin.patch(f"/api/v1/admin/users/{mine}", json={"status": "DEACTIVATED"}).status_code == 409
    assert admin.patch(f"/api/v1/admin/users/{mine}", json={"role_code": "MD"}).status_code == 409


def test_only_admin_may_administer_even_the_md_may_not(make_client):  # BRD: MD cannot change admin
    for role in ("MD", "PLANT_HEAD", "STORES", "ACCOUNTS", "OPERATOR"):
        client = make_client(role)
        assert client.get("/api/v1/admin/users").status_code == 403, role
        assert client.get("/api/v1/admin/roles").status_code == 403, role
        assert client.post("/api/v1/admin/users", json={
            "email": "x@vrub.example", "role_code": "STORES", "all_plants": True}).status_code == 403


def test_role_catalog_endpoint(make_client):
    roles = {r["code"]: r for r in make_client("ADMIN").get("/api/v1/admin/roles").json()}
    assert len(roles) == 12
    assert "admin" in roles["ADMIN"]["permissions"] and "admin" not in roles["MD"]["permissions"]
    assert roles["OPERATOR"]["modules"] == ["kaizen", "manufacturing", "routes"]


def test_users_of_other_tenants_are_invisible(make_client, api_database):
    foreign = uuid4()
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        role_b = conn.execute("select role_id from erp.role where tenant_id = %s and code = 'STORES'",
                              (api_database["tenant_b"],)).fetchone()[0]
        conn.execute("insert into erp.app_user(user_id, tenant_id, auth_subject, role_id, display_name) "
                     "values (%s, %s, 'b-person', %s, 'Tenant B person')",
                     (foreign, api_database["tenant_b"], role_b))
    admin = make_client("ADMIN")
    names = [u["display_name"] for u in admin.get("/api/v1/admin/users?limit=200").json()["items"]]
    assert "Tenant B person" not in names
    assert admin.get(f"/api/v1/admin/users/{foreign}").status_code == 404
    assert admin.patch(f"/api/v1/admin/users/{foreign}", json={"status": "LOCKED"}).status_code == 404


def test_user_changes_are_audited_with_the_acting_admin(make_client, api_database):
    _, email = account(api_database)
    admin = make_client("ADMIN")
    user_id = admin.post("/api/v1/admin/users", json={
        "email": email, "role_code": "QA", "all_plants": True}).json()["user_id"]
    admin.patch(f"/api/v1/admin/users/{user_id}", json={"status": "LOCKED"})
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        rows = conn.execute(
            "select a.action, u.display_name from erp.audit_log a "
            "join erp.app_user u on u.user_id = a.actor_user_id where a.entity_id = %s "
            "order by a.audit_id", (user_id,)).fetchall()
    assert rows == [("user.create", "ADMIN user"), ("user.update", "ADMIN user")]


def test_admin_reads_the_audit_log_and_others_cannot(make_client, api_database):
    admin = make_client("ADMIN")
    unit = admin.post("/api/v1/org/units", json={"code": code(), "name": "Audited plant"}).json()
    admin.patch(f"/api/v1/org/units/{unit['unit_id']}", json={"name": "Renamed plant"})
    log = admin.get(f"/api/v1/admin/audit-log?entity_id={unit['unit_id']}").json()
    assert [e["action"] for e in log["items"]] == ["unit.update", "unit.create"]  # newest first
    first = log["items"][0]
    assert first["actor"] == "ADMIN user" and first["actor_role_code"] == "ADMIN"
    assert first["details"]["changes"] == {"name": "Renamed plant"}
    assert first["details"]["email"] == "t@vrub.example"
    by_prefix = admin.get("/api/v1/admin/audit-log?action=unit.&limit=200").json()
    assert {e["action"] for e in by_prefix["items"]} <= {"unit.create", "unit.update"}
    assert admin.get("/api/v1/admin/audit-log?entity_type=unit&limit=1").json()["limit"] == 1
    assert admin.get("/api/v1/admin/audit-log?action=%25").json()["total"] == 0  # literal, not wildcard
    for role in ("MD", "PLANT_HEAD", "STORES"):
        assert make_client(role).get("/api/v1/admin/audit-log").status_code == 403, role


def test_the_audit_log_never_shows_another_tenants_entries(make_client, api_database):
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("insert into erp.audit_log(tenant_id, action, entity_type, entity_id) "
                     "values (%s, 'secret.thing', 'x', 'tenant-b-only')", (api_database["tenant_b"],))
    log = make_client("ADMIN").get("/api/v1/admin/audit-log?entity_id=tenant-b-only").json()
    assert log["total"] == 0 and log["items"] == []
