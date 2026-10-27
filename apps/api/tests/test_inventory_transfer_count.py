"""Transfers between stores and plants, cycle counts with approval, and lot tracing."""

from uuid import uuid4

import pytest

from .test_inventory_issue import receive
from .test_inventory_receipt import INV, code, reconciles, world

pytestmark = pytest.mark.usefixtures("api_database")
A = "/api/v1/approvals"


class Fresh:
    """make_client closes the previous client, so build a new one for every request."""

    def __init__(self, make_client, role, **kw):
        self.make, self.role, self.kw = make_client, role, kw

    def __getattr__(self, verb):
        return lambda *a, **k: getattr(self.make(self.role, **self.kw), verb)(*a, **k)


def new_store(make_client, unit_id, name="FG"):
    store = make_client("ADMIN").post("/api/v1/org/stores", json={
        "unit_id": unit_id, "code": name + uuid4().hex[:4].upper(), "name": name,
        "store_type": "RAW_MATERIAL"})
    assert store.status_code == 201, store.text
    return store.json()


def new_plant(make_client):
    return make_client("ADMIN").post("/api/v1/org/units", json={"code": code(), "name": "P2"}).json()


def transfer(make_client, src, dst, lot, qty, role="STORES"):
    return make_client(role).post(f"{INV}/transfers", json={
        "from_store_id": src, "to_store_id": dst,
        "lines": [{"lot_id": lot["lot_id"], "quantity": qty}]})


def valuation(make_client, unit_id):
    rows_ = make_client("ACCOUNTS").get(f"{INV}/valuation?unit_id={unit_id}").json()
    return (float(rows_[0]["quantity"]), float(rows_[0]["value"])) if rows_ else (0.0, 0.0)


def on_hand(make_client, store_id, item_id):
    got = Fresh(make_client, "STORES").get(f"{INV}/stock?store_id={store_id}&item_id={item_id}").json()
    return {r["lot_no"]: float(r["on_hand"]) for r in got["items"]}


# ------------------------------------------------------------------ transfers
def test_transfer_between_stores_of_one_plant(make_client, api_database):
    w = world(make_client, qty="100", price="10")
    lot = receive(make_client, w, "100")
    src = w["store"]["store_id"]
    dst = new_store(make_client, w["unit"]["unit_id"])["store_id"]
    made = transfer(make_client, src, dst, lot, "40")
    assert made.status_code == 201, made.text
    t = made.json()
    assert t["transfer_no"].startswith("TRF-") and t["status"] == "DRAFT"
    stores = Fresh(make_client, "STORES")
    assert stores.post(f"{INV}/transfers/{t['transfer_id']}/receive").status_code == 409  # not sent
    sent = stores.post(f"{INV}/transfers/{t['transfer_id']}/dispatch")
    assert sent.status_code == 200 and sent.json()["status"] == "DISPATCHED"
    assert float(sent.json()["lines"][0]["unit_cost"]) == 10
    assert on_hand(make_client, src, w["item"]["item_id"]) == {lot["lot_no"]: 60}
    assert valuation(make_client, w["unit"]["unit_id"]) == (60, 600)  # 40 in transit
    assert stores.post(f"{INV}/transfers/{t['transfer_id']}/dispatch").status_code == 409
    got = stores.post(f"{INV}/transfers/{t['transfer_id']}/receive")
    assert got.status_code == 200 and got.json()["status"] == "RECEIVED"
    assert on_hand(make_client, dst, w["item"]["item_id"]) == {lot["lot_no"]: 40}
    assert valuation(make_client, w["unit"]["unit_id"]) == (100, 1000)
    reconciles(make_client)
    # the receiving lot keeps the same number, is available and carries the same expiry
    new_lot = stores.get(f"{INV}/stock?store_id={dst}").json()["items"][0]
    old_lot = stores.get(f"{INV}/lots/{lot['lot_id']}").json()
    assert new_lot["lot_status"] == "AVAILABLE" and new_lot["expiry_at"] == old_lot["expiry_at"]
    # a second transfer of the same lot tops up the same destination lot
    again = transfer(make_client, src, dst, lot, "10").json()
    stores.post(f"{INV}/transfers/{again['transfer_id']}/dispatch")
    stores.post(f"{INV}/transfers/{again['transfer_id']}/receive")
    assert on_hand(make_client, dst, w["item"]["item_id"]) == {lot["lot_no"]: 50}
    reconciles(make_client)
    page = stores.get(f"{INV}/transfers?status=received").json()
    assert page["total"] >= 2 and stores.get(f"{INV}/transfers/{t['transfer_id']}").status_code == 200


