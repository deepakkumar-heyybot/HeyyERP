"""The stock ledger and FIFO cost layers: the only code that changes stock.

Every change of quantity goes through move(): it locks the lot's balance row, refuses to take
more than is available (nothing may go below zero, BRD RULE-07; PostgreSQL also enforces it),
appends an immutable ledger row and updates the balance. Callers that touch several lots lock
them in lot_id order so two requests cannot deadlock. Each ledger row carries an idempotency key,
so a retried request posts nothing twice.

Valuation is FIFO (RULE-06) per plant and item: each receipt opens a cost layer and every issue
consumes the oldest layers first. Layer quantity left always equals the quantity on hand.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status

from heyyerp_api.masters.common import Ctx, one, rows

MOVEMENTS = (
    "OPENING", "RECEIPT", "ISSUE", "TRANSFER_OUT", "TRANSFER_IN", "COUNT_ADJUST", "QA_REJECT",
)
PAISA = Decimal("0.01")


async def move(
    ctx: Ctx,
    *,
    unit_id: UUID,
    item_id: UUID,
    lot_id: UUID,
    store_id: UUID,
    delta: Decimal,
    movement_type: str,
    reference_type: str,
    reference_id: UUID,
    idempotency_key: str,
    consumes_reservation: Decimal = Decimal(0),
) -> UUID:
    """Post one stock movement. Returns the ledger id (the existing one if the key was used)."""
    if delta == 0:
        raise HTTPException(422, "A stock movement cannot be zero")
    existing = await one(
        ctx, "SELECT ledger_id FROM erp.stock_ledger WHERE idempotency_key = :k",
        {"k": idempotency_key})
    if existing is not None:
        return existing["ledger_id"]  # type: ignore[no-any-return]
    await rows(
        ctx,
        "INSERT INTO erp.stock_balance(tenant_id, unit_id, item_id, lot_id, store_id) VALUES "
        "(erp.current_tenant_id(), :u, :i, :l, :s) ON CONFLICT (lot_id, store_id) DO NOTHING "
        "RETURNING lot_id", {"u": unit_id, "i": item_id, "l": lot_id, "s": store_id})
    balance = await one(
        ctx, "SELECT quantity_on_hand, quantity_reserved FROM erp.stock_balance "
        "WHERE lot_id = :l AND store_id = :s FOR UPDATE", {"l": lot_id, "s": store_id})
    assert balance is not None
    on_hand = balance["quantity_on_hand"] + delta
    reserved = balance["quantity_reserved"] - consumes_reservation
    if on_hand < 0 or reserved < 0 or on_hand < reserved:
        available = balance["quantity_on_hand"] - balance["quantity_reserved"]
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Not enough stock: {available} available in this lot, {abs(delta)} needed")
    ledger = await one(
        ctx,
        "INSERT INTO erp.stock_ledger(tenant_id, unit_id, item_id, lot_id, store_id, qty_delta, "
        "movement_type, reference_type, reference_id, idempotency_key, created_by, occurred_at) "
        "VALUES (erp.current_tenant_id(), :u, :i, :l, :s, :d, :m, :rt, :rid, :k, :by, "
        "clock_timestamp()) RETURNING ledger_id",  # distinct per row, so FIFO order is exact
        {"u": unit_id, "i": item_id, "l": lot_id, "s": store_id, "d": delta, "m": movement_type,
         "rt": reference_type, "rid": reference_id, "k": idempotency_key,
         "by": ctx.principal.profile.app_user_id})
    assert ledger is not None
    await rows(
        ctx, "UPDATE erp.stock_balance SET quantity_on_hand = :oh, quantity_reserved = :rs, "
        "version = version + 1, updated_at = now() WHERE lot_id = :l AND store_id = :s "
        "RETURNING lot_id", {"oh": on_hand, "rs": reserved, "l": lot_id, "s": store_id})
    return ledger["ledger_id"]  # type: ignore[no-any-return]


async def reserve(ctx: Ctx, lot_id: UUID, store_id: UUID, quantity: Decimal) -> None:
    """Hold quantity on a lot so nobody else can issue it. The balance row is locked first."""
    balance = await one(
        ctx, "SELECT quantity_on_hand, quantity_reserved FROM erp.stock_balance "
        "WHERE lot_id = :l AND store_id = :s FOR UPDATE", {"l": lot_id, "s": store_id})
    if balance is None or balance["quantity_on_hand"] - balance["quantity_reserved"] < quantity:
        raise HTTPException(status.HTTP_409_CONFLICT, "Not enough free stock to reserve")
    await rows(
        ctx, "UPDATE erp.stock_balance SET quantity_reserved = quantity_reserved + :q, "
        "version = version + 1, updated_at = now() WHERE lot_id = :l AND store_id = :s "
        "RETURNING lot_id", {"q": quantity, "l": lot_id, "s": store_id})


async def unreserve(ctx: Ctx, lot_id: UUID, store_id: UUID, quantity: Decimal) -> None:
    await rows(
        ctx, "UPDATE erp.stock_balance SET quantity_reserved = quantity_reserved - :q, "
        "version = version + 1, updated_at = now() WHERE lot_id = :l AND store_id = :s "
        "RETURNING lot_id", {"q": quantity, "l": lot_id, "s": store_id})


# ------------------------------------------------------------------ FIFO cost layers
def money(value: Decimal) -> Decimal:
    return value.quantize(PAISA, rounding=ROUND_HALF_UP)


async def add_layer(
    ctx: Ctx, *, ledger_id: UUID, item_id: UUID, lot_id: UUID, quantity: Decimal,
    unit_cost: Decimal,
) -> None:
    await rows(
        ctx,
        "INSERT INTO erp.cost_layer(tenant_id, item_id, lot_id, receipt_ledger_id, received_qty, "
        "remaining_qty, unit_cost) VALUES (erp.current_tenant_id(), :i, :l, :r, :q, :q, :c) "
        "RETURNING cost_layer_id",
        {"i": item_id, "l": lot_id, "r": ledger_id, "q": quantity, "c": unit_cost})


async def take_layers(
    ctx: Ctx, *, unit_id: UUID, item_id: UUID, quantity: Decimal, issue_ledger_id: UUID
) -> Decimal:
    """Consume the oldest layers of the plant's item; returns the cost of what was taken."""
    layers = await rows(
        ctx,
        "SELECT cl.cost_layer_id, cl.remaining_qty, cl.unit_cost FROM erp.cost_layer cl "
        "JOIN erp.stock_ledger r ON r.ledger_id = cl.receipt_ledger_id WHERE r.unit_id = :u "
        "AND cl.item_id = :i AND cl.remaining_qty > 0 ORDER BY r.occurred_at, r.ledger_id "
        "FOR UPDATE OF cl", {"u": unit_id, "i": item_id})
    left, total = quantity, Decimal(0)
    for layer in layers:
        take = min(left, layer["remaining_qty"])
        cost = money(take * layer["unit_cost"])
        await rows(
            ctx, "UPDATE erp.cost_layer SET remaining_qty = remaining_qty - :q "
            "WHERE cost_layer_id = :id RETURNING cost_layer_id",
            {"q": take, "id": layer["cost_layer_id"]})
        await rows(
            ctx,
            "INSERT INTO erp.cost_layer_consumption(tenant_id, cost_layer_id, issue_ledger_id, "
            "quantity, extended_cost) VALUES (erp.current_tenant_id(), :c, :l, :q, :x) "
            "RETURNING consumption_id",
            {"c": layer["cost_layer_id"], "l": issue_ledger_id, "q": take, "x": cost})
        total += cost
        left -= take
        if left <= 0:
            return total
    raise HTTPException(
        status.HTTP_409_CONFLICT, "The valuation layers hold less than the stock; run the "
        "reconciliation report")


async def last_cost(ctx: Ctx, unit_id: UUID, item_id: UUID) -> Decimal | None:
    """Unit cost of the plant's most recent receipt of the item, if there ever was one."""
    row: dict[str, Any] | None = await one(
        ctx, "SELECT cl.unit_cost FROM erp.cost_layer cl JOIN erp.stock_ledger r ON "
        "r.ledger_id = cl.receipt_ledger_id WHERE r.unit_id = :u AND cl.item_id = :i "
        "ORDER BY r.occurred_at DESC, r.ledger_id DESC LIMIT 1", {"u": unit_id, "i": item_id})
    return row["unit_cost"] if row else None
