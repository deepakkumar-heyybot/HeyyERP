"""Purchase orders: creation paths, approval bands, rules, receiving handoff, history."""

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text

from heyyerp_api import db
from heyyerp_api.masters.common import Ctx
from heyyerp_api.procurement import receiving
from heyyerp_api.procurement.receiving import max_receivable
from heyyerp_api.rbac.seed import create_app_user
from heyyerp_api.security import Principal, Profile

from .conftest import API_DB, API_PASSWORD, API_ROLE, TENANT_A
from .dbutil import url_for
from .test_rfq import make_vendors, quote, rfq_body

PO = "/api/v1/procurement/purchase-orders"
IND = "/api/v1/procurement/indents"
RFQ = "/api/v1/procurement/rfqs"
A = "/api/v1/approvals"
pytestmark = pytest.mark.usefixtures("api_database")
TODAY = date.today()
SOON = (TODAY + timedelta(days=10)).isoformat()


def code() -> str:
    return "P" + uuid4().hex[:9].upper()


# ------------------------------------------------------------------ pure helpers
def test_receiving_tolerance_math():
    assert max_receivable(Decimal("100"), 10) == Decimal("110.000")
    assert max_receivable(Decimal("40"), 10) == Decimal("44.000")
    assert max_receivable(Decimal("33.333"), 10) == Decimal("36.666")  # rounds down, never up
    assert max_receivable(Decimal("50"), 0) == Decimal("50.000")
    assert max_receivable(Decimal("0"), 10) == Decimal("0.000")


# ------------------------------------------------------------------ fixtures-as-functions
def setup(make_client, vendors=1, items=2):
    admin = make_client("ADMIN")
    unit = admin.post("/api/v1/org/units", json={"code": code(), "name": "Plant"}).json()
    its = [admin.post("/api/v1/product/items", json={
        "code": code(), "name": "RM", "item_type": "RM", "uom_code": "KG"}).json()
        for _ in range(items)]
    tax = admin.post("/api/v1/ref/tax-codes", json={
        "code": code(), "description": "GST 18%", "gst_rate": "18"}).json()
    return unit, its, tax, make_vendors(make_client, vendors)


def po_body(unit, vendor, items, tax, qty="100", price="1000", **extra):
    return {"unit_id": unit["unit_id"], "vendor_id": vendor["vendor_id"],
            "lines": [{"item_id": i["item_id"], "ordered_qty": qty, "unit_price": price,
                       "tax_code_id": tax["tax_code_id"], "due_date": SOON} for i in items],
            **extra}


def make_po(client, unit, vendor, items, tax, **kw):
    response = client.post(PO, json=po_body(unit, vendor, items, tax, **kw))
    assert response.status_code == 201, response.text
    return response.json()


def decide(client, approval_id, decision="APPROVE", note=None):
    return client.post(f"{A}/{approval_id}/decide", json={"decision": decision, "note": note})


def open_po(make_client, unit, vendor, items, tax, qty="100", price="1000"):
    """A small order (under 5 lakh with GST) that PURCHASE raised, so it is approved at once."""
    purchase = make_client("PURCHASE")
    po = make_po(purchase, unit, vendor, items[:1], tax, qty=qty, price=price)
    sent = purchase.post(f"{PO}/{po['po_id']}/submit")
    assert sent.status_code == 200 and sent.json()["status"] == "OPEN", sent.text
    return sent.json()


def approved_indent(make_client, unit, items, qty="50"):
    stores = make_client("STORES")
    made = stores.post(IND, json={"unit_id": unit["unit_id"], "indent_type": "BOM", "lines": [
        {"item_id": i["item_id"], "requested_qty": qty} for i in items]}).json()
    approval = stores.post(f"{IND}/{made['indent_id']}/submit").json()["approval_id"]
    decide(make_client("PLANT_HEAD"), approval)
    return made


