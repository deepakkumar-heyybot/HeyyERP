"""Compound master, batch weighing, the derived batch lifecycle, QA and FIFO allocation."""

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import HTTPException
from sqlalchemy import text

from heyyerp_api import db
from heyyerp_api.manufacturing import batches
from heyyerp_api.masters.common import Ctx
from heyyerp_api.rbac.seed import create_app_user
from heyyerp_api.security import Principal, Profile

from .conftest import API_DB, API_PASSWORD, API_ROLE, TENANT_A
from .dbutil import url_for
from .test_inventory_receipt import INV, code, inspect, reconciles, world

pytestmark = pytest.mark.usefixtures("api_database")
M = "/api/v1/manufacturing"


class Fresh:
    """make_client closes the previous client, so build a new one for every request."""

    def __init__(self, make_client, role, **kw):
        self.make, self.role, self.kw = make_client, role, kw

    def __getattr__(self, verb):
        return lambda *a, **k: getattr(self.make(self.role, **self.kw), verb)(*a, **k)


def stocked(make_client, qtys=("1000", "1000")):
    """A plant whose raw-material store holds available stock of two items."""
    w = world(make_client, qty="2000", items=2)
    lines = [{"po_line_id": ln["po_line_id"], "store_id": w["store"]["store_id"],
              "received_qty": q} for ln, q in zip(w["po"]["lines"], qtys, strict=True)]
    grn = Fresh(make_client, "STORES").post(f"{INV}/grns", json={
        "po_id": w["po"]["po_id"], "supplier_dc_no": "DC-" + uuid4().hex[:5], "lines": lines})
    assert grn.status_code == 201, grn.text
    for line, q in zip(grn.json()["lines"], qtys, strict=True):
        assert inspect(make_client("QA"), line, q).status_code == 200
    return w


def compound_body(w, **extra):
    return {"code": "C" + code()[:8], "name": "Test compound", "lane": "RUBBER",
            "maturation_min_hours": "24", "maximum_age_hours": "120",
            "hardness_min": "55", "hardness_max": "65",
            "recipe": [{"item_id": w["items"][0]["item_id"], "phr": "100"},
                       {"item_id": w["items"][1]["item_id"], "phr": "50"}], **extra}


def make_compound(make_client, w, **extra):
    made = Fresh(make_client, "PROD").post(f"{M}/compounds", json=compound_body(w, **extra))
    assert made.status_code == 201, made.text
    return made.json()


def weigh(make_client, w, compound, kg="150", **extra):
    return Fresh(make_client, "PROD").post(f"{M}/batches", json={
        "compound_id": compound["compound_id"], "store_id": w["store"]["store_id"],
        "batch_kg": kg, **extra})


def set_batch(api_database, batch_id, **cols):
    sets = ", ".join(f"{k} = {v}" for k, v in cols.items())
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute(f"update erp.compound_batch set {sets} where batch_id = %s", (batch_id,))


def to_awaiting_qa(make_client, api_database, batch_id, age="30"):
    assert Fresh(make_client, "PROD").post(f"{M}/batches/{batch_id}/start-mixing").status_code == 200
    assert Fresh(make_client, "PROD").post(f"{M}/batches/{batch_id}/mixed").status_code == 200
    set_batch(api_database, batch_id, mixed_at=f"now() - interval '{age} hours'")


def qa(make_client, batch_id, result="PASSED", hardness="60", note=None):
    return Fresh(make_client, "QA").post(f"{M}/batches/{batch_id}/qa", json={
        "result": result, "hardness": hardness, "note": note})


def ready_batch(make_client, api_database, w, compound, kg="100", age="30"):
    batch = weigh(make_client, w, compound, kg).json()
    to_awaiting_qa(make_client, api_database, batch["batch_id"], age)
    assert qa(make_client, batch["batch_id"]).status_code == 200
    return batch["batch_id"]


def status_of(make_client, batch_id):
    return Fresh(make_client, "PROD").get(f"{M}/batches/{batch_id}").json()["status"]


# ------------------------------------------------------------------ compound master
def test_default_compounds_are_seeded_from_the_brd(make_client):
    got = {c["code"]: c for c in Fresh(make_client, "PROD").get(f"{M}/compounds?limit=100").json()["items"]}
    assert {"VR-C60", "VR-C70", "VR-E65"} <= set(got)
    assert float(got["VR-E65"]["maturation_min_hours"]) == 12 and got["VR-E65"]["lane"] == "EXTRUSION"
    assert float(got["VR-C60"]["maximum_age_hours"]) == 120


