"""Picking, dispatch, packing list, invoices with the GST hand-off, and stock protection."""

from datetime import date, timedelta
from uuid import uuid4

import psycopg
import pytest

from .conftest import TENANT_A
from .test_compound_batches import Fresh, reconciles
from .test_inventory_receipt import code
from .test_sales_orders import TODAY, S, order, price, setup_sales

pytestmark = pytest.mark.usefixtures("api_database")
INV = "/api/v1/inventory"
Q = "/api/v1/quality"


def fg_store(make_client, w):
    made = Fresh(make_client, "ADMIN").post("/api/v1/org/stores", json={
        "unit_id": w["unit"]["unit_id"], "code": "FG" + code()[:4], "name": "Finished goods",
        "store_type": "FINISHED_GOODS"})
    assert made.status_code == 201, made.text
    return made.json()


def fg_lot(api_database, w, store, quantity, days_old=0, expires_in_days=None, item=None):
    """Put finished goods straight into stock (ledger, balance and FIFO layer stay consistent)."""
    item = item or w["item"]
    lot_no = "FG-" + uuid4().hex[:8].upper()
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        lot_id = conn.execute(
            "insert into erp.lot(tenant_id, unit_id, item_id, store_id, lot_no, received_at, "
            "expiry_at, status) values (%s, %s, %s, %s, %s, now() - make_interval(days => %s), "
            "case when %s::int is null then null else now() + make_interval(days => %s::int) end, "
            "'AVAILABLE') returning lot_id",
            (TENANT_A, w["unit"]["unit_id"], item["item_id"], store["store_id"], lot_no,
             days_old, expires_in_days, expires_in_days)).fetchone()[0]
        ledger_id = conn.execute(
            "insert into erp.stock_ledger(tenant_id, unit_id, item_id, lot_id, store_id, qty_delta, "
            "movement_type, reference_type, reference_id, idempotency_key, occurred_at) values "
            "(%s, %s, %s, %s, %s, %s, 'RECEIPT', 'TEST', gen_random_uuid(), %s, "
            "now() - make_interval(days => %s)) returning ledger_id",
            (TENANT_A, w["unit"]["unit_id"], item["item_id"], lot_id, store["store_id"], quantity,
             "test:" + lot_no, days_old)).fetchone()[0]
        conn.execute(
            "insert into erp.stock_balance(tenant_id, unit_id, item_id, lot_id, store_id, "
            "quantity_on_hand) values (%s, %s, %s, %s, %s, %s)",
            (TENANT_A, w["unit"]["unit_id"], item["item_id"], lot_id, store["store_id"], quantity))
        conn.execute(
            "insert into erp.cost_layer(tenant_id, item_id, lot_id, receipt_ledger_id, "
            "received_qty, remaining_qty, unit_cost) values (%s, %s, %s, %s, %s, %s, 40)",
            (TENANT_A, item["item_id"], lot_id, ledger_id, quantity, quantity))
    return {"lot_id": str(lot_id), "lot_no": lot_no}


def sales_world(make_client, api_database, ordered="100", schedule=True, **kw):
    w = setup_sales(make_client, **kw)
    w["store"] = fg_store(make_client, w)
    price(make_client, w, amount="200")
    body = [{"item_id": w["item"]["item_id"], "ordered_qty": ordered,
             "tax_code_id": w["tax"]["tax_code_id"],
             "schedule": ([{"due_date": (TODAY + timedelta(days=5)).isoformat(),
                            "quantity": "60"},
                           {"due_date": (TODAY + timedelta(days=15)).isoformat(),
                            "quantity": "40"}] if schedule else [])}]
    w["so"] = order(make_client, w, lines=body, customer_po_no="PO-" + code()[:6]).json()
    w["line"] = w["so"]["lines"][0]
    return w


def dispatch_body(w, qty, **extra):
    return {"so_id": w["so"]["so_id"], "store_id": w["store"]["store_id"],
            "lines": [{"so_line_id": w["line"]["so_line_id"], "quantity": qty}], **extra}


def create(make_client, w, qty, role="SALES", **extra):
    return Fresh(make_client, role).post(f"{S}/dispatches", json=dispatch_body(w, qty, **extra))


def on_hand(make_client, w):
    got = Fresh(make_client, "STORES").get(
        f"{INV}/stock?store_id={w['store']['store_id']}&item_id={w['item']['item_id']}").json()
    return {r["lot_no"]: (float(r["on_hand"]), float(r["reserved"])) for r in got["items"]}


