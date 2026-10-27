"""Shift entries, downtime, OEE, rework, returned material, WIP and the lot reconciliation."""

from datetime import date, timedelta

import psycopg
import pytest

from .test_compound_batches import Fresh, M, reconciles
from .test_inventory_receipt import code
from .test_work_orders import run_route, shop, start, stock_on_hand, work_order

pytestmark = pytest.mark.usefixtures("api_database")
TODAY = date.today()


def machine(make_client, w, name="PR"):
    made = Fresh(make_client, "PROD").post(f"{M}/machines", json={
        "unit_id": w["unit"]["unit_id"], "code": name + code()[:4], "name": "Press"})
    assert made.status_code == 201, made.text
    return made.json()


def entry(make_client, w, wo, mc, day=TODAY, shift="A", good="100", role="PROD", **extra):
    return Fresh(make_client, role).post(f"{M}/shift-entries", json={
        "work_order_id": wo["work_order_id"], "machine_id": mc["machine_id"],
        "work_date": day.isoformat(), "shift_code": shift, "good_qty": good, **extra})


def lot_with_rejects(make_client, w, wo, qty="50", rejects="5"):
    lot = start(make_client, w, wo, qty).json()
    run_route(make_client, lot["prod_lot_id"], stop_at="QA_MOULDING")
    passed = Fresh(make_client, "QA").post(f"{M}/lots/{lot['prod_lot_id']}/move",
                                           json={"rejects": rejects})
    assert passed.status_code == 200, passed.text
    return lot["prod_lot_id"]


# ------------------------------------------------------------------ shift entries
def test_shift_entry_totals_come_from_the_reasons(make_client, api_database):
    w = shop(make_client, api_database, compound_kg=None, inserts=None)
    wo = work_order(make_client, w, w["fg"], "500")
    mc = machine(make_client, w)
    made = entry(make_client, w, wo, mc, good="420", rejects=[
        {"reason": "Flash", "quantity": "6"}, {"reason": "Short shot", "quantity": "4"}],
        downtime=[{"reason": "Mould change", "minutes": 45}, {"reason": "No power", "minutes": 15}])
    assert made.status_code == 201, made.text
    got = made.json()
    assert float(got["reject_qty"]) == 10 and got["downtime_minutes"] == 60
    assert got["work_order_no"] == wo["work_order_no"] and got["machine_code"] == mc["code"]
    assert sorted((x["kind"], x["reason"]) for x in got["losses"]) == [
        ("DOWNTIME", "Mould change"), ("DOWNTIME", "No power"), ("REJECT", "Flash"),
        ("REJECT", "Short shot")]
    again = entry(make_client, w, wo, mc, good="1")
    assert again.status_code == 409  # one entry per machine, order, date and shift
    listed = Fresh(make_client, "PROD").get(f"{M}/shift-entries?work_order_id={wo['work_order_id']}")
    assert listed.json()["total"] == 1 and len(listed.json()["items"][0]["losses"]) == 4


def test_shift_entry_validation_permissions_and_scope(make_client, api_database):
    w = shop(make_client, api_database, compound_kg=None, inserts=None)
    wo = work_order(make_client, w, w["fg"], "500")
    planned = work_order(make_client, w, w["fg"], "500", release_it=False)
    mc = machine(make_client, w)
    assert entry(make_client, w, wo, mc, day=TODAY + timedelta(days=1)).status_code == 422
    assert entry(make_client, w, wo, mc, shift="D").status_code == 422
    assert entry(make_client, w, wo, mc, good="0").status_code == 422  # nothing recorded
    assert entry(make_client, w, wo, mc, downtime=[
        {"reason": "Breakdown", "minutes": 300}, {"reason": "Other", "minutes": 300}]
    ).status_code == 422  # more than the 480-minute shift
    assert entry(make_client, w, wo, mc, good="-1").status_code == 422
    assert entry(make_client, w, planned, mc).status_code == 409  # not released
    other = make_client("ADMIN").post("/api/v1/org/units", json={"code": code(), "name": "P2"}).json()
    foreign = Fresh(make_client, "PROD").post(f"{M}/machines", json={
        "unit_id": other["unit_id"], "code": "X" + code()[:4], "name": "Elsewhere"}).json()
    assert entry(make_client, w, wo, foreign).status_code == 422  # machine of another plant
    for role in ("SALES", "PURCHASE", "QA"):
        assert entry(make_client, w, wo, mc, role=role).status_code == 403, role
    # a month already locked: two months back is past its last day plus the extra day
    old = (TODAY.replace(day=1) - timedelta(days=40))
    locked = entry(make_client, w, wo, mc, day=old)
    assert locked.status_code == 409 and "locked" in locked.json()["detail"]
    away = Fresh(make_client, "PROD", subject="u-shift-out", unit_ids=[other["unit_id"]])
    assert away.post(f"{M}/shift-entries", json={
        "work_order_id": wo["work_order_id"], "machine_id": mc["machine_id"],
        "work_date": TODAY.isoformat(), "shift_code": "A", "good_qty": "5"}).status_code == 404
    ok = entry(make_client, w, wo, mc)
    assert ok.status_code == 201
    assert away.get(f"{M}/shift-entries").json()["total"] == 0


