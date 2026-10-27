"""Document numbers and purchase indents, including approval bands and visibility."""

from datetime import date, timedelta
from uuid import uuid4

import psycopg
import pytest

from heyyerp_api.procurement.numbering import fy_label

I = "/api/v1/procurement/indents"  # noqa: E741 - short on purpose
A = "/api/v1/approvals"
pytestmark = pytest.mark.usefixtures("api_database")
TODAY = date.today()


def code() -> str:
    return "I" + uuid4().hex[:9].upper()


def test_financial_year_label():
    assert fy_label(date(2026, 4, 1)) == "26" and fy_label(date(2027, 3, 31)) == "26"
    assert fy_label(date(2026, 3, 31)) == "25" and fy_label(date(2027, 4, 1)) == "27"


def item(client, item_type, **extra):
    r = client.post("/api/v1/product/items", json={
        "code": code(), "name": f"{item_type} item", "item_type": item_type, "uom_code": "KG",
        **extra})
    assert r.status_code == 201, r.text
    return r.json()


def plant(admin):
    return admin.post("/api/v1/org/units", json={"code": code(), "name": "Plant"}).json()


def body(unit, indent_type="BOM", lines=None, **extra):
    return {"unit_id": unit["unit_id"], "indent_type": indent_type, "lines": lines or [], **extra}


def bom_line(rm, qty="10"):
    return {"item_id": rm["item_id"], "requested_qty": qty, "purpose": "Production"}


def capital_line(asset, value="300000"):
    return {"item_id": asset["item_id"], "requested_qty": "1",
            "capital_description": "New press", "capital_value": value}


def decide(client, approval_id, decision="APPROVE", note=None):
    return client.post(f"{A}/{approval_id}/decide", json={"decision": decision, "note": note})


# ------------------------------------------------------------------ numbering
def test_numbers_follow_the_pattern_and_never_repeat(make_client):
    admin = make_client("ADMIN")
    unit, rm = plant(admin), item(admin, "RM")
    stores = make_client("STORES")
    numbers = [stores.post(I, json=body(unit, lines=[bom_line(rm)])).json()["indent_no"]
               for _ in range(3)]
    label = fy_label(TODAY)
    assert all(n.startswith(f"IND-{label}-") and len(n) == len("IND-26-0001") for n in numbers)
    counts = [int(n.rsplit("-", 1)[1]) for n in numbers]
    assert counts == [counts[0], counts[0] + 1, counts[0] + 2]


# ------------------------------------------------------------------ creating and editing
def test_raising_an_indent_validates_header_and_lines(make_client):
    admin = make_client("ADMIN")
    unit, rm = plant(admin), item(admin, "RM")
    consumable, asset, fg = item(admin, "CONSUMABLE"), item(admin, "CAPITAL"), item(admin, "FG")
    stores = make_client("STORES")
    ok = stores.post(I, json=body(unit, lines=[bom_line(rm)],
                                  needed_by=(TODAY + timedelta(days=7)).isoformat()))
    assert ok.status_code == 201, ok.text
    made = ok.json()
    assert made["status"] == "DRAFT" and made["requester_name"] == "STORES user"
    assert made["lines"][0]["item_code"] == rm["code"] and made["lines"][0]["uom_code"] == "KG"
    post = lambda **b: stores.post(I, json=body(unit, **b)).status_code  # noqa: E731
    assert post(lines=[]) == 422
    assert post(lines=[bom_line(rm), bom_line(rm)]) == 422  # same item twice
    assert post(lines=[bom_line(rm, "0")]) == 422
    assert post(lines=[bom_line(fg)]) == 422  # a BOM indent asks for raw material
    assert post(indent_type="CONSUMABLE", lines=[bom_line(rm)]) == 422
    assert post(indent_type="CONSUMABLE", lines=[bom_line(consumable)]) == 201
    assert post(indent_type="CAPITAL", lines=[bom_line(asset)]) == 422  # needs description, value
    assert post(indent_type="BOM", lines=[capital_line(rm)]) == 422  # capital data on a BOM indent
    assert post(indent_type="CAPITAL", lines=[capital_line(asset)]) == 201
    assert post(lines=[bom_line(rm)], needed_by=(TODAY - timedelta(days=1)).isoformat()) == 422
    assert post(lines=[{"item_id": str(uuid4()), "requested_qty": "1"}]) == 422
    assert stores.post(I, json={**body(unit, lines=[bom_line(rm)]),
                                "requester_id": str(uuid4())}).status_code == 422


def test_only_an_inactive_item_or_plant_is_refused(make_client):
    admin = make_client("ADMIN")
    unit, rm = plant(admin), item(admin, "RM")
    admin.patch(f"/api/v1/product/items/{rm['item_id']}", json={"active": False})
    assert admin.post(I, json=body(unit, lines=[bom_line(rm)])).status_code == 422
    rm2 = item(admin, "RM")
    admin.patch(f"/api/v1/org/units/{unit['unit_id']}", json={"active": False})
    assert admin.post(I, json=body(unit, lines=[bom_line(rm2)])).status_code == 422