def test_transfer_between_plants_carries_the_cost(make_client):
    w = world(make_client, qty="100", price="25")
    lot = receive(make_client, w, "100")
    other = new_plant(make_client)
    dst = new_store(make_client, other["unit_id"])["store_id"]
    t = transfer(make_client, w["store"]["store_id"], dst, lot, "30").json()
    stores = Fresh(make_client, "STORES")
    stores.post(f"{INV}/transfers/{t['transfer_id']}/dispatch")
    assert valuation(make_client, w["unit"]["unit_id"]) == (70, 1750)
    assert valuation(make_client, other["unit_id"]) == (0, 0)  # in transit
    stores.post(f"{INV}/transfers/{t['transfer_id']}/receive")
    assert valuation(make_client, w["unit"]["unit_id"]) == (70, 1750)
    assert valuation(make_client, other["unit_id"]) == (30, 750)
    reconciles(make_client)
    # stock at the new plant can be issued at the carried cost
    issued = stores.post(f"{INV}/issues", json={
        "store_id": dst, "purpose": "Trial run",
        "lines": [{"item_id": w["item"]["item_id"], "quantity": "10"}]})
    assert issued.status_code == 201 and float(issued.json()["total_cost"]) == 250


def test_transfer_rules_and_permissions(make_client, api_database):
    w = world(make_client, qty="200")
    lot = receive(make_client, w, "100")
    src = w["store"]["store_id"]
    dst = new_store(make_client, w["unit"]["unit_id"])["store_id"]
    assert transfer(make_client, src, src, lot, "1").status_code == 422
    assert transfer(make_client, dst, src, lot, "1").status_code == 422  # lot is not in that store
    assert transfer(make_client, src, str(uuid4()), lot, "1").status_code == 422
    assert transfer(make_client, src, dst, lot, "0").status_code == 422
    twice = Fresh(make_client, "STORES").post(f"{INV}/transfers", json={
        "from_store_id": src, "to_store_id": dst,
        "lines": [{"lot_id": lot["lot_id"], "quantity": "1"}] * 2})
    assert twice.status_code == 422
    for role in ("PURCHASE", "SALES", "QA", "OPERATOR"):
        assert transfer(make_client, src, dst, lot, "1", role).status_code == 403, role
    # more than the lot holds: refused at dispatch, nothing moves
    big = transfer(make_client, src, dst, lot, "101").json()
    refused = Fresh(make_client, "STORES").post(f"{INV}/transfers/{big['transfer_id']}/dispatch")
    assert refused.status_code == 409 and "Not enough stock" in refused.json()["detail"]
    assert on_hand(make_client, src, w["item"]["item_id"]) == {lot["lot_no"]: 100}
    # a quarantined or expired lot cannot be sent
    ok = transfer(make_client, src, dst, lot, "5").json()
    make_client("QA").post(f"{INV}/lots/{lot['lot_id']}/quarantine", json={"reason": "Complaint"})
    assert make_client("STORES").post(
        f"{INV}/transfers/{ok['transfer_id']}/dispatch").status_code == 409
    make_client("QA").post(f"{INV}/lots/{lot['lot_id']}/unquarantine", json={"reason": "Cleared"})
    cancelled = Fresh(make_client, "STORES").post(f"{INV}/transfers/{ok['transfer_id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    assert make_client("STORES").post(
        f"{INV}/transfers/{ok['transfer_id']}/dispatch").status_code == 409
    reconciles(make_client)