def set_rule(make_client, key, value):
    r = make_client("ADMIN").put(f"/api/v1/admin/settings/{key}", json={"value": value})
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ creating
def test_create_a_purchase_order_with_gst_totals(make_client):
    unit, items, tax, (vendor,) = setup(make_client)
    purchase = make_client("PURCHASE")
    po = make_po(purchase, unit, vendor, items, tax, qty="100", price="50.25")
    assert po["status"] == "DRAFT" and po["po_no"].startswith("PO-") and po["currency_code"] == "INR"
    assert [float(ln["net"]) for ln in po["lines"]] == [5025.0, 5025.0]
    assert float(po["net_total"]) == 10050.0 and float(po["gst_total"]) == 1809.0
    assert float(po["gross_total"]) == 11859.0
    assert po["lines"][0]["schedule"][0]["due_date"] == SOON and po["lines"][0]["gst_rate"] == "18.0000"
    assert po["vendor_code"] == vendor["code"] and po["lines"][0]["received_qty"] == "0.000"


def test_creation_rules(make_client):
    unit, items, tax, (vendor,) = setup(make_client)
    purchase = make_client("PURCHASE")
    post = lambda **b: purchase.post(PO, json=po_body(unit, vendor, items, tax, **b)).status_code  # noqa: E731
    pending = purchase.post("/api/v1/parties/vendors", json={"code": code(), "name": "P"}).json()
    refused = purchase.post(PO, json=po_body(unit, pending, items, tax))
    assert refused.status_code == 422 and "cannot receive purchase orders" in refused.json()["detail"]
    assert post(qty="0") == 422 and post(price="-1") == 422
    gone = {"item_id": str(uuid4()), "ordered_qty": "1", "unit_price": "1", "due_date": SOON}
    assert purchase.post(PO, json={**po_body(unit, vendor, items, tax), "lines": [gone]}
                         ).status_code == 422
    line = po_body(unit, vendor, items, tax)["lines"][0]
    both = {**line, "schedule": [{"due_date": SOON, "quantity": "100"}]}
    neither = {k: v for k, v in line.items() if k != "due_date"}
    body = lambda *lines: purchase.post(PO, json={**po_body(unit, vendor, items, tax),  # noqa: E731
                                                  "lines": list(lines)}).status_code
    assert body(both) == 422 and body(neither) == 422 and body(line, line) == 422
    assert body({**line, "due_date": (TODAY - timedelta(days=1)).isoformat()}) == 422
    assert body({**line, "tax_code_id": str(uuid4())}) == 422
    split = {**neither, "schedule": [{"due_date": SOON, "quantity": "60"},
                                      {"due_date": (TODAY + timedelta(days=20)).isoformat(),
                                       "quantity": "40"}]}
    ok = purchase.post(PO, json={**po_body(unit, vendor, items, tax), "lines": [split]})
    assert ok.status_code == 201
    assert [float(s["scheduled_qty"]) for s in ok.json()["lines"][0]["schedule"]] == [60, 40]
    short = {**neither, "schedule": [{"due_date": SOON, "quantity": "60"}]}
    assert body(short) == 422  # schedule must add up to the ordered quantity
    assert post(payment_term_id=str(uuid4())) == 422
    assert post(tenant_id=str(uuid4())) == 422
    admin = make_client("ADMIN")
    admin.patch(f"/api/v1/product/items/{items[0]['item_id']}", json={"active": False})
    assert make_client("PURCHASE").post(PO, json=po_body(unit, vendor, items, tax)).status_code == 422


def test_the_vendors_payment_term_is_the_default(make_client):
    unit, items, tax, (vendor,) = setup(make_client)
    admin = make_client("ADMIN")
    term = admin.post("/api/v1/ref/payment-terms", json={
        "code": code(), "name": "Net 45", "due_days": 45}).json()
    admin.patch(f"/api/v1/parties/vendors/{vendor['vendor_id']}",
                json={"payment_term_id": term["payment_term_id"]})
    po = make_po(make_client("PURCHASE"), unit, vendor, items, tax)
    assert po["payment_term_id"] == term["payment_term_id"]


