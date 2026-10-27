"""Requests for quotation: invite approved vendors, record their quotes, compare (BRD FR-PRC-10).

DRAFT -> SENT (vendors invited) -> AWARDED (a purchase order was created from the winning quote)
or CLOSED / CANCELLED. A vendor may be invited only if it is APPROVED (FR-PRC-04). A vendor's
newer quote replaces its older one (the older stays, marked SUPERSEDED). The comparison sheet
puts the vendors side by side per line and marks the lowest price among offers that are still
valid and cover the full quantity. If the business rule `minQuotes` is set, an RFQ must invite at
least that many vendors before it is sent. Needs po.create to write and the procurement module
to read.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

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
from heyyerp_api.procurement.numbering import next_number

router = APIRouter(prefix="/procurement/rfqs", tags=["procurement"])
write = requires("po.create")
read = requires_module("procurement")


class LineIn(Strict):
    item_id: UUID
    quantity: Decimal = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)
    required_by: date | None = None


class RfqIn(Strict):
    unit_id: UUID
    response_due_at: datetime | None = None
    lines: list[LineIn] = Field(min_length=1, max_length=100)
    vendor_ids: list[UUID] = Field(default_factory=list, max_length=50)


class RfqUpdate(Strict):
    response_due_at: datetime | None = None
    lines: list[LineIn] = Field(min_length=1, max_length=100)


class InviteIn(Strict):
    vendor_ids: list[UUID] = Field(min_length=1, max_length=50)


class QuoteLineIn(Strict):
    rfq_line_id: UUID
    unit_price: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    quoted_qty: Decimal = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)
    lead_days: int | None = Field(default=None, ge=0, le=730)


class QuoteIn(Strict):
    vendor_id: UUID
    quote_reference: str | None = Field(default=None, max_length=60)
    valid_until: date | None = None
    lines: list[QuoteLineIn] = Field(min_length=1, max_length=100)


class RfqLineOut(BaseModel):
    rfq_line_id: UUID
    line_no: int
    item_id: UUID
    item_code: str
    item_name: str
    uom_code: str
    quantity: Decimal
    required_by: date | None


class InvitationOut(BaseModel):
    vendor_id: UUID
    vendor_code: str
    vendor_name: str
    response_status: str
    invited_at: datetime
    quotes_received: int


class RfqSummary(BaseModel):
    rfq_id: UUID
    rfq_no: str
    unit_id: UUID
    unit_code: str
    status: str
    response_due_at: datetime | None
    created_by_name: str
    created_at: datetime


class RfqOut(RfqSummary):
    lines: list[RfqLineOut]
    invitations: list[InvitationOut]


RFQ_SQL = (
    "SELECT r.rfq_id, r.rfq_no, r.unit_id, u.code AS unit_code, r.status, r.response_due_at, "
    "cu.display_name AS created_by_name, r.created_at FROM erp.rfq r "
    "JOIN erp.unit u ON u.unit_id = r.unit_id JOIN erp.app_user cu ON cu.user_id = r.created_by"
)


async def load(ctx: Ctx, rfq_id: UUID) -> RfqOut | None:
    clause, params = scope_clause(ctx, "r.unit_id")
    head = await one(ctx, f"{RFQ_SQL} WHERE r.rfq_id = :id{' AND ' + clause if clause else ''}",
                     {**params, "id": rfq_id})
    if head is None:
        return None
    lines = await rows(
        ctx,
        "SELECT l.rfq_line_id, l.line_no, l.item_id, i.code AS item_code, i.name AS item_name, "
        "um.code AS uom_code, l.quantity, l.required_by FROM erp.rfq_line l "
        "JOIN erp.item i ON i.item_id = l.item_id JOIN erp.uom um ON um.uom_id = i.uom_id "
        "WHERE l.rfq_id = :id ORDER BY l.line_no", {"id": rfq_id},
    )
    invitations = await rows(
        ctx,
        "SELECT v.vendor_id, v.code AS vendor_code, v.name AS vendor_name, n.response_status, "
        "n.invited_at, (SELECT count(*) FROM erp.vendor_quote q WHERE q.invitation_id = "
        "n.invitation_id) AS quotes_received FROM erp.rfq_vendor_invitation n "
        "JOIN erp.vendor v ON v.vendor_id = n.vendor_id WHERE n.rfq_id = :id ORDER BY v.code",
        {"id": rfq_id},
    )
    return RfqOut(**head, lines=[RfqLineOut(**ln) for ln in lines],
                  invitations=[InvitationOut(**i) for i in invitations])


async def require_rfq(ctx: Ctx, rfq_id: UUID, *states: str) -> dict[str, Any]:
    clause, params = scope_clause(ctx, "unit_id")
    rfq = await one(
        ctx, f"SELECT rfq_id, unit_id, status FROM erp.rfq WHERE rfq_id = :id"
        f"{' AND ' + clause if clause else ''} FOR UPDATE", {**params, "id": rfq_id})
    if rfq is None:
        raise not_found("RFQ")
    if states and rfq["status"] not in states:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The RFQ is {rfq['status'].lower()}")
    return rfq


async def check_lines(ctx: Ctx, lines: list[LineIn]) -> None:
    ids = [ln.item_id for ln in lines]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Each item may appear only once on an RFQ")
    found = {r["item_id"]: r for r in await rows(
        ctx, "SELECT item_id, code, active FROM erp.item WHERE item_id = ANY(CAST(:ids AS uuid[]))",
        {"ids": ids})}
    for line in lines:
        item = found.get(line.item_id)
        if item is None:
            raise HTTPException(422, "An item does not exist")
        if not item["active"]:
            raise HTTPException(422, f"Item {item['code']} is inactive")
        if line.required_by is not None and line.required_by < date.today():
            raise HTTPException(422, "A required-by date cannot be in the past")


async def write_lines(ctx: Ctx, rfq_id: UUID, lines: list[LineIn]) -> None:
    await rows(ctx, "DELETE FROM erp.rfq_line WHERE rfq_id = :id RETURNING rfq_line_id",
               {"id": rfq_id})
    for number, line in enumerate(lines, start=1):
        await one(
            ctx,
            "INSERT INTO erp.rfq_line(tenant_id, rfq_id, item_id, line_no, quantity, required_by) "
            "VALUES (erp.current_tenant_id(), :rfq, :item, :no, :qty, :by) RETURNING rfq_line_id",
            {"rfq": rfq_id, "item": line.item_id, "no": number, "qty": line.quantity,
             "by": line.required_by},
        )


async def invite(ctx: Ctx, rfq_id: UUID, vendor_ids: list[UUID]) -> None:
    for vendor_id in dict.fromkeys(vendor_ids):
        vendor = await one(ctx, "SELECT code, status FROM erp.vendor WHERE vendor_id = :id",
                           {"id": vendor_id})
        if vendor is None:
            raise HTTPException(422, "A vendor does not exist")
        if vendor["status"] != "APPROVED":
            raise HTTPException(
                422, f"Vendor {vendor['code']} is {vendor['status'].lower().replace('_', ' ')}; "
                "only approved vendors can be invited")
        await rows(
            ctx,
            "INSERT INTO erp.rfq_vendor_invitation(tenant_id, rfq_id, vendor_id) VALUES "
            "(erp.current_tenant_id(), :rfq, :vendor) ON CONFLICT (rfq_id, vendor_id) DO NOTHING "
            "RETURNING invitation_id", {"rfq": rfq_id, "vendor": vendor_id},
        )


@router.get("", response_model=Page[RfqSummary])
async def list_rfqs(
    rfq_status: str | None = Query(None, alias="status"),
    unit_id: UUID | None = None,
    page: Paging = Depends(),
    ctx: Ctx = Depends(read),
) -> Page[RfqSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "r.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for column, name, value in (("r.status", "status", rfq_status and rfq_status.upper()),
                                ("r.unit_id", "unit", unit_id)):
        if value is not None:
            conditions.append(f"{column} = :{name}")
            params[name] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{RFQ_SQL}{where} ORDER BY r.created_at DESC, r.rfq_no DESC "
                       "LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.rfq r{where}", params)
    return Page(items=[RfqSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/{rfq_id}", response_model=RfqOut)
async def get_rfq(rfq_id: UUID, ctx: Ctx = Depends(read)) -> RfqOut:
    rfq = await load(ctx, rfq_id)
    if rfq is None:
        raise not_found("RFQ")
    return rfq


@router.post("", response_model=RfqOut, status_code=status.HTTP_201_CREATED)
async def create_rfq(body: RfqIn, ctx: Ctx = Depends(write)) -> RfqOut:
    require_in_scope(ctx, body.unit_id)
    plant = await one(ctx, "SELECT active FROM erp.unit WHERE unit_id = :id", {"id": body.unit_id})
    if plant is None or not plant["active"]:
        raise HTTPException(422, "Plant does not exist or is inactive")
    await check_lines(ctx, body.lines)
    number = await next_number(ctx, "rfq")
    created = await one(
        ctx,
        "INSERT INTO erp.rfq(tenant_id, unit_id, rfq_no, response_due_at, created_by) VALUES "
        "(erp.current_tenant_id(), :unit, :no, :due, :me) RETURNING rfq_id",
        {"unit": body.unit_id, "no": number, "due": body.response_due_at,
         "me": ctx.principal.profile.app_user_id},
    )
    assert created is not None
    await write_lines(ctx, created["rfq_id"], body.lines)
    await invite(ctx, created["rfq_id"], body.vendor_ids)
    await audit(ctx, "rfq.create", "rfq", created["rfq_id"], {"rfq_no": number})
    rfq = await load(ctx, created["rfq_id"])
    assert rfq is not None
    return rfq


@router.put("/{rfq_id}", response_model=RfqOut)
async def update_rfq(rfq_id: UUID, body: RfqUpdate, ctx: Ctx = Depends(write)) -> RfqOut:
    """Replace a draft's lines and response date."""
    await require_rfq(ctx, rfq_id, "DRAFT")
    await check_lines(ctx, body.lines)
    await rows(ctx, "UPDATE erp.rfq SET response_due_at = :d WHERE rfq_id = :id RETURNING rfq_id",
               {"d": body.response_due_at, "id": rfq_id})
    await write_lines(ctx, rfq_id, body.lines)
    await audit(ctx, "rfq.update", "rfq", rfq_id, {"lines": len(body.lines)})
    rfq = await load(ctx, rfq_id)
    assert rfq is not None
    return rfq


