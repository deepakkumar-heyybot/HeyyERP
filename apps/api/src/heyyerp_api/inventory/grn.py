"""Goods receipts and the QA decision on received lots (BRD FR-PRC-07, FR-PRC-08, FR-INV-02).

Posting a GRN is one transaction: for each line it creates a lot in QA_HOLD (expiry = receipt date
plus the item's shelf life), posts the receipt to the stock ledger, opens a FIFO cost layer at the
PO price and records the quantity against the PO line, which refuses more than the pending
quantity plus the `grnTolPct` tolerance. Stock in QA_HOLD is on hand but cannot be issued. The QA
decision then accepts the lot (AVAILABLE) or sends the rejected part back: it leaves the stock and a
purchase return is opened for the debit note. Posting needs grn.post, the decision needs qa.approve,
reading needs the inventory module.
"""

from datetime import UTC, datetime, timedelta
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
    db_errors,
    not_found,
    one,
    require_in_scope,
    requires,
    requires_module,
    rows,
    scope_clause,
)
from heyyerp_api.procurement import receiving
from heyyerp_api.procurement.numbering import next_number
from heyyerp_api.quality.inspections import clearance, record_decision

router = APIRouter(prefix="/inventory", tags=["inventory"])
post_grn = requires("grn.post")
decide_qa = requires("qa.approve")
read = requires_module("inventory")
QTY = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)


class GrnLineIn(Strict):
    po_line_id: UUID
    store_id: UUID
    received_qty: Decimal = QTY
    supplier_lot_no: str | None = Field(default=None, max_length=60)


class GrnIn(Strict):
    po_id: UUID
    supplier_dc_no: str = Field(min_length=1, max_length=60)
    received_at: datetime | None = None
    lines: list[GrnLineIn] = Field(min_length=1, max_length=100)


class InspectIn(Strict):
    accepted_qty: Decimal = Field(ge=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)
    note: str | None = Field(default=None, max_length=500)


class GrnLineOut(BaseModel):
    grn_line_id: UUID
    po_line_id: UUID
    item_id: UUID
    item_code: str
    store_id: UUID
    store_code: str
    lot_id: UUID
    lot_no: str
    lot_status: str
    expiry_at: datetime | None
    received_qty: Decimal
    accepted_qty: Decimal
    rejected_qty: Decimal
    inspected: bool


class GrnSummary(BaseModel):
    grn_id: UUID
    grn_no: str
    unit_id: UUID
    unit_code: str
    po_id: UUID
    po_no: str
    vendor_code: str
    supplier_dc_no: str | None
    received_at: datetime
    status: str


class GrnOut(GrnSummary):
    lines: list[GrnLineOut]


GRN_SQL = (
    "SELECT g.grn_id, g.grn_no, g.unit_id, u.code AS unit_code, g.po_id, p.po_no, "
    "v.code AS vendor_code, g.supplier_dc_no, g.received_at, g.status FROM erp.grn g "
    "JOIN erp.unit u ON u.unit_id = g.unit_id JOIN erp.purchase_order p ON p.po_id = g.po_id "
    "JOIN erp.vendor v ON v.vendor_id = p.vendor_id"
)


async def load(ctx: Ctx, grn_id: UUID) -> GrnOut | None:
    clause, params = scope_clause(ctx, "g.unit_id")
    head = await one(ctx, f"{GRN_SQL} WHERE g.grn_id = :id{' AND ' + clause if clause else ''}",
                     {**params, "id": grn_id})
    if head is None:
        return None
    lines = await rows(
        ctx,
        "SELECT l.grn_line_id, l.po_line_id, l.item_id, i.code AS item_code, l.store_id, "
        "s.code AS store_code, l.lot_id, t.lot_no, t.status AS lot_status, t.expiry_at, "
        "l.received_qty, l.accepted_qty, l.rejected_qty, "
        "(l.accepted_qty + l.rejected_qty > 0) AS inspected FROM erp.grn_line l "
        "JOIN erp.item i ON i.item_id = l.item_id JOIN erp.store s ON s.store_id = l.store_id "
        "JOIN erp.lot t ON t.lot_id = l.lot_id WHERE l.grn_id = :id ORDER BY t.lot_no",
        {"id": grn_id})
    return GrnOut(**head, lines=[GrnLineOut(**ln) for ln in lines])