def test_transfer_plant_scope(make_client):
    w = world(make_client)
    lot = receive(make_client, w, "100")
    other = new_plant(make_client)
    dst = new_store(make_client, other["unit_id"])["store_id"]
    t = transfer(make_client, w["store"]["store_id"], dst, lot, "5").json()
    outsider = new_plant(make_client)
    stranger = Fresh(make_client, "STORES", subject="u-stranger-trf", unit_ids=[outsider["unit_id"]])
    assert stranger.get(f"{INV}/transfers/{t['transfer_id']}").status_code == 404
    assert stranger.post(f"{INV}/transfers/{t['transfer_id']}/dispatch").status_code == 404
    receiver = Fresh(make_client, "STORES", subject="u-receiver-trf", unit_ids=[other["unit_id"]])
    assert receiver.get(f"{INV}/transfers/{t['transfer_id']}").status_code == 200  # sees its end
    assert receiver.post(f"{INV}/transfers/{t['transfer_id']}/dispatch").status_code == 404
    make_client("STORES").post(f"{INV}/transfers/{t['transfer_id']}/dispatch")
    assert make_client("STORES", subject="u-stranger-trf", unit_ids=[outsider["unit_id"]]).post(
        f"{INV}/transfers/{t['transfer_id']}/receive").status_code == 404
    receiver = Fresh(make_client, "STORES", subject="u-receiver-trf", unit_ids=[other["unit_id"]])
    assert receiver.post(f"{INV}/transfers/{t['transfer_id']}/receive").status_code == 200
    reconciles(make_client)


# ------------------------------------------------------------------ counts
def start_count(make_client, store_id, role="STORES"):
    made = make_client(role).post(f"{INV}/counts", json={"store_id": store_id})
    assert made.status_code == 201, made.text
    return made.json()


def count_all(make_client, c, quantities):
    body = {"lines": [{"count_line_id": ln["count_line_id"], "counted_qty": q}
                      for ln, q in zip(c["lines"], quantities, strict=True)]}
    done = Fresh(make_client, "STORES").put(f"{INV}/counts/{c['count_id']}/lines", json=body)
    assert done.status_code == 200, done.text
    return done.json()


def approval_of(make_client, count_id, role="PLANT_HEAD"):
    inbox = make_client(role).get(f"{A}?scope=mine&state=PENDING&limit=200").json()["items"]
    return next(a["approval_id"] for a in inbox if a["document_id"] == count_id)


def decide(make_client, approval_id, role, decision="APPROVE"):
    note = "Recount it" if decision == "REJECT" else None
    r = make_client(role).post(f"{A}/{approval_id}/decide", json={"decision": decision, "note": note})
    assert r.status_code == 200, r.text
    return r.json()


def test_count_without_differences_posts_at_once(make_client):
    w = world(make_client)
    receive(make_client, w, "100")
    c = start_count(make_client, w["store"]["store_id"])
    assert c["count_no"].startswith("CNT-") and c["status"] == "DRAFT"
    assert c["lines"][0]["system_qty"] == "100.000" and c["lines"][0]["counted_qty"] is None
    stores = Fresh(make_client, "STORES")
    early = stores.post(f"{INV}/counts/{c['count_id']}/submit")
    assert early.status_code == 422 and "not been counted" in early.json()["detail"]
    count_all(make_client, c, ["100"])
    done = stores.post(f"{INV}/counts/{c['count_id']}/submit")
    assert done.status_code == 200 and done.json()["status"] == "POSTED"
    reconciles(make_client)


