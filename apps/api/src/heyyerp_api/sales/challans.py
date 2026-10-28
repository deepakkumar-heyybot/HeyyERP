"""Returnable, non-returnable, free-of-cost and job-work challans with due-back tracking
(BRD FR-SAL-07).

A challan sends stock out of a store without a sale. The lots are picked by the issue rule (or
named), only AVAILABLE and unexpired ones, and leave stock at their FIFO cost. A returnable or
job-work challan must say who has the goods and when they are due back, and shows as overdue after
that date until everything comes back; goods returned go into the lot they left from. A
non-returnable challan names the customer; a free-of-cost one (samples) may name none. Needs the
`challan` permission; reading needs the sales module.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from heyyerp_api.business_rules import service as rules
from heyyerp_api.inventory import ledger
from heyyerp_api.inventory.issues import candidates, choose, store_in_scope
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

router = APIRouter(prefix="/sales", tags=["sales"])
manage = requires("challan")
read = requires_module("sales")
ChallanType = Literal["RETURNABLE", "NON_RETURNABLE", "FOC", "JOB_WORK"]
RETURNS = ("RETURNABLE", "JOB_WORK")
Qty = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)


class LineIn(Strict):
    item_id: UUID
    quantity: Decimal = Qty
    lot_id: UUID | None = None


class ChallanIn(Strict):
    unit_id: UUID
    store_id: UUID
    challan_type: ChallanType
    customer_id: UUID | None = None
    due_back: date | None = None
    reason: str = Field(min_length=3, max_length=200)
    lines: list[LineIn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _rules(self) -> "ChallanIn":
        if self.challan_type in RETURNS:
            if self.customer_id is None or self.due_back is None:
                raise ValueError(
                    "A returnable or job-work challan needs the party and a due-back date")
        elif self.due_back is not None:
            raise ValueError("Only returnable and job-work challans have a due-back date")
        if self.challan_type == "NON_RETURNABLE" and self.customer_id is None:
            raise ValueError("A non-returnable challan needs the customer")
        items = [ln.item_id for ln in self.lines]
        if len(set(items)) != len(items):
            raise ValueError("Each item may appear on a challan only once")
        return self


class ReturnLine(Strict):
    challan_line_id: UUID
    quantity: Decimal = Qty


class ReturnIn(Strict):
    lines: list[ReturnLine] = Field(min_length=1, max_length=100)


class ChallanLineOut(BaseModel):
    challan_line_id: UUID
    item_code: str
    item_name: str
    lot_no: str | None
    quantity: Decimal
    returned_qty: Decimal
    outstanding_qty: Decimal


class ChallanSummary(BaseModel):
    challan_id: UUID
    challan_no: str
    unit_id: UUID
    challan_type: str
    customer_id: UUID | None
    customer_code: str | None
    issued_at: datetime
    due_back: date | None
    status: str
    overdue: bool
    days_overdue: int


class ChallanOut(ChallanSummary):
    reason: str | None
    store_id: UUID | None
    lines: list[ChallanLineOut]


CHALLAN_SQL = (
    "SELECT h.challan_id, h.challan_no, h.unit_id, h.challan_type, h.customer_id, c.code AS "
    "customer_code, h.issued_at, h.due_back, h.status, h.reason, h.store_id, "
    "(h.due_back IS NOT NULL AND h.due_back < CURRENT_DATE AND h.status <> 'RETURNED') AS "
    "overdue, CASE WHEN h.due_back IS NOT NULL AND h.due_back < CURRENT_DATE AND h.status <> "
    "'RETURNED' THEN CURRENT_DATE - h.due_back ELSE 0 END AS days_overdue FROM erp.challan h "
    "LEFT JOIN erp.customer c ON c.customer_id = h.customer_id"
)


async def load(ctx: Ctx, challan_id: UUID) -> ChallanOut | None:
    clause, params = scope_clause(ctx, "h.unit_id")
    head = await one(ctx, f"{CHALLAN_SQL} WHERE h.challan_id = :id"
                     f"{' AND ' + clause if clause else ''}", {**params, "id": challan_id})
    if head is None:
        return None
    lines = await rows(
        ctx, "SELECT l.challan_line_id, i.code AS item_code, i.name AS item_name, t.lot_no, "
        "l.quantity, l.returned_qty, l.quantity - l.returned_qty AS outstanding_qty FROM "
        "erp.challan_line l JOIN erp.item i ON i.item_id = l.item_id LEFT JOIN erp.lot t ON "
        "t.lot_id = l.lot_id WHERE l.challan_id = :id ORDER BY i.code, t.lot_no",
        {"id": challan_id})
    return ChallanOut(**head, lines=[ChallanLineOut(**ln) for ln in lines])


@router.post("/challans", response_model=ChallanOut, status_code=status.HTTP_201_CREATED)
async def issue_challan(body: ChallanIn, ctx: Ctx = Depends(manage)) -> ChallanOut:
    store = await store_in_scope(ctx, body.store_id)
    if store["unit_id"] != body.unit_id or not store["active"]:
        raise HTTPException(422, "The store must be an active store of the plant")
    if body.customer_id is not None:
        party = await one(ctx, "SELECT active FROM erp.customer WHERE customer_id = :id",
                          {"id": body.customer_id})
        if party is None or not party["active"]:
            raise HTTPException(422, "The customer does not exist or is inactive")
    if body.due_back is not None and body.due_back < date.today():
        raise HTTPException(422, "The due-back date is in the past")
    rule = await rules.get_value(ctx, "issueRule")
    items = {r["item_id"]: r for r in await rows(
        ctx, "SELECT item_id, code, active FROM erp.item WHERE item_id = ANY(CAST(:ids AS "
        "uuid[]))", {"ids": [ln.item_id for ln in body.lines]})}
    number = await next_number(ctx, "challan")
    created = await one(
        ctx, "INSERT INTO erp.challan(tenant_id, unit_id, customer_id, challan_no, challan_type, "
        "due_back, store_id, reason, created_by) VALUES (erp.current_tenant_id(), :u, :c, :n, "
        ":t, :d, :s, :r, :me) RETURNING challan_id",
        {"u": body.unit_id, "c": body.customer_id, "n": number, "t": body.challan_type,
         "d": body.due_back, "s": body.store_id, "r": body.reason.strip(),
         "me": ctx.principal.profile.app_user_id})
    assert created is not None
    steps = 0
    for line in sorted(body.lines, key=lambda ln: str(ln.item_id)):  # fixed lock order
        item = items.get(line.item_id)
        if item is None or not item["active"]:
            raise HTTPException(422, "An item does not exist or is inactive")
        lots = await candidates(ctx, body.store_id, line.item_id, lock=True)
        if line.lot_id is not None:
            lots = [lot for lot in lots if lot["lot_id"] == line.lot_id]
        picks, shortfall, _blocked = choose(lots, line.quantity, rule)
        if shortfall > 0:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Not enough stock of {item['code']}: {line.quantity - shortfall} can be sent, "
                f"{line.quantity} needed")
        for k in picks:
            steps += 1
            ledger_id = await ledger.move(
                ctx, unit_id=body.unit_id, item_id=line.item_id, lot_id=k["lot_id"],
                store_id=body.store_id, delta=-k["take"], movement_type="ISSUE",
                reference_type="CHALLAN", reference_id=created["challan_id"],
                idempotency_key=f"challan:{created['challan_id']}:{steps}")
            await ledger.take_layers(ctx, unit_id=body.unit_id, item_id=line.item_id,
                                     quantity=k["take"], issue_ledger_id=ledger_id)
            await rows(
                ctx, "INSERT INTO erp.challan_line(tenant_id, challan_id, item_id, lot_id, "
                "quantity) VALUES (erp.current_tenant_id(), :c, :i, :l, :q) "
                "RETURNING challan_line_id",
                {"c": created["challan_id"], "i": line.item_id, "l": k["lot_id"], "q": k["take"]})
    await audit(ctx, "challan.issue", "challan", created["challan_id"],
                {"challan_no": number, "type": body.challan_type, "lines": len(body.lines)})
    result = await load(ctx, created["challan_id"])
    assert result is not None
    return result


@router.get("/challans", response_model=Page[ChallanSummary])
async def list_challans(
    challan_type: ChallanType | None = None, customer_id: UUID | None = None,
    challan_status: str | None = Query(None, alias="status"), overdue: bool | None = None,
    page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[ChallanSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "h.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for text_, key, value in (("h.challan_type = :t", "t", challan_type),
                              ("h.customer_id = :c", "c", customer_id),
                              ("h.status = :st", "st",
                               challan_status.upper() if challan_status else None)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    if overdue is not None:
        late = "(h.due_back IS NOT NULL AND h.due_back < CURRENT_DATE AND h.status <> 'RETURNED')"
        conditions.append(late if overdue else f"NOT {late}")
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{CHALLAN_SQL}{where} ORDER BY h.challan_no DESC LIMIT :limit "
                       "OFFSET :offset", {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.challan h{where}", params)
    return Page(items=[ChallanSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/challans/{challan_id}", response_model=ChallanOut)
async def get_challan(challan_id: UUID, ctx: Ctx = Depends(read)) -> ChallanOut:
    found = await load(ctx, challan_id)
    if found is None:
        raise not_found("Challan")
    return found


@router.post("/challans/{challan_id}/return", response_model=ChallanOut)
async def return_goods(challan_id: UUID, body: ReturnIn, ctx: Ctx = Depends(manage)) -> ChallanOut:
    """Goods come back: they go into the lot they left from; the challan closes when all is back."""
    clause, params = scope_clause(ctx, "unit_id")
    head = await one(
        ctx, "SELECT challan_id, challan_no, unit_id, store_id, challan_type, status FROM "
        f"erp.challan WHERE challan_id = :id{' AND ' + clause if clause else ''} FOR UPDATE",
        {**params, "id": challan_id})
    if head is None:
        raise not_found("Challan")
    if head["challan_type"] not in RETURNS:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Only returnable and job-work challans take goods back")
    if head["status"] == "RETURNED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Everything on this challan is back")
    ids = [ln.challan_line_id for ln in body.lines]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Each line may appear only once")
    for number, line in enumerate(sorted(body.lines, key=lambda ln: str(ln.challan_line_id))):
        found = await one(
            ctx, "SELECT l.challan_line_id, l.item_id, l.lot_id, l.quantity, l.returned_qty "
            "FROM erp.challan_line l WHERE l.challan_line_id = :l AND l.challan_id = :c "
            "FOR UPDATE", {"l": line.challan_line_id, "c": challan_id})
        if found is None:
            raise HTTPException(422, "A line does not belong to this challan")
        outstanding = found["quantity"] - found["returned_qty"]
        if line.quantity > outstanding:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Only {outstanding} of this line is still out")
        ledger_id = await ledger.move(
            ctx, unit_id=head["unit_id"], item_id=found["item_id"], lot_id=found["lot_id"],
            store_id=head["store_id"], delta=line.quantity, movement_type="RECEIPT",
            reference_type="CHALLAN_RETURN", reference_id=challan_id,
            idempotency_key=f"challan-return:{line.challan_line_id}:{found['returned_qty']}:"
                            f"{number}")
        cost = await ledger.last_cost(ctx, head["unit_id"], found["item_id"])
        await ledger.add_layer(ctx, ledger_id=ledger_id, item_id=found["item_id"],
                               lot_id=found["lot_id"], quantity=line.quantity,
                               unit_cost=cost or Decimal(0))
        await rows(ctx, "UPDATE erp.challan_line SET returned_qty = returned_qty + :q WHERE "
                   "challan_line_id = :id RETURNING challan_line_id",
                   {"q": line.quantity, "id": line.challan_line_id})
    totals = await one(ctx, "SELECT sum(returned_qty) AS back, bool_and(returned_qty >= quantity) "
                       "AS full FROM erp.challan_line WHERE challan_id = :id", {"id": challan_id})
    assert totals is not None
    new = "RETURNED" if totals["full"] else "PARTIALLY_RETURNED"
    await rows(ctx, "UPDATE erp.challan SET status = :s WHERE challan_id = :id RETURNING "
               "challan_id", {"s": new, "id": challan_id})
    await audit(ctx, "challan.return", "challan", challan_id,
                {"challan_no": head["challan_no"], "status": new})
    result = await load(ctx, challan_id)
    assert result is not None
    return result