@router.post("/{rfq_id}/vendors", response_model=RfqOut)
async def invite_vendors(rfq_id: UUID, body: InviteIn, ctx: Ctx = Depends(write)) -> RfqOut:
    await require_rfq(ctx, rfq_id, "DRAFT", "SENT")
    await invite(ctx, rfq_id, body.vendor_ids)
    await audit(ctx, "rfq.invite", "rfq", rfq_id, {"vendor_ids": body.vendor_ids})
    rfq = await load(ctx, rfq_id)
    assert rfq is not None
    return rfq


@router.post("/{rfq_id}/send", response_model=RfqOut)
async def send_rfq(rfq_id: UUID, ctx: Ctx = Depends(write)) -> RfqOut:
    await require_rfq(ctx, rfq_id, "DRAFT")
    invited = await count(ctx, "SELECT count(*) FROM erp.rfq_vendor_invitation WHERE rfq_id = :id",
                          {"id": rfq_id})
    needed = await rules.get_value(ctx, "minQuotes")
    if invited < max(1, needed or 0):
        raise HTTPException(
            422, f"Invite at least {max(1, needed or 0)} approved vendor(s) before sending"
            + (" (business rule minQuotes)" if needed else ""))
    await rows(ctx, "UPDATE erp.rfq SET status = 'SENT' WHERE rfq_id = :id RETURNING rfq_id",
               {"id": rfq_id})
    await audit(ctx, "rfq.send", "rfq", rfq_id, {"vendors": invited})
    rfq = await load(ctx, rfq_id)
    assert rfq is not None
    return rfq


