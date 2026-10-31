"""GST return data, the GSTR-2B check and TDS on supplier bills."""

from uuid import uuid4

import psycopg
import pytest

from .test_compound_batches import Fresh
from .test_dispatch_invoice import create, fg_lot, sales_world
from .test_finance_core import TODAY, F
from .test_payables import balances, enter, payables_world
from .test_sales_orders import S

pytestmark = pytest.mark.usefixtures("api_database")
G = "/api/v1/gst"
TDS = f"{F}/tds"


def new_gstin():
    return "29" + uuid4().hex[:10].upper() + "1Z5"  # unique per vendor within the tenant


def sql(api_database, query, *params):
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute(query, params)


def set_state(api_database, unit_id, state="29"):
    sql(api_database, "update erp.unit set state_code = %s where unit_id = %s", state, unit_id)


def invoices_world(make_client, api_database, pieces=("20", "10")):
    w = sales_world(make_client, api_database)
    Fresh(make_client, "ACCOUNTS").post(f"{F}/accounts/seed-default",
                                        json={"unit_id": w["unit"]["unit_id"]})
    fg_lot(api_database, w, w["store"], "100")
    w["invoices"] = []
    sales = Fresh(make_client, "SALES")
    for qty in pieces:
        d = create(make_client, w, qty).json()
        sales.post(f"{S}/dispatches/{d['dispatch_id']}/dispatch")
        w["invoices"].append(sales.post(f"{S}/dispatches/{d['dispatch_id']}/invoice").json())
    return w


def prepare(make_client, unit, kind, role="ACCOUNTS"):
    return Fresh(make_client, role).post(f"{G}/returns", json={
        "unit_id": unit, "month": TODAY.isoformat(), "return_type": kind})


def test_gstr1_splits_tax_by_state_lists_cancelled_and_is_rebuilt_until_filed(
        make_client, api_database):
    w = invoices_world(make_client, api_database)
    unit, customer = w["unit"]["unit_id"], w["customer"]["customer_id"]
    assert prepare(make_client, unit, "GSTR1").status_code == 409  # the plant has no state yet
    set_state(api_database, unit)
    sql(api_database, "update erp.customer set gstin = %s where customer_id = %s",
        "29BBBBB1111B1Z5", customer)
    same = prepare(make_client, unit, "GSTR1").json()["payload"]  # 6,000 taxable, 1,080 tax
    assert len(same["b2b"]) == 2 and same["b2c"]["count"] == 0
    assert same["totals"]["taxable_amount"] == "6000.00"
    assert (same["totals"]["cgst"], same["totals"]["sgst"], same["totals"]["igst"]) == (
        "540.00", "540.00", "0.00")
    sql(api_database, "update erp.customer set gstin = %s where customer_id = %s",
        "27BBBBB1111B1Z5", customer)
    other = prepare(make_client, unit, "GSTR1").json()  # rebuilt: another state means IGST
    assert other["status"] == "PREPARED" and other["payload"]["totals"]["igst"] == "1080.00"
    sql(api_database, "update erp.customer set gstin = null where customer_id = %s", customer)
    sales = Fresh(make_client, "SALES")
    assert sales.post(f"{S}/invoices/{w['invoices'][1]['invoice_id']}/cancel").status_code == 200
    plain = prepare(make_client, unit, "GSTR1").json()
    assert plain["payload"]["b2c"]["count"] == 1 and plain["payload"]["b2b"] == []
    assert plain["payload"]["cancelled_invoices"] == [w["invoices"][1]["invoice_no"]]
    acct = Fresh(make_client, "ACCOUNTS")
    filed = acct.post(f"{G}/returns/{plain['return_data_id']}/file", json={"reference": "ARN-123"})
    assert filed.status_code == 200 and filed.json()["status"] == "FILED"
    assert prepare(make_client, unit, "GSTR1").status_code == 409  # kept as filed
    assert acct.post(f"{G}/returns/{plain['return_data_id']}/file",
                     json={"reference": "ARN-124"}).status_code == 409
    listed = acct.get(f"{G}/returns?unit_id={unit}").json()
    assert [r["return_type"] for r in listed] == ["GSTR1"]
    assert acct.get(f"{G}/returns/{plain['return_data_id']}").json()["filed_reference"] == "ARN-123"


