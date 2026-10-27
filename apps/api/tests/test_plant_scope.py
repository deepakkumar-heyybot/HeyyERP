"""Plant scope (FR-ADM-04): a user limited to some plants sees and changes only those."""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.usefixtures("api_database")


def code() -> str:
    return uuid4().hex[:10].upper()


@pytest.fixture
def plants(make_client):
    """Two plants, each with a store and a department, plus one organization-wide department."""
    admin = make_client("ADMIN")
    made = {}
    for name in ("p1", "p2"):
        unit = admin.post("/api/v1/org/units", json={"code": code(), "name": name}).json()
        store = admin.post("/api/v1/org/stores", json={
            "unit_id": unit["unit_id"], "code": code(), "name": name, "store_type": "OTHER"}).json()
        dept = admin.post("/api/v1/org/departments", json={
            "code": code(), "name": name, "unit_id": unit["unit_id"]}).json()
        made[name] = {"unit": unit, "store": store, "dept": dept}
    made["org_wide"] = admin.post("/api/v1/org/departments",
                                  json={"code": code(), "name": "Head office"}).json()
    return made


def scoped(make_client, plants, which="p1"):
    return make_client("ADMIN", subject=f"u-scoped-{which}-{uuid4().hex[:6]}",
                       unit_ids=[plants[which]["unit"]["unit_id"]])


def ids(page, key):
    return {item[key] for item in page["items"]}


def test_lists_show_only_the_users_plants(make_client, plants):
    client = scoped(make_client, plants)
    mine, theirs = plants["p1"], plants["p2"]
    units = ids(client.get("/api/v1/org/units?limit=200").json(), "unit_id")
    assert units == {mine["unit"]["unit_id"]}
    stores = ids(client.get("/api/v1/org/stores?limit=200").json(), "store_id")
    assert stores == {mine["store"]["store_id"]}
    depts = ids(client.get("/api/v1/org/departments?limit=200").json(), "department_id")
    assert mine["dept"]["department_id"] in depts and theirs["dept"]["department_id"] not in depts
    assert plants["org_wide"]["department_id"] in depts  # organization-wide stays visible
    assert client.get("/api/v1/org/units").json()["total"] == 1


def test_other_plants_are_not_found_by_id(make_client, plants):
    client = scoped(make_client, plants)
    theirs = plants["p2"]
    assert client.get(f"/api/v1/org/units/{theirs['unit']['unit_id']}").status_code == 404
    assert client.get(f"/api/v1/org/stores/{theirs['store']['store_id']}").status_code == 404
    assert client.get(f"/api/v1/org/departments/{theirs['dept']['department_id']}").status_code == 404
    assert client.patch(f"/api/v1/org/units/{theirs['unit']['unit_id']}",
                        json={"name": "x"}).status_code == 404
    assert client.patch(f"/api/v1/org/stores/{theirs['store']['store_id']}",
                        json={"name": "x"}).status_code == 404
    assert client.patch(f"/api/v1/org/departments/{theirs['dept']['department_id']}",
                        json={"name": "x"}).status_code == 404


def test_scoped_users_cannot_write_outside_their_plants(make_client, plants):
    client = scoped(make_client, plants)
    theirs = plants["p2"]["unit"]["unit_id"]
    store = {"unit_id": theirs, "code": code(), "name": "S", "store_type": "OTHER"}
    assert client.post("/api/v1/org/stores", json=store).status_code == 403
    assert client.post("/api/v1/org/departments", json={
        "code": code(), "name": "D", "unit_id": theirs}).status_code == 403
    assert client.post("/api/v1/org/departments", json={
        "code": code(), "name": "Org-wide"}).status_code == 403  # needs all plants
    assert client.post("/api/v1/org/units", json={"code": code(), "name": "New"}).status_code == 403
    mine = plants["p1"]["unit"]["unit_id"]
    assert client.post("/api/v1/org/stores", json={**store, "unit_id": mine}).status_code == 201
    moved = client.patch(f"/api/v1/org/departments/{plants['p1']['dept']['department_id']}",
                         json={"unit_id": theirs})
    assert moved.status_code == 403  # cannot move a department into a plant it cannot see


def test_organization_wide_department_is_read_only_for_scoped_users(make_client, plants):
    client = scoped(make_client, plants)
    org_wide = plants["org_wide"]["department_id"]
    assert client.get(f"/api/v1/org/departments/{org_wide}").status_code == 200
    assert client.patch(f"/api/v1/org/departments/{org_wide}", json={"name": "x"}).status_code == 404


def test_all_plant_users_and_scope_changes(make_client, plants):
    everyone = make_client("ADMIN")
    assert everyone.get("/api/v1/org/units?limit=200").json()["total"] >= 2
    both = make_client("ADMIN", subject="u-two-plants", unit_ids=[
        plants["p1"]["unit"]["unit_id"], plants["p2"]["unit"]["unit_id"]])
    shown = ids(both.get("/api/v1/org/units?limit=200").json(), "unit_id")
    assert shown == {plants["p1"]["unit"]["unit_id"], plants["p2"]["unit"]["unit_id"]}


def test_scope_filters_combine_with_other_filters(make_client, plants):
    client = scoped(make_client, plants)
    other = plants["p2"]["unit"]["unit_id"]
    assert client.get(f"/api/v1/org/stores?unit_id={other}").json()["total"] == 0
    assert client.get("/api/v1/org/stores?store_type=OTHER").json()["total"] == 1
    assert client.get("/api/v1/org/departments?active=true&limit=200").json()["total"] >= 2