# ------------------------------------------------------------------ the main flow
def test_dispatch_picks_fifo_reserves_then_ships_updates_order_and_schedule(
        make_client, api_database):
    w = sales_world(make_client, api_database)
    old = fg_lot(api_database, w, w["store"], "50", days_old=20)
    new = fg_lot(api_database, w, w["store"], "80", days_old=2)
    sales = Fresh(make_client, "SALES")
    plan = sales.post(f"{S}/dispatches/plan", json=dispatch_body(w, "60")).json()
    assert plan["can_dispatch"] is True and float(plan["lines"][0]["open_qty"]) == 100
    assert [(p["lot_no"], float(p["quantity"])) for p in plan["lines"][0]["picks"]] == [
        (old["lot_no"], 50.0), (new["lot_no"], 10.0)]  # oldest lot first
    made = create(make_client, w, "60", vehicle_no="HR55AB1234", transporter="Blue Dart")
    assert made.status_code == 201, made.text
    d = made.json()
    assert d["dispatch_no"].startswith("DSP-") and d["status"] == "DRAFT"
    assert [(ln["lot_no"], float(ln["quantity"])) for ln in d["lines"]] == [
        (old["lot_no"], 50.0), (new["lot_no"], 10.0)]
    assert on_hand(make_client, w) == {old["lot_no"]: (50.0, 50.0), new["lot_no"]: (80.0, 10.0)}
    more = create(make_client, w, "50")  # 100 ordered, 60 already picked: only 40 left open
    assert more.status_code == 409 and "only 40" in more.json()["detail"]
    sent = sales.post(f"{S}/dispatches/{d['dispatch_id']}/dispatch")
    assert sent.status_code == 200 and sent.json()["status"] == "DISPATCHED"
    assert on_hand(make_client, w) == {new["lot_no"]: (70.0, 0.0)}  # the old lot is used up
    so = sales.get(f"{S}/orders/{w['so']['so_id']}").json()
    line = so["lines"][0]
    assert so["status"] == "PARTIALLY_SHIPPED" and float(line["shipped_qty"]) == 60
    assert [(float(s["scheduled_qty"]), float(s["fulfilled_qty"])) for s in line["schedule"]] == [
        (60.0, 60.0), (40.0, 0.0)]
    assert sales.post(f"{S}/dispatches/{d['dispatch_id']}/dispatch").status_code == 409
    assert sales.post(f"{S}/dispatches/{d['dispatch_id']}/cancel").status_code == 409
    second = create(make_client, w, "40").json()
    sales.post(f"{S}/dispatches/{second['dispatch_id']}/dispatch")
    so = sales.get(f"{S}/orders/{w['so']['so_id']}").json()
    assert so["status"] == "SHIPPED"
    assert [float(s["fulfilled_qty"]) for s in so["lines"][0]["schedule"]] == [60.0, 40.0]
    assert create(make_client, w, "1").status_code == 409  # nothing left to dispatch
    assert sales.get(f"{S}/dispatches?so_id={w['so']['so_id']}&status=dispatched").json()[
        "total"] == 2
    reconciles(make_client)


def test_packing_list_carries_customer_part_numbers_and_lots(make_client, api_database):
    w = sales_world(make_client, api_database)
    lot = fg_lot(api_database, w, w["store"], "100", expires_in_days=90)
    d = create(make_client, w, "30", vehicle_no="DL01X9").json()
    sheet = Fresh(make_client, "SALES").get(f"{S}/dispatches/{d['dispatch_id']}/packing-list")
    assert sheet.status_code == 200
    p = sheet.json()
    assert p["dispatch_no"] == d["dispatch_no"] and p["so_no"] == w["so"]["so_no"]
    assert p["customer_po_no"] == w["so"]["customer_po_no"] and p["vehicle_no"] == "DL01X9"
    (line,) = p["lines"]
    assert line["customer_part_no"] == "CP-1" and line["lot_no"] == lot["lot_no"]
    assert float(line["quantity"]) == 30 and float(p["total_quantity"]) == 30 and line["expiry_at"]


# ------------------------------------------------------------------ stock protection
def test_a_shortage_blocks_the_dispatch_and_reserves_nothing(make_client, api_database):
    w = sales_world(make_client, api_database)
    lot = fg_lot(api_database, w, w["store"], "30")
    sales = Fresh(make_client, "SALES")
    plan = sales.post(f"{S}/dispatches/plan", json=dispatch_body(w, "50")).json()
    assert plan["can_dispatch"] is False and float(plan["lines"][0]["shortfall"]) == 20
    refused = create(make_client, w, "50")
    assert refused.status_code == 409 and "Not enough finished-goods stock" in refused.json()["detail"]
    assert sales.get(f"{S}/dispatches?so_id={w['so']['so_id']}").json()["total"] == 0
    assert on_hand(make_client, w) == {lot["lot_no"]: (30.0, 0.0)}