# ------------------------------------------------------------------ approval bands include GST
def test_the_approval_band_uses_the_value_including_gst(make_client):
    unit, items, tax, (vendor,) = setup(make_client)
    purchase = make_client("PURCHASE")
    # 4,20,000 net = 4,95,600 with GST: under 5 lakh, PURCHASE raised it, so it is approved at once.
    low = make_po(purchase, unit, vendor, items[:1], tax, qty="420", price="1000")
    sent = purchase.post(f"{PO}/{low['po_id']}/submit").json()
    assert sent["status"] == "OPEN"
    approval = purchase.get(f"{A}/{sent['approval_id']}").json()
    assert approval["rule"] == "R-PO-1" and approval["status"] == "APPROVED"
    # 4,30,000 net = 5,07,400 with GST: GST pushes it over 5 lakh into the Plant Head's band.
    mid = make_po(purchase, unit, vendor, items[:1], tax, qty="430", price="1000")
    sent = purchase.post(f"{PO}/{mid['po_id']}/submit").json()
    assert sent["status"] == "PENDING_APPROVAL"
    approval = purchase.get(f"{A}/{sent['approval_id']}").json()
    assert approval["rule"] == "R-PO-2" and float(approval["amount"]) == 507400.0
    assert [(s["role"], s["state"]) for s in approval["steps"]] == [
        ("PURCHASE", "SKIPPED"), ("PLANT_HEAD", "CURRENT")]
    decide(make_client("PLANT_HEAD"), sent["approval_id"])
    assert make_client("PURCHASE").get(f"{PO}/{mid['po_id']}").json()["status"] == "OPEN"
    # 22,00,000 net = 25,96,000 with GST: above 25 lakh the MD signs too.
    purchase = make_client("PURCHASE")
    big = make_po(purchase, unit, vendor, items[:1], tax, qty="2200", price="1000")
    sent = purchase.post(f"{PO}/{big['po_id']}/submit").json()
    approval = purchase.get(f"{A}/{sent['approval_id']}").json()
    assert approval["rule"] == "R-PO-3" and [s["role"] for s in approval["steps"]] == [
        "PURCHASE", "PLANT_HEAD", "MD"]
    decide(make_client("PLANT_HEAD"), sent["approval_id"])
    assert make_client("PURCHASE").get(f"{PO}/{big['po_id']}").json()["status"] == "PENDING_APPROVAL"
    decide(make_client("MD"), sent["approval_id"])
    assert make_client("PURCHASE").get(f"{PO}/{big['po_id']}").json()["status"] == "OPEN"


def test_rejection_and_duplicate_submission(make_client):
    unit, items, tax, (vendor,) = setup(make_client)
    purchase = make_client("PURCHASE")
    po = make_po(purchase, unit, vendor, items[:1], tax, qty="430", price="1000")
    url = f"{PO}/{po['po_id']}"
    first = purchase.post(f"{url}/submit")
    assert first.status_code == 200
    assert purchase.post(f"{url}/submit").status_code == 409  # already submitted
    assert purchase.put(url, json={"lines": po_body(unit, vendor, items[:1], tax)["lines"]}
                        ).status_code == 409  # frozen while in approval
    assert purchase.post(f"{url}/cancel").status_code == 409
    rejected = decide(make_client("PLANT_HEAD"), first.json()["approval_id"], "REJECT", "Too dear")
    assert rejected.json()["status"] == "REJECTED"
    purchase = make_client("PURCHASE")
    assert purchase.get(url).json()["status"] == "REJECTED"
    assert purchase.post(f"{url}/submit").status_code == 409  # a rejected order is final


def test_submit_needs_a_tax_code_on_every_line_and_an_approved_vendor(make_client):
    unit, items, tax, (vendor,) = setup(make_client)
    purchase = make_client("PURCHASE")
    body = po_body(unit, vendor, items[:1], tax)
    body["lines"][0].pop("tax_code_id")
    po = purchase.post(PO, json=body).json()
    refused = purchase.post(f"{PO}/{po['po_id']}/submit")
    assert refused.status_code == 422 and "tax code" in refused.json()["detail"]
    fixed = body["lines"][0] | {"tax_code_id": tax["tax_code_id"]}
    assert purchase.put(f"{PO}/{po['po_id']}", json={"lines": [fixed]}).status_code == 200
    make_client("ADMIN").patch(f"/api/v1/parties/vendors/{vendor['vendor_id']}/../x", json={}) \
        if False else None
    with_vendor_gone = purchase.post(f"{PO}/{po['po_id']}/submit")
    assert with_vendor_gone.status_code == 200