def test_gstr3b_sets_output_tax_against_input_credit(make_client, api_database):
    w = payables_world(make_client)
    unit, vendor = w["unit"]["unit_id"], w["vendor"]["vendor_id"]
    set_state(api_database, unit)
    supplier = new_gstin()
    sql(api_database, "update erp.vendor set gstin = %s where vendor_id = %s", supplier, vendor)
    bill = enter(make_client, w, qty="100", invoice="S-3B").json()  # 5,000 + 900 GST
    assert Fresh(make_client, "ACCOUNTS").post(f"{F}/bills/{bill['bill_id']}/book").status_code == 200
    got = prepare(make_client, unit, "GSTR3B").json()["payload"]
    assert got["outward"]["invoices"] == 0 and got["input_credit"]["bills"] == 1
    assert got["input_credit"]["cgst"] == got["input_credit"]["sgst"] == "450.00"
    assert got["net_payable"]["cgst"] == "-450.00" and got["total_payable"] == "0"
    refused = prepare(make_client, unit, "GSTR2B")
    assert refused.status_code == 422  # prepared by reconciling, not from the books


def test_the_gstr2b_check_finds_matches_differences_and_missing_either_side(
        make_client, api_database):
    w = payables_world(make_client)
    unit, vendor = w["unit"]["unit_id"], w["vendor"]["vendor_id"]
    set_state(api_database, unit)
    supplier = new_gstin()
    sql(api_database, "update erp.vendor set gstin = %s where vendor_id = %s", supplier, vendor)
    acct = Fresh(make_client, "ACCOUNTS")
    for invoice, qty in (("S-2B-1", "60"), ("S-2B-2", "40")):  # 3,540 and 2,360
        bill = enter(make_client, w, qty=qty, invoice=invoice).json()
        assert acct.post(f"{F}/bills/{bill['bill_id']}/book").status_code == 200

    def check(lines):
        return acct.post(f"{G}/gstr2b/reconcile", json={
            "unit_id": unit, "month": TODAY.isoformat(), "lines": lines})

    exact = {"supplier_gstin": supplier, "invoice_no": "s-2b-1",  # case does not matter
             "taxable_amount": "3000.00", "tax_amount": "540.00"}
    stranger = {"supplier_gstin": "27CCCCC2222C1Z5", "invoice_no": "X-9",
                "taxable_amount": "100.00", "tax_amount": "18.00"}
    got = check([exact, stranger]).json()["payload"]
    assert got["summary"]["matched"] == 1 and got["summary"]["missing_in_books"] == 1
    assert got["summary"]["missing_in_2b"] == 1 and got["summary"]["credit_at_risk"] == "360.00"
    off = check([{**exact, "tax_amount": "490.00"}]).json()["payload"]  # rebuilt
    assert off["summary"]["differing"] == 1 and off["summary"]["matched"] == 0
    assert off["differing"][0]["booked_tax"] == "540.00"
    assert acct.get(f"{G}/returns?unit_id={unit}&return_type=gstr2b").json()[0][
        "return_type"] == "GSTR2B"
    assert balances(make_client, w).get("1300") == 900  # the check changes no books


def booked_bill(make_client, w, invoice="TDS-1", qty="100"):
    bill = enter(make_client, w, qty=qty, invoice=invoice).json()
    assert Fresh(make_client, "ACCOUNTS").post(f"{F}/bills/{bill['bill_id']}/book").status_code == 200
    return bill


