"""Sales orders with delivery schedules and the credit check (BRD FR-SAL-02, FR-SAL-09).

An order has lines (rate from the customer's price list when none is typed) and a delivery schedule
per line: due dates with quantities that never add up to more than was ordered. The customer's
credit position is the open order value plus invoiced value (payments arrive with Phase 10, so
every invoice counts as unpaid until then). Going over the limit only warns, unless the business
rule `creditLimitBlock` is on, which refuses the order. An order is cancelled while nothing has
shipped, and closed when the customer will not take the rest. Needs sales.order to change; reading
needs the sales module.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from heyyerp_api.business_rules import service as rules
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
from heyyerp_api.sales.pricing import price_on

router = APIRouter(prefix="/sales", tags=["sales"])
manage = requires("sales.order")
read = requires_module("sales")
PAISA = Decimal("0.01")
SOLD_TYPES = ("FG", "WIP", "SPARE", "OTHER")
Qty = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)


def money(value: Decimal) -> Decimal:
    return value.quantize(PAISA, rounding=ROUND_HALF_UP)


def line_amounts(qty: Decimal, price: Decimal, rate: Decimal | None) -> tuple[Decimal, Decimal]:
    net = money(qty * price)
    return net, money(net * (rate or Decimal(0)) / Decimal(100))


class ScheduleIn(Strict):
    due_date: date
    quantity: Decimal = Qty


class SoLineIn(Strict):
    item_id: UUID
    ordered_qty: Decimal = Qty
    unit_price: Decimal | None = Field(default=None, ge=0, le=Decimal(1_000_000_000),
                                       max_digits=18, decimal_places=2)
    tax_code_id: UUID | None = None
    schedule: list[ScheduleIn] = Field(default_factory=list, max_length=60)


class SoIn(Strict):
    unit_id: UUID
    customer_id: UUID
    customer_po_no: str | None = Field(default=None, min_length=1, max_length=60)
    order_date: date | None = None
    lines: list[SoLineIn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _distinct_items(self) -> "SoIn":
        items = [ln.item_id for ln in self.lines]
        if len(set(items)) != len(items):
            raise ValueError("Each item may appear on an order only once")
        return self


class ScheduleOut(BaseModel):
    so_schedule_id: UUID
    due_date: date
    scheduled_qty: Decimal
    fulfilled_qty: Decimal


class SoLineOut(BaseModel):
    so_line_id: UUID
    line_no: int
    item_id: UUID
    item_code: str
    item_name: str
    customer_part_no: str | None
    ordered_qty: Decimal
    shipped_qty: Decimal
    unit_price: Decimal
    tax_code_id: UUID | None
    gst_rate: Decimal | None
    net_amount: Decimal
    gst_amount: Decimal
    schedule: list[ScheduleOut]


class SoSummary(BaseModel):
    so_id: UUID
    so_no: str
    unit_id: UUID
    customer_id: UUID
    customer_code: str
    customer_name: str
    customer_po_no: str | None
    order_date: date
    status: str
    quote_id: UUID | None


class SoOut(SoSummary):
    lines: list[SoLineOut]
    net_total: Decimal
    gst_total: Decimal
    gross_total: Decimal
    warnings: list[str] = []


SO_SQL = (
    "SELECT s.so_id, s.so_no, s.unit_id, s.customer_id, c.code AS customer_code, c.name AS "
    "customer_name, s.customer_po_no, s.order_date, s.status, s.quote_id FROM erp.sales_order s "
    "JOIN erp.customer c ON c.customer_id = s.customer_id"
)


async def load(ctx: Ctx, so_id: UUID) -> SoOut | None:
    clause, params = scope_clause(ctx, "s.unit_id")
    head = await one(ctx, f"{SO_SQL} WHERE s.so_id = :id{' AND ' + clause if clause else ''}",
                     {**params, "id": so_id})
    if head is None:
        return None
    raw = await rows(
        ctx, "SELECT l.so_line_id, l.line_no, l.item_id, i.code AS item_code, i.name AS "
        "item_name, x.customer_part_no, l.ordered_qty, l.shipped_qty, l.unit_price, "
        "l.tax_code_id, t.gst_rate FROM erp.so_line l JOIN erp.item i ON i.item_id = l.item_id "
        "LEFT JOIN erp.tax_code t ON t.tax_code_id = l.tax_code_id LEFT JOIN "
        "erp.customer_part_xref x ON x.customer_id = :c AND x.item_id = l.item_id WHERE "
        "l.so_id = :id ORDER BY l.line_no", {"id": so_id, "c": head["customer_id"]})
    lines: list[SoLineOut] = []
    net_total = gst_total = Decimal(0)
    for r in raw:
        net, gst = line_amounts(r["ordered_qty"], r["unit_price"], r["gst_rate"])
        schedule = await rows(
            ctx, "SELECT so_schedule_id, due_date, scheduled_qty, fulfilled_qty FROM "
            "erp.so_schedule WHERE so_line_id = :l ORDER BY due_date", {"l": r["so_line_id"]})
        lines.append(SoLineOut(**r, net_amount=net, gst_amount=gst,
                               schedule=[ScheduleOut(**s) for s in schedule]))
        net_total += net
        gst_total += gst
    return SoOut(**head, lines=lines, net_total=net_total, gst_total=gst_total,
                 gross_total=net_total + gst_total)


async def credit_position(
    ctx: Ctx, customer_id: UUID, extra: Decimal = Decimal(0), exclude_so: UUID | None = None
) -> dict[str, Any]:
    """Open order value (unshipped part) plus invoiced value, against the customer's limit."""
    customer = await one(ctx, "SELECT code, name, credit_limit FROM erp.customer WHERE "
                         "customer_id = :id", {"id": customer_id})
    if customer is None:
        raise not_found("Customer")
    open_orders = await one(
        ctx, "SELECT COALESCE(sum(round((l.ordered_qty - l.shipped_qty) * l.unit_price * "
        "(1 + COALESCE(t.gst_rate, 0) / 100), 2)), 0) AS v FROM erp.so_line l JOIN "
        "erp.sales_order s ON s.so_id = l.so_id LEFT JOIN erp.tax_code t ON t.tax_code_id = "
        "l.tax_code_id WHERE s.customer_id = :c AND s.status IN ('OPEN', 'PARTIALLY_SHIPPED') "
        "AND (CAST(:ex AS uuid) IS NULL OR s.so_id <> :ex)", {"c": customer_id, "ex": exclude_so})
    invoiced = await one(
        ctx, "SELECT COALESCE(sum(taxable_amount + tax_amount), 0) AS v FROM "
        "erp.customer_invoice WHERE customer_id = :c AND status <> 'CANCELLED'",
        {"c": customer_id})
    assert open_orders is not None and invoiced is not None
    exposure = open_orders["v"] + invoiced["v"] + extra
    limit = customer["credit_limit"]
    return {"customer_code": customer["code"], "credit_limit": limit,
            "open_orders": open_orders["v"], "invoiced": invoiced["v"], "exposure": exposure,
            "over_limit": limit is not None and exposure > limit,
            "available": None if limit is None else limit - exposure}


