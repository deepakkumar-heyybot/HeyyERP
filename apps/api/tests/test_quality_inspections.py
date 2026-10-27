"""Inspection plans, inspection records, calibration and the QA queue."""

from datetime import date, timedelta
from uuid import uuid4

import pytest

from .test_bom import item as make_item
from .test_compound_batches import (
    INV,
    Fresh,
    M,
    make_compound,
    reconciles,
    stocked,
    to_awaiting_qa,
    weigh,
)
from .test_inventory_receipt import code, inspect, world
from .test_work_orders import run_route, shop, start, work_order

pytestmark = pytest.mark.usefixtures("api_database")
Q = "/api/v1/quality"
TODAY = date.today()


def plan_body(item, kind="INCOMING", **extra):
    return {"item_id": item["item_id"], "kind": kind, "name": f"{kind.title()} check",
            "params": [{"parameter_code": "moisture", "name": "Moisture", "unit": "%",
                        "max_value": "1.0"},
                       {"parameter_code": "purity", "name": "Purity", "unit": "%",
                        "min_value": "95", "is_key": True}], **extra}


def make_plan(make_client, item, kind="INCOMING"):
    made = Fresh(make_client, "QA").post(f"{Q}/plans", json=plan_body(item, kind))
    assert made.status_code == 201, made.text
    return made.json()


def inspection(make_client, kind, subject_type, subject_id, result="ACCEPT", **extra):
    return Fresh(make_client, "QA").post(f"{Q}/inspections", json={
        "kind": kind, "subject_type": subject_type, "subject_id": subject_id, "result": result,
        **extra})


GOOD = [{"parameter_code": "MOISTURE", "value": "0.4"}, {"parameter_code": "PURITY", "value": "97"}]
BAD = [{"parameter_code": "MOISTURE", "value": "1.6"}, {"parameter_code": "PURITY", "value": "97"}]


def received_line(make_client, w, qty="100"):
    made = Fresh(make_client, "STORES").post(f"{INV}/grns", json={
        "po_id": w["po"]["po_id"], "supplier_dc_no": "DC-" + uuid4().hex[:5], "lines": [{
            "po_line_id": w["po"]["lines"][0]["po_line_id"], "store_id": w["store"]["store_id"],
            "received_qty": qty}]})
    assert made.status_code == 201, made.text
    return made.json()["lines"][0]


def accept(make_client, line, qty):
    return inspect(Fresh(make_client, "QA"), line, qty)


# ------------------------------------------------------------------ plans
def test_plans_are_defined_per_item_and_kind_and_a_new_one_retires_the_old(make_client):
    w = world(make_client)
    first = make_plan(make_client, w["item"])
    assert [p["parameter_code"] for p in first["params"]] == ["MOISTURE", "PURITY"]
    assert first["active"] and first["item_code"] == w["item"]["code"]
    second = make_plan(make_client, w["item"])
    qa = Fresh(make_client, "QA")
    assert qa.get(f"{Q}/plans/{first['plan_id']}").json()["active"] is False
    assert qa.get(f"{Q}/plans?item_id={w['item']['item_id']}&active=true").json()["total"] == 1
    other_kind = make_plan(make_client, w["item"], "FINAL")
    assert other_kind["active"] and qa.get(f"{Q}/plans?item_id={w['item']['item_id']}"
                                           ).json()["total"] == 3
    retired = qa.post(f"{Q}/plans/{second['plan_id']}/retire")
    assert retired.status_code == 200 and retired.json()["active"] is False
    assert qa.post(f"{Q}/plans/{second['plan_id']}/retire").status_code == 409


def test_plan_validation_and_permissions(make_client):
    w = world(make_client)
    qa = Fresh(make_client, "QA")
    base = plan_body(w["item"])
    post = lambda **b: qa.post(f"{Q}/plans", json={**base, **b}).status_code  # noqa: E731
    assert post(params=[]) == 422
    assert post(params=[{"parameter_code": "X", "name": "No limits"}]) == 422
    assert post(params=[{"parameter_code": "X", "name": "Inverted", "min_value": "5",
                         "max_value": "1"}]) == 422
    same = {"parameter_code": "x", "name": "Same", "max_value": "1"}
    assert post(params=[same, same]) == 422
    assert post(item_id=str(uuid4())) == 422 and post(kind="DAILY") == 422
    for role in ("SALES", "PURCHASE", "STORES", "PROD"):
        assert Fresh(make_client, role).post(f"{Q}/plans", json=base).status_code == 403, role
    assert Fresh(make_client, "SALES").get(f"{Q}/plans").status_code == 403


