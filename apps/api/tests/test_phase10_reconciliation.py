"""Phase 10 as a whole: the books tie to the documents, the month lock holds, rules apply later only.

The other finance test files check each feature. These check what must stay true across them: after
every money step the trial balance balances and equals the sub-ledgers (supplier bills, customer
invoices and receipts, cost of sales), a closed month refuses every new posting, the database itself
refuses what the API never allows, the company's costing rules reject bad values and never revalue
past lots, and the new endpoints keep their permissions and tenant walls.
"""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from .test_compound_batches import Fresh
from .test_costing import Rules, cost_of, costed_shop, pack_lot, sql
from .test_finance_core import TODAY, F
from .test_gst_tds import TDS, G, booked_bill
from .test_payables import balances, payables_world
from .test_receivables import balance, invoice, receipt, receivables_world
from .test_sales_orders import S

pytestmark = pytest.mark.usefixtures("api_database")
NOBODY = "00000000-0000-4000-8000-000000000000"


# ------------------------------------------------------------------ payables tie to the ledger
def payable_tie(make_client, w, bills):
    """The trial balance balances and the payable account equals what the bills still owe."""
    acct = Fresh(make_client, "ACCOUNTS")
    tb = acct.get(f"{F}/trial-balance?unit_id={w['unit']['unit_id']}").json()
    assert tb["balanced"], "the trial balance does not balance"
    owed = sum(Decimal(acct.get(f"{F}/bills/{b['bill_id']}").json()["outstanding"]) for b in bills)
    assert Decimal(str(balances(make_client, w).get("2000", 0))) == -owed, "payable differs"


def test_every_payables_step_balances_the_books_and_ties_to_the_bills(make_client, api_database):
    w = payables_world(make_client)
    unit = w["unit"]["unit_id"]
    acct = Fresh(make_client, "ACCOUNTS")
    first = booked_bill(make_client, w, invoice="P10-1", qty="60")  # 3,000 + 540
    payable_tie(make_client, w, [first])
    second = booked_bill(make_client, w, invoice="P10-2", qty="40")  # 2,000 + 360
    bills = [first, second]
    payable_tie(make_client, w, bills)
    entry = acct.post(TDS, json={"bill_id": first["bill_id"], "section_code": "194C"}).json()
    assert entry["deducted_amount"] == "60.00"
    payable_tie(make_client, w, bills)
    paid_in = acct.post(f"{TDS}/deposit", json={
        "unit_id": unit, "entry_ids": [entry["tds_entry_id"]], "challan_no": "CH-P10"})
    assert paid_in.status_code == 200
    payable_tie(make_client, w, bills)
    far = (TODAY + timedelta(days=60)).isoformat()
    run = acct.post(f"{F}/payment-runs", json={"unit_id": unit, "as_of": far})
    assert run.status_code == 201, run.text
    for p in run.json():
        assert acct.post(f"{F}/payments/{p['payment_id']}/pay", json={}).status_code == 200
        payable_tie(make_client, w, bills)
    books = balances(make_client, w)
    assert books.get("2000", 0) == 0 and not books.get("2200")  # nothing owed, TDS paid over
    assert books["1010"] == -(3480 + 2360 + 60)  # vendors net of TDS, plus the challan
    assert all(acct.get(f"{F}/bills/{b['bill_id']}").json()["status"] == "PAID" for b in bills)


# ------------------------------------------------------------------ receivables tie to the ledger
def receivable_tie(make_client, w):
    """Ledger = ageing net = customer statement: what customers owe less money on account."""
    acct = Fresh(make_client, "ACCOUNTS")
    unit = w["unit"]["unit_id"]
    tb = acct.get(f"{F}/trial-balance?unit_id={unit}").json()
    assert tb["balanced"], "the trial balance does not balance"
    net = Decimal(acct.get(f"{F}/receivables/ageing?unit_id={unit}").json()["totals"]["net"])
    statement = acct.get(f"{F}/customers/{w['customer']['customer_id']}/statement?unit_id={unit}"
                         ).json()
    assert Decimal(statement["balance"]) == net, "the statement differs from the ageing"
    assert Decimal(str(balance(make_client, w, "1100"))) == net, "the ledger differs"