def test_held_and_expired_lots_are_never_picked(make_client, api_database):
    w = sales_world(make_client, api_database)
    good = fg_lot(api_database, w, w["store"], "40", days_old=1)
    held = fg_lot(api_database, w, w["store"], "40", days_old=30)  # oldest, but QA holds it
    stale = fg_lot(api_database, w, w["store"], "40", days_old=60, expires_in_days=0)
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("update erp.lot set expiry_at = now() - interval '1 day' where lot_id = %s",
                     (stale["lot_id"],))
    placed = Fresh(make_client, "QA").post(f"{Q}/holds", json={
        "subject_type": "STOCK_LOT", "subject_id": held["lot_id"], "reason": "Customer complaint"})
    assert placed.status_code == 201, placed.text
    plan = Fresh(make_client, "SALES").post(f"{S}/dispatches/plan", json=dispatch_body(w, "60")).json()
    assert plan["can_dispatch"] is False and float(plan["lines"][0]["shortfall"]) == 20
    assert [p["lot_no"] for p in plan["lines"][0]["picks"]] == [good["lot_no"]]
    assert create(make_client, w, "60").status_code == 409
    assert create(make_client, w, "40").status_code == 201


def test_cancelling_a_draft_dispatch_releases_the_stock(make_client, api_database):
    w = sales_world(make_client, api_database)
    lot = fg_lot(api_database, w, w["store"], "100")
    d = create(make_client, w, "70").json()
    assert on_hand(make_client, w) == {lot["lot_no"]: (100.0, 70.0)}
    sales = Fresh(make_client, "SALES")
    gone = sales.post(f"{S}/dispatches/{d['dispatch_id']}/cancel")
    assert gone.status_code == 200 and gone.json()["status"] == "CANCELLED"
    assert on_hand(make_client, w) == {lot["lot_no"]: (100.0, 0.0)}
    assert sales.post(f"{S}/dispatches/{d['dispatch_id']}/dispatch").status_code == 409
    assert create(make_client, w, "100").status_code == 201  # all of it is free again
    reserved = Fresh(make_client, "STORES").get(
        f"{INV}/reservations?reference={d['dispatch_no']}&status=released").json()
    assert len(reserved) == 1


def test_dispatch_validation_permissions_and_scope(make_client, api_database):
    w = sales_world(make_client, api_database)
    fg_lot(api_database, w, w["store"], "100")
    sales = Fresh(make_client, "SALES")
    other_unit = Fresh(make_client, "ADMIN").post("/api/v1/org/units",
                                                   json={"code": code(), "name": "P2"}).json()
    elsewhere = Fresh(make_client, "ADMIN").post("/api/v1/org/stores", json={
        "unit_id": other_unit["unit_id"], "code": "FG", "name": "Other FG",
        "store_type": "FINISHED_GOODS"}).json()
    assert sales.post(f"{S}/dispatches", json={**dispatch_body(w, "5"), "store_id":
                      elsewhere["store_id"]}).status_code == 422  # another plant's store
    assert sales.post(f"{S}/dispatches", json={**dispatch_body(w, "5"), "so_id":
                      str(uuid4())}).status_code == 404
    assert sales.post(f"{S}/dispatches", json={**dispatch_body(w, "5"), "lines": [
        {"so_line_id": str(uuid4()), "quantity": "5"}]}).status_code == 422
    assert create(make_client, w, "0").status_code == 422
    for role in ("PURCHASE", "STORES", "QA", "PROD"):
        assert create(make_client, w, "5", role=role).status_code == 403, role
    away = Fresh(make_client, "SALES", subject="u-disp-out", unit_ids=[other_unit["unit_id"]])
    d = create(make_client, w, "5").json()
    assert away.get(f"{S}/dispatches/{d['dispatch_id']}").status_code == 404
    assert away.post(f"{S}/dispatches/{d['dispatch_id']}/dispatch").status_code == 404
    assert away.get(f"{S}/dispatches").json()["total"] == 0
    sales.post(f"{S}/dispatches/{d['dispatch_id']}/cancel")
    sales.post(f"{S}/orders/{w['so']['so_id']}/cancel")
    assert create(make_client, w, "5").status_code == 409  # the order is cancelled


# ------------------------------------------------------------------ invoices
def shipped_dispatch(make_client, api_database, w, qty="50"):
    fg_lot(api_database, w, w["store"], "100")
    d = create(make_client, w, qty).json()
    sent = Fresh(make_client, "SALES").post(f"{S}/dispatches/{d['dispatch_id']}/dispatch")
    assert sent.status_code == 200, sent.text
    return d