# ------------------------------------------------------------------ incoming inspection and GRN release
def test_a_grn_line_with_a_plan_cannot_be_accepted_without_a_passing_inspection(make_client):
    w = world(make_client)
    make_plan(make_client, w["item"])
    line = received_line(make_client, w)
    refused = accept(make_client, line, "100")
    assert refused.status_code == 409 and "inspection first" in refused.json()["detail"]
    gone = inspection(make_client, "INCOMING", "GRN_LINE", line["grn_line_id"], "ACCEPT",
                      measurements=GOOD[:1])
    assert gone.status_code == 422 and "missing PURITY" in gone.json()["detail"]
    odd = inspection(make_client, "INCOMING", "GRN_LINE", line["grn_line_id"], "ACCEPT",
                     measurements=GOOD + [{"parameter_code": "COLOUR", "value": "1"}])
    assert odd.status_code == 422 and "not in the plan: COLOUR" in odd.json()["detail"]
    lying = inspection(make_client, "INCOMING", "GRN_LINE", line["grn_line_id"], "ACCEPT",
                       measurements=BAD)
    assert lying.status_code == 422 and "MOISTURE" in lying.json()["detail"]
    failed = inspection(make_client, "INCOMING", "GRN_LINE", line["grn_line_id"], "REJECT",
                        measurements=BAD, notes="Wet material")
    assert failed.status_code == 201, failed.text
    got = {m["parameter_code"]: m for m in failed.json()["measurements"]}
    assert got["MOISTURE"]["result"] == "FAIL" and got["PURITY"]["result"] == "PASS"
    assert float(got["MOISTURE"]["max_value"]) == 1.0 and failed.json()["source"] == "MANUAL"
    blocked = accept(make_client, line, "100")
    assert blocked.status_code == 409 and "reject" in blocked.json()["detail"]
    passing = inspection(make_client, "INCOMING", "GRN_LINE", line["grn_line_id"], "ACCEPT",
                         measurements=GOOD)
    assert passing.status_code == 201 and passing.json()["plan_name"] == "Incoming check"
    done = accept(make_client, line, "100")
    assert done.status_code == 200, done.text
    history = Fresh(make_client, "QA").get(
        f"{Q}/inspections?subject_type=GRN_LINE&subject_id={line['grn_line_id']}").json()
    assert [(i["source"], i["result"]) for i in history["items"]][-1] == ("MANUAL", "REJECT")
    assert {i["source"] for i in history["items"]} == {"MANUAL", "AUTO"} and history["total"] == 3
    reconciles(make_client)


def test_rejecting_a_line_never_needs_an_inspection_and_is_recorded(make_client):
    w = world(make_client)
    make_plan(make_client, w["item"])
    line = received_line(make_client, w)
    assert accept(make_client, line, "0").status_code == 200  # reject all: no clearance needed
    (record,) = Fresh(make_client, "QA").get(
        f"{Q}/inspections?subject_id={line['grn_line_id']}").json()["items"]
    assert record["source"] == "AUTO" and record["result"] == "REJECT" and record["kind"] == "INCOMING"


def test_without_a_plan_the_decision_is_recorded_automatically(make_client):
    w = world(make_client)
    line = received_line(make_client, w)
    assert accept(make_client, line, "60").status_code == 200
    (record,) = Fresh(make_client, "QA").get(
        f"{Q}/inspections?subject_id={line['grn_line_id']}").json()["items"]
    assert record["source"] == "AUTO" and record["result"] == "PARTIAL"
    assert record["inspection_no"].startswith("QI-") and record["inspected_by_name"]