def test_count_with_differences_needs_approval_and_then_corrects_the_books(make_client):
    w = world(make_client, qty="200", price="10")
    first = receive(make_client, w, "100")
    second = receive(make_client, w, "100")
    c = start_count(make_client, w["store"]["store_id"])
    by_lot = {ln["lot_no"]: n for n, ln in enumerate(c["lines"])}
    quantities = ["0", "0"]
    quantities[by_lot[first["lot_no"]]] = "92"  # 8 missing
    quantities[by_lot[second["lot_no"]]] = "103"  # 3 found
    counted = count_all(make_client, c, quantities)
    assert sorted(float(ln["variance"]) for ln in counted["lines"]) == [-8, 3]
    sent = Fresh(make_client, "STORES").post(f"{INV}/counts/{c['count_id']}/submit").json()
    assert sent["status"] == "PENDING_APPROVAL" and sent["submitted_by_name"]
    assert on_hand(make_client, w["store"]["store_id"], w["item"]["item_id"]) == {
        first["lot_no"]: 100, second["lot_no"]: 100}  # nothing changes before approval
    stores = Fresh(make_client, "STORES")
    assert stores.put(f"{INV}/counts/{c['count_id']}/lines", json={"lines": [{
        "count_line_id": c["lines"][0]["count_line_id"], "counted_qty": "1"}]}).status_code == 409
    approval = approval_of(make_client, c["count_id"])
    decide(make_client, approval, "PLANT_HEAD")
    assert stores.get(f"{INV}/counts/{c['count_id']}").json()["status"] == "PENDING_APPROVAL"
    final = decide(make_client, approval, "ACCOUNTS")
    assert final["status"] == "APPROVED"
    assert stores.get(f"{INV}/counts/{c['count_id']}").json()["status"] == "POSTED"
    assert on_hand(make_client, w["store"]["store_id"], w["item"]["item_id"]) == {
        first["lot_no"]: 92, second["lot_no"]: 103}
    assert valuation(make_client, w["unit"]["unit_id"]) == (195, 1950)  # surplus valued at last cost
    reconciles(make_client)
    # the adjustments show in the lot's history
    trace = stores.get(f"{INV}/lots/{first['lot_id']}/trace").json()
    assert trace["moves"][-1]["movement_type"] == "COUNT_ADJUST"
    assert trace["moves"][-1]["document_no"] == c["count_no"]


def test_rejected_count_changes_nothing_and_can_be_recounted(make_client):
    w = world(make_client)
    receive(make_client, w, "100")
    c = start_count(make_client, w["store"]["store_id"])
    count_all(make_client, c, ["70"])
    stores = Fresh(make_client, "STORES")
    assert stores.post(f"{INV}/counts", json={"store_id": w["store"]["store_id"]}).status_code == 409
    stores.post(f"{INV}/counts/{c['count_id']}/submit")
    decide(make_client, approval_of(make_client, c["count_id"]), "PLANT_HEAD", "REJECT")
    assert stores.get(f"{INV}/counts/{c['count_id']}").json()["status"] == "RECOUNT"
    assert on_hand(make_client, w["store"]["store_id"], w["item"]["item_id"]) != {}
    assert sum(on_hand(make_client, w["store"]["store_id"], w["item"]["item_id"]).values()) == 100
    again = stores.post(f"{INV}/counts/{c['count_id']}/recount")
    assert again.status_code == 201 and again.json()["count_id"] != c["count_id"]
    assert stores.post(f"{INV}/counts/{again.json()['count_id']}/recount").status_code == 409
    reconciles(make_client)


def test_count_rules_scope_and_permissions(make_client):
    w = world(make_client)
    receive(make_client, w, "100")
    store = w["store"]["store_id"]
    for role in ("SALES", "PURCHASE", "OPERATOR"):
        assert make_client(role).post(f"{INV}/counts", json={"store_id": store}).status_code == 403
    assert make_client("STORES").post(f"{INV}/counts", json={"store_id": str(uuid4())}
                                      ).status_code == 404
    other = new_plant(make_client)
    outsider = Fresh(make_client, "STORES", subject="u-count-out", unit_ids=[other["unit_id"]])
    assert outsider.post(f"{INV}/counts", json={"store_id": store}).status_code == 404
    c = start_count(make_client, store)
    outsider = Fresh(make_client, "STORES", subject="u-count-out", unit_ids=[other["unit_id"]])
    assert outsider.get(f"{INV}/counts/{c['count_id']}").status_code == 404
    stores = Fresh(make_client, "STORES")
    foreign = {"lines": [{"count_line_id": str(uuid4()), "counted_qty": "1"}]}
    assert stores.put(f"{INV}/counts/{c['count_id']}/lines", json=foreign).status_code == 422
    assert stores.put(f"{INV}/counts/{c['count_id']}/lines", json={"lines": [{
        "count_line_id": c["lines"][0]["count_line_id"], "counted_qty": "-1"}]}).status_code == 422
    assert stores.post(f"{INV}/counts/{c['count_id']}/cancel").json()["status"] == "CANCELLED"
    assert stores.get(f"{INV}/counts?status=cancelled").json()["total"] >= 1