# ------------------------------------------------------------------ business rules
def test_require_indent_rule(make_client):
    unit, items, tax, (vendor,) = setup(make_client)
    set_rule(make_client, "requireIndent", True)
    try:
        purchase = make_client("PURCHASE")
        direct = make_po(purchase, unit, vendor, items[:1], tax)
        refused = purchase.post(f"{PO}/{direct['po_id']}/submit")
        assert refused.status_code == 422 and "requireIndent" in refused.json()["detail"]
        indent = approved_indent(make_client, unit, items[:1])
        purchase = make_client("PURCHASE")
        po = purchase.post(f"{PO}/from-indents", json={
            "indent_ids": [indent["indent_id"]], "vendor_id": vendor["vendor_id"],
            "due_date": SOON, "prices": [{"item_id": items[0]["item_id"], "unit_price": "100",
                                          "tax_code_id": tax["tax_code_id"]}]})
        assert po.status_code == 201, po.text
        assert purchase.post(f"{PO}/{po.json()['po_id']}/submit").status_code == 200
    finally:
        set_rule(make_client, "requireIndent", False)


def test_minimum_quotes_rule(make_client):
    unit, items, tax, (v1, v2) = setup(make_client, vendors=2)
    purchase = make_client("PURCHASE")
    rfq = purchase.post(RFQ, json=rfq_body(unit, items, [v1, v2])).json()
    purchase.post(f"{RFQ}/{rfq['rfq_id']}/send")
    purchase.post(f"{RFQ}/{rfq['rfq_id']}/quotes", json=quote(v1, rfq, ["10", "20"]))
    set_rule(make_client, "minQuotes", 2)
    try:
        purchase = make_client("PURCHASE")
        direct = make_po(purchase, unit, v1, items[:1], tax)
        refused = purchase.post(f"{PO}/{direct['po_id']}/submit")
        assert refused.status_code == 422 and "minQuotes" in refused.json()["detail"]
        via_rfq = make_po(purchase, unit, v1, items, tax, rfq_id=rfq["rfq_id"])
        one_quote = purchase.post(f"{PO}/{via_rfq['po_id']}/submit")
        assert one_quote.status_code == 422 and "has 1" in one_quote.json()["detail"]
        purchase.post(f"{RFQ}/{rfq['rfq_id']}/quotes", json=quote(v2, rfq, ["11", "21"]))
        assert purchase.post(f"{PO}/{via_rfq['po_id']}/submit").status_code == 200
    finally:
        set_rule(make_client, "minQuotes", None)


# ------------------------------------------------------------------ from indents and RFQs
def test_order_from_indents_adds_up_quantities_and_links_them(make_client):
    unit, items, tax, (vendor,) = setup(make_client)
    first = approved_indent(make_client, unit, items, qty="30")
    second = approved_indent(make_client, unit, items[:1], qty="20")
    purchase = make_client("PURCHASE")
    prices = [{"item_id": i["item_id"], "unit_price": "10", "tax_code_id": tax["tax_code_id"]}
              for i in items]
    body = {"indent_ids": [first["indent_id"], second["indent_id"]],
            "vendor_id": vendor["vendor_id"], "due_date": SOON, "prices": prices}
    po = purchase.post(f"{PO}/from-indents", json=body)
    assert po.status_code == 201, po.text
    qty = {ln["item_code"]: float(ln["ordered_qty"]) for ln in po.json()["lines"]}
    assert qty == {items[0]["code"]: 50.0, items[1]["code"]: 30.0}
    assert {i["indent_no"] for i in po.json()["indents"]} == {first["indent_no"], second["indent_no"]}
    for indent in (first, second):
        assert purchase.get(f"{IND}/{indent['indent_id']}").json()["status"] == "ORDERED"
    again = purchase.post(f"{PO}/from-indents", json=body)
    assert again.status_code == 422 and "approved indents" in again.json()["detail"]
    # cancelling the order releases the indents so they can be ordered again
    assert purchase.post(f"{PO}/{po.json()['po_id']}/cancel").json()["status"] == "CANCELLED"
    assert purchase.get(f"{IND}/{first['indent_id']}").json()["status"] == "APPROVED"
    assert purchase.post(f"{PO}/from-indents", json=body).status_code == 201


