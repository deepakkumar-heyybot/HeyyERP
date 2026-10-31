"""Customer receipts, invoice posting, ageing, collections and the overdue credit check."""

from datetime import timedelta

import psycopg
import pytest

from .test_compound_batches import Fresh
from .test_dispatch_invoice import create, fg_lot, sales_world
from .test_finance_core import TODAY, F
from .test_sales_orders import S

pytestmark = pytest.mark.usefixtures("api_database")


def receivables_world(make_client, api_database, ordered="100"):
    w = sales_world(make_client, api_database, ordered=ordered)
    seeded = Fresh(make_client, "ACCOUNTS").post(f"{F}/accounts/seed-default",
                                                 json={"unit_id": w["unit"]["unit_id"]})
    assert seeded.status_code == 200, seeded.text
    fg_lot(api_database, w, w["store"], "100")
    return w


def invoice(make_client, w, qty):
    sales = Fresh(make_client, "SALES")
    d = create(make_client, w, qty).json()
    assert sales.post(f"{S}/dispatches/{d['dispatch_id']}/dispatch").status_code == 200
    made = sales.post(f"{S}/dispatches/{d['dispatch_id']}/invoice")
    assert made.status_code == 201, made.text
    return made.json()  # 50 pieces -> 10,000 + 1,800 GST = 11,800


def receipt(make_client, w, amount, allocations=(), ref="UTR-1", **extra):
    return Fresh(make_client, "ACCOUNTS").post(f"{F}/receipts", json={
        "unit_id": w["unit"]["unit_id"], "customer_id": w["customer"]["customer_id"],
        "amount": amount, "reference": ref,
        "allocations": [{"invoice_id": i, "amount": a} for i, a in allocations], **extra})


def balance(make_client, w, code):
    tb = Fresh(make_client, "ACCOUNTS").get(
        f"{F}/trial-balance?unit_id={w['unit']['unit_id']}").json()
    return float(next((r["closing"] for r in tb["rows"] if r["code"] == code), 0))


def get_invoice(make_client, inv):
    return Fresh(make_client, "SALES").get(f"{S}/invoices/{inv['invoice_id']}").json()


def age(api_database, inv, days):
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("update erp.customer_invoice set due_date = %s where invoice_id = %s",
                     (TODAY - timedelta(days=days), inv["invoice_id"]))


def test_an_invoice_books_receivable_sales_and_gst_and_cancelling_reverses_it(
        make_client, api_database):
    w = receivables_world(make_client, api_database)
    inv = invoice(make_client, w, "50")
    assert inv["journal_id"] and inv["payment_status"] == "UNPAID"
    assert float(inv["outstanding"]) == 11800
    assert balance(make_client, w, "1100") == 11800 and balance(make_client, w, "4000") == -10000
    assert balance(make_client, w, "2100") == -1800
    gone = Fresh(make_client, "SALES").post(f"{S}/invoices/{inv['invoice_id']}/cancel")
    assert gone.status_code == 200, gone.text
    assert balance(make_client, w, "1100") == 0 and balance(make_client, w, "4000") == 0