@router.get("/grns", response_model=Page[GrnSummary])
async def list_grns(
    po_id: UUID | None = None,
    grn_status: str | None = Query(None, alias="status"),
    page: Paging = Depends(),
    ctx: Ctx = Depends(read),
) -> Page[GrnSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "g.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for column, name, value in (("g.po_id", "po", po_id),
                                ("g.status", "status", grn_status and grn_status.upper())):
        if value is not None:
            conditions.append(f"{column} = :{name}")
            params[name] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{GRN_SQL}{where} ORDER BY g.received_at DESC, g.grn_no DESC "
                       "LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.grn g{where}", params)
    return Page(items=[GrnSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/grns/{grn_id}", response_model=GrnOut)
async def get_grn(grn_id: UUID, ctx: Ctx = Depends(read)) -> GrnOut:
    grn = await load(ctx, grn_id)
    if grn is None:
        raise not_found("Goods receipt")
    return grn


@router.post("/grns", response_model=GrnOut, status_code=status.HTTP_201_CREATED)
async def create_grn(body: GrnIn, ctx: Ctx = Depends(post_grn)) -> GrnOut:
    """Receive goods against an open purchase order. Everything posts together or not at all."""
    po = await one(ctx, "SELECT po_id, po_no, unit_id, status FROM erp.purchase_order "
                   "WHERE po_id = :id", {"id": body.po_id})
    if po is None:
        raise not_found("Purchase order")
    require_in_scope(ctx, po["unit_id"])
    dc = body.supplier_dc_no.strip()
    if await one(ctx, "SELECT 1 AS x FROM erp.grn WHERE po_id = :po AND "
                 "lower(supplier_dc_no) = lower(:dc) AND status <> 'CANCELLED'",
                 {"po": body.po_id, "dc": dc}):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Delivery challan {dc} was already received on this order")
    received_at = body.received_at or datetime.now(UTC)
    if received_at > datetime.now(UTC) + timedelta(minutes=5):
        raise HTTPException(422, "The receipt date cannot be in the future")
    ids = [ln.po_line_id for ln in body.lines]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "A PO line can appear only once on a goods receipt")
    po_lines = {r["po_line_id"]: r for r in await rows(
        ctx, "SELECT l.po_line_id, l.line_no, l.item_id, l.unit_price, i.code, i.active, "
        "i.shelf_life_days FROM erp.po_line l JOIN erp.item i ON i.item_id = l.item_id "
        "WHERE l.po_id = :po", {"po": body.po_id})}
    stores = {r["store_id"]: r for r in await rows(
        ctx, "SELECT store_id, unit_id, active FROM erp.store WHERE store_id = "
        "ANY(CAST(:ids AS uuid[]))", {"ids": [ln.store_id for ln in body.lines]})}
    for line in body.lines:
        if line.po_line_id not in po_lines:
            raise HTTPException(422, "A line does not belong to this purchase order")
        store = stores.get(line.store_id)
        if store is None or store["unit_id"] != po["unit_id"] or not store["active"]:
            raise HTTPException(422, "The store must be an active store of the order's plant")
        if not po_lines[line.po_line_id]["active"]:
            raise HTTPException(422, f"Item {po_lines[line.po_line_id]['code']} is inactive")
    number = await next_number(ctx, "grn")
    with db_errors("Goods receipt"):
        grn = await one(
            ctx,
            "INSERT INTO erp.grn(tenant_id, unit_id, po_id, grn_no, supplier_dc_no, received_at, "
            "status, posted_by) VALUES (erp.current_tenant_id(), :unit, :po, :no, :dc, :at, "
            "'POSTED', :me) RETURNING grn_id",
            {"unit": po["unit_id"], "po": body.po_id, "no": number, "dc": dc, "at": received_at,
             "me": ctx.principal.profile.app_user_id})
    assert grn is not None
    for line in sorted(body.lines, key=lambda ln: str(ln.po_line_id)):  # fixed lock order
        info = po_lines[line.po_line_id]
        expiry = (received_at + timedelta(days=info["shelf_life_days"])
                  if info["shelf_life_days"] else None)
        lot_no = f"{number}/{info['line_no']}"
        lot = await one(
            ctx,
            "INSERT INTO erp.lot(tenant_id, unit_id, item_id, store_id, lot_no, supplier_lot_no, "
            "received_at, expiry_at, status) VALUES (erp.current_tenant_id(), :unit, :item, "
            ":store, :lot, :supplier, :at, :exp, 'QA_HOLD') RETURNING lot_id",
            {"unit": po["unit_id"], "item": info["item_id"], "store": line.store_id,
             "lot": lot_no, "supplier": line.supplier_lot_no, "at": received_at, "exp": expiry})
        assert lot is not None
        ledger_id = await ledger.move(
            ctx, unit_id=po["unit_id"], item_id=info["item_id"], lot_id=lot["lot_id"],
            store_id=line.store_id, delta=line.received_qty, movement_type="RECEIPT",
            reference_type="GRN", reference_id=grn["grn_id"],
            idempotency_key=f"grn:{grn['grn_id']}:{line.po_line_id}")
        await ledger.add_layer(ctx, ledger_id=ledger_id, item_id=info["item_id"],
                               lot_id=lot["lot_id"], quantity=line.received_qty,
                               unit_cost=info["unit_price"])
        await receiving.apply_receipt(ctx, line.po_line_id, line.received_qty)
        await one(
            ctx,
            "INSERT INTO erp.grn_line(tenant_id, grn_id, po_line_id, item_id, store_id, lot_id, "
            "received_qty) VALUES (erp.current_tenant_id(), :grn, :pl, :item, :store, :lot, :q) "
            "RETURNING grn_line_id",
            {"grn": grn["grn_id"], "pl": line.po_line_id, "item": info["item_id"],
             "store": line.store_id, "lot": lot["lot_id"], "q": line.received_qty})
    await audit(ctx, "grn.post", "grn", grn["grn_id"],
                {"grn_no": number, "po_id": body.po_id, "supplier_dc_no": dc,
                 "lines": len(body.lines)})
    result = await load(ctx, grn["grn_id"])
    assert result is not None
    return result


@router.post("/grn-lines/{grn_line_id}/inspect", response_model=GrnOut)
async def inspect_line(grn_line_id: UUID, body: InspectIn, ctx: Ctx = Depends(decide_qa)) -> GrnOut:
    """QA decision on a received lot: accept the good quantity, return the rest to the vendor."""
    line = await one(
        ctx,
        "SELECT l.grn_line_id, l.grn_id, l.item_id, l.store_id, l.lot_id, l.received_qty, "
        "l.accepted_qty, l.rejected_qty, g.unit_id, g.po_id, t.status AS lot_status "
        "FROM erp.grn_line l JOIN erp.grn g ON g.grn_id = l.grn_id JOIN erp.lot t ON "
        "t.lot_id = l.lot_id WHERE l.grn_line_id = :id FOR UPDATE OF l, t", {"id": grn_line_id})
    if line is None or not ctx.principal.may_see_unit(line["unit_id"]):
        raise not_found("Goods receipt line")
    if line["accepted_qty"] + line["rejected_qty"] > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "This line has already been inspected")
    if line["lot_status"] != "QA_HOLD":
        raise HTTPException(status.HTTP_409_CONFLICT, f"The lot is {line['lot_status']}")
    if body.accepted_qty > line["received_qty"]:
        raise HTTPException(422, "Cannot accept more than was received")
    if body.accepted_qty > 0:
        await clearance(ctx, kind="INCOMING", subject_type="GRN_LINE", subject_id=grn_line_id,
                        item_id=line["item_id"], since=None)
    rejected = line["received_qty"] - body.accepted_qty
    if rejected > 0:
        out = await ledger.move(
            ctx, unit_id=line["unit_id"], item_id=line["item_id"], lot_id=line["lot_id"],
            store_id=line["store_id"], delta=-rejected, movement_type="QA_REJECT",
            reference_type="GRN", reference_id=line["grn_id"],
            idempotency_key=f"grn-reject:{grn_line_id}")
        await ledger.take_layers(ctx, unit_id=line["unit_id"], item_id=line["item_id"],
                                 quantity=rejected, issue_ledger_id=out)
        if await one(ctx, "SELECT 1 AS x FROM erp.purchase_return WHERE grn_id = :id",
                     {"id": line["grn_id"]}) is None:
            vendor = await one(ctx, "SELECT vendor_id FROM erp.purchase_order WHERE po_id = :id",
                               {"id": line["po_id"]})
            assert vendor is not None
            await rows(
                ctx,
                "INSERT INTO erp.purchase_return(tenant_id, grn_id, vendor_id, return_no) VALUES "
                "(erp.current_tenant_id(), :g, :v, :n) RETURNING return_id",
                {"g": line["grn_id"], "v": vendor["vendor_id"],
                 "n": await next_number(ctx, "return")})
    await rows(ctx, "UPDATE erp.grn_line SET accepted_qty = :a, rejected_qty = :r WHERE "
               "grn_line_id = :id RETURNING grn_line_id",
               {"a": body.accepted_qty, "r": rejected, "id": grn_line_id})
    await rows(ctx, "UPDATE erp.lot SET status = :s WHERE lot_id = :id RETURNING lot_id",
               {"s": "AVAILABLE" if body.accepted_qty > 0 else "BLOCKED", "id": line["lot_id"]})
    await record_decision(
        ctx, kind="INCOMING", subject_type="GRN_LINE", subject_id=grn_line_id,
        unit_id=line["unit_id"], notes=body.note,
        result="REJECT" if body.accepted_qty == 0 else "PARTIAL" if rejected > 0 else "ACCEPT")
    await audit(ctx, "grn.inspect", "grn", line["grn_id"],
                {"grn_line_id": grn_line_id, "accepted": body.accepted_qty, "rejected": rejected,
                 "note": body.note})
    result = await load(ctx, line["grn_id"])
    assert result is not None
    return result