@router.post("/{rfq_id}/cancel", response_model=RfqOut)
async def cancel_rfq(rfq_id: UUID, ctx: Ctx = Depends(write)) -> RfqOut:
    await require_rfq(ctx, rfq_id, "DRAFT", "SENT")
    await rows(ctx, "UPDATE erp.rfq SET status = 'CANCELLED' WHERE rfq_id = :id RETURNING rfq_id",
               {"id": rfq_id})
    await audit(ctx, "rfq.cancel", "rfq", rfq_id, {})
    rfq = await load(ctx, rfq_id)
    assert rfq is not None
    return rfq


@router.post("/{rfq_id}/quotes", response_model=RfqOut, status_code=status.HTTP_201_CREATED)
async def record_quote(rfq_id: UUID, body: QuoteIn, ctx: Ctx = Depends(write)) -> RfqOut:
    """Enter a vendor's quote. A second quote from the same vendor replaces the first."""
    await require_rfq(ctx, rfq_id, "SENT")
    invitation = await one(
        ctx, "SELECT invitation_id FROM erp.rfq_vendor_invitation WHERE rfq_id = :r AND "
        "vendor_id = :v", {"r": rfq_id, "v": body.vendor_id})
    if invitation is None:
        raise HTTPException(422, "That vendor was not invited to this RFQ")
    if body.valid_until is not None and body.valid_until < date.today():
        raise HTTPException(422, "The quote has already expired")
    wanted = {r["rfq_line_id"]: r["quantity"] for r in await rows(
        ctx, "SELECT rfq_line_id, quantity FROM erp.rfq_line WHERE rfq_id = :id", {"id": rfq_id})}
    seen: set[UUID] = set()
    for line in body.lines:
        if line.rfq_line_id not in wanted:
            raise HTTPException(422, "A quoted line does not belong to this RFQ")
        if line.rfq_line_id in seen:
            raise HTTPException(422, "A line is quoted twice")
        seen.add(line.rfq_line_id)
    await rows(ctx, "UPDATE erp.vendor_quote SET status = 'SUPERSEDED' WHERE invitation_id = :i "
               "AND status = 'RECEIVED' RETURNING vendor_quote_id",
               {"i": invitation["invitation_id"]})
    with db_errors("Quote"):
        quote = await one(
            ctx,
            "INSERT INTO erp.vendor_quote(tenant_id, invitation_id, quote_reference, valid_until) "
            "VALUES (erp.current_tenant_id(), :inv, :ref, :valid) RETURNING vendor_quote_id",
            {"inv": invitation["invitation_id"], "ref": body.quote_reference,
             "valid": body.valid_until},
        )
    assert quote is not None
    for line in body.lines:
        await one(
            ctx,
            "INSERT INTO erp.vendor_quote_line(tenant_id, vendor_quote_id, rfq_line_id, "
            "unit_price, quoted_qty, lead_days) VALUES (erp.current_tenant_id(), :q, :line, "
            ":price, :qty, :lead) RETURNING vendor_quote_line_id",
            {"q": quote["vendor_quote_id"], "line": line.rfq_line_id, "price": line.unit_price,
             "qty": line.quoted_qty, "lead": line.lead_days},
        )
    await rows(ctx, "UPDATE erp.rfq_vendor_invitation SET response_status = 'QUOTED' "
               "WHERE invitation_id = :i RETURNING invitation_id",
               {"i": invitation["invitation_id"]})
    await audit(ctx, "rfq.quote", "rfq", rfq_id,
                {"vendor_id": body.vendor_id, "lines": len(body.lines)})
    rfq = await load(ctx, rfq_id)
    assert rfq is not None
    return rfq