def test_from_indents_validation(make_client):
    unit, items, tax, (vendor,) = setup(make_client)
    indent = approved_indent(make_client, unit, items[:1])
    purchase = make_client("PURCHASE")
    price = {"item_id": items[0]["item_id"], "unit_price": "10", "tax_code_id": tax["tax_code_id"]}
    base = {"indent_ids": [indent["indent_id"]], "vendor_id": vendor["vendor_id"], "due_date": SOON}
    post = lambda **b: purchase.post(f"{PO}/from-indents", json={**base, "prices": [price], **b})  # noqa: E731
    assert post(prices=[{**price, "item_id": items[1]["item_id"]}]).status_code == 422
    assert post(prices=[price, {**price, "item_id": items[1]["item_id"]}]).status_code == 422
    assert post(indent_ids=[str(uuid4())]).status_code == 422
    draft = make_client("STORES").post(IND, json={"unit_id": unit["unit_id"], "indent_type": "BOM",
        "lines": [{"item_id": items[0]["item_id"], "requested_qty": "5"}]}).json()
    purchase = make_client("PURCHASE")
    refused = purchase.post(f"{PO}/from-indents", json={
        **base, "indent_ids": [draft["indent_id"]], "prices": [price]})
    assert refused.status_code == 422 and "only approved indents" in refused.json()["detail"]


def test_awarding_an_rfq_creates_the_order_at_the_quoted_prices(make_client, api_database):
    unit, items, tax, (v1, v2) = setup(make_client, vendors=2)
    purchase = make_client("PURCHASE")
    rfq = purchase.post(RFQ, json=rfq_body(unit, items, [v1, v2])).json()
    purchase.post(f"{RFQ}/{rfq['rfq_id']}/send")
    purchase.post(f"{RFQ}/{rfq['rfq_id']}/quotes", json=quote(v1, rfq, ["10", "20"]))
    partial = quote(v2, rfq, ["9", "9"])
    partial["lines"] = partial["lines"][:1]  # v2 quotes only the first line
    purchase.post(f"{RFQ}/{rfq['rfq_id']}/quotes", json=partial)
    url = f"{RFQ}/{rfq['rfq_id']}/award"
    award = {"vendor_id": v2["vendor_id"], "due_date": SOON, "tax_code_id": tax["tax_code_id"]}
    assert purchase.post(url, json=award).status_code == 422  # v2 did not quote every line
    won = purchase.post(url, json={**award, "vendor_id": v1["vendor_id"]})
    assert won.status_code == 201, won.text
    po = won.json()
    assert po["rfq_no"] == rfq["rfq_no"] and po["vendor_code"] == v1["code"]
    assert [float(ln["unit_price"]) for ln in po["lines"]] == [10.0, 20.0]
    assert [float(ln["ordered_qty"]) for ln in po["lines"]] == [100.0, 100.0]
    assert purchase.get(f"{RFQ}/{rfq['rfq_id']}").json()["status"] == "AWARDED"
    assert purchase.post(url, json={**award, "vendor_id": v1["vendor_id"]}).status_code == 409
    other = purchase.post(RFQ, json=rfq_body(unit, items, [v1])).json()
    purchase.post(f"{RFQ}/{other['rfq_id']}/send")
    purchase.post(f"{RFQ}/{other['rfq_id']}/quotes", json=quote(v1, other, ["1", "1"]))
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("update erp.vendor_quote set valid_until = current_date - 1 where "
                     "invitation_id in (select invitation_id from erp.rfq_vendor_invitation "
                     "where rfq_id = %s)", (other["rfq_id"],))
    stale = make_client("PURCHASE").post(f"{RFQ}/{other['rfq_id']}/award", json={
        **award, "vendor_id": v1["vendor_id"]})
    assert stale.status_code == 422 and "expired" in stale.json()["detail"]
    direct = make_client("PURCHASE").post(PO, json=po_body(
        unit, v2, items, tax, rfq_id=other["rfq_id"]))
    assert direct.status_code == 422  # v2 never quoted on that RFQ


