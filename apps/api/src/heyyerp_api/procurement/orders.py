"""Purchase orders (BRD FR-PRC-02, FR-PRC-03, FR-PRC-04, FR-PRC-06, RULE-01, RULE-03, RULE-05).

DRAFT -> PENDING_APPROVAL -> OPEN -> PARTIALLY_RECEIVED -> RECEIVED (or CLOSED), with REJECTED
and CANCELLED. A PO is raised directly, from approved indents, or by awarding an RFQ. Only an
APPROVED vendor can receive one. The approval band uses the order value including GST, so every
line needs a tax code before submission. Business rules read from settings:
  requireIndent  the PO must come from an approved indent
  minQuotes      the PO must come from an RFQ that received at least that many quotes
Needs po.create to write and the procurement module to read.
"""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Self
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
from heyyerp_api.workflow import engine
from heyyerp_api.workflow.registry import DocInfo, DocumentType, register

router = APIRouter(prefix="/procurement", tags=["procurement"])
write = requires("po.create")
read = requires_module("procurement")
PAISA = Decimal("0.01")
LIVE_PO = ("DRAFT", "PENDING_APPROVAL", "OPEN", "PARTIALLY_RECEIVED", "RECEIVED")


class ScheduleIn(Strict):
    due_date: date
    quantity: Decimal = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)


class LineIn(Strict):
    item_id: UUID
    ordered_qty: Decimal = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)
    unit_price: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    tax_code_id: UUID | None = None
    due_date: date | None = None  # a single delivery of the whole quantity ...
    schedule: list[ScheduleIn] = Field(default_factory=list, max_length=24)  # ... or several

    @model_validator(mode="after")
    def _delivery(self) -> Self:
        if bool(self.due_date) == bool(self.schedule):
            raise ValueError("Give either due_date or a delivery schedule")
        if self.schedule and sum(s.quantity for s in self.schedule) != self.ordered_qty:
            raise ValueError("The delivery schedule must add up to the ordered quantity")
        return self


class PoIn(Strict):
    unit_id: UUID
    vendor_id: UUID
    payment_term_id: UUID | None = None
    rfq_id: UUID | None = None
    indent_ids: list[UUID] = Field(default_factory=list, max_length=20)
    lines: list[LineIn] = Field(min_length=1, max_length=200)


class PoUpdate(Strict):
    payment_term_id: UUID | None = None
    lines: list[LineIn] = Field(min_length=1, max_length=200)


class PriceIn(Strict):
    item_id: UUID
    unit_price: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    tax_code_id: UUID | None = None


class FromIndentsIn(Strict):
    indent_ids: list[UUID] = Field(min_length=1, max_length=20)
    vendor_id: UUID
    payment_term_id: UUID | None = None
    due_date: date | None = None
    prices: list[PriceIn] = Field(min_length=1, max_length=200)


class AwardIn(Strict):
    vendor_id: UUID
    due_date: date
    tax_code_id: UUID | None = None
    payment_term_id: UUID | None = None


class ScheduleOut(BaseModel):
    due_date: date
    scheduled_qty: Decimal
    received_qty: Decimal


class LineOut(BaseModel):
    po_line_id: UUID
    line_no: int
    item_id: UUID
    item_code: str
    item_name: str
    uom_code: str
    ordered_qty: Decimal
    unit_price: Decimal
    tax_code_id: UUID | None
    tax_code: str | None
    gst_rate: Decimal | None
    net: Decimal
    gst: Decimal
    gross: Decimal
    received_qty: Decimal
    schedule: list[ScheduleOut]


class PoSummary(BaseModel):
    po_id: UUID
    po_no: str
    unit_id: UUID
    unit_code: str
    vendor_id: UUID
    vendor_code: str
    vendor_name: str
    status: str
    currency_code: str
    payment_term_id: UUID | None
    rfq_id: UUID | None
    rfq_no: str | None
    created_by_name: str
    created_at: datetime
    approval_id: UUID | None


