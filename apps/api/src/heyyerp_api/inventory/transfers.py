"""Moving stock between stores and plants, and tracing where a lot came from (BRD FR-INV-05).

A transfer is DRAFT -> DISPATCHED -> RECEIVED. Dispatch takes the stock out of the source lots and
off the plant's FIFO layers (the stock is "in transit": counted nowhere); receiving puts it into a
lot of the same number in the destination store, with the same expiry and age, and opens a cost
layer at the cost that left. So value is carried between plants and nothing is lost on the way.
Only AVAILABLE, unexpired lots can be sent. Needs inv.transfer; reading needs the inventory module.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from heyyerp_api.inventory import ledger
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
)
from heyyerp_api.procurement.numbering import next_number

router = APIRouter(prefix="/inventory", tags=["inventory"])
write = requires("inv.transfer")
read = requires_module("inventory")


class TransferLineIn(Strict):
    lot_id: UUID
    quantity: Decimal = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)


class TransferIn(Strict):
    from_store_id: UUID
    to_store_id: UUID
    lines: list[TransferLineIn] = Field(min_length=1, max_length=50)


class TransferLine(BaseModel):
    transfer_line_id: UUID
    item_id: UUID
    item_code: str
    lot_id: UUID
    lot_no: str
    quantity: Decimal
    unit_cost: Decimal | None


class TransferSummary(BaseModel):
    transfer_id: UUID
    transfer_no: str
    status: str
    from_store_id: UUID
    from_store_code: str
    from_unit_id: UUID
    to_store_id: UUID
    to_store_code: str
    to_unit_id: UUID
    dispatched_at: datetime | None
    received_at: datetime | None


class TransferOut(TransferSummary):
    lines: list[TransferLine]


TRANSFER_SQL = (
    "SELECT t.transfer_id, t.transfer_no, t.status, t.from_store_id, f.code AS from_store_code, "
    "f.unit_id AS from_unit_id, t.to_store_id, d.code AS to_store_code, d.unit_id AS to_unit_id, "
    "t.dispatched_at, t.received_at FROM erp.stock_transfer t JOIN erp.store f ON "
    "f.store_id = t.from_store_id JOIN erp.store d ON d.store_id = t.to_store_id"
)


def visible(ctx: Ctx, row: dict[str, Any]) -> bool:
    """A transfer is visible to people who may see either end."""
    see = ctx.principal.may_see_unit
    return bool(see(row["from_unit_id"]) or see(row["to_unit_id"]))


async def load(ctx: Ctx, transfer_id: UUID) -> TransferOut | None:
    head = await one(ctx, f"{TRANSFER_SQL} WHERE t.transfer_id = :id", {"id": transfer_id})
    if head is None or not visible(ctx, head):
        return None
    lines = await rows(
        ctx,
        "SELECT l.transfer_line_id, l.item_id, i.code AS item_code, l.lot_id, t.lot_no, "
        "l.quantity, l.unit_cost FROM erp.stock_transfer_line l JOIN erp.item i ON "
        "i.item_id = l.item_id JOIN erp.lot t ON t.lot_id = l.lot_id WHERE l.transfer_id = :id "
        "ORDER BY i.code, t.lot_no", {"id": transfer_id})
    return TransferOut(**head, lines=[TransferLine(**ln) for ln in lines])


async def require_transfer(ctx: Ctx, transfer_id: UUID, *allowed: str) -> dict[str, Any]:
    """Lock the transfer and check it is visible and in one of the allowed states."""
    head = await one(ctx, f"{TRANSFER_SQL} WHERE t.transfer_id = :id FOR UPDATE OF t",
                     {"id": transfer_id})
    if head is None or not visible(ctx, head):
        raise not_found("Transfer")
    if head["status"] not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The transfer is {str(head['status']).lower()}; this needs it to be "
            f"{' or '.join(a.lower() for a in allowed)}")
    return head


@router.post("/transfers", response_model=TransferOut, status_code=status.HTTP_201_CREATED)
async def create_transfer(body: TransferIn, ctx: Ctx = Depends(write)) -> TransferOut:
    if body.from_store_id == body.to_store_id:
        raise HTTPException(422, "The two stores must differ")
    stores = {s["store_id"]: s for s in await rows(
        ctx, "SELECT store_id, unit_id, active FROM erp.store WHERE store_id = ANY(CAST(:ids AS "
        "uuid[]))", {"ids": [body.from_store_id, body.to_store_id]})}
    source = stores.get(body.from_store_id)
    if source is None or not ctx.principal.may_see_unit(source["unit_id"]):
        raise not_found("Source store")
    target = stores.get(body.to_store_id)
    if target is None:
        raise HTTPException(422, "The destination store does not exist")
    if not (source["active"] and target["active"]):
        raise HTTPException(422, "Both stores must be active")
    lot_ids = [ln.lot_id for ln in body.lines]
    if len(set(lot_ids)) != len(lot_ids):
        raise HTTPException(422, "Each lot may appear only once")
    lots = {r["lot_id"]: r for r in await rows(
        ctx, "SELECT lot_id, item_id, store_id FROM erp.lot WHERE lot_id = ANY(CAST(:ids AS "
        "uuid[]))", {"ids": lot_ids})}
    for lot_id in lot_ids:
        if lot_id not in lots or lots[lot_id]["store_id"] != body.from_store_id:
            raise HTTPException(422, "Every lot must be a lot in the source store")
    number = await next_number(ctx, "transfer")
    created = await one(
        ctx, "INSERT INTO erp.stock_transfer(tenant_id, transfer_no, from_store_id, to_store_id) "
        "VALUES (erp.current_tenant_id(), :n, :f, :t) RETURNING transfer_id",
        {"n": number, "f": body.from_store_id, "t": body.to_store_id})
    assert created is not None
    for ln in body.lines:
        await rows(
            ctx, "INSERT INTO erp.stock_transfer_line(tenant_id, transfer_id, item_id, lot_id, "
            "quantity) VALUES (erp.current_tenant_id(), :t, :i, :l, :q) RETURNING transfer_line_id",
            {"t": created["transfer_id"], "i": lots[ln.lot_id]["item_id"], "l": ln.lot_id,
             "q": ln.quantity})
    await audit(ctx, "transfer.create", "transfer", created["transfer_id"],
                {"transfer_no": number, "lines": len(body.lines)})
    result = await load(ctx, created["transfer_id"])
    assert result is not None
    return result


@router.get("/transfers", response_model=Page[TransferSummary])
async def list_transfers(
    status_: str | None = Query(None, alias="status"), store_id: UUID | None = None,
    page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[TransferSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    if status_:
        conditions.append("t.status = :status")
        params["status"] = status_.upper()
    if store_id is not None:
        conditions.append("(t.from_store_id = :store OR t.to_store_id = :store)")
        params["store"] = store_id
    scope = ctx.principal.profile.unit_ids
    if scope is not None:
        conditions.append("(f.unit_id = ANY(CAST(:scope AS uuid[])) OR "
                          "d.unit_id = ANY(CAST(:scope AS uuid[])))")
        params["scope"] = list(scope)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{TRANSFER_SQL}{where} ORDER BY t.transfer_no DESC LIMIT :limit "
                       "OFFSET :offset", {**params, "limit": page.limit, "offset": page.offset})
    total = await count(
        ctx, "SELECT count(*) FROM erp.stock_transfer t JOIN erp.store f ON f.store_id = "
        f"t.from_store_id JOIN erp.store d ON d.store_id = t.to_store_id{where}", params)
    return Page(items=[TransferSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/transfers/{transfer_id}", response_model=TransferOut)
async def get_transfer(transfer_id: UUID, ctx: Ctx = Depends(read)) -> TransferOut:
    found = await load(ctx, transfer_id)
    if found is None:
        raise not_found("Transfer")
    return found


@router.post("/transfers/{transfer_id}/cancel", response_model=TransferOut)
async def cancel_transfer(transfer_id: UUID, ctx: Ctx = Depends(write)) -> TransferOut:
    await require_transfer(ctx, transfer_id, "DRAFT")
    await rows(ctx, "UPDATE erp.stock_transfer SET status = 'CANCELLED' WHERE transfer_id = :id "
               "RETURNING transfer_id", {"id": transfer_id})
    await audit(ctx, "transfer.cancel", "transfer", transfer_id, {})
    result = await load(ctx, transfer_id)
    assert result is not None
    return result


@router.post("/transfers/{transfer_id}/dispatch", response_model=TransferOut)
async def dispatch_transfer(transfer_id: UUID, ctx: Ctx = Depends(write)) -> TransferOut:
    """Take the stock out of the source store; the cost that leaves travels with it."""
    head = await require_transfer(ctx, transfer_id, "DRAFT")
    if not ctx.principal.may_see_unit(head["from_unit_id"]):
        raise not_found("Transfer")
    lines = await rows(
        ctx,
        "SELECT l.transfer_line_id, l.item_id, l.lot_id, l.quantity, t.status, t.lot_no, "
        "(t.expiry_at IS NOT NULL AND t.expiry_at <= now()) AS expired FROM "
        "erp.stock_transfer_line l JOIN erp.lot t ON t.lot_id = l.lot_id WHERE l.transfer_id = :id "
        "ORDER BY l.lot_id", {"id": transfer_id})  # fixed order: no deadlock between two transfers
    for line in lines:
        if line["status"] != "AVAILABLE" or line["expired"]:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Lot {line['lot_no']} cannot be sent: it is "
                f"{'expired' if line['expired'] else str(line['status']).lower()}")
        ledger_id = await ledger.move(
            ctx, unit_id=head["from_unit_id"], item_id=line["item_id"], lot_id=line["lot_id"],
            store_id=head["from_store_id"], delta=-line["quantity"], movement_type="TRANSFER_OUT",
            reference_type="TRANSFER", reference_id=transfer_id,
            idempotency_key=f"transfer:{transfer_id}:out:{line['transfer_line_id']}")
        cost = await ledger.take_layers(
            ctx, unit_id=head["from_unit_id"], item_id=line["item_id"],
            quantity=line["quantity"], issue_ledger_id=ledger_id)
        await rows(
            ctx, "UPDATE erp.stock_transfer_line SET unit_cost = :c WHERE transfer_line_id = :id "
            "RETURNING transfer_line_id",
            {"c": (cost / line["quantity"]).quantize(Decimal("0.000001")),
             "id": line["transfer_line_id"]})
    await rows(ctx, "UPDATE erp.stock_transfer SET status = 'DISPATCHED', dispatched_at = now() "
               "WHERE transfer_id = :id RETURNING transfer_id", {"id": transfer_id})
    await audit(ctx, "transfer.dispatch", "transfer", transfer_id,
                {"transfer_no": head["transfer_no"], "lines": len(lines)})
    result = await load(ctx, transfer_id)
    assert result is not None
    return result


@router.post("/transfers/{transfer_id}/receive", response_model=TransferOut)
async def receive_transfer(transfer_id: UUID, ctx: Ctx = Depends(write)) -> TransferOut:
    """Book the stock into the destination store (whoever may see that plant does this)."""
    head = await require_transfer(ctx, transfer_id, "DISPATCHED")
    if not ctx.principal.may_see_unit(head["to_unit_id"]):
        raise not_found("Transfer")
    lines = await rows(
        ctx,
        "SELECT l.transfer_line_id, l.item_id, l.quantity, l.unit_cost, t.lot_no, "
        "t.supplier_lot_no, t.received_at, t.expiry_at FROM erp.stock_transfer_line l JOIN "
        "erp.lot t ON t.lot_id = l.lot_id WHERE l.transfer_id = :id ORDER BY l.lot_id",
        {"id": transfer_id})
    for line in lines:
        target = await one(
            ctx, "SELECT lot_id FROM erp.lot WHERE item_id = :i AND store_id = :s AND lot_no = :n "
            "FOR UPDATE", {"i": line["item_id"], "s": head["to_store_id"], "n": line["lot_no"]})
        if target is None:
            target = await one(
                ctx,
                "INSERT INTO erp.lot(tenant_id, unit_id, item_id, store_id, lot_no, "
                "supplier_lot_no, received_at, expiry_at, status) VALUES "
                "(erp.current_tenant_id(), :u, :i, :s, :n, :sl, :r, :e, 'AVAILABLE') "
                "RETURNING lot_id",
                {"u": head["to_unit_id"], "i": line["item_id"], "s": head["to_store_id"],
                 "n": line["lot_no"], "sl": line["supplier_lot_no"], "r": line["received_at"],
                 "e": line["expiry_at"]})
        assert target is not None
        ledger_id = await ledger.move(
            ctx, unit_id=head["to_unit_id"], item_id=line["item_id"], lot_id=target["lot_id"],
            store_id=head["to_store_id"], delta=line["quantity"], movement_type="TRANSFER_IN",
            reference_type="TRANSFER", reference_id=transfer_id,
            idempotency_key=f"transfer:{transfer_id}:in:{line['transfer_line_id']}")
        await ledger.add_layer(
            ctx, ledger_id=ledger_id, item_id=line["item_id"], lot_id=target["lot_id"],
            quantity=line["quantity"], unit_cost=line["unit_cost"] or Decimal(0))
    await rows(ctx, "UPDATE erp.stock_transfer SET status = 'RECEIVED', received_at = now() "
               "WHERE transfer_id = :id RETURNING transfer_id", {"id": transfer_id})
    await audit(ctx, "transfer.receive", "transfer", transfer_id,
                {"transfer_no": head["transfer_no"], "lines": len(lines)})
    result = await load(ctx, transfer_id)
    assert result is not None
    return result


# ------------------------------------------------------------------ lot genealogy
class TraceMove(BaseModel):
    movement_type: str
    quantity: Decimal
    reference_type: str
    reference_id: UUID
    document_no: str | None
    occurred_at: datetime


class TraceLink(BaseModel):
    direction: str  # "from" (this lot came out of that one) or "to" (this lot went into that one)
    lot_id: UUID | None
    lot_no: str
    store_code: str
    transfer_no: str
    quantity: Decimal


class Trace(BaseModel):
    lot_id: UUID
    lot_no: str
    item_code: str
    store_code: str
    status: str
    received_at: datetime
    expiry_at: datetime | None
    origin: dict[str, Any] | None  # the goods receipt and vendor, when it came from a supplier
    on_hand: Decimal
    moves: list[TraceMove]
    links: list[TraceLink]


@router.get("/lots/{lot_id}/trace", response_model=Trace)
async def trace_lot(lot_id: UUID, ctx: Ctx = Depends(read)) -> Trace:
    """Where a lot came from, everything that happened to it, and where it went."""
    lot = await one(
        ctx, "SELECT t.lot_id, t.lot_no, t.item_id, i.code AS item_code, t.unit_id, s.code AS "
        "store_code, t.store_id, t.status, t.received_at, t.expiry_at, COALESCE((SELECT "
        "sum(quantity_on_hand) FROM erp.stock_balance b WHERE b.lot_id = t.lot_id), 0) AS on_hand "
        "FROM erp.lot t JOIN erp.item i ON i.item_id = t.item_id JOIN erp.store s ON "
        "s.store_id = t.store_id WHERE t.lot_id = :id", {"id": lot_id})
    if lot is None or not ctx.principal.may_see_unit(lot["unit_id"]):
        raise not_found("Lot")
    origin = await one(
        ctx, "SELECT g.grn_no, g.supplier_dc_no, p.po_no, v.name AS vendor, g.received_at, "
        "gl.received_qty, gl.accepted_qty, gl.rejected_qty FROM erp.grn_line gl JOIN erp.grn g ON "
        "g.grn_id = gl.grn_id JOIN erp.purchase_order p ON p.po_id = g.po_id JOIN erp.vendor v "
        "ON v.vendor_id = p.vendor_id WHERE gl.lot_id = :id", {"id": lot_id})
    moves = await rows(
        ctx,
        "SELECT l.movement_type, l.qty_delta AS quantity, l.reference_type, l.reference_id, "
        "COALESCE(g.grn_no, i.issue_no, tr.transfer_no, c.count_no) AS document_no, l.occurred_at "
        "FROM erp.stock_ledger l LEFT JOIN erp.grn g ON g.grn_id = l.reference_id AND "
        "l.reference_type = 'GRN' LEFT JOIN erp.stock_issue i ON i.issue_id = l.reference_id AND "
        "l.reference_type = 'ISSUE' LEFT JOIN erp.stock_transfer tr ON tr.transfer_id = "
        "l.reference_id AND l.reference_type = 'TRANSFER' LEFT JOIN erp.stock_count c ON "
        "c.count_id = l.reference_id AND l.reference_type = 'COUNT' WHERE l.lot_id = :id "
        "ORDER BY l.occurred_at, l.ledger_id", {"id": lot_id})
    links = await rows(
        ctx,
        "SELECT CASE WHEN x.lot_id = :id THEN 'to' ELSE 'from' END AS direction, "
        "CASE WHEN x.lot_id = :id THEN d.lot_id ELSE x.lot_id END AS lot_id, x.lot_no, "
        "CASE WHEN x.lot_id = :id THEN ds.code ELSE fs.code END AS store_code, "
        "tr.transfer_no, x.quantity FROM (SELECT l.transfer_id, l.lot_id, l.item_id, l.quantity, "
        "t.lot_no FROM erp.stock_transfer_line l JOIN erp.lot t ON t.lot_id = l.lot_id) x "
        "JOIN erp.stock_transfer tr ON tr.transfer_id = x.transfer_id AND tr.status = 'RECEIVED' "
        "JOIN erp.store fs ON fs.store_id = tr.from_store_id JOIN erp.store ds ON "
        "ds.store_id = tr.to_store_id LEFT JOIN erp.lot d ON d.item_id = x.item_id AND "
        "d.store_id = tr.to_store_id AND d.lot_no = x.lot_no WHERE x.lot_id = :id OR "
        "(d.lot_id = :id) ORDER BY tr.transfer_no", {"id": lot_id})
    return Trace(
        **{k: lot[k] for k in ("lot_id", "lot_no", "item_code", "store_code", "status",
                               "received_at", "expiry_at", "on_hand")},
        origin=origin, moves=[TraceMove(**m) for m in moves],
        links=[TraceLink(**ln) for ln in links])