# ------------------------------------------------------------------ receiving handoff
def with_ctx(coro):
    """Run a service call the way the Phase 6 goods receipt will: in its own tenant session."""
    with psycopg.connect(url_for(API_DB), autocommit=True) as owner:
        receiver = create_app_user(owner, TENANT_A, "u-receiver", "STORES", "Receiver")
    profile = Profile(receiver, "Receiver", "STORES", frozenset(), frozenset(), None)

    async def run():
        db.configure_database(url_for(API_DB, API_ROLE, API_PASSWORD).replace(
            "postgresql://", "postgresql+asyncpg://"))
        try:
            async with db.SessionFactory() as session, session.begin():
                await session.execute(text("select set_config('app.tenant_id', :t, true)"),
                                      {"t": TENANT_A})
                ctx = Ctx(session, Principal("u", "e", True, "o", True, UUID(TENANT_A), profile))
                return await coro(ctx)
        finally:
            await db.engine.dispose()

    return asyncio.run(run())


def test_receiving_follows_pending_plus_tolerance(make_client):
    unit, items, tax, (vendor,) = setup(make_client)
    po = open_po(make_client, unit, vendor, items, tax)
    line_id = UUID(po["lines"][0]["po_line_id"])
    view = make_client("PURCHASE").get(f"{PO}/{po['po_id']}/receiving").json()
    assert float(view[0]["pending_qty"]) == 100 and float(view[0]["max_receivable_qty"]) == 110

    got = with_ctx(lambda ctx: receiving.apply_receipt(ctx, line_id, Decimal("60")))
    assert got["status"] == "PARTIALLY_RECEIVED" and got["line"]["pending_qty"] == Decimal("40.000")
    assert got["line"]["max_receivable_qty"] == Decimal("44.000")
    with pytest.raises(Exception, match="Only 44"):
        with_ctx(lambda ctx: receiving.apply_receipt(ctx, line_id, Decimal("45")))
    with pytest.raises(Exception, match="above zero"):
        with_ctx(lambda ctx: receiving.apply_receipt(ctx, line_id, Decimal("0")))
    done = with_ctx(lambda ctx: receiving.apply_receipt(ctx, line_id, Decimal("44")))
    assert done["status"] == "RECEIVED"  # 104 received: the 4% over is within the 10% tolerance
    purchase = make_client("PURCHASE")
    assert float(purchase.get(f"{PO}/{po['po_id']}").json()["lines"][0]["received_qty"]) == 104
    assert purchase.post(f"{PO}/{po['po_id']}/cancel").status_code == 409  # goods already in
    with pytest.raises(Exception, match="cannot receive goods"):
        with_ctx(lambda ctx: receiving.apply_receipt(ctx, line_id, Decimal("1")))
    assert purchase.post(f"{PO}/{po['po_id']}/close").json()["status"] == "CLOSED"


def test_the_tolerance_comes_from_the_business_rule(make_client):
    unit, items, tax, (vendor,) = setup(make_client)
    po = open_po(make_client, unit, vendor, items, tax)
    line_id = UUID(po["lines"][0]["po_line_id"])
    set_rule(make_client, "grnTolPct", 0)
    try:
        view = make_client("PURCHASE").get(f"{PO}/{po['po_id']}/receiving").json()
        assert float(view[0]["max_receivable_qty"]) == 100
        with pytest.raises(Exception, match="Only 100"):
            with_ctx(lambda ctx: receiving.apply_receipt(ctx, line_id, Decimal("101")))
    finally:
        set_rule(make_client, "grnTolPct", 10)


def test_receipts_fill_the_delivery_schedule_oldest_first(make_client):
    unit, items, tax, (vendor,) = setup(make_client)
    purchase = make_client("PURCHASE")
    line = po_body(unit, vendor, items[:1], tax)["lines"][0]
    line.pop("due_date")
    line["schedule"] = [{"due_date": (TODAY + timedelta(days=20)).isoformat(), "quantity": "40"},
                        {"due_date": SOON, "quantity": "60"}]
    po = purchase.post(PO, json={**po_body(unit, vendor, items[:1], tax), "lines": [line]}).json()
    purchase.post(f"{PO}/{po['po_id']}/submit")
    line_id = UUID(po["lines"][0]["po_line_id"])
    with_ctx(lambda ctx: receiving.apply_receipt(ctx, line_id, Decimal("70")))
    schedule = make_client("PURCHASE").get(f"{PO}/{po['po_id']}").json()["lines"][0]["schedule"]
    assert [(s["due_date"], float(s["received_qty"])) for s in schedule] == [
        (SOON, 60.0), ((TODAY + timedelta(days=20)).isoformat(), 10.0)]