class PoOut(PoSummary):
    net_total: Decimal
    gst_total: Decimal
    gross_total: Decimal
    indents: list[dict[str, Any]]
    lines: list[LineOut]


class ReceivingLine(BaseModel):
    po_line_id: UUID
    line_no: int
    item_code: str
    ordered_qty: Decimal
    received_qty: Decimal
    pending_qty: Decimal
    max_receivable_qty: Decimal


class LateLine(BaseModel):
    po_id: UUID
    po_no: str
    vendor_code: str
    vendor_name: str
    item_code: str
    due_date: date
    days_late: int
    pending_qty: Decimal


class HistoryEvent(BaseModel):
    occurred_at: datetime
    action: str
    actor: str | None
    actor_role_code: str | None
    details: dict[str, Any]


class History(BaseModel):
    events: list[HistoryEvent]
    approval: dict[str, Any] | None


PO_SQL = (
    "SELECT p.po_id, p.po_no, p.unit_id, u.code AS unit_code, p.vendor_id, v.code AS vendor_code, "
    "v.name AS vendor_name, p.status, p.currency_code, p.payment_term_id, p.rfq_id, r.rfq_no, "
    "cu.display_name AS created_by_name, p.created_at, a.approval_id FROM erp.purchase_order p "
    "JOIN erp.unit u ON u.unit_id = p.unit_id JOIN erp.vendor v ON v.vendor_id = p.vendor_id "
    "JOIN erp.app_user cu ON cu.user_id = p.created_by LEFT JOIN erp.rfq r ON r.rfq_id = p.rfq_id "
    "LEFT JOIN erp.approval_instance a ON a.document_type = 'po' AND a.document_id = p.po_id"
)


def money(value: Decimal) -> Decimal:
    return value.quantize(PAISA, rounding=ROUND_HALF_UP)


def line_amounts(qty: Decimal, price: Decimal, rate: Decimal | None) -> tuple[Decimal, Decimal]:
    """Net and GST for one line, each rounded to the paisa; no tax code means no GST yet."""
    net = money(qty * price)
    return net, money(net * (rate or Decimal(0)) / Decimal(100))


async def load(ctx: Ctx, po_id: UUID) -> PoOut | None:
    clause, params = scope_clause(ctx, "p.unit_id")
    head = await one(ctx, f"{PO_SQL} WHERE p.po_id = :id{' AND ' + clause if clause else ''}",
                     {**params, "id": po_id})
    if head is None:
        return None
    raw = await rows(
        ctx,
        "SELECT l.po_line_id, l.line_no, l.item_id, i.code AS item_code, i.name AS item_name, "
        "um.code AS uom_code, l.ordered_qty, l.unit_price, l.tax_code_id, t.code AS tax_code, "
        "t.gst_rate FROM erp.po_line l JOIN erp.item i ON i.item_id = l.item_id "
        "JOIN erp.uom um ON um.uom_id = i.uom_id LEFT JOIN erp.tax_code t ON "
        "t.tax_code_id = l.tax_code_id WHERE l.po_id = :id ORDER BY l.line_no", {"id": po_id},
    )
    schedules = await rows(
        ctx, "SELECT s.po_line_id, s.due_date, s.scheduled_qty, s.received_qty FROM "
        "erp.po_schedule s JOIN erp.po_line l ON l.po_line_id = s.po_line_id WHERE l.po_id = :id "
        "ORDER BY s.due_date, s.po_schedule_id", {"id": po_id})
    lines: list[LineOut] = []
    totals = [Decimal(0), Decimal(0)]
    for line in raw:
        net, gst = line_amounts(line["ordered_qty"], line["unit_price"], line["gst_rate"])
        totals[0] += net
        totals[1] += gst
        mine = [s for s in schedules if s["po_line_id"] == line["po_line_id"]]
        lines.append(LineOut(
            **line, net=net, gst=gst, gross=net + gst,
            received_qty=sum((s["received_qty"] for s in mine), Decimal(0)),
            schedule=[ScheduleOut(**{k: s[k] for k in ("due_date", "scheduled_qty",
                                                       "received_qty")}) for s in mine]))
    indents = await rows(
        ctx, "SELECT i.indent_id, i.indent_no FROM erp.po_indent_link l JOIN erp.indent i "
        "ON i.indent_id = l.indent_id WHERE l.po_id = :id ORDER BY i.indent_no", {"id": po_id})
    return PoOut(**head, net_total=totals[0], gst_total=totals[1],
                 gross_total=totals[0] + totals[1], indents=indents, lines=lines)


