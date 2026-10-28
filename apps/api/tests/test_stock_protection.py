"""Cross-module guards: stock that must not leave can leave by no road, and the last stock cannot
be promised twice.

Every road that takes finished or raw stock out of a store (stock issue, transfer, challan, sales
dispatch) must refuse a lot that is under QA hold, quarantined, blocked, still waiting for QA, or
expired. When a new road is added (for example job-work receipts or customer returns), add it to
`ROADS` so it is held to the same rule.
"""

import asyncio
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import HTTPException
from sqlalchemy import text

from heyyerp_api import db
from heyyerp_api.masters.common import Ctx
from heyyerp_api.rbac.seed import create_app_user
from heyyerp_api.sales import dispatch as dispatch_module
from heyyerp_api.sales.dispatch import DispatchIn, LineIn
from heyyerp_api.security import Principal, Profile

from .conftest import API_DB, API_PASSWORD, API_ROLE, TENANT_A
from .dbutil import url_for
from .test_compound_batches import Fresh, reconciles
from .test_dispatch_invoice import create, fg_lot, on_hand, sales_world
from .test_inventory_receipt import INV
from .test_inventory_transfer_count import new_store, transfer
from .test_sales_orders import S, order

pytestmark = pytest.mark.usefixtures("api_database")


# ------------------------------------------------------------------ the roads out of a store
def road_issue(make_client, w, lot):
    return Fresh(make_client, "STORES").post(f"{INV}/issues", json={
        "store_id": w["store"]["store_id"], "purpose": "Guard check",
        "lines": [{"item_id": w["item"]["item_id"], "quantity": "5"}]})


def road_transfer(make_client, w, lot):
    dst = new_store(make_client, w["unit"]["unit_id"])["store_id"]
    made = transfer(make_client, w["store"]["store_id"], dst, lot, "5")
    assert made.status_code == 201, made.text
    return Fresh(make_client, "STORES").post(
        f"{INV}/transfers/{made.json()['transfer_id']}/dispatch")


def road_challan(make_client, w, lot):
    return Fresh(make_client, "SALES").post(f"{S}/challans", json={
        "unit_id": w["unit"]["unit_id"], "store_id": w["store"]["store_id"],
        "challan_type": "FOC", "reason": "Guard check",
        "lines": [{"item_id": w["item"]["item_id"], "quantity": "5"}]})


def road_dispatch(make_client, w, lot):
    return create(make_client, w, "5")


ROADS = {"issue": road_issue, "transfer": road_transfer, "challan": road_challan,
         "dispatch": road_dispatch}
BAD_STATES = {
    "quarantined": "status = 'QUARANTINE'",
    "blocked": "status = 'BLOCKED'",
    "waiting for QA": "status = 'QA_HOLD'",
    "expired": "expiry_at = now() - interval '1 day'",
}


def set_lot(api_database, lot, assignment):
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute(f"update erp.lot set {assignment} where lot_id = %s", (lot["lot_id"],))


@pytest.mark.parametrize("state", list(BAD_STATES))
def test_no_road_takes_a_lot_that_is_not_fit_to_leave(make_client, api_database, state):
    w = sales_world(make_client, api_database)
    for name, road in ROADS.items():
        lot = fg_lot(api_database, w, w["store"], "50", days_old=3)
        set_lot(api_database, lot, BAD_STATES[state])
        refused = road(make_client, w, lot)
        assert refused.status_code == 409, f"{name} let a {state} lot go: {refused.text}"
        assert on_hand(make_client, w).get(lot["lot_no"], (0.0, 0.0))[1] == 0.0, name  # none held


def test_a_fit_lot_leaves_by_every_road(make_client, api_database):
    w = sales_world(make_client, api_database)
    for name, road in ROADS.items():
        lot = fg_lot(api_database, w, w["store"], "50")
        done = road(make_client, w, lot)
        assert done.status_code in (200, 201), f"{name} refused a good lot: {done.text}"