def test_compound_create_validate_and_edit(make_client, api_database):
    w = stocked(make_client)
    c = make_compound(make_client, w)
    assert [float(r["phr"]) for r in c["recipe"]] == [100, 50] and c["active"]
    prod = Fresh(make_client, "PROD")
    post = lambda **b: prod.post(f"{M}/compounds", json=compound_body(w, **b)).status_code  # noqa: E731
    assert post(code=c["code"]) == 409
    assert post(maximum_age_hours="24") == 422  # not longer than the minimum maturation
    assert post(hardness_min="70") == 422
    dup = compound_body(w)["recipe"][0]
    assert post(recipe=[dup, dup]) == 422
    assert post(recipe=[{"item_id": str(uuid4()), "phr": "10"}]) == 422
    assert post(recipe=[{"item_id": w["items"][0]["item_id"], "phr": "0"}]) == 422
    assert post(lane="OTHER") == 422 and post(tenant_id=str(uuid4())) == 422
    wip = make_client("ADMIN").post("/api/v1/product/items", json={
        "code": code(), "name": "Compound as item", "item_type": "WIP", "uom_code": "KG"})
    assert wip.status_code == 201, wip.text
    assert post(output_item_id=wip.json()["item_id"]) == 201
    assert post(output_item_id=w["items"][0]["item_id"]) == 422  # not a WIP item
    for role in ("SALES", "QA", "PURCHASE"):
        assert Fresh(make_client, role).post(f"{M}/compounds", json=compound_body(w)).status_code == 403
    # the recipe can change until a batch exists
    new = [{"item_id": w["items"][0]["item_id"], "phr": "80"}]
    patched = prod.patch(f"{M}/compounds/{c['compound_id']}", json={"recipe": new, "name": "Renamed"})
    assert patched.status_code == 200 and len(patched.json()["recipe"]) == 1
    assert prod.patch(f"{M}/compounds/{c['compound_id']}", json={
        "maximum_age_hours": "1"}).status_code == 422
    assert prod.patch(f"{M}/compounds/{c['compound_id']}", json={"name": None}).status_code == 422
    assert weigh(make_client, w, patched.json(), "100").status_code == 201
    frozen = prod.patch(f"{M}/compounds/{c['compound_id']}", json={"recipe": new})
    assert frozen.status_code == 409 and "new compound code" in frozen.json()["detail"]
    assert prod.patch(f"{M}/compounds/{c['compound_id']}", json={"active": False}).status_code == 200
    assert weigh(make_client, w, c, "100").status_code == 422  # inactive


# ------------------------------------------------------------------ weighing
def test_weighing_issues_raw_material_fefo_and_records_target_against_actual(
        make_client, api_database):
    w = stocked(make_client)
    c = make_compound(make_client, w)
    made = weigh(make_client, w, c, "150")
    assert made.status_code == 201, made.text
    b = made.json()
    assert b["batch_no"].startswith("BAT-") and b["status"] == "WEIGHED"
    assert float(b["total_kg"]) == 150 and float(b["remaining_kg"]) == 150
    got = {x["item_code"]: x for x in b["weighing"]}
    assert float(got[w["items"][0]["code"]]["target_qty"]) == 100
    assert float(got[w["items"][1]["code"]]["actual_qty"]) == 50
    left = {r["item_code"]: float(r["on_hand"]) for r in Fresh(make_client, "STORES").get(
        f"{INV}/stock?store_id={w['store']['store_id']}").json()["items"]}
    assert left == {w["items"][0]["code"]: 900, w["items"][1]["code"]: 950}
    reconciles(make_client)
    # the weights actually weighed can differ from the target; the batch keeps what was weighed
    recipe = {r["item_code"]: r["recipe_line_id"] for r in c["recipe"]}
    custom = weigh(make_client, w, c, "150", lines=[
        {"recipe_line_id": recipe[w["items"][0]["code"]], "actual_qty": "103"}]).json()
    row = next(x for x in custom["weighing"] if x["item_code"] == w["items"][0]["code"])
    assert float(row["target_qty"]) == 100 and float(row["variance"]) == 3
    assert float(custom["total_kg"]) == 153


