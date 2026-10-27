"""Issuing stock (FEFO / FIFO), blocked expired lots, reservations and concurrent issues."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import HTTPException
from sqlalchemy import text

from heyyerp_api import db
from heyyerp_api.inventory import issues
from heyyerp_api.inventory.issues import IssueIn, LineIn, choose
from heyyerp_api.masters.common import Ctx
from heyyerp_api.rbac.seed import create_app_user
from heyyerp_api.security import Principal, Profile

from .conftest import API_DB, API_PASSWORD, API_ROLE, TENANT_A
from .dbutil import url_for
from .test_inventory_receipt import INV, PO, SOON, inspect, reconciles, world

pytestmark = pytest.mark.usefixtures("api_database")
NOW = datetime.now(UTC)


def lot(no, expiry_days=None, received_days=0, available=10, status="AVAILABLE", expired=False):
    return {"lot_id": uuid4(), "lot_no": no, "status": status, "available": Decimal(available),
            "expiry_at": NOW + timedelta(days=expiry_days) if expiry_days is not None else None,
            "received_at": NOW - timedelta(days=received_days), "expired": expired}


# ------------------------------------------------------------------ the pure choice of lots
def test_fefo_takes_the_earliest_expiry_then_the_oldest_receipt_and_never_expired():
    lots = [lot("late", 90, received_days=30), lot("soon", 10, received_days=5),
            lot("none", None, received_days=60),  # no expiry goes last
            lot("tie-new", 10, received_days=1), lot("gone", -3, received_days=90, expired=True)]
    picks, short, blocked = choose(lots, Decimal(25), "FEFO")
    assert [(p["lot_no"], p["take"]) for p in picks] == [
        ("soon", 10), ("tie-new", 10), ("late", 5)]
    assert short == 0 and [b["lot_no"] for b in blocked] == ["gone"]  # expired is listed, not used


def test_fifo_rule_takes_the_oldest_receipt_regardless_of_expiry():
    lots = [lot("new", 5, received_days=1), lot("old", 90, received_days=50)]
    picks, _, _ = choose(lots, Decimal(15), "FIFO")
    assert [(p["lot_no"], p["take"]) for p in picks] == [("old", 10), ("new", 5)]


def test_unavailable_lots_are_never_chosen_and_the_shortfall_is_reported():
    lots = [lot("held", 10, status="QA_HOLD"), lot("bad", 10, status="QUARANTINE"),
            lot("empty", 10, available=0), lot("ok", 20, available=4)]
    picks, short, blocked = choose(lots, Decimal(10), "FEFO")
    assert [p["lot_no"] for p in picks] == ["ok"] and short == Decimal(6) and blocked == []


# ------------------------------------------------------------------ helpers
def receive(make_client, w, qty, accept=None, shelf_days=None, expired=False, dc=None, api_database=None):
    r = make_client("STORES").post(f"{INV}/grns", json={
        "po_id": w["po"]["po_id"], "supplier_dc_no": dc or f"DC-{uuid4().hex[:6]}",
        "lines": [{"po_line_id": w["po"]["lines"][0]["po_line_id"],
                   "store_id": w["store"]["store_id"], "received_qty": qty}]})
    assert r.status_code == 201, r.text
    line = r.json()["lines"][0]
    done = inspect(make_client("QA"), line, accept or qty)
    assert done.status_code == 200, done.text
    return line


def set_lot(api_database, lot_id, **cols):
    sets = ", ".join(f"{k} = %s" for k in cols)
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute(f"update erp.lot set {sets} where lot_id = %s", (*cols.values(), lot_id))


def issue_body(w, qty, **extra):
    return {"store_id": w["store"]["store_id"], "purpose": "Production batch",
            "lines": [{"item_id": w["item"]["item_id"], "quantity": qty}], **extra}


def plan_body(w, qty):
    return {k: v for k, v in issue_body(w, qty).items() if k != "purpose"}


def stock(make_client, w):
    return {r["lot_no"]: r for r in make_client("STORES").get(
        f"{INV}/stock?item_id={w['item']['item_id']}&in_stock_only=false").json()["items"]}


# ------------------------------------------------------------------ issuing through the API
def test_issue_takes_fefo_lots_and_costs_them_fifo(make_client, api_database):
    w = world(make_client, qty="300", price="10")
    first = receive(make_client, w, "100")  # cost 10, expires later
    second = receive(make_client, w, "100")  # cost 10, expires sooner
    set_lot(api_database, second["lot_id"], expiry_at=NOW + timedelta(days=20))
    set_lot(api_database, first["lot_id"], expiry_at=NOW + timedelta(days=90))
    plan = make_client("STORES").post(f"{INV}/issues/plan", json=plan_body(w, "150")).json()
    assert plan["rule"] == "FEFO" and plan["can_issue"] is True
    assert [(p["lot_no"], float(p["quantity"])) for p in plan["lines"][0]["picks"]] == [
        (second["lot_no"], 100.0), (first["lot_no"], 50.0)]
    done = make_client("STORES").post(f"{INV}/issues", json=issue_body(w, "150", reference="BATCH-7"))
    assert done.status_code == 201, done.text
    out = done.json()
    assert out["issue_no"].startswith("ISS-") and out["reference"] == "BATCH-7"
    assert [(ln["lot_no"], float(ln["quantity"]), float(ln["cost"])) for ln in out["lines"]] == [
        (second["lot_no"], 100.0, 1000.0), (first["lot_no"], 50.0, 500.0)]
    assert float(out["total_cost"]) == 1500 and out["issued_by_name"] == "STORES user"
    left = stock(make_client, w)
    assert float(left[second["lot_no"]]["on_hand"]) == 0 and float(left[first["lot_no"]]["on_hand"]) == 50
    reconciles(make_client)


def test_fifo_valuation_uses_the_oldest_cost_first(make_client):
    w = world(make_client, qty="200", price="10")
    receive(make_client, w, "100")  # layer 1 at 10
    po = make_client("PURCHASE").get(f"{PO}/{w['po']['po_id']}").json()
    assert po["status"] == "PARTIALLY_RECEIVED"
    # a second order for the same item at a different price makes a second cost layer
    purchase = make_client("PURCHASE")
    other = purchase.post(PO, json={
        "unit_id": w["unit"]["unit_id"], "vendor_id": w["vendor"]["vendor_id"],
        "lines": [{"item_id": w["item"]["item_id"], "ordered_qty": "100", "unit_price": "20",
                   "tax_code_id": w["po"]["lines"][0]["tax_code_id"], "due_date": SOON}]}).json()
    other = purchase.post(f"{PO}/{other['po_id']}/submit").json()
    line = make_client("STORES").post(f"{INV}/grns", json={
        "po_id": other["po_id"], "supplier_dc_no": "DC-2", "lines": [{
            "po_line_id": other["lines"][0]["po_line_id"], "store_id": w["store"]["store_id"],
            "received_qty": "100"}]}).json()["lines"][0]
    inspect(make_client("QA"), line, "100")
    issued = make_client("STORES").post(f"{INV}/issues", json=issue_body(w, "150")).json()
    assert float(issued["total_cost"]) == 100 * 10 + 50 * 20  # oldest layer first
    value = make_client("ACCOUNTS").get(f"{INV}/valuation?unit_id={w['unit']['unit_id']}").json()[0]
    assert float(value["quantity"]) == 50 and float(value["value"]) == 1000
    reconciles(make_client)


def test_shortfall_refuses_the_whole_issue_and_changes_nothing(make_client):
    w = world(make_client, qty="100")
    receive(make_client, w, "100")
    stores = make_client("STORES")
    plan = stores.post(f"{INV}/issues/plan", json=plan_body(w, "130")).json()
    assert plan["can_issue"] is False and float(plan["lines"][0]["shortfall"]) == 30
    refused = stores.post(f"{INV}/issues", json=issue_body(w, "130"))
    assert refused.status_code == 409 and "Not enough stock" in refused.json()["detail"]
    assert stores.get(f"{INV}/issues").json()["total"] == 0 or all(
        i["store_id"] != w["store"]["store_id"] for i in stores.get(f"{INV}/issues").json()["items"])
    assert float(list(stock(make_client, w).values())[0]["on_hand"]) == 100
    reconciles(make_client)


def test_expired_lots_are_blocked_and_listed(make_client, api_database):
    w = world(make_client, qty="200")
    old = receive(make_client, w, "100")
    fresh = receive(make_client, w, "100")
    set_lot(api_database, old["lot_id"], received_at=NOW - timedelta(days=30),
            expiry_at=NOW - timedelta(days=1))
    stores = make_client("STORES")
    plan = stores.post(f"{INV}/issues/plan", json=plan_body(w, "100")).json()["lines"][0]
    assert [p["lot_no"] for p in plan["picks"]] == [fresh["lot_no"]]
    assert plan["blocked"][0]["lot_no"] == old["lot_no"] and plan["blocked"][0]["reason"] == "Expired"
    assert stores.post(f"{INV}/issues", json=issue_body(w, "100")).status_code == 201
    stuck = stores.post(f"{INV}/issues", json=issue_body(w, "1"))
    assert stuck.status_code == 409 and "expired and blocked" in stuck.json()["detail"]
    listed = make_client("STORES").get(f"{INV}/expiring?within_days=0").json()
    assert old["lot_no"] in {e["lot_no"] for e in listed}
    assert make_client("STORES").get(f"{INV}/expiring?within_days=0&include_expired=false").json() == []


def test_held_quarantined_and_rejected_stock_cannot_be_issued(make_client):
    w = world(make_client, qty="300")
    held = make_client("STORES").post(f"{INV}/grns", json={
        "po_id": w["po"]["po_id"], "supplier_dc_no": "DC-H", "lines": [{
            "po_line_id": w["po"]["lines"][0]["po_line_id"], "store_id": w["store"]["store_id"],
            "received_qty": "100"}]})
    assert held.status_code == 201  # still QA_HOLD
    stores = make_client("STORES")
    assert stores.post(f"{INV}/issues", json=issue_body(w, "1")).status_code == 409
    good = receive(make_client, w, "100")
    make_client("QA").post(f"{INV}/lots/{good['lot_id']}/quarantine", json={"reason": "Complaint"})
    assert make_client("STORES").post(f"{INV}/issues", json=issue_body(w, "1")).status_code == 409
    make_client("QA").post(f"{INV}/lots/{good['lot_id']}/unquarantine", json={"reason": "Cleared"})
    assert make_client("STORES").post(f"{INV}/issues", json=issue_body(w, "100")).status_code == 201


def test_issue_validation_and_permissions(make_client):
    w = world(make_client)
    receive(make_client, w, "100")
    stores = make_client("STORES")
    post = lambda **b: stores.post(f"{INV}/issues", json={**issue_body(w, "1"), **b}).status_code  # noqa: E731
    line = issue_body(w, "1")["lines"][0]
    assert post(lines=[]) == 422 and post(lines=[line, line]) == 422
    assert post(lines=[{**line, "quantity": "0"}]) == 422
    assert post(lines=[{"item_id": str(uuid4()), "quantity": "1"}]) == 422
    assert post(purpose="x") == 422 and post(store_id=str(uuid4())) == 404
    assert post(tenant_id=str(uuid4())) == 422
    for role in ("PURCHASE", "SALES", "QA", "ACCOUNTS", "OPERATOR"):
        assert make_client(role).post(f"{INV}/issues", json=issue_body(w, "1")).status_code == 403, role
    for role in ("STORES", "PROD", "PLANT_HEAD", "MD"):
        assert make_client(role).post(f"{INV}/issues/plan", json=plan_body(w, "1")).status_code == 200
    shut = make_client("ADMIN").patch(f"/api/v1/org/stores/{w['store']['store_id']}",
                                      json={"active": False})
    assert shut.status_code == 409  # still holds stock
    assert make_client("STORES").post(f"{INV}/issues", json=issue_body(w, "99")).status_code == 201
    assert make_client("STORES").post(f"{INV}/issues", json=issue_body(w, "1")).status_code == 201
    assert make_client("ADMIN").patch(f"/api/v1/org/stores/{w['store']['store_id']}",
                                      json={"active": False}).status_code == 200
    assert make_client("STORES").post(f"{INV}/issues", json=issue_body(w, "1")).status_code == 422


def test_issue_history_scope_and_audit(make_client, api_database):
    w = world(make_client)
    receive(make_client, w, "100")
    issued = make_client("STORES").post(f"{INV}/issues", json=issue_body(w, "10")).json()
    other = make_client("ADMIN").post("/api/v1/org/units", json={"code": "O" + uuid4().hex[:6], "name": "P2"}).json()
    scoped = make_client("STORES", subject="u-scoped-issue", unit_ids=[other["unit_id"]])
    assert scoped.post(f"{INV}/issues", json=issue_body(w, "1")).status_code == 404  # not their plant
    assert scoped.get(f"{INV}/issues/{issued['issue_id']}").status_code == 404
    stores = make_client("STORES")
    assert stores.get(f"{INV}/issues/{issued['issue_id']}").json()["issue_no"] == issued["issue_no"]
    assert issued["issue_id"] in {i["issue_id"] for i in stores.get(f"{INV}/issues?limit=200").json()["items"]}
    with psycopg.connect(api_database["owner"]) as conn:
        actions = [r[0] for r in conn.execute(
            "select action from erp.audit_log where entity_id = %s", (issued["issue_id"],))]
    assert actions == ["stock.issue"]


# ------------------------------------------------------------------ reservations
def test_reservations_hold_stock_and_can_be_used_or_released(make_client):
    w = world(make_client, qty="200")
    receive(make_client, w, "100")
    receive(make_client, w, "100")
    stores = make_client("STORES")
    held = stores.post(f"{INV}/reservations", json={
        "store_id": w["store"]["store_id"], "item_id": w["item"]["item_id"], "quantity": "150",
        "reference_type": "WORK_ORDER", "reference": "WO-1"})
    assert held.status_code == 201, held.text
    assert sorted(float(r["quantity"]) for r in held.json()) == [50.0, 100.0]
    rows_ = list(stock(make_client, w).values())
    assert sum(float(r["reserved"]) for r in rows_) == 150 and sum(float(r["available"]) for r in rows_) == 50
    stores = make_client("STORES")
    assert stores.post(f"{INV}/reservations", json={  # only 50 free now
        "store_id": w["store"]["store_id"], "item_id": w["item"]["item_id"], "quantity": "60",
        "reference_type": "WORK_ORDER", "reference": "WO-2"}).status_code == 409
    assert stores.post(f"{INV}/issues", json=issue_body(w, "60")).status_code == 409  # held stock
    assert stores.post(f"{INV}/issues", json=issue_body(w, "50")).status_code == 201
    reconciles(make_client)
    using = make_client("STORES").post(f"{INV}/issues", json=issue_body(
        w, "100", use_reservation={"reference_type": "WORK_ORDER", "reference": "WO-1"}))
    assert using.status_code == 201, using.text
    left = make_client("STORES").get(f"{INV}/reservations?reference=WO-1").json()
    assert sum(float(r["quantity"]) for r in left) == 50  # 100 of the 150 were issued
    assert make_client("STORES").post(f"{INV}/reservations/release", json={
        "reference_type": "WORK_ORDER", "reference": "WO-1"}).json() == {"released": len(left)}
    assert all(float(r["reserved"]) == 0 for r in stock(make_client, w).values())
    assert make_client("STORES").get(f"{INV}/reservations?reference=WO-1").json() == []
    reconciles(make_client)


def test_releasing_one_reservation(make_client):
    w = world(make_client)
    receive(make_client, w, "100")
    stores = make_client("STORES")
    held = stores.post(f"{INV}/reservations", json={
        "store_id": w["store"]["store_id"], "item_id": w["item"]["item_id"], "quantity": "30",
        "reference_type": "SALES_ORDER", "reference": "SO-9"}).json()[0]
    assert stores.delete(f"{INV}/reservations/{held['reservation_id']}").status_code == 204
    assert stores.delete(f"{INV}/reservations/{held['reservation_id']}").status_code == 404
    assert stores.get(f"{INV}/reservations?reference=SO-9&status=released").json()[0][
        "reservation_id"] == held["reservation_id"]
    assert make_client("SALES").post(f"{INV}/reservations", json={
        "store_id": w["store"]["store_id"], "item_id": w["item"]["item_id"], "quantity": "1",
        "reference_type": "OTHER", "reference": "x"}).status_code == 403
    reconciles(make_client)


# ------------------------------------------------------------------ concurrency
def run_concurrently(w, requests):
    """Fire several issues at the same moment, each in its own transaction, as separate users."""
    users = []
    with psycopg.connect(url_for(API_DB), autocommit=True) as owner:
        for n in range(len(requests)):
            uid = create_app_user(owner, TENANT_A, f"racer-issue-{n}", "STORES", f"Racer {n}")
            users.append(Profile(uid, f"Racer {n}", "STORES", frozenset(), frozenset(), None))

    async def attempt(profile, quantity):
        async with db.SessionFactory() as session, session.begin():
            await session.execute(text("select set_config('app.tenant_id', :t, true)"),
                                  {"t": TENANT_A})
            ctx = Ctx(session, Principal("u", "e", True, "o", True, UUID(TENANT_A), profile))
            body = IssueIn(store_id=UUID(w["store"]["store_id"]), purpose="Race",
                           lines=[LineIn(item_id=UUID(w["item"]["item_id"]), quantity=quantity)])
            try:
                await issues.issue(ctx, body)
                return "ok"
            except HTTPException as exc:
                return exc.status_code

    async def race():
        db.configure_database(url_for(API_DB, API_ROLE, API_PASSWORD).replace(
            "postgresql://", "postgresql+asyncpg://"))
        try:
            return await asyncio.gather(*(attempt(u, q) for u, q in zip(users, requests, strict=True)))
        finally:
            await db.engine.dispose()

    return asyncio.run(race())


def test_two_issues_for_the_last_stock_cannot_both_succeed(make_client):
    w = world(make_client, qty="100")
    receive(make_client, w, "100")
    results = run_concurrently(w, [Decimal(60), Decimal(60)])
    assert sorted(map(str, results)) == ["409", "ok"], results
    assert float(list(stock(make_client, w).values())[0]["on_hand"]) == 40
    reconciles(make_client)


def test_many_concurrent_issues_never_overdraw_and_always_reconcile(make_client):
    w = world(make_client, qty="100")
    receive(make_client, w, "100")
    results = run_concurrently(w, [Decimal(15)] * 10)
    assert results.count("ok") == 6 and results.count(409) == 4, results  # 90 issued, 10 left
    assert float(list(stock(make_client, w).values())[0]["on_hand"]) == 10
    reconciles(make_client)  # ledger, balance and FIFO layers still agree