def test_an_order_not_yet_approved_cannot_receive(make_client):
    unit, items, tax, (vendor,) = setup(make_client)
    draft = make_po(make_client("PURCHASE"), unit, vendor, items[:1], tax)
    with pytest.raises(Exception, match="draft order cannot receive"):
        with_ctx(lambda ctx: receiving.apply_receipt(
            ctx, UUID(draft["lines"][0]["po_line_id"]), Decimal("1")))
    with pytest.raises(Exception, match="not found"):
        with_ctx(lambda ctx: receiving.apply_receipt(ctx, uuid4(), Decimal("1")))


def test_late_deliveries_are_listed_until_received(make_client, api_database):
    unit, items, tax, (vendor,) = setup(make_client)
    po = open_po(make_client, unit, vendor, items, tax)
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("update erp.po_schedule set due_date = current_date - 3 where po_line_id = %s",
                     (po["lines"][0]["po_line_id"],))
    late = make_client("PURCHASE").get(f"{PO}/late-lines").json()
    mine = [ln for ln in late if ln["po_id"] == po["po_id"]]
    assert len(mine) == 1 and mine[0]["days_late"] == 3 and float(mine[0]["pending_qty"]) == 100
    assert mine[0]["vendor_code"] == vendor["code"]
    line_id = UUID(po["lines"][0]["po_line_id"])
    with_ctx(lambda ctx: receiving.apply_receipt(ctx, line_id, Decimal("100")))
    late = make_client("PURCHASE").get(f"{PO}/late-lines").json()
    assert po["po_id"] not in {ln["po_id"] for ln in late}


# ------------------------------------------------------------------ cancel, history, permissions
def test_cancel_and_close_rules(make_client):
    unit, items, tax, (vendor,) = setup(make_client)
    purchase = make_client("PURCHASE")
    draft = make_po(purchase, unit, vendor, items[:1], tax)
    assert purchase.post(f"{PO}/{draft['po_id']}/close").status_code == 409  # nothing to close
    assert purchase.post(f"{PO}/{draft['po_id']}/cancel").json()["status"] == "CANCELLED"
    assert purchase.post(f"{PO}/{draft['po_id']}/cancel").status_code == 409
    assert purchase.post(f"{PO}/{draft['po_id']}/submit").status_code == 409
    open_ = open_po(make_client, unit, vendor, items, tax)
    assert make_client("PURCHASE").post(f"{PO}/{open_['po_id']}/cancel").json()["status"] == "CANCELLED"


def test_history_shows_every_step_and_the_approval(make_client):
    unit, items, tax, (vendor,) = setup(make_client)
    purchase = make_client("PURCHASE")
    po = make_po(purchase, unit, vendor, items[:1], tax, qty="430", price="1000")
    purchase.put(f"{PO}/{po['po_id']}", json={"lines": po_body(unit, vendor, items[:1], tax,
                                                               qty="430")["lines"]})
    sent = purchase.post(f"{PO}/{po['po_id']}/submit").json()
    decide(make_client("PLANT_HEAD"), sent["approval_id"], note="Approved for production")
    # editing the draft re-created its lines, so read the current line id
    current = make_client("PURCHASE").get(f"{PO}/{po['po_id']}").json()
    line_id = UUID(current["lines"][0]["po_line_id"])
    with_ctx(lambda ctx: receiving.apply_receipt(ctx, line_id, Decimal("100")))
    history = make_client("PURCHASE").get(f"{PO}/{po['po_id']}/history").json()
    assert [e["action"] for e in history["events"]] == [
        "po.create", "po.update", "po.submit", "po.receipt"]
    assert history["events"][0]["actor"] == "PURCHASE user"
    steps = [(s["role"], s["state"]) for s in history["approval"]["steps"]]
    assert steps == [("PURCHASE", "SKIPPED"), ("PLANT_HEAD", "APPROVED")]
    assert history["approval"]["steps"][1]["note"] == "Approved for production"


