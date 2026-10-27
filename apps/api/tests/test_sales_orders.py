"""Price lists, quotations, sales orders with delivery schedules, credit check and schedule import."""

import base64
import io
from datetime import date, timedelta
from uuid import uuid4

import pytest
from openpyxl import Workbook

from .test_bom import item as make_item
from .test_compound_batches import Fresh
from .test_inventory_receipt import code

pytestmark = pytest.mark.usefixtures("api_database")
S = "/api/v1/sales"
TODAY = date.today()
IN = lambda days: (TODAY + timedelta(days=days)).isoformat()  # noqa: E731


def setup_sales(make_client, credit_limit=None, parts=("CP-1",)):
    """A plant, a customer, FG items with the customer's part numbers and a GST 18% tax code."""
    admin = Fresh(make_client, "ADMIN")
    unit = admin.post("/api/v1/org/units", json={"code": code(), "name": "Plant"}).json()
    customer = Fresh(make_client, "SALES").post("/api/v1/parties/customers", json={
        "code": "C" + code()[:6], "name": "Maruti Suzuki",
        **({"credit_limit": str(credit_limit)} if credit_limit is not None else {})}).json()
    tax = admin.post("/api/v1/ref/tax-codes", json={
        "code": code(), "description": "GST 18%", "gst_rate": "18"}).json()
    items = []
    for part in parts:
        item = make_item(admin, "FG", "NOS")
        mapped = admin.post(f"/api/v1/product/items/{item['item_id']}/customer-parts", json={
            "customer_id": customer["customer_id"], "customer_part_no": part})
        assert mapped.status_code == 201, mapped.text
        items.append(item)
    return {"unit": unit, "customer": customer, "tax": tax, "items": items, "item": items[0]}


def price(make_client, w, item=None, amount="100", valid_from=None, role="SALES"):
    return Fresh(make_client, role).post(f"{S}/prices", json={
        "customer_id": w["customer"]["customer_id"], "item_id": (item or w["item"])["item_id"],
        "unit_price": amount, "valid_from": (valid_from or TODAY - timedelta(days=30)).isoformat()})


def order(make_client, w, lines=None, **extra):
    body = {"unit_id": w["unit"]["unit_id"], "customer_id": w["customer"]["customer_id"],
            "lines": lines or [{"item_id": w["item"]["item_id"], "ordered_qty": "100",
                                "unit_price": "100", "tax_code_id": w["tax"]["tax_code_id"]}],
            **extra}
    return Fresh(make_client, "SALES").post(f"{S}/orders", json=body)


# ------------------------------------------------------------------ price list
def test_prices_are_effective_by_date_and_a_new_price_closes_the_old_one(make_client):
    w = setup_sales(make_client)
    first = price(make_client, w, amount="100", valid_from=TODAY - timedelta(days=60))
    assert first.status_code == 201 and first.json()["valid_to"] is None
    second = price(make_client, w, amount="110", valid_from=TODAY - timedelta(days=10))
    assert second.status_code == 201
    sales = Fresh(make_client, "SALES")
    asof = lambda d: sales.get(f"{S}/prices/effective", params={  # noqa: E731
        "customer_id": w["customer"]["customer_id"], "item_id": w["item"]["item_id"],
        "on": (TODAY + timedelta(days=d)).isoformat()})
    assert float(asof(0).json()["unit_price"]) == 110 and float(asof(-20).json()["unit_price"]) == 100
    assert asof(-90).status_code == 404  # before any price
    history = sales.get(f"{S}/prices?item_id={w['item']['item_id']}").json()["items"]
    assert [(float(p["unit_price"]), p["valid_to"] is None) for p in history] == [
        (110.0, True), (100.0, False)]
    assert history[1]["valid_to"] == (TODAY - timedelta(days=11)).isoformat()
    assert price(make_client, w, amount="90", valid_from=TODAY - timedelta(days=40)
                 ).status_code == 409  # older than the newest price
    assert price(make_client, w, amount="-1").status_code == 422
    assert price(make_client, w, role="PURCHASE").status_code == 403
    raw = make_item(Fresh(make_client, "ADMIN"), "RM", "KG")
    assert price(make_client, w, item=raw).status_code == 422  # raw material is not sold


