"""Quality holds, NCRs with 8D steps and dispositions, and the policy that held or rejected goods
cannot be used."""

from uuid import uuid4

import pytest

from .test_compound_batches import Fresh, M, make_compound, stocked, to_awaiting_qa, weigh
from .test_inventory_receipt import INV, code, world
from .test_inventory_transfer_count import new_store, transfer
from .test_quality_inspections import Q, accept, received_line
from .test_work_orders import run_route, shop, start, work_order

pytestmark = pytest.mark.usefixtures("api_database")


def good_lot(make_client, w, qty="100"):
    """A received and accepted (AVAILABLE) lot."""
    line = received_line(make_client, w, qty)
    assert accept(make_client, line, qty).status_code == 200
    return line


def hold(make_client, subject_type, subject_id, reason="Customer complaint"):
    return Fresh(make_client, "QA").post(f"{Q}/holds", json={
        "subject_type": subject_type, "subject_id": subject_id, "reason": reason})


def release(make_client, hold_id, note="Checked and cleared"):
    return Fresh(make_client, "QA").post(f"{Q}/holds/{hold_id}/release", json={"note": note})


def issue(make_client, w, qty="10"):
    return Fresh(make_client, "STORES").post(f"{INV}/issues", json={
        "store_id": w["store"]["store_id"], "purpose": "Production batch",
        "lines": [{"item_id": w["item"]["item_id"], "quantity": qty}]})


def lot_status(make_client, lot_id):
    return Fresh(make_client, "STORES").get(f"{INV}/lots/{lot_id}").json()["lot_status"]


# ------------------------------------------------------------------ holds
def test_a_held_stock_lot_cannot_be_issued_or_transferred_until_released(make_client):
    w = world(make_client)
    line = good_lot(make_client, w)
    dst = new_store(make_client, w["unit"]["unit_id"])["store_id"]
    placed = hold(make_client, "STOCK_LOT", line["lot_id"])
    assert placed.status_code == 201, placed.text
    h = placed.json()
    assert h["active"] and h["subject_label"] == line["lot_no"] and h["placed_by_name"]
    assert lot_status(make_client, line["lot_id"]) == "QUARANTINE"
    refused = issue(make_client, w)
    assert refused.status_code == 409
    sent = transfer(make_client, w["store"]["store_id"], dst, line, "10").json()
    dispatch = Fresh(make_client, "STORES").post(f"{INV}/transfers/{sent['transfer_id']}/dispatch")
    assert dispatch.status_code == 409 and "quarantine" in dispatch.json()["detail"]
    assert hold(make_client, "STOCK_LOT", line["lot_id"]).status_code == 409  # already held
    assert release(make_client, h["hold_id"], "ok").status_code == 422  # note too short
    back = release(make_client, h["hold_id"])
    assert back.status_code == 200 and back.json()["active"] is False
    assert back.json()["released_by_name"] and back.json()["release_note"] == "Checked and cleared"
    assert lot_status(make_client, line["lot_id"]) == "AVAILABLE"
    assert release(make_client, h["hold_id"]).status_code == 409  # already released
    assert issue(make_client, w).status_code == 201
    assert Fresh(make_client, "STORES").post(
        f"{INV}/transfers/{sent['transfer_id']}/dispatch").status_code == 200


def test_goods_that_are_not_available_cannot_be_held_and_rejected_goods_cannot_be_used(
        make_client):
    w = world(make_client, qty="300")
    waiting = received_line(make_client, w, "100")  # still in QA hold
    assert hold(make_client, "STOCK_LOT", waiting["lot_id"]).status_code == 409
    assert accept(make_client, waiting, "0").status_code == 200  # rejected: lot BLOCKED
    assert lot_status(make_client, waiting["lot_id"]) == "BLOCKED"
    assert hold(make_client, "STOCK_LOT", waiting["lot_id"]).status_code == 409
    assert issue(make_client, w, "1").status_code == 409  # nothing usable in the store


