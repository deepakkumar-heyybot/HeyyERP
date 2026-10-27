"""RFQs: invitations, quotes, the comparison sheet and the minimum-quotes rule."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from heyyerp_api.procurement.rfq import build_comparison

R = "/api/v1/procurement/rfqs"
pytestmark = pytest.mark.usefixtures("api_database")
TODAY = date.today()


def code() -> str:
    return "Q" + uuid4().hex[:9].upper()


# ------------------------------------------------------------------ comparison logic (pure)
def test_comparison_marks_the_lowest_valid_full_quantity_offer():
    line1, line2 = uuid4(), uuid4()
    a, b, c = uuid4(), uuid4(), uuid4()
    wanted = [{"rfq_line_id": line1, "line_no": 1, "item_code": "X", "quantity": Decimal(100)},
              {"rfq_line_id": line2, "line_no": 2, "item_code": "Y", "quantity": Decimal(10)}]

    def offer(vendor, code_, line, price, qty, lead=5, valid=None):
        return {"vendor_id": vendor, "vendor_code": code_, "vendor_name": code_.title(),
                "rfq_line_id": line, "unit_price": Decimal(price), "quoted_qty": Decimal(qty),
                "lead_days": lead, "valid_until": valid}

    offers = [
        offer(a, "A", line1, "10", 100), offer(a, "A", line2, "50", 10),
        offer(b, "B", line1, "9", 60),  # cheapest per unit but cannot supply 100
        offer(b, "B", line2, "45", 10, lead=9),
        offer(c, "C", line1, "8", 100, valid=TODAY - timedelta(days=1)),  # expired
        offer(c, "C", line2, "40", 10),
    ]
    lines, vendors, cheapest = build_comparison(wanted, offers, TODAY)
    low = {ln.item_code: [o.vendor_code for o in ln.offers if o.is_lowest] for ln in lines}
    assert low == {"X": ["A"], "Y": ["C"]}  # B is short, C's line-1 offer expired
    assert [o.vendor_code for o in lines[0].offers] == ["C", "B", "A"]  # cheapest price first
    totals = {v.vendor_code: v for v in vendors}
    assert totals["A"].total == Decimal("1500") and totals["A"].covers_all_lines
    assert totals["B"].covers_all_lines is False and totals["C"].has_expired_offer
    assert cheapest == a  # B is short, C has an expired offer
    assert totals["B"].max_lead_days == 9


def test_comparison_with_no_complete_bidder_names_nobody():
    line = uuid4()
    wanted = [{"rfq_line_id": line, "line_no": 1, "item_code": "X", "quantity": Decimal(5)}]
    assert build_comparison(wanted, [], TODAY)[2] is None
    short = [{"vendor_id": uuid4(), "vendor_code": "A", "vendor_name": "A", "rfq_line_id": line,
              "unit_price": Decimal(1), "quoted_qty": Decimal(2), "lead_days": None,
              "valid_until": None}]
    assert build_comparison(wanted, short, TODAY)[2] is None


# ------------------------------------------------------------------ helpers
def make_vendors(make_client, n):
    """n APPROVED vendors (created by PURCHASE, approved by the Plant Head)."""
    created = []
    for _ in range(n):
        purchase = make_client("PURCHASE")
        v = purchase.post("/api/v1/parties/vendors", json={"code": code(), "name": "V"}).json()
        approval = purchase.post("/api/v1/approvals", json={
            "document_type": "vendor", "document_id": v["vendor_id"]}).json()["approval_id"]
        make_client("PLANT_HEAD").post(f"/api/v1/approvals/{approval}/decide",
                                       json={"decision": "APPROVE"})
        created.append(v)
    return created


def setup(make_client, vendors=2, items=2):
    admin = make_client("ADMIN")
    unit = admin.post("/api/v1/org/units", json={"code": code(), "name": "Plant"}).json()
    its = [admin.post("/api/v1/product/items", json={
        "code": code(), "name": "RM", "item_type": "RM", "uom_code": "KG"}).json()
        for _ in range(items)]
    return unit, its, make_vendors(make_client, vendors)


def rfq_body(unit, items, vendors=(), **extra):
    return {"unit_id": unit["unit_id"],
            "lines": [{"item_id": i["item_id"], "quantity": "100"} for i in items],
            "vendor_ids": [v["vendor_id"] for v in vendors], **extra}


def quote(vendor, rfq, prices, valid_until=None, qty="100", lead=7, ref=None):
    return {"vendor_id": vendor["vendor_id"], "valid_until": valid_until, "quote_reference": ref,
            "lines": [{"rfq_line_id": ln["rfq_line_id"], "unit_price": price, "quoted_qty": qty,
                       "lead_days": lead} for ln, price in zip(rfq["lines"], prices, strict=True)]}


# ------------------------------------------------------------------ lifecycle
def test_create_invite_send_and_quote(make_client):
    unit, items, vendors = setup(make_client)
    purchase = make_client("PURCHASE")
    rfq = purchase.post(R, json=rfq_body(unit, items, vendors))
    assert rfq.status_code == 201, rfq.text
    rfq = rfq.json()
    assert rfq["status"] == "DRAFT" and rfq["rfq_no"].startswith("RFQ-")
    assert [i["response_status"] for i in rfq["invitations"]] == ["INVITED", "INVITED"]
    assert [ln["line_no"] for ln in rfq["lines"]] == [1, 2]
    early = quote(vendors[0], rfq, ["10", "20"])
    assert purchase.post(f"{R}/{rfq['rfq_id']}/quotes", json=early).status_code == 409  # not sent
    sent = purchase.post(f"{R}/{rfq['rfq_id']}/send")
    assert sent.status_code == 200 and sent.json()["status"] == "SENT"
    assert purchase.post(f"{R}/{rfq['rfq_id']}/send").status_code == 409
    q = purchase.post(f"{R}/{rfq['rfq_id']}/quotes", json=quote(vendors[0], rfq, ["10", "20"]))
    assert q.status_code == 201
    status = {i["vendor_code"]: i["response_status"] for i in q.json()["invitations"]}
    assert status[vendors[0]["code"]] == "QUOTED" and status[vendors[1]["code"]] == "INVITED"


def test_only_approved_vendors_can_be_invited(make_client):
    unit, items, (good,) = setup(make_client, vendors=1)
    purchase = make_client("PURCHASE")
    pending = purchase.post("/api/v1/parties/vendors", json={"code": code(), "name": "P"}).json()
    refused = purchase.post(R, json=rfq_body(unit, items, [pending]))
    assert refused.status_code == 422 and "approved" in refused.json()["detail"]
    rfq = purchase.post(R, json=rfq_body(unit, items, [good])).json()
    assert purchase.post(f"{R}/{rfq['rfq_id']}/vendors", json={
        "vendor_ids": [pending["vendor_id"]]}).status_code == 422
    assert purchase.post(f"{R}/{rfq['rfq_id']}/vendors", json={
        "vendor_ids": [str(uuid4())]}).status_code == 422
    again = purchase.post(f"{R}/{rfq['rfq_id']}/vendors", json={"vendor_ids": [good["vendor_id"]]})
    assert again.status_code == 200 and len(again.json()["invitations"]) == 1  # no duplicate


def test_rfq_validation(make_client):
    unit, items, vendors = setup(make_client, vendors=1, items=2)
    purchase = make_client("PURCHASE")
    bad = lambda **b: purchase.post(R, json={**rfq_body(unit, items), **b}).status_code  # noqa: E731
    assert bad(lines=[]) == 422
    one = {"item_id": items[0]["item_id"], "quantity": "5"}
    assert bad(lines=[one, one]) == 422
    assert bad(lines=[{**one, "quantity": "0"}]) == 422
    assert bad(lines=[{**one, "required_by": (TODAY - timedelta(days=1)).isoformat()}]) == 422
    assert bad(lines=[{"item_id": str(uuid4()), "quantity": "1"}]) == 422
    assert bad(unit_id=str(uuid4())) == 422  # no such plant
    draft = purchase.post(R, json=rfq_body(unit, items)).json()
    assert purchase.post(f"{R}/{draft['rfq_id']}/send").status_code == 422  # no vendor invited
    edited = purchase.put(f"{R}/{draft['rfq_id']}", json={"lines": [one]})
    assert edited.status_code == 200 and len(edited.json()["lines"]) == 1
    purchase.post(f"{R}/{draft['rfq_id']}/vendors", json={"vendor_ids": [vendors[0]["vendor_id"]]})
    purchase.post(f"{R}/{draft['rfq_id']}/send")
    assert purchase.put(f"{R}/{draft['rfq_id']}", json={"lines": [one]}).status_code == 409


def test_quote_validation_and_replacement(make_client):
    unit, items, (v1, v2) = setup(make_client)
    purchase = make_client("PURCHASE")
    rfq = purchase.post(R, json=rfq_body(unit, items, [v1])).json()
    other_rfq = purchase.post(R, json=rfq_body(unit, items, [v1])).json()
    purchase.post(f"{R}/{rfq['rfq_id']}/send")
    url = f"{R}/{rfq['rfq_id']}/quotes"
    assert purchase.post(url, json=quote(v2, rfq, ["1", "1"])).status_code == 422  # not invited
    expired = quote(v1, rfq, ["1", "1"], valid_until=(TODAY - timedelta(days=1)).isoformat())
    assert purchase.post(url, json=expired).status_code == 422
    foreign_line = quote(v1, other_rfq, ["1", "1"])
    assert purchase.post(url, json=foreign_line).status_code == 422  # lines of another RFQ
    dup = quote(v1, rfq, ["1", "1"])
    dup["lines"][1] = dup["lines"][0]
    assert purchase.post(url, json=dup).status_code == 422
    assert purchase.post(url, json=quote(v1, rfq, ["-1", "1"])).status_code == 422
    assert purchase.post(url, json=quote(v1, rfq, ["1", "1"], qty="0")).status_code == 422
    assert purchase.post(url, json=quote(v1, rfq, ["12", "22"], ref="Q-1")).status_code == 201
    assert purchase.post(url, json=quote(v1, rfq, ["11", "21"], ref="Q-2")).status_code == 201
    sheet = purchase.get(f"{R}/{rfq['rfq_id']}/comparison").json()
    assert [float(o["unit_price"]) for o in sheet["lines"][0]["offers"]] == [11.0]  # latest only
    invited = purchase.get(f"{R}/{rfq['rfq_id']}").json()["invitations"][0]
    assert invited["quotes_received"] == 2  # the replaced one is kept for the record


def test_comparison_sheet_over_the_api(make_client):
    unit, items, (v1, v2, v3) = setup(make_client, vendors=3)
    purchase = make_client("PURCHASE")
    rfq = purchase.post(R, json=rfq_body(unit, items, [v1, v2, v3])).json()
    purchase.post(f"{R}/{rfq['rfq_id']}/send")
    url = f"{R}/{rfq['rfq_id']}/quotes"
    purchase.post(url, json=quote(v1, rfq, ["10", "20"], lead=5))
    purchase.post(url, json=quote(v2, rfq, ["9", "22"], lead=10))
    purchase.post(url, json=quote(v3, rfq, ["9.50", "19"], qty="50"))  # cannot supply 100
    sheet = purchase.get(f"{R}/{rfq['rfq_id']}/comparison").json()
    lowest = [[o["vendor_code"] for o in ln["offers"] if o["is_lowest"]] for ln in sheet["lines"]]
    assert lowest == [[v2["code"]], [v1["code"]]]  # v3's lower prices do not cover the quantity
    totals = {v["vendor_code"]: v for v in sheet["vendors"]}
    assert float(totals[v1["code"]]["total"]) == 3000 and float(totals[v2["code"]]["total"]) == 3100
    assert totals[v3["code"]]["covers_all_lines"] is False
    assert sheet["cheapest_vendor_id"] == v1["vendor_id"]  # lowest complete total
    assert make_client("PURCHASE").get(f"{R}/{uuid4()}/comparison").status_code == 404


# ------------------------------------------------------------------ rules, permissions, isolation
def test_minimum_quotes_rule_decides_how_many_vendors_must_be_invited(make_client):
    unit, items, (v1, v2) = setup(make_client)
    admin = make_client("ADMIN")
    admin.put("/api/v1/admin/settings/minQuotes", json={"value": 2})
    try:
        purchase = make_client("PURCHASE")
        rfq = purchase.post(R, json=rfq_body(unit, items, [v1])).json()
        refused = purchase.post(f"{R}/{rfq['rfq_id']}/send")
        assert refused.status_code == 422 and "minQuotes" in refused.json()["detail"]
        purchase.post(f"{R}/{rfq['rfq_id']}/vendors", json={"vendor_ids": [v2["vendor_id"]]})
        assert purchase.post(f"{R}/{rfq['rfq_id']}/send").status_code == 200
    finally:
        make_client("ADMIN").put("/api/v1/admin/settings/minQuotes", json={"value": None})


def test_permissions_and_visibility(make_client):
    unit, items, vendors = setup(make_client, vendors=1)
    purchase = make_client("PURCHASE")
    rfq = purchase.post(R, json=rfq_body(unit, items, vendors)).json()
    for role in ("STORES", "QA", "SALES", "OPERATOR", "PROD"):
        client = make_client(role)
        assert client.post(R, json=rfq_body(unit, items, vendors)).status_code == 403, role
        assert client.post(f"{R}/{rfq['rfq_id']}/send").status_code == 403, role
    for role in ("OPERATOR", "PROD"):  # no procurement module
        assert make_client(role).get(f"{R}/{rfq['rfq_id']}").status_code == 403, role
        assert make_client(role).get(R).status_code == 403, role
    for role in ("PURCHASE", "PLANT_HEAD", "ACCOUNTS", "STORES"):
        assert make_client(role).get(f"{R}/{rfq['rfq_id']}").status_code == 200, role
    cancelled = make_client("PURCHASE").post(f"{R}/{rfq['rfq_id']}/cancel")
    assert cancelled.json()["status"] == "CANCELLED"
    assert make_client("PURCHASE").post(f"{R}/{rfq['rfq_id']}/cancel").status_code == 409


def test_plant_scope_tenant_isolation_and_audit(make_client, api_database):
    unit, items, vendors = setup(make_client, vendors=1)
    other = make_client("ADMIN").post("/api/v1/org/units", json={"code": code(), "name": "P2"}).json()
    scoped = make_client("PURCHASE", subject="u-scoped-purchase", unit_ids=[other["unit_id"]])
    assert scoped.post(R, json=rfq_body(unit, items, vendors)).status_code == 403
    purchase = make_client("PURCHASE")
    rfq = purchase.post(R, json=rfq_body(unit, items, vendors)).json()
    scoped = make_client("PURCHASE", subject="u-scoped-purchase", unit_ids=[other["unit_id"]])
    assert scoped.get(f"{R}/{rfq['rfq_id']}").status_code == 404
    assert rfq["rfq_id"] not in {r["rfq_id"] for r in scoped.get(f"{R}?limit=200").json()["items"]}
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        tenant_b, user_b = api_database["tenant_b"], uuid4()
        role = conn.execute("select role_id from erp.role where tenant_id = %s and code = 'STORES'",
                            (tenant_b,)).fetchone()[0]
        conn.execute("insert into erp.app_user(user_id, tenant_id, auth_subject, role_id, "
                     "display_name) values (%s, %s, 'rfq-b', %s, 'B')", (user_b, tenant_b, role))
        unit_b = conn.execute("insert into erp.unit(tenant_id, code, name) values (%s, 'BQ', 'B') "
                              "returning unit_id", (tenant_b,)).fetchone()[0]
        foreign = conn.execute(
            "insert into erp.rfq(tenant_id, unit_id, rfq_no, created_by) values (%s, %s, "
            "'RFQ-26-9999', %s) returning rfq_id", (tenant_b, unit_b, user_b)).fetchone()[0]
    purchase = make_client("PURCHASE")
    assert purchase.get(f"{R}/{foreign}").status_code == 404
    assert purchase.post(f"{R}/{foreign}/send").status_code == 404
    purchase.post(f"{R}/{rfq['rfq_id']}/send")
    with psycopg.connect(api_database["owner"]) as conn:
        actions = [r[0] for r in conn.execute(
            "select action from erp.audit_log where entity_id = %s order by audit_id",
            (rfq["rfq_id"],))]
    assert actions == ["rfq.create", "rfq.send"]
