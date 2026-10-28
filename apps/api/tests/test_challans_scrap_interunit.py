"""Challans with due-back tracking, scrap sales with approval, and inter-unit invoices."""

from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest

from .test_compound_batches import Fresh, reconciles
from .test_dispatch_invoice import fg_lot, fg_store
from .test_inventory_issue import receive
from .test_inventory_receipt import INV, code, world
from .test_inventory_transfer_count import new_plant, new_store, transfer
from .test_sales_orders import TODAY, S, setup_sales

pytestmark = pytest.mark.usefixtures("api_database")
A = "/api/v1/approvals"


def stocked_challan_world(make_client, api_database, quantity="100"):
    w = setup_sales(make_client)
    w["store"] = fg_store(make_client, w)
    w["lot"] = fg_lot(api_database, w, w["store"], quantity)
    return w


def challan(make_client, w, qty="40", ctype="RETURNABLE", role="SALES", **extra):
    body = {"unit_id": w["unit"]["unit_id"], "store_id": w["store"]["store_id"],
            "challan_type": ctype, "reason": "Heat treatment at job worker",
            "lines": [{"item_id": w["item"]["item_id"], "quantity": qty}], **extra}
    if ctype in ("RETURNABLE", "JOB_WORK", "NON_RETURNABLE"):
        body.setdefault("customer_id", w["customer"]["customer_id"])
    if ctype in ("RETURNABLE", "JOB_WORK"):
        body.setdefault("due_back", (TODAY + timedelta(days=10)).isoformat())
    return Fresh(make_client, role).post(f"{S}/challans", json=body)


def stock(make_client, w):
    got = Fresh(make_client, "STORES").get(
        f"/api/v1/inventory/stock?store_id={w['store']['store_id']}"
        f"&item_id={w['item']['item_id']}").json()["items"]
    return sum(float(r["on_hand"]) for r in got)


# ------------------------------------------------------------------ challans
def test_a_returnable_challan_takes_stock_out_and_back_in(make_client, api_database):
    w = stocked_challan_world(make_client, api_database)
    made = challan(make_client, w, "40")
    assert made.status_code == 201, made.text
    c = made.json()
    assert c["challan_no"].startswith("CHL-") and c["status"] == "OUT" and not c["overdue"]
    assert stock(make_client, w) == 60
    (line,) = c["lines"]
    assert float(line["outstanding_qty"]) == 40 and line["lot_no"] == w["lot"]["lot_no"]
    sales = Fresh(make_client, "SALES")
    back = lambda qty: sales.post(f"{S}/challans/{c['challan_id']}/return", json={  # noqa: E731
        "lines": [{"challan_line_id": line["challan_line_id"], "quantity": qty}]})
    part = back("15")
    assert part.status_code == 200 and part.json()["status"] == "PARTIALLY_RETURNED"
    assert stock(make_client, w) == 75 and float(part.json()["lines"][0]["outstanding_qty"]) == 25
    assert back("26").status_code == 409  # only 25 is out
    done = back("25")
    assert done.json()["status"] == "RETURNED" and stock(make_client, w) == 100
    assert back("1").status_code == 409  # everything is back
    reconciles(make_client)


def test_overdue_challans_are_tracked_until_everything_is_back(make_client, api_database):
    w = stocked_challan_world(make_client, api_database)
    c = challan(make_client, w, "10", "JOB_WORK").json()
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("update erp.challan set due_back = current_date - 4 where challan_id = %s",
                     (c["challan_id"],))
    sales = Fresh(make_client, "SALES")
    late = sales.get(f"{S}/challans/{c['challan_id']}").json()
    assert late["overdue"] is True and late["days_overdue"] == 4
    listed = sales.get(f"{S}/challans?overdue=true&limit=200").json()["items"]
    assert c["challan_id"] in {x["challan_id"] for x in listed}
    fresh = challan(make_client, w, "5").json()
    assert fresh["challan_id"] not in {x["challan_id"] for x in listed}
    sales.post(f"{S}/challans/{c['challan_id']}/return", json={"lines": [{
        "challan_line_id": late["lines"][0]["challan_line_id"], "quantity": "10"}]})
    after = sales.get(f"{S}/challans?overdue=true&limit=200").json()["items"]
    assert c["challan_id"] not in {x["challan_id"] for x in after}  # returned: no longer late