# ------------------------------------------------------------------ comparison sheet
class Offer(BaseModel):
    vendor_id: UUID
    vendor_code: str
    unit_price: Decimal
    quoted_qty: Decimal
    lead_days: int | None
    line_total: Decimal  # price times the quantity we want, not the quantity quoted
    valid_until: date | None
    expired: bool
    covers_quantity: bool
    is_lowest: bool


class CompareLine(BaseModel):
    rfq_line_id: UUID
    line_no: int
    item_code: str
    quantity: Decimal
    offers: list[Offer]


class VendorTotal(BaseModel):
    vendor_id: UUID
    vendor_code: str
    vendor_name: str
    lines_quoted: int
    covers_all_lines: bool
    has_expired_offer: bool
    total: Decimal
    max_lead_days: int | None


class Comparison(BaseModel):
    rfq_id: UUID
    rfq_no: str
    lines: list[CompareLine]
    vendors: list[VendorTotal]
    cheapest_vendor_id: UUID | None


def build_comparison(
    wanted: list[dict[str, Any]], offers: list[dict[str, Any]], today: date
) -> tuple[list[CompareLine], list[VendorTotal], UUID | None]:
    """Pure: line-by-line offers with the lowest marked, and per-vendor totals."""
    lines: list[CompareLine] = []
    for line in wanted:
        entries = []
        for o in offers:
            if o["rfq_line_id"] != line["rfq_line_id"]:
                continue
            expired = o["valid_until"] is not None and o["valid_until"] < today
            entries.append({**o, "expired": expired,
                            "covers": o["quoted_qty"] >= line["quantity"]})
        eligible = [e["unit_price"] for e in entries if not e["expired"] and e["covers"]]
        best = min(eligible) if eligible else None
        lines.append(CompareLine(
            rfq_line_id=line["rfq_line_id"], line_no=line["line_no"], item_code=line["item_code"],
            quantity=line["quantity"],
            offers=[Offer(
                vendor_id=e["vendor_id"], vendor_code=e["vendor_code"],
                unit_price=e["unit_price"], quoted_qty=e["quoted_qty"], lead_days=e["lead_days"],
                line_total=e["unit_price"] * line["quantity"], valid_until=e["valid_until"],
                expired=e["expired"], covers_quantity=e["covers"],
                is_lowest=best is not None and not e["expired"] and e["covers"]
                and e["unit_price"] == best,
            ) for e in sorted(entries, key=lambda e: (e["unit_price"], e["vendor_code"]))],
        ))
    totals: dict[UUID, VendorTotal] = {}
    for compare_line in lines:
        for offer in compare_line.offers:
            vendor = next(o for o in offers if o["vendor_id"] == offer.vendor_id)
            current = totals.setdefault(offer.vendor_id, VendorTotal(
                vendor_id=offer.vendor_id, vendor_code=offer.vendor_code,
                vendor_name=vendor["vendor_name"], lines_quoted=0, covers_all_lines=True,
                has_expired_offer=False, total=Decimal(0), max_lead_days=None))
            current.lines_quoted += 1
            current.total += offer.line_total
            current.covers_all_lines &= offer.covers_quantity
            current.has_expired_offer |= offer.expired
            if offer.lead_days is not None:
                current.max_lead_days = max(current.max_lead_days or 0, offer.lead_days)
    for total in totals.values():
        total.covers_all_lines &= total.lines_quoted == len(wanted)
    contenders = [t for t in totals.values() if t.covers_all_lines and not t.has_expired_offer]
    winner = min(contenders, key=lambda t: (t.total, t.max_lead_days or 0, t.vendor_code),
                 default=None)
    return lines, sorted(totals.values(), key=lambda t: t.vendor_code), (
        winner.vendor_id if winner else None)