def test_only_the_requester_edits_a_draft_and_only_while_it_is_a_draft(make_client):
    admin = make_client("ADMIN")
    unit, rm1, rm2 = plant(admin), item(admin, "RM"), item(admin, "RM")
    prod = make_client("PROD")
    made = prod.post(I, json=body(unit, lines=[bom_line(rm1)])).json()
    url = f"{I}/{made['indent_id']}"
    new = {"needed_by": (TODAY + timedelta(days=3)).isoformat(), "lines": [bom_line(rm2, "4")]}
    edited = prod.put(url, json=new)
    assert edited.status_code == 200 and edited.json()["lines"][0]["item_code"] == rm2["code"]
    assert float(edited.json()["lines"][0]["requested_qty"]) == 4
    assert make_client("MAINT").put(url, json=new).status_code == 404  # not theirs, not visible
    assert make_client("STORES").put(url, json=new).status_code == 403  # sees it, may not edit
    prod = make_client("PROD")
    prod.post(f"{url}/submit")
    assert prod.put(url, json=new).status_code == 409  # frozen once submitted
    assert prod.post(f"{url}/submit").status_code == 409


# ------------------------------------------------------------------ approval routing
def test_bom_indent_goes_through_department_head_then_plant_head(make_client):
    admin = make_client("ADMIN")
    unit, rm = plant(admin), item(admin, "RM")
    dept = admin.post("/api/v1/org/departments", json={
        "code": code(), "name": "Stores dept", "head_role_code": "PURCHASE"}).json()
    stores = make_client("STORES")
    made = stores.post(I, json=body(unit, lines=[bom_line(rm)],
                                    department_id=dept["department_id"])).json()
    sent = stores.post(f"{I}/{made['indent_id']}/submit")
    assert sent.status_code == 200 and sent.json()["status"] == "PENDING_APPROVAL"
    approval = stores.get(f"{A}/{sent.json()['approval_id']}").json()
    assert approval["rule"] == "R-IND" and approval["title"].startswith("Indent IND-")
    assert [(s["role"], s["state"]) for s in approval["steps"]] == [
        ("PURCHASE", "CURRENT"), ("PLANT_HEAD", "WAITING")]
    decide(make_client("PURCHASE"), approval["approval_id"])
    assert decide(make_client("PLANT_HEAD"), approval["approval_id"]).json()["status"] == "APPROVED"
    assert make_client("STORES").get(f"{I}/{made['indent_id']}").json()["status"] == "APPROVED"


def test_capital_indents_are_banded_by_value(make_client):
    admin = make_client("ADMIN")
    unit, asset = plant(admin), item(admin, "CAPITAL")
    prod = make_client("PROD")
    small = prod.post(I, json=body(unit, "CAPITAL", [capital_line(asset, "2000000")])).json()
    assert small["capital_total"] == "2000000.00"
    sent = prod.post(f"{I}/{small['indent_id']}/submit").json()
    approval = prod.get(f"{A}/{sent['approval_id']}").json()
    assert approval["rule"] == "R-IND-CAP-1" and float(approval["amount"]) == 2_000_000
    # No department, so the "department head" step falls back to the Plant Head and merges with it.
    assert [(s["role"], s["state"]) for s in approval["steps"]] == [("PLANT_HEAD", "CURRENT")]
    admin = make_client("ADMIN")
    asset2 = item(admin, "CAPITAL")
    prod = make_client("PROD")
    large = prod.post(I, json=body(unit, "CAPITAL", [capital_line(asset2, "3000000")])).json()
    sent = prod.post(f"{I}/{large['indent_id']}/submit").json()
    approval = prod.get(f"{A}/{sent['approval_id']}").json()
    assert approval["rule"] == "R-IND-CAP-2" and [s["role"] for s in approval["steps"]] == [
        "PLANT_HEAD", "MD"]


def test_rejection_marks_the_indent_rejected_and_it_cannot_be_resubmitted(make_client):
    admin = make_client("ADMIN")
    unit, rm = plant(admin), item(admin, "RM")
    stores = make_client("STORES")
    made = stores.post(I, json=body(unit, lines=[bom_line(rm)])).json()
    approval_id = stores.post(f"{I}/{made['indent_id']}/submit").json()["approval_id"]
    decide(make_client("PLANT_HEAD"), approval_id, "REJECT", "Not budgeted")
    stores = make_client("STORES")
    assert stores.get(f"{I}/{made['indent_id']}").json()["status"] == "REJECTED"
    assert stores.post(f"{I}/{made['indent_id']}/submit").status_code == 409
    assert stores.post(f"{I}/{made['indent_id']}/cancel").status_code == 409


