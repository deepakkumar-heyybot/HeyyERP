"""Issuing stock to production and reserving it (BRD FR-INV-03, FR-INV-04, RULE-07, RULE-08).

The issue rule (setting `issueRule`) picks the lots: FEFO takes the earliest expiry first and then
the oldest receipt; FIFO the oldest receipt. Only AVAILABLE lots that have not expired and are not
reserved by someone else can be issued. Expired lots are blocked and reported. If the stock does
not cover the request the whole issue is refused: stock never goes below zero. The lots of an item
are locked in a fixed order before anything is taken, so two people issuing at once cannot both
take the last of a lot. Needs inv.issue; reading needs the inventory module.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from heyyerp_api.business_rules import service as rules
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
    scope_clause,
)
from heyyerp_api.procurement.numbering import next_number

router = APIRouter(prefix="/inventory", tags=["inventory"])
issue_stock_permission = requires("inv.issue")
read = requires_module("inventory")
ReferenceType = Literal["WORK_ORDER", "SALES_ORDER", "DISPATCH", "OTHER"]
Qty = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)


class LineIn(Strict):
    item_id: UUID
    quantity: Decimal = Qty


class ReservationRef(Strict):
    reference_type: ReferenceType
    reference: str = Field(min_length=1, max_length=60)


class PlanIn(Strict):
    store_id: UUID
    lines: list[LineIn] = Field(min_length=1, max_length=50)


class IssueIn(PlanIn):
    purpose: str = Field(min_length=3, max_length=200)
    reference: str | None = Field(default=None, max_length=60)
    use_reservation: ReservationRef | None = None


class Pick(BaseModel):
    lot_id: UUID
    lot_no: str
    expiry_at: datetime | None
    received_at: datetime
    quantity: Decimal


class Blocked(BaseModel):
    lot_id: UUID
    lot_no: str
    expiry_at: datetime | None
    quantity: Decimal
    reason: str


class PlanLine(BaseModel):
    item_id: UUID
    item_code: str
    requested: Decimal
    picks: list[Pick]
    shortfall: Decimal
    blocked: list[Blocked]


class Plan(BaseModel):
    store_id: UUID
    rule: str
    can_issue: bool
    lines: list[PlanLine]


class IssuedLine(BaseModel):
    item_code: str
    lot_no: str
    quantity: Decimal
    cost: Decimal


class IssueSummary(BaseModel):
    issue_id: UUID
    issue_no: str
    unit_id: UUID
    store_id: UUID
    store_code: str
    purpose: str
    reference: str | None
    issued_by_name: str | None
    issued_at: datetime


class IssueOut(IssueSummary):
    total_cost: Decimal
    lines: list[IssuedLine]


class ReserveIn(Strict):
    store_id: UUID
    item_id: UUID
    quantity: Decimal = Qty
    reference_type: ReferenceType
    reference: str = Field(min_length=1, max_length=60)


class ReservationOut(BaseModel):
    reservation_id: UUID
    lot_id: UUID
    lot_no: str
    item_code: str
    store_code: str
    quantity: Decimal
    reference_type: str
    reference: str
    status: str
    created_at: datetime


# ------------------------------------------------------------------ the pure part: choosing lots
def order_key(rule: str, lot: dict[str, Any]) -> tuple[Any, ...]:
    if rule == "FIFO":
        return (lot["received_at"], lot["lot_no"])
    expiry = lot["expiry_at"]
    return (expiry is None, expiry or lot["received_at"], lot["received_at"], lot["lot_no"])


def choose(
    lots: list[dict[str, Any]], quantity: Decimal, rule: str
) -> tuple[list[dict[str, Any]], Decimal, list[dict[str, Any]]]:
    """Pick lots for a quantity: (picks, shortfall, blocked). Pure, so it is easy to test.

    Each lot dict needs lot_id, lot_no, status, expiry_at, received_at, expired and available.
    """
    usable = [lot for lot in lots if lot["status"] == "AVAILABLE" and not lot["expired"]
              and lot["available"] > 0]
    blocked = [lot for lot in lots if lot["status"] == "AVAILABLE" and lot["expired"]
               and lot["available"] > 0]
    picks: list[dict[str, Any]] = []
    left = quantity
    for lot in sorted(usable, key=lambda x: order_key(rule, x)):
        if left <= 0:
            break
        take = min(left, lot["available"])
        picks.append({**lot, "take": take})
        left -= take
    return picks, max(left, Decimal(0)), blocked


async def candidates(ctx: Ctx, store_id: UUID, item_id: UUID, lock: bool) -> list[dict[str, Any]]:
    """Lots with stock for an item in a store, locked in lot_id order when posting."""
    return await rows(
        ctx,
        "SELECT b.lot_id, t.lot_no, t.status, t.expiry_at, t.received_at, "
        "(t.expiry_at IS NOT NULL AND t.expiry_at <= now()) AS expired, "
        "(b.quantity_on_hand - b.quantity_reserved) AS available, b.unit_id "
        "FROM erp.stock_balance b JOIN erp.lot t ON t.lot_id = b.lot_id "
        "WHERE b.store_id = :s AND b.item_id = :i AND b.quantity_on_hand > 0 "
        f"ORDER BY b.lot_id{' FOR UPDATE OF b' if lock else ''}", {"s": store_id, "i": item_id})


async def store_in_scope(ctx: Ctx, store_id: UUID) -> dict[str, Any]:
    store = await one(ctx, "SELECT store_id, unit_id, code, active FROM erp.store "
                      "WHERE store_id = :id", {"id": store_id})
    if store is None or not ctx.principal.may_see_unit(store["unit_id"]):
        raise not_found("Store")
    return store


async def items_by_id(ctx: Ctx, ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Each item may appear only once")
    found = {r["item_id"]: r for r in await rows(
        ctx, "SELECT item_id, code, active FROM erp.item WHERE item_id = ANY(CAST(:ids AS uuid[]))",
        {"ids": ids})}
    for item_id in ids:
        if item_id not in found:
            raise HTTPException(422, "An item does not exist")
    return found


# ------------------------------------------------------------------ plan (preview, no changes)
@router.post("/issues/plan", response_model=Plan)
async def plan_issue(body: PlanIn, ctx: Ctx = Depends(issue_stock_permission)) -> Plan:
    """Show which lots an issue would take, what is short, and which expired lots are blocked."""
    await store_in_scope(ctx, body.store_id)
    rule = await rules.get_value(ctx, "issueRule")
    items = await items_by_id(ctx, [ln.item_id for ln in body.lines])
    lines: list[PlanLine] = []
    for line in body.lines:
        lots = await candidates(ctx, body.store_id, line.item_id, lock=False)
        picks, shortfall, blocked = choose(lots, line.quantity, rule)
        lines.append(PlanLine(
            item_id=line.item_id, item_code=items[line.item_id]["code"], requested=line.quantity,
            picks=[Pick(lot_id=p["lot_id"], lot_no=p["lot_no"], expiry_at=p["expiry_at"],
                        received_at=p["received_at"], quantity=p["take"]) for p in picks],
            shortfall=shortfall,
            blocked=[Blocked(lot_id=b["lot_id"], lot_no=b["lot_no"], expiry_at=b["expiry_at"],
                             quantity=b["available"], reason="Expired") for b in blocked]))
    return Plan(store_id=body.store_id, rule=rule, can_issue=all(ln.shortfall == 0 for ln in lines),
                lines=lines)


# ------------------------------------------------------------------ post an issue
async def issue(ctx: Ctx, body: IssueIn) -> UUID:
    """Take the stock. Everything or nothing: a shortfall on any line refuses the whole issue."""
    store = await store_in_scope(ctx, body.store_id)
    if not store["active"]:
        raise HTTPException(422, "The store is inactive")
    rule = await rules.get_value(ctx, "issueRule")
    wanted = sorted(body.lines, key=lambda ln: str(ln.item_id))  # fixed lock order
    items = await items_by_id(ctx, [ln.item_id for ln in wanted])
    number = await next_number(ctx, "issue")
    created = await one(
        ctx,
        "INSERT INTO erp.stock_issue(tenant_id, unit_id, store_id, issue_no, purpose, reference, "
        "issued_by) VALUES (erp.current_tenant_id(), :u, :s, :n, :p, :r, :me) RETURNING issue_id",
        {"u": store["unit_id"], "s": body.store_id, "n": number, "p": body.purpose.strip(),
         "r": body.reference, "me": ctx.principal.profile.app_user_id})
    assert created is not None
    issue_id: UUID = created["issue_id"]
    steps = 0

    async def take(lot_id: UUID, item_id: UUID, quantity: Decimal, from_reservation: bool) -> None:
        nonlocal steps
        steps += 1
        ledger_id = await ledger.move(
            ctx, unit_id=store["unit_id"], item_id=item_id, lot_id=lot_id,
            store_id=body.store_id, delta=-quantity, movement_type="ISSUE",
            reference_type="ISSUE", reference_id=issue_id,
            idempotency_key=f"issue:{issue_id}:{steps}",
            consumes_reservation=quantity if from_reservation else Decimal(0))
        await ledger.take_layers(ctx, unit_id=store["unit_id"], item_id=item_id,
                                 quantity=quantity, issue_ledger_id=ledger_id)

    for line in wanted:
        need = line.quantity
        item = items[line.item_id]
        if body.use_reservation is not None:
            held = await rows(
                ctx,
                "SELECT x.reservation_id, x.lot_id, x.quantity FROM erp.stock_reservation x "
                "JOIN erp.lot t ON t.lot_id = x.lot_id WHERE x.store_id = :s AND x.item_id = :i "
                "AND x.status = 'ACTIVE' AND x.reference_type = :rt AND x.reference = :r "
                "ORDER BY x.lot_id FOR UPDATE OF x",
                {"s": body.store_id, "i": line.item_id, "rt": body.use_reservation.reference_type,
                 "r": body.use_reservation.reference})
            for reservation in held:
                if need <= 0:
                    break
                used = min(need, reservation["quantity"])
                await take(reservation["lot_id"], line.item_id, used, True)
                left_on_hold = reservation["quantity"] - used
                await rows(
                    ctx, "UPDATE erp.stock_reservation SET quantity = :q, status = :st WHERE "
                    "reservation_id = :id RETURNING reservation_id",
                    {"q": left_on_hold if left_on_hold > 0 else reservation["quantity"],
                     "st": "ACTIVE" if left_on_hold > 0 else "CONSUMED",
                     "id": reservation["reservation_id"]})
                need -= used
        if need > 0:
            lots = await candidates(ctx, body.store_id, line.item_id, lock=True)
            picks, shortfall, blocked = choose(lots, need, rule)
            if shortfall > 0:
                free = sum((p["take"] for p in picks), Decimal(0))
                expired = sum((b["available"] for b in blocked), Decimal(0))
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"Not enough stock of {item['code']}: {free} can be issued, {need} needed"
                    + (f" ({expired} more is expired and blocked)" if expired else ""))
            for pick in picks:
                await take(pick["lot_id"], line.item_id, pick["take"], False)
    await audit(ctx, "stock.issue", "issue", issue_id,
                {"issue_no": number, "store_id": body.store_id, "purpose": body.purpose,
                 "lines": len(body.lines), "rule": rule})
    return issue_id


ISSUE_SQL = (
    "SELECT i.issue_id, i.issue_no, i.unit_id, i.store_id, s.code AS store_code, i.purpose, "
    "i.reference, u.display_name AS issued_by_name, i.issued_at FROM erp.stock_issue i "
    "JOIN erp.store s ON s.store_id = i.store_id LEFT JOIN erp.app_user u ON "
    "u.user_id = i.issued_by"
)


async def load_issue(ctx: Ctx, issue_id: UUID) -> IssueOut | None:
    clause, params = scope_clause(ctx, "i.unit_id")
    head = await one(ctx, f"{ISSUE_SQL} WHERE i.issue_id = :id{' AND ' + clause if clause else ''}",
                     {**params, "id": issue_id})
    if head is None:
        return None
    lines = await rows(
        ctx,
        "SELECT it.code AS item_code, t.lot_no, -l.qty_delta AS quantity, "
        "COALESCE((SELECT sum(c.extended_cost) FROM erp.cost_layer_consumption c WHERE "
        "c.issue_ledger_id = l.ledger_id), 0) AS cost FROM erp.stock_ledger l JOIN erp.lot t ON "
        "t.lot_id = l.lot_id JOIN erp.item it ON it.item_id = l.item_id WHERE "
        "l.reference_type = 'ISSUE' AND l.reference_id = :id ORDER BY l.occurred_at",
        {"id": issue_id})
    return IssueOut(**head, total_cost=sum((ln["cost"] for ln in lines), Decimal(0)),
                    lines=[IssuedLine(**ln) for ln in lines])


@router.post("/issues", response_model=IssueOut, status_code=status.HTTP_201_CREATED)
async def create_issue(body: IssueIn, ctx: Ctx = Depends(issue_stock_permission)) -> IssueOut:
    issue_id = await issue(ctx, body)
    result = await load_issue(ctx, issue_id)
    assert result is not None
    return result


@router.get("/issues", response_model=Page[IssueSummary])
async def list_issues(
    store_id: UUID | None = None, page: Paging = Depends(), ctx: Ctx = Depends(read)
) -> Page[IssueSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "i.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    if store_id is not None:
        conditions.append("i.store_id = :store")
        params["store"] = store_id
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{ISSUE_SQL}{where} ORDER BY i.issued_at DESC, i.issue_no DESC "
                       "LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.stock_issue i{where}", params)
    return Page(items=[IssueSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/issues/{issue_id}", response_model=IssueOut)
async def get_issue(issue_id: UUID, ctx: Ctx = Depends(read)) -> IssueOut:
    found = await load_issue(ctx, issue_id)
    if found is None:
        raise not_found("Issue")
    return found


# ------------------------------------------------------------------ reservations
RES_SQL = (
    "SELECT x.reservation_id, x.lot_id, t.lot_no, i.code AS item_code, s.code AS store_code, "
    "x.quantity, x.reference_type, x.reference, x.status, x.created_at FROM "
    "erp.stock_reservation x JOIN erp.lot t ON t.lot_id = x.lot_id JOIN erp.item i ON "
    "i.item_id = x.item_id JOIN erp.store s ON s.store_id = x.store_id"
)


@router.post("/reservations", response_model=list[ReservationOut],
             status_code=status.HTTP_201_CREATED)
async def reserve_stock(
    body: ReserveIn, ctx: Ctx = Depends(issue_stock_permission)
) -> list[ReservationOut]:
    """Hold stock for an order: lots are chosen by the issue rule; all or nothing."""
    store = await store_in_scope(ctx, body.store_id)
    rule = await rules.get_value(ctx, "issueRule")
    item = (await items_by_id(ctx, [body.item_id]))[body.item_id]
    lots = await candidates(ctx, body.store_id, body.item_id, lock=True)
    picks, shortfall, _ = choose(lots, body.quantity, rule)
    if shortfall > 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only {body.quantity - shortfall} of {item['code']} is free to reserve")
    made: list[UUID] = []
    for pick in picks:
        await ledger.reserve(ctx, pick["lot_id"], body.store_id, pick["take"])
        created = await one(
            ctx,
            "INSERT INTO erp.stock_reservation(tenant_id, unit_id, store_id, item_id, lot_id, "
            "quantity, reference_type, reference, created_by) VALUES (erp.current_tenant_id(), "
            ":u, :s, :i, :l, :q, :rt, :r, :me) RETURNING reservation_id",
            {"u": store["unit_id"], "s": body.store_id, "i": body.item_id, "l": pick["lot_id"],
             "q": pick["take"], "rt": body.reference_type, "r": body.reference,
             "me": ctx.principal.profile.app_user_id})
        assert created is not None
        made.append(created["reservation_id"])
    await audit(ctx, "stock.reserve", "reservation", made[0],
                {"reference_type": body.reference_type, "reference": body.reference,
                 "quantity": body.quantity, "lots": len(picks)})
    found = await rows(ctx, f"{RES_SQL} WHERE x.reservation_id = ANY(CAST(:ids AS uuid[])) "
                       "ORDER BY t.lot_no", {"ids": made})
    return [ReservationOut(**f) for f in found]


@router.get("/reservations", response_model=list[ReservationOut])
async def list_reservations(
    reference_type: ReferenceType | None = None,
    reference: str | None = None,
    reservation_status: str = Query("ACTIVE", alias="status"),
    ctx: Ctx = Depends(read),
) -> list[ReservationOut]:
    conditions = ["x.status = :status"]
    params: dict[str, Any] = {"status": reservation_status.upper()}
    clause, scope_params = scope_clause(ctx, "x.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for column, name, value in (("x.reference_type", "rt", reference_type),
                                ("x.reference", "ref", reference)):
        if value is not None:
            conditions.append(f"{column} = :{name}")
            params[name] = value
    found = await rows(ctx, f"{RES_SQL} WHERE {' AND '.join(conditions)} ORDER BY x.created_at, "
                       "t.lot_no", params)
    return [ReservationOut(**f) for f in found]


async def release(ctx: Ctx, where: str, params: dict[str, Any]) -> int:
    held = await rows(
        ctx, f"SELECT x.reservation_id, x.lot_id, x.store_id, x.quantity FROM "
        f"erp.stock_reservation x WHERE x.status = 'ACTIVE' AND {where} ORDER BY x.lot_id "
        "FOR UPDATE OF x", params)
    for reservation in held:
        await ledger.unreserve(ctx, reservation["lot_id"], reservation["store_id"],
                               reservation["quantity"])
        await rows(ctx, "UPDATE erp.stock_reservation SET status = 'RELEASED' WHERE "
                   "reservation_id = :id RETURNING reservation_id",
                   {"id": reservation["reservation_id"]})
    return len(held)


@router.delete("/reservations/{reservation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def release_one(
    reservation_id: UUID, ctx: Ctx = Depends(issue_stock_permission)
) -> None:
    clause, params = scope_clause(ctx, "x.unit_id")
    done = await release(ctx, "x.reservation_id = :id" + (f" AND {clause}" if clause else ""),
                         {**params, "id": reservation_id})
    if not done:
        raise not_found("Active reservation")
    await audit(ctx, "stock.release", "reservation", reservation_id, {})


@router.post("/reservations/release", response_model=dict[str, int])
async def release_reference(
    body: ReservationRef, ctx: Ctx = Depends(issue_stock_permission)
) -> dict[str, int]:
    """Release everything held for one order (for example when the order is cancelled)."""
    clause, params = scope_clause(ctx, "x.unit_id")
    done = await release(
        ctx, "x.reference_type = :rt AND x.reference = :r" + (f" AND {clause}" if clause else ""),
        {**params, "rt": body.reference_type, "r": body.reference})
    if done:
        await audit(ctx, "stock.release", "reservation", uuid4(),
                    {"reference_type": body.reference_type, "reference": body.reference,
                     "released": done})
    return {"released": done}

