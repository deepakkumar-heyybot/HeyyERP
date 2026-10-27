"""Work orders and production lots through their routes (BRD FR-MFG-04 to FR-MFG-08, 6.2, 6.4).

A work order plans a quantity of an item with the bill of material in force today and the item's
active route. Production lots are started against a released order. Starting a lot consumes what
the bill asks for, all or nothing: a compound component is drawn from Ready batches oldest first
(across several batches), a work-in-progress component (the painted metal insert) from metal lots
that reached "Ready for moulding", oldest first, and anything else from stock by the issue rule.
If a compound or insert is short the lot cannot be created. Each move to the next stage records
who, when, quantity in and out and rejects; leaving a QA gate needs qa.approve, any other stage
route.move. Packing books the finished goods into stock with a Code 39 label code. Every lot
keeps its sources, so a finished lot traces back to compound batches, metal lots and raw material.
"""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from heyyerp_api.business_rules import service as rules
from heyyerp_api.inventory import ledger
from heyyerp_api.inventory.issues import candidates, choose, store_in_scope
from heyyerp_api.manufacturing.batches import allocate
from heyyerp_api.masters.common import (
    Ctx,
    Page,
    Paging,
    Strict,
    audit,
    count,
    not_found,
    one,
    read_ctx,
    requires,
    requires_module,
    rows,
    scope_clause,
)
from heyyerp_api.procurement.numbering import next_number
from heyyerp_api.security import has_permission

router = APIRouter(prefix="/manufacturing", tags=["manufacturing"])
plan = requires("mfg.plan")
move_permission = requires("route.move")
read = requires_module("manufacturing")
GRAM = Decimal("0.001")
Qty = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)


def amount(value: Decimal) -> Decimal:
    return value.quantize(GRAM, rounding=ROUND_HALF_UP)


# ------------------------------------------------------------------ work orders
class WorkOrderIn(Strict):
    unit_id: UUID
    item_id: UUID
    planned_qty: Decimal = Qty
    planned_start: date | None = None
    planned_finish: date | None = None

    @model_validator(mode="after")
    def _dates(self) -> "WorkOrderIn":
        if self.planned_start and self.planned_finish and self.planned_finish < self.planned_start:
            raise ValueError("The finish date is before the start date")
        return self


class Material(BaseModel):
    component_item_id: UUID
    component_code: str
    component_name: str
    kind: str  # COMPOUND, INSERT or MATERIAL
    quantity_per: Decimal
    scrap_factor: Decimal
    required_qty: Decimal


class WorkOrderSummary(BaseModel):
    work_order_id: UUID
    work_order_no: str
    unit_id: UUID
    item_id: UUID
    item_code: str
    item_name: str
    bom_id: UUID
    route_id: UUID
    route_code: str
    planned_qty: Decimal
    started_qty: Decimal
    packed_qty: Decimal
    status: str
    planned_start: date | None
    planned_finish: date | None


class WorkOrderOut(WorkOrderSummary):
    bom_version: int
    materials: list[Material]


WO_SQL = (
    "SELECT w.work_order_id, w.work_order_no, w.unit_id, w.item_id, i.code AS item_code, "
    "i.name AS item_name, w.bom_id, w.route_id, r.code AS route_code, w.planned_qty, "
    "COALESCE((SELECT sum(m.quantity_out) FROM erp.prod_lot_move m JOIN erp.prod_lot l ON "
    "l.prod_lot_id = m.prod_lot_id WHERE l.work_order_id = w.work_order_id AND "
    "m.from_stage_id IS NULL), 0) AS started_qty, COALESCE((SELECT sum(p.packed_qty) FROM "
    "erp.packing p JOIN erp.prod_lot l ON l.prod_lot_id = p.prod_lot_id WHERE "
    "l.work_order_id = w.work_order_id), 0) AS packed_qty, w.status, w.planned_start, "
    "w.planned_finish FROM erp.work_order w JOIN erp.item i ON i.item_id = w.item_id JOIN "
    "erp.route r ON r.route_id = w.route_id"
)


async def bom_components(ctx: Ctx, bom_id: UUID) -> list[dict[str, Any]]:
    return await rows(
        ctx,
        "SELECT l.component_item_id, i.code AS component_code, i.name AS component_name, "
        "i.item_type, l.quantity_per, l.scrap_factor, c.compound_id FROM erp.bom_line l JOIN "
        "erp.item i ON i.item_id = l.component_item_id LEFT JOIN erp.compound c ON "
        "c.output_item_id = l.component_item_id WHERE l.bom_id = :id ORDER BY l.line_no",
        {"id": bom_id})


