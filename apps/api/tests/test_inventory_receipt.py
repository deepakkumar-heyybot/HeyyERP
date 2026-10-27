"""Goods receipts, QA release, FIFO layers, stock reads and the reconciliation check."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from .test_rfq import make_vendors

INV = "/api/v1/inventory"
PO = "/api/v1/procurement/purchase-orders"
pytestmark = pytest.mark.usefixtures("api_database")
SOON = (date.today() + timedelta(days=10)).isoformat()


def code() -> str:
    return "S" + uuid4().hex[:9].upper()


def world(make_client, shelf=180, qty="100", price="50", items=1):
    """A plant with a store, an approved vendor and an OPEN purchase order (auto-approved)."""
    admin = make_client("ADMIN")
    unit = admin.post("/api/v1/org/units", json={"code": code(), "name": "Plant"}).json()
    store = admin.post("/api/v1/org/stores", json={
        "unit_id": unit["unit_id"], "code": "RM", "name": "Raw store",
        "store_type": "RAW_MATERIAL"}).json()
    its = [admin.post("/api/v1/product/items", json={
        "code": code(), "name": "Natural rubber", "item_type": "RM", "uom_code": "KG",
        "shelf_life_days": shelf}).json() for _ in range(items)]
    tax = admin.post("/api/v1/ref/tax-codes", json={
        "code": code(), "description": "GST 18%", "gst_rate": "18"}).json()
    (vendor,) = make_vendors(make_client, 1)
    purchase = make_client("PURCHASE")
    po = purchase.post(PO, json={
        "unit_id": unit["unit_id"], "vendor_id": vendor["vendor_id"],
        "lines": [{"item_id": i["item_id"], "ordered_qty": qty, "unit_price": price,
                   "tax_code_id": tax["tax_code_id"], "due_date": SOON} for i in its]}).json()
    po = purchase.post(f"{PO}/{po['po_id']}/submit").json()
    assert po["status"] == "OPEN", po
    return {"unit": unit, "store": store, "items": its, "item": its[0], "vendor": vendor, "po": po}


def grn_body(w, qty="100", dc=None, lines=None, **extra):
    return {"po_id": w["po"]["po_id"], "supplier_dc_no": dc or f"DC-{uuid4().hex[:6]}",
            "lines": lines or [{"po_line_id": w["po"]["lines"][0]["po_line_id"],
                                "store_id": w["store"]["store_id"], "received_qty": qty}],
            **extra}


def post_grn(make_client, w, **kw):
    response = make_client("STORES").post(f"{INV}/grns", json=grn_body(w, **kw))
    assert response.status_code == 201, response.text
    return response.json()


def stock_of(client, w):
    return client.get(f"{INV}/stock?item_id={w['item']['item_id']}").json()["items"]


def reconciles(make_client):
    report = make_client("STORES").get(f"{INV}/reconciliation").json()
    assert report["ok"], report["problems"]


# ------------------------------------------------------------------ posting a receipt
def test_a_receipt_creates_a_held_lot_with_expiry_ledger_and_cost(make_client, api_database):
    w = world(make_client)
    grn = post_grn(make_client, w, qty="60")
    assert grn["status"] == "POSTED" and grn["grn_no"].startswith("GRN-") and grn["vendor_code"]
    line = grn["lines"][0]
    assert line["lot_status"] == "QA_HOLD" and line["lot_no"] == f"{grn['grn_no']}/1"
    assert line["inspected"] is False and float(line["received_qty"]) == 60
    received = datetime.fromisoformat(grn["received_at"])
    assert datetime.fromisoformat(line["expiry_at"]) - received == timedelta(days=180)

    rows = stock_of(make_client("STORES"), w)
    assert len(rows) == 1 and float(rows[0]["on_hand"]) == 60 and rows[0]["lot_status"] == "QA_HOLD"
    po = make_client("PURCHASE").get(f"{PO}/{w['po']['po_id']}").json()
    assert po["status"] == "PARTIALLY_RECEIVED" and float(po["lines"][0]["received_qty"]) == 60
    with psycopg.connect(api_database["owner"]) as conn:
        ledger = conn.execute("select movement_type, qty_delta, reference_type from erp.stock_ledger "
                              "where lot_id = %s", (line["lot_id"],)).fetchall()
        layer = conn.execute("select received_qty, remaining_qty, unit_cost from erp.cost_layer "
                             "where lot_id = %s", (line["lot_id"],)).fetchone()
    assert ledger == [("RECEIPT", 60, "GRN")] and tuple(map(float, layer)) == (60, 60, 50)
    value = make_client("ACCOUNTS").get(f"{INV}/valuation?unit_id={w['unit']['unit_id']}").json()
    assert float(value[0]["quantity"]) == 60 and float(value[0]["value"]) == 3000
    assert float(value[0]["average_cost"]) == 50
    reconciles(make_client)


def test_items_without_shelf_life_have_no_expiry(make_client):
    w = world(make_client, shelf=None)
    assert post_grn(make_client, w)["lines"][0]["expiry_at"] is None


def test_receiving_follows_the_tolerance_and_closes_the_order(make_client):
    w = world(make_client)
    over = make_client("STORES").post(f"{INV}/grns", json=grn_body(w, qty="111"))
    assert over.status_code == 409 and "Only 110" in over.json()["detail"]
    post_grn(make_client, w, qty="105")  # within pending + 10%
    assert make_client("PURCHASE").get(f"{PO}/{w['po']['po_id']}").json()["status"] == "RECEIVED"
    again = make_client("STORES").post(f"{INV}/grns", json=grn_body(w, qty="1"))
    assert again.status_code == 409 and "cannot receive goods" in again.json()["detail"]
    reconciles(make_client)


def test_the_same_delivery_challan_cannot_be_received_twice(make_client):
    w = world(make_client)
    post_grn(make_client, w, qty="10", dc="DC-777")
    stores = make_client("STORES")
    dup = stores.post(f"{INV}/grns", json=grn_body(w, qty="10", dc="  dc-777 "))
    assert dup.status_code == 409 and "already received" in dup.json()["detail"]
    assert post_grn(make_client, w, qty="10", dc="DC-778")["status"] == "POSTED"
    stores = make_client("STORES")  # post_grn built its own client, which closed the old one
    assert stores.get(f"{INV}/grns?po_id={w['po']['po_id']}").json()["total"] == 2


def test_a_failed_line_rolls_the_whole_receipt_back(make_client):
    w = world(make_client, items=2, qty="100")
    lines = [{"po_line_id": ln["po_line_id"], "store_id": w["store"]["store_id"],
              "received_qty": q} for ln, q in zip(w["po"]["lines"], ["50", "500"], strict=True)]
    refused = make_client("STORES").post(f"{INV}/grns", json=grn_body(w, lines=lines))
    assert refused.status_code == 409
    stores = make_client("STORES")
    assert stores.get(f"{INV}/grns?po_id={w['po']['po_id']}").json()["total"] == 0
    for item in w["items"]:
        assert stores.get(f"{INV}/stock?item_id={item['item_id']}").json()["total"] == 0
    po = make_client("PURCHASE").get(f"{PO}/{w['po']['po_id']}").json()
    assert po["status"] == "OPEN" and all(float(ln["received_qty"]) == 0 for ln in po["lines"])
    reconciles(make_client)


def test_receipt_validation(make_client):
    w = world(make_client)
    other = make_client("ADMIN").post("/api/v1/org/units", json={"code": code(), "name": "P2"}).json()
    foreign_store = make_client("ADMIN").post("/api/v1/org/stores", json={
        "unit_id": other["unit_id"], "code": "X", "name": "X", "store_type": "OTHER"}).json()
    line = grn_body(w)["lines"][0]
    stores = make_client("STORES")
    post = lambda **b: stores.post(f"{INV}/grns", json={**grn_body(w), **b}).status_code  # noqa: E731
    assert post(lines=[{**line, "store_id": foreign_store["store_id"]}]) == 422  # other plant
    assert post(lines=[{**line, "received_qty": "0"}]) == 422
    assert post(lines=[{**line, "po_line_id": str(uuid4())}]) == 422
    assert post(lines=[line, line]) == 422
    assert post(supplier_dc_no="") == 422
    assert post(received_at=(datetime.now(UTC) + timedelta(days=2)).isoformat()) == 422
    assert post(po_id=str(uuid4())) == 404
    assert post(tenant_id=str(uuid4())) == 422
    make_client("ADMIN").patch(f"/api/v1/org/stores/{w['store']['store_id']}", json={"active": False})
    assert make_client("STORES").post(f"{INV}/grns", json=grn_body(w)).status_code == 422


def test_an_unapproved_order_cannot_be_received(make_client):
    w = world(make_client)
    purchase = make_client("PURCHASE")
    draft = purchase.post(PO, json={
        "unit_id": w["unit"]["unit_id"], "vendor_id": w["vendor"]["vendor_id"],
        "lines": [{"item_id": w["item"]["item_id"], "ordered_qty": "10", "unit_price": "5",
                   "due_date": SOON}]}).json()
    body = {"po_id": draft["po_id"], "supplier_dc_no": "DC-D", "lines": [{
        "po_line_id": draft["lines"][0]["po_line_id"], "store_id": w["store"]["store_id"],
        "received_qty": "5"}]}
    refused = make_client("STORES").post(f"{INV}/grns", json=body)
    assert refused.status_code == 409 and "draft order" in refused.json()["detail"]


# ------------------------------------------------------------------ QA decision
def inspect(client, line, accepted, note=None):
    return client.post(f"{INV}/grn-lines/{line['grn_line_id']}/inspect",
                       json={"accepted_qty": accepted, "note": note})


def test_accepting_a_lot_makes_it_available(make_client):
    w = world(make_client)
    grn = post_grn(make_client, w)
    done = inspect(make_client("QA"), grn["lines"][0], "100")
    assert done.status_code == 200
    line = done.json()["lines"][0]
    assert line["lot_status"] == "AVAILABLE" and line["inspected"] and float(line["accepted_qty"]) == 100
    row = stock_of(make_client("STORES"), w)[0]
    assert row["lot_status"] == "AVAILABLE" and float(row["available"]) == 100
    assert inspect(make_client("QA"), line, "100").status_code == 409  # decided once
    reconciles(make_client)


def test_partial_acceptance_returns_the_rest_and_opens_a_purchase_return(make_client, api_database):
    w = world(make_client)
    grn = post_grn(make_client, w)
    done = inspect(make_client("QA"), grn["lines"][0], "70", "30 kg moisture failed")
    line = done.json()["lines"][0]
    assert float(line["accepted_qty"]) == 70 and float(line["rejected_qty"]) == 30
    assert float(stock_of(make_client("STORES"), w)[0]["on_hand"]) == 70
    with psycopg.connect(api_database["owner"]) as conn:
        moves = conn.execute("select movement_type, qty_delta from erp.stock_ledger where lot_id = %s "
                             "order by occurred_at", (line["lot_id"],)).fetchall()
        ret = conn.execute("select return_no, status from erp.purchase_return where grn_id = %s",
                           (grn["grn_id"],)).fetchone()
    assert [(m, float(q)) for m, q in moves] == [("RECEIPT", 100.0), ("QA_REJECT", -30.0)]
    assert ret[0].startswith("RET-") and ret[1] == "DRAFT"
    value = make_client("ACCOUNTS").get(f"{INV}/valuation").json()
    mine = [v for v in value if v["unit_id"] == w["unit"]["unit_id"]][0]
    assert float(mine["quantity"]) == 70 and float(mine["value"]) == 3500  # rejected cost left
    reconciles(make_client)


def test_rejecting_everything_blocks_the_lot(make_client):
    w = world(make_client)
    grn = post_grn(make_client, w)
    line = inspect(make_client("QA"), grn["lines"][0], "0").json()["lines"][0]
    assert line["lot_status"] == "BLOCKED" and float(line["rejected_qty"]) == 100
    assert stock_of(make_client("STORES"), w) == []  # nothing on hand
    assert make_client("STORES").get(f"{INV}/valuation").json() == [] or all(
        v["unit_id"] != w["unit"]["unit_id"] for v in make_client("STORES").get(f"{INV}/valuation").json())
    reconciles(make_client)


def test_inspection_rules_and_permissions(make_client):
    w = world(make_client)
    grn = post_grn(make_client, w)
    line = grn["lines"][0]
    for role in ("STORES", "PURCHASE", "PROD", "SALES"):
        assert inspect(make_client(role), line, "100").status_code == 403, role
    qa = make_client("QA")
    assert inspect(qa, line, "101").status_code == 422  # more than received
    assert inspect(qa, line, "-1").status_code == 422
    assert qa.post(f"{INV}/grn-lines/{uuid4()}/inspect", json={"accepted_qty": "1"}).status_code == 404
    assert inspect(qa, line, "100").status_code == 200


def test_quarantine_and_release(make_client):
    w = world(make_client)
    grn = post_grn(make_client, w)
    inspect(make_client("QA"), grn["lines"][0], "100")
    lot = grn["lines"][0]["lot_id"]
    qa = make_client("QA")
    assert qa.post(f"{INV}/lots/{lot}/quarantine", json={"reason": "x"}).status_code == 422
    held = qa.post(f"{INV}/lots/{lot}/quarantine", json={"reason": "Customer complaint"})
    assert held.status_code == 200 and held.json()["lot_status"] == "QUARANTINE"
    assert qa.post(f"{INV}/lots/{lot}/quarantine", json={"reason": "again"}).status_code == 409
    assert make_client("STORES").post(f"{INV}/lots/{lot}/quarantine", json={"reason": "no"}
                                      ).status_code == 403
    freed = make_client("QA").post(f"{INV}/lots/{lot}/unquarantine", json={"reason": "Cleared"})
    assert freed.json()["lot_status"] == "AVAILABLE"
    unchecked = world(make_client)
    held_lot = post_grn(make_client, unchecked)["lines"][0]["lot_id"]  # still QA_HOLD
    assert make_client("QA").post(f"{INV}/lots/{held_lot}/quarantine", json={"reason": "no way"}
                                  ).status_code == 409


# ------------------------------------------------------------------ reads, scope, isolation
def test_stock_reads_filters_and_permissions(make_client):
    w = world(make_client)
    grn = post_grn(make_client, w, qty="40")
    inspect(make_client("QA"), grn["lines"][0], "40")
    for role in ("OPERATOR", "HR"):  # no inventory module
        assert make_client(role).get(f"{INV}/stock").status_code == 403, role
        assert make_client(role).get(f"{INV}/grns").status_code == 403, role
    for role in ("STORES", "PURCHASE", "QA", "PROD", "SALES", "ACCOUNTS", "PLANT_HEAD"):
        assert make_client(role).get(f"{INV}/stock?item_id={w['item']['item_id']}").status_code == 200
    stores = make_client("STORES")
    mine = stores.get(f"{INV}/stock?store_id={w['store']['store_id']}").json()
    assert [r["lot_no"] for r in mine["items"]] == [grn["lines"][0]["lot_no"]]
    assert stores.get(f"{INV}/stock?lot_status=QA_HOLD&item_id={w['item']['item_id']}").json()["total"] == 0
    assert stores.get(f"{INV}/lots/{grn['lines'][0]['lot_id']}").json()["item_code"] == w["item"]["code"]
    assert stores.get(f"{INV}/lots/{uuid4()}").status_code == 404
    grn_view = stores.get(f"{INV}/grns/{grn['grn_id']}").json()
    assert grn_view["lines"][0]["lot_status"] == "AVAILABLE"


def test_only_grn_posters_receive_goods(make_client):
    w = world(make_client)
    for role in ("PURCHASE", "QA", "SALES", "ACCOUNTS", "OPERATOR", "PROD"):
        assert make_client(role).post(f"{INV}/grns", json=grn_body(w)).status_code == 403, role
    assert make_client("MD").post(f"{INV}/grns", json=grn_body(w, qty="1")).status_code == 201


def test_plant_scope_and_tenant_isolation(make_client, api_database):
    w = world(make_client)
    grn = post_grn(make_client, w)
    other = make_client("ADMIN").post("/api/v1/org/units", json={"code": code(), "name": "P2"}).json()
    scoped = make_client("STORES", subject="u-scoped-grn", unit_ids=[other["unit_id"]])
    assert scoped.post(f"{INV}/grns", json=grn_body(w, qty="1")).status_code == 403
    assert scoped.get(f"{INV}/grns/{grn['grn_id']}").status_code == 404
    assert scoped.get(f"{INV}/lots/{grn['lines'][0]['lot_id']}").status_code == 404
    assert scoped.get(f"{INV}/stock?item_id={w['item']['item_id']}").json()["total"] == 0
    qa = make_client("QA", subject="u-scoped-qa", unit_ids=[other["unit_id"]])
    assert inspect(qa, grn["lines"][0], "100").status_code == 404
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        tenant_b = api_database["tenant_b"]
        unit_b = conn.execute("insert into erp.unit(tenant_id, code, name) values (%s, 'BG', 'B') "
                              "returning unit_id", (tenant_b,)).fetchone()[0]
        store_b = conn.execute("insert into erp.store(tenant_id, unit_id, code, name, store_type) "
                               "values (%s, %s, 'BS', 'B', 'OTHER') returning store_id",
                               (tenant_b, unit_b)).fetchone()[0]
        uom = conn.execute("select uom_id from erp.uom where code = 'KG'").fetchone()[0]
        item_b = conn.execute("insert into erp.item(tenant_id, uom_id, code, name, item_type) values "
                              "(%s, %s, 'BI', 'B', 'RM') returning item_id", (tenant_b, uom)).fetchone()[0]
        lot_b = conn.execute("insert into erp.lot(tenant_id, unit_id, item_id, store_id, lot_no, "
                             "received_at, status) values (%s, %s, %s, %s, 'LB', now(), 'AVAILABLE') "
                             "returning lot_id", (tenant_b, unit_b, item_b, store_b)).fetchone()[0]
        conn.execute("insert into erp.stock_balance(tenant_id, unit_id, item_id, lot_id, store_id, "
                     "quantity_on_hand) values (%s, %s, %s, %s, %s, 5)",
                     (tenant_b, unit_b, item_b, lot_b, store_b))
    admin = make_client("ADMIN")
    assert admin.get(f"{INV}/lots/{lot_b}").status_code == 404
    assert str(lot_b) not in {r["lot_id"] for r in admin.get(f"{INV}/stock?limit=200").json()["items"]}


def test_reorder_suggestions_and_expiry_list(make_client):
    w = world(make_client)
    admin = make_client("ADMIN")
    admin.put(f"/api/v1/product/items/{w['item']['item_id']}/policies/{w['unit']['unit_id']}",
              json={"reorder_point": "80", "reorder_qty": "500"})
    flagged = make_client("PURCHASE").get(f"{INV}/reorder?unit_id={w['unit']['unit_id']}").json()
    assert [r["item_code"] for r in flagged] == [w["item"]["code"]]
    assert float(flagged[0]["available"]) == 0 and float(flagged[0]["suggested_qty"]) == 500
    grn = post_grn(make_client, w, qty="60")
    inspect(make_client("QA"), grn["lines"][0], "60")
    flagged = make_client("PURCHASE").get(f"{INV}/reorder?unit_id={w['unit']['unit_id']}").json()
    assert float(flagged[0]["available"]) == 60 and float(flagged[0]["suggested_qty"]) == 500
    post_grn(make_client, w, qty="30")  # held stock is not available
    assert make_client("PURCHASE").get(f"{INV}/reorder?unit_id={w['unit']['unit_id']}"
                                       ).json()[0]["available"] == "60.000"
    soon = make_client("STORES").get(f"{INV}/expiring?within_days=400").json()
    assert w["item"]["code"] in {e["item_code"] for e in soon}
    never = make_client("STORES").get(f"{INV}/expiring?within_days=10").json()
    assert w["item"]["code"] not in {e["item_code"] for e in never}