def test_the_lock_window_follows_the_business_rules():
    from heyyerp_api.manufacturing.shopfloor import last_open_day

    assert last_open_day(date(2026, 9, 10), 0, 1) == date(2026, 10, 1)  # last day + 1
    assert last_open_day(date(2026, 2, 3), 0, 0) == date(2026, 2, 28)
    assert last_open_day(date(2026, 9, 10), 20, 3) == date(2026, 9, 23)  # lock day 20 + 3
    assert last_open_day(date(2026, 2, 3), 30, 0) == date(2026, 2, 28)  # clamped to the month


def test_missing_entries_alert_for_machines_that_ran_recently(make_client, api_database):
    w = shop(make_client, api_database, compound_kg=None, inserts=None)
    wo = work_order(make_client, w, w["fg"], "500")
    mc = machine(make_client, w)
    machine(make_client, w, "IDLE")  # never ran: not expected to report
    yesterday = TODAY - timedelta(days=1)
    assert entry(make_client, w, wo, mc, day=yesterday, shift="A").status_code == 201
    prod = Fresh(make_client, "PROD")
    got = prod.get(f"{M}/shift-entries/missing?work_date={TODAY}").json()
    assert sorted((m["machine_code"], m["shift_code"]) for m in got) == [
        (mc["code"], "A"), (mc["code"], "B"), (mc["code"], "C")]
    assert entry(make_client, w, wo, mc, shift="B").status_code == 201
    left = prod.get(f"{M}/shift-entries/missing?work_date={TODAY}").json()
    assert sorted(m["shift_code"] for m in left) == ["A", "C"]
    only = prod.get(f"{M}/shift-entries/missing?work_date={TODAY}&shift_code=C").json()
    assert [m["shift_code"] for m in only] == ["C"]


def test_oee_gives_availability_and_quality_per_machine(make_client, api_database):
    w = shop(make_client, api_database, compound_kg=None, inserts=None)
    wo = work_order(make_client, w, w["fg"], "5000")
    mc = machine(make_client, w)
    entry(make_client, w, wo, mc, shift="A", good="90", rejects=[{"reason": "Flash", "quantity": "10"}],
          downtime=[{"reason": "Mould change", "minutes": 60}])
    entry(make_client, w, wo, mc, shift="B", good="100", downtime=[{"reason": "Breakdown",
                                                                    "minutes": 120}])
    rows_ = Fresh(make_client, "PROD").get(
        f"{M}/oee?date_from={TODAY - timedelta(days=1)}&date_to={TODAY}").json()
    (row,) = [r for r in rows_ if r["machine_id"] == mc["machine_id"]]
    assert row["shifts"] == 2 and row["planned_minutes"] == 960 and row["downtime_minutes"] == 180
    assert float(row["availability"]) == pytest.approx(0.8125)
    assert float(row["quality"]) == pytest.approx(190 / 200) and row["performance"] is None
    assert Fresh(make_client, "PROD").get(
        f"{M}/oee?date_from={TODAY}&date_to={TODAY - timedelta(days=1)}").status_code == 422


# ------------------------------------------------------------------ rework and returns
def test_rework_takes_rejected_pieces_back_up_to_what_was_rejected(make_client, api_database):
    w = shop(make_client, api_database)
    wo = work_order(make_client, w, w["fg"], "100")
    lot_id = lot_with_rejects(make_client, w, wo, "50", "5")
    prod = Fresh(make_client, "PROD")
    assert float(prod.get(f"{M}/lots/{lot_id}").json()["quantity"]) == 45
    done = prod.post(f"{M}/lots/{lot_id}/rework", json={"quantity": "3", "reason": "Trim flash"})
    assert done.status_code == 200 and float(done.json()["quantity"]) == 48
    over = prod.post(f"{M}/lots/{lot_id}/rework", json={"quantity": "3", "reason": "Trim flash"})
    assert over.status_code == 409 and "Only 2" in over.json()["detail"]
    assert prod.post(f"{M}/lots/{lot_id}/rework", json={"quantity": "2", "reason": "ok"}
                     ).status_code == 422  # reason too short
    assert Fresh(make_client, "SALES").post(f"{M}/lots/{lot_id}/rework", json={
        "quantity": "1", "reason": "Trim flash"}).status_code == 403
    check = prod.get(f"{M}/reconciliation").json()
    assert check["ok"], check