async def require_po(ctx: Ctx, po_id: UUID, *states: str) -> dict[str, Any]:
    clause, params = scope_clause(ctx, "unit_id")
    scope = f" AND {clause}" if clause else ""
    po = await one(
        ctx, "SELECT po_id, unit_id, vendor_id, status, rfq_id FROM erp.purchase_order "
        f"WHERE po_id = :id{scope} FOR UPDATE", {**params, "id": po_id})
    if po is None:
        raise not_found("Purchase order")
    if states and po["status"] not in states:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The order is {str(po['status']).lower().replace('_', ' ')}")
    return po


async def check_header(
    ctx: Ctx, unit_id: UUID, vendor_id: UUID, payment_term_id: UUID | None
) -> dict[str, Any]:
    require_in_scope(ctx, unit_id)
    plant = await one(ctx, "SELECT active FROM erp.unit WHERE unit_id = :id", {"id": unit_id})
    if plant is None or not plant["active"]:
        raise HTTPException(422, "Plant does not exist or is inactive")
    vendor = await one(ctx, "SELECT code, status, payment_term_id FROM erp.vendor "
                       "WHERE vendor_id = :id", {"id": vendor_id})
    if vendor is None:
        raise HTTPException(422, "Vendor does not exist")
    if vendor["status"] != "APPROVED":
        raise HTTPException(
            422, f"Vendor {vendor['code']} is {str(vendor['status']).lower().replace('_', ' ')} "
            "and cannot receive purchase orders")
    if payment_term_id is not None and await one(
        ctx, "SELECT 1 AS x FROM erp.payment_term WHERE payment_term_id = :id",
        {"id": payment_term_id},
    ) is None:
        raise HTTPException(422, "Payment term does not exist")
    return vendor


async def check_lines(ctx: Ctx, lines: list[LineIn]) -> None:
    ids = [ln.item_id for ln in lines]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Each item may appear only once on an order")
    items = {r["item_id"]: r for r in await rows(
        ctx, "SELECT item_id, code, active FROM erp.item WHERE item_id = ANY(CAST(:ids AS uuid[]))",
        {"ids": ids})}
    tax_ids = [ln.tax_code_id for ln in lines if ln.tax_code_id]
    taxes = {r["tax_code_id"]: r for r in await rows(
        ctx, "SELECT tax_code_id, active FROM erp.tax_code WHERE tax_code_id = "
        "ANY(CAST(:ids AS uuid[]))", {"ids": tax_ids})} if tax_ids else {}
    for line in lines:
        item = items.get(line.item_id)
        if item is None:
            raise HTTPException(422, "An item does not exist")
        if not item["active"]:
            raise HTTPException(422, f"Item {item['code']} is inactive")
        tax = taxes.get(line.tax_code_id) if line.tax_code_id else None
        if line.tax_code_id and (tax is None or not tax["active"]):
            raise HTTPException(422, "A tax code does not exist or is inactive")
        due = [s.due_date for s in line.schedule] or ([line.due_date] if line.due_date else [])
        if any(d < date.today() for d in due):
            raise HTTPException(422, "A delivery date cannot be in the past")