def test_every_receivables_step_keeps_ledger_ageing_and_statement_equal(
        make_client, api_database):
    w = receivables_world(make_client, api_database)
    one, two, three = (invoice(make_client, w, q) for q in ("10", "20", "30"))  # 2,360 4,720 7,080
    receivable_tie(make_client, w)
    r1 = receipt(make_client, w, "5000", [(one["invoice_id"], "2000")], ref="P10-R1").json()
    receivable_tie(make_client, w)
    r2 = receipt(make_client, w, "1000", ref="P10-R2").json()  # nothing applied
    receivable_tie(make_client, w)
    acct = Fresh(make_client, "ACCOUNTS")
    assert acct.post(f"{F}/receipts/{r1['payment_id']}/apply", json={"allocations": [
        {"invoice_id": two["invoice_id"], "amount": "3000"}]}).status_code == 200
    receivable_tie(make_client, w)
    assert acct.post(f"{F}/receipts/{r2['payment_id']}/reverse", json={"reason": "Bounced"}
                     ).status_code == 200
    receivable_tie(make_client, w)
    assert Fresh(make_client, "SALES").post(
        f"{S}/invoices/{three['invoice_id']}/cancel").status_code == 200
    receivable_tie(make_client, w)
    assert acct.post(f"{F}/receipts/{r1['payment_id']}/reverse", json={"reason": "Wrong"}
                     ).status_code == 200
    receivable_tie(make_client, w)
    assert Decimal(str(balance(make_client, w, "1100"))) == 2360 + 4720  # two live invoices
    books = Fresh(make_client, "ACCOUNTS").get(
        f"{F}/trial-balance?unit_id={w['unit']['unit_id']}").json()["rows"]
    by = {r["code"]: Decimal(r["closing"]) for r in books}
    assert by["5000"] == 40 * 60 and by["1220"] == -(40 * 60)  # cost of all three dispatches
    assert by["4000"] == -(2000 + 4000) and by["2100"] == -(360 + 720)  # sales and GST, live only


# ------------------------------------------------------------------ the month lock
def test_a_closed_month_refuses_receipts_tds_and_dispatches_and_leaves_no_trace(
        make_client, api_database):
    w = receivables_world(make_client, api_database)
    unit, month = w["unit"]["unit_id"], f"{TODAY:%Y-%m}"
    inv = invoice(make_client, w, "10")
    acct, admin = Fresh(make_client, "ACCOUNTS"), Fresh(make_client, "ADMIN")
    assert acct.post(f"{F}/periods/close", json={"unit_id": unit, "month": month}
                     ).status_code == 200
    refused = receipt(make_client, w, "1000", [(inv["invoice_id"], "1000")])
    assert refused.status_code == 409 and "closed" in refused.json()["detail"].lower()
    assert Fresh(make_client, "SALES").get(f"{S}/invoices/{inv['invoice_id']}").json()[
        "paid_amount"] == "0.00"  # nothing was half applied
    assert acct.get(f"{F}/receipts?customer_id={w['customer']['customer_id']}").json()[
        "total"] == 0
    assert admin.post(f"{F}/periods/reopen", json={"unit_id": unit, "month": month}
                      ).status_code == 200
    assert receipt(make_client, w, "1000", [(inv["invoice_id"], "1000")]).status_code == 201

    p = payables_world(make_client)
    bill = booked_bill(make_client, p, invoice="P10-LOCK")
    assert acct.post(f"{F}/periods/close", json={"unit_id": p["unit"]["unit_id"], "month": month}
                     ).status_code == 200
    stopped = acct.post(TDS, json={"bill_id": bill["bill_id"], "section_code": "194C"})
    assert stopped.status_code == 409 and "closed" in stopped.json()["detail"].lower()
    assert acct.get(f"{TDS}?unit_id={p['unit']['unit_id']}").json() == []
    admin.post(f"{F}/periods/reopen", json={"unit_id": p["unit"]["unit_id"], "month": month})
    assert acct.post(TDS, json={"bill_id": bill["bill_id"], "section_code": "194C"}
                     ).status_code == 201


# ------------------------------------------------------------------ the database as the last wall
def test_the_database_refuses_what_the_api_never_allows(make_client, api_database):
    w = receivables_world(make_client, api_database)
    inv = invoice(make_client, w, "10")
    r = receipt(make_client, w, "2360", [(inv["invoice_id"], "1000")]).json()
    p = payables_world(make_client)
    bill = booked_bill(make_client, p, invoice="P10-DB")
    Fresh(make_client, "ACCOUNTS").post(TDS, json={"bill_id": bill["bill_id"],
                                                  "section_code": "194C"})
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):  # paid more than the invoice is worth
            conn.execute("update erp.customer_invoice set paid_amount = 99999 where invoice_id = %s",
                         (inv["invoice_id"],))
        with pytest.raises(psycopg.errors.UniqueViolation):  # one allocation per receipt and invoice
            conn.execute(
                "insert into erp.payment_allocation(tenant_id, payment_id, invoice_id, "
                "allocated_amount) select tenant_id, payment_id, invoice_id, 1 from "
                "erp.payment_allocation where payment_id = %s", (r["payment_id"],))
        with pytest.raises(psycopg.errors.UniqueViolation):  # one live TDS entry per bill
            conn.execute(
                "insert into erp.tds_entry(tenant_id, unit_id, period_id, vendor_id, section_code, "
                "taxable_base, deducted_amount, bill_id) select tenant_id, unit_id, period_id, "
                "vendor_id, '194J', 1, 1, bill_id from erp.tds_entry where bill_id = %s",
                (bill["bill_id"],))
        journal = conn.execute("select journal_id from erp.payment where payment_id = %s",
                               (r["payment_id"],)).fetchone()[0]
        with pytest.raises(psycopg.Error):  # a posted receipt journal cannot be edited or removed
            conn.execute("update erp.journal_line set debit = debit + 1 where journal_id = %s",
                         (journal,))
        with pytest.raises(psycopg.Error):
            conn.execute("delete from erp.journal where journal_id = %s", (journal,))