def test_a_qa_hold_closes_every_road_and_a_release_reopens_them(make_client, api_database):
    w = sales_world(make_client, api_database)
    lot = fg_lot(api_database, w, w["store"], "500")
    qa = Fresh(make_client, "QA")
    hold = qa.post("/api/v1/quality/holds", json={
        "subject_type": "STOCK_LOT", "subject_id": lot["lot_id"], "reason": "Complaint"}).json()
    for name, road in ROADS.items():
        assert road(make_client, w, lot).status_code == 409, name
    released = qa.post(f"/api/v1/quality/holds/{hold['hold_id']}/release", json={
        "note": "Cleared after re-inspection"})
    assert released.status_code == 200
    for name, road in ROADS.items():
        assert road(make_client, w, lot).status_code in (200, 201), name
    reconciles(make_client)


# ------------------------------------------------------------------ the last stock is promised once
def run_dispatches(w, requests):
    """Create several dispatches at the same moment, each in its own transaction."""
    users = []
    with psycopg.connect(url_for(API_DB), autocommit=True) as owner:
        for n in range(len(requests)):
            uid = create_app_user(owner, TENANT_A, f"disp-{uuid4().hex[:6]}-{n}", "SALES", f"D{n}")
            users.append(Profile(uid, f"D{n}", "SALES", frozenset(), frozenset(), None))

    async def attempt(profile, so_id, line_id, quantity):
        async with db.SessionFactory() as session, session.begin():
            await session.execute(text("select set_config('app.tenant_id', :t, true)"),
                                  {"t": TENANT_A})
            ctx = Ctx(session, Principal("u", "e", True, "o", True, UUID(TENANT_A), profile))
            body = DispatchIn(so_id=UUID(so_id), store_id=UUID(w["store"]["store_id"]),
                              lines=[LineIn(so_line_id=UUID(line_id), quantity=quantity)])
            try:
                await dispatch_module.create_dispatch(body, ctx)
                return "ok"
            except HTTPException as exc:
                return exc.status_code

    async def race():
        db.configure_database(url_for(API_DB, API_ROLE, API_PASSWORD).replace(
            "postgresql://", "postgresql+asyncpg://"))
        try:
            return await asyncio.gather(*(attempt(u, *r) for u, r in zip(users, requests,
                                                                         strict=True)))
        finally:
            await db.engine.dispose()

    return asyncio.run(race())


def test_two_orders_cannot_both_take_the_last_stock(make_client, api_database):
    w = sales_world(make_client, api_database, ordered="100")
    fg_lot(api_database, w, w["store"], "100")
    second = order(make_client, w, customer_po_no="PO-RACE-2", lines=[{
        "item_id": w["item"]["item_id"], "ordered_qty": "100", "unit_price": "200"}]).json()
    results = run_dispatches(w, [
        (w["so"]["so_id"], w["line"]["so_line_id"], 60),
        (second["so_id"], second["lines"][0]["so_line_id"], 60)])
    assert sorted(map(str, results)) == ["409", "ok"], results
    assert sum(held for _, held in on_hand(make_client, w).values()) == 60
    reconciles(make_client)


def test_one_order_cannot_be_over_dispatched_by_parallel_requests(make_client, api_database):
    w = sales_world(make_client, api_database, ordered="100")
    fg_lot(api_database, w, w["store"], "500")
    results = run_dispatches(w, [(w["so"]["so_id"], w["line"]["so_line_id"], 30)] * 6)
    assert results.count("ok") == 3 and results.count(409) == 3, results  # 90 of 100 fit
    drafts = Fresh(make_client, "SALES").get(
        f"{S}/dispatches?so_id={w['so']['so_id']}&status=draft").json()
    assert drafts["total"] == 3
    assert sum(held for _, held in on_hand(make_client, w).values()) == 90
    for d in drafts["items"]:
        Fresh(make_client, "SALES").post(f"{S}/dispatches/{d['dispatch_id']}/cancel")
    assert sum(held for _, held in on_hand(make_client, w).values()) == 0