def test_challan_types_and_their_rules(make_client, api_database):
    w = stocked_challan_world(make_client, api_database)
    day = (TODAY + timedelta(days=5)).isoformat()
    assert challan(make_client, w, "5", "RETURNABLE", customer_id=None).status_code == 422
    assert challan(make_client, w, "5", "RETURNABLE", due_back=None).status_code == 422
    assert challan(make_client, w, "5", "RETURNABLE", due_back=(
        TODAY - timedelta(days=1)).isoformat()).status_code == 422
    assert challan(make_client, w, "5", "NON_RETURNABLE", due_back=day).status_code == 422
    assert challan(make_client, w, "5", "NON_RETURNABLE", customer_id=None).status_code == 422
    sample = challan(make_client, w, "2", "FOC")  # free samples need no customer
    assert sample.status_code == 201 and sample.json()["customer_id"] is None
    gift = challan(make_client, w, "3", "NON_RETURNABLE")
    assert gift.status_code == 201 and gift.json()["due_back"] is None
    line = gift.json()["lines"][0]["challan_line_id"]
    refused = Fresh(make_client, "SALES").post(f"{S}/challans/{gift.json()['challan_id']}/return",
                                               json={"lines": [{"challan_line_id": line,
                                                                "quantity": "1"}]})
    assert refused.status_code == 409 and "returnable" in refused.json()["detail"]
    assert stock(make_client, w) == 95


def test_a_challan_cannot_send_what_is_not_in_stock(make_client, api_database):
    w = stocked_challan_world(make_client, api_database, "20")
    refused = challan(make_client, w, "30")
    assert refused.status_code == 409 and "Not enough stock" in refused.json()["detail"]
    assert stock(make_client, w) == 20
    held = fg_lot(api_database, w, w["store"], "50", days_old=40)
    Fresh(make_client, "QA").post("/api/v1/quality/holds", json={
        "subject_type": "STOCK_LOT", "subject_id": held["lot_id"], "reason": "Complaint"})
    assert challan(make_client, w, "30").status_code == 409  # the held lot is not usable
    assert challan(make_client, w, "20").status_code == 201


def test_challan_permissions_and_scope(make_client, api_database):
    w = stocked_challan_world(make_client, api_database)
    for role in ("PURCHASE", "QA", "ACCOUNTS"):
        assert challan(make_client, w, "1", role=role).status_code == 403, role
    other_plant = Fresh(make_client, "ADMIN").post("/api/v1/org/units",
                                                    json={"code": code(), "name": "P2"}).json()
    c = challan(make_client, w, "5").json()
    away = Fresh(make_client, "SALES", subject="u-chl-out", unit_ids=[other_plant["unit_id"]])
    assert away.get(f"{S}/challans/{c['challan_id']}").status_code == 404
    assert away.get(f"{S}/challans").json()["total"] == 0
    assert away.post(f"{S}/challans/{c['challan_id']}/return", json={"lines": [{
        "challan_line_id": c["lines"][0]["challan_line_id"], "quantity": "1"}]}).status_code == 404
    wrong_store = Fresh(make_client, "SALES").post(f"{S}/challans", json={
        "unit_id": other_plant["unit_id"], "store_id": w["store"]["store_id"],
        "challan_type": "FOC", "reason": "Sample", "lines": [{
            "item_id": w["item"]["item_id"], "quantity": "1"}]})
    assert wrong_store.status_code == 422


# ------------------------------------------------------------------ scrap sales
def decide(make_client, approval_id, role, decision="APPROVE", note=None):
    return Fresh(make_client, role).post(f"{A}/{approval_id}/decide",
                                         json={"decision": decision, "note": note})


def draft_scrap(make_client, w, amount="25000"):
    made = Fresh(make_client, "SALES").post(f"{S}/scrap-sales", json={
        "unit_id": w["unit"]["unit_id"], "customer_id": w["customer"]["customer_id"],
        "description": "Rubber trimmings and rejected metal inserts", "quantity": "850",
        "amount": amount})
    assert made.status_code == 201, made.text
    return made.json()


