"""Organization master data through the real API, RLS and audit trail (needs a database)."""

from uuid import uuid4

import psycopg
import pytest

from heyyerp_api.masters.validation import _gstin_check_char

pytestmark = pytest.mark.usefixtures("api_database")


def code() -> str:
    return uuid4().hex[:10].upper()


def gstin(state: str = "27") -> str:
    """A well-formed GSTIN with a valid check character and a unique PAN-like middle."""
    letters = "".join(chr(65 + int(c, 16) % 26) for c in uuid4().hex[:5])
    body = f"{state}{letters}{int(uuid4().int % 9000) + 1000}A1Z"
    return body + _gstin_check_char(body)


def make_unit(client, **extra):
    response = client.post("/api/v1/org/units", json={"code": code(), "name": "Plant", **extra})
    assert response.status_code == 201, response.text
    return response.json()


def owner_rows(api_database, sql, params=()):
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        return conn.execute(sql, params).fetchall()


# ------------------------------------------------------------------ plants
def test_admin_creates_a_plant_and_gstin_gives_the_state(make_client):
    client = make_client("ADMIN")
    g = gstin("06")
    unit = make_unit(client, gstin=g)
    assert unit["gstin"] == g and unit["state_code"] == "06" and unit["active"] is True
    assert client.get(f"/api/v1/org/units/{unit['unit_id']}").json()["code"] == unit["code"]


def test_codes_are_normalised_and_must_be_unique(make_client):
    client = make_client("MD")
    c = code().lower()
    assert client.post("/api/v1/org/units", json={"code": c, "name": "A"}).json()["code"] == c.upper()
    assert client.post("/api/v1/org/units", json={"code": c.upper(), "name": "B"}).status_code == 409


def test_gstin_is_validated_and_unique(make_client):
    client = make_client("ADMIN")
    post = lambda **b: client.post("/api/v1/org/units", json={"code": code(), "name": "P", **b})  # noqa: E731
    assert post(gstin="27AAPFU0939F1ZW").status_code == 422  # bad check character
    assert post(gstin="06AAKCV4123R1ZQ").status_code == 422  # BRD placeholder
    g = gstin()
    assert post(gstin=g).status_code == 201
    assert post(gstin=g).status_code == 409
    assert post(gstin=gstin("06"), state_code="27").status_code == 422  # state conflicts


def test_client_cannot_inject_a_tenant_or_unknown_fields(make_client, api_database):
    client = make_client("ADMIN")
    response = client.post("/api/v1/org/units", json={
        "code": code(), "name": "Sneaky", "tenant_id": api_database["tenant_b"]})
    assert response.status_code == 422


def test_only_org_managers_can_write_but_any_member_can_read(make_client):
    admin = make_client("ADMIN")
    unit = make_unit(admin)
    for role in ("STORES", "PURCHASE", "SALES", "PLANT_HEAD", "ACCOUNTS", "OPERATOR"):
        other = make_client(role)
        assert other.get("/api/v1/org/units").status_code == 200
        assert other.post("/api/v1/org/units", json={"code": code(), "name": "X"}).status_code == 403
        patch = other.patch(f"/api/v1/org/units/{unit['unit_id']}", json={"name": "Y"})
        assert patch.status_code == 403


def test_unauthenticated_requests_are_refused():
    from fastapi.testclient import TestClient

    from heyyerp_api.main import app

    with TestClient(app) as anonymous:
        assert anonymous.get("/api/v1/org/units").status_code == 401


def test_other_tenants_plants_are_invisible_and_untouchable(make_client, api_database):
    foreign_id = uuid4()
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("insert into erp.unit(unit_id, tenant_id, code, name) values (%s,%s,%s,%s)",
                     (foreign_id, api_database["tenant_b"], "FOREIGN", "Tenant B plant"))
    client = make_client("ADMIN")
    codes = [u["code"] for u in client.get("/api/v1/org/units?limit=200").json()["items"]]
    assert "FOREIGN" not in codes
    assert client.get(f"/api/v1/org/units/{foreign_id}").status_code == 404
    assert client.patch(f"/api/v1/org/units/{foreign_id}", json={"name": "x"}).status_code == 404
    store = client.post("/api/v1/org/stores", json={
        "unit_id": str(foreign_id), "code": code(), "name": "S", "store_type": "OTHER"})
    assert store.status_code == 422  # a store cannot be attached to another tenant's plant


def test_list_filters_and_paging(make_client):
    client = make_client("ADMIN")
    unit = make_unit(client)
    client.patch(f"/api/v1/org/units/{unit['unit_id']}", json={"active": False})
    inactive = client.get("/api/v1/org/units?active=false&limit=200").json()
    assert unit["code"] in [u["code"] for u in inactive["items"]]
    assert all(u["active"] is False for u in inactive["items"])
    page = client.get("/api/v1/org/units?limit=1&offset=0").json()
    assert len(page["items"]) == 1 and page["total"] >= 1 and page["limit"] == 1
    assert client.get("/api/v1/org/units?limit=0").status_code == 422


