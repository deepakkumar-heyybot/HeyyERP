"""The handoff from purchase orders to receiving (BRD FR-PRC-07), for the Phase 6 goods receipt.

A receipt may bring in at most the pending quantity plus the tolerance in the business rule
`grnTolPct` (10% by default). apply_receipt() records the quantity against the line's delivery
schedule, oldest due date first, and moves the order to PARTIALLY_RECEIVED or RECEIVED. The goods
receipt calls it inside its own transaction so the stock posting and the PO update stand or fall
together.
"""

from decimal import ROUND_DOWN, Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status

from heyyerp_api.business_rules import service as rules
from heyyerp_api.masters.common import Ctx, audit, not_found, one, rows

RECEIVABLE = ("OPEN", "PARTIALLY_RECEIVED")


def max_receivable(pending: Decimal, tolerance_pct: int | Decimal) -> Decimal:
    """Pending quantity plus the tolerance, to the 3 decimals quantities use."""
    return (pending * (Decimal(100) + Decimal(tolerance_pct)) / Decimal(100)).quantize(
        Decimal("0.001"), rounding=ROUND_DOWN)


async def line_status(ctx: Ctx, po_id: UUID) -> list[dict[str, Any]]:
    tolerance = await rules.get_value(ctx, "grnTolPct")
    lines = await rows(
        ctx,
        "SELECT l.po_line_id, l.line_no, l.item_id, i.code AS item_code, l.ordered_qty, "
        "COALESCE((SELECT sum(s.received_qty) FROM erp.po_schedule s WHERE s.po_line_id = "
        "l.po_line_id), 0) AS received_qty FROM erp.po_line l JOIN erp.item i ON "
        "i.item_id = l.item_id WHERE l.po_id = :id ORDER BY l.line_no", {"id": po_id},
    )
    for line in lines:
        line["pending_qty"] = max(line["ordered_qty"] - line["received_qty"], Decimal(0))
        line["max_receivable_qty"] = max_receivable(line["pending_qty"], tolerance)
    return lines


async def apply_receipt(ctx: Ctx, po_line_id: UUID, quantity: Decimal) -> dict[str, Any]:
    """Record a received quantity against a PO line. Raises 409/422 when it is not allowed."""
    if quantity <= 0:
        raise HTTPException(422, "The received quantity must be above zero")
    line = await one(
        ctx,
        "SELECT l.po_line_id, l.po_id, l.ordered_qty, p.status FROM erp.po_line l "
        "JOIN erp.purchase_order p ON p.po_id = l.po_id WHERE l.po_line_id = :id "
        "FOR UPDATE OF p", {"id": po_line_id},
    )
    if line is None:
        raise not_found("Purchase order line")
    if line["status"] not in RECEIVABLE:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A {str(line['status']).lower().replace('_', ' ')} order cannot receive goods")
    current = next(s for s in await line_status(ctx, line["po_id"])
                   if s["po_line_id"] == po_line_id)
    if quantity > current["max_receivable_qty"]:
        tolerance = await rules.get_value(ctx, "grnTolPct")
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only {current['max_receivable_qty']} can be received on this line "
            f"(pending {current['pending_qty']} plus the {tolerance}% tolerance)")
    schedules = await rows(
        ctx, "SELECT po_schedule_id, scheduled_qty, received_qty FROM erp.po_schedule "
        "WHERE po_line_id = :id ORDER BY due_date, po_schedule_id", {"id": po_line_id})
    left = quantity
    for index, schedule in enumerate(schedules):
        room = max(schedule["scheduled_qty"] - schedule["received_qty"], Decimal(0))
        # Whatever the schedules cannot absorb (the tolerance) goes on the last delivery.
        take = left if index == len(schedules) - 1 else min(left, room)
        if take > 0:
            await rows(ctx, "UPDATE erp.po_schedule SET received_qty = received_qty + :q "
                       "WHERE po_schedule_id = :id RETURNING po_schedule_id",
                       {"q": take, "id": schedule["po_schedule_id"]})
            left -= take
        if left <= 0:
            break
    after = await line_status(ctx, line["po_id"])
    complete = all(s["received_qty"] >= s["ordered_qty"] for s in after)
    new_status = "RECEIVED" if complete else "PARTIALLY_RECEIVED"
    await rows(ctx, "UPDATE erp.purchase_order SET status = :s WHERE po_id = :id "
               "RETURNING po_id", {"s": new_status, "id": line["po_id"]})
    await audit(ctx, "po.receipt", "po", line["po_id"],
                {"po_line_id": po_line_id, "quantity": quantity, "status": new_status})
    return {"po_id": line["po_id"], "status": new_status,
            "line": next(s for s in after if s["po_line_id"] == po_line_id)}