async def credit_warnings(
    ctx: Ctx, customer_id: UUID, extra: Decimal, exclude_so: UUID | None = None
) -> list[str]:
    """A warning when the customer would be over the limit; an error when the rule says block."""
    position = await credit_position(ctx, customer_id, extra, exclude_so)
    if not position["over_limit"]:
        return []
    text = (f"Customer {position['customer_code']} would be over the credit limit: "
            f"{position['exposure']} against {position['credit_limit']}")
    if await rules.get_value(ctx, "creditLimitBlock"):
        raise HTTPException(status.HTTP_409_CONFLICT, text)
    return [text]


async def refresh_status(ctx: Ctx, so_id: UUID) -> None:
    """OPEN, PARTIALLY_SHIPPED or SHIPPED from what dispatch has sent; closed orders stay put."""
    totals = await one(ctx, "SELECT COALESCE(sum(shipped_qty), 0) AS s, bool_and(shipped_qty >= "
                       "ordered_qty) AS full FROM erp.so_line WHERE so_id = :id", {"id": so_id})
    assert totals is not None
    new = "SHIPPED" if totals["full"] else "PARTIALLY_SHIPPED" if totals["s"] > 0 else "OPEN"
    await rows(ctx, "UPDATE erp.sales_order SET status = :s WHERE so_id = :id AND status IN "
               "('OPEN', 'PARTIALLY_SHIPPED', 'SHIPPED') RETURNING so_id", {"s": new, "id": so_id})