def test_weighing_draws_the_earliest_expiry_lot_first_across_lots(make_client, api_database):
    w = stocked(make_client, ("100", "100"))
    again = Fresh(make_client, "STORES").post(f"{INV}/grns", json={
        "po_id": w["po"]["po_id"], "supplier_dc_no": "DC-2", "lines": [{
            "po_line_id": w["po"]["lines"][0]["po_line_id"], "store_id": w["store"]["store_id"],
            "received_qty": "100"}]}).json()["lines"][0]
    inspect(make_client("QA"), again, "100")
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("update erp.lot set expiry_at = now() + interval '5 days' where lot_id = %s",
                     (again["lot_id"],))
    c = make_compound(make_client, w)
    b = weigh(make_client, w, c, "150").json()  # needs 100 of item 0 and 50 of item 1
    first = [x for x in b["weighing"] if x["item_code"] == w["items"][0]["code"]]
    assert [x["lot_no"] for x in first] == [again["lot_no"]]  # the 5-day lot, not the 180-day one


def test_weighing_refuses_a_shortfall_and_changes_nothing(make_client):
    w = stocked(make_client, ("1000", "10"))
    c = make_compound(make_client, w)
    refused = weigh(make_client, w, c, "150")
    assert refused.status_code == 409 and "Not enough stock" in refused.json()["detail"]
    assert Fresh(make_client, "PROD").get(f"{M}/batches?compound_id={c['compound_id']}").json()[
        "total"] == 0
    assert sum(float(r["on_hand"]) for r in Fresh(make_client, "STORES").get(
        f"{INV}/stock?store_id={w['store']['store_id']}").json()["items"]) == 1010
    reconciles(make_client)


def test_weighing_validation_and_permissions(make_client):
    w = stocked(make_client)
    c = make_compound(make_client, w)
    other = make_compound(make_client, w)
    body = {"compound_id": c["compound_id"], "store_id": w["store"]["store_id"], "batch_kg": "10"}
    prod = Fresh(make_client, "PROD")
    assert prod.post(f"{M}/batches", json={**body, "batch_kg": "0"}).status_code == 422
    assert prod.post(f"{M}/batches", json={**body, "store_id": str(uuid4())}).status_code == 404
    assert prod.post(f"{M}/batches", json={**body, "compound_id": str(uuid4())}).status_code == 422
    assert prod.post(f"{M}/batches", json={**body, "lines": [{
        "recipe_line_id": other["recipe"][0]["recipe_line_id"], "actual_qty": "5"}]}
    ).status_code == 422  # a line of another compound
    empty = Fresh(make_client, "PROD").post(f"{M}/compounds", json={
        **compound_body(w, recipe=[]), "code": "EMPTY1"}).json()
    assert prod.post(f"{M}/batches", json={**body, "compound_id": empty["compound_id"]}
                     ).status_code == 422  # no recipe yet
    for role in ("SALES", "PURCHASE", "ACCOUNTS"):
        assert Fresh(make_client, role).post(f"{M}/batches", json=body).status_code == 403, role


# ------------------------------------------------------------------ lifecycle and QA
def test_batch_status_is_derived_through_the_whole_lifecycle(make_client, api_database):
    w = stocked(make_client)
    c = make_compound(make_client, w)
    b = weigh(make_client, w, c, "150").json()["batch_id"]
    prod = Fresh(make_client, "PROD")
    assert status_of(make_client, b) == "WEIGHED"
    assert prod.post(f"{M}/batches/{b}/mixed").status_code == 409  # mixing has not started
    assert prod.post(f"{M}/batches/{b}/start-mixing").json()["status"] == "MIXING"
    assert prod.post(f"{M}/batches/{b}/start-mixing").status_code == 409
    done = prod.post(f"{M}/batches/{b}/mixed").json()
    assert done["status"] == "MATURING" and done["mixed_at"]
    assert qa(make_client, b).status_code == 409  # still maturing
    set_batch(api_database, b, mixed_at="now() - interval '30 hours'")
    assert status_of(make_client, b) == "AWAITING_QA"
    assert qa(make_client, b, hardness=None).status_code == 422  # hardness needed
    out = qa(make_client, b, hardness="70")
    assert out.status_code == 422 and "outside the spec" in out.json()["detail"]
    passed = qa(make_client, b, hardness="60")
    assert passed.status_code == 200 and passed.json()["status"] == "READY"
    assert passed.json()["qa_note"] is None and float(passed.json()["hardness"]) == 60
    assert qa(make_client, b).status_code == 409  # decided already
    set_batch(api_database, b, mixed_at="now() - interval '121 hours'")
    assert status_of(make_client, b) == "EXPIRED"
    set_batch(api_database, b, mixed_at="now() - interval '30 hours'", remaining_kg="0")
    assert status_of(make_client, b) == "CONSUMED"


