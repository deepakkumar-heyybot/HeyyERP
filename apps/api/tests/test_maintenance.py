"""Preventive maintenance plans, mould shots, breakdown tickets, spares and reliability."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

from .test_bom import item as make_item
from .test_compound_batches import Fresh
from .test_costing import Rules
from .test_dispatch_invoice import fg_lot
from .test_inventory_receipt import code

pytestmark = pytest.mark.usefixtures("api_database")
MT = "/api/v1/maintenance"
MF = "/api/v1/manufacturing"
NOBODY = "00000000-0000-4000-8000-000000000000"


def plant(make_client):
    admin = Fresh(make_client, "ADMIN")
    unit = admin.post("/api/v1/org/units", json={"code": code(), "name": "Plant"}).json()
    store = admin.post("/api/v1/org/stores", json={
        "unit_id": unit["unit_id"], "code": "SP" + code()[:4], "name": "Spares",
        "store_type": "SPARES"}).json()
    return {"unit": unit, "store": store}


def make_machine(make_client, w, name="PR"):
    made = Fresh(make_client, "PROD").post(f"{MF}/machines", json={
        "unit_id": w["unit"]["unit_id"], "code": name + code()[:4], "name": "Press"})
    assert made.status_code == 201, made.text
    return made.json()


def make_mould(make_client, w, limit=1000):
    part = make_item(Fresh(make_client, "ADMIN"), "FG", "NOS")
    made = Fresh(make_client, "PROD").post(f"{MF}/moulds", json={
        "unit_id": w["unit"]["unit_id"], "part_item_id": part["item_id"], "code": "M" + code()[:5],
        "shots_limit": limit})
    assert made.status_code == 201, made.text
    return made.json()


def plan(make_client, **body):
    return Fresh(make_client, "MAINT").post(f"{MT}/pm-plans", json={"title": "Lubrication", **body})


def sql(api_database, query, *params):
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute(query, params)


# ------------------------------------------------------------------ plans by days
def test_a_plan_by_days_goes_from_ok_to_due_soon_to_overdue_and_is_reset_when_done(
        make_client, api_database):
    w = plant(make_client)
    mc = make_machine(make_client, w)
    made = plan(make_client, machine_id=mc["machine_id"], interval_days=30)
    assert made.status_code == 201, made.text
    p = made.json()
    assert p["pm_status"] == "OK" and p["equipment_code"] == mc["code"] and p["shots_left"] is None
    maint = Fresh(make_client, "MAINT")
    for days_ago, expected in ((20, "OK"), (25, "DUE_SOON"), (40, "OVERDUE")):
        sql(api_database, "update erp.pm_plan set last_done_at = now() - make_interval(days => %s)"
            " where pm_plan_id = %s", days_ago, p["pm_plan_id"])
        assert maint.get(f"{MT}/pm-plans/{p['pm_plan_id']}").json()["pm_status"] == expected
    sql(api_database, "update erp.pm_plan set last_done_at = now() - interval '25 days' "
        "where pm_plan_id = %s", p["pm_plan_id"])
    with Rules(make_client, pmDueSoonDays=3):  # the company narrows the "due soon" window
        assert maint.get(f"{MT}/pm-plans/{p['pm_plan_id']}").json()["pm_status"] == "OK"
    overdue = maint.get(f"{MT}/pm-plans?unit_id={w['unit']['unit_id']}&status=OVERDUE").json()
    assert overdue["total"] == 0
    sql(api_database, "update erp.pm_plan set last_done_at = now() - interval '40 days' "
        "where pm_plan_id = %s", p["pm_plan_id"])
    first = maint.get(f"{MT}/pm-plans?unit_id={w['unit']['unit_id']}").json()["items"][0]
    assert first["pm_plan_id"] == p["pm_plan_id"] and first["pm_status"] == "OVERDUE"
    done = maint.post(f"{MT}/pm-plans/{p['pm_plan_id']}/done", json={"notes": "Greased, checked"})
    assert done.status_code == 200 and done.json()["pm_status"] == "OK"
    due = datetime.fromisoformat(done.json()["next_due_at"])
    assert abs((due - (datetime.now(UTC) + timedelta(days=30))).total_seconds()) < 120
    log = maint.get(f"{MT}/pm-plans/{p['pm_plan_id']}/history").json()
    assert len(log) == 1 and log[0]["status"] == "DONE" and log[0]["notes"] == "Greased, checked"
    assert maint.post(f"{MT}/pm-plans/{p['pm_plan_id']}/done", json={
        "done_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()}).status_code == 422
    stopped = maint.patch(f"{MT}/pm-plans/{p['pm_plan_id']}", json={"active": False})
    assert stopped.status_code == 200
    assert maint.post(f"{MT}/pm-plans/{p['pm_plan_id']}/done", json={}).status_code == 409


# ------------------------------------------------------------------ plans by shots
def test_a_mould_plan_follows_the_shots_and_the_mould_life_is_tracked(make_client, api_database):
    w = plant(make_client)
    mould = make_mould(make_client, w, limit=1000)
    p = plan(make_client, mould_id=mould["mould_id"], interval_shots=100).json()
    assert p["shots_left"] == 100 and p["pm_status"] == "OK"
    operator = Fresh(make_client, "OPERATOR")
    shots = operator.post(f"{MT}/moulds/{mould['mould_id']}/shots", json={"shots": 95})
    assert shots.status_code == 200 and shots.json()["life_used_pct"] == 9.5
    assert Fresh(make_client, "MAINT").get(f"{MT}/pm-plans/{p['pm_plan_id']}").json()[
        "pm_status"] == "DUE_SOON"  # the last tenth of the shots
    operator.post(f"{MT}/moulds/{mould['mould_id']}/shots", json={"shots": 10})
    late = Fresh(make_client, "MAINT").get(f"{MT}/pm-plans/{p['pm_plan_id']}").json()
    assert late["pm_status"] == "OVERDUE" and late["shots_left"] == -5
    done = Fresh(make_client, "MAINT").post(f"{MT}/pm-plans/{p['pm_plan_id']}/done", json={}).json()
    assert done["shots_left"] == 100 and done["pm_status"] == "OK"  # counting starts again
    assert Fresh(make_client, "MAINT").get(f"{MT}/pm-plans/{p['pm_plan_id']}/history").json()[0][
        "shots_at_done"] == 105
    assert Fresh(make_client, "MAINT").get(
        f"{MT}/moulds-over-limit?unit_id={w['unit']['unit_id']}").json() == []
    operator.post(f"{MT}/moulds/{mould['mould_id']}/shots", json={"shots": 900})
    worn = Fresh(make_client, "MAINT").get(
        f"{MT}/moulds-over-limit?unit_id={w['unit']['unit_id']}").json()
    assert [m["code"] for m in worn] == [mould["code"]] and worn[0]["over_limit"]


def test_plan_validation_and_permissions(make_client, api_database):
    w = plant(make_client)
    mc = make_machine(make_client, w)
    mould = make_mould(make_client, w)
    for body in ({"interval_days": 30},  # no target
                 {"machine_id": mc["machine_id"], "mould_id": mould["mould_id"], "interval_days": 5},
                 {"machine_id": mc["machine_id"]},  # no interval
                 {"machine_id": mc["machine_id"], "interval_shots": 100},  # shots on a machine
                 {"machine_id": mc["machine_id"], "interval_days": 0}):
        assert plan(make_client, **body).status_code == 422, body
    assert plan(make_client, machine_id=NOBODY, interval_days=5).status_code == 422
    Fresh(make_client, "PROD").patch(f"{MF}/machines/{mc['machine_id']}", json={"active": False})
    assert plan(make_client, machine_id=mc["machine_id"], interval_days=5).status_code == 422
    for role in ("PROD", "OPERATOR", "SALES", "STORES"):  # only maintenance plans and records
        assert Fresh(make_client, role).post(f"{MT}/pm-plans", json={
            "title": "Abc", "mould_id": mould["mould_id"], "interval_shots": 10}
        ).status_code == 403, role
    for role in ("SALES", "STORES", "QA"):  # shots come from production
        assert Fresh(make_client, role).post(f"{MT}/moulds/{mould['mould_id']}/shots", json={
            "shots": 5}).status_code == 403, role
    assert Fresh(make_client, "OPERATOR").post(f"{MT}/moulds/{mould['mould_id']}/shots", json={
        "shots": 0}).status_code == 422
    assert Fresh(make_client, "MAINT").get(f"{MT}/pm-plans/{NOBODY}").status_code == 404


# ------------------------------------------------------------------ breakdowns and spares
def spare_stock(make_client, api_database, w, quantity="10"):
    spare = make_item(Fresh(make_client, "ADMIN"), "SPARE", "NOS")
    fg_lot(api_database, w, w["store"], quantity, item=spare)
    return spare


def on_hand(make_client, w, spare):
    got = Fresh(make_client, "STORES").get(
        f"/api/v1/inventory/stock?store_id={w['store']['store_id']}&item_id={spare['item_id']}")
    return sum(float(r["on_hand"]) for r in got.json()["items"])


def test_a_breakdown_is_reported_repaired_with_spares_and_closed(make_client, api_database):
    w = plant(make_client)
    mc = make_machine(make_client, w)
    spare = spare_stock(make_client, api_database, w)
    raw = make_item(Fresh(make_client, "ADMIN"), "RM", "KG")
    fg_lot(api_database, w, w["store"], "5", item=raw)
    reported = Fresh(make_client, "OPERATOR").post(f"{MT}/breakdowns", json={
        "machine_id": mc["machine_id"], "description": "Hydraulic leak at the clamp"})
    assert reported.status_code == 201, reported.text
    b = reported.json()
    assert b["breakdown_no"].startswith("BD-") and b["status"] == "OPEN" and b["ended_at"] is None
    assert Fresh(make_client, "PROD").post(f"{MT}/breakdowns", json={
        "machine_id": mc["machine_id"], "description": "Again, the same machine"}
    ).status_code == 409  # one open breakdown per machine
    maint = Fresh(make_client, "MAINT")
    body = {"store_id": w["store"]["store_id"], "lines": [
        {"item_id": spare["item_id"], "quantity": "3"}]}
    assert Fresh(make_client, "OPERATOR").post(f"{MT}/breakdowns/{b['breakdown_id']}/spares",
                                               json=body).status_code == 403
    wrong = maint.post(f"{MT}/breakdowns/{b['breakdown_id']}/spares", json={
        "store_id": w["store"]["store_id"], "lines": [{"item_id": raw["item_id"], "quantity": "1"}]})
    assert wrong.status_code == 422 and "SPARE" in wrong.json()["detail"]
    short = maint.post(f"{MT}/breakdowns/{b['breakdown_id']}/spares", json={
        "store_id": w["store"]["store_id"], "lines": [
            {"item_id": spare["item_id"], "quantity": "50"}]})
    assert short.status_code == 409
    assert on_hand(make_client, w, spare) == 10  # the refused issue took nothing
    used = maint.post(f"{MT}/breakdowns/{b['breakdown_id']}/spares", json=body)
    assert used.status_code == 200, used.text
    assert [(s["item_code"], Decimal(s["quantity"])) for s in used.json()["spares"]] == [
        (spare["code"], Decimal(3))]
    assert on_hand(make_client, w, spare) == 7
    assert Fresh(make_client, "OPERATOR").post(f"{MT}/breakdowns/{b['breakdown_id']}/close", json={
        "cause": "Seal worn", "corrective_action": "Replaced"}).status_code == 403
    assert maint.post(f"{MT}/breakdowns/{b['breakdown_id']}/close", json={
        "cause": "Seal worn", "corrective_action": "Replaced the seal",
        "ended_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}).status_code == 422
    closed = maint.post(f"{MT}/breakdowns/{b['breakdown_id']}/close", json={
        "cause": "Seal worn", "corrective_action": "Replaced the seal"})
    assert closed.status_code == 200 and closed.json()["status"] == "CLOSED"
    assert closed.json()["cause"] == "Seal worn" and closed.json()["ended_at"]
    assert maint.post(f"{MT}/breakdowns/{b['breakdown_id']}/close", json={
        "cause": "Again", "corrective_action": "Again"}).status_code == 409
    assert maint.post(f"{MT}/breakdowns/{b['breakdown_id']}/spares", json=body).status_code == 409
    listed = maint.get(f"{MT}/breakdowns?machine_id={mc['machine_id']}&status=closed").json()
    assert listed["total"] == 1 and listed["items"][0]["breakdown_no"] == b["breakdown_no"]
    again = Fresh(make_client, "PROD").post(f"{MT}/breakdowns", json={
        "machine_id": mc["machine_id"], "description": "A new failure later"})
    assert again.status_code == 201  # the machine was running again


def test_breakdown_dates_and_permissions(make_client, api_database):
    w = plant(make_client)
    mc = make_machine(make_client, w)
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    assert Fresh(make_client, "OPERATOR").post(f"{MT}/breakdowns", json={
        "machine_id": mc["machine_id"], "description": "Not yet", "started_at": future}
    ).status_code == 422
    for role in ("SALES", "STORES", "QA", "ACCOUNTS"):
        assert Fresh(make_client, role).post(f"{MT}/breakdowns", json={
            "machine_id": mc["machine_id"], "description": "Not mine"}).status_code == 403, role
    assert Fresh(make_client, "OPERATOR").post(f"{MT}/breakdowns", json={
        "machine_id": NOBODY, "description": "No such machine"}).status_code == 422
    b = Fresh(make_client, "OPERATOR").post(f"{MT}/breakdowns", json={
        "machine_id": mc["machine_id"], "description": "Stopped",
        "started_at": (datetime.now(UTC) - timedelta(hours=3)).isoformat()}).json()
    assert 170 <= b["downtime_minutes"] <= 190  # still down: the clock runs
    early = Fresh(make_client, "MAINT").post(f"{MT}/breakdowns/{b['breakdown_id']}/close", json={
        "cause": "Cause", "corrective_action": "Action",
        "ended_at": (datetime.now(UTC) - timedelta(hours=5)).isoformat()})
    assert early.status_code == 422  # it cannot end before it began
    assert Fresh(make_client, "SALES").get(f"{MT}/breakdowns").status_code == 403


# ------------------------------------------------------------------ MTTR and MTBF
def test_reliability_gives_mttr_and_mtbf_per_machine(make_client, api_database):
    w = plant(make_client)
    busy, quiet = make_machine(make_client, w, "A"), make_machine(make_client, w, "B")
    today = datetime.now(UTC)
    for days_ago, hours in ((5, 2), (3, 4)):
        started = today - timedelta(days=days_ago)
        made = Fresh(make_client, "OPERATOR").post(f"{MT}/breakdowns", json={
            "machine_id": busy["machine_id"], "description": "Stopped", "started_at": started.isoformat()})
        assert made.status_code == 201, made.text
        closed = Fresh(make_client, "MAINT").post(
            f"{MT}/breakdowns/{made.json()['breakdown_id']}/close", json={
                "cause": "Cause", "corrective_action": "Action",
                "ended_at": (started + timedelta(hours=hours)).isoformat()})
        assert closed.status_code == 200, closed.text
    first = (today - timedelta(days=9)).date().isoformat()
    got = Fresh(make_client, "PLANT_HEAD").get(
        f"{MT}/reliability?unit_id={w['unit']['unit_id']}&date_from={first}"
        f"&date_to={today.date().isoformat()}").json()
    assert got["window_hours"] == 240
    by = {m["machine_code"]: m for m in got["machines"]}
    a, b = by[busy["code"]], by[quiet["code"]]
    assert a["breakdowns"] == 2 and a["downtime_hours"] == 6 and a["mttr_hours"] == 3
    assert a["mtbf_hours"] == 117  # (240 - 6) / 2
    assert b["breakdowns"] == 0 and b["mttr_hours"] is None and b["mtbf_hours"] is None
    assert Fresh(make_client, "MAINT").get(
        f"{MT}/reliability?unit_id={w['unit']['unit_id']}&date_from=2030-02-01&date_to=2030-01-01"
    ).status_code == 422