async def check_schedule(
    order_date: date, ordered: Decimal, schedule: list[ScheduleIn]
) -> None:
    dates = [s.due_date for s in schedule]
    if len(set(dates)) != len(dates):
        raise HTTPException(422, "A delivery date may appear only once per line")
    if any(d < order_date for d in dates):
        raise HTTPException(422, "A delivery date cannot be before the order date")
    if sum((s.quantity for s in schedule), Decimal(0)) > ordered:
        raise HTTPException(422, "The scheduled quantity exceeds the ordered quantity")


async def create_order(
    ctx: Ctx, body: SoIn, quote_id: UUID | None = None,
    quote_prices: dict[UUID, Decimal] | None = None,
) -> tuple[UUID, list[str]]:
    if not ctx.principal.may_see_unit(body.unit_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your plant scope")
    unit = await one(ctx, "SELECT active FROM erp.unit WHERE unit_id = :id", {"id": body.unit_id})
    if unit is None or not unit["active"]:
        raise HTTPException(422, "The plant does not exist or is inactive")
    customer = await one(ctx, "SELECT active FROM erp.customer WHERE customer_id = :id",
                         {"id": body.customer_id})
    if customer is None or not customer["active"]:
        raise HTTPException(422, "The customer does not exist or is inactive")
    day = body.order_date or date.today()
    if body.customer_po_no and await one(
            ctx, "SELECT 1 AS x FROM erp.sales_order WHERE customer_id = :c AND customer_po_no = "
            ":p AND status <> 'CANCELLED'", {"c": body.customer_id, "p": body.customer_po_no}):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This customer already has an order with that PO number")
    items = {r["item_id"]: r for r in await rows(
        ctx, "SELECT item_id, code, item_type, active FROM erp.item WHERE item_id = "
        "ANY(CAST(:ids AS uuid[]))", {"ids": [ln.item_id for ln in body.lines]})}
    planned: list[tuple[SoLineIn, Decimal, Decimal | None]] = []
    gross = Decimal(0)
    for line in body.lines:
        item = items.get(line.item_id)
        if item is None or not item["active"] or item["item_type"] not in SOLD_TYPES:
            raise HTTPException(422, "Every item must be an active item that can be sold")
        price = line.unit_price
        if price is None and quote_prices is not None:
            price = quote_prices.get(line.item_id)
        if price is None:
            price = await price_on(ctx, body.customer_id, line.item_id, day)
        if price is None:
            raise HTTPException(
                422, f"No price for {item['code']} on the customer's price list; enter a rate")
        rate = None
        if line.tax_code_id is not None:
            tax = await one(ctx, "SELECT gst_rate FROM erp.tax_code WHERE tax_code_id = :id",
                            {"id": line.tax_code_id})
            if tax is None:
                raise HTTPException(422, "A tax code does not exist")
            rate = tax["gst_rate"]
        await check_schedule(day, line.ordered_qty, line.schedule)
        net, gst = line_amounts(line.ordered_qty, price, rate)
        gross += net + gst
        planned.append((line, price, rate))
    warnings = await credit_warnings(ctx, body.customer_id, gross)
    number = await next_number(ctx, "sales_order")
    created = await one(
        ctx, "INSERT INTO erp.sales_order(tenant_id, unit_id, customer_id, so_no, customer_po_no, "
        "order_date, quote_id, created_by) VALUES (erp.current_tenant_id(), :u, :c, :n, :p, :d, "
        ":q, :me) RETURNING so_id",
        {"u": body.unit_id, "c": body.customer_id, "n": number, "p": body.customer_po_no,
         "d": day, "q": quote_id, "me": ctx.principal.profile.app_user_id})
    assert created is not None
    for number_, (line, price, _rate) in enumerate(planned, start=1):
        made = await one(
            ctx, "INSERT INTO erp.so_line(tenant_id, so_id, line_no, item_id, ordered_qty, "
            "unit_price, tax_code_id) VALUES (erp.current_tenant_id(), :so, :n, :i, :q, :p, :t) "
            "RETURNING so_line_id",
            {"so": created["so_id"], "n": number_, "i": line.item_id, "q": line.ordered_qty,
             "p": price, "t": line.tax_code_id})
        assert made is not None
        for sched in line.schedule:
            await rows(
                ctx, "INSERT INTO erp.so_schedule(tenant_id, so_line_id, due_date, "
                "scheduled_qty) VALUES (erp.current_tenant_id(), :l, :d, :q) "
                "RETURNING so_schedule_id",
                {"l": made["so_line_id"], "d": sched.due_date, "q": sched.quantity})
    await audit(ctx, "so.create", "sales_order", created["so_id"],
                {"so_no": number, "customer_id": body.customer_id, "gross_total": gross,
                 "warnings": warnings})
    return created["so_id"], warnings


@router.post("/orders", response_model=SoOut, status_code=status.HTTP_201_CREATED)
async def post_order(body: SoIn, ctx: Ctx = Depends(manage)) -> SoOut:
    so_id, warnings = await create_order(ctx, body)
    result = await load(ctx, so_id)
    assert result is not None
    result.warnings = warnings
    return result


@router.get("/orders", response_model=Page[SoSummary])
async def list_orders(
    customer_id: UUID | None = None, so_status: str | None = Query(None, alias="status"),
    customer_po_no: str | None = None, page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[SoSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "s.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for text_, key, value in (("s.customer_id = :c", "c", customer_id),
                              ("s.status = :st", "st", so_status.upper() if so_status else None),
                              ("s.customer_po_no = :po", "po", customer_po_no)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{SO_SQL}{where} ORDER BY s.so_no DESC LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.sales_order s{where}", params)
    return Page(items=[SoSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/orders/{so_id}", response_model=SoOut)
async def get_order(so_id: UUID, ctx: Ctx = Depends(read)) -> SoOut:
    found = await load(ctx, so_id)
    if found is None:
        raise not_found("Sales order")
    return found


async def require_order(ctx: Ctx, so_id: UUID, *allowed: str) -> dict[str, Any]:
    clause, params = scope_clause(ctx, "unit_id")
    head = await one(
        ctx, "SELECT so_id, so_no, unit_id, customer_id, order_date, status FROM "
        f"erp.sales_order WHERE so_id = :id{' AND ' + clause if clause else ''} FOR UPDATE",
        {**params, "id": so_id})
    if head is None:
        raise not_found("Sales order")
    if head["status"] not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The order is {str(head['status']).lower().replace('_', ' ')}; this needs it to be "
            f"{' or '.join(a.lower().replace('_', ' ') for a in allowed)}")
    return head


@router.post("/orders/{so_id}/cancel", response_model=SoOut)
async def cancel_order(so_id: UUID, ctx: Ctx = Depends(manage)) -> SoOut:
    await require_order(ctx, so_id, "OPEN")
    await rows(ctx, "UPDATE erp.sales_order SET status = 'CANCELLED' WHERE so_id = :id "
               "RETURNING so_id", {"id": so_id})
    await audit(ctx, "so.cancel", "sales_order", so_id, {})
    result = await load(ctx, so_id)
    assert result is not None
    return result


@router.post("/orders/{so_id}/close", response_model=SoOut)
async def close_order(so_id: UUID, ctx: Ctx = Depends(manage)) -> SoOut:
    """The customer will not take the rest: no further dispatch against this order."""
    await require_order(ctx, so_id, "PARTIALLY_SHIPPED", "SHIPPED")
    await rows(ctx, "UPDATE erp.sales_order SET status = 'CLOSED' WHERE so_id = :id "
               "RETURNING so_id", {"id": so_id})
    await audit(ctx, "so.close", "sales_order", so_id, {})
    result = await load(ctx, so_id)
    assert result is not None
    return result


class ScheduleBody(Strict):
    schedule: list[ScheduleIn] = Field(max_length=60)


@router.put("/orders/{so_id}/lines/{so_line_id}/schedule", response_model=SoOut)
async def set_schedule(
    so_id: UUID, so_line_id: UUID, body: ScheduleBody, ctx: Ctx = Depends(manage)
) -> SoOut:
    """Replace a line's delivery schedule; dates that already shipped keep at least that much."""
    order = await require_order(ctx, so_id, "OPEN", "PARTIALLY_SHIPPED")
    line = await one(ctx, "SELECT ordered_qty FROM erp.so_line WHERE so_line_id = :l AND "
                     "so_id = :s FOR UPDATE", {"l": so_line_id, "s": so_id})
    if line is None:
        raise not_found("Order line")
    await check_schedule(order["order_date"], line["ordered_qty"], body.schedule)
    existing = {r["due_date"]: r for r in await rows(
        ctx, "SELECT so_schedule_id, due_date, fulfilled_qty FROM erp.so_schedule WHERE "
        "so_line_id = :l", {"l": so_line_id})}
    given = {s.due_date: s for s in body.schedule}
    for due, row in existing.items():
        if due not in given and row["fulfilled_qty"] > 0:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"The {due} delivery has already shipped and cannot be removed")
        if due in given and given[due].quantity < row["fulfilled_qty"]:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"{row['fulfilled_qty']} already shipped for {due}")
    for due, row in existing.items():
        if due not in given:
            await rows(ctx, "DELETE FROM erp.so_schedule WHERE so_schedule_id = :id "
                       "RETURNING so_schedule_id", {"id": row["so_schedule_id"]})
    for sched in body.schedule:
        if sched.due_date in existing:
            await rows(ctx, "UPDATE erp.so_schedule SET scheduled_qty = :q WHERE so_schedule_id "
                       "= :id RETURNING so_schedule_id",
                       {"q": sched.quantity, "id": existing[sched.due_date]["so_schedule_id"]})
        else:
            await rows(
                ctx, "INSERT INTO erp.so_schedule(tenant_id, so_line_id, due_date, "
                "scheduled_qty) VALUES (erp.current_tenant_id(), :l, :d, :q) "
                "RETURNING so_schedule_id",
                {"l": so_line_id, "d": sched.due_date, "q": sched.quantity})
    await audit(ctx, "so.schedule", "sales_order", so_id,
                {"so_line_id": so_line_id, "dates": len(body.schedule)})
    result = await load(ctx, so_id)
    assert result is not None
    return result


class CreditOut(BaseModel):
    customer_code: str
    credit_limit: Decimal | None
    open_orders: Decimal
    invoiced: Decimal
    exposure: Decimal
    available: Decimal | None
    over_limit: bool
    blocks: bool


@router.get("/customers/{customer_id}/credit", response_model=CreditOut)
async def customer_credit(customer_id: UUID, ctx: Ctx = Depends(read)) -> CreditOut:
    position = await credit_position(ctx, customer_id)
    return CreditOut(**position, blocks=bool(await rules.get_value(ctx, "creditLimitBlock")))

