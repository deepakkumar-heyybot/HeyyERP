"""Compound batches: weigh, mix, mature, QA, ready (BRD FR-MFG-02, FR-MFG-03, 6.3, 6.4).

Weighing issues the raw material first-expiry-first from the store (a stock ISSUE per lot drawn)
and records target against actual for every ingredient and lot. The batch status is derived by the
database view `v_compound_batch_status` from the mixing time, the QA result and the kilos left,
never stored: Weighed, Mixing, Maturing, Awaiting QA, Ready, Consumed, Expired, Rejected. QA can
decide only once the minimum maturation has passed, and a hardness outside the compound's spec
cannot be approved. `allocate` takes compound from Ready batches oldest first for production.
Weighing and mixing need route.move, the QA decision needs qa.approve.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from heyyerp_api.inventory import ledger
from heyyerp_api.inventory.issues import candidates, choose, store_in_scope
from heyyerp_api.masters.common import (
    Ctx,
    Page,
    Paging,
    Strict,
    audit,
    count,
    not_found,
    one,
    requires,
    requires_module,
    rows,
    scope_clause,
)
from heyyerp_api.procurement.numbering import next_number
from heyyerp_api.quality.inspections import clearance, record_decision

router = APIRouter(prefix="/manufacturing", tags=["manufacturing"])
move = requires("route.move")
qa = requires("qa.approve")
read = requires_module("manufacturing")
Qty = Field(gt=0, le=Decimal(1_000_000), max_digits=18, decimal_places=3)
GRAM = Decimal("0.001")


class WeighedLine(Strict):
    recipe_line_id: UUID
    actual_qty: Decimal = Qty


class BatchIn(Strict):
    compound_id: UUID
    store_id: UUID  # the raw-material store the ingredients come from
    batch_kg: Decimal = Qty  # planned total; the batch records what was actually weighed
    lines: list[WeighedLine] = Field(default_factory=list, max_length=60)  # default: the target


class QaIn(Strict):
    result: Literal["PASSED", "FAILED"]
    hardness: Decimal | None = Field(default=None, ge=0, le=100, max_digits=8, decimal_places=2)
    note: str | None = Field(default=None, max_length=500)


class WeighingOut(BaseModel):
    item_code: str
    item_name: str
    lot_no: str
    target_qty: Decimal
    actual_qty: Decimal
    variance: Decimal


class BatchSummary(BaseModel):
    batch_id: UUID
    batch_no: str
    unit_id: UUID
    compound_id: UUID
    compound_code: str
    compound_name: str
    status: str
    total_kg: Decimal
    remaining_kg: Decimal
    weighed_at: datetime | None
    mixed_at: datetime | None
    age_hours: Decimal | None
    maturation_min_hours: Decimal
    maximum_age_hours: Decimal
    qa_status: str
    hardness: Decimal | None


class BatchOut(BatchSummary):
    qa_note: str | None
    qa_at: datetime | None
    weighing: list[WeighingOut]


VIEW = "SELECT {cols} FROM erp.v_compound_batch_status b"
SUMMARY_COLS = (
    "b.batch_id, b.batch_no, b.unit_id, b.compound_id, b.compound_code, b.compound_name, "
    "b.status, b.total_kg, b.remaining_kg, b.weighed_at, b.mixed_at, b.age_hours, "
    "b.maturation_min_hours, b.maximum_age_hours, b.qa_status, b.hardness")


async def load(ctx: Ctx, batch_id: UUID) -> BatchOut | None:
    clause, params = scope_clause(ctx, "b.unit_id")
    head = await one(
        ctx, f"{VIEW.format(cols=SUMMARY_COLS + ', b.qa_note, b.qa_at')} WHERE b.batch_id = :id"
        f"{' AND ' + clause if clause else ''}", {**params, "id": batch_id})
    if head is None:
        return None
    weighing = await rows(
        ctx, "SELECT i.code AS item_code, i.name AS item_name, t.lot_no, w.target_qty, "
        "w.actual_qty, w.actual_qty - w.target_qty AS variance FROM erp.batch_weighing_line w "
        "JOIN erp.compound_recipe_line r ON r.recipe_line_id = w.recipe_line_id JOIN erp.item i "
        "ON i.item_id = r.item_id JOIN erp.lot t ON t.lot_id = w.source_lot_id WHERE "
        "w.batch_id = :id ORDER BY r.line_no, t.lot_no", {"id": batch_id})
    return BatchOut(**head, weighing=[WeighingOut(**w) for w in weighing])


async def require_batch(ctx: Ctx, batch_id: UUID, *allowed: str) -> dict[str, Any]:
    """Lock the batch and check its derived status is one of the allowed ones."""
    clause, params = scope_clause(ctx, "unit_id")
    found = await one(
        ctx, "SELECT batch_id FROM erp.compound_batch WHERE batch_id = :id"
        f"{' AND ' + clause if clause else ''} FOR UPDATE", {**params, "id": batch_id})
    if found is None:
        raise not_found("Batch")
    head = await one(
        ctx, "SELECT batch_id, batch_no, status, age_hours, maturation_min_hours, hardness_min, "
        "hardness_max, unit_id FROM erp.v_compound_batch_status WHERE batch_id = :id",
        {"id": batch_id})
    assert head is not None
    if head["status"] not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The batch is {str(head['status']).lower().replace('_', ' ')}; this needs it to be "
            f"{' or '.join(a.lower().replace('_', ' ') for a in allowed)}")
    return head


@router.post("/batches", response_model=BatchOut, status_code=status.HTTP_201_CREATED)
async def weigh_batch(body: BatchIn, ctx: Ctx = Depends(move)) -> BatchOut:
    """Weigh a batch: issue the ingredients first-expiry-first and record target against actual."""
    store = await store_in_scope(ctx, body.store_id)
    if not store["active"]:
        raise HTTPException(422, "The store is inactive")
    compound = await one(ctx, "SELECT compound_id, active FROM erp.compound WHERE "
                         "compound_id = :id",
                         {"id": body.compound_id})
    if compound is None or not compound["active"]:
        raise HTTPException(422, "The compound does not exist or is inactive")
    recipe = await rows(
        ctx, "SELECT recipe_line_id, item_id, phr FROM erp.compound_recipe_line WHERE "
        "compound_id = :id ORDER BY item_id", {"id": body.compound_id})  # item order: lock order
    if not recipe:
        raise HTTPException(422, "The compound has no recipe yet")
    total_phr = sum((r["phr"] for r in recipe), Decimal(0))
    given = {ln.recipe_line_id: ln.actual_qty for ln in body.lines}
    if len(given) != len(body.lines) or not set(given) <= {r["recipe_line_id"] for r in recipe}:
        raise HTTPException(422, "Weights must name each recipe line of this compound once")
    plan = []
    for line in recipe:
        target = (line["phr"] / total_phr * body.batch_kg).quantize(GRAM)
        if target <= 0:
            raise HTTPException(422, "The batch is too small to weigh every ingredient")
        plan.append({**line, "target": target,
                     "actual": given.get(line["recipe_line_id"], target)})
    number = await next_number(ctx, "batch")
    created = await one(
        ctx,
        "INSERT INTO erp.compound_batch(tenant_id, unit_id, compound_id, batch_no, weighed_at, "
        "total_kg, remaining_kg, created_by, store_id) VALUES (erp.current_tenant_id(), :u, :c, "
        ":n, now(), :kg, :kg, :me, :s) RETURNING batch_id",
        {"u": store["unit_id"], "c": body.compound_id, "n": number,
         "kg": sum((p["actual"] for p in plan), Decimal(0)),
         "me": ctx.principal.profile.app_user_id, "s": body.store_id})
    assert created is not None
    batch_id: UUID = created["batch_id"]
    steps = 0
    for line in plan:
        lots = await candidates(ctx, body.store_id, line["item_id"], lock=True)
        # raw material is always issued first-expiry-first
        picks, shortfall, blocked = choose(lots, line["actual"], "FEFO")
        if shortfall > 0:
            code = (await one(ctx, "SELECT code FROM erp.item WHERE item_id = :i",
                              {"i": line["item_id"]}) or {"code": "an ingredient"})["code"]
            expired = sum((b["available"] for b in blocked), Decimal(0))
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Not enough stock of {code}: {line['actual'] - shortfall} can be weighed, "
                f"{line['actual']} needed" + (f" ({expired} more is expired)" if expired else ""))
        for pick in picks:
            steps += 1
            ledger_id = await ledger.move(
                ctx, unit_id=store["unit_id"], item_id=line["item_id"], lot_id=pick["lot_id"],
                store_id=body.store_id, delta=-pick["take"], movement_type="ISSUE",
                reference_type="BATCH", reference_id=batch_id,
                idempotency_key=f"batch:{batch_id}:{steps}")
            await ledger.take_layers(ctx, unit_id=store["unit_id"], item_id=line["item_id"],
                                     quantity=pick["take"], issue_ledger_id=ledger_id)
            share = line["target"] * pick["take"] / line["actual"]
            await rows(
                ctx, "INSERT INTO erp.batch_weighing_line(tenant_id, batch_id, recipe_line_id, "
                "source_lot_id, target_qty, actual_qty) VALUES (erp.current_tenant_id(), :b, :r, "
                ":l, :t, :a) RETURNING weighing_line_id",
                {"b": batch_id, "r": line["recipe_line_id"], "l": pick["lot_id"],
                 "t": max(share.quantize(GRAM), GRAM), "a": pick["take"]})
    await audit(ctx, "batch.weigh", "batch", batch_id,
                {"batch_no": number, "compound_id": body.compound_id, "lots_drawn": steps})
    result = await load(ctx, batch_id)
    assert result is not None
    return result


@router.get("/batches", response_model=Page[BatchSummary])
async def list_batches(
    compound_id: UUID | None = None, batch_status: str | None = Query(None, alias="status"),
    page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[BatchSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "b.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    if compound_id is not None:
        conditions.append("b.compound_id = :compound")
        params["compound"] = compound_id
    if batch_status:
        conditions.append("b.status = :status")
        params["status"] = batch_status.upper()
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(
        ctx, f"{VIEW.format(cols=SUMMARY_COLS)}{where} ORDER BY b.batch_no DESC LIMIT :limit "
        "OFFSET :offset", {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.v_compound_batch_status b{where}", params)
    return Page(items=[BatchSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/batches/{batch_id}", response_model=BatchOut)
async def get_batch(batch_id: UUID, ctx: Ctx = Depends(read)) -> BatchOut:
    found = await load(ctx, batch_id)
    if found is None:
        raise not_found("Batch")
    return found


@router.post("/batches/{batch_id}/start-mixing", response_model=BatchOut)
async def start_mixing(batch_id: UUID, ctx: Ctx = Depends(move)) -> BatchOut:
    head = await require_batch(ctx, batch_id, "WEIGHED")
    await rows(ctx, "UPDATE erp.compound_batch SET mix_started_at = now() WHERE batch_id = :id "
               "RETURNING batch_id", {"id": batch_id})
    await audit(ctx, "batch.start_mixing", "batch", batch_id, {"batch_no": head["batch_no"]})
    result = await load(ctx, batch_id)
    assert result is not None
    return result


@router.post("/batches/{batch_id}/mixed", response_model=BatchOut)
async def finish_mixing(batch_id: UUID, ctx: Ctx = Depends(move)) -> BatchOut:
    """Mixing is done: the maturation clock starts now."""
    head = await require_batch(ctx, batch_id, "MIXING")
    await rows(ctx, "UPDATE erp.compound_batch SET mixed_at = now() WHERE batch_id = :id "
               "RETURNING batch_id", {"id": batch_id})
    await audit(ctx, "batch.mixed", "batch", batch_id, {"batch_no": head["batch_no"]})
    result = await load(ctx, batch_id)
    assert result is not None
    return result


@router.post("/batches/{batch_id}/qa", response_model=BatchOut)
async def decide_qa(batch_id: UUID, body: QaIn, ctx: Ctx = Depends(qa)) -> BatchOut:
    """Pass or fail a matured batch. A hardness outside the compound's spec cannot be approved."""
    head = await require_batch(ctx, batch_id, "AWAITING_QA")
    if body.result == "PASSED":
        if body.hardness is None:
            raise HTTPException(422, "Enter the measured hardness to approve a batch")
        low, high = head["hardness_min"], head["hardness_max"]
        if (low is not None and body.hardness < low) or (high is not None and body.hardness > high):
            raise HTTPException(
                422, f"Hardness {body.hardness} is outside the spec {low} to {high}; "
                "the batch cannot be approved")
    elif not body.note:
        raise HTTPException(422, "A failed batch needs a note explaining why")
    spec = await one(
        ctx, "SELECT c.output_item_id FROM erp.compound_batch b JOIN erp.compound c ON "
        "c.compound_id = b.compound_id WHERE b.batch_id = :id", {"id": batch_id})
    assert spec is not None
    if body.result == "PASSED":
        await clearance(ctx, kind="IN_PROCESS", subject_type="COMPOUND_BATCH", subject_id=batch_id,
                        item_id=spec["output_item_id"], since=None)
    await record_decision(
        ctx, kind="IN_PROCESS", subject_type="COMPOUND_BATCH", subject_id=batch_id,
        unit_id=head["unit_id"], notes=body.note,
        result="ACCEPT" if body.result == "PASSED" else "REJECT")
    await rows(
        ctx, "UPDATE erp.compound_batch SET qa_status = :r, hardness = :h, qa_note = :n, "
        "qa_by = :me, qa_at = now() WHERE batch_id = :id RETURNING batch_id",
        {"r": body.result, "h": body.hardness, "n": body.note,
         "me": ctx.principal.profile.app_user_id, "id": batch_id})
    await audit(ctx, "batch.qa", "batch", batch_id,
                {"batch_no": head["batch_no"], "result": body.result, "hardness": body.hardness})
    result = await load(ctx, batch_id)
    assert result is not None
    return result