def test_unused_material_goes_back_to_the_stock_lot_it_came_from(make_client, api_database):
    w = shop(make_client, api_database)
    wo = work_order(make_client, w, w["fg"], "100")
    lot = start(make_client, w, wo, "100").json()
    raw = next(s for s in lot["sources"] if s["consumption_type"] == "RAW_MATERIAL")
    after_issue = stock_on_hand(make_client, w, w["items"][1])
    prod = Fresh(make_client, "PROD")
    back = prod.post(f"{M}/lots/{lot['prod_lot_id']}/return-material", json={
        "source_lot_no": raw["source"], "quantity": "2"})
    assert back.status_code == 200, back.text
    assert stock_on_hand(make_client, w, w["items"][1]) == after_issue + 2
    more = prod.post(f"{M}/lots/{lot['prod_lot_id']}/return-material", json={
        "source_lot_no": raw["source"], "quantity": "3.5"})
    assert more.status_code == 409 and "Only 3" in more.json()["detail"]
    assert prod.post(f"{M}/lots/{lot['prod_lot_id']}/return-material", json={
        "source_lot_no": "NOPE", "quantity": "1"}).status_code == 422
    assert prod.post(f"{M}/lots/{lot['prod_lot_id']}/return-material", json={
        "source_lot_no": raw["source"], "quantity": "3"}).status_code == 200
    reconciles(make_client)
    variance = {v["component_code"]: v for v in prod.get(
        f"{M}/work-orders/{wo['work_order_id']}/material-variance").json()}
    raw_row = variance[w["items"][1]["code"]]
    assert float(raw_row["required"]) == 5 and float(raw_row["consumed"]) == 5
    assert float(raw_row["returned"]) == 5 and float(raw_row["variance"]) == -5
    others = [v for k, v in variance.items() if k != w["items"][1]["code"]]
    assert all(float(v["variance"]) == 0 for v in others)  # compound and inserts match the bill


# ------------------------------------------------------------------ WIP and reconciliation
def test_wip_lists_lots_by_route_stage_and_item(make_client, api_database):
    w = shop(make_client, api_database)
    wo = work_order(make_client, w, w["fg"], "100")
    first = start(make_client, w, wo, "30").json()
    second = start(make_client, w, wo, "20").json()
    run_route(make_client, second["prod_lot_id"], stop_at="QA_MOULDING")
    got = Fresh(make_client, "PROD").get(f"{M}/wip?unit_id={w['unit']['unit_id']}").json()
    by = {(r["route_code"], r["stage_code"], r["item_code"]): r for r in got}
    assert float(by[("MOULD", "MOULDING", w["fg"]["code"])]["quantity"]) == 30
    assert float(by[("MOULD", "QA_MOULDING", w["fg"]["code"])]["quantity"]) == 20
    # inserts waiting for moulding are WIP too: 200 made, 50 drawn by these lots
    assert float(by[("METAL", "READY_FOR_MOULDING", w["insert"]["code"])]["quantity"]) == 150
    assert first["status"] == "IN_PROCESS"


def test_lot_reconciliation_accounts_for_every_piece_and_catches_a_difference(
        make_client, api_database):
    w = shop(make_client, api_database)
    wo = work_order(make_client, w, w["fg"], "100")
    lot_id = lot_with_rejects(make_client, w, wo, "50", "5")
    check = Fresh(make_client, "PROD").get(f"{M}/reconciliation").json()
    assert check["ok"] and check["lots_checked"] >= 3, check
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("update erp.prod_lot set quantity = quantity + 1 where prod_lot_id = %s",
                     (lot_id,))
    broken = Fresh(make_client, "PROD").get(f"{M}/reconciliation").json()
    assert not broken["ok"] and [p["prod_lot_id"] for p in broken["problems"]] == [lot_id]
    assert Fresh(make_client, "SALES").get(f"{M}/reconciliation").status_code == 403
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("update erp.prod_lot set quantity = quantity - 1 where prod_lot_id = %s",
                     (lot_id,))
    assert Fresh(make_client, "PROD").get(f"{M}/reconciliation").json()["ok"]