def test_inspection_validation_scope_and_permissions(make_client):
    w = world(make_client)
    line = received_line(make_client, w)
    qa = Fresh(make_client, "QA")
    body = {"kind": "INCOMING", "subject_type": "GRN_LINE", "subject_id": line["grn_line_id"],
            "result": "ACCEPT"}
    assert qa.post(f"{Q}/inspections", json={**body, "subject_id": str(uuid4())}).status_code == 404
    assert qa.post(f"{Q}/inspections", json={**body, "result": "MAYBE"}).status_code == 422
    assert qa.post(f"{Q}/inspections", json={**body, "measurements": GOOD + GOOD}
                   ).status_code == 422  # a parameter twice
    free = qa.post(f"{Q}/inspections", json={**body, "measurements": [
        {"parameter_code": "ODOUR", "value": "1", "unit": "pts"}]})
    assert free.status_code == 201 and free.json()["measurements"][0]["result"] == "INFO"
    for role in ("SALES", "PURCHASE", "STORES"):
        assert Fresh(make_client, role).post(f"{Q}/inspections", json=body).status_code == 403
    other = make_client("ADMIN").post("/api/v1/org/units", json={"code": code(), "name": "P2"}).json()
    away = Fresh(make_client, "QA", subject="u-qa-out", unit_ids=[other["unit_id"]])
    assert away.post(f"{Q}/inspections", json=body).status_code == 404
    assert away.get(f"{Q}/inspections/{free.json()['inspection_id']}").status_code == 404
    assert away.get(f"{Q}/queue").json() == []


# ------------------------------------------------------------------ instruments
def test_instrument_calibration_status_alerts_and_use_in_inspections(make_client):
    w = world(make_client)
    line = received_line(make_client, w)
    qa = Fresh(make_client, "QA")
    unit = w["unit"]["unit_id"]
    inst = qa.post(f"{Q}/instruments", json={"unit_id": unit, "code": "dg-1", "name": "Durometer"})
    assert inst.status_code == 201 and inst.json()["code"] == "DG-1"
    assert inst.json()["calibration_status"] == "NEVER"
    iid = inst.json()["instrument_id"]
    assert qa.post(f"{Q}/instruments", json={"unit_id": unit, "code": "DG-1", "name": "Dup"}
                   ).status_code == 409
    use = lambda: inspection(make_client, "INCOMING", "GRN_LINE", line["grn_line_id"],  # noqa: E731
                             instrument_id=iid)
    assert use().status_code == 409 and "never" in use().json()["detail"]

    def calibrate(done, due, result="PASS"):
        return qa.post(f"{Q}/instruments/{iid}/calibrations", json={
            "calibrated_at": done.isoformat(), "due_at": due.isoformat(), "result": result,
            "certificate_ref": "CERT-1"})

    ok = calibrate(TODAY - timedelta(days=10), TODAY + timedelta(days=200))
    assert ok.status_code == 201 and ok.json()["calibration_status"] == "OK"
    assert use().status_code == 201
    late = calibrate(TODAY - timedelta(days=9), TODAY - timedelta(days=2))
    assert late.json()["calibration_status"] == "OVERDUE" and late.json()["days_to_due"] == -2
    assert use().status_code == 409 and "overdue" in use().json()["detail"]
    soon = calibrate(TODAY - timedelta(days=8), TODAY + timedelta(days=12)).json()
    assert soon["calibration_status"] == "DUE_SOON" and soon["days_to_due"] == 12
    assert use().status_code == 201  # still usable inside the warning window
    assert [i["code"] for i in qa.get(f"{Q}/calibration/due").json()] == ["DG-1"]
    assert qa.get(f"{Q}/calibration/due?within_days=5").json() == []
    failed = calibrate(TODAY - timedelta(days=7), TODAY + timedelta(days=90), "FAIL").json()
    assert failed["calibration_status"] == "FAILED" and use().status_code == 409
    assert qa.get(f"{Q}/instruments?calibration_status=failed").json()["total"] == 1
    assert calibrate(TODAY + timedelta(days=1), TODAY + timedelta(days=30)).status_code == 422
    assert calibrate(TODAY, TODAY).status_code == 422
    qa.patch(f"{Q}/instruments/{iid}", json={"active": False})
    assert calibrate(TODAY - timedelta(days=6), TODAY + timedelta(days=90)).json()["active"] is False
    assert use().status_code == 409 and "inactive" in use().json()["detail"]
    assert Fresh(make_client, "SALES").post(f"{Q}/instruments", json={
        "unit_id": unit, "code": "X", "name": "Nope"}).status_code == 403
    assert qa.patch(f"{Q}/instruments/{iid}", json={"name": None}).status_code == 422


