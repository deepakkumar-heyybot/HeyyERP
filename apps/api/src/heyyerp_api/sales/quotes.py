"""Customer quotations: offer, accept, turn into a sales order (BRD FR-SAL-02, screen "RFQ &
quotations").

A quotation records what was offered to a customer (rate from the price list when none is typed)
and the customer enquiry it answers. DRAFT -> SENT -> ACCEPTED or REJECTED; a quotation past its
valid-until date can no longer be sent or accepted. An accepted quotation converts into a sales
order at the quoted rates, once. Needs sales.quote to quote and sales.order to convert.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

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
from heyyerp_api.sales.orders import SOLD_TYPES, SoIn, SoLineIn, line_amounts
from heyyerp_api.sales.pricing import price_on

router = APIRouter(prefix="/sales", tags=["sales"])
quote_permission = requires("sales.quote")
order_permission = requires("sales.order")
read = requires_module("sales")
Qty = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)


class QuoteLineIn(Strict):
    item_id: UUID
    quantity: Decimal = Qty
    unit_price: Decimal | None = Field(default=None, ge=0, le=Decimal(1_000_000_000),
                                       max_digits=18, decimal_places=2)
    tax_code_id: UUID | None = None


class QuoteIn(Strict):
    unit_id: UUID
    customer_id: UUID
    enquiry_ref: str | None = Field(default=None, max_length=100)
    valid_until: date
    lines: list[QuoteLineIn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _distinct_items(self) -> "QuoteIn":
        items = [ln.item_id for ln in self.lines]
        if len(set(items)) != len(items):
            raise ValueError("Each item may appear on a quotation only once")
        return self


class QuoteLineOut(BaseModel):
    quote_line_id: UUID
    line_no: int
    item_id: UUID
    item_code: str
    item_name: str
    quantity: Decimal
    unit_price: Decimal
    tax_code_id: UUID | None
    gst_rate: Decimal | None
    net_amount: Decimal
    gst_amount: Decimal


class QuoteSummary(BaseModel):
    quote_id: UUID
    quote_no: str
    unit_id: UUID
    customer_id: UUID
    customer_code: str
    enquiry_ref: str | None
    valid_until: date
    status: str
    so_id: UUID | None


class QuoteOut(QuoteSummary):
    lines: list[QuoteLineOut]
    net_total: Decimal
    gst_total: Decimal
    gross_total: Decimal


QUOTE_SQL = (
    "SELECT q.quote_id, q.quote_no, q.unit_id, q.customer_id, c.code AS customer_code, "
    "q.enquiry_ref, q.valid_until, CASE WHEN q.status IN ('DRAFT', 'SENT') AND q.valid_until < "
    "CURRENT_DATE THEN 'EXPIRED' ELSE q.status END AS status, q.so_id FROM erp.quotation q JOIN "
    "erp.customer c ON c.customer_id = q.customer_id"
)


async def load(ctx: Ctx, quote_id: UUID) -> QuoteOut | None:
    clause, params = scope_clause(ctx, "q.unit_id")
    head = await one(ctx, f"{QUOTE_SQL} WHERE q.quote_id = :id{' AND ' + clause if clause else ''}",
                     {**params, "id": quote_id})
    if head is None:
        return None
    raw = await rows(
        ctx, "SELECT l.quote_line_id, l.line_no, l.item_id, i.code AS item_code, i.name AS "
        "item_name, l.quantity, l.unit_price, l.tax_code_id, t.gst_rate FROM "
        "erp.quotation_line l JOIN erp.item i ON i.item_id = l.item_id LEFT JOIN erp.tax_code t "
        "ON t.tax_code_id = l.tax_code_id WHERE l.quote_id = :id ORDER BY l.line_no",
        {"id": quote_id})
    lines, net_total, gst_total = [], Decimal(0), Decimal(0)
    for r in raw:
        net, gst = line_amounts(r["quantity"], r["unit_price"], r["gst_rate"])
        lines.append(QuoteLineOut(**r, net_amount=net, gst_amount=gst))
        net_total += net
        gst_total += gst
    return QuoteOut(**head, lines=lines, net_total=net_total, gst_total=gst_total,
                    gross_total=net_total + gst_total)


async def require_quote(ctx: Ctx, quote_id: UUID, *allowed: str) -> dict[str, Any]:
    clause, params = scope_clause(ctx, "unit_id")
    head = await one(
        ctx, "SELECT quote_id, quote_no, unit_id, customer_id, valid_until, status, so_id FROM "
        f"erp.quotation WHERE quote_id = :id{' AND ' + clause if clause else ''} FOR UPDATE",
        {**params, "id": quote_id})
    if head is None:
        raise not_found("Quotation")
    if head["status"] not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The quotation is {str(head['status']).lower()}; this needs it to be "
            f"{' or '.join(a.lower() for a in allowed)}")
    return head


def refuse_if_expired(head: dict[str, Any]) -> None:
    """Expiry is derived from the date (it shows as EXPIRED), so refusing needs no update."""
    if head["valid_until"] < date.today():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The quotation was valid until {head['valid_until']} and has expired")


@router.post("/quotes", response_model=QuoteOut, status_code=status.HTTP_201_CREATED)
async def create_quote(body: QuoteIn, ctx: Ctx = Depends(quote_permission)) -> QuoteOut:
    if not ctx.principal.may_see_unit(body.unit_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your plant scope")
    if body.valid_until < date.today():
        raise HTTPException(422, "The valid-until date is in the past")
    unit = await one(ctx, "SELECT active FROM erp.unit WHERE unit_id = :id", {"id": body.unit_id})
    customer = await one(ctx, "SELECT active FROM erp.customer WHERE customer_id = :id",
                         {"id": body.customer_id})
    if unit is None or not unit["active"] or customer is None or not customer["active"]:
        raise HTTPException(422, "The plant or customer does not exist or is inactive")
    items = {r["item_id"]: r for r in await rows(
        ctx, "SELECT item_id, code, item_type, active FROM erp.item WHERE item_id = "
        "ANY(CAST(:ids AS uuid[]))", {"ids": [ln.item_id for ln in body.lines]})}
    prices: list[Decimal] = []
    for line in body.lines:
        item = items.get(line.item_id)
        if item is None or not item["active"] or item["item_type"] not in SOLD_TYPES:
            raise HTTPException(422, "Every item must be an active item that can be sold")
        price = line.unit_price
        if price is None:
            price = await price_on(ctx, body.customer_id, line.item_id, date.today())
        if price is None:
            raise HTTPException(
                422, f"No price for {item['code']} on the customer's price list; enter a rate")
        if line.tax_code_id is not None and await one(
                ctx, "SELECT 1 AS x FROM erp.tax_code WHERE tax_code_id = :id",
                {"id": line.tax_code_id}) is None:
            raise HTTPException(422, "A tax code does not exist")
        prices.append(price)
    number = await next_number(ctx, "quote")
    created = await one(
        ctx, "INSERT INTO erp.quotation(tenant_id, unit_id, customer_id, quote_no, enquiry_ref, "
        "valid_until, created_by) VALUES (erp.current_tenant_id(), :u, :c, :n, :e, :v, :me) "
        "RETURNING quote_id",
        {"u": body.unit_id, "c": body.customer_id, "n": number, "e": body.enquiry_ref,
         "v": body.valid_until, "me": ctx.principal.profile.app_user_id})
    assert created is not None
    for number_, (line, price) in enumerate(zip(body.lines, prices, strict=True), start=1):
        await rows(
            ctx, "INSERT INTO erp.quotation_line(tenant_id, quote_id, line_no, item_id, quantity, "
            "unit_price, tax_code_id) VALUES (erp.current_tenant_id(), :q, :n, :i, :qty, :p, :t) "
            "RETURNING quote_line_id",
            {"q": created["quote_id"], "n": number_, "i": line.item_id, "qty": line.quantity,
             "p": price, "t": line.tax_code_id})
    await audit(ctx, "quote.create", "quotation", created["quote_id"],
                {"quote_no": number, "customer_id": body.customer_id})
    result = await load(ctx, created["quote_id"])
    assert result is not None
    return result


@router.get("/quotes", response_model=Page[QuoteSummary])
async def list_quotes(
    customer_id: UUID | None = None, quote_status: str | None = Query(None, alias="status"),
    page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[QuoteSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "q.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for text_, key, value in (("q.customer_id = :c", "c", customer_id),
                              ("(CASE WHEN q.status IN ('DRAFT', 'SENT') AND q.valid_until < "
                               "CURRENT_DATE THEN 'EXPIRED' ELSE q.status END) = :st", "st",
                               quote_status.upper() if quote_status else None)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{QUOTE_SQL}{where} ORDER BY q.quote_no DESC LIMIT :limit "
                       "OFFSET :offset", {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.quotation q{where}", params)
    return Page(items=[QuoteSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/quotes/{quote_id}", response_model=QuoteOut)
async def get_quote(quote_id: UUID, ctx: Ctx = Depends(read)) -> QuoteOut:
    found = await load(ctx, quote_id)
    if found is None:
        raise not_found("Quotation")
    return found


async def move(ctx: Ctx, quote_id: UUID, new: str, *allowed: str) -> QuoteOut:
    head = await require_quote(ctx, quote_id, *allowed)
    if new in ("SENT", "ACCEPTED"):
        refuse_if_expired(head)
    await rows(ctx, "UPDATE erp.quotation SET status = :s WHERE quote_id = :id RETURNING quote_id",
               {"s": new, "id": quote_id})
    await audit(ctx, f"quote.{new.lower()}", "quotation", quote_id, {"quote_no": head["quote_no"]})
    result = await load(ctx, quote_id)
    assert result is not None
    return result


@router.post("/quotes/{quote_id}/send", response_model=QuoteOut)
async def send_quote(quote_id: UUID, ctx: Ctx = Depends(quote_permission)) -> QuoteOut:
    return await move(ctx, quote_id, "SENT", "DRAFT")


@router.post("/quotes/{quote_id}/accept", response_model=QuoteOut)
async def accept_quote(quote_id: UUID, ctx: Ctx = Depends(quote_permission)) -> QuoteOut:
    return await move(ctx, quote_id, "ACCEPTED", "SENT")


@router.post("/quotes/{quote_id}/reject", response_model=QuoteOut)
async def reject_quote(quote_id: UUID, ctx: Ctx = Depends(quote_permission)) -> QuoteOut:
    return await move(ctx, quote_id, "REJECTED", "SENT")


class ConvertIn(Strict):
    customer_po_no: str | None = Field(default=None, min_length=1, max_length=60)


@router.post("/quotes/{quote_id}/convert", response_model=orders.SoOut,
             status_code=status.HTTP_201_CREATED)
async def convert_quote(
    quote_id: UUID, body: ConvertIn, ctx: Ctx = Depends(order_permission)
) -> orders.SoOut:
    """Turn an accepted quotation into a sales order at the quoted rates (once)."""
    head = await require_quote(ctx, quote_id, "ACCEPTED")
    lines = await rows(
        ctx, "SELECT item_id, quantity, unit_price, tax_code_id FROM erp.quotation_line WHERE "
        "quote_id = :id ORDER BY line_no", {"id": quote_id})
    so_body = SoIn(
        unit_id=head["unit_id"], customer_id=head["customer_id"],
        customer_po_no=body.customer_po_no,
        lines=[SoLineIn(item_id=ln["item_id"], ordered_qty=ln["quantity"],
                        unit_price=ln["unit_price"], tax_code_id=ln["tax_code_id"])
               for ln in lines])
    so_id, warnings = await orders.create_order(ctx, so_body, quote_id=quote_id)
    await rows(ctx, "UPDATE erp.quotation SET status = 'CONVERTED', so_id = :so WHERE quote_id = "
               ":id RETURNING quote_id", {"so": so_id, "id": quote_id})
    await audit(ctx, "quote.convert", "quotation", quote_id, {"so_id": so_id})
    result = await orders.load(ctx, so_id)
    assert result is not None
    result.warnings = warnings
    return result