def test_a_receipt_is_applied_part_by_part_and_an_invoice_is_paid_off(make_client, api_database):
    w = receivables_world(make_client, api_database)
    inv = invoice(make_client, w, "50")
    got = receipt(make_client, w, "5000", [(inv["invoice_id"], "3000")])
    assert got.status_code == 201, got.text
    r = got.json()
    assert r["receipt_no"].startswith("RCT-") and r["status"] == "RECEIVED"
    assert float(r["applied"]) == 3000 and float(r["on_account"]) == 2000
    assert balance(make_client, w, "1010") == 5000 and balance(make_client, w, "1100") == 6800
    now = get_invoice(make_client, inv)
    assert now["payment_status"] == "PART_PAID" and float(now["outstanding"]) == 8800
    more = Fresh(make_client, "ACCOUNTS").post(f"{F}/receipts/{r['payment_id']}/apply", json={
        "allocations": [{"invoice_id": inv["invoice_id"], "amount": "2000"}]})
    assert more.status_code == 200 and float(more.json()["on_account"]) == 0
    over = Fresh(make_client, "ACCOUNTS").post(f"{F}/receipts/{r['payment_id']}/apply", json={
        "allocations": [{"invoice_id": inv["invoice_id"], "amount": "1"}]})
    assert over.status_code == 409  # nothing left on this receipt
    second = receipt(make_client, w, "9000", [(inv["invoice_id"], "6800")], ref="UTR-2")
    assert second.status_code == 201
    assert get_invoice(make_client, inv)["payment_status"] == "PAID"
    assert balance(make_client, w, "1100") == -2200  # 11,800 billed, 14,000 received
    listed = Fresh(make_client, "ACCOUNTS").get(f"{F}/receipts?unapplied=true").json()
    assert [x["receipt_no"] for x in listed["items"]] == [second.json()["receipt_no"]]


def test_a_receipt_cannot_be_over_applied_or_applied_to_the_wrong_invoice(
        make_client, api_database):
    w = receivables_world(make_client, api_database)
    inv = invoice(make_client, w, "50")
    id_ = inv["invoice_id"]
    assert receipt(make_client, w, "1000", [(id_, "1500")]).status_code == 409  # more than receipt
    assert receipt(make_client, w, "20000", [(id_, "12000")]).status_code == 409  # more than owed
    assert receipt(make_client, w, "1000", [(id_, "500"), (id_, "500")]).status_code == 422
    assert receipt(make_client, w, "1000", [(
        "00000000-0000-4000-8000-000000000000", "500")]).status_code == 422
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    assert receipt(make_client, w, "1000", receipt_date=tomorrow).status_code == 422
    assert receipt(make_client, w, "0").status_code == 422
    assert float(get_invoice(make_client, inv)["paid_amount"]) == 0


def test_reversing_a_receipt_restores_the_invoice_and_the_books(make_client, api_database):
    w = receivables_world(make_client, api_database)
    inv = invoice(make_client, w, "50")
    r = receipt(make_client, w, "11800", [(inv["invoice_id"], "11800")]).json()
    assert get_invoice(make_client, inv)["payment_status"] == "PAID"
    acct = Fresh(make_client, "ACCOUNTS")
    gone = acct.post(f"{F}/receipts/{r['payment_id']}/reverse", json={"reason": "Cheque bounced"})
    assert gone.status_code == 200 and gone.json()["status"] == "CANCELLED"
    assert gone.json()["allocations"] == []
    assert get_invoice(make_client, inv)["payment_status"] == "UNPAID"
    assert balance(make_client, w, "1010") == 0 and balance(make_client, w, "1100") == 11800
    assert acct.post(f"{F}/receipts/{r['payment_id']}/reverse", json={"reason": "Again"}
                     ).status_code == 409
    assert acct.post(f"{F}/receipts/{r['payment_id']}/apply", json={"allocations": [
        {"invoice_id": inv["invoice_id"], "amount": "1"}]}).status_code == 409


def test_an_invoice_with_receipts_cannot_be_cancelled_until_they_are_reversed(
        make_client, api_database):
    w = receivables_world(make_client, api_database)
    inv = invoice(make_client, w, "50")
    r = receipt(make_client, w, "1000", [(inv["invoice_id"], "1000")]).json()
    sales = Fresh(make_client, "SALES")
    blocked = sales.post(f"{S}/invoices/{inv['invoice_id']}/cancel")
    assert blocked.status_code == 409 and "Receipts" in blocked.json()["detail"]
    Fresh(make_client, "ACCOUNTS").post(f"{F}/receipts/{r['payment_id']}/reverse",
                                        json={"reason": "Wrong customer"})
    assert sales.post(f"{S}/invoices/{inv['invoice_id']}/cancel").status_code == 200


