"""Items, plant policies, technical specifications, customer part numbers and routes."""

from uuid import uuid4

import psycopg
import pytest

from heyyerp_api.masters.validation import clean_hsn, clean_item_code

P = "/api/v1/product"
pytestmark = pytest.mark.usefixtures("api_database")


def code() -> str:
    return "T" + uuid4().hex[:9].upper()


def make_item(client, item_type="RM", uom="KG", **extra):
    response = client.post(f"{P}/items", json={"code": code(), "name": "Test item",
                                                "item_type": item_type, "uom_code": uom, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def make_plant(admin, with_store=False):
    unit = admin.post("/api/v1/org/units", json={"code": code(), "name": "Plant"}).json()
    if not with_store:
        return unit
    store = admin.post("/api/v1/org/stores", json={
        "unit_id": unit["unit_id"], "code": "MAIN", "name": "Main", "store_type": "RAW_MATERIAL"}
    ).json()
    return unit, store


# ------------------------------------------------------------------ validators (no database)
def test_item_code_and_hsn_validators():
    assert clean_item_code(" vr-bush/0123.a ") == "VR-BUSH/0123.A"
    for bad in ("", "-X", "has space", "x" * 31, "A#B"):
        with pytest.raises(ValueError):
            clean_item_code(bad)
    assert clean_hsn("4016") == "4016" and clean_hsn("40169990") == "40169990"
    assert clean_hsn("  ") is None and clean_hsn(None) is None
    for bad in ("401", "40169", "4016999", "ABCD", "4016.99"):
        with pytest.raises(ValueError):
            clean_hsn(bad)


# ------------------------------------------------------------------ items
def test_item_lifecycle_and_validation(make_client):
    stores = make_client("STORES")
    item = make_item(stores, hsn_code="400121", shelf_life_days=180)
    assert item["uom_code"] == "KG" and item["hsn_code"] == "400121" and item["active"] is True
    assert stores.get(f"{P}/items/{item['item_id']}").json()["code"] == item["code"]
    post = lambda **b: stores.post(f"{P}/items", json={  # noqa: E731
        "code": code(), "name": "x", "item_type": "RM", "uom_code": "KG", **b})
    assert post(code=item["code"]).status_code == 409
    assert post(uom_code="LIGHTYEAR").status_code == 422
    assert post(item_type="WIDGET").status_code == 422
    assert post(hsn_code="12").status_code == 422
    assert post(shelf_life_days=-1).status_code == 422
    assert post(category_id=str(uuid4())).status_code == 422
    assert post(route_code="MOULD").status_code == 422  # an RM is not made on a route
    assert post(tenant_id=str(uuid4())).status_code == 422
    patched = stores.patch(f"{P}/items/{item['item_id']}", json={"name": "Renamed", "active": False})
    assert patched.json()["name"] == "Renamed" and patched.json()["active"] is False
    assert stores.patch(f"{P}/items/{item['item_id']}", json={"item_type": "FG"}).status_code == 422
    assert stores.patch(f"{P}/items/{item['item_id']}", json={"uom_code": "G"}).status_code == 422
    assert stores.patch(f"{P}/items/{item['item_id']}", json={"name": None}).status_code == 422
    assert stores.get(f"{P}/items/{uuid4()}").status_code == 404


def test_item_with_category_and_made_item_with_route(make_client):
    admin = make_client("ADMIN")
    category = admin.post("/api/v1/ref/item-categories",
                          json={"code": code(), "name": "Rubber parts"}).json()
    fg = make_item(admin, "FG", "NOS", category_id=category["category_id"], route_code="MOULD")
    assert fg["category_id"] == category["category_id"] and fg["route_code"] == "MOULD"
    assert admin.patch(f"{P}/items/{fg['item_id']}", json={"route_code": "NOPE"}).status_code == 422
    assert admin.patch(f"{P}/items/{fg['item_id']}", json={"route_code": None}).json()[
        "route_code"] is None


def test_item_permissions_and_filters(make_client):
    admin = make_client("ADMIN")
    rm = make_item(admin, "RM", hsn_code="4001")
    fg = make_item(admin, "FG", "NOS")
    for role in ("SALES", "ACCOUNTS", "OPERATOR", "MAINT", "HR"):
        client = make_client(role)
        assert client.get(f"{P}/items").status_code == 200
        assert client.post(f"{P}/items", json={"code": code(), "name": "x", "item_type": "RM",
                                               "uom_code": "KG"}).status_code == 403, role
    for role in ("STORES", "PURCHASE", "QA", "PROD", "PLANT_HEAD"):
        assert make_client(role).post(f"{P}/items", json={
            "code": code(), "name": "x", "item_type": "RM", "uom_code": "KG"}).status_code == 201
    admin = make_client("ADMIN")
    only_fg = admin.get(f"{P}/items?item_type=FG&limit=200").json()
    assert fg["item_id"] in {i["item_id"] for i in only_fg["items"]}
    assert all(i["item_type"] == "FG" for i in only_fg["items"])
    found = admin.get(f"{P}/items?q={rm['code'].lower()}").json()
    assert [i["item_id"] for i in found["items"]] == [rm["item_id"]]
    assert admin.get(f"{P}/items?q=%25").json()["total"] == 0  # wildcard is literal


# ------------------------------------------------------------------ plant policies / applicability
def test_plant_policy_makes_an_item_applicable_at_that_plant(make_client):
    admin = make_client("ADMIN")
    (plant1, store1), plant2 = make_plant(admin, with_store=True), make_plant(admin)
    item = make_item(admin)
    url = f"{P}/items/{item['item_id']}/policies"
    assert admin.get(url).json() == []
    put = admin.put(f"{url}/{plant1['unit_id']}", json={
        "reorder_point": "100.5", "reorder_qty": "500", "default_store_id": store1["store_id"]})
    assert put.status_code == 200, put.text
    assert float(put.json()["reorder_point"]) == 100.5 and put.json()["unit_code"] == plant1["code"]
    again = admin.put(f"{url}/{plant1['unit_id']}", json={"reorder_point": "10"})  # upsert
    assert float(again.json()["reorder_point"]) == 10 and again.json()["default_store_id"] is None
    ids = lambda u: {i["item_id"] for i in admin.get(  # noqa: E731
        f"{P}/items?unit_id={u}&limit=200").json()["items"]}
    assert item["item_id"] in ids(plant1["unit_id"])
    assert item["item_id"] not in ids(plant2["unit_id"])  # not applicable at plant 2
    assert admin.delete(f"{url}/{plant1['unit_id']}").status_code == 204
    assert item["item_id"] not in ids(plant1["unit_id"])
    assert admin.delete(f"{url}/{plant1['unit_id']}").status_code == 404


def test_policy_validation(make_client):
    admin = make_client("ADMIN")
    (plant1, store1), plant2 = make_plant(admin, with_store=True), make_plant(admin)
    item = make_item(admin)
    url = f"{P}/items/{item['item_id']}/policies"
    assert admin.put(f"{url}/{plant2['unit_id']}", json={"default_store_id": store1["store_id"]}
                     ).status_code == 422  # that store belongs to another plant
    assert admin.put(f"{url}/{plant1['unit_id']}", json={"reorder_point": "-1"}).status_code == 422
    assert admin.put(f"{url}/{uuid4()}", json={}).status_code == 404
    admin.patch(f"/api/v1/org/stores/{store1['store_id']}", json={"active": False})
    assert admin.put(f"{url}/{plant1['unit_id']}", json={"default_store_id": store1["store_id"]}
                     ).status_code == 422  # inactive store
    admin.patch(f"/api/v1/org/stores/{store1['store_id']}", json={"active": False})
    admin.patch(f"/api/v1/org/units/{plant2['unit_id']}", json={"active": False})
    assert admin.put(f"{url}/{plant2['unit_id']}", json={}).status_code == 422  # inactive plant
    admin.patch(f"{P}/items/{item['item_id']}", json={"active": False})
    assert admin.put(f"{url}/{plant1['unit_id']}", json={}).status_code == 422  # inactive item


def test_plant_scope_limits_policies(make_client):
    admin = make_client("ADMIN")
    mine, theirs = make_plant(admin), make_plant(admin)
    item = make_item(admin)
    admin.put(f"{P}/items/{item['item_id']}/policies/{theirs['unit_id']}", json={})
    scoped = make_client("PROD", subject="u-scoped-prod", unit_ids=[mine["unit_id"]])
    url = f"{P}/items/{item['item_id']}/policies"
    assert scoped.get(url).json() == []  # the other plant's policy is invisible
    assert scoped.put(f"{url}/{theirs['unit_id']}", json={}).status_code == 403
    assert scoped.put(f"{url}/{mine['unit_id']}", json={}).status_code == 200
    assert scoped.delete(f"{url}/{theirs['unit_id']}").status_code == 403
    assert scoped.get(f"{P}/items?unit_id={theirs['unit_id']}").status_code == 404


# ------------------------------------------------------------------ technical specifications
def test_specifications(make_client):
    qa = make_client("QA")
    item = make_item(qa, "FG", "NOS")
    url = f"{P}/items/{item['item_id']}/specs"
    hardness = qa.put(f"{url}/Hardness", json={"unit": "Shore A", "target_value": "65",
                                               "min_value": "60", "max_value": "70"})
    assert hardness.status_code == 200 and hardness.json()["name"] == "Hardness"
    qa.put(f"{url}/Drawing no", json={"text_value": "DRG-1042 rev C"})
    changed = qa.put(f"{url}/Hardness", json={"min_value": "62", "max_value": "68"})
    assert float(changed.json()["min_value"]) == 62 and changed.json()["target_value"] is None
    assert [s["name"] for s in qa.get(url).json()] == ["Drawing no", "Hardness"]
    bad = lambda **b: qa.put(f"{url}/X", json=b).status_code  # noqa: E731
    assert bad() == 422 and bad(unit="mm") == 422  # nothing to specify
    assert bad(min_value="10", max_value="5") == 422
    assert bad(target_value="20", min_value="10", max_value="15") == 422
    assert qa.delete(f"{url}/Hardness").status_code == 204
    assert qa.delete(f"{url}/Hardness").status_code == 404
    assert make_client("SALES").put(f"{url}/Weight", json={"target_value": "1"}).status_code == 403
    assert make_client("SALES").get(url).status_code == 200


# ------------------------------------------------------------------ customer part numbers
def test_customer_part_numbers(make_client):
    admin = make_client("ADMIN")
    fg, rm = make_item(admin, "FG", "NOS"), make_item(admin)
    other_fg = make_item(admin, "FG", "NOS")  # made now: building the SALES client closes this one
    customer = admin.post("/api/v1/parties/customers", json={"code": code(), "name": "OEM"}).json()
    sales = make_client("SALES")
    url = f"{P}/items/{fg['item_id']}/customer-parts"
    added = sales.post(url, json={"customer_id": customer["customer_id"],
                                  "customer_part_no": " 54500-AB123 "})
    assert added.status_code == 201 and added.json()["customer_part_no"] == "54500-AB123"
    assert added.json()["customer_code"] == customer["code"]
    assert sales.post(url, json={"customer_id": customer["customer_id"],
                                 "customer_part_no": "54500-AB123"}).status_code == 409
    clash = sales.post(f"{P}/items/{other_fg['item_id']}/customer-parts", json={
        "customer_id": customer["customer_id"], "customer_part_no": "54500-AB123"})
    assert clash.status_code == 409  # one customer part number maps to one item
    assert sales.post(f"{P}/items/{rm['item_id']}/customer-parts", json={
        "customer_id": customer["customer_id"], "customer_part_no": "X1"}).status_code == 422
    assert sales.post(url, json={"customer_id": str(uuid4()),
                                 "customer_part_no": "Y"}).status_code == 422
    assert [p["customer_part_no"] for p in sales.get(url).json()] == ["54500-AB123"]
    assert sales.delete(f"{P}/customer-parts/{added.json()['xref_id']}").status_code == 204
    assert sales.delete(f"{P}/customer-parts/{added.json()['xref_id']}").status_code == 404
    # (a new client closes the previous one, so this check comes last)
    assert make_client("STORES").post(url, json={"customer_id": customer["customer_id"],
                                                 "customer_part_no": "Z"}).status_code == 403


# ------------------------------------------------------------------ routes
def stages(*spec):
    return [{"stage_code": c, "stage_name": c.title(), "is_qa_gate": qa, "is_end": end}
            for c, qa, end in spec]


def test_default_routes_follow_the_brd(make_client):
    routes = {r["code"]: r for r in make_client("OPERATOR").get(f"{P}/routes").json()
              if r["code"] in ("METAL", "MOULD", "EXTR")}
    assert set(routes) == {"METAL", "MOULD", "EXTR"}
    assert len(routes["METAL"]["stages"]) == 7 and len(routes["MOULD"]["stages"]) == 4
    assert len(routes["EXTR"]["stages"]) == 5
    metal = routes["METAL"]["stages"]
    assert [s["is_qa_gate"] for s in metal] == [False, True, False, True, False, True, False]
    assert metal[-1]["stage_code"] == "READY_FOR_MOULDING" and metal[-1]["is_end"] is True
    assert routes["MOULD"]["stages"][-1]["stage_code"] == "FG_STORE"
    assert all(r["version"] == 1 and r["active"] for r in routes.values())
    assert make_client("OPERATOR").get(f"{P}/routes/by-code/mould").json()["code"] == "MOULD"


def test_route_validation_and_permissions(make_client):
    prod = make_client("PROD")
    ok = stages(("CUT", False, False), ("QA", True, False), ("STORE", False, True))
    base = {"code": code(), "name": "Test route", "stages": ok}
    assert prod.post(f"{P}/routes", json=base).status_code == 201
    for bad in (
        {**base, "code": code(), "stages": []},
        {**base, "code": code(), "stages": stages(("A", False, False), ("B", False, False))},
        {**base, "code": code(), "stages": stages(("A", False, True), ("B", False, True))},
        {**base, "code": code(), "stages": stages(("A", False, True), ("B", False, False))},
        {**base, "code": code(), "stages": stages(("A", False, False), ("A", False, True))},
    ):
        assert prod.post(f"{P}/routes", json=bad).status_code == 422
    assert prod.post(f"{P}/routes", json=base).status_code == 409  # same code and version
    for role in ("STORES", "SALES", "OPERATOR", "ACCOUNTS"):
        assert make_client(role).post(f"{P}/routes", json={
            **base, "code": code()}).status_code == 403, role


def test_revising_a_route_keeps_the_old_version_and_activates_the_new(make_client):
    qa = make_client("QA")
    c = code()
    v1 = qa.post(f"{P}/routes", json={"code": c, "name": "R", "stages": stages(
        ("CUT", False, False), ("STORE", False, True))}).json()
    v2 = qa.post(f"{P}/routes/{v1['route_id']}/revise", json={"name": "R rev 2", "stages": stages(
        ("CUT", False, False), ("QA", True, False), ("STORE", False, True))})
    assert v2.status_code == 201 and v2.json()["version"] == 2 and v2.json()["active"] is True
    assert len(v2.json()["stages"]) == 3
    old = qa.get(f"{P}/routes/{v1['route_id']}").json()
    assert old["active"] is False and len(old["stages"]) == 2  # history is untouched
    assert qa.get(f"{P}/routes/by-code/{c}").json()["version"] == 2
    v3 = qa.post(f"{P}/routes/{v2.json()['route_id']}/revise", json={"name": "R", "stages": stages(
        ("STORE", False, True))}).json()
    assert v3["version"] == 3
    assert {r["version"] for r in qa.get(f"{P}/routes?active=false").json() if r["code"] == c} == {1, 2}
    assert qa.post(f"{P}/routes/{uuid4()}/revise", json={"name": "x", "stages": stages(
        ("S", False, True))}).status_code == 404


def test_retiring_a_route_in_use_is_refused(make_client):
    qa = make_client("QA")
    c = code()
    route = qa.post(f"{P}/routes", json={"code": c, "name": "R", "stages": stages(
        ("A", False, False), ("B", False, True))}).json()
    item = make_item(qa, "FG", "NOS", route_code=c)
    refused = qa.post(f"{P}/routes/{route['route_id']}/retire")
    assert refused.status_code == 409 and "item" in refused.json()["detail"]
    qa.patch(f"{P}/items/{item['item_id']}", json={"route_code": None})
    assert qa.post(f"{P}/routes/{route['route_id']}/retire").json()["active"] is False
    assert qa.post(f"{P}/routes/{route['route_id']}/retire").status_code == 409
    assert qa.patch(f"{P}/items/{item['item_id']}", json={"route_code": c}).status_code == 422


# ------------------------------------------------------------------ isolation and audit
def test_items_are_tenant_isolated_and_changes_audited(make_client, api_database):
    foreign = uuid4()
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        uom = conn.execute("select uom_id from erp.uom where code = 'KG'").fetchone()[0]
        conn.execute("insert into erp.item(item_id, tenant_id, uom_id, code, name, item_type) "
                     "values (%s, %s, %s, 'THEIRS', 'Tenant B item', 'RM')",
                     (foreign, api_database["tenant_b"], uom))
    admin = make_client("ADMIN")
    assert admin.get(f"{P}/items/{foreign}").status_code == 404
    assert admin.patch(f"{P}/items/{foreign}", json={"name": "x"}).status_code == 404
    assert "THEIRS" not in [i["code"] for i in admin.get(f"{P}/items?limit=200").json()["items"]]
    assert admin.post(f"{P}/items", json={"code": "THEIRS", "name": "Mine", "item_type": "RM",
                                          "uom_code": "KG"}).status_code == 201  # codes per tenant
    item = make_item(admin)
    admin.patch(f"{P}/items/{item['item_id']}", json={"name": "Changed"})
    admin.put(f"{P}/items/{item['item_id']}/specs/Weight", json={"target_value": "2"})
    with psycopg.connect(api_database["owner"]) as conn:
        actions = [r[0] for r in conn.execute(
            "select action from erp.audit_log where entity_id = %s order by audit_id",
            (item["item_id"],))]
    assert actions == ["item.create", "item.update", "item.spec.set"]