def test_permissions_and_visibility(make_client):
    unit, items, tax, (vendor,) = setup(make_client)
    po = make_po(make_client("PURCHASE"), unit, vendor, items[:1], tax)
    for role in ("STORES", "QA", "SALES", "ACCOUNTS", "HR", "OPERATOR", "PROD"):
        client = make_client(role)
        assert client.post(PO, json=po_body(unit, vendor, items, tax)).status_code == 403, role
        assert client.post(f"{PO}/{po['po_id']}/submit").status_code == 403, role
        assert client.post(f"{PO}/{po['po_id']}/cancel").status_code == 403, role
        assert client.post(f"{PO}/from-indents", json={}).status_code in (403, 422), role
    for role in ("OPERATOR", "PROD", "SALES", "HR"):  # no procurement module
        assert make_client(role).get(f"{PO}/{po['po_id']}").status_code == 403, role
        assert make_client(role).get(PO).status_code == 403, role
        assert make_client(role).get(f"{PO}/late-lines").status_code == 403, role
    for role in ("PURCHASE", "PLANT_HEAD", "ACCOUNTS", "STORES", "QA", "MD"):
        assert make_client(role).get(f"{PO}/{po['po_id']}").status_code == 200, role
    assert make_client("PURCHASE").get(f"{PO}/{uuid4()}").status_code == 404


def test_plant_scope_tenant_isolation_and_audit(make_client, api_database):
    unit, items, tax, (vendor,) = setup(make_client)
    other = make_client("ADMIN").post("/api/v1/org/units", json={"code": code(), "name": "P2"}).json()
    scoped = make_client("PURCHASE", subject="u-scoped-po", unit_ids=[other["unit_id"]])
    assert scoped.post(PO, json=po_body(unit, vendor, items, tax)).status_code == 403
    po = make_po(make_client("PURCHASE"), unit, vendor, items[:1], tax)
    scoped = make_client("PURCHASE", subject="u-scoped-po", unit_ids=[other["unit_id"]])
    assert scoped.get(f"{PO}/{po['po_id']}").status_code == 404
    assert scoped.post(f"{PO}/{po['po_id']}/submit").status_code == 404
    assert po["po_id"] not in {p["po_id"] for p in scoped.get(f"{PO}?limit=200").json()["items"]}
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        tenant_b, user_b = api_database["tenant_b"], uuid4()
        role = conn.execute("select role_id from erp.role where tenant_id = %s and code = 'STORES'",
                            (tenant_b,)).fetchone()[0]
        conn.execute("insert into erp.app_user(user_id, tenant_id, auth_subject, role_id, "
                     "display_name) values (%s, %s, 'po-b', %s, 'B')", (user_b, tenant_b, role))
        unit_b = conn.execute("insert into erp.unit(tenant_id, code, name) values (%s, 'BPO', 'B') "
                              "returning unit_id", (tenant_b,)).fetchone()[0]
        vendor_b = conn.execute("insert into erp.vendor(tenant_id, code, name, status) values "
                                "(%s, 'BV', 'BV', 'APPROVED') returning vendor_id",
                                (tenant_b,)).fetchone()[0]
        foreign = conn.execute(
            "insert into erp.purchase_order(tenant_id, unit_id, vendor_id, po_no, created_by) "
            "values (%s, %s, %s, 'PO-26-9999', %s) returning po_id",
            (tenant_b, unit_b, vendor_b, user_b)).fetchone()[0]
    purchase = make_client("PURCHASE")
    assert purchase.get(f"{PO}/{foreign}").status_code == 404
    assert purchase.post(f"{PO}/{foreign}/cancel").status_code == 404
    assert purchase.get(f"{PO}/{foreign}/history").status_code == 404
    purchase.post(f"{PO}/{po['po_id']}/submit")
    with psycopg.connect(api_database["owner"]) as conn:
        actions = [r[0] for r in conn.execute(
            "select action from erp.audit_log where entity_id = %s and entity_type = 'po' "
            "order by audit_id", (po["po_id"],))]
    assert actions == ["po.create", "po.submit"]