def test_failed_qa_rejects_the_batch_and_roles_are_enforced(make_client, api_database):
    w = stocked(make_client)
    c = make_compound(make_client, w)
    b = weigh(make_client, w, c, "150").json()["batch_id"]
    to_awaiting_qa(make_client, api_database, b)
    assert Fresh(make_client, "PROD").post(f"{M}/batches/{b}/qa", json={
        "result": "PASSED", "hardness": "60"}).status_code == 403  # route.move is not qa.approve
    assert qa(make_client, b, "FAILED", None).status_code == 422  # a failure needs a note
    failed = qa(make_client, b, "FAILED", "52", "Too soft")
    assert failed.status_code == 200 and failed.json()["status"] == "REJECTED"
    assert failed.json()["qa_note"] == "Too soft"
    avail = Fresh(make_client, "PROD").get(f"{M}/compounds/{c['compound_id']}/available").json()
    assert avail["batches"] == [] and float(avail["ready_kg"]) == 0
    assert Fresh(make_client, "SALES").get(f"{M}/batches/{b}").status_code == 403


def test_batch_listing_filters_and_plant_scope(make_client, api_database):
    w = stocked(make_client)
    c = make_compound(make_client, w)
    one_ready = ready_batch(make_client, api_database, w, c, "100")
    weighed = weigh(make_client, w, c, "100").json()["batch_id"]
    prod = Fresh(make_client, "PROD")
    ready = prod.get(f"{M}/batches?compound_id={c['compound_id']}&status=ready").json()
    assert [x["batch_id"] for x in ready["items"]] == [one_ready]
    both = prod.get(f"{M}/batches?compound_id={c['compound_id']}").json()
    assert both["total"] == 2 and weighed in {x["batch_id"] for x in both["items"]}
    other = make_client("ADMIN").post("/api/v1/org/units", json={"code": code(), "name": "P2"}).json()
    away = Fresh(make_client, "PROD", subject="u-batch-out", unit_ids=[other["unit_id"]])
    assert away.get(f"{M}/batches/{one_ready}").status_code == 404
    assert away.get(f"{M}/batches?compound_id={c['compound_id']}").json()["total"] == 0
    assert away.post(f"{M}/batches/{one_ready}/start-mixing").status_code == 404
    assert away.post(f"{M}/batches", json={
        "compound_id": c["compound_id"], "store_id": w["store"]["store_id"],
        "batch_kg": "10"}).status_code == 404


# ------------------------------------------------------------------ FIFO allocation
def test_available_compound_is_listed_oldest_first(make_client, api_database):
    w = stocked(make_client)
    c = make_compound(make_client, w)
    newer = ready_batch(make_client, api_database, w, c, "100", age="30")
    older = ready_batch(make_client, api_database, w, c, "100", age="60")
    got = Fresh(make_client, "PROD").get(f"{M}/compounds/{c['compound_id']}/available").json()
    assert [x["batch_id"] for x in got["batches"]] == [older, newer]
    assert float(got["ready_kg"]) == 200


def run_allocations(w, compound_id, kilos):
    """Run several allocations at the same moment, each in its own transaction."""
    users = []
    with psycopg.connect(url_for(API_DB), autocommit=True) as owner:
        for n in range(len(kilos)):
            uid = create_app_user(owner, TENANT_A, f"alloc-{uuid4().hex[:6]}-{n}", "PROD", f"A{n}")
            users.append(Profile(uid, f"A{n}", "PROD", frozenset(), frozenset(), None))

    async def attempt(profile, kg):
        async with db.SessionFactory() as session, session.begin():
            await session.execute(text("select set_config('app.tenant_id', :t, true)"),
                                  {"t": TENANT_A})
            ctx = Ctx(session, Principal("u", "e", True, "o", True, UUID(TENANT_A), profile))
            try:
                return await batches.allocate(ctx, UUID(w["unit"]["unit_id"]),
                                              UUID(compound_id), kg)
            except HTTPException as exc:
                return exc.status_code

    async def race():
        db.configure_database(url_for(API_DB, API_ROLE, API_PASSWORD).replace(
            "postgresql://", "postgresql+asyncpg://"))
        try:
            return await asyncio.gather(*(attempt(u, k) for u, k in zip(users, kilos, strict=True)))
        finally:
            await db.engine.dispose()

    return asyncio.run(race())