def test_changes_are_written_to_the_audit_log(make_client, api_database):
    client = make_client("ADMIN")
    unit = make_unit(client)
    client.patch(f"/api/v1/org/units/{unit['unit_id']}", json={"name": "Renamed"})
    actions = [r[0] for r in owner_rows(
        api_database,
        "select action from erp.audit_log where entity_id = %s order by audit_id",
        (unit["unit_id"],))]
    assert actions == ["unit.create", "unit.update"]
    who = owner_rows(api_database, "select details->>'auth_user_id', actor_role_code "
                     "from erp.audit_log where entity_id = %s limit 1", (unit["unit_id"],))
    assert who == [("u-ADMIN", "ADMIN")]


# ------------------------------------------------------------------ departments
def make_dept(client, **extra):
    response = client.post("/api/v1/org/departments", json={"code": code(), "name": "Dept", **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_department_hierarchy_rules(make_client):
    client = make_client("ADMIN")
    unit = make_unit(client)
    parent = make_dept(client, unit_id=unit["unit_id"])
    child = make_dept(client, unit_id=unit["unit_id"], parent_department_id=parent["department_id"])
    base = "/api/v1/org/departments/"
    assert client.patch(base + parent["department_id"],
                        json={"parent_department_id": child["department_id"]}).status_code == 422
    assert client.patch(base + parent["department_id"],
                        json={"parent_department_id": parent["department_id"]}).status_code == 422
    assert client.patch(base + parent["department_id"], json={"active": False}).status_code == 409
    assert client.patch(base + child["department_id"], json={"active": False}).status_code == 200
    assert client.patch(base + parent["department_id"], json={"active": False}).status_code == 200
    dangling = {"unit_id": unit["unit_id"], "parent_department_id": parent["department_id"]}
    assert client.post("/api/v1/org/departments", json={"code": code(), "name": "D", **dangling}
                       ).status_code == 422  # parent is inactive
    assert client.post("/api/v1/org/departments", json={
        "code": code(), "name": "D", "parent_department_id": str(uuid4())}).status_code == 422


def test_department_code_is_unique_and_inactive_plant_is_refused(make_client):
    client = make_client("ADMIN")
    dept = make_dept(client)
    assert client.post("/api/v1/org/departments",
                       json={"code": dept["code"], "name": "Again"}).status_code == 409
    unit = make_unit(client)
    client.patch(f"/api/v1/org/units/{unit['unit_id']}", json={"active": False})
    assert client.post("/api/v1/org/departments", json={
        "code": code(), "name": "D", "unit_id": unit["unit_id"]}).status_code == 422


# ------------------------------------------------------------------ stores
def make_store(client, unit_id, **extra):
    body = {"unit_id": unit_id, "code": code(), "name": "Store", "store_type": "RAW_MATERIAL", **extra}
    response = client.post("/api/v1/org/stores", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_store_codes_are_unique_per_plant_only(make_client):
    client = make_client("ADMIN")
    first, second = make_unit(client), make_unit(client)
    store = make_store(client, first["unit_id"])
    again = {"unit_id": first["unit_id"], "code": store["code"], "name": "S", "store_type": "WIP"}
    assert client.post("/api/v1/org/stores", json=again).status_code == 409
    make_store(client, second["unit_id"], code=store["code"])  # same code, different plant: fine


def test_store_validation_and_filters(make_client):
    client = make_client("ADMIN")
    unit = make_unit(client)
    bad = {"unit_id": unit["unit_id"], "code": code(), "name": "S", "store_type": "GARAGE"}
    assert client.post("/api/v1/org/stores", json=bad).status_code == 422
    make_store(client, unit["unit_id"], store_type="SPARES")
    make_store(client, unit["unit_id"], store_type="WIP")
    spares = client.get(f"/api/v1/org/stores?unit_id={unit['unit_id']}&store_type=SPARES").json()
    assert spares["total"] == 1 and spares["items"][0]["store_type"] == "SPARES"


def test_plant_cannot_be_deactivated_while_it_has_active_stores(make_client):
    client = make_client("ADMIN")
    unit = make_unit(client)
    store = make_store(client, unit["unit_id"])
    plant = f"/api/v1/org/units/{unit['unit_id']}"
    assert client.patch(plant, json={"active": False}).status_code == 409
    assert client.patch(f"/api/v1/org/stores/{store['store_id']}", json={"active": False}).status_code == 200
    assert client.patch(plant, json={"active": False}).status_code == 200
    # An inactive plant accepts no new stores.
    body = {"unit_id": unit["unit_id"], "code": code(), "name": "S", "store_type": "OTHER"}
    assert client.post("/api/v1/org/stores", json=body).status_code == 422
