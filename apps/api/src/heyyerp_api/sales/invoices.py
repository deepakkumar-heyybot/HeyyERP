"""Customer invoices raised from dispatches, with the GST hand-off (BRD FR-SAL-06, FR-SAL-09).

An invoice is created from a dispatch that has left: one line per order line with what shipped,
at the order's rate and tax code, numbered INV-yy-nnnn, due according to the customer's payment
term. A dispatch has one live invoice; a cancelled invoice frees it to be invoiced again, but only
while no e-invoice (IRN) or e-way bill is recorded against it. The credit check runs again and only
warns, unless the rule `creditLimitBlock` is on. The IRN, acknowledgement and e-way bill numbers
come from the GST service provider (Phase 12 automates it); until then the people with the `gst`
permission record them here, once. Needs sales.dispatch to invoice or cancel.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from heyyerp_api.business_rules import service as rules
from heyyerp_api.finance.autopost import book, post_sales_invoice
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
from heyyerp_api.sales import orders
from heyyerp_api.sales.dispatch import require_dispatch

router = APIRouter(prefix="/sales", tags=["sales"])
manage = requires("sales.dispatch")
gst = requires("gst")
read = requires_module("sales")


class GstIn(Strict):
    irn: str | None = Field(default=None, min_length=10, max_length=100)
    irn_ack_no: str | None = Field(default=None, min_length=3, max_length=40)
    ewb_no: str | None = Field(default=None, min_length=8, max_length=20)


class InvoiceLineOut(BaseModel):
    so_line_id: UUID
    item_code: str
    item_name: str
    customer_part_no: str | None
    quantity: Decimal
    unit_price: Decimal
    taxable_amount: Decimal
    tax_amount: Decimal


class InvoiceSummary(BaseModel):
    invoice_id: UUID
    invoice_no: str
    unit_id: UUID
    customer_id: UUID
    customer_code: str
    dispatch_id: UUID
    dispatch_no: str
    issue_date: date
    due_date: date | None
    taxable_amount: Decimal
    tax_amount: Decimal
    gross_amount: Decimal
    status: str
    irn: str | None
    ewb_no: str | None
    handoff_status: str  # PENDING until the IRN is recorded, then DONE (CANCELLED if cancelled)
    paid_amount: Decimal
    outstanding: Decimal
    payment_status: str  # UNPAID, PART_PAID or PAID
    journal_id: UUID | None


class InvoiceOut(InvoiceSummary):
    irn_ack_no: str | None
    lines: list[InvoiceLineOut]
    warnings: list[str] = []


INVOICE_SQL = (
    "SELECT v.invoice_id, v.invoice_no, v.unit_id, v.customer_id, c.code AS customer_code, "
    "v.dispatch_id, d.dispatch_no, v.issue_date, v.due_date, v.taxable_amount, v.tax_amount, "
    "v.taxable_amount + v.tax_amount AS gross_amount, v.status, v.irn, v.ewb_no, v.irn_ack_no, "
    "CASE WHEN v.status = 'CANCELLED' THEN 'CANCELLED' WHEN v.irn IS NULL THEN 'PENDING' "
    "ELSE 'DONE' END AS handoff_status, v.paid_amount, v.taxable_amount + v.tax_amount - "
    "v.paid_amount AS outstanding, CASE WHEN v.paid_amount = 0 THEN 'UNPAID' WHEN "
    "v.paid_amount >= v.taxable_amount + v.tax_amount THEN 'PAID' ELSE 'PART_PAID' END AS "
    "payment_status, v.journal_id FROM erp.customer_invoice v JOIN erp.customer c ON "
    "c.customer_id = v.customer_id JOIN erp.dispatch d ON d.dispatch_id = v.dispatch_id"
)


async def load(ctx: Ctx, invoice_id: UUID) -> InvoiceOut | None:
    clause, params = scope_clause(ctx, "v.unit_id")
    head = await one(
        ctx, f"{INVOICE_SQL} WHERE v.invoice_id = :id{' AND ' + clause if clause else ''}",
        {**params, "id": invoice_id})
    if head is None:
        return None
    lines = await rows(
        ctx, "SELECT il.so_line_id, i.code AS item_code, i.name AS item_name, "
        "x.customer_part_no, il.quantity, il.unit_price, il.taxable_amount, il.tax_amount FROM "
        "erp.invoice_line il JOIN erp.item i ON i.item_id = il.item_id LEFT JOIN "
        "erp.customer_part_xref x ON x.customer_id = :c AND x.item_id = il.item_id WHERE "
        "il.invoice_id = :id ORDER BY i.code", {"id": invoice_id, "c": head["customer_id"]})
    return InvoiceOut(**head, lines=[InvoiceLineOut(**ln) for ln in lines])


@router.post("/dispatches/{dispatch_id}/invoice", response_model=InvoiceOut,
             status_code=status.HTTP_201_CREATED)
async def invoice_dispatch(dispatch_id: UUID, ctx: Ctx = Depends(manage)) -> InvoiceOut:
    head = await require_dispatch(ctx, dispatch_id, "DISPATCHED")
    if await one(ctx, "SELECT 1 AS x FROM erp.customer_invoice WHERE dispatch_id = :id AND "
                 "status <> 'CANCELLED'", {"id": dispatch_id}):
        raise HTTPException(status.HTTP_409_CONFLICT, "This dispatch is already invoiced")
    order = await one(
        ctx, "SELECT s.customer_id, c.payment_term_id FROM erp.sales_order s JOIN erp.customer c "
        "ON c.customer_id = s.customer_id WHERE s.so_id = :id", {"id": head["so_id"]})
    assert order is not None
    term = await one(ctx, "SELECT due_days FROM erp.payment_term WHERE payment_term_id = :id",
                     {"id": order["payment_term_id"]}) if order["payment_term_id"] else None
    raw = await rows(
        ctx, "SELECT l.so_line_id, l.item_id, l.unit_price, t.gst_rate, sum(dl.quantity) AS "
        "quantity FROM erp.dispatch_line dl JOIN erp.so_line l ON l.so_line_id = dl.so_line_id "
        "LEFT JOIN erp.tax_code t ON t.tax_code_id = l.tax_code_id WHERE dl.dispatch_id = :id "
        "GROUP BY l.so_line_id, l.item_id, l.unit_price, t.gst_rate ORDER BY l.so_line_id",
        {"id": dispatch_id})
    lines = []
    for r in raw:
        net, tax = orders.line_amounts(r["quantity"], r["unit_price"], r["gst_rate"])
        lines.append({**r, "net": net, "tax": tax})
    number = await next_number(ctx, "invoice")
    today = date.today()
    created = await one(
        ctx, "INSERT INTO erp.customer_invoice(tenant_id, unit_id, customer_id, dispatch_id, "
        "invoice_no, issue_date, due_date, taxable_amount, tax_amount, status) VALUES "
        "(erp.current_tenant_id(), :u, :c, :d, :n, :i, :due, :net, :tax, 'ISSUED') "
        "RETURNING invoice_id",
        {"u": head["unit_id"], "c": order["customer_id"], "d": dispatch_id, "n": number,
         "i": today, "due": today + timedelta(days=term["due_days"] if term else 0),
         "net": sum((ln["net"] for ln in lines), Decimal(0)),
         "tax": sum((ln["tax"] for ln in lines), Decimal(0))})
    assert created is not None
    for ln in lines:
        await rows(
            ctx, "INSERT INTO erp.invoice_line(tenant_id, invoice_id, so_line_id, item_id, "
            "quantity, unit_price, taxable_amount, tax_amount) VALUES (erp.current_tenant_id(), "
            ":inv, :l, :i, :q, :p, :net, :tax) RETURNING invoice_line_id",
            {"inv": created["invoice_id"], "l": ln["so_line_id"], "i": ln["item_id"],
             "q": ln["quantity"], "p": ln["unit_price"], "net": ln["net"], "tax": ln["tax"]})
    journal_id = await post_sales_invoice(ctx, created["invoice_id"])
    if journal_id is not None:
        await rows(ctx, "UPDATE erp.customer_invoice SET journal_id = :j WHERE invoice_id = :id "
                   "RETURNING invoice_id", {"j": journal_id, "id": created["invoice_id"]})
    if await rules.get_value(ctx, "cogsTiming") == "ON_INVOICE":
        cost = (await one(ctx, "SELECT cost_amount FROM erp.dispatch WHERE dispatch_id = :id",
                          {"id": dispatch_id}) or {"cost_amount": Decimal(0)})["cost_amount"]
        cogs = await book(
            ctx, unit_id=head["unit_id"], source_type="INVOICE_COGS",
            source_id=created["invoice_id"], narration=f"Cost of sales {number}",
            amounts=[("COST_OF_SALES", cost, Decimal(0)), ("INVENTORY_FG", Decimal(0), cost)])
        if cogs is not None:
            await rows(ctx, "UPDATE erp.customer_invoice SET cogs_journal_id = :j WHERE "
                       "invoice_id = :id RETURNING invoice_id",
                       {"j": cogs, "id": created["invoice_id"]})
    warnings = await orders.credit_warnings(ctx, order["customer_id"], Decimal(0))
    await audit(ctx, "invoice.create", "invoice", created["invoice_id"],
                {"invoice_no": number, "dispatch_no": head["dispatch_no"],
                 "gross": sum((ln["net"] + ln["tax"] for ln in lines), Decimal(0)),
                 "warnings": warnings})
    result = await load(ctx, created["invoice_id"])
    assert result is not None
    result.warnings = warnings
    return result


@router.get("/invoices", response_model=Page[InvoiceSummary])
async def list_invoices(
    customer_id: UUID | None = None, invoice_status: str | None = Query(None, alias="status"),
    dispatch_id: UUID | None = None, page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[InvoiceSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "v.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for text_, key, value in (("v.customer_id = :c", "c", customer_id),
                              ("v.dispatch_id = :d", "d", dispatch_id),
                              ("v.status = :st", "st",
                               invoice_status.upper() if invoice_status else None)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{INVOICE_SQL}{where} ORDER BY v.invoice_no DESC LIMIT :limit "
                       "OFFSET :offset", {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.customer_invoice v{where}", params)
    return Page(items=[InvoiceSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
async def get_invoice(invoice_id: UUID, ctx: Ctx = Depends(read)) -> InvoiceOut:
    found = await load(ctx, invoice_id)
    if found is None:
        raise not_found("Invoice")
    return found


async def require_invoice(ctx: Ctx, invoice_id: UUID) -> dict[str, Any]:
    clause, params = scope_clause(ctx, "unit_id")
    head = await one(
        ctx, "SELECT invoice_id, invoice_no, status, irn, ewb_no, unit_id, paid_amount, "
        "journal_id, cogs_journal_id FROM "
        f"erp.customer_invoice WHERE invoice_id = :id{' AND ' + clause if clause else ''} "
        "FOR UPDATE", {**params, "id": invoice_id})
    if head is None:
        raise not_found("Invoice")
    if head["status"] != "ISSUED":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The invoice is {str(head['status']).lower()}")
    return head


@router.post("/invoices/{invoice_id}/cancel", response_model=InvoiceOut)
async def cancel_invoice(invoice_id: UUID, ctx: Ctx = Depends(manage)) -> InvoiceOut:
    """Cancel an invoice with no e-invoice or e-way bill yet; the dispatch can be re-invoiced."""
    head = await require_invoice(ctx, invoice_id)
    if head["irn"] or head["ewb_no"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An e-invoice or e-way bill is recorded; cancel it with the GST portal first")
    if head["paid_amount"] > 0:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Receipts are applied to this invoice; reverse them first")
    reversal = None
    if head["journal_id"] is not None:
        reversed_ = await reverse_journal(
            head["journal_id"], ReverseIn(reason=f"Invoice {head['invoice_no']} cancelled"), ctx)
        reversal = reversed_.journal_id
    if head["cogs_journal_id"] is not None:  # the cost was recognised with the sale
        await reverse_journal(head["cogs_journal_id"], ReverseIn(
            reason=f"Invoice {head['invoice_no']} cancelled"), ctx)
    await rows(ctx, "UPDATE erp.customer_invoice SET status = 'CANCELLED', "
               "reversal_journal_id = :r WHERE invoice_id = :id RETURNING invoice_id",
               {"r": reversal, "id": invoice_id})
    await audit(ctx, "invoice.cancel", "invoice", invoice_id, {"invoice_no": head["invoice_no"]})
    result = await load(ctx, invoice_id)
    assert result is not None
    return result


@router.post("/invoices/{invoice_id}/gst-reference", response_model=InvoiceOut)
async def record_gst_reference(
    invoice_id: UUID, body: GstIn, ctx: Ctx = Depends(gst)
) -> InvoiceOut:
    """Record the IRN, acknowledgement and e-way bill from the GST provider; each only once."""
    head = await require_invoice(ctx, invoice_id)
    if body.irn is None and body.irn_ack_no is None and body.ewb_no is None:
        raise HTTPException(422, "Give the IRN, the acknowledgement number or the e-way bill")
    if (body.irn and head["irn"]) or (body.ewb_no and head["ewb_no"]):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "That reference is already recorded and cannot be changed")
    await rows(
        ctx, "UPDATE erp.customer_invoice SET irn = COALESCE(irn, :irn), irn_ack_no = "
        "COALESCE(:ack, irn_ack_no), ewb_no = COALESCE(ewb_no, :ewb) WHERE invoice_id = :id "
        "RETURNING invoice_id",
        {"irn": body.irn, "ack": body.irn_ack_no, "ewb": body.ewb_no, "id": invoice_id})
    await audit(ctx, "invoice.gst_reference", "invoice", invoice_id,
                {"invoice_no": head["invoice_no"], "irn": bool(body.irn),
                 "ewb_no": bool(body.ewb_no)})
    result = await load(ctx, invoice_id)
    assert result is not None
    return result

