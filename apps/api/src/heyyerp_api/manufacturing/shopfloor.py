"""Shop-floor records: shift entries, downtime, rework, returned material, WIP, OEE and checks
(BRD FR-MFG-09, FR-MFG-11, BR-06).

A shift entry (A, B or C, per machine and work order) records good pieces, rejects by reason and
downtime by reason; the totals are computed from the reasons. Entries for a closed month are
refused: a month's production stays open until the business rule `lockDay` plus
`prodLockExtraDays` (default: its last day plus one). `missing` lists the machines that ran
recently but have no entry for a shift. OEE has availability and quality; performance needs an
ideal cycle time, which the schema does not hold yet. Rework takes rejected pieces back into a lot
that is still in process. Unused raw material goes back to stores into the lot it came from.
Shift entries need mfg.entry, rework and returns route.move.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from heyyerp_api.business_rules import service as rules
from heyyerp_api.finance.period import last_open_day
from heyyerp_api.inventory import ledger
from heyyerp_api.manufacturing.orders import (
    LotOut,
    bom_components,
    load_lot,
    load_wo,
    lock_lot,
    required,
)
from heyyerp_api.masters.common import (
    Ctx,
    Page,
    Paging,
    Strict,
    audit,
    count,
    db_errors,
    not_found,
    one,
    requires,
    requires_module,
    rows,
    scope_clause,
)
from heyyerp_api.security import has_permission

router = APIRouter(prefix="/manufacturing", tags=["manufacturing"])
enter = requires("mfg.entry")
move = requires("route.move")
read = requires_module("manufacturing")
SHIFT_MINUTES = 480  # 8-hour shifts A, B and C
ShiftCode = Literal["A", "B", "C"]


# ------------------------------------------------------------------ shift entries
class RejectIn(Strict):
    reason: str = Field(min_length=2, max_length=100)
    quantity: Decimal = Field(gt=0, le=Decimal(1_000_000), max_digits=18, decimal_places=3)


class DowntimeIn(Strict):
    reason: str = Field(min_length=2, max_length=100)
    minutes: int = Field(gt=0, le=SHIFT_MINUTES)


class ShiftIn(Strict):
    work_order_id: UUID
    machine_id: UUID
    work_date: date
    shift_code: ShiftCode
    good_qty: Decimal = Field(ge=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)
    rejects: list[RejectIn] = Field(default_factory=list, max_length=30)
    downtime: list[DowntimeIn] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def _totals(self) -> "ShiftIn":
        if sum(d.minutes for d in self.downtime) > SHIFT_MINUTES:
            raise ValueError(f"Downtime cannot exceed the {SHIFT_MINUTES}-minute shift")
        if self.good_qty == 0 and not self.rejects and not self.downtime:
            raise ValueError("An entry needs good pieces, rejects or downtime")
        return self


class LossOut(BaseModel):
    kind: str
    reason: str
    quantity: Decimal
    minutes: int


class ShiftOut(BaseModel):
    shift_entry_id: UUID
    unit_id: UUID
    work_order_id: UUID
    work_order_no: str
    machine_id: UUID
    machine_code: str
    operator: str
    work_date: date
    shift_code: str
    good_qty: Decimal
    reject_qty: Decimal
    downtime_minutes: int
    losses: list[LossOut]


SHIFT_SQL = (
    "SELECT s.shift_entry_id, s.unit_id, s.work_order_id, w.work_order_no, s.machine_id, "
    "m.code AS machine_code, u.display_name AS operator, s.work_date, s.shift_code, s.good_qty, "
    "s.reject_qty, s.downtime_minutes FROM erp.shift_production s JOIN erp.work_order w ON "
    "w.work_order_id = s.work_order_id JOIN erp.machine m ON m.machine_id = s.machine_id JOIN "
    "erp.app_user u ON u.user_id = s.operator_id"
)


async def load_shift(ctx: Ctx, entry_id: UUID) -> ShiftOut | None:
    clause, params = scope_clause(ctx, "s.unit_id")
    head = await one(
        ctx, f"{SHIFT_SQL} WHERE s.shift_entry_id = :id{' AND ' + clause if clause else ''}",
        {**params, "id": entry_id})
    if head is None:
        return None
    losses = await rows(ctx, "SELECT kind, reason, quantity, minutes FROM erp.shift_loss WHERE "
                        "shift_entry_id = :id ORDER BY kind, reason", {"id": entry_id})
    return ShiftOut(**head, losses=[LossOut(**x) for x in losses])


@router.post("/shift-entries", response_model=ShiftOut, status_code=status.HTTP_201_CREATED)
async def record_shift(body: ShiftIn, ctx: Ctx = Depends(enter)) -> ShiftOut:
    today = date.today()
    if body.work_date > today:
        raise HTTPException(422, "A shift entry cannot be dated in the future")
    closes = last_open_day(body.work_date, await rules.get_value(ctx, "lockDay"),
                           await rules.get_value(ctx, "prodLockExtraDays"))
    if today > closes:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Production for that month was locked on {closes}")
    clause, params = scope_clause(ctx, "unit_id")
    wo = await one(
        ctx, "SELECT work_order_id, unit_id, status FROM erp.work_order WHERE work_order_id = :id"
        f"{' AND ' + clause if clause else ''}", {**params, "id": body.work_order_id})
    if wo is None:
        raise not_found("Work order")
    if wo["status"] not in ("RELEASED", "IN_PROGRESS", "COMPLETED"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Shift output is booked against a released work order")
    machine = await one(ctx, "SELECT unit_id, active FROM erp.machine WHERE machine_id = :id",
                        {"id": body.machine_id})
    if machine is None or not machine["active"] or machine["unit_id"] != wo["unit_id"]:
        raise HTTPException(422, "The machine must be an active machine of the order's plant")
    with db_errors("This machine already has an entry for that shift and work order"):
        created = await one(
            ctx, "INSERT INTO erp.shift_production(tenant_id, unit_id, work_order_id, machine_id, "
            "operator_id, work_date, shift_code, good_qty, reject_qty, downtime_minutes) VALUES "
            "(erp.current_tenant_id(), :u, :w, :m, :me, :d, :s, :g, :r, :t) "
            "RETURNING shift_entry_id",
            {"u": wo["unit_id"], "w": body.work_order_id, "m": body.machine_id,
             "me": ctx.principal.profile.app_user_id, "d": body.work_date, "s": body.shift_code,
             "g": body.good_qty, "r": sum((x.quantity for x in body.rejects), Decimal(0)),
             "t": sum(x.minutes for x in body.downtime)})
    assert created is not None
    for rej in body.rejects:
        await rows(
            ctx, "INSERT INTO erp.shift_loss(tenant_id, shift_entry_id, kind, reason, quantity) "
            "VALUES (erp.current_tenant_id(), :e, 'REJECT', :r, :q) RETURNING loss_id",
            {"e": created["shift_entry_id"], "r": rej.reason.strip(), "q": rej.quantity})
    for down in body.downtime:
        await rows(
            ctx, "INSERT INTO erp.shift_loss(tenant_id, shift_entry_id, kind, reason, minutes) "
            "VALUES (erp.current_tenant_id(), :e, 'DOWNTIME', :r, :m) RETURNING loss_id",
            {"e": created["shift_entry_id"], "r": down.reason.strip(), "m": down.minutes})
    await audit(ctx, "shift.record", "shift_entry", created["shift_entry_id"],
                {"work_date": body.work_date, "shift": body.shift_code, "good": body.good_qty})
    result = await load_shift(ctx, created["shift_entry_id"])
    assert result is not None
    return result


@router.get("/shift-entries", response_model=Page[ShiftOut])
async def list_shifts(
    work_order_id: UUID | None = None, machine_id: UUID | None = None,
    date_from: date | None = None, date_to: date | None = None,
    page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[ShiftOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "s.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for text_, key, value in (("s.work_order_id = :wo", "wo", work_order_id),
                              ("s.machine_id = :machine", "machine", machine_id),
                              ("s.work_date >= :frm", "frm", date_from),
                              ("s.work_date <= :to", "to", date_to)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    heads = await rows(ctx, f"{SHIFT_SQL}{where} ORDER BY s.work_date DESC, s.shift_code, "
                       "m.code LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    items = []
    for head in heads:
        losses = await rows(ctx, "SELECT kind, reason, quantity, minutes FROM erp.shift_loss "
                            "WHERE shift_entry_id = :id ORDER BY kind, reason",
                            {"id": head["shift_entry_id"]})
        items.append(ShiftOut(**head, losses=[LossOut(**x) for x in losses]))
    total = await count(ctx, f"SELECT count(*) FROM erp.shift_production s{where}", params)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


class Missing(BaseModel):
    unit_id: UUID
    machine_id: UUID
    machine_code: str
    work_date: date
    shift_code: str


@router.get("/shift-entries/missing", response_model=list[Missing])
async def missing_entries(
    work_date: date, shift_code: ShiftCode | None = None, ctx: Ctx = Depends(read)
) -> list[Missing]:
    """Machines that ran in the previous 7 days but have no entry for the shift (the alert)."""
    clause, params = scope_clause(ctx, "m.unit_id")
    shifts = [shift_code] if shift_code else ["A", "B", "C"]
    found: list[Missing] = []
    for shift in shifts:
        machines = await rows(
            ctx, "SELECT m.machine_id, m.unit_id, m.code FROM erp.machine m WHERE m.active AND "
            "EXISTS (SELECT 1 FROM erp.shift_production s WHERE s.machine_id = m.machine_id AND "
            "s.work_date >= CAST(:d AS date) - 7 AND s.work_date < :d) AND NOT EXISTS (SELECT 1 "
            "FROM erp.shift_production s WHERE s.machine_id = m.machine_id AND s.work_date = :d "
            f"AND s.shift_code = :s){' AND ' + clause if clause else ''} ORDER BY m.code",
            {**params, "d": work_date, "s": shift})
        found += [Missing(unit_id=m["unit_id"], machine_id=m["machine_id"],
                          machine_code=m["code"], work_date=work_date, shift_code=shift)
                  for m in machines]
    return found


class OeeRow(BaseModel):
    machine_id: UUID
    machine_code: str
    shifts: int
    planned_minutes: int
    downtime_minutes: int
    availability: Decimal
    good_qty: Decimal
    reject_qty: Decimal
    quality: Decimal | None
    performance: Decimal | None  # needs an ideal cycle time, which is not recorded yet


@router.get("/oee", response_model=list[OeeRow])
async def oee(
    date_from: date, date_to: date, unit_id: UUID | None = None, ctx: Ctx = Depends(read)
) -> list[OeeRow]:
    """Availability (planned minutes less downtime) and quality (good / all pieces) per machine."""
    if date_to < date_from:
        raise HTTPException(422, "The end date is before the start date")
    clause, params = scope_clause(ctx, "s.unit_id")
    extra = ""
    if unit_id is not None:
        extra = " AND s.unit_id = :unit"
        params["unit"] = unit_id
    data = await rows(
        ctx, "SELECT s.machine_id, m.code AS machine_code, count(*) AS shifts, sum(s.good_qty) AS "
        "good_qty, sum(s.reject_qty) AS reject_qty, sum(s.downtime_minutes) AS downtime FROM "
        "erp.shift_production s JOIN erp.machine m ON m.machine_id = s.machine_id WHERE "
        f"s.work_date BETWEEN :a AND :b{extra}{' AND ' + clause if clause else ''} "
        "GROUP BY s.machine_id, m.code ORDER BY m.code", {**params, "a": date_from, "b": date_to})
    out = []
    for r in data:
        planned = r["shifts"] * SHIFT_MINUTES
        total = r["good_qty"] + r["reject_qty"]
        out.append(OeeRow(
            machine_id=r["machine_id"], machine_code=r["machine_code"], shifts=r["shifts"],
            planned_minutes=planned, downtime_minutes=r["downtime"],
            availability=((planned - r["downtime"]) / Decimal(planned)).quantize(Decimal("0.0001")),
            good_qty=r["good_qty"], reject_qty=r["reject_qty"],
            quality=(r["good_qty"] / total).quantize(Decimal("0.0001")) if total else None,
            performance=None))
    return out


# ------------------------------------------------------------------ rework and returns
class ReworkIn(Strict):
    quantity: Decimal = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)
    reason: str = Field(min_length=3, max_length=200)


@router.post("/lots/{prod_lot_id}/rework", response_model=LotOut)
async def rework_lot(prod_lot_id: UUID, body: ReworkIn, ctx: Ctx = Depends(move)) -> LotOut:
    """Take rejected pieces back into the lot (still in process), up to what was rejected."""
    lot = await lock_lot(ctx, prod_lot_id)
    totals = await one(
        ctx, "SELECT COALESCE(sum(rejects), 0) AS rejected, (SELECT COALESCE(sum(quantity), 0) "
        "FROM erp.rework_record WHERE prod_lot_id = :id) AS reworked FROM erp.prod_lot_move "
        "WHERE prod_lot_id = :id", {"id": prod_lot_id})
    assert totals is not None
    open_rejects = totals["rejected"] - totals["reworked"]
    if body.quantity > open_rejects:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only {open_rejects} rejected pieces of this lot are waiting for rework")
    await rows(
        ctx, "INSERT INTO erp.rework_record(tenant_id, prod_lot_id, quantity, reason, created_by) "
        "VALUES (erp.current_tenant_id(), :l, :q, :r, :me) RETURNING rework_id",
        {"l": prod_lot_id, "q": body.quantity, "r": body.reason.strip(),
         "me": ctx.principal.profile.app_user_id})
    await rows(ctx, "UPDATE erp.prod_lot SET quantity = quantity + :q WHERE prod_lot_id = :id "
               "RETURNING prod_lot_id", {"q": body.quantity, "id": prod_lot_id})
    await audit(ctx, "prod_lot.rework", "prod_lot", prod_lot_id,
                {"lot_no": lot["lot_no"], "quantity": body.quantity, "reason": body.reason})
    result = await load_lot(ctx, prod_lot_id)
    assert result is not None
    return result


class ReturnIn(Strict):
    source_lot_no: str = Field(min_length=1, max_length=80)  # the stock lot the material came from
    quantity: Decimal = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)


@router.post("/lots/{prod_lot_id}/return-material", response_model=LotOut)
async def return_material(
    prod_lot_id: UUID, body: ReturnIn, ctx: Ctx = Depends(move)
) -> LotOut:
    """Hand unused raw material back to stores, into the lot it was issued from."""
    lot = await lock_lot(ctx, prod_lot_id)
    used = await one(
        ctx, "SELECT c.consumption_id, c.quantity, c.returned_qty, c.source_lot_id, t.store_id, "
        "t.item_id FROM erp.lot_consumption c JOIN erp.lot t ON t.lot_id = c.source_lot_id "
        "WHERE c.prod_lot_id = :p AND c.consumption_type = 'RAW_MATERIAL' AND t.lot_no = :n "
        "ORDER BY c.consumed_at LIMIT 1 FOR UPDATE OF c",
        {"p": prod_lot_id, "n": body.source_lot_no})
    if used is None:
        raise HTTPException(422, "This lot did not use raw material from that stock lot")
    left = used["quantity"] - used["returned_qty"]
    if body.quantity > left:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Only {left} of that material is left to return")
    ledger_id = await ledger.move(
        ctx, unit_id=lot["unit_id"], item_id=used["item_id"], lot_id=used["source_lot_id"],
        store_id=used["store_id"], delta=body.quantity, movement_type="RECEIPT",
        reference_type="PROD_RETURN", reference_id=prod_lot_id,
        idempotency_key=f"prodreturn:{used['consumption_id']}:{used['returned_qty']}")
    cost = await ledger.last_cost(ctx, lot["unit_id"], used["item_id"])
    await ledger.add_layer(ctx, ledger_id=ledger_id, item_id=used["item_id"],
                           lot_id=used["source_lot_id"], quantity=body.quantity,
                           unit_cost=cost or Decimal(0))
    await rows(ctx, "UPDATE erp.lot_consumption SET returned_qty = returned_qty + :q WHERE "
               "consumption_id = :id RETURNING consumption_id",
               {"q": body.quantity, "id": used["consumption_id"]})
    await audit(ctx, "prod_lot.return_material", "prod_lot", prod_lot_id,
                {"lot_no": lot["lot_no"], "source": body.source_lot_no, "quantity": body.quantity})
    result = await load_lot(ctx, prod_lot_id)
    assert result is not None
    return result


# ------------------------------------------------------------------ WIP and checks
class WipRow(BaseModel):
    route_code: str
    stage_code: str
    stage_name: str
    item_code: str
    lots: int
    quantity: Decimal


@router.get("/wip", response_model=list[WipRow])
async def work_in_progress(unit_id: UUID | None = None, ctx: Ctx = Depends(read)) -> list[WipRow]:
    """Lots in process, and metal inserts waiting for moulding, by route stage and item."""
    clause, params = scope_clause(ctx, "l.unit_id")
    extra = ""
    if unit_id is not None:
        extra = " AND l.unit_id = :unit"
        params["unit"] = unit_id
    data = await rows(
        ctx, "SELECT r.code AS route_code, s.stage_code, s.stage_name, i.code AS item_code, "
        "count(*) AS lots, sum(l.quantity) AS quantity FROM erp.prod_lot l JOIN erp.route r ON "
        "r.route_id = l.route_id JOIN erp.route_stage s ON s.route_stage_id = l.current_stage_id "
        "JOIN erp.item i ON i.item_id = l.item_id WHERE l.status IN ('IN_PROCESS', 'READY') AND "
        f"l.quantity > 0{extra}{' AND ' + clause if clause else ''} GROUP BY r.code, "
        "s.sequence_no, s.stage_code, s.stage_name, i.code ORDER BY r.code, s.sequence_no, i.code",
        params)
    return [WipRow(**d) for d in data]


class Problem(BaseModel):
    prod_lot_id: UUID
    lot_no: str
    started: Decimal
    accounted: Decimal


class LotReconciliation(BaseModel):
    ok: bool
    lots_checked: int
    problems: list[Problem]


@router.get("/reconciliation", response_model=LotReconciliation)
async def reconcile_lots(
    unit_id: UUID | None = None, ctx: Ctx = Depends(read)
) -> LotReconciliation:
    """Every piece started is somewhere: still in the lot, rejected, or drawn by another lot."""
    if not has_permission(ctx.principal, "mfg.plan") and not has_permission(
            ctx.principal, "qa.approve"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Requires permission mfg.plan")
    clause, params = scope_clause(ctx, "l.unit_id")
    extra = ""
    if unit_id is not None:
        extra = " AND l.unit_id = :unit"
        params["unit"] = unit_id
    data = await rows(
        ctx,
        "SELECT l.prod_lot_id, l.lot_no, "
        "(SELECT COALESCE(sum(m.quantity_out), 0) FROM erp.prod_lot_move m WHERE m.prod_lot_id = "
        "l.prod_lot_id AND m.from_stage_id IS NULL) + (SELECT COALESCE(sum(r.quantity), 0) FROM "
        "erp.rework_record r WHERE r.prod_lot_id = l.prod_lot_id) AS started, l.quantity + "
        "(SELECT COALESCE(sum(m.rejects), 0) FROM erp.prod_lot_move m WHERE m.prod_lot_id = "
        "l.prod_lot_id) + (SELECT COALESCE(sum(c.quantity), 0) FROM erp.lot_consumption c WHERE "
        f"c.source_prod_lot_id = l.prod_lot_id) AS accounted FROM erp.prod_lot l WHERE true{extra}"
        f"{' AND ' + clause if clause else ''}", params)
    problems = [Problem(prod_lot_id=d["prod_lot_id"], lot_no=d["lot_no"], started=d["started"],
                        accounted=d["accounted"]) for d in data if d["started"] != d["accounted"]]
    return LotReconciliation(ok=not problems, lots_checked=len(data), problems=problems)


class Variance(BaseModel):
    component_code: str
    required: Decimal
    consumed: Decimal
    returned: Decimal
    variance: Decimal


@router.get("/work-orders/{work_order_id}/material-variance", response_model=list[Variance])
async def material_variance(work_order_id: UUID, ctx: Ctx = Depends(read)) -> list[Variance]:
    """Required (for the pieces started) against consumed less returned, per bill component."""
    wo = await load_wo(ctx, work_order_id)
    if wo is None:
        raise not_found("Work order")
    consumed = {r["item_id"]: r for r in await rows(
        ctx,
        "SELECT item_id, sum(quantity) AS quantity, sum(returned) AS returned FROM ("
        "SELECT t.item_id, c.quantity, c.returned_qty AS returned FROM erp.lot_consumption c "
        "JOIN erp.prod_lot l ON l.prod_lot_id = c.prod_lot_id JOIN erp.lot t ON t.lot_id = "
        "c.source_lot_id WHERE l.work_order_id = :w UNION ALL SELECT co.output_item_id, "
        "c.quantity, 0 FROM erp.lot_consumption c JOIN erp.prod_lot l ON l.prod_lot_id = "
        "c.prod_lot_id JOIN erp.compound_batch b ON b.batch_id = c.source_batch_id JOIN "
        "erp.compound co ON co.compound_id = b.compound_id WHERE l.work_order_id = :w UNION ALL "
        "SELECT p.item_id, c.quantity, 0 FROM erp.lot_consumption c JOIN erp.prod_lot l ON "
        "l.prod_lot_id = c.prod_lot_id JOIN erp.prod_lot p ON p.prod_lot_id = "
        "c.source_prod_lot_id WHERE l.work_order_id = :w) u GROUP BY item_id",
        {"w": work_order_id})}
    out = []
    for comp in await bom_components(ctx, wo.bom_id):
        need = required(comp, wo.started_qty)
        got = consumed.get(comp["component_item_id"])
        used = got["quantity"] if got else Decimal(0)
        back = got["returned"] if got else Decimal(0)
        out.append(Variance(component_code=comp["component_code"], required=need, consumed=used,
                            returned=back, variance=used - back - need))
    return out