class Available(BaseModel):
    compound_id: UUID
    ready_kg: Decimal
    batches: list[BatchSummary]


@router.get("/compounds/{compound_id}/available", response_model=Available)
async def available(
    compound_id: UUID, unit_id: UUID | None = None, ctx: Ctx = Depends(read)
) -> Available:
    """Ready compound, oldest first: what a press can draw right now."""
    conditions = ["b.compound_id = :c", "b.status = 'READY'"]
    params: dict[str, Any] = {"c": compound_id}
    clause, scope_params = scope_clause(ctx, "b.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    if unit_id is not None:
        conditions.append("b.unit_id = :u")
        params["u"] = unit_id
    items = await rows(
        ctx, f"{VIEW.format(cols=SUMMARY_COLS)} WHERE {' AND '.join(conditions)} "
        "ORDER BY b.mixed_at, b.batch_id", params)
    return Available(compound_id=compound_id,
                     ready_kg=sum((i["remaining_kg"] for i in items), Decimal(0)),
                     batches=[BatchSummary(**i) for i in items])


async def allocate(
    ctx: Ctx, unit_id: UUID, compound_id: UUID, kilos: Decimal
) -> list[tuple[UUID, Decimal]]:
    """Take compound from Ready batches, oldest first (BRD 6.4); returns [(batch_id, kg)].

    The candidate batches are locked in batch_id order and their status is read again once locked,
    so a batch that expired or was used up meanwhile is not drawn from. Nothing is taken unless the
    whole need can be met.
    """
    ids = [r["batch_id"] for r in await rows(
        ctx, "SELECT batch_id FROM erp.v_compound_batch_status WHERE unit_id = :u AND "
        "compound_id = :c AND status = 'READY'", {"u": unit_id, "c": compound_id})]
    if ids:
        await rows(ctx, "SELECT batch_id FROM erp.compound_batch WHERE batch_id = ANY(CAST(:ids AS "
                   "uuid[])) ORDER BY batch_id FOR UPDATE", {"ids": ids})
    ready = await rows(
        ctx, "SELECT batch_id, remaining_kg FROM erp.v_compound_batch_status WHERE "
        "batch_id = ANY(CAST(:ids AS uuid[])) AND status = 'READY' ORDER BY mixed_at, batch_id",
        {"ids": ids})
    draws: list[tuple[UUID, Decimal]] = []
    left = kilos
    for batch in ready:
        if left <= 0:
            break
        take = min(left, batch["remaining_kg"])
        draws.append((batch["batch_id"], take))
        left -= take
    if left > 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Not enough ready compound: {kilos - left} kg is matured and approved, {kilos} kg "
            "needed")
    for batch_id, take in draws:
        await rows(ctx, "UPDATE erp.compound_batch SET remaining_kg = remaining_kg - :k WHERE "
                   "batch_id = :id RETURNING batch_id", {"k": take, "id": batch_id})
    return draws