def test_invoice_follows_the_dispatch_with_gst_and_one_live_invoice(make_client, api_database):
    w = sales_world(make_client, api_database)
    d = shipped_dispatch(make_client, api_database, w, "50")
    sales = Fresh(make_client, "SALES")
    made = sales.post(f"{S}/dispatches/{d['dispatch_id']}/invoice")
    assert made.status_code == 201, made.text
    inv = made.json()
    assert inv["invoice_no"].startswith("INV-") and inv["status"] == "ISSUED"
    assert float(inv["taxable_amount"]) == 10000 and float(inv["tax_amount"]) == 1800
    assert float(inv["gross_amount"]) == 11800 and inv["handoff_status"] == "PENDING"
    assert inv["due_date"] == TODAY.isoformat()  # no payment term on this customer
    (line,) = inv["lines"]
    assert float(line["quantity"]) == 50 and line["customer_part_no"] == "CP-1"
    assert sales.post(f"{S}/dispatches/{d['dispatch_id']}/invoice").status_code == 409
    gone = sales.post(f"{S}/invoices/{inv['invoice_id']}/cancel")
    assert gone.status_code == 200 and gone.json()["handoff_status"] == "CANCELLED"
    again = sales.post(f"{S}/dispatches/{d['dispatch_id']}/invoice")
    assert again.status_code == 201 and again.json()["invoice_no"] != inv["invoice_no"]
    assert sales.get(f"{S}/invoices?dispatch_id={d['dispatch_id']}").json()["total"] == 2
    assert sales.get(f"{S}/invoices/{again.json()['invoice_id']}").status_code == 200


def test_a_draft_dispatch_cannot_be_invoiced_and_gst_references_are_recorded_once(
        make_client, api_database):
    w = sales_world(make_client, api_database)
    fg_lot(api_database, w, w["store"], "100")
    draft = create(make_client, w, "10").json()
    sales = Fresh(make_client, "SALES")
    assert sales.post(f"{S}/dispatches/{draft['dispatch_id']}/invoice").status_code == 409
    sales.post(f"{S}/dispatches/{draft['dispatch_id']}/dispatch")
    inv = sales.post(f"{S}/dispatches/{draft['dispatch_id']}/invoice").json()
    ref = f"{S}/invoices/{inv['invoice_id']}/gst-reference"
    assert sales.post(ref, json={"irn": "a" * 64}).status_code == 403  # needs the gst permission
    acct = Fresh(make_client, "ACCOUNTS")
    assert acct.post(ref, json={}).status_code == 422
    done = acct.post(ref, json={"irn": "a" * 64, "irn_ack_no": "112233445566", "ewb_no": "331000111222"})
    assert done.status_code == 200 and done.json()["handoff_status"] == "DONE"
    assert done.json()["ewb_no"] == "331000111222"
    assert acct.post(ref, json={"irn": "b" * 64}).status_code == 409  # recorded once
    blocked = sales.post(f"{S}/invoices/{inv['invoice_id']}/cancel")
    assert blocked.status_code == 409 and "GST portal" in blocked.json()["detail"]


def test_invoice_uses_the_payment_term_and_credit_check_only_warns(make_client, api_database):
    w = sales_world(make_client, api_database, credit_limit=5000)
    term = Fresh(make_client, "ADMIN").post("/api/v1/ref/payment-terms", json={
        "code": "N" + code()[:5], "name": "Net 45", "due_days": 45})
    assert term.status_code == 201, term.text
    patch = Fresh(make_client, "SALES").patch(
        f"/api/v1/parties/customers/{w['customer']['customer_id']}",
        json={"payment_term_id": term.json()["payment_term_id"]})
    assert patch.status_code == 200, patch.text
    d = shipped_dispatch(make_client, api_database, w, "50")
    inv = Fresh(make_client, "SALES").post(f"{S}/dispatches/{d['dispatch_id']}/invoice").json()
    assert inv["due_date"] == (TODAY + timedelta(days=45)).isoformat()
    assert inv["warnings"] and "credit limit" in inv["warnings"][0]  # 11,800 + open orders > 5,000
    assert Fresh(make_client, "SALES").get(
        f"{S}/customers/{w['customer']['customer_id']}/credit").json()["invoiced"] == "11800.00"


def test_invoice_permissions_and_scope(make_client, api_database):
    w = sales_world(make_client, api_database)
    d = shipped_dispatch(make_client, api_database, w, "10")
    for role in ("PURCHASE", "STORES", "QA"):
        assert Fresh(make_client, role).post(
            f"{S}/dispatches/{d['dispatch_id']}/invoice").status_code == 403, role
    inv = Fresh(make_client, "SALES").post(f"{S}/dispatches/{d['dispatch_id']}/invoice").json()
    other = Fresh(make_client, "ADMIN").post("/api/v1/org/units",
                                              json={"code": code(), "name": "P2"}).json()
    away = Fresh(make_client, "SALES", subject="u-inv-out", unit_ids=[other["unit_id"]])
    assert away.get(f"{S}/invoices/{inv['invoice_id']}").status_code == 404
    assert away.post(f"{S}/invoices/{inv['invoice_id']}/cancel").status_code == 404
    assert away.get(f"{S}/invoices").json()["total"] == 0
    assert date.today().isoformat() == inv["issue_date"]