# ------------------------------------------------------------------ sales orders
def test_order_takes_the_price_list_rate_and_computes_gst_and_schedule(make_client):
    w = setup_sales(make_client)
    price(make_client, w, amount="250")
    made = order(make_client, w, customer_po_no="PO-777", lines=[{
        "item_id": w["item"]["item_id"], "ordered_qty": "100",
        "tax_code_id": w["tax"]["tax_code_id"],
        "schedule": [{"due_date": IN(10), "quantity": "60"}, {"due_date": IN(20), "quantity": "40"}]}])
    assert made.status_code == 201, made.text
    so = made.json()
    assert so["so_no"].startswith("SO-") and so["status"] == "OPEN" and so["warnings"] == []
    (line,) = so["lines"]
    assert float(line["unit_price"]) == 250 and line["customer_part_no"] == "CP-1"
    assert float(line["net_amount"]) == 25000 and float(line["gst_amount"]) == 4500
    assert float(so["gross_total"]) == 29500
    assert [float(s["scheduled_qty"]) for s in line["schedule"]] == [60, 40]
    sales = Fresh(make_client, "SALES")
    assert sales.get(f"{S}/orders/{so['so_id']}").json()["customer_po_no"] == "PO-777"
    assert sales.get(f"{S}/orders?customer_po_no=PO-777").json()["total"] == 1
    assert order(make_client, w, customer_po_no="PO-777").status_code == 409  # PO already used


def test_order_validation_and_permissions(make_client):
    w = setup_sales(make_client)
    item_id = w["item"]["item_id"]
    line = {"item_id": item_id, "ordered_qty": "10", "unit_price": "5"}
    assert order(make_client, w, lines=[{**line, "unit_price": None}]).status_code == 422  # no price
    assert order(make_client, w, lines=[line, line]).status_code == 422  # same item twice
    assert order(make_client, w, lines=[{**line, "ordered_qty": "0"}]).status_code == 422
    assert order(make_client, w, lines=[{**line, "schedule": [
        {"due_date": IN(5), "quantity": "11"}]}]).status_code == 422  # more than ordered
    assert order(make_client, w, lines=[{**line, "schedule": [
        {"due_date": IN(5), "quantity": "5"}, {"due_date": IN(5), "quantity": "5"}]}]
    ).status_code == 422  # same date twice
    raw = make_item(Fresh(make_client, "ADMIN"), "RM", "KG")
    assert order(make_client, w, lines=[{**line, "item_id": raw["item_id"]}]).status_code == 422
    assert order(make_client, w, lines=[{**line, "tax_code_id": str(uuid4())}]).status_code == 422
    assert order(make_client, w, unit_id=str(uuid4())).status_code == 422
    for role in ("PURCHASE", "STORES", "QA"):
        assert Fresh(make_client, role).post(f"{S}/orders", json={
            "unit_id": w["unit"]["unit_id"], "customer_id": w["customer"]["customer_id"],
            "lines": [line]}).status_code == 403, role
    other = Fresh(make_client, "ADMIN").post("/api/v1/org/units",
                                              json={"code": code(), "name": "P2"}).json()
    away = Fresh(make_client, "SALES", subject="u-sales-out", unit_ids=[other["unit_id"]])
    so = order(make_client, w).json()
    assert away.get(f"{S}/orders/{so['so_id']}").status_code == 404
    assert away.get(f"{S}/orders").json()["total"] == 0
    assert away.post(f"{S}/orders/{so['so_id']}/cancel").status_code == 404


def test_schedule_can_be_replaced_and_orders_cancelled_or_closed(make_client):
    w = setup_sales(make_client)
    so = order(make_client, w).json()
    line_id = so["lines"][0]["so_line_id"]
    sales = Fresh(make_client, "SALES")
    put = lambda sched: sales.put(f"{S}/orders/{so['so_id']}/lines/{line_id}/schedule",  # noqa: E731
                                  json={"schedule": sched})
    done = put([{"due_date": IN(7), "quantity": "30"}, {"due_date": IN(14), "quantity": "70"}])
    assert done.status_code == 200 and len(done.json()["lines"][0]["schedule"]) == 2
    again = put([{"due_date": IN(14), "quantity": "50"}])  # drops one date, changes the other
    assert [(s["due_date"], float(s["scheduled_qty"])) for s in again.json()["lines"][0][
        "schedule"]] == [(IN(14), 50.0)]
    assert put([{"due_date": IN(14), "quantity": "101"}]).status_code == 422
    assert put([{"due_date": (TODAY - timedelta(days=1)).isoformat(), "quantity": "5"}]
               ).status_code == 422  # before the order date
    assert sales.put(f"{S}/orders/{so['so_id']}/lines/{uuid4()}/schedule",
                     json={"schedule": []}).status_code == 404
    assert sales.post(f"{S}/orders/{so['so_id']}/close").status_code == 409  # nothing shipped
    assert sales.post(f"{S}/orders/{so['so_id']}/cancel").json()["status"] == "CANCELLED"
    assert sales.post(f"{S}/orders/{so['so_id']}/cancel").status_code == 409
    assert put([{"due_date": IN(3), "quantity": "5"}]).status_code == 409  # cancelled