def test_allocation_takes_oldest_first_across_batches_and_all_or_nothing(
        make_client, api_database):
    w = stocked(make_client)
    c = make_compound(make_client, w)
    newer = ready_batch(make_client, api_database, w, c, "100", age="30")
    older = ready_batch(make_client, api_database, w, c, "100", age="60")
    (draws,) = run_allocations(w, c["compound_id"], [Decimal(130)])
    assert draws == [(UUID(older), Decimal(100)), (UUID(newer), Decimal(30))]
    left = {x["batch_id"]: float(x["remaining_kg"]) for x in Fresh(make_client, "PROD").get(
        f"{M}/batches?compound_id={c['compound_id']}").json()["items"]}
    assert left == {older: 0, newer: 70}
    assert status_of(make_client, older) == "CONSUMED"
    (short,) = run_allocations(w, c["compound_id"], [Decimal(80)])
    assert short == 409  # 70 kg left: nothing is taken
    assert Fresh(make_client, "PROD").get(f"{M}/batches/{newer}").json()["remaining_kg"] == "70.000"


def test_expired_or_unapproved_compound_is_never_allocated(make_client, api_database):
    w = stocked(make_client)
    c = make_compound(make_client, w)
    ok = ready_batch(make_client, api_database, w, c, "50", age="30")
    stale = ready_batch(make_client, api_database, w, c, "50", age="40")
    set_batch(api_database, stale, mixed_at="now() - interval '130 hours'")  # past its max age
    maturing = weigh(make_client, w, c, "50").json()["batch_id"]
    to_awaiting_qa(make_client, api_database, maturing, age="2")
    (got,) = run_allocations(w, c["compound_id"], [Decimal(50)])
    assert got == [(UUID(ok), Decimal(50))]
    (none,) = run_allocations(w, c["compound_id"], [Decimal(1)])
    assert none == 409


def test_concurrent_allocations_never_overdraw_a_batch(make_client, api_database):
    w = stocked(make_client)
    c = make_compound(make_client, w)
    only = ready_batch(make_client, api_database, w, c, "100", age="30")
    results = run_allocations(w, c["compound_id"], [Decimal(15)] * 10)
    assert sum(1 for r in results if r != 409) == 6 and results.count(409) == 4, results
    assert Fresh(make_client, "PROD").get(f"{M}/batches/{only}").json()["remaining_kg"] == "10.000"


# ------------------------------------------------------------------ machines and moulds
def test_machine_and_mould_masters(make_client):
    w = stocked(make_client)
    unit = w["unit"]["unit_id"]
    prod = Fresh(make_client, "PROD")
    made = prod.post(f"{M}/machines", json={"unit_id": unit, "code": "pr-01", "name": "Press 1",
                                            "machine_type": "PRESS"})
    assert made.status_code == 201 and made.json()["code"] == "PR-01"
    assert prod.post(f"{M}/machines", json={"unit_id": unit, "code": "PR-01", "name": "Dup"}
                     ).status_code == 409
    assert prod.patch(f"{M}/machines/{made.json()['machine_id']}", json={"active": False}
                      ).json()["active"] is False
    assert prod.patch(f"{M}/machines/{made.json()['machine_id']}", json={"name": None}
                      ).status_code == 422
    mould = prod.post(f"{M}/moulds", json={"unit_id": unit, "part_item_id": w["item"]["item_id"],
                                           "code": "m-9", "shots_limit": 500000})
    assert mould.status_code == 201 and mould.json()["part_code"] == w["item"]["code"]
    assert prod.post(f"{M}/moulds", json={"unit_id": unit, "part_item_id": str(uuid4()),
                                          "code": "M-10"}).status_code == 422
    assert prod.patch(f"{M}/moulds/{mould.json()['mould_id']}", json={"shots_limit": 0}
                      ).status_code == 422
    assert prod.get(f"{M}/machines?unit_id={unit}").json()["total"] == 1
    assert Fresh(make_client, "SALES").post(f"{M}/machines", json={
        "unit_id": unit, "code": "X1", "name": "Nope"}).status_code == 403
    other = make_client("ADMIN").post("/api/v1/org/units", json={"code": code(), "name": "P2"}).json()
    away = Fresh(make_client, "PROD", subject="u-equip-out", unit_ids=[other["unit_id"]])
    assert away.get(f"{M}/machines/{made.json()['machine_id']}").status_code == 404
    assert away.post(f"{M}/machines", json={"unit_id": unit, "code": "Z", "name": "Zed"}
                     ).status_code == 403
