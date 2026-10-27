"""Work orders, production lots through their routes, consumption rules, packing and tracing."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from .test_bom import item as make_item
from .test_bom import release
from .test_compound_batches import (
    INV,
    Fresh,
    M,
    make_compound,
    ready_batch,
    reconciles,
    stocked,
    weigh,
)
from .test_inventory_receipt import code

pytestmark = pytest.mark.usefixtures("api_database")
P = "/api/v1/product"


def approved_bom(make_client, fg, lines):
    made = Fresh(make_client, "PROD").post(f"{P}/boms", json={
        "item_id": fg["item_id"], "lines": [
            {"component_item_id": c["item_id"], "quantity_per": q, "scrap_factor": s}
            for c, q, s in lines]})
    assert made.status_code == 201, made.text
    return release(make_client, made.json())


def work_order(make_client, w, item, qty="100", release_it=True, **extra):
    made = Fresh(make_client, "PROD").post(f"{M}/work-orders", json={
        "unit_id": w["unit"]["unit_id"], "item_id": item["item_id"], "planned_qty": qty, **extra})
    assert made.status_code == 201, made.text
    if release_it:
        done = Fresh(make_client, "PROD").post(f"{M}/work-orders/{made.json()['work_order_id']}/release")
        assert done.status_code == 200, done.text
        return done.json()
    return made.json()


def start(make_client, w, wo, qty, store=True):
    body = {"work_order_id": wo["work_order_id"], "quantity": qty}
    if store:
        body["store_id"] = w["store"]["store_id"]
    return Fresh(make_client, "PROD").post(f"{M}/lots", json=body)


def run_route(make_client, lot_id, stop_at=None):
    """Move a lot along its route as the right role at each stage, until the end or stop_at."""
    while True:
        lot = Fresh(make_client, "PROD").get(f"{M}/lots/{lot_id}").json()
        if lot["is_end"] or lot["stage_code"] == stop_at or lot["status"] != "IN_PROCESS":
            return lot
        role = "QA" if lot["is_qa_gate"] else "PROD"
        moved = Fresh(make_client, role).post(f"{M}/lots/{lot_id}/move", json={})
        assert moved.status_code == 200, moved.text


def shop(make_client, api_database, compound_kg="100", inserts="200"):
    """A plant with raw material, a Ready compound batch, a ready metal insert lot and a bill."""
    w = stocked(make_client)
    admin = make_client("ADMIN")
    cmpd_item = make_item(admin, "WIP", "KG")
    compound = make_compound(make_client, w, output_item_id=cmpd_item["item_id"])
    batch = ready_batch(make_client, api_database, w, compound, compound_kg) if compound_kg else None
    insert = make_item(Fresh(make_client, "ADMIN"), "WIP", "NOS", route_code="METAL")
    approved_bom(make_client, insert, [(w["items"][0], "1", "0")])
    metal_lot = None
    if inserts:
        metal_wo = work_order(make_client, w, insert, inserts)
        metal_lot = start(make_client, w, metal_wo, inserts).json()
        assert run_route(make_client, metal_lot["prod_lot_id"])["status"] == "READY"
    fg = make_item(Fresh(make_client, "ADMIN"), "FG", "NOS", route_code="MOULD")
    approved_bom(make_client, fg, [(cmpd_item, "0.1", "0"), (insert, "1", "0"),
                                   (w["items"][1], "0.05", "0")])
    w.update(compound=compound, batch=batch, insert=insert, metal_lot=metal_lot, fg=fg)
    return w


def stock_on_hand(make_client, w, item):
    got = Fresh(make_client, "STORES").get(
        f"{INV}/stock?store_id={w['store']['store_id']}&item_id={item['item_id']}").json()
    return sum(float(r["on_hand"]) for r in got["items"])


# ------------------------------------------------------------------ work orders
def test_work_order_takes_the_bom_in_force_and_the_item_route(make_client, api_database):
    w = shop(make_client, api_database, compound_kg=None, inserts=None)
    wo = work_order(make_client, w, w["fg"], "100", release_it=False)
    assert wo["work_order_no"].startswith("WO-") and wo["status"] == "PLANNED"
    assert wo["route_code"] == "MOULD" and wo["bom_version"] == 1
    assert {m["kind"] for m in wo["materials"]} == {"COMPOUND", "INSERT", "MATERIAL"}
    assert sorted(float(m["required_qty"]) for m in wo["materials"]) == [5, 10, 100]
    assert float(wo["started_qty"]) == 0
    prod = Fresh(make_client, "PROD")
    assert prod.post(f"{M}/work-orders/{wo['work_order_id']}/release").json()["status"] == "RELEASED"
    assert prod.post(f"{M}/work-orders/{wo['work_order_id']}/release").status_code == 409
    assert prod.post(f"{M}/work-orders/{wo['work_order_id']}/cancel").json()["status"] == "CANCELLED"
    assert prod.get(f"{M}/work-orders?status=cancelled").json()["total"] >= 1
    assert prod.get(f"{M}/work-orders/{wo['work_order_id']}").status_code == 200


def test_scrap_factor_raises_the_required_material(make_client, api_database):
    w = stocked(make_client)
    fg = make_item(Fresh(make_client, "ADMIN"), "FG", "NOS", route_code="MOULD")
    approved_bom(make_client, fg, [(w["items"][0], "2", "0.05")])
    wo = work_order(make_client, w, fg, "100", release_it=False)
    assert float(wo["materials"][0]["required_qty"]) == 210  # 2 per piece plus 5% scrap


def test_work_order_validation_and_permissions(make_client, api_database):
    w = shop(make_client, api_database, compound_kg=None, inserts=None)
    prod = Fresh(make_client, "PROD")
    post = lambda **b: prod.post(f"{M}/work-orders", json={  # noqa: E731
        "unit_id": w["unit"]["unit_id"], "item_id": w["fg"]["item_id"], "planned_qty": "10",
        **b}).status_code
    assert post(planned_qty="0") == 422 and post(item_id=str(uuid4())) == 422
    assert post(unit_id=str(uuid4())) == 422
    assert post(planned_start="2026-12-10", planned_finish="2026-12-01") == 422
    rm = make_item(Fresh(make_client, "ADMIN"), "RM", "KG")
    assert post(item_id=rm["item_id"]) == 422  # raw material is not made
    no_route = make_item(Fresh(make_client, "ADMIN"), "FG", "NOS")
    assert post(item_id=no_route["item_id"]) == 422
    no_bom = make_item(Fresh(make_client, "ADMIN"), "FG", "NOS", route_code="MOULD")
    refused = post(item_id=no_bom["item_id"])
    assert refused == 422
    for role in ("SALES", "QA", "PURCHASE", "STORES"):
        assert Fresh(make_client, role).post(f"{M}/work-orders", json={
            "unit_id": w["unit"]["unit_id"], "item_id": w["fg"]["item_id"],
            "planned_qty": "1"}).status_code == 403, role
    future = (date.today() + timedelta(days=3)).isoformat()
    assert post(planned_start=future, planned_finish=future) == 201


# ------------------------------------------------------------------ the moulding flow
def test_a_moulded_lot_consumes_compound_insert_and_material_then_packs_to_fg(
        make_client, api_database):
    w = shop(make_client, api_database)
    wo = work_order(make_client, w, w["fg"], "100")
    store = w["store"]["store_id"]
    before = stock_on_hand(make_client, w, w["items"][1])
    made = start(make_client, w, wo, "100")
    assert made.status_code == 201, made.text
    lot = made.json()
    assert lot["lot_no"].startswith("PL-") and lot["stage_code"] == "MOULDING"
    kinds = {s["consumption_type"]: float(s["quantity"]) for s in lot["sources"]}
    assert kinds == {"COMPOUND_BATCH": 10, "METAL_PROD_LOT": 100, "RAW_MATERIAL": 5}
    assert stock_on_hand(make_client, w, w["items"][1]) == before - 5
    batch = Fresh(make_client, "PROD").get(f"{M}/batches/{w['batch']}").json()
    assert float(batch["remaining_kg"]) == 90
    inserts = Fresh(make_client, "PROD").get(f"{M}/lots/{w['metal_lot']['prod_lot_id']}").json()
    assert float(inserts["quantity"]) == 100 and inserts["status"] == "READY"
    assert Fresh(make_client, "PROD").get(f"{M}/work-orders/{wo['work_order_id']}").json()[
        "status"] == "IN_PROGRESS"
    # moulding is done by route.move; leaving the QA gate needs qa.approve
    moved = Fresh(make_client, "PROD").post(f"{M}/lots/{lot['prod_lot_id']}/move", json={})
    assert moved.status_code == 200 and moved.json()["stage_code"] == "QA_MOULDING"
    denied = Fresh(make_client, "PROD").post(f"{M}/lots/{lot['prod_lot_id']}/move", json={})
    assert denied.status_code == 403 and "qa.approve" in denied.json()["detail"]
    over = Fresh(make_client, "QA").post(f"{M}/lots/{lot['prod_lot_id']}/move",
                                         json={"rejects": "101"})
    assert over.status_code == 422
    passed = Fresh(make_client, "QA").post(f"{M}/lots/{lot['prod_lot_id']}/move", json={
        "rejects": "5", "notes": "5 flash defects"})
    assert passed.status_code == 200 and passed.json()["stage_code"] == "PACKING"
    assert float(passed.json()["quantity"]) == 95
    to_fg = Fresh(make_client, "PROD").post(f"{M}/lots/{lot['prod_lot_id']}/move", json={})
    assert to_fg.status_code == 409 and "packing" in to_fg.json()["detail"]
    fg_store = make_client("ADMIN").post("/api/v1/org/stores", json={
        "unit_id": w["unit"]["unit_id"], "code": "FG", "name": "Finished goods",
        "store_type": "FINISHED_GOODS"}).json()
    packed = Fresh(make_client, "PROD").post(f"{M}/lots/{lot['prod_lot_id']}/pack",
                                             json={"fg_store_id": fg_store["store_id"]})
    assert packed.status_code == 200, packed.text
    done = packed.json()
    assert done["status"] == "COMPLETED" and done["stage_code"] == "FG_STORE" and done["is_end"]
    assert done["packing"]["label_code"].startswith("PK-") and done["packing"]["fg_lot_no"] == lot[
        "lot_no"]
    assert float(done["packing"]["packed_qty"]) == 95
    fg_stock = Fresh(make_client, "STORES").get(
        f"{INV}/stock?store_id={fg_store['store_id']}").json()["items"]
    assert [(r["item_code"], float(r["on_hand"])) for r in fg_stock] == [(w["fg"]["code"], 95)]
    stages = [(m["from_stage"], m["to_stage"], float(m["rejects"])) for m in done["moves"]]
    assert stages == [(None, "MOULDING", 0), ("MOULDING", "QA_MOULDING", 0),
                      ("QA_MOULDING", "PACKING", 5), ("PACKING", "FG_STORE", 0)]
    assert Fresh(make_client, "PROD").post(f"{M}/lots/{lot['prod_lot_id']}/move", json={}
                                           ).status_code == 409  # completed
    summary = Fresh(make_client, "PROD").get(f"{M}/work-orders/{wo['work_order_id']}").json()
    assert float(summary["started_qty"]) == 100 and float(summary["packed_qty"]) == 95
    assert Fresh(make_client, "PROD").post(
        f"{M}/work-orders/{wo['work_order_id']}/complete").json()["status"] == "COMPLETED"
    reconciles(make_client)
    assert store  # the raw-material store is untouched apart from the issue above


def test_a_short_compound_or_insert_refuses_the_whole_lot(make_client, api_database):
    w = shop(make_client, api_database, compound_kg="8", inserts="200")  # 100 pcs need 10 kg
    wo = work_order(make_client, w, w["fg"], "100")
    before = stock_on_hand(make_client, w, w["items"][1])
    refused = start(make_client, w, wo, "100")
    assert refused.status_code == 409 and "Not enough ready compound" in refused.json()["detail"]
    assert Fresh(make_client, "PROD").get(f"{M}/lots?work_order_id={wo['work_order_id']}").json()[
        "total"] == 0
    assert stock_on_hand(make_client, w, w["items"][1]) == before  # nothing was issued
    batch = Fresh(make_client, "PROD").get(f"{M}/batches/{w['batch']}").json()
    assert float(batch["remaining_kg"]) == 8
    assert start(make_client, w, wo, "80").status_code == 201  # 8 kg covers 80 pieces
    assert Fresh(make_client, "PROD").get(f"{M}/work-orders/{wo['work_order_id']}").json()[
        "status"] == "IN_PROGRESS"


def test_no_inserts_ready_means_no_moulding_lot(make_client, api_database):
    w = shop(make_client, api_database, inserts=None)
    wo = work_order(make_client, w, w["fg"], "10")
    refused = start(make_client, w, wo, "10")
    assert refused.status_code == 409 and "metal inserts" in refused.json()["detail"]


def test_unmatured_or_unapproved_compound_cannot_start_a_lot(make_client, api_database):
    w = shop(make_client, api_database, compound_kg=None)
    wo = work_order(make_client, w, w["fg"], "10")
    batch = weigh(make_client, w, w["compound"], "100").json()  # only weighed
    assert start(make_client, w, wo, "10").status_code == 409
    from .test_compound_batches import to_awaiting_qa
    to_awaiting_qa(make_client, api_database, batch["batch_id"], age="2")  # still maturing
    assert start(make_client, w, wo, "10").status_code == 409


def test_lot_rules_validation_and_scope(make_client, api_database):
    w = shop(make_client, api_database)
    wo = work_order(make_client, w, w["fg"], "100")
    planned = work_order(make_client, w, w["fg"], "100", release_it=False)
    assert start(make_client, w, planned, "10").status_code == 409  # not released
    assert start(make_client, w, wo, "10", store=False).status_code == 422  # needs a store
    assert start(make_client, w, wo, "0").status_code == 422
    assert start(make_client, w, wo, "101").status_code == 409  # more than planned
    other = make_client("ADMIN").post("/api/v1/org/units", json={"code": code(), "name": "P2"}).json()
    foreign = make_client("ADMIN").post("/api/v1/org/stores", json={
        "unit_id": other["unit_id"], "code": "RM", "name": "Other", "store_type": "RAW_MATERIAL"}
    ).json()
    mismatch = Fresh(make_client, "PROD").post(f"{M}/lots", json={
        "work_order_id": wo["work_order_id"], "quantity": "10", "store_id": foreign["store_id"]})
    assert mismatch.status_code == 422
    for role in ("SALES", "PURCHASE", "ACCOUNTS"):
        assert start_as(make_client, role, wo, w).status_code == 403, role
    lot = start(make_client, w, wo, "20").json()
    away = Fresh(make_client, "PROD", subject="u-lot-out", unit_ids=[other["unit_id"]])
    assert away.get(f"{M}/lots/{lot['prod_lot_id']}").status_code == 404
    assert away.post(f"{M}/lots/{lot['prod_lot_id']}/move", json={}).status_code == 404
    assert away.get(f"{M}/work-orders/{wo['work_order_id']}").status_code == 404
    assert away.get(f"{M}/lots").json()["total"] == 0
    # scrapping the whole lot at a gate ends it
    run_route(make_client, lot["prod_lot_id"], stop_at="QA_MOULDING")
    gone = Fresh(make_client, "QA").post(f"{M}/lots/{lot['prod_lot_id']}/move",
                                         json={"rejects": "20"})
    assert gone.status_code == 200 and gone.json()["status"] == "SCRAPPED"
    assert Fresh(make_client, "QA").post(f"{M}/lots/{lot['prod_lot_id']}/move", json={}
                                         ).status_code == 409
    cancel = Fresh(make_client, "PROD").post(f"{M}/work-orders/{wo['work_order_id']}/cancel")
    assert cancel.status_code == 409  # lots already started


def start_as(make_client, role, wo, w):
    return Fresh(make_client, role).post(f"{M}/lots", json={
        "work_order_id": wo["work_order_id"], "quantity": "1", "store_id": w["store"]["store_id"]})


def test_packing_only_at_the_packing_stage(make_client, api_database):
    w = shop(make_client, api_database)
    wo = work_order(make_client, w, w["fg"], "10")
    lot = start(make_client, w, wo, "10").json()
    fg = make_client("ADMIN").post("/api/v1/org/stores", json={
        "unit_id": w["unit"]["unit_id"], "code": "FG", "name": "FG", "store_type": "FINISHED_GOODS"}
    ).json()
    pack = lambda: Fresh(make_client, "PROD").post(  # noqa: E731
        f"{M}/lots/{lot['prod_lot_id']}/pack", json={"fg_store_id": fg["store_id"]})
    assert pack().status_code == 409  # still moulding
    run_route(make_client, lot["prod_lot_id"], stop_at="PACKING")
    assert Fresh(make_client, "QA").post(f"{M}/lots/{lot['prod_lot_id']}/pack", json={
        "fg_store_id": fg["store_id"]}).status_code == 403  # QA cannot pack: no route.move
    assert Fresh(make_client, "PROD").post(f"{M}/lots/{lot['prod_lot_id']}/pack", json={
        "fg_store_id": str(uuid4())}).status_code == 404
    done = pack()
    assert done.status_code == 200 and done.json()["status"] == "COMPLETED"
    assert pack().status_code == 409  # already packed


# ------------------------------------------------------------------ traceability
def test_a_finished_lot_traces_back_to_compound_batch_metal_lot_and_raw_material(
        make_client, api_database):
    w = shop(make_client, api_database)
    wo = work_order(make_client, w, w["fg"], "50")
    lot = start(make_client, w, wo, "50").json()
    trace = Fresh(make_client, "QA").get(f"{M}/lots/{lot['prod_lot_id']}/trace").json()
    assert trace["lot_no"] == lot["lot_no"] and trace["item_code"] == w["fg"]["code"]
    (batch,) = trace["compound_batches"]
    assert batch["batch_no"].startswith("BAT-") and Decimal(batch["quantity"]) == 5
    assert {r["item_code"] for r in batch["raw_material"]} == {
        w["items"][0]["code"], w["items"][1]["code"]}
    (metal,) = trace["metal_lots"]
    assert metal["lot_no"] == w["metal_lot"]["lot_no"] and Decimal(metal["quantity_used"]) == 50
    assert [r["item_code"] for r in metal["raw_material"]] == [w["items"][0]["code"]]
    assert [r["item_code"] for r in trace["raw_material"]] == [w["items"][1]["code"]]
    used = Fresh(make_client, "QA").get(f"{M}/batches/{w['batch']}/used-in").json()
    assert [u["lot_no"] for u in used] == [lot["lot_no"]]
    assert Fresh(make_client, "QA").get(f"{M}/lots/{uuid4()}/trace").status_code == 404