def test_tds_is_booked_settles_part_of_the_bill_and_is_deposited(make_client, api_database):
    w = payables_world(make_client)
    unit = w["unit"]["unit_id"]
    bill = booked_bill(make_client, w)  # 5,000 + 900 = 5,900 payable
    acct = Fresh(make_client, "ACCOUNTS")
    assert acct.post(TDS, json={"bill_id": bill["bill_id"], "section_code": "194X"}
                     ).status_code == 422  # no standard rate for this section
    done = acct.post(TDS, json={"bill_id": bill["bill_id"], "section_code": "194c"})
    assert done.status_code == 201, done.text
    entry = done.json()
    assert entry["section_code"] == "194C" and entry["deducted_amount"] == "100.00"
    assert entry["status"] == "OPEN"
    now = acct.get(f"{F}/bills/{bill['bill_id']}").json()
    assert now["status"] == "PARTIALLY_PAID" and float(now["outstanding"]) == 5800
    books = balances(make_client, w)
    assert books["2000"] == -5800 and books["2200"] == -100
    assert acct.post(TDS, json={"bill_id": bill["bill_id"], "section_code": "194J"}
                     ).status_code == 409  # one deduction per bill
    assert acct.post(f"{F}/bills/{bill['bill_id']}/cancel").status_code == 409
    undone = acct.post(f"{TDS}/{entry['tds_entry_id']}/cancel", json={"reason": "Wrong section"})
    assert undone.status_code == 200 and undone.json()["status"] == "CANCELLED"
    assert acct.get(f"{F}/bills/{bill['bill_id']}").json()["status"] == "BOOKED"
    assert balances(make_client, w)["2000"] == -5900 and not balances(make_client, w).get("2200")
    again = acct.post(TDS, json={"bill_id": bill["bill_id"], "section_code": "194J", "rate": "5",
                                 "taxable_base": "4000"}).json()  # rate and base overridden
    assert again["deducted_amount"] == "200.00" and again["rate"] == "5.0000"
    summary = acct.get(f"{TDS}/summary?unit_id={unit}&month={TODAY.isoformat()}").json()
    assert [(s["section_code"], s["deducted_amount"], s["deposited_amount"]) for s in summary] == [
        ("194J", "200.00", "0.00")]
    paid = acct.post(f"{TDS}/deposit", json={
        "unit_id": unit, "entry_ids": [again["tds_entry_id"]], "challan_no": "CH-0001"})
    assert paid.status_code == 200 and paid.json()[0]["status"] == "DEPOSITED"
    assert balances(make_client, w)["1010"] == -200 and not balances(make_client, w).get("2200")
    assert acct.post(f"{TDS}/deposit", json={
        "unit_id": unit, "entry_ids": [again["tds_entry_id"]], "challan_no": "CH-0002"}
    ).status_code == 409
    assert acct.post(f"{TDS}/{again['tds_entry_id']}/cancel", json={"reason": "Too late"}
                     ).status_code == 409
    prepared = acct.post(f"{TDS}/summary/prepare?unit_id={unit}&month={TODAY.isoformat()}")
    assert prepared.json()[0]["deposited_amount"] == "200.00"
    assert acct.get(f"{G}/returns?unit_id={unit}&return_type=TDS").json()[0]["status"] == "PREPARED"
    assert acct.get(f"{TDS}?status=deposited").json()[0]["challan_no"] == "CH-0001"


def test_tds_validation_and_permissions_hold(make_client, api_database):
    w = payables_world(make_client)
    unit = w["unit"]["unit_id"]
    bill = booked_bill(make_client, w, invoice="TDS-2")
    acct = Fresh(make_client, "ACCOUNTS")
    assert acct.post(TDS, json={"bill_id": bill["bill_id"], "section_code": "194C",
                                "rate": "100", "taxable_base": "6000"}).status_code == 422
    assert acct.post(TDS, json={"bill_id": "00000000-0000-4000-8000-000000000000",
                                "section_code": "194C"}).status_code == 404
    for role in ("STORES", "QA", "OPERATOR"):
        assert Fresh(make_client, role).post(TDS, json={
            "bill_id": bill["bill_id"], "section_code": "194C"}).status_code == 403
        assert Fresh(make_client, role).get(f"{G}/returns?unit_id={unit}").status_code == 403
    assert Fresh(make_client, "SALES").post(f"{G}/returns", json={
        "unit_id": unit, "month": TODAY.isoformat(), "return_type": "GSTR1"}).status_code == 403
