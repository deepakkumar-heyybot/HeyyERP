"""Reading stock: balances by lot, valuation, expiry, reorder, and the reconciliation check.

Stock on hand is per lot and store. Available = on hand minus reserved, and only lots in status
AVAILABLE can be issued. Reads need the inventory module and respect the plant scope.
The reconciliation report proves the three records agree: the ledger (every movement), the
balances, and the FIFO cost layers.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

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

router = APIRouter(prefix="/inventory", tags=["inventory"])
read = requires_module("inventory")
quality = requires("qa.approve")
counting = requires("inv.count")


class StockRow(BaseModel):
    lot_id: UUID
    lot_no: str
    lot_status: str
    item_id: UUID
    item_code: str
    item_name: str
    uom_code: str
    store_id: UUID
    store_code: str
    unit_id: UUID
    unit_code: str
    received_at: datetime
    expiry_at: datetime | None
    expired: bool
    on_hand: Decimal
    reserved: Decimal
    available: Decimal


STOCK_SQL = (
    "SELECT b.lot_id, t.lot_no, t.status AS lot_status, b.item_id, i.code AS item_code, "
    "i.name AS item_name, um.code AS uom_code, b.store_id, s.code AS store_code, b.unit_id, "
    "u.code AS unit_code, t.received_at, t.expiry_at, "
    "(t.expiry_at IS NOT NULL AND t.expiry_at <= now()) AS expired, "
    "b.quantity_on_hand AS on_hand, b.quantity_reserved AS reserved, "
    "(b.quantity_on_hand - b.quantity_reserved) AS available "
    "FROM erp.stock_balance b JOIN erp.lot t ON t.lot_id = b.lot_id JOIN erp.item i ON "
    "i.item_id = b.item_id JOIN erp.uom um ON um.uom_id = i.uom_id JOIN erp.store s ON "
    "s.store_id = b.store_id JOIN erp.unit u ON u.unit_id = b.unit_id"
)


@router.get("/stock", response_model=Page[StockRow])
async def stock(
    unit_id: UUID | None = None,
    store_id: UUID | None = None,
    item_id: UUID | None = None,
    lot_status: str | None = None,
    in_stock_only: bool = True,
    page: Paging = Depends(),
    ctx: Ctx = Depends(read),
) -> Page[StockRow]:
    """Stock by lot and store. By default only lots with something on hand."""
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "b.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for column, name, value in (("b.unit_id", "unit", unit_id), ("b.store_id", "store", store_id),
                                ("b.item_id", "item", item_id),
                                ("t.status", "status", lot_status and lot_status.upper())):
        if value is not None:
            conditions.append(f"{column} = :{name}")
            params[name] = value
    if in_stock_only:
        conditions.append("b.quantity_on_hand > 0")
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(
        ctx, f"{STOCK_SQL}{where} ORDER BY i.code, t.expiry_at NULLS LAST, t.received_at, "
        "t.lot_no LIMIT :limit OFFSET :offset",
        {**params, "limit": page.limit, "offset": page.offset})
    total = await count(
        ctx, "SELECT count(*) FROM erp.stock_balance b JOIN erp.lot t ON t.lot_id = b.lot_id"
        f"{where}", params)
    return Page(items=[StockRow(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/lots/{lot_id}", response_model=StockRow)
async def get_lot(lot_id: UUID, ctx: Ctx = Depends(read)) -> StockRow:
    clause, params = scope_clause(ctx, "b.unit_id")
    row = await one(ctx, f"{STOCK_SQL} WHERE b.lot_id = :id{' AND ' + clause if clause else ''}",
                    {**params, "id": lot_id})
    if row is None:
        raise not_found("Lot")
    return StockRow(**row)


class QuarantineIn(Strict):
    reason: str = Field(min_length=3, max_length=300)


@router.post("/lots/{lot_id}/quarantine", response_model=StockRow)
async def quarantine(lot_id: UUID, body: QuarantineIn, ctx: Ctx = Depends(quality)) -> StockRow:
    """Hold an available lot (suspected quality problem); it cannot be issued until released."""
    await _change_status(ctx, lot_id, "AVAILABLE", "QUARANTINE", "lot.quarantine", body.reason)
    return await get_lot(lot_id, ctx)


@router.post("/lots/{lot_id}/unquarantine", response_model=StockRow)
async def unquarantine(lot_id: UUID, body: QuarantineIn, ctx: Ctx = Depends(quality)) -> StockRow:
    await _change_status(ctx, lot_id, "QUARANTINE", "AVAILABLE", "lot.unquarantine", body.reason)
    return await get_lot(lot_id, ctx)


async def _change_status(
    ctx: Ctx, lot_id: UUID, expected: str, new: str, action: str, reason: str
) -> None:
    lot = await one(ctx, "SELECT unit_id, status FROM erp.lot WHERE lot_id = :id FOR UPDATE",
                    {"id": lot_id})
    if lot is None or not ctx.principal.may_see_unit(lot["unit_id"]):
        raise not_found("Lot")
    if lot["status"] != expected:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The lot is {lot['status']}, not {expected}")
    await rows(ctx, "UPDATE erp.lot SET status = :s WHERE lot_id = :id RETURNING lot_id",
               {"s": new, "id": lot_id})
    await audit(ctx, action, "lot", lot_id, {"reason": reason})


# ------------------------------------------------------------------ valuation, expiry, reorder
class ValuationRow(BaseModel):
    unit_id: UUID
    unit_code: str
    item_id: UUID
    item_code: str
    quantity: Decimal
    value: Decimal
    average_cost: Decimal | None


@router.get("/valuation", response_model=list[ValuationRow])
async def valuation(unit_id: UUID | None = None, ctx: Ctx = Depends(read)) -> list[ValuationRow]:
    """FIFO value of the stock: the cost layers that are not used up yet, per plant and item."""
    conditions = ["cl.remaining_qty > 0"]
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "r.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    if unit_id is not None:
        conditions.append("r.unit_id = :unit")
        params["unit"] = unit_id
    found = await rows(
        ctx,
        "SELECT r.unit_id, u.code AS unit_code, cl.item_id, i.code AS item_code, "
        "sum(cl.remaining_qty) AS quantity, round(sum(cl.remaining_qty * cl.unit_cost), 2) AS "
        "value FROM erp.cost_layer cl JOIN erp.stock_ledger r ON r.ledger_id = "
        "cl.receipt_ledger_id JOIN erp.item i ON i.item_id = cl.item_id JOIN erp.unit u ON "
        f"u.unit_id = r.unit_id WHERE {' AND '.join(conditions)} GROUP BY r.unit_id, u.code, "
        "cl.item_id, i.code ORDER BY u.code, i.code", params)
    return [ValuationRow(**f, average_cost=(f["value"] / f["quantity"]).quantize(Decimal("0.0001"))
                         if f["quantity"] else None) for f in found]


class ExpiringRow(BaseModel):
    lot_id: UUID
    lot_no: str
    item_code: str
    store_code: str
    expiry_at: datetime
    on_hand: Decimal
    days_left: int


@router.get("/expiring", response_model=list[ExpiringRow])
async def expiring(
    within_days: int = Query(30, ge=0, le=3650),
    include_expired: bool = True,
    ctx: Ctx = Depends(read),
) -> list[ExpiringRow]:
    """Lots that expire soon, or already have (with stock still on hand). Soonest first."""
    clause, params = scope_clause(ctx, "b.unit_id")
    found = await rows(
        ctx,
        "SELECT b.lot_id, t.lot_no, i.code AS item_code, s.code AS store_code, t.expiry_at, "
        "b.quantity_on_hand AS on_hand, (t.expiry_at::date - current_date) AS days_left "
        "FROM erp.stock_balance b JOIN erp.lot t ON t.lot_id = b.lot_id JOIN erp.item i ON "
        "i.item_id = b.item_id JOIN erp.store s ON s.store_id = b.store_id WHERE "
        "b.quantity_on_hand > 0 AND t.expiry_at IS NOT NULL AND t.expiry_at <= now() + "
        "make_interval(days => :d)" + ("" if include_expired else " AND t.expiry_at > now()")
        + (f" AND {clause}" if clause else "") + " ORDER BY t.expiry_at, t.lot_no",
        {**params, "d": within_days})
    return [ExpiringRow(**f) for f in found]


class ReorderRow(BaseModel):
    unit_id: UUID
    unit_code: str
    item_id: UUID
    item_code: str
    item_name: str
    available: Decimal
    reorder_point: Decimal
    suggested_qty: Decimal


@router.get("/reorder", response_model=list[ReorderRow])
async def reorder(unit_id: UUID | None = None, ctx: Ctx = Depends(read)) -> list[ReorderRow]:
    """Items whose free stock at a plant is below the reorder point (FR-INV-08).

    The suggestion is the reorder quantity, or the shortfall if that is larger.
    """
    conditions = ["p.reorder_point > 0"]
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "p.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    if unit_id is not None:
        conditions.append("p.unit_id = :unit")
        params["unit"] = unit_id
    found = await rows(
        ctx,
        "SELECT * FROM (SELECT p.unit_id, u.code AS unit_code, p.item_id, i.code AS item_code, "
        "i.name AS item_name, p.reorder_point, p.reorder_qty, COALESCE((SELECT "
        "sum(b.quantity_on_hand - b.quantity_reserved) FROM erp.stock_balance b JOIN erp.lot t "
        "ON t.lot_id = b.lot_id WHERE b.unit_id = p.unit_id AND b.item_id = p.item_id AND "
        "t.status = 'AVAILABLE' AND (t.expiry_at IS NULL OR t.expiry_at > now())), 0) AS "
        "available FROM erp.item_unit_policy p JOIN erp.item i ON i.item_id = p.item_id AND "
        f"i.active JOIN erp.unit u ON u.unit_id = p.unit_id WHERE {' AND '.join(conditions)}) x "
        "WHERE x.available < x.reorder_point ORDER BY x.unit_code, x.item_code", params)
    return [ReorderRow(
        **{k: f[k] for k in ("unit_id", "unit_code", "item_id", "item_code", "item_name",
                              "available", "reorder_point")},
        suggested_qty=max(f["reorder_qty"], f["reorder_point"] - f["available"])) for f in found]


# ------------------------------------------------------------------ reconciliation
class Problem(BaseModel):
    kind: str
    detail: str


class Reconciliation(BaseModel):
    ok: bool
    checked_at: datetime
    lots_checked: int
    problems: list[Problem]


@router.get("/reconciliation", response_model=Reconciliation)
async def reconciliation(ctx: Ctx = Depends(counting)) -> Reconciliation:
    """Do the ledger, the balances, the reservations and the FIFO layers agree?"""
    problems: list[Problem] = []
    clause, params = scope_clause(ctx, "b.unit_id")
    scope = f" AND {clause}" if clause else ""
    for r in await rows(
        ctx, "SELECT b.lot_id, t.lot_no, b.quantity_on_hand, COALESCE((SELECT sum(l.qty_delta) "
        "FROM erp.stock_ledger l WHERE l.lot_id = b.lot_id AND l.store_id = b.store_id), 0) AS "
        "ledger_sum FROM erp.stock_balance b JOIN erp.lot t ON t.lot_id = b.lot_id "
        f"WHERE true{scope}", params,
    ):
        if r["quantity_on_hand"] != r["ledger_sum"]:
            problems.append(Problem(kind="LEDGER_VS_BALANCE", detail=(
                f"Lot {r['lot_no']}: balance {r['quantity_on_hand']} but ledger adds up to "
                f"{r['ledger_sum']}")))
    for r in await rows(
        ctx, "SELECT b.lot_id, t.lot_no, b.quantity_reserved, COALESCE((SELECT "
        "sum(x.quantity) FROM erp.stock_reservation x WHERE x.lot_id = b.lot_id AND x.status = "
        f"'ACTIVE'), 0) AS held FROM erp.stock_balance b JOIN erp.lot t ON t.lot_id = b.lot_id "
        f"WHERE true{scope}", params,
    ):
        if r["quantity_reserved"] != r["held"]:
            problems.append(Problem(kind="RESERVED_VS_RESERVATIONS", detail=(
                f"Lot {r['lot_no']}: {r['quantity_reserved']} reserved but active reservations "
                f"add up to {r['held']}")))
    unit_scope, unit_params = scope_clause(ctx, "q.unit_id")
    for r in await rows(
        ctx,
        "SELECT q.unit_id, q.item_id, i.code, q.on_hand, COALESCE(l.layer_qty, 0) AS layer_qty "
        "FROM (SELECT unit_id, item_id, sum(quantity_on_hand) AS on_hand FROM erp.stock_balance "
        "GROUP BY unit_id, item_id) q JOIN erp.item i ON i.item_id = q.item_id LEFT JOIN "
        "(SELECT r.unit_id, cl.item_id, sum(cl.remaining_qty) AS layer_qty FROM erp.cost_layer cl "
        "JOIN erp.stock_ledger r ON r.ledger_id = cl.receipt_ledger_id GROUP BY r.unit_id, "
        f"cl.item_id) l ON l.unit_id = q.unit_id AND l.item_id = q.item_id WHERE true"
        f"{' AND ' + unit_scope if unit_scope else ''}", unit_params,
    ):
        if r["on_hand"] != r["layer_qty"]:
            problems.append(Problem(kind="BALANCE_VS_COST_LAYERS", detail=(
                f"Item {r['code']}: {r['on_hand']} on hand but FIFO layers hold {r['layer_qty']}")))
    lots = await count(ctx, f"SELECT count(*) FROM erp.stock_balance b WHERE true{scope}", params)
    return Reconciliation(ok=not problems, checked_at=datetime.now(), lots_checked=lots,
                          problems=problems)

