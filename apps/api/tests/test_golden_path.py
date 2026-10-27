"""One end-to-end run across the modules built so far.

Purchase order, goods receipt, QA release, compound batch, metal insert lot, finished lot, QA gate,
packing into finished-goods stock, traceability, reconciliations and the audit trail. If a later
change breaks how the modules hand over to each other, this test shows it even when each module's
own tests still pass. When a new module joins the chain (sales, dispatch, finance), extend this test.
"""

import psycopg
import pytest

from .test_compound_batches import INV, Fresh, M, reconciles
from .test_inventory_receipt import code
from .test_work_orders import run_route, shop, start, work_order

pytestmark = pytest.mark.usefixtures("api_database")
Q = "/api/v1/quality"


def test_from_purchase_order_to_packed_finished_goods(make_client, api_database):
    w = shop(make_client, api_database)  # PO -> GRN -> QA -> batch (Ready) -> insert lot (Ready)
    wo = work_order(make_client, w, w["fg"], "100")
    lot = start(make_client, w, wo, "100").json()
    sources = {s["consumption_type"] for s in lot["sources"]}
    assert sources == {"COMPOUND_BATCH", "METAL_PROD_LOT", "RAW_MATERIAL"}

    # moulding, QA gate with rejects, packing into a finished-goods store
    at_gate = run_route(make_client, lot["prod_lot_id"], stop_at="QA_MOULDING")
    assert at_gate["stage_code"] == "QA_MOULDING"
    queue = Fresh(make_client, "QA").get(f"{Q}/queue?subject_type=PROD_LOT").json()
    assert lot["prod_lot_id"] in {q["subject_id"] for q in queue}
    passed = Fresh(make_client, "QA").post(f"{M}/lots/{lot['prod_lot_id']}/move",
                                           json={"rejects": "3"})
    assert passed.status_code == 200 and passed.json()["stage_code"] == "PACKING"
    fg_store = make_client("ADMIN").post("/api/v1/org/stores", json={
        "unit_id": w["unit"]["unit_id"], "code": "FG" + code()[:3], "name": "Finished goods",
        "store_type": "FINISHED_GOODS"}).json()
    packed = Fresh(make_client, "PROD").post(f"{M}/lots/{lot['prod_lot_id']}/pack",
                                             json={"fg_store_id": fg_store["store_id"]})
    assert packed.status_code == 200 and packed.json()["status"] == "COMPLETED"

    # the finished goods are in stock and can be issued like any other stock
    stock = Fresh(make_client, "STORES").get(
        f"{INV}/stock?store_id={fg_store['store_id']}").json()["items"]
    assert [(r["item_code"], float(r["on_hand"])) for r in stock] == [(w["fg"]["code"], 97)]
    issued = Fresh(make_client, "STORES").post(f"{INV}/issues", json={
        "store_id": fg_store["store_id"], "purpose": "Sample to customer",
        "lines": [{"item_id": w["fg"]["item_id"], "quantity": "7"}]})
    assert issued.status_code == 201

    # traceability goes back through compound batch and insert lot to the raw-material lots
    trace = Fresh(make_client, "QA").get(f"{M}/lots/{lot['prod_lot_id']}/trace").json()
    assert trace["compound_batches"][0]["raw_material"] and trace["metal_lots"][0]["raw_material"]
    assert trace["packing"]["label_code"].startswith("PK-")
    used = Fresh(make_client, "QA").get(f"{M}/batches/{w['batch']}/used-in").json()
    assert [u["lot_no"] for u in used] == [lot["lot_no"]]

    # nothing is out of balance: stock ledger, cost layers, production lots, material use
    reconciles(make_client)
    assert Fresh(make_client, "PROD").get(f"{M}/reconciliation").json()["ok"]
    variance = Fresh(make_client, "PROD").get(
        f"{M}/work-orders/{wo['work_order_id']}/material-variance").json()
    assert all(float(v["variance"]) == 0 for v in variance)
    waiting = Fresh(make_client, "QA").get(f"{Q}/queue?subject_type=PROD_LOT").json()
    assert lot["prod_lot_id"] not in {q["subject_id"] for q in waiting}

    # every step left an audit row
    with psycopg.connect(api_database["owner"]) as conn:
        actions = {r[0] for r in conn.execute(
            "select action from erp.audit_log where entity_id = any(%s)",
            ([lot["prod_lot_id"], wo["work_order_id"], w["batch"]],))}
    assert {"work_order.create", "prod_lot.start", "prod_lot.move", "prod_lot.pack",
            "batch.weigh", "batch.qa"} <= actions, actions