def test_hold_listing_permissions_and_scope(make_client):
    w = world(make_client)
    line = good_lot(make_client, w)
    h = hold(make_client, "STOCK_LOT", line["lot_id"]).json()
    qa = Fresh(make_client, "QA")
    assert qa.get(f"{Q}/holds?active=true").json()["total"] >= 1
    assert qa.get(f"{Q}/holds?subject_id={line['lot_id']}").json()["items"][0]["hold_id"] == h[
        "hold_id"]
    assert qa.get(f"{Q}/holds/{h['hold_id']}").status_code == 200
    for role in ("SALES", "PURCHASE", "STORES", "PROD"):
        assert hold(make_client, "STOCK_LOT", line["lot_id"]) and Fresh(make_client, role).post(
            f"{Q}/holds", json={"subject_type": "STOCK_LOT", "subject_id": line["lot_id"],
                                "reason": "Nope nope"}).status_code == 403, role
        assert Fresh(make_client, role).post(
            f"{Q}/holds/{h['hold_id']}/release", json={"note": "Nope nope"}).status_code == 403
    other = make_client("ADMIN").post("/api/v1/org/units", json={"code": code(), "name": "P2"}).json()
    away = Fresh(make_client, "QA", subject="u-hold-out", unit_ids=[other["unit_id"]])
    assert away.get(f"{Q}/holds/{h['hold_id']}").status_code == 404
    assert away.post(f"{Q}/holds/{h['hold_id']}/release", json={"note": "Not mine"}).status_code == 404
    assert away.get(f"{Q}/holds").json()["total"] == 0
    assert hold(make_client, "STOCK_LOT", str(uuid4())).status_code == 404


def test_a_held_compound_batch_is_never_allocated(make_client, api_database):
    w = stocked(make_client)
    compound = make_compound(make_client, w)
    batch = weigh(make_client, w, compound, "100").json()["batch_id"]
    to_awaiting_qa(make_client, api_database, batch)
    assert Fresh(make_client, "QA").post(f"{M}/batches/{batch}/qa", json={
        "result": "PASSED", "hardness": "60"}).status_code == 200
    avail = lambda: Fresh(make_client, "PROD").get(  # noqa: E731
        f"{M}/compounds/{compound['compound_id']}/available").json()["ready_kg"]
    assert float(avail()) == 100
    h = hold(make_client, "COMPOUND_BATCH", batch).json()
    assert Fresh(make_client, "PROD").get(f"{M}/batches/{batch}").json()["status"] == "ON_HOLD"
    assert float(avail()) == 0
    assert hold(make_client, "COMPOUND_BATCH", batch).status_code == 409
    assert release(make_client, h["hold_id"]).status_code == 200
    assert Fresh(make_client, "PROD").get(f"{M}/batches/{batch}").json()["status"] == "READY"
    assert float(avail()) == 100


def test_a_held_production_lot_cannot_move_and_held_inserts_cannot_be_drawn(
        make_client, api_database):
    w = shop(make_client, api_database)
    insert_lot = w["metal_lot"]["prod_lot_id"]
    wo = work_order(make_client, w, w["fg"], "100")
    h = hold(make_client, "PROD_LOT", insert_lot).json()
    assert Fresh(make_client, "PROD").get(f"{M}/lots/{insert_lot}").json()["status"] == "HOLD"
    refused = start(make_client, w, wo, "10")
    assert refused.status_code == 409 and "metal inserts" in refused.json()["detail"]
    assert release(make_client, h["hold_id"]).status_code == 200
    assert Fresh(make_client, "PROD").get(f"{M}/lots/{insert_lot}").json()["status"] == "READY"
    lot = start(make_client, w, wo, "10")
    assert lot.status_code == 201
    lot_id = lot.json()["prod_lot_id"]
    second = hold(make_client, "PROD_LOT", lot_id).json()
    stuck = Fresh(make_client, "PROD").post(f"{M}/lots/{lot_id}/move", json={})
    assert stuck.status_code == 409 and "hold" in stuck.json()["detail"]
    assert Fresh(make_client, "PROD").post(f"{M}/lots/{lot_id}/rework", json={
        "quantity": "1", "reason": "Trim it"}).status_code == 409
    assert release(make_client, second["hold_id"]).status_code == 200
    moved = run_route(make_client, lot_id, stop_at="QA_MOULDING")
    assert moved["stage_code"] == "QA_MOULDING"


# ------------------------------------------------------------------ NCR and 8D
def raise_ncr(make_client, source_type, source_id, **extra):
    return Fresh(make_client, "QA").post(f"{Q}/ncrs", json={
        "source_type": source_type, "source_id": source_id, "title": "Wrong hardness found",
        "description": "Customer reported soft parts", "severity": "MAJOR", **extra})


def do_step(make_client, ncr_id, code_, text="Action taken and verified", **extra):
    return Fresh(make_client, "QA").patch(f"{Q}/ncrs/{ncr_id}/steps/{code_}", json={
        "action_text": text, "complete": True, **extra})


def finish_steps(make_client, ncr_id):
    for n in range(1, 9):
        assert do_step(make_client, ncr_id, f"D{n}", evidence_ref=f"file-{n}").status_code == 200