# ------------------------------------------------------------------ credit check
def test_credit_limit_warns_and_blocks_only_when_the_rule_says_so(make_client):
    w = setup_sales(make_client, credit_limit=50000)
    first = order(make_client, w, lines=[{"item_id": w["item"]["item_id"], "ordered_qty": "100",
                                          "unit_price": "100"}])  # 10,000, no GST
    assert first.status_code == 201 and first.json()["warnings"] == []
    big = order(make_client, w, lines=[{"item_id": w["item"]["item_id"], "ordered_qty": "500",
                                        "unit_price": "100"}])  # 50,000 more: over the limit
    assert big.status_code == 201 and "over the credit limit" in big.json()["warnings"][0]
    credit = Fresh(make_client, "SALES").get(
        f"{S}/customers/{w['customer']['customer_id']}/credit").json()
    assert float(credit["exposure"]) == 60000 and credit["over_limit"] is True
    assert float(credit["available"]) == -10000 and credit["blocks"] is False
    admin = Fresh(make_client, "ADMIN")
    rule = "/api/v1/admin/settings/creditLimitBlock"
    assert admin.put(rule, json={"value": True}).status_code == 200
    try:
        blocked = order(make_client, w, lines=[{"item_id": w["item"]["item_id"],
                                                "ordered_qty": "10", "unit_price": "100"}])
        assert blocked.status_code == 409 and "credit limit" in blocked.json()["detail"]
        assert Fresh(make_client, "SALES").get(
            f"{S}/customers/{w['customer']['customer_id']}/credit").json()["blocks"] is True
    finally:
        assert admin.put(rule, json={"value": False}).status_code == 200


# ------------------------------------------------------------------ quotations
def test_a_quotation_is_sent_accepted_and_converted_once(make_client):
    w = setup_sales(make_client)
    price(make_client, w, amount="80")
    sales = Fresh(make_client, "SALES")
    body = {"unit_id": w["unit"]["unit_id"], "customer_id": w["customer"]["customer_id"],
            "enquiry_ref": "RFQ-OEM-12", "valid_until": IN(15),
            "lines": [{"item_id": w["item"]["item_id"], "quantity": "200",
                       "tax_code_id": w["tax"]["tax_code_id"]}]}
    made = sales.post(f"{S}/quotes", json=body)
    assert made.status_code == 201, made.text
    q = made.json()
    assert q["quote_no"].startswith("QT-") and q["status"] == "DRAFT"
    assert float(q["lines"][0]["unit_price"]) == 80 and float(q["gross_total"]) == 18880
    assert sales.post(f"{S}/quotes/{q['quote_id']}/accept").status_code == 409  # not sent yet
    assert sales.post(f"{S}/quotes/{q['quote_id']}/convert", json={}).status_code == 409
    assert sales.post(f"{S}/quotes/{q['quote_id']}/send").json()["status"] == "SENT"
    assert sales.post(f"{S}/quotes/{q['quote_id']}/accept").json()["status"] == "ACCEPTED"
    so = sales.post(f"{S}/quotes/{q['quote_id']}/convert", json={"customer_po_no": "PO-Q1"})
    assert so.status_code == 201, so.text
    assert float(so.json()["lines"][0]["unit_price"]) == 80 and so.json()["quote_id"] == q["quote_id"]
    done = sales.get(f"{S}/quotes/{q['quote_id']}").json()
    assert done["status"] == "CONVERTED" and done["so_id"] == so.json()["so_id"]
    assert sales.post(f"{S}/quotes/{q['quote_id']}/convert", json={}).status_code == 409
    rejected = sales.post(f"{S}/quotes", json=body).json()
    sales.post(f"{S}/quotes/{rejected['quote_id']}/send")
    assert sales.post(f"{S}/quotes/{rejected['quote_id']}/reject").json()["status"] == "REJECTED"
    assert sales.get(f"{S}/quotes?status=converted").json()["total"] >= 1