# ------------------------------------------------------------------ the company's costing rules
def test_costing_rules_reject_bad_values_and_never_revalue_past_lots(make_client, api_database):
    w = costed_shop(make_client, api_database)
    admin = Fresh(make_client, "ADMIN")
    for key, bad in (("costMethod", "LIFO"), ("cogsTiming", "NEVER"), ("overheadPct", 501),
                     ("overheadPct", -1), ("productionPosting", "yes")):
        assert admin.put(f"/api/v1/admin/settings/{key}", json={"value": bad}).status_code == 422, key
    assert Fresh(make_client, "ACCOUNTS").put("/api/v1/admin/settings/costMethod", json={
        "value": "STANDARD"}).status_code == 403  # only an administrator decides
    first = pack_lot(make_client, w, qty="50", rejects="0")
    before = cost_of(make_client, first)
    assert before["cost_basis"] == "ACTUAL"
    fg = w["fg"]["item_id"]
    Fresh(make_client, "ACCOUNTS").put(f"{F}/costing/standard/{fg}", json={"standard_cost": "10"})
    with Rules(make_client, costMethod="STANDARD", overheadPct=25):
        later = cost_of(make_client, pack_lot(make_client, w, qty="20", rejects="0"))
        assert later["cost_basis"] == "STANDARD" and Decimal(later["unit_cost"]) == 10
    assert cost_of(make_client, first) == before  # the earlier lot keeps its cost
    layer = sql(api_database, "select unit_cost from erp.cost_layer where lot_id = (select "
                "fg_lot_id from erp.packing where prod_lot_id = %s)", first)[0]
    assert layer == Decimal(before["unit_cost"])


# ------------------------------------------------------------------ permissions and tenant wall
def test_phase_10_writes_need_their_permissions_and_reads_their_module(make_client, api_database):
    unit = str(uuid4())
    writes = [
        ("post", f"{F}/receipts", {"unit_id": unit, "customer_id": NOBODY, "amount": "1",
                                   "reference": "x"}),
        ("post", f"{F}/receipts/{NOBODY}/apply", {"allocations": [
            {"invoice_id": NOBODY, "amount": "1"}]}),
        ("post", f"{F}/receipts/{NOBODY}/reverse", {"reason": "abc"}),
        ("post", TDS, {"bill_id": NOBODY, "section_code": "194C"}),
        ("post", f"{TDS}/deposit", {"unit_id": unit, "entry_ids": [NOBODY], "challan_no": "abc"}),
        ("post", f"{TDS}/{NOBODY}/cancel", {"reason": "abc"}),
        ("post", f"{G}/returns", {"unit_id": unit, "month": TODAY.isoformat()}),
        ("post", f"{G}/gstr2b/reconcile", {"unit_id": unit, "month": TODAY.isoformat(),
                                           "lines": []}),
        ("post", f"{G}/returns/{NOBODY}/file", {"reference": "abc"}),
        ("put", f"{F}/costing/standard/{NOBODY}", {"standard_cost": "1"}),
    ]
    reads = [f"{F}/consolidation", f"{F}/receipts", f"{F}/receivables/ageing?unit_id={unit}",
             f"{F}/receivables/collections?unit_id={unit}", f"{TDS}", f"{G}/returns?unit_id={unit}",
             f"{F}/costing/lots/{NOBODY}"]
    for role in ("STORES", "QA", "OPERATOR"):
        client = Fresh(make_client, role)
        for method, url, body in writes:
            assert getattr(client, method)(url, json=body).status_code == 403, (role, url)
        for url in reads:
            assert Fresh(make_client, role).get(url).status_code == 403, (role, url)


def test_another_tenants_gst_returns_are_invisible(make_client, api_database):
    unit, period, row = uuid4(), uuid4(), uuid4()
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("insert into erp.unit(unit_id, tenant_id, code, name) values (%s, %s, %s, 'B')",
                     (unit, api_database["tenant_b"], "B" + uuid4().hex[:6]))
        conn.execute("insert into erp.fiscal_period(period_id, tenant_id, unit_id, year_month) "
                     "values (%s, %s, %s, date_trunc('month', now())::date)",
                     (period, api_database["tenant_b"], unit))
        conn.execute("insert into erp.gst_return_data(return_data_id, tenant_id, unit_id, "
                     "period_id, return_type) values (%s, %s, %s, %s, 'GSTR1')",
                     (row, api_database["tenant_b"], unit, period))
    acct = Fresh(make_client, "ACCOUNTS")
    assert acct.get(f"{G}/returns/{row}").status_code == 404
    assert acct.post(f"{G}/returns/{row}/file", json={"reference": "ARN-B"}).status_code == 404
    listed = acct.get(f"{G}/returns?unit_id={unit}")
    assert listed.status_code in (403, 404) or listed.json() == []