def test_an_ncr_with_a_hold_runs_the_8d_steps_and_scrap_removes_the_goods(make_client):
    w = world(make_client)
    line = good_lot(make_client, w)
    raised = raise_ncr(make_client, "GRN_LINE", line["grn_line_id"], hold=True)
    assert raised.status_code == 201, raised.text
    n = raised.json()
    assert n["ncr_no"].startswith("NCR-") and n["status"] == "OPEN" and n["severity"] == "MAJOR"
    assert [s["step_code"] for s in n["steps"]] == [f"D{i}" for i in range(1, 9)]
    assert n["steps"][3]["title"] == "Find the root cause" and n["open_hold_id"]
    assert n["source_label"].endswith(line["lot_no"])
    assert lot_status(make_client, line["lot_id"]) == "QUARANTINE"
    assert do_step(make_client, n["ncr_id"], "D2").status_code == 409  # D1 not done yet
    assert Fresh(make_client, "QA").patch(f"{Q}/ncrs/{n['ncr_id']}/steps/D1", json={
        "complete": True}).status_code == 422  # nothing described
    first = do_step(make_client, n["ncr_id"], "D1", owner_user_id=None)
    assert first.status_code == 200 and first.json()["status"] == "IN_PROGRESS"
    assert first.json()["steps"][0]["completed_by_name"] and first.json()["steps"][0][
        "completed_at"]
    assert do_step(make_client, n["ncr_id"], "D1").status_code == 409  # done already
    early = Fresh(make_client, "QA").post(f"{Q}/ncrs/{n['ncr_id']}/close")
    assert early.status_code == 409 and "D2" in early.json()["detail"]
    for step in ("D2", "D3", "D4", "D5", "D6", "D7", "D8"):
        assert do_step(make_client, n["ncr_id"], step, evidence_ref="photo-1").status_code == 200
    nodisp = Fresh(make_client, "QA").post(f"{Q}/ncrs/{n['ncr_id']}/close")
    assert nodisp.status_code == 409 and "disposition" in nodisp.json()["detail"]
    disp = Fresh(make_client, "QA").post(f"{Q}/ncrs/{n['ncr_id']}/disposition", json={
        "disposition": "SCRAP", "note": "Out of spec, not reworkable"})
    assert disp.status_code == 200 and disp.json()["disposition"] == "SCRAP"
    assert disp.json()["open_hold_id"] is None and disp.json()["disposition_by_name"]
    assert lot_status(make_client, line["lot_id"]) == "BLOCKED"
    assert Fresh(make_client, "QA").post(f"{Q}/ncrs/{n['ncr_id']}/disposition", json={
        "disposition": "REWORK", "note": "Changed my mind"}).status_code == 409
    closed = Fresh(make_client, "QA").post(f"{Q}/ncrs/{n['ncr_id']}/close")
    assert closed.status_code == 200 and closed.json()["status"] == "CLOSED"
    assert closed.json()["closed_at"]
    assert do_step(make_client, n["ncr_id"], "D1").status_code == 409  # closed
    assert Fresh(make_client, "QA").post(f"{Q}/ncrs/{n['ncr_id']}/cancel").status_code == 409


def test_use_as_is_releases_the_hold_and_the_ncr_cannot_close_with_a_hold_open(make_client):
    w = world(make_client)
    line = good_lot(make_client, w)
    n = raise_ncr(make_client, "STOCK_LOT", line["lot_id"], hold=True).json()
    qa = Fresh(make_client, "QA")
    cancel = qa.post(f"{Q}/ncrs/{n['ncr_id']}/cancel")
    assert cancel.status_code == 409 and "Release the hold" in cancel.json()["detail"]
    finish_steps(make_client, n["ncr_id"])
    stuck = qa.post(f"{Q}/ncrs/{n['ncr_id']}/close")
    assert stuck.status_code == 409  # no disposition yet
    qa.post(f"{Q}/ncrs/{n['ncr_id']}/disposition", json={
        "disposition": "USE_AS_IS", "note": "Within customer concession"})
    assert lot_status(make_client, line["lot_id"]) == "AVAILABLE"
    assert qa.post(f"{Q}/ncrs/{n['ncr_id']}/close").json()["status"] == "CLOSED"
    assert issue(make_client, w).status_code == 201