def test_ageing_groups_what_is_owed_by_lateness_and_nets_receipts_on_account(
        make_client, api_database):
    w = receivables_world(make_client, api_database)
    first = invoice(make_client, w, "20")  # 4,720
    second = invoice(make_client, w, "30")  # 7,080
    age(api_database, first, 45)
    age(api_database, second, 0)
    receipt(make_client, w, "1000")  # on account, applied to nothing
    acct = Fresh(make_client, "ACCOUNTS")
    got = acct.get(f"{F}/receivables/ageing?unit_id={w['unit']['unit_id']}").json()
    (row,) = got["rows"]
    assert float(row["d31_60"]) == 4720 and float(row["not_due"]) == 7080
    assert float(row["total"]) == 11800 and float(row["on_account"]) == 1000
    assert float(row["net"]) == 10800 and float(got["totals"]["total"]) == 11800
    far = (TODAY + timedelta(days=100)).isoformat()
    later = acct.get(f"{F}/receivables/ageing?unit_id={w['unit']['unit_id']}&as_of={far}").json()
    assert float(later["rows"][0]["over_90"]) == 11800  # 145 and 100 days late by then
    assert balance(make_client, w, "1100") == 11800 - 1000  # the ledger agrees with the net


def test_collections_lists_only_overdue_invoices_worst_first(make_client, api_database):
    w = receivables_world(make_client, api_database)
    old = invoice(make_client, w, "10")
    recent = invoice(make_client, w, "10")
    paid = invoice(make_client, w, "10")
    age(api_database, old, 70)
    age(api_database, recent, 5)
    age(api_database, paid, 90)
    receipt(make_client, w, "2360", [(paid["invoice_id"], "2360")])
    acct = Fresh(make_client, "ACCOUNTS")
    got = acct.get(f"{F}/receivables/collections?unit_id={w['unit']['unit_id']}").json()
    assert [g["invoice_id"] for g in got] == [old["invoice_id"], recent["invoice_id"]]
    assert got[0]["days_overdue"] == 70 and got[0]["last_receipt_date"] == TODAY.isoformat()


def test_the_credit_check_counts_what_is_unpaid_and_warns_of_overdue_invoices(
        make_client, api_database):
    w = receivables_world(make_client, api_database)
    inv = invoice(make_client, w, "50")
    age(api_database, inv, 10)
    sales = Fresh(make_client, "SALES")
    credit = sales.get(f"{S}/customers/{w['customer']['customer_id']}/credit").json()
    assert credit["overdue_invoices"] == 1 and float(credit["overdue_amount"]) == 11800
    receipt(make_client, w, "11800", [(inv["invoice_id"], "11800")])
    paid = sales.get(f"{S}/customers/{w['customer']['customer_id']}/credit").json()
    assert paid["overdue_invoices"] == 0 and float(paid["received"]) == 11800


def test_the_customer_statement_runs_a_balance_and_permissions_hold(make_client, api_database):
    w = receivables_world(make_client, api_database)
    inv = invoice(make_client, w, "50")
    receipt(make_client, w, "3000", [(inv["invoice_id"], "3000")])
    acct = Fresh(make_client, "ACCOUNTS")
    st = acct.get(f"{F}/customers/{w['customer']['customer_id']}/statement"
                  f"?unit_id={w['unit']['unit_id']}").json()
    assert [x["kind"] for x in st["lines"]] == ["INVOICE", "RECEIPT"]
    assert float(st["balance"]) == 8800 and float(st["lines"][0]["balance"]) == 11800
    for role in ("SALES", "STORES", "QA"):
        assert Fresh(make_client, role).post(f"{F}/receipts", json={
            "unit_id": w["unit"]["unit_id"], "customer_id": w["customer"]["customer_id"],
            "amount": "10", "reference": "X"}).status_code == 403
    assert acct.get(f"{F}/receipts/00000000-0000-4000-8000-000000000000").status_code == 404
    suppliers = Fresh(make_client, "ACCOUNTS").get(f"{F}/payments?limit=200").json()["items"]
    assert not any(p["payment_no"].startswith("RCT-") for p in suppliers)  # receipts are separate