def test_cancelling(make_client):
    admin = make_client("ADMIN")
    unit, rm = plant(admin), item(admin, "RM")
    stores = make_client("STORES")
    draft = stores.post(I, json=body(unit, lines=[bom_line(rm)])).json()
    assert stores.post(f"{I}/{draft['indent_id']}/cancel").json()["status"] == "CANCELLED"
    assert stores.post(f"{I}/{draft['indent_id']}/cancel").status_code == 409
    pending = stores.post(I, json=body(unit, lines=[bom_line(rm)])).json()
    stores.post(f"{I}/{pending['indent_id']}/submit")
    assert stores.post(f"{I}/{pending['indent_id']}/cancel").status_code == 409  # in approval


# ------------------------------------------------------------------ permissions, visibility, scope
def test_who_may_raise_and_who_may_see(make_client):
    admin = make_client("ADMIN")
    unit, rm = plant(admin), item(admin, "RM")
    for role in ("SALES", "ACCOUNTS", "HR", "OPERATOR"):  # no indent.raise
        assert make_client(role).post(I, json=body(unit, lines=[bom_line(rm)])).status_code == 403
    prod = make_client("PROD")  # raises indents but has no procurement module
    mine = prod.post(I, json=body(unit, lines=[bom_line(rm)])).json()
    assert prod.get(f"{I}/{mine['indent_id']}").status_code == 200
    stores = make_client("STORES")
    theirs = stores.post(I, json=body(unit, lines=[bom_line(rm)])).json()
    prod = make_client("PROD")
    assert prod.get(f"{I}/{theirs['indent_id']}").status_code == 404  # another person's indent
    seen = {i["indent_id"] for i in prod.get(f"{I}?limit=200").json()["items"]}
    assert mine["indent_id"] in seen and theirs["indent_id"] not in seen
    purchase = make_client("PURCHASE")  # procurement module: sees everything in scope
    seen = {i["indent_id"] for i in purchase.get(f"{I}?limit=200").json()["items"]}
    assert {mine["indent_id"], theirs["indent_id"]} <= seen
    only_mine = purchase.get(f"{I}?mine=true&limit=200").json()["items"]
    assert all(i["requester_name"] == "PURCHASE user" for i in only_mine)


def test_plant_scope_limits_indents(make_client):
    admin = make_client("ADMIN")
    mine, theirs, rm = plant(admin), plant(admin), item(admin, "RM")
    stores = make_client("STORES", subject="u-scoped-stores", unit_ids=[mine["unit_id"]])
    assert stores.post(I, json=body(theirs, lines=[bom_line(rm)])).status_code == 403
    own = stores.post(I, json=body(mine, lines=[bom_line(rm)])).json()
    other = make_client("PURCHASE").post(I, json=body(theirs, lines=[bom_line(rm)])).json()
    scoped = make_client("PLANT_HEAD", subject="u-scoped-head", unit_ids=[mine["unit_id"]])
    ids = {i["indent_id"] for i in scoped.get(f"{I}?limit=200").json()["items"]}
    assert own["indent_id"] in ids and other["indent_id"] not in ids
    assert scoped.get(f"{I}/{other['indent_id']}").status_code == 404


def test_other_tenants_indents_are_invisible_and_changes_audited(make_client, api_database):
    admin = make_client("ADMIN")
    unit, rm = plant(admin), item(admin, "RM")
    made = make_client("STORES").post(I, json=body(unit, lines=[bom_line(rm)])).json()
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        tenant_b, user_b = api_database["tenant_b"], uuid4()
        role = conn.execute("select role_id from erp.role where tenant_id = %s and code = 'STORES'",
                            (tenant_b,)).fetchone()[0]
        conn.execute("insert into erp.app_user(user_id, tenant_id, auth_subject, role_id, "
                     "display_name) values (%s, %s, 'b-user', %s, 'B user')",
                     (user_b, tenant_b, role))
        unit_b = conn.execute("insert into erp.unit(tenant_id, code, name) values (%s, 'BP', 'B') "
                              "returning unit_id", (tenant_b,)).fetchone()[0]
        foreign = conn.execute(
            "insert into erp.indent(tenant_id, unit_id, requester_id, indent_no, indent_type) "
            "values (%s, %s, %s, 'IND-26-9999', 'BOM') returning indent_id",
            (tenant_b, unit_b, user_b)).fetchone()[0]
    admin = make_client("ADMIN")
    assert admin.get(f"{I}/{foreign}").status_code == 404
    assert str(foreign) not in {i["indent_id"] for i in admin.get(f"{I}?limit=200").json()["items"]}
    make_client("STORES").post(f"{I}/{made['indent_id']}/submit")
    with psycopg.connect(api_database["owner"]) as conn:
        actions = [r[0] for r in conn.execute(
            "select action from erp.audit_log where entity_id = %s order by audit_id",
            (made["indent_id"],))]
    assert actions == ["indent.create", "indent.submit"]