def test_a_hold_released_by_hand_lets_the_ncr_close_and_scrap_applies_without_a_hold(
        make_client, api_database):
    w = shop(make_client, api_database)
    wo = work_order(make_client, w, w["fg"], "100")
    lot_id = start(make_client, w, wo, "10").json()["prod_lot_id"]
    n = raise_ncr(make_client, "PROD_LOT", lot_id).json()  # no hold requested
    qa = Fresh(make_client, "QA")
    assert qa.post(f"{Q}/ncrs/{n['ncr_id']}/disposition", json={
        "disposition": "SCRAP", "note": "Mould damage"}).status_code == 200
    assert Fresh(make_client, "PROD").get(f"{M}/lots/{lot_id}").json()["status"] == "SCRAPPED"
    batch = w["batch"]
    nb = raise_ncr(make_client, "COMPOUND_BATCH", batch, hold=True).json()
    assert Fresh(make_client, "PROD").get(f"{M}/batches/{batch}").json()["status"] == "ON_HOLD"
    qa.post(f"{Q}/ncrs/{nb['ncr_id']}/disposition", json={
        "disposition": "SCRAP", "note": "Contaminated"})
    assert Fresh(make_client, "PROD").get(f"{M}/batches/{batch}").json()["status"] == "REJECTED"


def test_supplier_and_customer_ncrs_have_no_goods_to_hold(make_client):
    w = world(make_client)
    vendor = w["vendor"]["vendor_id"]
    qa = Fresh(make_client, "QA")
    sup = raise_ncr(make_client, "SUPPLIER", vendor)
    assert sup.status_code == 201 and sup.json()["source_label"] and sup.json()["unit_id"] is None
    assert raise_ncr(make_client, "SUPPLIER", vendor, hold=True).status_code == 422
    assert qa.post(f"{Q}/ncrs/{sup.json()['ncr_id']}/disposition", json={
        "disposition": "SCRAP", "note": "Not goods"}).status_code == 422
    finish_steps(make_client, sup.json()["ncr_id"])
    assert qa.post(f"{Q}/ncrs/{sup.json()['ncr_id']}/close").json()["status"] == "CLOSED"
    customer = Fresh(make_client, "SALES").post("/api/v1/parties/customers", json={
        "code": "C" + code()[:6], "name": "Maruti Suzuki"}).json()
    complaint = raise_ncr(make_client, "CUSTOMER_COMPLAINT", customer["customer_id"])
    assert complaint.status_code == 201
    complaints = qa.get(f"{Q}/ncrs?source_type=CUSTOMER_COMPLAINT&limit=200").json()["items"]
    assert complaint.json()["ncr_id"] in {x["ncr_id"] for x in complaints}
    closed = qa.get(f"{Q}/ncrs?status=closed&limit=200").json()["items"]
    assert sup.json()["ncr_id"] in {x["ncr_id"] for x in closed}
    assert qa.post(f"{Q}/ncrs/{complaint.json()['ncr_id']}/cancel").json()["status"] == "CANCELLED"


def test_ncr_validation_permissions_and_scope(make_client):
    w = world(make_client)
    line = good_lot(make_client, w)
    base = {"source_type": "STOCK_LOT", "source_id": line["lot_id"], "title": "Dented drums"}
    qa = Fresh(make_client, "QA")
    post = lambda **b: qa.post(f"{Q}/ncrs", json={**base, **b}).status_code  # noqa: E731
    assert post(title="x") == 422 and post(severity="HUGE") == 422
    assert post(source_id=str(uuid4())) == 404
    assert post(source_type="SUPPLIER", source_id=str(uuid4())) == 422
    assert post(owner_user_id=str(uuid4())) == 422
    assert post(source_type="WEATHER") == 422
    for role in ("SALES", "PURCHASE", "STORES"):
        assert Fresh(make_client, role).post(f"{Q}/ncrs", json=base).status_code == 403, role
    made = qa.post(f"{Q}/ncrs", json=base).json()
    other = make_client("ADMIN").post("/api/v1/org/units", json={"code": code(), "name": "P2"}).json()
    away = Fresh(make_client, "QA", subject="u-ncr-out", unit_ids=[other["unit_id"]])
    assert away.get(f"{Q}/ncrs/{made['ncr_id']}").status_code == 404
    assert away.post(f"{Q}/ncrs/{made['ncr_id']}/close").status_code == 404
    assert away.post(f"{Q}/ncrs", json=base).status_code == 404
    assert made["ncr_id"] not in {x["ncr_id"] for x in away.get(f"{Q}/ncrs").json()["items"]}
    bad_step = qa.patch(f"{Q}/ncrs/{made['ncr_id']}/steps/D9", json={"complete": False})
    assert bad_step.status_code == 422
    assert qa.get(f"{Q}/ncrs/{uuid4()}").status_code == 404