def test_a_scrap_sale_is_invoiced_only_after_both_approvals(make_client):
    w = setup_sales(make_client)
    sale = draft_scrap(make_client, w)
    assert sale["sale_no"].startswith("SCR-") and sale["status"] == "DRAFT"
    sales = Fresh(make_client, "SALES")
    assert sales.post(f"{S}/scrap-sales/{sale['scrap_sale_id']}/invoice").status_code == 409
    sent = sales.post(f"{S}/scrap-sales/{sale['scrap_sale_id']}/submit")
    assert sent.status_code == 200 and sent.json()["status"] == "PENDING_APPROVAL"
    approval = sent.json()["approval_id"]
    assert approval and sales.post(f"{S}/scrap-sales/{sale['scrap_sale_id']}/submit"
                                   ).status_code == 409
    assert decide(make_client, approval, "PLANT_HEAD").status_code == 200
    assert sales.get(f"{S}/scrap-sales/{sale['scrap_sale_id']}").json()["status"] == (
        "PENDING_APPROVAL")  # Accounts has not approved yet
    assert sales.post(f"{S}/scrap-sales/{sale['scrap_sale_id']}/invoice").status_code == 409
    assert decide(make_client, approval, "ACCOUNTS").status_code == 200
    assert sales.get(f"{S}/scrap-sales/{sale['scrap_sale_id']}").json()["status"] == "APPROVED"
    done = sales.post(f"{S}/scrap-sales/{sale['scrap_sale_id']}/invoice")
    assert done.status_code == 200 and done.json()["status"] == "INVOICED"
    assert done.json()["invoice_no"].startswith("INV-") and done.json()["invoiced_at"]
    assert sales.post(f"{S}/scrap-sales/{sale['scrap_sale_id']}/invoice").status_code == 409


def test_a_rejected_scrap_sale_is_final_and_a_draft_can_be_cancelled(make_client):
    w = setup_sales(make_client)
    sale = draft_scrap(make_client, w, "9000")
    sales = Fresh(make_client, "SALES")
    approval = sales.post(f"{S}/scrap-sales/{sale['scrap_sale_id']}/submit").json()["approval_id"]
    assert decide(make_client, approval, "PLANT_HEAD", "REJECT", "Price too low").status_code == 200
    got = sales.get(f"{S}/scrap-sales/{sale['scrap_sale_id']}").json()
    assert got["status"] == "REJECTED"
    assert sales.post(f"{S}/scrap-sales/{sale['scrap_sale_id']}/invoice").status_code == 409
    assert sales.post(f"{S}/scrap-sales/{sale['scrap_sale_id']}/cancel").status_code == 409
    other = draft_scrap(make_client, w)
    assert sales.post(f"{S}/scrap-sales/{other['scrap_sale_id']}/cancel").json()["status"] == (
        "CANCELLED")
    assert sales.get(f"{S}/scrap-sales?status=rejected&limit=200").json()["total"] >= 1


def test_scrap_sale_validation_and_permissions(make_client):
    w = setup_sales(make_client)
    sales = Fresh(make_client, "SALES")
    body = {"unit_id": w["unit"]["unit_id"], "description": "Old press", "quantity": "1",
            "amount": "50000"}
    assert sales.post(f"{S}/scrap-sales", json={**body, "quantity": "0"}).status_code == 422
    assert sales.post(f"{S}/scrap-sales", json={**body, "amount": "-5"}).status_code == 422
    assert sales.post(f"{S}/scrap-sales", json={**body, "description": "x"}).status_code == 422
    assert sales.post(f"{S}/scrap-sales", json={**body, "customer_id": str(uuid4())}
                      ).status_code == 422
    assert sales.post(f"{S}/scrap-sales", json=body).status_code == 201  # no customer yet is fine
    for role in ("PURCHASE", "STORES", "QA"):
        assert Fresh(make_client, role).post(f"{S}/scrap-sales", json=body).status_code == 403
    away_unit = Fresh(make_client, "ADMIN").post("/api/v1/org/units",
                                                  json={"code": code(), "name": "P2"}).json()
    away = Fresh(make_client, "SALES", subject="u-scr-out", unit_ids=[away_unit["unit_id"]])
    sale = draft_scrap(make_client, w)
    assert away.get(f"{S}/scrap-sales/{sale['scrap_sale_id']}").status_code == 404
    assert away.post(f"{S}/scrap-sales/{sale['scrap_sale_id']}/submit").status_code == 404


# ------------------------------------------------------------------ inter-unit invoices
def inter_plant_transfer(make_client, api_database, qty="30", dispatch=True):
    w = world(make_client, qty="100", price="10")
    lot = receive(make_client, w, "100")
    plant2 = new_plant(make_client)
    dst = new_store(make_client, plant2["unit_id"])["store_id"]
    t = transfer(make_client, w["store"]["store_id"], dst, lot, qty).json()
    if dispatch:
        assert Fresh(make_client, "STORES").post(
            f"{INV}/transfers/{t['transfer_id']}/dispatch").status_code == 200
    return w, plant2, t