# ------------------------------------------------------------------ lot trace
def test_lot_trace_shows_origin_moves_and_transfers(make_client):
    w = world(make_client, price="10")
    lot = receive(make_client, w, "100")
    dst = new_store(make_client, w["unit"]["unit_id"])["store_id"]
    t = transfer(make_client, w["store"]["store_id"], dst, lot, "25").json()
    stores = Fresh(make_client, "STORES")
    stores.post(f"{INV}/transfers/{t['transfer_id']}/dispatch")
    stores.post(f"{INV}/transfers/{t['transfer_id']}/receive")
    issued = stores.post(f"{INV}/issues", json={
        "store_id": dst, "purpose": "Trial run",
        "lines": [{"item_id": w["item"]["item_id"], "quantity": "5"}]}).json()
    trace = stores.get(f"{INV}/lots/{lot['lot_id']}/trace").json()
    assert trace["origin"]["grn_no"].startswith("GRN-") and trace["origin"]["po_no"]
    assert trace["on_hand"] == "75.000"
    assert [m["movement_type"] for m in trace["moves"]] == ["RECEIPT", "TRANSFER_OUT"]
    assert trace["moves"][1]["document_no"] == t["transfer_no"]
    assert [(link["direction"], link["transfer_no"]) for link in trace["links"]] == [
        ("to", t["transfer_no"])]
    dest_lot = stores.get(f"{INV}/stock?store_id={dst}").json()["items"][0]
    down = stores.get(f"{INV}/lots/{dest_lot['lot_id']}/trace").json()
    assert [m["movement_type"] for m in down["moves"]] == ["TRANSFER_IN", "ISSUE"]
    assert down["moves"][1]["document_no"] == issued["issue_no"]
    assert [(link["direction"], link["lot_id"]) for link in down["links"]] == [
        ("from", lot["lot_id"])]
    other = new_plant(make_client)
    outsider = Fresh(make_client, "STORES", subject="u-trace-out", unit_ids=[other["unit_id"]])
    assert outsider.get(f"{INV}/lots/{lot['lot_id']}/trace").status_code == 404
    assert stores.get(f"{INV}/lots/{uuid4()}/trace").status_code == 404


def test_a_store_with_stock_in_transit_or_a_count_open_cannot_be_deactivated(make_client):
    w = world(make_client)
    lot = receive(make_client, w, "100")
    src = w["store"]["store_id"]
    dst = new_store(make_client, w["unit"]["unit_id"])["store_id"]
    shut = lambda s: make_client("ADMIN").patch(  # noqa: E731
        f"/api/v1/org/stores/{s}", json={"active": False}).status_code
    assert shut(dst) == 200  # empty store: fine
    dst = new_store(make_client, w["unit"]["unit_id"])["store_id"]
    t = transfer(make_client, src, dst, lot, "100").json()
    make_client("STORES").post(f"{INV}/transfers/{t['transfer_id']}/dispatch")
    assert shut(src) == 409 and shut(dst) == 409  # stock on the way to dst; src is mid-transfer
    make_client("STORES").post(f"{INV}/transfers/{t['transfer_id']}/receive")
    assert shut(src) == 200  # src is empty now
    assert shut(dst) == 409  # dst holds the stock