async def write_lines(ctx: Ctx, po_id: UUID, lines: list[LineIn]) -> None:
    await rows(ctx, "DELETE FROM erp.po_schedule WHERE po_line_id IN (SELECT po_line_id FROM "
               "erp.po_line WHERE po_id = :id) RETURNING po_schedule_id", {"id": po_id})
    await rows(ctx, "DELETE FROM erp.po_line WHERE po_id = :id RETURNING po_line_id",
               {"id": po_id})
    for number, line in enumerate(lines, start=1):
        created = await one(
            ctx,
            "INSERT INTO erp.po_line(tenant_id, po_id, line_no, item_id, ordered_qty, unit_price, "
            "tax_code_id) VALUES (erp.current_tenant_id(), :po, :no, :item, :qty, :price, :tax) "
            "RETURNING po_line_id",
            {"po": po_id, "no": number, "item": line.item_id, "qty": line.ordered_qty,
             "price": line.unit_price, "tax": line.tax_code_id},
        )
        assert created is not None
        deliveries = line.schedule or [ScheduleIn(due_date=line.due_date or date.today(),
                                                  quantity=line.ordered_qty)]
        for delivery in deliveries:
            await one(
                ctx,
                "INSERT INTO erp.po_schedule(tenant_id, po_line_id, due_date, scheduled_qty) "
                "VALUES (erp.current_tenant_id(), :line, :due, :qty) RETURNING po_schedule_id",
                {"line": created["po_line_id"], "due": delivery.due_date,
                 "qty": delivery.quantity},
            )


async def link_indents(ctx: Ctx, po_id: UUID, unit_id: UUID, indent_ids: list[UUID]) -> None:
    for indent_id in dict.fromkeys(indent_ids):
        indent = await one(ctx, "SELECT indent_no, unit_id, status FROM erp.indent "
                           "WHERE indent_id = :id FOR UPDATE", {"id": indent_id})
        if indent is None:
            raise HTTPException(422, "An indent does not exist")
        if indent["unit_id"] != unit_id:
            raise HTTPException(422, f"Indent {indent['indent_no']} belongs to another plant")
        if indent["status"] != "APPROVED":
            raise HTTPException(
                422, f"Indent {indent['indent_no']} is "
                f"{str(indent['status']).lower().replace('_', ' ')}; only approved indents can "
                "be ordered")
        await rows(ctx, "INSERT INTO erp.po_indent_link(tenant_id, po_id, indent_id) VALUES "
                   "(erp.current_tenant_id(), :po, :ind) RETURNING po_id",
                   {"po": po_id, "ind": indent_id})
        await rows(ctx, "UPDATE erp.indent SET status = 'ORDERED' WHERE indent_id = :id "
                   "RETURNING indent_id", {"id": indent_id})


async def release_indents(ctx: Ctx, po_id: UUID) -> None:
    """A rejected or cancelled order frees its indents so they can be ordered again."""
    await rows(
        ctx,
        "UPDATE erp.indent SET status = 'APPROVED' WHERE status = 'ORDERED' AND indent_id IN "
        "(SELECT l.indent_id FROM erp.po_indent_link l WHERE l.po_id = :id AND NOT EXISTS "
        "(SELECT 1 FROM erp.po_indent_link o JOIN erp.purchase_order p ON p.po_id = o.po_id "
        "WHERE o.indent_id = l.indent_id AND o.po_id <> :id AND p.status = ANY(CAST(:live AS "
        "text[])))) RETURNING indent_id", {"id": po_id, "live": list(LIVE_PO)},
    )


async def check_rfq(ctx: Ctx, rfq_id: UUID, vendor_id: UUID, unit_id: UUID) -> None:
    rfq = await one(ctx, "SELECT unit_id, status FROM erp.rfq WHERE rfq_id = :id", {"id": rfq_id})
    if rfq is None:
        raise HTTPException(422, "RFQ does not exist")
    if rfq["unit_id"] != unit_id:
        raise HTTPException(422, "The RFQ belongs to another plant")
    if rfq["status"] not in ("SENT", "AWARDED"):
        raise HTTPException(422, f"The RFQ is {str(rfq['status']).lower()}")
    if await one(ctx, "SELECT 1 AS x FROM erp.rfq_vendor_invitation WHERE rfq_id = :r AND "
                 "vendor_id = :v AND response_status = 'QUOTED'",
                 {"r": rfq_id, "v": vendor_id}) is None:
        raise HTTPException(422, "That vendor has not quoted on this RFQ")


