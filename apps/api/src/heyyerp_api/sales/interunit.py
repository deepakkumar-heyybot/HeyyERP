"""Inter-unit invoices for stock moved between plants (BRD FR-INV-07, FR-SAL-06).

Goods that move between two plants (two GSTINs) need an invoice and an e-way bill. This raises
the invoice from a transfer between different plants that has been dispatched or received, valued
at the cost that left the sending plant; one live invoice per transfer. `pending` lists the
transfers still waiting for one. The IRN and e-way bill are recorded by people with the `gst`
permission until the GST service provider integration (Phase 12). Needs the `gst` permission;
reading needs the sales module.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from heyyerp_api.finance.autopost import book
from heyyerp_api.finance.journals import ReverseIn, reverse_journal
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
from heyyerp_api.sales.orders import money

router = APIRouter(prefix="/sales", tags=["sales"])
manage = requires("gst")
read = requires_module("sales")


class InterUnitIn(Strict):
    transfer_id: UUID


class GstIn(Strict):
    irn: str | None = Field(default=None, min_length=10, max_length=100)
    irn_ack_no: str | None = Field(default=None, min_length=3, max_length=40)
    ewb_no: str | None = Field(default=None, min_length=8, max_length=20)


class InterUnitOut(BaseModel):
    inter_unit_invoice_id: UUID
    invoice_no: str
    from_unit_id: UUID
    to_unit_id: UUID
    transfer_id: UUID
    transfer_no: str
    issue_date: date | None
    taxable_amount: Decimal
    tax_amount: Decimal
    status: str
    irn: str | None
    ewb_no: str | None
    handoff_status: str


class PendingTransfer(BaseModel):
    transfer_id: UUID
    transfer_no: str
    status: str
    from_unit_id: UUID
    to_unit_id: UUID
    dispatched_at: datetime | None
    value: Decimal


IU_SQL = (
    "SELECT v.inter_unit_invoice_id, v.invoice_no, v.from_unit_id, v.to_unit_id, v.transfer_id, "
    "t.transfer_no, v.issue_date, v.taxable_amount, v.tax_amount, v.status, v.irn, v.ewb_no, "
    "CASE WHEN v.status = 'CANCELLED' THEN 'CANCELLED' WHEN v.irn IS NULL THEN 'PENDING' ELSE "
    "'DONE' END AS handoff_status FROM erp.inter_unit_invoice v JOIN erp.stock_transfer t ON "
    "t.transfer_id = v.transfer_id"
)


def visible(ctx: Ctx, row: dict[str, Any]) -> bool:
    see = ctx.principal.may_see_unit
    return bool(see(row["from_unit_id"]) or see(row["to_unit_id"]))


async def load(ctx: Ctx, invoice_id: UUID) -> InterUnitOut | None:
    row = await one(ctx, f"{IU_SQL} WHERE v.inter_unit_invoice_id = :id", {"id": invoice_id})
    return InterUnitOut(**row) if row and visible(ctx, row) else None


async def transfer_value(ctx: Ctx, transfer_id: UUID) -> Decimal:
    total = await one(ctx, "SELECT COALESCE(sum(quantity * unit_cost), 0) AS v FROM "
                      "erp.stock_transfer_line WHERE transfer_id = :id", {"id": transfer_id})
    assert total is not None
    return money(total["v"])


@router.post("/inter-unit-invoices", response_model=InterUnitOut,
             status_code=status.HTTP_201_CREATED)
async def raise_invoice(body: InterUnitIn, ctx: Ctx = Depends(manage)) -> InterUnitOut:
    transfer = await one(
        ctx, "SELECT t.transfer_id, t.transfer_no, t.status, f.unit_id AS from_unit_id, d.unit_id "
        "AS to_unit_id FROM erp.stock_transfer t JOIN erp.store f ON f.store_id = t.from_store_id "
        "JOIN erp.store d ON d.store_id = t.to_store_id WHERE t.transfer_id = :id FOR UPDATE OF t",
        {"id": body.transfer_id})
    if transfer is None or not visible(ctx, transfer):
        raise not_found("Transfer")
    if transfer["from_unit_id"] == transfer["to_unit_id"]:
        raise HTTPException(422, "Both stores are in the same plant; no invoice is needed")
    if transfer["status"] not in ("DISPATCHED", "RECEIVED"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "The transfer has not been dispatched yet")
    if await one(ctx, "SELECT 1 AS x FROM erp.inter_unit_invoice WHERE transfer_id = :id AND "
                 "status <> 'CANCELLED'", {"id": body.transfer_id}):
        raise HTTPException(status.HTTP_409_CONFLICT, "This transfer already has an invoice")
    number = await next_number(ctx, "inter_unit")
    created = await one(
        ctx, "INSERT INTO erp.inter_unit_invoice(tenant_id, from_unit_id, to_unit_id, "
        "transfer_id, invoice_no, status, issue_date, taxable_amount, created_by) VALUES "
        "(erp.current_tenant_id(), :f, :t, :tr, :n, 'ISSUED', CURRENT_DATE, :v, :me) "
        "RETURNING inter_unit_invoice_id",
        {"f": transfer["from_unit_id"], "t": transfer["to_unit_id"], "tr": body.transfer_id,
         "n": number, "v": await transfer_value(ctx, body.transfer_id),
         "me": ctx.principal.profile.app_user_id})
    assert created is not None
    value = await transfer_value(ctx, body.transfer_id)
    sides = (("from_journal_id", transfer["from_unit_id"], "FROM", [
        ("INTER_UNIT_RECEIVABLE", value, Decimal(0)), ("INTER_UNIT_SALES", Decimal(0), value)]),
        ("to_journal_id", transfer["to_unit_id"], "TO", [
            ("INTER_UNIT_PURCHASES", value, Decimal(0)),
            ("INTER_UNIT_PAYABLE", Decimal(0), value)]))
    for column, unit, kind, amounts in sides:
        journal = await book(
            ctx, unit_id=unit, source_type=f"INTER_UNIT_{kind}",
            source_id=created["inter_unit_invoice_id"], narration=f"Inter-unit invoice {number}",
            amounts=amounts)
        if journal is not None:
            await rows(ctx, f"UPDATE erp.inter_unit_invoice SET {column} = :j WHERE "
                       "inter_unit_invoice_id = :id RETURNING inter_unit_invoice_id",
                       {"j": journal, "id": created["inter_unit_invoice_id"]})
    await audit(ctx, "inter_unit.raise", "inter_unit_invoice", created["inter_unit_invoice_id"],
                {"invoice_no": number, "transfer_no": transfer["transfer_no"]})
    result = await load(ctx, created["inter_unit_invoice_id"])
    assert result is not None
    return result


@router.get("/inter-unit-invoices/pending", response_model=list[PendingTransfer])
async def pending_transfers(ctx: Ctx = Depends(read)) -> list[PendingTransfer]:
    """Dispatched or received transfers between plants that have no live invoice yet."""
    found = await rows(
        ctx, "SELECT t.transfer_id, t.transfer_no, t.status, f.unit_id AS from_unit_id, d.unit_id "
        "AS to_unit_id, t.dispatched_at FROM erp.stock_transfer t JOIN erp.store f ON "
        "f.store_id = t.from_store_id JOIN erp.store d ON d.store_id = t.to_store_id WHERE "
        "t.status IN ('DISPATCHED', 'RECEIVED') AND f.unit_id <> d.unit_id AND NOT EXISTS "
        "(SELECT 1 FROM erp.inter_unit_invoice v WHERE v.transfer_id = t.transfer_id AND "
        "v.status <> 'CANCELLED') ORDER BY t.transfer_no")
    return [PendingTransfer(**r, value=await transfer_value(ctx, r["transfer_id"]))
            for r in found if visible(ctx, r)]


@router.get("/inter-unit-invoices", response_model=Page[InterUnitOut])
async def list_invoices(
    iu_status: str | None = Query(None, alias="status"), page: Paging = Depends(),
    ctx: Ctx = Depends(read),
) -> Page[InterUnitOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause_from, scope_params = scope_clause(ctx, "v.from_unit_id")
    if clause_from:
        clause_to, _ = scope_clause(ctx, "v.to_unit_id")
        conditions.append(f"({clause_from} OR {clause_to})")
        params.update(scope_params)
    if iu_status:
        conditions.append("v.status = :st")
        params["st"] = iu_status.upper()
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{IU_SQL}{where} ORDER BY v.invoice_no DESC LIMIT :limit OFFSET "
                       ":offset", {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.inter_unit_invoice v{where}", params)
    return Page(items=[InterUnitOut(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/inter-unit-invoices/{invoice_id}", response_model=InterUnitOut)
async def get_invoice(invoice_id: UUID, ctx: Ctx = Depends(read)) -> InterUnitOut:
    found = await load(ctx, invoice_id)
    if found is None:
        raise not_found("Inter-unit invoice")
    return found


async def require_issued(ctx: Ctx, invoice_id: UUID) -> dict[str, Any]:
    row = await one(
        ctx, "SELECT inter_unit_invoice_id, invoice_no, status, irn, ewb_no, from_unit_id, "
        "to_unit_id FROM erp.inter_unit_invoice WHERE inter_unit_invoice_id = :id FOR UPDATE",
        {"id": invoice_id})
    if row is None or not visible(ctx, row):
        raise not_found("Inter-unit invoice")
    if row["status"] != "ISSUED":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The invoice is {str(row['status']).lower()}")
    return row


@router.post("/inter-unit-invoices/{invoice_id}/cancel", response_model=InterUnitOut)
async def cancel_invoice(invoice_id: UUID, ctx: Ctx = Depends(manage)) -> InterUnitOut:
    row = await require_issued(ctx, invoice_id)
    if row["irn"] or row["ewb_no"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An e-invoice or e-way bill is recorded; cancel it with the GST portal first")
    for journal in await rows(
            ctx, "SELECT from_journal_id AS j FROM erp.inter_unit_invoice WHERE "
            "inter_unit_invoice_id = :id UNION ALL SELECT to_journal_id FROM "
            "erp.inter_unit_invoice WHERE inter_unit_invoice_id = :id", {"id": invoice_id}):
        if journal["j"] is not None:
            await reverse_journal(journal["j"], ReverseIn(
                reason=f"Inter-unit invoice {row['invoice_no']} cancelled"), ctx)
    await rows(ctx, "UPDATE erp.inter_unit_invoice SET status = 'CANCELLED' WHERE "
               "inter_unit_invoice_id = :id RETURNING inter_unit_invoice_id", {"id": invoice_id})
    await audit(ctx, "inter_unit.cancel", "inter_unit_invoice", invoice_id,
                {"invoice_no": row["invoice_no"]})
    result = await load(ctx, invoice_id)
    assert result is not None
    return result


@router.post("/inter-unit-invoices/{invoice_id}/gst-reference", response_model=InterUnitOut)
async def record_gst_reference(
    invoice_id: UUID, body: GstIn, ctx: Ctx = Depends(manage)
) -> InterUnitOut:
    row = await require_issued(ctx, invoice_id)
    if body.irn is None and body.irn_ack_no is None and body.ewb_no is None:
        raise HTTPException(422, "Give the IRN, the acknowledgement number or the e-way bill")
    if (body.irn and row["irn"]) or (body.ewb_no and row["ewb_no"]):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "That reference is already recorded and cannot be changed")
    await rows(
        ctx, "UPDATE erp.inter_unit_invoice SET irn = COALESCE(irn, :irn), irn_ack_no = "
        "COALESCE(:ack, irn_ack_no), ewb_no = COALESCE(ewb_no, :ewb) WHERE inter_unit_invoice_id "
        "= :id RETURNING inter_unit_invoice_id",
        {"irn": body.irn, "ack": body.irn_ack_no, "ewb": body.ewb_no, "id": invoice_id})
    await audit(ctx, "inter_unit.gst_reference", "inter_unit_invoice", invoice_id,
                {"invoice_no": row["invoice_no"]})
    result = await load(ctx, invoice_id)
    assert result is not None
    return result