def kind_of(component: dict[str, Any]) -> str:
    if component["compound_id"] is not None:
        return "COMPOUND"
    return "INSERT" if component["item_type"] == "WIP" else "MATERIAL"


def required(component: dict[str, Any], quantity: Decimal) -> Decimal:
    return amount(component["quantity_per"] * (1 + component["scrap_factor"]) * quantity)


async def load_wo(ctx: Ctx, work_order_id: UUID) -> WorkOrderOut | None:
    clause, params = scope_clause(ctx, "w.unit_id")
    head = await one(
        ctx, f"{WO_SQL} WHERE w.work_order_id = :id{' AND ' + clause if clause else ''}",
        {**params, "id": work_order_id})
    if head is None:
        return None
    bom = await one(ctx, "SELECT version FROM erp.bom WHERE bom_id = :id", {"id": head["bom_id"]})
    assert bom is not None
    materials = [
        Material(component_item_id=c["component_item_id"], component_code=c["component_code"],
                 component_name=c["component_name"], kind=kind_of(c),
                 quantity_per=c["quantity_per"], scrap_factor=c["scrap_factor"],
                 required_qty=required(c, head["planned_qty"]))
        for c in await bom_components(ctx, head["bom_id"])]
    return WorkOrderOut(**head, bom_version=bom["version"], materials=materials)


async def require_wo(ctx: Ctx, work_order_id: UUID, *allowed: str) -> dict[str, Any]:
    clause, params = scope_clause(ctx, "unit_id")
    head = await one(
        ctx, "SELECT work_order_id, work_order_no, unit_id, item_id, bom_id, route_id, "
        "planned_qty, status FROM erp.work_order WHERE work_order_id = :id"
        f"{' AND ' + clause if clause else ''} FOR UPDATE", {**params, "id": work_order_id})
    if head is None:
        raise not_found("Work order")
    if head["status"] not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The work order is {str(head['status']).lower().replace('_', ' ')}; this needs it "
            f"to be {' or '.join(a.lower().replace('_', ' ') for a in allowed)}")
    return head