async def create(ctx: Ctx, body: PoIn) -> UUID:
    vendor = await check_header(ctx, body.unit_id, body.vendor_id, body.payment_term_id)
    await check_lines(ctx, body.lines)
    if body.rfq_id is not None:
        await check_rfq(ctx, body.rfq_id, body.vendor_id, body.unit_id)
    number = await next_number(ctx, "po")
    with db_errors("Purchase order"):
        created = await one(
            ctx,
            "INSERT INTO erp.purchase_order(tenant_id, unit_id, vendor_id, po_no, payment_term_id, "
            "created_by, rfq_id) VALUES (erp.current_tenant_id(), :unit, :vendor, :no, :term, "
            ":me, :rfq) RETURNING po_id",
            {"unit": body.unit_id, "vendor": body.vendor_id, "no": number,
             "term": body.payment_term_id or vendor["payment_term_id"],
             "me": ctx.principal.profile.app_user_id, "rfq": body.rfq_id},
        )
    assert created is not None
    await write_lines(ctx, created["po_id"], body.lines)
    await link_indents(ctx, created["po_id"], body.unit_id, body.indent_ids)
    await audit(ctx, "po.create", "po", created["po_id"],
                {"po_no": number, "vendor_id": body.vendor_id, "lines": len(body.lines),
                 "indents": len(body.indent_ids), "rfq_id": body.rfq_id})
    return created["po_id"]  # type: ignore[no-any-return]