# ------------------------------------------------------------------ compound batch and stage lot gates
def test_a_compound_batch_with_a_plan_needs_a_passing_inspection_before_qa_approval(
        make_client, api_database):
    w = stocked(make_client)
    wip = make_item(Fresh(make_client, "ADMIN"), "WIP", "KG")
    compound = make_compound(make_client, w, output_item_id=wip["item_id"])
    Fresh(make_client, "QA").post(f"{Q}/plans", json={
        "item_id": wip["item_id"], "kind": "IN_PROCESS", "name": "Batch check",
        "params": [{"parameter_code": "T90", "name": "t90", "unit": "min", "max_value": "6"}]})
    batch = weigh(make_client, w, compound, "100").json()["batch_id"]
    to_awaiting_qa(make_client, api_database, batch)
    qa = Fresh(make_client, "QA")
    queue = [q for q in qa.get(f"{Q}/queue?subject_type=COMPOUND_BATCH").json()
             if q["subject_id"] == batch]
    assert len(queue) == 1 and queue[0]["inspected"] is False
    refused = qa.post(f"{M}/batches/{batch}/qa", json={"result": "PASSED", "hardness": "60"})
    assert refused.status_code == 409 and "inspection first" in refused.json()["detail"]
    assert inspection(make_client, "IN_PROCESS", "COMPOUND_BATCH", batch,
                      measurements=[{"parameter_code": "T90", "value": "5.2"}]).status_code == 201
    assert qa.post(f"{M}/batches/{batch}/qa", json={"result": "PASSED", "hardness": "60"}
                   ).status_code == 200
    assert [q for q in qa.get(f"{Q}/queue").json() if q["subject_id"] == batch] == []
    history = qa.get(f"{Q}/inspections?subject_id={batch}").json()
    assert {i["source"] for i in history["items"]} == {"MANUAL", "AUTO"}


def test_a_stage_lot_cannot_leave_a_qa_gate_on_a_held_or_missing_inspection(
        make_client, api_database):
    w = shop(make_client, api_database)
    Fresh(make_client, "QA").post(f"{Q}/plans", json={
        "item_id": w["fg"]["item_id"], "kind": "FINAL", "name": "Final check",
        "params": [{"parameter_code": "HARD", "name": "Hardness", "unit": "ShA",
                    "min_value": "55", "max_value": "65"}]})
    wo = work_order(make_client, w, w["fg"], "100")
    lot = start(make_client, w, wo, "40").json()
    lot_id = lot["prod_lot_id"]
    run_route(make_client, lot_id, stop_at="QA_MOULDING")
    qa = Fresh(make_client, "QA")
    queued = [q for q in qa.get(f"{Q}/queue").json() if q["subject_id"] == lot_id]
    assert queued and queued[0]["stage"] == "QA_MOULDING" and queued[0]["inspected"] is False
    step = lambda **b: qa.post(f"{M}/lots/{lot_id}/move", json=b)  # noqa: E731
    first = step()
    assert first.status_code == 409 and "inspection first" in first.json()["detail"]
    held = inspection(make_client, "FINAL", "PROD_LOT", lot_id, "HOLD",
                      measurements=[{"parameter_code": "HARD", "value": "70"}])
    assert held.status_code == 201
    blocked = step()
    assert blocked.status_code == 409 and "hold" in blocked.json()["detail"]
    assert inspection(make_client, "FINAL", "PROD_LOT", lot_id, "ACCEPT",
                      measurements=[{"parameter_code": "HARD", "value": "60"}]).status_code == 201
    moved = step(rejects="4")
    assert moved.status_code == 200 and moved.json()["stage_code"] == "PACKING"
    results = [(i["source"], i["result"]) for i in qa.get(
        f"{Q}/inspections?subject_id={lot_id}").json()["items"]]
    assert ("AUTO", "PARTIAL") in results and ("MANUAL", "HOLD") in results
    assert [q for q in qa.get(f"{Q}/queue").json() if q["subject_id"] == lot_id] == []