def test_an_inter_plant_transfer_gets_one_invoice_valued_at_cost(make_client, api_database):
    w, plant2, t = inter_plant_transfer(make_client, api_database)
    acct = Fresh(make_client, "ACCOUNTS")
    pending = acct.get(f"{S}/inter-unit-invoices/pending").json()
    assert t["transfer_id"] in {p["transfer_id"] for p in pending}
    made = acct.post(f"{S}/inter-unit-invoices", json={"transfer_id": t["transfer_id"]})
    assert made.status_code == 201, made.text
    inv = made.json()
    assert inv["invoice_no"].startswith("IUI-") and inv["status"] == "ISSUED"
    assert float(inv["taxable_amount"]) == 300 and inv["transfer_no"] == t["transfer_no"]
    assert inv["from_unit_id"] == w["unit"]["unit_id"] and inv["to_unit_id"] == plant2["unit_id"]
    assert acct.post(f"{S}/inter-unit-invoices", json={"transfer_id": t["transfer_id"]}
                     ).status_code == 409
    assert t["transfer_id"] not in {p["transfer_id"] for p in acct.get(
        f"{S}/inter-unit-invoices/pending").json()}
    ref = f"{S}/inter-unit-invoices/{inv['inter_unit_invoice_id']}/gst-reference"
    assert acct.post(ref, json={}).status_code == 422
    done = acct.post(ref, json={"irn": "c" * 64, "ewb_no": "441000111222"})
    assert done.status_code == 200 and done.json()["handoff_status"] == "DONE"
    assert acct.post(ref, json={"ewb_no": "441000999999"}).status_code == 409
    assert acct.post(f"{S}/inter-unit-invoices/{inv['inter_unit_invoice_id']}/cancel"
                     ).status_code == 409
    assert acct.get(f"{S}/inter-unit-invoices?status=issued").json()["total"] >= 1


def test_inter_unit_invoice_rules_and_permissions(make_client, api_database):
    w, plant2, draft = inter_plant_transfer(make_client, api_database, dispatch=False)
    acct = Fresh(make_client, "ACCOUNTS")
    refused = acct.post(f"{S}/inter-unit-invoices", json={"transfer_id": draft["transfer_id"]})
    assert refused.status_code == 409 and "not been dispatched" in refused.json()["detail"]
    same_plant = world(make_client, qty="50")
    lot = receive(make_client, same_plant, "50")
    near = new_store(make_client, same_plant["unit"]["unit_id"])["store_id"]
    t = transfer(make_client, same_plant["store"]["store_id"], near, lot, "10").json()
    Fresh(make_client, "STORES").post(f"{INV}/transfers/{t['transfer_id']}/dispatch")
    assert acct.post(f"{S}/inter-unit-invoices", json={"transfer_id": t["transfer_id"]}
                     ).status_code == 422  # same plant
    assert acct.post(f"{S}/inter-unit-invoices", json={"transfer_id": str(uuid4())}
                     ).status_code == 404
    for role in ("SALES", "STORES", "QA", "PURCHASE"):
        assert Fresh(make_client, role).post(f"{S}/inter-unit-invoices", json={
            "transfer_id": draft["transfer_id"]}).status_code == 403, role
    Fresh(make_client, "STORES").post(f"{INV}/transfers/{draft['transfer_id']}/dispatch")
    stranger_unit = new_plant(make_client)
    away = Fresh(make_client, "ACCOUNTS", subject="u-iu-out", unit_ids=[stranger_unit["unit_id"]])
    assert away.post(f"{S}/inter-unit-invoices", json={"transfer_id": draft["transfer_id"]}
                     ).status_code == 404
    made = acct.post(f"{S}/inter-unit-invoices", json={"transfer_id": draft["transfer_id"]}).json()
    cancelled = acct.post(f"{S}/inter-unit-invoices/{made['inter_unit_invoice_id']}/cancel")
    assert cancelled.json()["handoff_status"] == "CANCELLED"
    again = acct.post(f"{S}/inter-unit-invoices", json={"transfer_id": draft["transfer_id"]})
    assert again.status_code == 201  # a cancelled invoice frees the transfer