def test_quotation_validation_expiry_and_permissions(make_client, api_database):
    import psycopg

    w = setup_sales(make_client)
    sales = Fresh(make_client, "SALES")
    body = {"unit_id": w["unit"]["unit_id"], "customer_id": w["customer"]["customer_id"],
            "valid_until": IN(5), "lines": [{"item_id": w["item"]["item_id"], "quantity": "5",
                                             "unit_price": "10"}]}
    assert sales.post(f"{S}/quotes", json={**body, "valid_until": IN(-1)}).status_code == 422
    assert sales.post(f"{S}/quotes", json={**body, "lines": [
        {**body["lines"][0], "unit_price": None}]}).status_code == 422  # no price on the list
    for role in ("PURCHASE", "STORES"):
        assert Fresh(make_client, role).post(f"{S}/quotes", json=body).status_code == 403
    q = sales.post(f"{S}/quotes", json=body).json()
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("update erp.quotation set valid_until = current_date - 1 where quote_id = %s",
                     (q["quote_id"],))
    late = sales.post(f"{S}/quotes/{q['quote_id']}/send")
    assert late.status_code == 409 and "expired" in late.json()["detail"]
    assert sales.get(f"{S}/quotes/{q['quote_id']}").json()["status"] == "EXPIRED"


# ------------------------------------------------------------------ schedule import
def order_with_po(make_client, w, po="PO-100", qty="100"):
    return order(make_client, w, customer_po_no=po, lines=[{
        "item_id": w["item"]["item_id"], "ordered_qty": qty, "unit_price": "10"}]).json()


def upload(make_client, w, text=None, source="CSV", content=None, role="SALES"):
    body = {"customer_id": w["customer"]["customer_id"], "source_type": source}
    if text is not None:
        body["text"] = text
    if content is not None:
        body["content_base64"] = base64.b64encode(content).decode()
    return Fresh(make_client, role).post(f"{S}/schedule-imports", json=body)


def test_csv_import_previews_matches_and_applies(make_client):
    w = setup_sales(make_client)
    so = order_with_po(make_client, w)
    csv_text = (f"PO,Part No,Delivery Date,Qty\nPO-100,CP-1,{IN(10)},40\nPO-100,CP-1,{IN(20)},30\n"
                "PO-999,CP-1,2026-12-01,5\nPO-100,CP-X,2026-12-01,5\nPO-100,CP-1,31/02/2026,5\n"
                f"PO-100,CP-1,{IN(30)},0\n")
    up = upload(make_client, w, csv_text)
    assert up.status_code == 201, up.text
    imp = up.json()
    assert imp["status"] == "PREVIEW" and imp["rows_total"] == 6 and imp["rows_ok"] == 2
    actions = [(r["row_no"], r["action"]) for r in imp["rows"]]
    assert actions == [(2, "CREATE"), (3, "CREATE"), (4, "ERROR"), (5, "ERROR"), (6, "ERROR"),
                       (7, "ERROR")]
    errors = {r["row_no"]: r["error"] for r in imp["rows"] if r["error"]}
    assert "PO-999" in errors[4] and "cross-reference" in errors[5]
    assert "date" in errors[6] and "quantity" in errors[7]
    assert imp["rows"][0]["so_no"] == so["so_no"] and imp["rows"][0]["item_code"]
    sales = Fresh(make_client, "SALES")
    assert sales.get(f"{S}/orders/{so['so_id']}").json()["lines"][0]["schedule"] == []  # not yet
    refused = sales.post(f"{S}/schedule-imports/{imp['import_id']}/apply", json={})
    assert refused.status_code == 409 and "4 row(s)" in refused.json()["detail"]
    done = sales.post(f"{S}/schedule-imports/{imp['import_id']}/apply", json={"skip_errors": True})
    assert done.status_code == 200 and done.json()["status"] == "APPLIED"
    sched = sales.get(f"{S}/orders/{so['so_id']}").json()["lines"][0]["schedule"]
    assert [(s["due_date"], float(s["scheduled_qty"])) for s in sched] == [
        (IN(10), 40.0), (IN(20), 30.0)]
    assert sales.post(f"{S}/schedule-imports/{imp['import_id']}/apply", json={}).status_code == 409
    assert sales.post(f"{S}/schedule-imports/{imp['import_id']}/discard").status_code == 409


