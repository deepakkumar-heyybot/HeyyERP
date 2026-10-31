"""Shift entries name the mould that ran: its shots count towards its life and its maintenance."""

import pytest

from .test_compound_batches import Fresh
from .test_maintenance import MF, MT, make_mould, plan
from .test_shop_floor import entry, machine
from .test_work_orders import shop, work_order

pytestmark = pytest.mark.usefixtures("api_database")


def test_shots_on_the_shift_entry_reach_the_mould_and_its_maintenance_plan(
        make_client, api_database):
    w = shop(make_client, api_database)
    wo = work_order(make_client, w, w["fg"], "100")
    mc = machine(make_client, w)
    mould = make_mould(make_client, w, limit=2000)
    p = plan(make_client, mould_id=mould["mould_id"], interval_shots=1000).json()
    first = entry(make_client, w, wo, mc, good="100", mould_id=mould["mould_id"], shots=950)
    assert first.status_code == 201, first.text
    assert first.json()["mould_code"] == mould["code"] and first.json()["shots"] == 950
    maint = Fresh(make_client, "MAINT")
    life = maint.get(f"{MT}/moulds/{mould['mould_id']}/life").json()
    assert life["shots_done"] == 950 and life["life_used_pct"] == 47.5
    assert maint.get(f"{MT}/pm-plans/{p['pm_plan_id']}").json()["pm_status"] == "DUE_SOON"
    second = entry(make_client, w, wo, mc, shift="B", good="100", mould_id=mould["mould_id"],
                   shots=100)
    assert second.status_code == 201
    assert maint.get(f"{MT}/pm-plans/{p['pm_plan_id']}").json()["pm_status"] == "OVERDUE"
    assert maint.get(f"{MT}/moulds/{mould['mould_id']}/life").json()["shots_done"] == 1050


def test_the_mould_and_its_shots_go_together_and_must_be_a_mould_of_the_plant(
        make_client, api_database):
    w = shop(make_client, api_database)
    wo = work_order(make_client, w, w["fg"], "100")
    mc = machine(make_client, w)
    mould = make_mould(make_client, w)
    assert entry(make_client, w, wo, mc, mould_id=mould["mould_id"]).status_code == 422
    assert entry(make_client, w, wo, mc, shots=10).status_code == 422
    assert entry(make_client, w, wo, mc, mould_id=mould["mould_id"], shots=0).status_code == 422
    other = Fresh(make_client, "ADMIN").post("/api/v1/org/units", json={
        "code": "OP" + mould["code"][-4:], "name": "Other plant"}).json()
    foreign = make_mould(make_client, {"unit": other})
    assert entry(make_client, w, wo, mc, shift="B", mould_id=foreign["mould_id"],
                 shots=5).status_code == 422
    Fresh(make_client, "PROD").patch(f"{MF}/moulds/{mould['mould_id']}", json={"active": False})
    assert entry(make_client, w, wo, mc, shift="C", mould_id=mould["mould_id"],
                 shots=5).status_code == 422