async def latest_offers(ctx: Ctx, rfq_id: UUID) -> list[dict[str, Any]]:
    return await rows(
        ctx,
        "SELECT v.vendor_id, v.code AS vendor_code, v.name AS vendor_name, ql.rfq_line_id, "
        "ql.unit_price, ql.quoted_qty, ql.lead_days, q.valid_until, q.vendor_quote_id "
        "FROM erp.vendor_quote q JOIN erp.rfq_vendor_invitation n ON n.invitation_id = "
        "q.invitation_id JOIN erp.vendor v ON v.vendor_id = n.vendor_id JOIN "
        "erp.vendor_quote_line ql ON ql.vendor_quote_id = q.vendor_quote_id "
        "WHERE n.rfq_id = :id AND q.status = 'RECEIVED'", {"id": rfq_id},
    )


@router.get("/{rfq_id}/comparison", response_model=Comparison)
async def comparison(rfq_id: UUID, ctx: Ctx = Depends(read)) -> Comparison:
    """Quotes side by side. The lowest valid, full-quantity offer per line is marked."""
    rfq = await load(ctx, rfq_id)
    if rfq is None:
        raise not_found("RFQ")
    wanted = [{"rfq_line_id": ln.rfq_line_id, "line_no": ln.line_no, "item_code": ln.item_code,
               "quantity": ln.quantity} for ln in rfq.lines]
    lines, vendors, cheapest = build_comparison(wanted, await latest_offers(ctx, rfq_id),
                                                date.today())
    return Comparison(rfq_id=rfq.rfq_id, rfq_no=rfq.rfq_no, lines=lines, vendors=vendors,
                      cheapest_vendor_id=cheapest)
