"""Picking and dispatch of finished goods against a sales order (BRD FR-SAL-05).

Planning shows which finished-goods lots would be picked, oldest first (FIFO), and what is short.
Creating a dispatch picks for real: the lots are reserved for it, and it is refused when the stock
is short or when more than the order still needs is asked for (shipped plus other open dispatches
count). Dispatching consumes the reservations, issues the stock and its FIFO cost, adds to the
order line's shipped quantity, fulfils the delivery schedule oldest date first and moves the order
to partially shipped or shipped. Only AVAILABLE, unexpired lots are picked, so held, quarantined
or rejected stock never leaves. The packing list carries the customer's part numbers and the lot
labels; the trace leads from the dispatch back to compound batches and raw-material lots. Needs
sales.dispatch to change; reading needs the sales module.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from heyyerp_api.inventory import ledger
from heyyerp_api.inventory.issues import candidates, choose, store_in_scope
from heyyerp_api.manufacturing.orders import upstream
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
from heyyerp_api.sales import orders

router = APIRouter(prefix="/sales", tags=["sales"])
manage = requires("sales.dispatch")
read = requires_module("sales")
Qty = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)
PICK_RULE = "FIFO"  # BRD FR-SAL-05: dispatch picks finished-goods lots FIFO


class LineIn(Strict):
    so_line_id: UUID
    quantity: Decimal = Qty


class PlanIn(Strict):
    so_id: UUID
    store_id: UUID
    lines: list[LineIn] = Field(min_length=1, max_length=100)


class DispatchIn(PlanIn):
    vehicle_no: str | None = Field(default=None, max_length=30)
    transporter: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=300)


class PickOut(BaseModel):
    lot_id: UUID
    lot_no: str
    received_at: datetime
    quantity: Decimal


class PlanLine(BaseModel):
    so_line_id: UUID
    item_code: str
    requested: Decimal
    open_qty: Decimal
    picks: list[PickOut]
    shortfall: Decimal


class PlanOut(BaseModel):
    can_dispatch: bool
    lines: list[PlanLine]


class DispatchLineOut(BaseModel):
    dispatch_line_id: UUID
    so_line_id: UUID
    line_no: int
    item_code: str
    customer_part_no: str | None
    lot_id: UUID
    lot_no: str
    quantity: Decimal


class DispatchSummary(BaseModel):
    dispatch_id: UUID
    dispatch_no: str
    unit_id: UUID
    so_id: UUID
    so_no: str
    customer_code: str
    status: str
    dispatched_at: datetime | None
    vehicle_no: str | None
    created_at: datetime


class DispatchOut(DispatchSummary):
    transporter: str | None
    notes: str | None
    store_id: UUID | None
    lines: list[DispatchLineOut]


DISPATCH_SQL = (
    "SELECT d.dispatch_id, d.dispatch_no, d.unit_id, d.so_id, s.so_no, c.code AS customer_code, "
    "d.status, d.dispatched_at, d.vehicle_no, d.created_at, d.transporter, d.notes, d.store_id "
    "FROM erp.dispatch d JOIN erp.sales_order s ON s.so_id = d.so_id JOIN erp.customer c ON "
    "c.customer_id = s.customer_id"
)


async def load(ctx: Ctx, dispatch_id: UUID) -> DispatchOut | None:
    clause, params = scope_clause(ctx, "d.unit_id")
    head = await one(
        ctx, f"{DISPATCH_SQL} WHERE d.dispatch_id = :id{' AND ' + clause if clause else ''}",
        {**params, "id": dispatch_id})
    if head is None:
        return None
    lines = await rows(
        ctx, "SELECT dl.dispatch_line_id, dl.so_line_id, l.line_no, i.code AS item_code, "
        "x.customer_part_no, dl.lot_id, t.lot_no, dl.quantity FROM erp.dispatch_line dl JOIN "
        "erp.so_line l ON l.so_line_id = dl.so_line_id JOIN erp.item i ON i.item_id = l.item_id "
        "JOIN erp.lot t ON t.lot_id = dl.lot_id JOIN erp.sales_order s ON s.so_id = l.so_id "
        "LEFT JOIN erp.customer_part_xref x ON x.customer_id = s.customer_id AND x.item_id = "
        "l.item_id WHERE dl.dispatch_id = :id ORDER BY l.line_no, t.received_at, t.lot_no",
        {"id": dispatch_id})
    return DispatchOut(**head, lines=[DispatchLineOut(**ln) for ln in lines])


async def pick(ctx: Ctx, body: PlanIn, lock: bool) -> tuple[dict[str, Any], dict[str, Any],
                                                             list[dict[str, Any]]]:
    """Check the order and store, and choose lots FIFO for every line (nothing is changed)."""
    order = await one(
        ctx, "SELECT so_id, so_no, unit_id, status FROM erp.sales_order WHERE so_id = :id"
        f"{' FOR UPDATE' if lock else ''}", {"id": body.so_id})
    if order is None or not ctx.principal.may_see_unit(order["unit_id"]):
        raise not_found("Sales order")
    if order["status"] not in ("OPEN", "PARTIALLY_SHIPPED"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The order is {str(order['status']).lower().replace('_', ' ')} and cannot be "
            "dispatched")
    store = await store_in_scope(ctx, body.store_id)
    if store["unit_id"] != order["unit_id"] or not store["active"]:
        raise HTTPException(422, "The store must be an active store of the order's plant")
    ids = [ln.so_line_id for ln in body.lines]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Each order line may appear only once")
    planned: list[dict[str, Any]] = []
    for line in sorted(body.lines, key=lambda ln: str(ln.so_line_id)):  # fixed lock order
        found = await one(
            ctx, "SELECT l.so_line_id, l.item_id, i.code AS item_code, l.ordered_qty, "
            "l.shipped_qty, (SELECT COALESCE(sum(dl.quantity), 0) FROM erp.dispatch_line dl "
            "JOIN erp.dispatch d ON d.dispatch_id = dl.dispatch_id WHERE dl.so_line_id = "
            "l.so_line_id AND d.status = 'DRAFT') AS drafted FROM erp.so_line l JOIN erp.item i "
            "ON i.item_id = l.item_id WHERE l.so_line_id = :l AND l.so_id = :s",
            {"l": line.so_line_id, "s": body.so_id})
        if found is None:
            raise HTTPException(422, "A line does not belong to this order")
        open_qty = found["ordered_qty"] - found["shipped_qty"] - found["drafted"]
        if line.quantity > open_qty:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{found['item_code']}: {line.quantity} asked but only {open_qty} is still open "
                "on the order")
        lots = await candidates(ctx, body.store_id, found["item_id"], lock=lock)
        picks, shortfall, _blocked = choose(lots, line.quantity, PICK_RULE)
        planned.append({**found, "requested": line.quantity, "open_qty": open_qty,
                        "picks": picks, "shortfall": shortfall})
    return order, store, planned


@router.post("/dispatches/plan", response_model=PlanOut)
async def plan_dispatch(body: PlanIn, ctx: Ctx = Depends(manage)) -> PlanOut:
    """Show which lots would be picked and what is short, without changing anything."""
    _order, _store, planned = await pick(ctx, body, lock=False)
    lines = [PlanLine(
        so_line_id=p["so_line_id"], item_code=p["item_code"], requested=p["requested"],
        open_qty=p["open_qty"], shortfall=p["shortfall"],
        picks=[PickOut(lot_id=k["lot_id"], lot_no=k["lot_no"], received_at=k["received_at"],
                       quantity=k["take"]) for k in p["picks"]]) for p in planned]
    return PlanOut(can_dispatch=all(ln.shortfall == 0 for ln in lines), lines=lines)


@router.post("/dispatches", response_model=DispatchOut, status_code=status.HTTP_201_CREATED)
async def create_dispatch(body: DispatchIn, ctx: Ctx = Depends(manage)) -> DispatchOut:
    """Pick the stock for real: reserve the lots for this dispatch, or refuse if any is short."""
    order, store, planned = await pick(ctx, body, lock=True)
    short = [p for p in planned if p["shortfall"] > 0]
    if short:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Not enough finished-goods stock: " + "; ".join(
                f"{p['item_code']} needs {p['requested']}, {p['requested'] - p['shortfall']} "
                "available" for p in short))
    number = await next_number(ctx, "dispatch")
    created = await one(
        ctx, "INSERT INTO erp.dispatch(tenant_id, unit_id, so_id, dispatch_no, created_by, "
        "vehicle_no, transporter, notes, store_id) VALUES (erp.current_tenant_id(), :u, :so, :n, "
        ":me, :v, :t, :notes, :s) RETURNING dispatch_id",
        {"u": order["unit_id"], "so": body.so_id, "n": number,
         "me": ctx.principal.profile.app_user_id, "v": body.vehicle_no, "t": body.transporter,
         "notes": body.notes, "s": body.store_id})
    assert created is not None
    for p in planned:
        for k in p["picks"]:
            await ledger.reserve(ctx, k["lot_id"], body.store_id, k["take"])
            await rows(
                ctx, "INSERT INTO erp.dispatch_line(tenant_id, dispatch_id, so_line_id, lot_id, "
                "quantity) VALUES (erp.current_tenant_id(), :d, :l, :lot, :q) "
                "RETURNING dispatch_line_id",
                {"d": created["dispatch_id"], "l": p["so_line_id"], "lot": k["lot_id"],
                 "q": k["take"]})
            await rows(
                ctx, "INSERT INTO erp.stock_reservation(tenant_id, unit_id, store_id, item_id, "
                "lot_id, quantity, reference_type, reference, created_by) VALUES "
                "(erp.current_tenant_id(), :u, :s, :i, :lot, :q, 'DISPATCH', :r, :me) "
                "RETURNING reservation_id",
                {"u": order["unit_id"], "s": body.store_id, "i": p["item_id"], "lot": k["lot_id"],
                 "q": k["take"], "r": number, "me": ctx.principal.profile.app_user_id})
    await audit(ctx, "dispatch.create", "dispatch", created["dispatch_id"],
                {"dispatch_no": number, "so_no": order["so_no"], "lines": len(planned)})
    result = await load(ctx, created["dispatch_id"])
    assert result is not None
    return result


async def require_dispatch(ctx: Ctx, dispatch_id: UUID, *allowed: str) -> dict[str, Any]:
    clause, params = scope_clause(ctx, "unit_id")
    head = await one(
        ctx, "SELECT dispatch_id, dispatch_no, unit_id, so_id, store_id, status FROM "
        f"erp.dispatch WHERE dispatch_id = :id{' AND ' + clause if clause else ''} FOR UPDATE",
        {**params, "id": dispatch_id})
    if head is None:
        raise not_found("Dispatch")
    if head["status"] not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The dispatch is {str(head['status']).lower()}; this needs it to be "
            f"{' or '.join(a.lower() for a in allowed)}")
    return head


@router.post("/dispatches/{dispatch_id}/dispatch", response_model=DispatchOut)
async def confirm_dispatch(dispatch_id: UUID, ctx: Ctx = Depends(manage)) -> DispatchOut:
    """The goods leave: issue the stock, update the order and fulfil the schedule."""
    head = await require_dispatch(ctx, dispatch_id, "DRAFT")
    await orders.require_order(ctx, head["so_id"], "OPEN", "PARTIALLY_SHIPPED")
    lines = await rows(
        ctx, "SELECT dl.dispatch_line_id, dl.so_line_id, dl.lot_id, dl.quantity, l.item_id FROM "
        "erp.dispatch_line dl JOIN erp.so_line l ON l.so_line_id = dl.so_line_id WHERE "
        "dl.dispatch_id = :id ORDER BY dl.lot_id", {"id": dispatch_id})
    shipped: dict[UUID, Decimal] = {}
    for line in lines:
        ledger_id = await ledger.move(
            ctx, unit_id=head["unit_id"], item_id=line["item_id"], lot_id=line["lot_id"],
            store_id=head["store_id"], delta=-line["quantity"], movement_type="ISSUE",
            reference_type="DISPATCH", reference_id=dispatch_id,
            idempotency_key=f"dispatch:{dispatch_id}:{line['dispatch_line_id']}",
            consumes_reservation=line["quantity"])
        await ledger.take_layers(ctx, unit_id=head["unit_id"], item_id=line["item_id"],
                                 quantity=line["quantity"], issue_ledger_id=ledger_id)
        await rows(
            ctx, "UPDATE erp.stock_reservation SET status = 'CONSUMED' WHERE reference_type = "
            "'DISPATCH' AND reference = :r AND lot_id = :lot AND status = 'ACTIVE' "
            "RETURNING reservation_id", {"r": head["dispatch_no"], "lot": line["lot_id"]})
        shipped[line["so_line_id"]] = shipped.get(line["so_line_id"], Decimal(0)) + line[
            "quantity"]
    for so_line_id, quantity in shipped.items():
        await rows(ctx, "UPDATE erp.so_line SET shipped_qty = shipped_qty + :q WHERE so_line_id = "
                   ":l RETURNING so_line_id", {"q": quantity, "l": so_line_id})
        left = quantity
        for sched in await rows(
                ctx, "SELECT so_schedule_id, scheduled_qty - fulfilled_qty AS open FROM "
                "erp.so_schedule WHERE so_line_id = :l AND fulfilled_qty < scheduled_qty ORDER BY "
                "due_date FOR UPDATE", {"l": so_line_id}):
            if left <= 0:
                break
            take = min(left, sched["open"])
            await rows(ctx, "UPDATE erp.so_schedule SET fulfilled_qty = fulfilled_qty + :q WHERE "
                       "so_schedule_id = :id RETURNING so_schedule_id",
                       {"q": take, "id": sched["so_schedule_id"]})
            left -= take
    await rows(ctx, "UPDATE erp.dispatch SET status = 'DISPATCHED', dispatched_at = now(), "
               "confirmed_by = :me WHERE dispatch_id = :id RETURNING dispatch_id",
               {"me": ctx.principal.profile.app_user_id, "id": dispatch_id})
    await orders.refresh_status(ctx, head["so_id"])
    await audit(ctx, "dispatch.confirm", "dispatch", dispatch_id,
                {"dispatch_no": head["dispatch_no"], "lines": len(lines)})
    result = await load(ctx, dispatch_id)
    assert result is not None
    return result


@router.post("/dispatches/{dispatch_id}/cancel", response_model=DispatchOut)
async def cancel_dispatch(dispatch_id: UUID, ctx: Ctx = Depends(manage)) -> DispatchOut:
    """Give the picked stock back: release the reservations of a dispatch that never left."""
    head = await require_dispatch(ctx, dispatch_id, "DRAFT")
    for line in await rows(ctx, "SELECT lot_id, quantity FROM erp.dispatch_line WHERE "
                           "dispatch_id = :id ORDER BY lot_id", {"id": dispatch_id}):
        await ledger.unreserve(ctx, line["lot_id"], head["store_id"], line["quantity"])
    await rows(ctx, "UPDATE erp.stock_reservation SET status = 'RELEASED' WHERE reference_type = "
               "'DISPATCH' AND reference = :r AND status = 'ACTIVE' RETURNING reservation_id",
               {"r": head["dispatch_no"]})
    await rows(ctx, "UPDATE erp.dispatch SET status = 'CANCELLED' WHERE dispatch_id = :id "
               "RETURNING dispatch_id", {"id": dispatch_id})
    await audit(ctx, "dispatch.cancel", "dispatch", dispatch_id,
                {"dispatch_no": head["dispatch_no"]})
    result = await load(ctx, dispatch_id)
    assert result is not None
    return result


@router.get("/dispatches", response_model=Page[DispatchSummary])
async def list_dispatches(
    so_id: UUID | None = None, dispatch_status: str | None = Query(None, alias="status"),
    page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[DispatchSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "d.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for text_, key, value in (("d.so_id = :so", "so", so_id),
                              ("d.status = :st", "st",
                               dispatch_status.upper() if dispatch_status else None)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{DISPATCH_SQL}{where} ORDER BY d.dispatch_no DESC LIMIT :limit "
                       "OFFSET :offset", {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.dispatch d{where}", params)
    return Page(items=[DispatchSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/dispatches/{dispatch_id}", response_model=DispatchOut)
async def get_dispatch(dispatch_id: UUID, ctx: Ctx = Depends(read)) -> DispatchOut:
    found = await load(ctx, dispatch_id)
    if found is None:
        raise not_found("Dispatch")
    return found


class PackingLine(BaseModel):
    line_no: int
    item_code: str
    item_name: str
    customer_part_no: str | None
    lot_no: str
    label_code: str | None
    expiry_at: datetime | None
    quantity: Decimal


class PackingList(BaseModel):
    dispatch_no: str
    status: str
    so_no: str
    customer_po_no: str | None
    customer_code: str
    customer_name: str
    dispatched_at: datetime | None
    vehicle_no: str | None
    transporter: str | None
    printed_on: date
    lines: list[PackingLine]
    total_quantity: Decimal


@router.get("/dispatches/{dispatch_id}/packing-list", response_model=PackingList)
async def packing_list(dispatch_id: UUID, ctx: Ctx = Depends(read)) -> PackingList:
    """The sheet that travels with the goods: customer part numbers, lots and label codes."""
    head = await load(ctx, dispatch_id)
    if head is None:
        raise not_found("Dispatch")
    info = await one(
        ctx, "SELECT s.customer_po_no, c.name AS customer_name FROM erp.sales_order s JOIN "
        "erp.customer c ON c.customer_id = s.customer_id WHERE s.so_id = :id", {"id": head.so_id})
    assert info is not None
    detail = await rows(
        ctx, "SELECT l.line_no, i.code AS item_code, i.name AS item_name, x.customer_part_no, "
        "t.lot_no, p.label_code, t.expiry_at, dl.quantity FROM erp.dispatch_line dl JOIN "
        "erp.so_line l ON l.so_line_id = dl.so_line_id JOIN erp.item i ON i.item_id = l.item_id "
        "JOIN erp.lot t ON t.lot_id = dl.lot_id JOIN erp.sales_order s ON s.so_id = l.so_id "
        "LEFT JOIN erp.customer_part_xref x ON x.customer_id = s.customer_id AND x.item_id = "
        "l.item_id LEFT JOIN erp.packing p ON p.fg_lot_id = dl.lot_id WHERE dl.dispatch_id = :id "
        "ORDER BY l.line_no, t.lot_no", {"id": dispatch_id})
    head_row = await one(ctx, "SELECT vehicle_no, transporter FROM erp.dispatch WHERE "
                         "dispatch_id = :id", {"id": dispatch_id})
    assert head_row is not None
    return PackingList(
        dispatch_no=head.dispatch_no, status=head.status, so_no=head.so_no,
        customer_po_no=info["customer_po_no"], customer_code=head.customer_code,
        customer_name=info["customer_name"], dispatched_at=head.dispatched_at,
        vehicle_no=head_row["vehicle_no"], transporter=head_row["transporter"],
        printed_on=date.today(), lines=[PackingLine(**d) for d in detail],
        total_quantity=sum((d["quantity"] for d in detail), Decimal(0)))


@router.get("/dispatches/{dispatch_id}/trace", response_model=dict[str, Any])
async def trace_dispatch(dispatch_id: UUID, ctx: Ctx = Depends(read)) -> dict[str, Any]:
    """Order to dispatch to finished lot to production lot, compound batches and raw material."""
    head = await load(ctx, dispatch_id)
    if head is None:
        raise not_found("Dispatch")
    lots = []
    for line in head.lines:
        stock = await one(ctx, "SELECT item_id, unit_id, lot_no FROM erp.lot WHERE lot_id = :id",
                          {"id": line.lot_id})
        assert stock is not None
        made = await one(
            ctx, "SELECT prod_lot_id FROM erp.prod_lot WHERE lot_no = :n AND item_id = :i AND "
            "unit_id = :u", {"n": stock["lot_no"], "i": stock["item_id"],
                             "u": stock["unit_id"]})
        lots.append({
            "line_no": line.line_no, "item_code": line.item_code, "lot_no": line.lot_no,
            "quantity": line.quantity,
            "production": await upstream(ctx, made["prod_lot_id"]) if made else None})
    return {"dispatch_no": head.dispatch_no, "so_no": head.so_no, "status": head.status,
            "customer_code": head.customer_code, "lots": lots}
