"""Receipt postings, supplier bills with 3-way match, booking, payment runs and the bank file."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from .test_compound_batches import Fresh
from .test_inventory_issue import receive
from .test_inventory_receipt import SOON, code, inspect, world
from .test_rfq import make_vendors

pytestmark = pytest.mark.usefixtures("api_database")
F = "/api/v1/finance"
PO = "/api/v1/procurement/purchase-orders"
INV = "/api/v1/inventory"
TODAY = date.today()


def payables_world(make_client, with_chart=True, qty="100"):
    """A plant with an approved vendor, an open PO (100 at 50, GST 18%) and 100 received and accepted."""
    w = world(make_client, qty="300", price="50")
    unit = w["unit"]["unit_id"]
    w["acc"] = {}
    if with_chart:
        seeded = Fresh(make_client, "ACCOUNTS").post(f"{F}/accounts/seed-default",
                                                     json={"unit_id": unit})
        assert seeded.status_code == 200, seeded.text
        w["acc"] = {a["code"]: a["account_id"] for a in seeded.json()}
    w["grn"] = receive(make_client, w, qty)
    w["po_line"] = w["po"]["lines"][0]
    return w


def bill_body(w, qty="100", rate="50", invoice="INV-1", day=None, vendor=None, lines=None):
    return {"unit_id": w["unit"]["unit_id"], "vendor_id": (vendor or w["vendor"])["vendor_id"],
            "supplier_invoice_no": invoice, "invoice_date": (day or TODAY).isoformat(),
            "lines": lines or [{"po_line_id": w["po_line"]["po_line_id"],
                                "grn_line_id": w["grn"]["grn_line_id"], "quantity": qty,
                                "unit_rate": rate, "tax_code_id": w["po_line"]["tax_code_id"]}]}


def enter(make_client, w, role="ACCOUNTS", **kw):
    return Fresh(make_client, role).post(f"{F}/bills", json=bill_body(w, **kw))


def live(books):
    """Only the accounts that still hold a balance."""
    return {code: amount for code, amount in books.items() if amount}


def balances(make_client, w):
    tb = Fresh(make_client, "ACCOUNTS").get(
        f"{F}/trial-balance?unit_id={w['unit']['unit_id']}").json()
    return {r["code"]: float(r["closing"]) for r in tb["rows"]}


# ------------------------------------------------------------------ receipt posting
def test_accepted_goods_are_booked_into_inventory_against_goods_received_not_billed(make_client):
    w = payables_world(make_client)
    assert balances(make_client, w) == {"1200": 5000.0, "2010": -5000.0}  # 100 at 50
    posted = Fresh(make_client, "ACCOUNTS").get(f"{F}/journals?source_type=grn_line").json()
    assert any(j["narration"].startswith("Goods received GRN-") for j in posted["items"])


def test_a_plant_without_a_chart_of_accounts_is_not_posted_and_not_blocked(make_client):
    w = payables_world(make_client, with_chart=False)
    journals = Fresh(make_client, "ACCOUNTS").get(
        f"{F}/journals?unit_id={w['unit']['unit_id']}").json()
    assert journals["total"] == 0  # nothing posted, yet the receipt went through
    stock = Fresh(make_client, "STORES").get(
        f"{INV}/stock?store_id={w['store']['store_id']}").json()["items"]
    assert [float(r["on_hand"]) for r in stock] == [100.0]


# ------------------------------------------------------------------ bills and the 3-way match
def test_a_clean_bill_matches_books_and_clears_goods_received_not_billed(make_client):
    w = payables_world(make_client)
    made = enter(make_client, w)
    assert made.status_code == 201, made.text
    b = made.json()
    assert b["status"] == "MATCHED" and float(b["taxable_amount"]) == 5000
    assert float(b["tax_amount"]) == 900 and float(b["gross_amount"]) == 5900
    assert b["due_date"] == TODAY.isoformat() and b["lines"][0]["match_status"] == "NONE"
    booked = Fresh(make_client, "ACCOUNTS").post(f"{F}/bills/{b['bill_id']}/book")
    assert booked.status_code == 200 and booked.json()["status"] == "BOOKED"
    assert booked.json()["journal_id"] and float(booked.json()["outstanding"]) == 5900
    assert live(balances(make_client, w)) == {"1200": 5000.0, "1300": 900.0, "2000": -5900.0}
    assert Fresh(make_client, "ACCOUNTS").post(f"{F}/bills/{b['bill_id']}/book").status_code == 409


def test_quantity_and_rate_differences_are_exceptions(make_client):
    w = payables_world(make_client)
    acct = Fresh(make_client, "ACCOUNTS")

    def exception_bill(invoice, **kw):
        b = enter(make_client, w, invoice=invoice, **kw).json()
        assert acct.post(f"{F}/bills/{b['bill_id']}/book").status_code == 409  # not until approved
        acct.post(f"{F}/bills/{b['bill_id']}/cancel")  # keeps the receipt free for the next case
        return b

    qty = exception_bill("INV-QTY", qty="120")
    assert qty["status"] == "EXCEPTION" and qty["lines"][0]["match_status"] == "QTY"
    assert float(qty["lines"][0]["qty_variance"]) == 20 and "qty over by 20" in qty[
        "exception_note"]
    rate = exception_bill("INV-RATE", qty="50", rate="55")
    assert rate["lines"][0]["match_status"] == "RATE"
    assert float(rate["lines"][0]["rate_variance"]) == 5
    both = exception_bill("INV-BOTH", qty="130", rate="52")
    assert both["lines"][0]["match_status"] == "QTY_RATE"


def test_the_price_tolerance_rule_allows_small_rate_differences(make_client):
    w = payables_world(make_client)
    rule = "/api/v1/admin/settings/priceTolPct"
    admin = Fresh(make_client, "ADMIN")
    assert admin.put(rule, json={"value": 10}).status_code == 200
    try:
        within = enter(make_client, w, qty="50", rate="54", invoice="INV-T1").json()  # +8%
        beyond = enter(make_client, w, qty="50", rate="56", invoice="INV-T2").json()  # +12%
    finally:
        admin.put(rule, json={"value": 0})
    assert within["status"] == "MATCHED" and beyond["status"] == "EXCEPTION"


def test_a_receipt_cannot_be_billed_twice_over(make_client):
    w = payables_world(make_client)
    acct = Fresh(make_client, "ACCOUNTS")
    first = enter(make_client, w, qty="60", invoice="A-1").json()
    assert first["status"] == "MATCHED"
    second = enter(make_client, w, qty="60", invoice="A-2", day=TODAY - timedelta(days=20)).json()
    assert second["status"] == "EXCEPTION" and float(second["lines"][0]["qty_variance"]) == 20
    acct.post(f"{F}/bills/{second['bill_id']}/cancel")  # frees what it held
    third = enter(make_client, w, qty="40", invoice="A-3", day=TODAY - timedelta(days=40)).json()
    assert third["status"] == "MATCHED"  # 60 + 40 = all 100 received
    fourth = enter(make_client, w, qty="10", invoice="A-4", day=TODAY - timedelta(days=60)).json()
    assert fourth["status"] == "EXCEPTION" and float(fourth["lines"][0]["qty_variance"]) == 10


def test_duplicate_invoice_numbers_are_refused_and_look_alikes_are_flagged(make_client):
    w = payables_world(make_client)
    enter(make_client, w, qty="50", invoice="DUP-1")
    same = enter(make_client, w, qty="50", invoice="DUP-1")
    assert same.status_code == 409 and "already entered" in same.json()["detail"]
    twin = enter(make_client, w, qty="50", invoice="DUP-1B", day=TODAY - timedelta(days=3)).json()
    assert twin["status"] == "EXCEPTION" and "possible duplicate of invoice DUP-1" in twin[
        "exception_note"]


def test_a_line_that_matches_nothing_needs_an_expense_account_and_approval(make_client):
    w = payables_world(make_client)
    freight = {"description": "Freight inward", "quantity": "1", "unit_rate": "800"}
    missing = enter(make_client, w, invoice="F-0", lines=[freight])
    assert missing.status_code == 422  # no expense account
    made = enter(make_client, w, invoice="F-1", lines=[
        {**freight, "expense_account_id": w["acc"]["5500"]}])
    b = made.json()
    assert b["status"] == "EXCEPTION" and b["lines"][0]["match_status"] == "UNMATCHED"
    # ADMIN raises it so that the Accounts step is not skipped, then Accounts approves
    sent = Fresh(make_client, "ADMIN").post(f"{F}/bills/{b['bill_id']}/submit")
    assert sent.status_code == 200 and sent.json()["status"] == "PENDING_APPROVAL"
    approval = sent.json()["approval_id"]
    assert Fresh(make_client, "ACCOUNTS").post(f"{F}/bills/{b['bill_id']}/book"
                                               ).status_code == 409  # still pending
    decided = Fresh(make_client, "ACCOUNTS").post(f"/api/v1/approvals/{approval}/decide",
                                                  json={"decision": "APPROVE", "note": None})
    assert decided.status_code == 200
    assert Fresh(make_client, "ACCOUNTS").get(f"{F}/bills/{b['bill_id']}").json()["status"] == (
        "APPROVED")
    assert Fresh(make_client, "ACCOUNTS").post(f"{F}/bills/{b['bill_id']}/book"
                                               ).status_code == 200
    assert balances(make_client, w)["5500"] == 800.0


def test_a_rejected_exception_bill_cannot_be_booked(make_client):
    w = payables_world(make_client)
    b = enter(make_client, w, qty="150", invoice="R-1").json()
    approval = Fresh(make_client, "ADMIN").post(f"{F}/bills/{b['bill_id']}/submit").json()[
        "approval_id"]
    Fresh(make_client, "ACCOUNTS").post(f"/api/v1/approvals/{approval}/decide", json={
        "decision": "REJECT", "note": "Vendor must re-issue"})
    got = Fresh(make_client, "ACCOUNTS").get(f"{F}/bills/{b['bill_id']}").json()
    assert got["status"] == "REJECTED"
    assert Fresh(make_client, "ACCOUNTS").post(f"{F}/bills/{b['bill_id']}/book").status_code == 409


def test_bill_validation_permissions_and_scope(make_client):
    w = payables_world(make_client)
    assert enter(make_client, w, day=TODAY + timedelta(days=2)).status_code == 422
    assert enter(make_client, w, qty="0").status_code == 422
    other_vendor = make_vendors(make_client, 1)[0]
    wrong = enter(make_client, w, vendor=other_vendor)
    assert wrong.status_code == 422  # the receipt belongs to another vendor
    for role in ("STORES", "SALES", "QA", "PURCHASE"):
        assert enter(make_client, w, role=role).status_code == 403, role
    b = enter(make_client, w, invoice="S-1").json()
    other = Fresh(make_client, "ADMIN").post("/api/v1/org/units",
                                              json={"code": code(), "name": "P2"}).json()
    away = Fresh(make_client, "ACCOUNTS", subject="u-bill-out", unit_ids=[other["unit_id"]])
    assert away.get(f"{F}/bills/{b['bill_id']}").status_code == 404
    assert away.post(f"{F}/bills/{b['bill_id']}/book").status_code == 404
    assert away.get(f"{F}/bills").json()["total"] == 0


def test_booking_needs_the_chart_of_accounts(make_client):
    w = payables_world(make_client, with_chart=False)
    b = enter(make_client, w).json()
    refused = Fresh(make_client, "ACCOUNTS").post(f"{F}/bills/{b['bill_id']}/book")
    assert refused.status_code == 409 and "chart of accounts" in refused.json()["detail"]


def test_msme_vendors_fall_due_within_the_msme_limit(make_client, api_database):
    w = payables_world(make_client)
    normal = enter(make_client, w, qty="30", invoice="M-0").json()
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("update erp.vendor set msme_flag = true where vendor_id = %s",
                     (w["vendor"]["vendor_id"],))
    msme = enter(make_client, w, qty="30", invoice="M-1", day=TODAY).json()
    assert normal["due_date"] == TODAY.isoformat()
    assert msme["due_date"] == (TODAY + timedelta(days=45)).isoformat() and msme["msme_flag"]


# ------------------------------------------------------------------ payments
def booked_bill(make_client, w, invoice, qty="100", day=None):
    b = enter(make_client, w, qty=qty, invoice=invoice, day=day).json()
    assert Fresh(make_client, "ACCOUNTS").post(f"{F}/bills/{b['bill_id']}/book").status_code == 200
    return b


def test_a_payment_run_proposes_msme_first_pays_and_settles_the_bills(make_client, api_database):
    w = payables_world(make_client)
    vendor_two = make_vendors(make_client, 1)[0]
    purchase = Fresh(make_client, "PURCHASE")
    po2 = purchase.post(PO, json={
        "unit_id": w["unit"]["unit_id"], "vendor_id": vendor_two["vendor_id"],
        "lines": [{"item_id": w["item"]["item_id"], "ordered_qty": "50", "unit_price": "40",
                   "tax_code_id": w["po_line"]["tax_code_id"], "due_date": SOON}]}).json()
    po2 = purchase.post(f"{PO}/{po2['po_id']}/submit").json()
    grn2 = Fresh(make_client, "STORES").post(f"{INV}/grns", json={
        "po_id": po2["po_id"], "supplier_dc_no": "DC-V2", "lines": [{
            "po_line_id": po2["lines"][0]["po_line_id"], "store_id": w["store"]["store_id"],
            "received_qty": "50"}]}).json()["lines"][0]
    inspect(Fresh(make_client, "QA"), grn2, "50")
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("update erp.vendor set msme_flag = true where vendor_id = %s",
                     (vendor_two["vendor_id"],))
    plain = booked_bill(make_client, w, "P-1", "100")  # vendor one, due today
    msme = Fresh(make_client, "ACCOUNTS").post(f"{F}/bills", json={
        "unit_id": w["unit"]["unit_id"], "vendor_id": vendor_two["vendor_id"],
        "supplier_invoice_no": "V2-1", "invoice_date": TODAY.isoformat(), "lines": [{
            "po_line_id": po2["lines"][0]["po_line_id"], "grn_line_id": grn2["grn_line_id"],
            "quantity": "50", "unit_rate": "40", "tax_code_id": w["po_line"]["tax_code_id"]}]}
                                                  ).json()
    Fresh(make_client, "ACCOUNTS").post(f"{F}/bills/{msme['bill_id']}/book")
    acct = Fresh(make_client, "ACCOUNTS")
    far = (TODAY + timedelta(days=60)).isoformat()
    plan = acct.get(f"{F}/payment-proposals?unit_id={w['unit']['unit_id']}&as_of={far}").json()
    assert [p["vendor_code"] for p in plan] == [vendor_two["code"], w["vendor"]["code"]]  # MSME first
    assert plan[0]["msme_flag"] and float(plan[0]["total"]) == 2360 and float(plan[1]["total"]) == 5900
    today_only = acct.get(f"{F}/payment-proposals?unit_id={w['unit']['unit_id']}").json()
    assert [p["vendor_code"] for p in today_only] == [w["vendor"]["code"]]  # MSME bill not due yet
    run = acct.post(f"{F}/payment-runs", json={"unit_id": w["unit"]["unit_id"], "as_of": far})
    assert run.status_code == 201, run.text
    payments = run.json()
    assert len(payments) == 2 and {p["status"] for p in payments} == {"PROPOSED"}
    assert len({p["run_id"] for p in payments}) == 1
    again = acct.post(f"{F}/payment-runs", json={"unit_id": w["unit"]["unit_id"], "as_of": far})
    assert again.status_code == 409  # everything is already proposed
    run_id = payments[0]["run_id"]
    csv_text = acct.get(f"{F}/payment-runs/{run_id}/bank-file")
    assert csv_text.status_code == 200 and csv_text.text.count("MISSING_BANK_DETAILS") == 2
    for vendor in (vendor_two, w["vendor"]):
        assert acct.put(f"{F}/vendors/{vendor['vendor_id']}/bank-account", json={
            "account_holder": "Vendor Co", "account_no": "123456789012",
            "ifsc": "HDFC0001234", "bank_name": "HDFC"}).json()["account_no_masked"] == (
            "********9012")
    filled = acct.get(f"{F}/payment-runs/{run_id}/bank-file").text
    assert "MISSING_BANK_DETAILS" not in filled and "HDFC0001234" in filled
    assert "2360.00" in filled and "5900.00" in filled
    for p in payments:
        paid = acct.post(f"{F}/payments/{p['payment_id']}/pay", json={"bank_reference": "UTR1"})
        assert paid.status_code == 200 and paid.json()["status"] == "PAID", paid.text
        assert acct.post(f"{F}/payments/{p['payment_id']}/pay", json={}).status_code == 409
        assert acct.post(f"{F}/payments/{p['payment_id']}/cancel").status_code == 409
    for b in (plain, msme):
        assert acct.get(f"{F}/bills/{b['bill_id']}").json()["status"] == "PAID"
    books = balances(make_client, w)
    assert books["2000"] == 0.0 and books["1010"] == -8260.0  # payable cleared, bank paid out


def test_partial_payments_over_allocation_and_cancelled_proposals(make_client):
    w = payables_world(make_client)
    b = booked_bill(make_client, w, "PP-1")
    acct = Fresh(make_client, "ACCOUNTS")
    first = acct.post(f"{F}/payments", json={"unit_id": w["unit"]["unit_id"],
                                             "vendor_id": w["vendor"]["vendor_id"],
                                             "allocations": [{"bill_id": b["bill_id"],
                                                              "amount": "2000"}]})
    assert first.status_code == 201, first.text
    over = acct.post(f"{F}/payments", json={"unit_id": w["unit"]["unit_id"],
                                            "vendor_id": w["vendor"]["vendor_id"],
                                            "allocations": [{"bill_id": b["bill_id"],
                                                             "amount": "4000"}]})
    assert over.status_code == 409 and "only 3900" in over.json()["detail"]  # 2000 is proposed
    acct.post(f"{F}/payments/{first.json()['payment_id']}/pay", json={})
    part = acct.get(f"{F}/bills/{b['bill_id']}").json()
    assert part["status"] == "PARTIALLY_PAID" and float(part["outstanding"]) == 3900
    assert acct.post(f"{F}/bills/{b['bill_id']}/cancel").status_code == 409  # part paid
    second = acct.post(f"{F}/payments", json={"unit_id": w["unit"]["unit_id"],
                                              "vendor_id": w["vendor"]["vendor_id"],
                                              "allocations": [{"bill_id": b["bill_id"],
                                                               "amount": "3900"}]}).json()
    acct.post(f"{F}/payments/{second['payment_id']}/cancel")
    third = acct.post(f"{F}/payments", json={"unit_id": w["unit"]["unit_id"],
                                             "vendor_id": w["vendor"]["vendor_id"],
                                             "allocations": [{"bill_id": b["bill_id"],
                                                              "amount": "3900"}]})
    assert third.status_code == 201  # the cancelled proposal released its bill
    assert acct.post(f"{F}/payments", json={"unit_id": w["unit"]["unit_id"], "vendor_id":
                     w["vendor"]["vendor_id"], "allocations": [{"bill_id": str(uuid4()),
                                                                "amount": "1"}]}).status_code == 422


def test_payment_permissions_and_a_cancelled_bill_reverses_in_the_books(make_client):
    w = payables_world(make_client)
    b = booked_bill(make_client, w, "C-1", "100")
    for role in ("PURCHASE", "STORES", "SALES", "QA"):
        assert Fresh(make_client, role).post(f"{F}/payment-runs", json={
            "unit_id": w["unit"]["unit_id"]}).status_code == 403, role
    assert Fresh(make_client, "ACCOUNTS").post(f"{F}/bills/{b['bill_id']}/cancel").status_code == 200
    got = Fresh(make_client, "ACCOUNTS").get(f"{F}/bills/{b['bill_id']}").json()
    assert got["status"] == "CANCELLED"
    assert live(balances(make_client, w)) == {"1200": 5000.0, "2010": -5000.0}  # receipt only
    assert Fresh(make_client, "ACCOUNTS").post(f"{F}/payment-runs", json={
        "unit_id": w["unit"]["unit_id"]}).status_code == 409  # nothing to pay


def test_the_payable_account_always_equals_what_is_owed_on_booked_bills(make_client):
    w = payables_world(make_client, qty="100")
    a = booked_bill(make_client, w, "REC-1", "40")
    booked_bill(make_client, w, "REC-2", "30")
    acct = Fresh(make_client, "ACCOUNTS")
    acct.post(f"{F}/payments", json={"unit_id": w["unit"]["unit_id"],
                                     "vendor_id": w["vendor"]["vendor_id"],
                                     "allocations": [{"bill_id": a["bill_id"], "amount": "500"}]})
    payment = acct.get(f"{F}/payments?unit_id={w['unit']['unit_id']}&status=proposed").json()
    acct.post(f"{F}/payments/{payment['items'][0]['payment_id']}/pay", json={})
    owed = sum(Decimal(b["outstanding"]) for b in acct.get(
        f"{F}/bills?unit_id={w['unit']['unit_id']}&limit=200").json()["items"]
        if b["status"] in ("BOOKED", "PARTIALLY_PAID"))
    books = balances(make_client, w)
    assert -Decimal(str(books["2000"])) == owed  # the ledger and the bills agree
    assert books["2010"] == -1500.0  # 30 of 100 received is still not billed (30 x 50)
    tb = acct.get(f"{F}/trial-balance?unit_id={w['unit']['unit_id']}").json()
    assert tb["balanced"]