def test_a_second_import_updates_dates_and_cannot_exceed_the_order(make_client):
    w = setup_sales(make_client)
    so = order_with_po(make_client, w, qty="100")
    sales = Fresh(make_client, "SALES")
    first = upload(make_client, w, f"po\tpart\tdate\tqty\nPO-100\tCP-1\t{IN(10)}\t60\n").json()
    sales.post(f"{S}/schedule-imports/{first['import_id']}/apply", json={})
    second = upload(make_client, w, f"po;part;date;qty\nPO-100;CP-1;{IN(10)};80\n"
                                    f"PO-100;CP-1;{IN(15)};30\n").json()
    assert [r["action"] for r in second["rows"]] == ["UPDATE", "ERROR"]  # 80 + 30 > 100
    assert "exceed the ordered quantity" in second["rows"][1]["error"]
    sales.post(f"{S}/schedule-imports/{second['import_id']}/apply", json={"skip_errors": True})
    sched = sales.get(f"{S}/orders/{so['so_id']}").json()["lines"][0]["schedule"]
    assert [(s["due_date"], float(s["scheduled_qty"])) for s in sched] == [(IN(10), 80.0)]
    dup = upload(make_client, w, f"po,part,date,qty\nPO-100,CP-1,{IN(40)},5\n"
                                 f"PO-100,CP-1,{IN(40)},6\n").json()
    assert [r["action"] for r in dup["rows"]] == ["CREATE", "ERROR"]
    assert "twice" in dup["rows"][1]["error"]


def test_xlsx_import_and_the_apply_check_uses_todays_state(make_client):
    w = setup_sales(make_client)
    so = order_with_po(make_client, w, po="PO-XL", qty="50")
    book = Workbook()
    sheet = book.active
    sheet.append(["Customer PO", "Part Number", "Delivery Date", "Quantity"])
    sheet.append(["PO-XL", "CP-1", TODAY + timedelta(days=12), 25])
    sheet.append(["PO-XL", "CP-1", f"{(TODAY + timedelta(days=22)).strftime('%d/%m/%Y')}", "20"])
    buffer = io.BytesIO()
    book.save(buffer)
    up = upload(make_client, w, source="XLSX", content=buffer.getvalue())
    assert up.status_code == 201 and up.json()["rows_ok"] == 2, up.text
    # the order is cancelled between preview and apply: the apply refuses the stale rows
    Fresh(make_client, "SALES").post(f"{S}/orders/{so['so_id']}/cancel")
    stale = Fresh(make_client, "SALES").post(
        f"{S}/schedule-imports/{up.json()['import_id']}/apply", json={})
    assert stale.status_code == 409 and "2 row(s)" in stale.json()["detail"]
    Fresh(make_client, "SALES").post(f"{S}/schedule-imports/{up.json()['import_id']}/discard")
    assert Fresh(make_client, "SALES").get(
        f"{S}/schedule-imports/{up.json()['import_id']}").json()["status"] == "DISCARDED"


def test_import_validation_and_permissions(make_client):
    w = setup_sales(make_client)
    order_with_po(make_client, w)
    assert upload(make_client, w, "").status_code == 422
    assert upload(make_client, w, "po,part,date,qty\n").status_code == 422  # no data rows
    assert upload(make_client, w, "a,b,c\n1,2,3\n").status_code == 422  # columns not found
    assert upload(make_client, w, source="XLSX", content=b"not a workbook").status_code == 422
    bad = Fresh(make_client, "SALES").post(f"{S}/schedule-imports", json={
        "customer_id": w["customer"]["customer_id"], "source_type": "CSV",
        "text": "x", "content_base64": "eA=="})
    assert bad.status_code == 422  # both sources
    missing = Fresh(make_client, "SALES").post(f"{S}/schedule-imports", json={
        "customer_id": str(uuid4()), "source_type": "CSV", "text": "po,part,date,qty\n1,2,3,4"})
    assert missing.status_code == 422
    for role in ("PURCHASE", "STORES"):
        assert upload(make_client, w, "po,part,date,qty\nP,C,2026-01-01,1\n", role=role
                      ).status_code == 403
    listing = Fresh(make_client, "SALES").get(f"{S}/schedule-imports").json()
    assert all("rows" not in i for i in listing["items"])