@router.post("/work-orders", response_model=WorkOrderOut, status_code=status.HTTP_201_CREATED)
async def create_work_order(body: WorkOrderIn, ctx: Ctx = Depends(plan)) -> WorkOrderOut:
    if not ctx.principal.may_see_unit(body.unit_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your plant scope")
    unit = await one(ctx, "SELECT active FROM erp.unit WHERE unit_id = :id", {"id": body.unit_id})
    if unit is None or not unit["active"]:
        raise HTTPException(422, "The plant does not exist or is inactive")
    item = await one(ctx, "SELECT item_type, route_code, active FROM erp.item WHERE item_id = :id",
                     {"id": body.item_id})
    if item is None or not item["active"] or item["item_type"] not in ("FG", "WIP"):
        raise HTTPException(422, "A work order makes an active finished or work-in-progress item")
    if item["route_code"] is None:
        raise HTTPException(422, "The item has no production route; set one on the item first")
    route = await one(ctx, "SELECT route_id FROM erp.route WHERE code = :c AND active "
                      "ORDER BY version DESC LIMIT 1", {"c": item["route_code"]})
    if route is None:
        raise HTTPException(422, f"No active production route {item['route_code']!r}")
    bom = await one(
        ctx, "SELECT bom_id FROM erp.bom WHERE item_id = :i AND status IN ('APPROVED', "
        "'SUPERSEDED') AND valid_from <= CURRENT_DATE AND (valid_to IS NULL OR valid_to >= "
        "CURRENT_DATE) ORDER BY valid_from DESC, version DESC LIMIT 1", {"i": body.item_id})
    if bom is None:
        raise HTTPException(422, "The item has no approved bill of material in force today")
    number = await next_number(ctx, "work_order")
    created = await one(
        ctx, "INSERT INTO erp.work_order(tenant_id, unit_id, item_id, bom_id, route_id, "
        "work_order_no, planned_qty, planned_start, planned_finish) VALUES "
        "(erp.current_tenant_id(), :u, :i, :b, :r, :n, :q, :s, :f) RETURNING work_order_id",
        {"u": body.unit_id, "i": body.item_id, "b": bom["bom_id"], "r": route["route_id"],
         "n": number, "q": body.planned_qty, "s": body.planned_start, "f": body.planned_finish})
    assert created is not None
    await audit(ctx, "work_order.create", "work_order", created["work_order_id"],
                {"work_order_no": number, "item_id": body.item_id, "planned_qty": body.planned_qty})
    result = await load_wo(ctx, created["work_order_id"])
    assert result is not None
    return result


@router.get("/work-orders", response_model=Page[WorkOrderSummary])
async def list_work_orders(
    wo_status: str | None = Query(None, alias="status"), unit_id: UUID | None = None,
    item_id: UUID | None = None, page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[WorkOrderSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "w.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for column, key, value in (("w.status", "status", wo_status.upper() if wo_status else None),
                               ("w.unit_id", "unit", unit_id), ("w.item_id", "item", item_id)):
        if value is not None:
            conditions.append(f"{column} = :{key}")
            params[key] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{WO_SQL}{where} ORDER BY w.work_order_no DESC LIMIT :limit "
                       "OFFSET :offset", {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.work_order w{where}", params)
    return Page(items=[WorkOrderSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/work-orders/{work_order_id}", response_model=WorkOrderOut)
async def get_work_order(work_order_id: UUID, ctx: Ctx = Depends(read)) -> WorkOrderOut:
    found = await load_wo(ctx, work_order_id)
    if found is None:
        raise not_found("Work order")
    return found


async def set_wo_status(ctx: Ctx, work_order_id: UUID, new: str, *allowed: str) -> WorkOrderOut:
    head = await require_wo(ctx, work_order_id, *allowed)
    if new == "CANCELLED" and await count(
            ctx, "SELECT count(*) FROM erp.prod_lot WHERE work_order_id = :id",
            {"id": work_order_id}):
        raise HTTPException(status.HTTP_409_CONFLICT, "Lots were already started on this order")
    if new == "COMPLETED" and await count(
            ctx, "SELECT count(*) FROM erp.prod_lot WHERE work_order_id = :id AND status = "
            "'IN_PROCESS'", {"id": work_order_id}):
        raise HTTPException(status.HTTP_409_CONFLICT, "Some lots are still in process")
    await rows(ctx, "UPDATE erp.work_order SET status = :s WHERE work_order_id = :id "
               "RETURNING work_order_id", {"s": new, "id": work_order_id})
    await audit(ctx, f"work_order.{new.lower()}", "work_order", work_order_id,
                {"work_order_no": head["work_order_no"]})
    result = await load_wo(ctx, work_order_id)
    assert result is not None
    return result


@router.post("/work-orders/{work_order_id}/release", response_model=WorkOrderOut)
async def release_work_order(work_order_id: UUID, ctx: Ctx = Depends(plan)) -> WorkOrderOut:
    return await set_wo_status(ctx, work_order_id, "RELEASED", "PLANNED")


@router.post("/work-orders/{work_order_id}/cancel", response_model=WorkOrderOut)
async def cancel_work_order(work_order_id: UUID, ctx: Ctx = Depends(plan)) -> WorkOrderOut:
    return await set_wo_status(ctx, work_order_id, "CANCELLED", "PLANNED", "RELEASED")


@router.post("/work-orders/{work_order_id}/complete", response_model=WorkOrderOut)
async def complete_work_order(work_order_id: UUID, ctx: Ctx = Depends(plan)) -> WorkOrderOut:
    return await set_wo_status(ctx, work_order_id, "COMPLETED", "IN_PROGRESS")


# ------------------------------------------------------------------ production lots
class LotIn(Strict):
    work_order_id: UUID
    quantity: Decimal = Qty
    store_id: UUID | None = None  # where raw-material components are issued from


class MoveIn(Strict):
    rejects: Decimal = Field(default=Decimal(0), ge=0, le=Decimal(1_000_000_000), max_digits=18,
                             decimal_places=3)
    notes: str | None = Field(default=None, max_length=500)


class PackIn(Strict):
    fg_store_id: UUID
    notes: str | None = Field(default=None, max_length=500)


class SourceOut(BaseModel):
    consumption_type: str
    quantity: Decimal
    source: str  # lot number, batch number or production lot number
    item_code: str | None
    consumed_at: datetime


class MoveOut(BaseModel):
    from_stage: str | None
    to_stage: str
    actor: str
    quantity_in: Decimal
    quantity_out: Decimal
    rejects: Decimal
    moved_at: datetime
    notes: str | None


class PackingOut(BaseModel):
    label_code: str
    packed_qty: Decimal
    packed_at: datetime
    fg_store_id: UUID
    fg_lot_no: str | None


class LotSummary(BaseModel):
    prod_lot_id: UUID
    lot_no: str
    unit_id: UUID
    work_order_id: UUID
    work_order_no: str
    item_id: UUID
    item_code: str
    route_code: str
    stage_code: str
    stage_name: str
    is_qa_gate: bool
    is_end: bool
    quantity: Decimal
    status: str
    created_at: datetime


class LotOut(LotSummary):
    sources: list[SourceOut]
    moves: list[MoveOut]
    packing: PackingOut | None


LOT_SQL = (
    "SELECT l.prod_lot_id, l.lot_no, l.unit_id, l.work_order_id, w.work_order_no, l.item_id, "
    "i.code AS item_code, r.code AS route_code, s.stage_code, s.stage_name, s.is_qa_gate, "
    "s.is_end, l.quantity, l.status, l.created_at FROM erp.prod_lot l JOIN erp.work_order w ON "
    "w.work_order_id = l.work_order_id JOIN erp.item i ON i.item_id = l.item_id JOIN erp.route r "
    "ON r.route_id = l.route_id JOIN erp.route_stage s ON s.route_stage_id = l.current_stage_id"
)


async def load_lot(ctx: Ctx, prod_lot_id: UUID) -> LotOut | None:
    clause, params = scope_clause(ctx, "l.unit_id")
    head = await one(
        ctx, f"{LOT_SQL} WHERE l.prod_lot_id = :id{' AND ' + clause if clause else ''}",
        {**params, "id": prod_lot_id})
    if head is None:
        return None
    sources = await rows(
        ctx,
        "SELECT c.consumption_type, c.quantity, c.consumed_at, COALESCE(t.lot_no, b.batch_no, "
        "p.lot_no) AS source, COALESCE(ti.code, pi.code) AS item_code FROM erp.lot_consumption c "
        "LEFT JOIN erp.lot t ON t.lot_id = c.source_lot_id LEFT JOIN erp.item ti ON "
        "ti.item_id = t.item_id LEFT JOIN erp.compound_batch b ON b.batch_id = c.source_batch_id "
        "LEFT JOIN erp.prod_lot p ON p.prod_lot_id = c.source_prod_lot_id LEFT JOIN erp.item pi "
        "ON pi.item_id = p.item_id WHERE c.prod_lot_id = :id ORDER BY c.consumed_at, "
        "c.consumption_id", {"id": prod_lot_id})
    moves = await rows(
        ctx,
        "SELECT fs.stage_code AS from_stage, ts.stage_code AS to_stage, u.display_name AS actor, "
        "m.quantity_in, m.quantity_out, m.rejects, m.moved_at, m.notes FROM erp.prod_lot_move m "
        "LEFT JOIN erp.route_stage fs ON fs.route_stage_id = m.from_stage_id JOIN "
        "erp.route_stage ts ON ts.route_stage_id = m.to_stage_id JOIN erp.app_user u ON "
        "u.user_id = m.actor_user_id WHERE m.prod_lot_id = :id ORDER BY m.moved_at, m.move_id",
        {"id": prod_lot_id})
    packed = await one(
        ctx, "SELECT p.label_code, p.packed_qty, p.packed_at, p.fg_store_id, t.lot_no AS "
        "fg_lot_no FROM erp.packing p LEFT JOIN erp.lot t ON t.lot_id = p.fg_lot_id WHERE "
        "p.prod_lot_id = :id", {"id": prod_lot_id})
    return LotOut(**head, sources=[SourceOut(**s) for s in sources],
                  moves=[MoveOut(**m) for m in moves],
                  packing=PackingOut(**packed) if packed else None)


async def stages_of(ctx: Ctx, route_id: UUID) -> list[dict[str, Any]]:
    return await rows(
        ctx, "SELECT route_stage_id, sequence_no, stage_code, stage_name, is_qa_gate, is_end "
        "FROM erp.route_stage WHERE route_id = :id ORDER BY sequence_no", {"id": route_id})


async def record_move(
    ctx: Ctx, prod_lot_id: UUID, from_stage: UUID | None, to_stage: UUID, quantity_in: Decimal,
    quantity_out: Decimal, rejects: Decimal, notes: str | None,
) -> None:
    await rows(
        ctx, "INSERT INTO erp.prod_lot_move(tenant_id, prod_lot_id, from_stage_id, to_stage_id, "
        "actor_user_id, quantity_in, quantity_out, rejects, notes) VALUES "
        "(erp.current_tenant_id(), :l, :f, :t, :me, :qi, :qo, :rj, :n) RETURNING move_id",
        {"l": prod_lot_id, "f": from_stage, "t": to_stage, "me": ctx.principal.profile.app_user_id,
         "qi": quantity_in, "qo": quantity_out, "rj": rejects, "n": notes})


async def consume(
    ctx: Ctx, prod_lot_id: UUID, kind: str, quantity: Decimal, *, lot_id: UUID | None = None,
    batch_id: UUID | None = None, source_prod_lot_id: UUID | None = None,
) -> None:
    await rows(
        ctx, "INSERT INTO erp.lot_consumption(tenant_id, prod_lot_id, source_lot_id, "
        "source_batch_id, source_prod_lot_id, consumption_type, quantity) VALUES "
        "(erp.current_tenant_id(), :p, :l, :b, :s, :k, :q) RETURNING consumption_id",
        {"p": prod_lot_id, "l": lot_id, "b": batch_id, "s": source_prod_lot_id, "k": kind,
         "q": quantity})


async def draw_inserts(
    ctx: Ctx, unit_id: UUID, item_id: UUID, pieces: Decimal
) -> list[tuple[UUID, Decimal]]:
    """Take metal inserts from lots that reached "Ready for moulding", oldest lot first."""
    ids = [r["prod_lot_id"] for r in await rows(
        ctx, "SELECT prod_lot_id FROM erp.prod_lot WHERE unit_id = :u AND item_id = :i AND "
        "status = 'READY' AND quantity > 0", {"u": unit_id, "i": item_id})]
    if ids:
        await rows(ctx, "SELECT prod_lot_id FROM erp.prod_lot WHERE prod_lot_id = ANY(CAST(:ids AS "
                   "uuid[])) ORDER BY prod_lot_id FOR UPDATE", {"ids": ids})
    ready = await rows(
        ctx, "SELECT prod_lot_id, quantity FROM erp.prod_lot WHERE prod_lot_id = "
        "ANY(CAST(:ids AS uuid[])) AND status = 'READY' AND quantity > 0 ORDER BY created_at, "
        "prod_lot_id", {"ids": ids})
    draws: list[tuple[UUID, Decimal]] = []
    left = pieces
    for lot in ready:
        if left <= 0:
            break
        take = min(left, lot["quantity"])
        draws.append((lot["prod_lot_id"], take))
        left -= take
    if left > 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Not enough metal inserts ready for moulding: {pieces - left} available, "
            f"{pieces} needed")
    for lot_id, take in draws:
        await rows(
            ctx, "UPDATE erp.prod_lot SET quantity = quantity - :q, status = CASE WHEN quantity "
            "- :q = 0 THEN 'CONSUMED' ELSE status END WHERE prod_lot_id = :id RETURNING "
            "prod_lot_id", {"q": take, "id": lot_id})
    return draws


@router.post("/lots", response_model=LotOut, status_code=status.HTTP_201_CREATED)
async def start_lot(body: LotIn, ctx: Ctx = Depends(move_permission)) -> LotOut:
    """Start a production lot: consume what the bill asks for, or refuse the whole lot."""
    wo = await require_wo(ctx, body.work_order_id, "RELEASED", "IN_PROGRESS")
    started = await one(
        ctx, "SELECT COALESCE(sum(m.quantity_out), 0) AS q FROM erp.prod_lot_move m JOIN "
        "erp.prod_lot l ON l.prod_lot_id = m.prod_lot_id WHERE l.work_order_id = :id AND "
        "m.from_stage_id IS NULL", {"id": body.work_order_id})
    assert started is not None
    if started["q"] + body.quantity > wo["planned_qty"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The order plans {wo['planned_qty']}; {started['q']} is already started, so "
            f"{body.quantity} more would exceed it")
    components = await bom_components(ctx, wo["bom_id"])
    needs_store = any(kind_of(c) == "MATERIAL" for c in components)
    store = None
    if needs_store:
        if body.store_id is None:
            raise HTTPException(422, "Say which store the raw materials are issued from")
        store = await store_in_scope(ctx, body.store_id)
        if store["unit_id"] != wo["unit_id"] or not store["active"]:
            raise HTTPException(422, "The store must be an active store of the order's plant")
    stages = await stages_of(ctx, wo["route_id"])
    first = stages[0]
    number = await next_number(ctx, "prod_lot")
    created = await one(
        ctx, "INSERT INTO erp.prod_lot(tenant_id, unit_id, work_order_id, item_id, route_id, "
        "current_stage_id, lot_no, quantity) VALUES (erp.current_tenant_id(), :u, :w, :i, :r, "
        ":s, :n, :q) RETURNING prod_lot_id",
        {"u": wo["unit_id"], "w": body.work_order_id, "i": wo["item_id"], "r": wo["route_id"],
         "s": first["route_stage_id"], "n": number, "q": body.quantity})
    assert created is not None
    lot_id: UUID = created["prod_lot_id"]
    issue_rule = await rules.get_value(ctx, "issueRule")
    steps = 0
    for component in sorted(components, key=lambda c: str(c["component_item_id"])):
        need = required(component, body.quantity)
        kind = kind_of(component)
        if kind == "COMPOUND":
            for batch_id, kilos in await allocate(ctx, wo["unit_id"], component["compound_id"],
                                                  need):
                await consume(ctx, lot_id, "COMPOUND_BATCH", kilos, batch_id=batch_id)
        elif kind == "INSERT":
            for source_id, pieces in await draw_inserts(
                    ctx, wo["unit_id"], component["component_item_id"], need):
                await consume(ctx, lot_id, "METAL_PROD_LOT", pieces, source_prod_lot_id=source_id)
        else:
            assert store is not None
            lots = await candidates(ctx, store["store_id"], component["component_item_id"],
                                    lock=True)
            picks, shortfall, blocked = choose(lots, need, issue_rule)
            if shortfall > 0:
                expired = sum((b["available"] for b in blocked), Decimal(0))
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"Not enough stock of {component['component_code']}: {need - shortfall} "
                    f"can be issued, {need} needed"
                    + (f" ({expired} more is expired)" if expired else ""))
            for pick in picks:
                steps += 1
                ledger_id = await ledger.move(
                    ctx, unit_id=wo["unit_id"], item_id=component["component_item_id"],
                    lot_id=pick["lot_id"], store_id=store["store_id"], delta=-pick["take"],
                    movement_type="ISSUE", reference_type="PROD_LOT", reference_id=lot_id,
                    idempotency_key=f"prodlot:{lot_id}:{steps}")
                await ledger.take_layers(
                    ctx, unit_id=wo["unit_id"], item_id=component["component_item_id"],
                    quantity=pick["take"], issue_ledger_id=ledger_id)
                await consume(ctx, lot_id, "RAW_MATERIAL", pick["take"], lot_id=pick["lot_id"])
    await record_move(ctx, lot_id, None, first["route_stage_id"], body.quantity, body.quantity,
                      Decimal(0), None)
    await rows(ctx, "UPDATE erp.work_order SET status = 'IN_PROGRESS' WHERE work_order_id = :id "
               "RETURNING work_order_id", {"id": body.work_order_id})
    await audit(ctx, "prod_lot.start", "prod_lot", lot_id,
                {"lot_no": number, "work_order_no": wo["work_order_no"],
                 "quantity": body.quantity})
    result = await load_lot(ctx, lot_id)
    assert result is not None
    return result


@router.get("/lots", response_model=Page[LotSummary])
async def list_lots(
    work_order_id: UUID | None = None, lot_status: str | None = Query(None, alias="status"),
    stage: str | None = None, page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[LotSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "l.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for column, key, value in (("l.work_order_id", "wo", work_order_id),
                               ("l.status", "status", lot_status.upper() if lot_status else None),
                               ("s.stage_code", "stage", stage.upper() if stage else None)):
        if value is not None:
            conditions.append(f"{column} = :{key}")
            params[key] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{LOT_SQL}{where} ORDER BY l.lot_no DESC LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    total = await count(
        ctx, "SELECT count(*) FROM erp.prod_lot l JOIN erp.route_stage s ON s.route_stage_id = "
        f"l.current_stage_id{where}", params)
    return Page(items=[LotSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/lots/{prod_lot_id}", response_model=LotOut)
async def get_lot(prod_lot_id: UUID, ctx: Ctx = Depends(read)) -> LotOut:
    found = await load_lot(ctx, prod_lot_id)
    if found is None:
        raise not_found("Production lot")
    return found


async def lock_lot(ctx: Ctx, prod_lot_id: UUID) -> dict[str, Any]:
    clause, params = scope_clause(ctx, "unit_id")
    lot = await one(
        ctx, "SELECT prod_lot_id, lot_no, unit_id, work_order_id, item_id, route_id, "
        f"current_stage_id, quantity, status FROM erp.prod_lot WHERE prod_lot_id = :id"
        f"{' AND ' + clause if clause else ''} FOR UPDATE", {**params, "id": prod_lot_id})
    if lot is None:
        raise not_found("Production lot")
    if lot["status"] != "IN_PROCESS":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The lot is {str(lot['status']).lower().replace('_', ' ')}")
    return lot


@router.post("/lots/{prod_lot_id}/move", response_model=LotOut)
async def move_lot(
    prod_lot_id: UUID, body: MoveIn, ctx: Ctx = Depends(read_ctx)
) -> LotOut:
    """Send the lot to the next stage. Leaving a QA gate needs qa.approve, otherwise route.move."""
    lot = await lock_lot(ctx, prod_lot_id)
    stages = await stages_of(ctx, lot["route_id"])
    here = next(s for s in stages if s["route_stage_id"] == lot["current_stage_id"])
    needed = "qa.approve" if here["is_qa_gate"] else "route.move"
    if not has_permission(ctx.principal, needed):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Requires permission {needed} to pass {here['stage_name'].lower()}")
    target = stages[here["sequence_no"]] if here["sequence_no"] < len(stages) else None
    if target is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The lot is at the last stage of its route")
    if target["stage_code"] == "FG_STORE":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Book the lot into the finished-goods store with the packing step")
    if body.rejects > lot["quantity"]:
        raise HTTPException(422, f"Rejects exceed the {lot['quantity']} in the lot")
    remaining = lot["quantity"] - body.rejects
    new_status = ("SCRAPPED" if remaining == 0
                  else "READY" if target["is_end"] else "IN_PROCESS")
    await record_move(ctx, prod_lot_id, here["route_stage_id"], target["route_stage_id"],
                      lot["quantity"], remaining, body.rejects, body.notes)
    await rows(
        ctx, "UPDATE erp.prod_lot SET current_stage_id = :s, quantity = :q, status = :st WHERE "
        "prod_lot_id = :id RETURNING prod_lot_id",
        {"s": target["route_stage_id"], "q": remaining, "st": new_status, "id": prod_lot_id})
    await audit(ctx, "prod_lot.move", "prod_lot", prod_lot_id,
                {"lot_no": lot["lot_no"], "from": here["stage_code"], "to": target["stage_code"],
                 "rejects": body.rejects})
    result = await load_lot(ctx, prod_lot_id)
    assert result is not None
    return result


@router.post("/lots/{prod_lot_id}/pack", response_model=LotOut)
async def pack_lot(
    prod_lot_id: UUID, body: PackIn, ctx: Ctx = Depends(move_permission)
) -> LotOut:
    """Packing and barcoding: book the finished goods into the FG store with a lot label."""
    lot = await lock_lot(ctx, prod_lot_id)
    stages = await stages_of(ctx, lot["route_id"])
    here = next(s for s in stages if s["route_stage_id"] == lot["current_stage_id"])
    end = stages[-1]
    if here["stage_code"] != "PACKING" or end["stage_code"] != "FG_STORE":
        raise HTTPException(status.HTTP_409_CONFLICT, "The lot is not at the packing stage")
    store = await store_in_scope(ctx, body.fg_store_id)
    if store["unit_id"] != lot["unit_id"] or not store["active"]:
        raise HTTPException(422, "The store must be an active store of the lot's plant")
    item = await one(ctx, "SELECT shelf_life_days FROM erp.item WHERE item_id = :id",
                     {"id": lot["item_id"]})
    assert item is not None
    packed = lot["quantity"]
    label = await next_number(ctx, "packing")
    fg_lot = await one(
        ctx, "INSERT INTO erp.lot(tenant_id, unit_id, item_id, store_id, lot_no, received_at, "
        "expiry_at, status) VALUES (erp.current_tenant_id(), :u, :i, :s, :n, now(), "
        "CASE WHEN CAST(:life AS int) IS NULL THEN NULL ELSE now() + make_interval(days => "
        "CAST(:life AS int)) END, 'AVAILABLE') RETURNING lot_id",
        {"u": lot["unit_id"], "i": lot["item_id"], "s": body.fg_store_id, "n": lot["lot_no"],
         "life": item["shelf_life_days"]})
    assert fg_lot is not None
    packing = await one(
        ctx, "INSERT INTO erp.packing(tenant_id, prod_lot_id, fg_store_id, packed_by, packed_qty, "
        "label_code, fg_lot_id) VALUES (erp.current_tenant_id(), :p, :s, :me, :q, :lb, :l) "
        "RETURNING packing_id",
        {"p": prod_lot_id, "s": body.fg_store_id, "me": ctx.principal.profile.app_user_id,
         "q": packed, "lb": label, "l": fg_lot["lot_id"]})
    assert packing is not None
    # ponytail: finished goods enter stock at zero cost until Phase 10 costs production
    ledger_id = await ledger.move(
        ctx, unit_id=lot["unit_id"], item_id=lot["item_id"], lot_id=fg_lot["lot_id"],
        store_id=body.fg_store_id, delta=packed, movement_type="RECEIPT",
        reference_type="PACKING", reference_id=packing["packing_id"],
        idempotency_key=f"packing:{packing['packing_id']}")
    await ledger.add_layer(ctx, ledger_id=ledger_id, item_id=lot["item_id"],
                           lot_id=fg_lot["lot_id"], quantity=packed, unit_cost=Decimal(0))
    await record_move(ctx, prod_lot_id, here["route_stage_id"], end["route_stage_id"], packed,
                      packed, Decimal(0), body.notes)
    await rows(
        ctx, "UPDATE erp.prod_lot SET current_stage_id = :s, status = 'COMPLETED' WHERE "
        "prod_lot_id = :id RETURNING prod_lot_id",
        {"s": end["route_stage_id"], "id": prod_lot_id})
    await audit(ctx, "prod_lot.pack", "prod_lot", prod_lot_id,
                {"lot_no": lot["lot_no"], "label_code": label, "quantity": packed})
    result = await load_lot(ctx, prod_lot_id)
    assert result is not None
    return result


# ------------------------------------------------------------------ traceability (BR-04)
async def upstream(ctx: Ctx, prod_lot_id: UUID, depth: int = 0) -> dict[str, Any]:
    """Everything a lot was made from: raw-material lots, compound batches (with the raw material
    they were weighed from) and metal lots (with their own sources)."""
    lot = await one(
        ctx, "SELECT l.lot_no, l.quantity, l.status, i.code AS item_code, w.work_order_no "
        "FROM erp.prod_lot l JOIN erp.item i ON i.item_id = l.item_id JOIN erp.work_order w ON "
        "w.work_order_id = l.work_order_id WHERE l.prod_lot_id = :id", {"id": prod_lot_id})
    assert lot is not None
    node: dict[str, Any] = {**lot, "raw_material": [], "compound_batches": [], "metal_lots": []}
    for src in await rows(
            ctx, "SELECT consumption_type, source_lot_id, source_batch_id, source_prod_lot_id, "
            "quantity FROM erp.lot_consumption WHERE prod_lot_id = :id ORDER BY consumed_at, "
            "consumption_id", {"id": prod_lot_id}):
        if src["consumption_type"] == "RAW_MATERIAL":
            raw = await one(ctx, "SELECT t.lot_no, i.code AS item_code FROM erp.lot t JOIN "
                            "erp.item i ON i.item_id = t.item_id WHERE t.lot_id = :id",
                            {"id": src["source_lot_id"]})
            node["raw_material"].append({**(raw or {}), "quantity": src["quantity"]})
        elif src["consumption_type"] == "COMPOUND_BATCH":
            batch = await one(ctx, "SELECT batch_no, compound_code FROM "
                              "erp.v_compound_batch_status WHERE batch_id = :id",
                              {"id": src["source_batch_id"]})
            weighed = await rows(
                ctx, "SELECT t.lot_no, i.code AS item_code, w.actual_qty AS quantity FROM "
                "erp.batch_weighing_line w JOIN erp.lot t ON t.lot_id = w.source_lot_id JOIN "
                "erp.item i ON i.item_id = t.item_id WHERE w.batch_id = :id ORDER BY i.code, "
                "t.lot_no", {"id": src["source_batch_id"]})
            node["compound_batches"].append(
                {**(batch or {}), "quantity": src["quantity"], "raw_material": weighed})
        elif src["consumption_type"] == "METAL_PROD_LOT" and depth < 5:
            node["metal_lots"].append(
                {**await upstream(ctx, src["source_prod_lot_id"], depth + 1),
                 "quantity_used": src["quantity"]})
    return node


@router.get("/lots/{prod_lot_id}/trace", response_model=dict[str, Any])
async def trace_lot(prod_lot_id: UUID, ctx: Ctx = Depends(read)) -> dict[str, Any]:
    """Backward trace: the finished or in-process lot, its sources and where it was packed."""
    lot = await load_lot(ctx, prod_lot_id)
    if lot is None:
        raise not_found("Production lot")
    tree = await upstream(ctx, prod_lot_id)
    tree["packing"] = lot.packing.model_dump(mode="json") if lot.packing else None
    return tree


@router.get("/batches/{batch_id}/used-in", response_model=list[LotSummary])
async def batch_used_in(batch_id: UUID, ctx: Ctx = Depends(read)) -> list[LotSummary]:
    """Forward trace for a recall: every production lot that drew from a compound batch."""
    clause, params = scope_clause(ctx, "l.unit_id")
    items = await rows(
        ctx, f"{LOT_SQL} WHERE l.prod_lot_id IN (SELECT prod_lot_id FROM erp.lot_consumption "
        f"WHERE source_batch_id = :b){' AND ' + clause if clause else ''} ORDER BY l.lot_no",
        {**params, "b": batch_id})
    return [LotSummary(**i) for i in items]