# ------------------------------------------------------------------ reading
@router.get("/purchase-orders", response_model=Page[PoSummary])
async def list_pos(
    po_status: str | None = Query(None, alias="status"),
    vendor_id: UUID | None = None,
    unit_id: UUID | None = None,
    page: Paging = Depends(),
    ctx: Ctx = Depends(read),
) -> Page[PoSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "p.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for column, name, value in (("p.status", "status", po_status and po_status.upper()),
                                ("p.vendor_id", "vendor", vendor_id),
                                ("p.unit_id", "unit", unit_id)):
        if value is not None:
            conditions.append(f"{column} = :{name}")
            params[name] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{PO_SQL}{where} ORDER BY p.created_at DESC, p.po_no DESC "
                       "LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.purchase_order p{where}", params)
    return Page(items=[PoSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/purchase-orders/late-lines", response_model=list[LateLine])
async def late_lines(ctx: Ctx = Depends(read)) -> list[LateLine]:
    """Deliveries past their due date that are still pending: the supplier follow-up list."""
    clause, params = scope_clause(ctx, "p.unit_id")
    found = await rows(
        ctx,
        "SELECT p.po_id, p.po_no, v.code AS vendor_code, v.name AS vendor_name, i.code AS "
        "item_code, s.due_date, (current_date - s.due_date) AS days_late, "
        "(s.scheduled_qty - s.received_qty) AS pending_qty FROM erp.po_schedule s "
        "JOIN erp.po_line l ON l.po_line_id = s.po_line_id JOIN erp.purchase_order p ON "
        "p.po_id = l.po_id JOIN erp.vendor v ON v.vendor_id = p.vendor_id JOIN erp.item i ON "
        "i.item_id = l.item_id WHERE s.due_date < current_date AND s.received_qty < "
        "s.scheduled_qty AND p.status IN ('OPEN', 'PARTIALLY_RECEIVED')"
        f"{' AND ' + clause if clause else ''} ORDER BY s.due_date, p.po_no", params,
    )
    return [LateLine(**f) for f in found]


@router.get("/purchase-orders/{po_id}", response_model=PoOut)
async def get_po(po_id: UUID, ctx: Ctx = Depends(read)) -> PoOut:
    po = await load(ctx, po_id)
    if po is None:
        raise not_found("Purchase order")
    return po


@router.get("/purchase-orders/{po_id}/receiving", response_model=list[ReceivingLine])
async def receiving_view(po_id: UUID, ctx: Ctx = Depends(read)) -> list[ReceivingLine]:
    """What the goods receipt may bring in per line: pending plus the grnTolPct tolerance."""
    if await load(ctx, po_id) is None:
        raise not_found("Purchase order")
    return [ReceivingLine(**s) for s in await receiving.line_status(ctx, po_id)]


@router.get("/purchase-orders/{po_id}/history", response_model=History)
async def history(po_id: UUID, ctx: Ctx = Depends(read)) -> History:
    """Every change to the order, oldest first, and the approval it went through."""
    po = await load(ctx, po_id)
    if po is None:
        raise not_found("Purchase order")
    events = await rows(
        ctx,
        "SELECT a.occurred_at, a.action, u.display_name AS actor, a.actor_role_code, a.details "
        "FROM erp.audit_log a LEFT JOIN erp.app_user u ON u.user_id = a.actor_user_id "
        "WHERE a.entity_type = 'po' AND a.entity_id = :id ORDER BY a.audit_id",
        {"id": str(po_id)},
    )
    approval = await engine.detail(ctx, po.approval_id) if po.approval_id else None
    return History(events=[HistoryEvent(**e) for e in events], approval=approval)


# ------------------------------------------------------------------ writing
@router.post("/purchase-orders", response_model=PoOut, status_code=status.HTTP_201_CREATED)
async def create_po(body: PoIn, ctx: Ctx = Depends(write)) -> PoOut:
    po_id = await create(ctx, body)
    po = await load(ctx, po_id)
    assert po is not None
    return po


@router.post("/purchase-orders/from-indents", response_model=PoOut,
             status_code=status.HTTP_201_CREATED)
async def create_from_indents(body: FromIndentsIn, ctx: Ctx = Depends(write)) -> PoOut:
    """One order for one or more approved indents: quantities are added up per item."""
    wanted: dict[UUID, Decimal] = {}
    needed: list[date] = []
    unit_id: UUID | None = None
    for indent_id in dict.fromkeys(body.indent_ids):
        indent = await one(ctx, "SELECT unit_id, needed_by FROM erp.indent WHERE indent_id = :id",
                           {"id": indent_id})
        if indent is None:
            raise HTTPException(422, "An indent does not exist")
        if unit_id not in (None, indent["unit_id"]):
            raise HTTPException(422, "All indents of one order must belong to the same plant")
        unit_id = indent["unit_id"]
        if indent["needed_by"]:
            needed.append(indent["needed_by"])
        for line in await rows(ctx, "SELECT item_id, requested_qty FROM erp.indent_line WHERE "
                               "indent_id = :id", {"id": indent_id}):
            item = line["item_id"]
            wanted[item] = wanted.get(item, Decimal(0)) + line["requested_qty"]
    prices = {p.item_id: p for p in body.prices}
    missing = set(wanted) - set(prices)
    if missing or set(prices) - set(wanted):
        raise HTTPException(422, "Give a price for exactly the items on the indents")
    due = body.due_date or (min(needed) if needed else None)
    if due is None:
        raise HTTPException(422, "Give a due_date: the indents have no needed-by date")
    assert unit_id is not None
    po_id = await create(ctx, PoIn(
        unit_id=unit_id, vendor_id=body.vendor_id, payment_term_id=body.payment_term_id,
        indent_ids=body.indent_ids,
        lines=[LineIn(item_id=item, ordered_qty=qty, unit_price=prices[item].unit_price,
                      tax_code_id=prices[item].tax_code_id, due_date=due)
               for item, qty in wanted.items()]))
    po = await load(ctx, po_id)
    assert po is not None
    return po


@router.post("/rfqs/{rfq_id}/award", response_model=PoOut, status_code=status.HTTP_201_CREATED)
async def award_rfq(rfq_id: UUID, body: AwardIn, ctx: Ctx = Depends(write)) -> PoOut:
    """Order from the winning vendor at its quoted prices and mark the RFQ awarded."""
    from heyyerp_api.procurement import rfq as rfq_module

    header = await rfq_module.require_rfq(ctx, rfq_id, "SENT")
    offers = [o for o in await rfq_module.latest_offers(ctx, rfq_id)
              if o["vendor_id"] == body.vendor_id]
    lines = await rows(ctx, "SELECT rfq_line_id, item_id, quantity FROM erp.rfq_line "
                       "WHERE rfq_id = :id ORDER BY line_no", {"id": rfq_id})
    by_line = {o["rfq_line_id"]: o for o in offers}
    if not offers or any(ln["rfq_line_id"] not in by_line for ln in lines):
        raise HTTPException(422, "The vendor's quote does not cover every line of the RFQ")
    for line in lines:
        offer = by_line[line["rfq_line_id"]]
        if offer["quoted_qty"] < line["quantity"]:
            raise HTTPException(422, "The vendor cannot supply the full quantity on every line")
        if offer["valid_until"] is not None and offer["valid_until"] < date.today():
            raise HTTPException(422, "The vendor's quote has expired")
    po_id = await create(ctx, PoIn(
        unit_id=header["unit_id"], vendor_id=body.vendor_id, payment_term_id=body.payment_term_id,
        rfq_id=rfq_id,
        lines=[LineIn(item_id=ln["item_id"], ordered_qty=ln["quantity"],
                      unit_price=by_line[ln["rfq_line_id"]]["unit_price"],
                      tax_code_id=body.tax_code_id, due_date=body.due_date) for ln in lines]))
    await rows(ctx, "UPDATE erp.rfq SET status = 'AWARDED' WHERE rfq_id = :id RETURNING rfq_id",
               {"id": rfq_id})
    await audit(ctx, "rfq.award", "rfq", rfq_id, {"vendor_id": body.vendor_id, "po_id": po_id})
    po = await load(ctx, po_id)
    assert po is not None
    return po


@router.put("/purchase-orders/{po_id}", response_model=PoOut)
async def update_po(po_id: UUID, body: PoUpdate, ctx: Ctx = Depends(write)) -> PoOut:
    """Replace a draft's lines and payment term."""
    po = await require_po(ctx, po_id, "DRAFT")
    await check_header(ctx, po["unit_id"], po["vendor_id"], body.payment_term_id)
    await check_lines(ctx, body.lines)
    await rows(ctx, "UPDATE erp.purchase_order SET payment_term_id = :t WHERE po_id = :id "
               "RETURNING po_id", {"t": body.payment_term_id, "id": po_id})
    await write_lines(ctx, po_id, body.lines)
    await audit(ctx, "po.update", "po", po_id, {"lines": len(body.lines)})
    result = await load(ctx, po_id)
    assert result is not None
    return result


@router.post("/purchase-orders/{po_id}/submit", response_model=PoOut)
async def submit_po(po_id: UUID, ctx: Ctx = Depends(write)) -> PoOut:
    """Send a draft for approval; the order value including GST picks the approval band."""
    po = await require_po(ctx, po_id, "DRAFT")
    current = await load(ctx, po_id)
    assert current is not None
    await check_header(ctx, po["unit_id"], po["vendor_id"], current.payment_term_id)
    if any(ln.tax_code_id is None for ln in current.lines):
        raise HTTPException(422, "Every line needs a tax code before the order is submitted")
    if await rules.get_value(ctx, "requireIndent") and not current.indents:
        raise HTTPException(
            422, "Business rule requireIndent: this order must come from an approved indent")
    needed = await rules.get_value(ctx, "minQuotes")
    if needed:
        quotes = await count(
            ctx, "SELECT count(*) FROM erp.rfq_vendor_invitation WHERE rfq_id = :id AND "
            "response_status = 'QUOTED'", {"id": po["rfq_id"]}) if po["rfq_id"] else 0
        if quotes < needed:
            raise HTTPException(
                422, f"Business rule minQuotes: the order needs an RFQ with at least {needed} "
                f"quotes (it has {quotes})")
    await rows(ctx, "UPDATE erp.purchase_order SET status = 'PENDING_APPROVAL' WHERE po_id = :id "
               "RETURNING po_id", {"id": po_id})
    approval_id = await engine.submit(ctx, "po", po_id)
    await audit(ctx, "po.submit", "po", po_id,
                {"approval_id": approval_id, "gross_total": current.gross_total})
    result = await load(ctx, po_id)
    assert result is not None
    return result


@router.post("/purchase-orders/{po_id}/cancel", response_model=PoOut)
async def cancel_po(po_id: UUID, ctx: Ctx = Depends(write)) -> PoOut:
    """Cancel a draft, or an approved order nothing has been received against."""
    await require_po(ctx, po_id, "DRAFT", "OPEN")
    if await one(ctx, "SELECT 1 AS x FROM erp.po_schedule s JOIN erp.po_line l ON l.po_line_id = "
                 "s.po_line_id WHERE l.po_id = :id AND s.received_qty > 0", {"id": po_id}):
        raise HTTPException(status.HTTP_409_CONFLICT, "Goods were already received on this order")
    await rows(ctx, "UPDATE erp.purchase_order SET status = 'CANCELLED' WHERE po_id = :id "
               "RETURNING po_id", {"id": po_id})
    await release_indents(ctx, po_id)
    await audit(ctx, "po.cancel", "po", po_id, {})
    result = await load(ctx, po_id)
    assert result is not None
    return result


@router.post("/purchase-orders/{po_id}/close", response_model=PoOut)
async def close_po(po_id: UUID, ctx: Ctx = Depends(write)) -> PoOut:
    """Close a part-received order: the rest will not be delivered."""
    await require_po(ctx, po_id, "PARTIALLY_RECEIVED", "RECEIVED")
    await rows(ctx, "UPDATE erp.purchase_order SET status = 'CLOSED' WHERE po_id = :id "
               "RETURNING po_id", {"id": po_id})
    await audit(ctx, "po.close", "po", po_id, {})
    result = await load(ctx, po_id)
    assert result is not None
    return result


# ------------------------------------------------------------------ approval adapter
async def load_for_approval(ctx: Ctx, po_id: UUID) -> DocInfo | None:
    head = await one(ctx, "SELECT po_no, status FROM erp.purchase_order WHERE po_id = :id",
                     {"id": po_id})
    if head is None:
        return None
    rates = await rows(
        ctx, "SELECT l.ordered_qty, l.unit_price, t.gst_rate FROM erp.po_line l LEFT JOIN "
        "erp.tax_code t ON t.tax_code_id = l.tax_code_id WHERE l.po_id = :id", {"id": po_id})
    gross = Decimal(0)
    for r in rates:
        net, gst = line_amounts(r["ordered_qty"], r["unit_price"], r["gst_rate"])
        gross += net + gst
    return DocInfo(
        title=f"Purchase order {head['po_no']}", amount=gross, department_id=None,
        submittable=head["status"] == "PENDING_APPROVAL",
        reason=f"The order is {str(head['status']).lower().replace('_', ' ')}",
    )


async def finalize_po(ctx: Ctx, po_id: UUID, approved: bool) -> None:
    await rows(ctx, "UPDATE erp.purchase_order SET status = :s WHERE po_id = :id RETURNING po_id",
               {"s": "OPEN" if approved else "REJECTED", "id": po_id})
    if not approved:
        await release_indents(ctx, po_id)


register(DocumentType("po", "po.create", load_for_approval, finalize_po))
