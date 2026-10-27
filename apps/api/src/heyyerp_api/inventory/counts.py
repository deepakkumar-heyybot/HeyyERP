"""Cycle counts: count what is on the shelf, approve the differences, then correct the books
(BRD FR-INV-06, approval type `count`).

Starting a count freezes a snapshot of every lot with stock in the store (system quantity). The
counter enters the quantity found on each line; submitting needs every line counted. A count with
no differences is posted at once; otherwise it goes for approval and the stock is corrected only
when it is approved (COUNT_ADJUST movements). A rejected count becomes RECOUNT and a fresh count of
the same store can be started from it. Needs inv.count; reading needs the inventory module.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from heyyerp_api.inventory import ledger
from heyyerp_api.inventory.issues import store_in_scope
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
from heyyerp_api.workflow import engine
from heyyerp_api.workflow.registry import DocInfo, DocumentType, register

router = APIRouter(prefix="/inventory", tags=["inventory"])
write = requires("inv.count")
read = requires_module("inventory")


class CountIn(Strict):
    store_id: UUID


class CountedLine(Strict):
    count_line_id: UUID
    counted_qty: Decimal = Field(ge=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)


class CountedIn(Strict):
    lines: list[CountedLine] = Field(min_length=1, max_length=2000)


class CountLine(BaseModel):
    count_line_id: UUID
    item_id: UUID
    item_code: str
    lot_id: UUID
    lot_no: str
    system_qty: Decimal
    counted_qty: Decimal | None  # None until somebody counts the line
    variance: Decimal | None
    counted_at: datetime | None


class CountSummary(BaseModel):
    count_id: UUID
    count_no: str
    unit_id: UUID
    store_id: UUID
    store_code: str
    period_start: date
    status: str
    started_at: datetime
    submitted_by_name: str | None


class CountOut(CountSummary):
    lines: list[CountLine]


COUNT_SQL = (
    "SELECT c.count_id, c.count_no, c.unit_id, c.store_id, s.code AS store_code, c.period_start, "
    "c.status, c.started_at, u.display_name AS submitted_by_name FROM erp.stock_count c JOIN "
    "erp.store s ON s.store_id = c.store_id LEFT JOIN erp.app_user u ON u.user_id = c.submitted_by"
)


async def load(ctx: Ctx, count_id: UUID) -> CountOut | None:
    clause, params = scope_clause(ctx, "c.unit_id")
    head = await one(ctx, f"{COUNT_SQL} WHERE c.count_id = :id{' AND ' + clause if clause else ''}",
                     {**params, "id": count_id})
    if head is None:
        return None
    lines = await rows(
        ctx,
        "SELECT l.count_line_id, l.item_id, i.code AS item_code, l.lot_id, t.lot_no, l.system_qty, "
        "CASE WHEN l.counted_at IS NULL THEN NULL ELSE l.counted_qty END AS counted_qty, "
        "CASE WHEN l.counted_at IS NULL THEN NULL ELSE l.variance END AS variance, l.counted_at "
        "FROM erp.stock_count_line l JOIN erp.item i ON i.item_id = l.item_id JOIN erp.lot t ON "
        "t.lot_id = l.lot_id WHERE l.count_id = :id ORDER BY i.code, t.lot_no", {"id": count_id})
    return CountOut(**head, lines=[CountLine(**ln) for ln in lines])


async def start(ctx: Ctx, store_id: UUID) -> UUID:
    store = await store_in_scope(ctx, store_id)
    if not store["active"]:
        raise HTTPException(422, "The store is inactive")
    if await one(ctx, "SELECT 1 AS x FROM erp.stock_count WHERE store_id = :s AND status IN "
                 "('DRAFT', 'PENDING_APPROVAL')", {"s": store_id}):
        raise HTTPException(status.HTTP_409_CONFLICT, "This store already has a count in progress")
    number = await next_number(ctx, "count")
    created = await one(
        ctx, "INSERT INTO erp.stock_count(tenant_id, unit_id, store_id, count_no, period_start) "
        "VALUES (erp.current_tenant_id(), :u, :s, :n, CURRENT_DATE) RETURNING count_id",
        {"u": store["unit_id"], "s": store_id, "n": number})
    assert created is not None
    await rows(
        ctx,
        "INSERT INTO erp.stock_count_line(tenant_id, count_id, item_id, lot_id, system_qty, "
        "counted_qty) SELECT erp.current_tenant_id(), :c, b.item_id, b.lot_id, b.quantity_on_hand, "
        "0 FROM erp.stock_balance b WHERE b.store_id = :s AND b.quantity_on_hand > 0 "
        "RETURNING count_line_id", {"c": created["count_id"], "s": store_id})
    await audit(ctx, "count.start", "count", created["count_id"], {"count_no": number})
    return created["count_id"]  # type: ignore[no-any-return]


async def require_count(ctx: Ctx, count_id: UUID, *allowed: str) -> dict[str, Any]:
    clause, params = scope_clause(ctx, "c.unit_id")
    head = await one(
        ctx, f"{COUNT_SQL} WHERE c.count_id = :id{' AND ' + clause if clause else ''} "
        "FOR UPDATE OF c", {**params, "id": count_id})
    if head is None:
        raise not_found("Count")
    if head["status"] not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The count is {str(head['status']).lower().replace('_', ' ')}; this needs it to be "
            f"{' or '.join(a.lower().replace('_', ' ') for a in allowed)}")
    return head


@router.post("/counts", response_model=CountOut, status_code=status.HTTP_201_CREATED)
async def start_count(body: CountIn, ctx: Ctx = Depends(write)) -> CountOut:
    result = await load(ctx, await start(ctx, body.store_id))
    assert result is not None
    return result


@router.get("/counts", response_model=Page[CountSummary])
async def list_counts(
    status_: str | None = Query(None, alias="status"), store_id: UUID | None = None,
    page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[CountSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "c.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    if status_:
        conditions.append("c.status = :status")
        params["status"] = status_.upper()
    if store_id is not None:
        conditions.append("c.store_id = :store")
        params["store"] = store_id
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{COUNT_SQL}{where} ORDER BY c.count_no DESC LIMIT :limit "
                       "OFFSET :offset", {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.stock_count c{where}", params)
    return Page(items=[CountSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/counts/{count_id}", response_model=CountOut)
async def get_count(count_id: UUID, ctx: Ctx = Depends(read)) -> CountOut:
    found = await load(ctx, count_id)
    if found is None:
        raise not_found("Count")
    return found


@router.put("/counts/{count_id}/lines", response_model=CountOut)
async def enter_counts(count_id: UUID, body: CountedIn, ctx: Ctx = Depends(write)) -> CountOut:
    await require_count(ctx, count_id, "DRAFT")
    ids = [ln.count_line_id for ln in body.lines]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Each line may appear only once")
    for line in body.lines:
        done = await rows(
            ctx, "UPDATE erp.stock_count_line SET counted_qty = :q, counted_at = now() WHERE "
            "count_line_id = :l AND count_id = :c RETURNING count_line_id",
            {"q": line.counted_qty, "l": line.count_line_id, "c": count_id})
        if not done:
            raise HTTPException(422, "A line does not belong to this count")
    result = await load(ctx, count_id)
    assert result is not None
    return result


@router.post("/counts/{count_id}/cancel", response_model=CountOut)
async def cancel_count(count_id: UUID, ctx: Ctx = Depends(write)) -> CountOut:
    await require_count(ctx, count_id, "DRAFT")
    await rows(ctx, "UPDATE erp.stock_count SET status = 'CANCELLED' WHERE count_id = :id "
               "RETURNING count_id", {"id": count_id})
    await audit(ctx, "count.cancel", "count", count_id, {})
    result = await load(ctx, count_id)
    assert result is not None
    return result


@router.post("/counts/{count_id}/recount", response_model=CountOut,
             status_code=status.HTTP_201_CREATED)
async def recount(count_id: UUID, ctx: Ctx = Depends(write)) -> CountOut:
    """After a rejection: start a new count of the same store from today's stock."""
    old = await require_count(ctx, count_id, "RECOUNT")
    result = await load(ctx, await start(ctx, old["store_id"]))
    assert result is not None
    return result


@router.post("/counts/{count_id}/submit", response_model=CountOut)
async def submit_count(count_id: UUID, ctx: Ctx = Depends(write)) -> CountOut:
    head = await require_count(ctx, count_id, "DRAFT")
    uncounted = await count(
        ctx, "SELECT count(*) FROM erp.stock_count_line WHERE count_id = :id AND "
        "counted_at IS NULL", {"id": count_id})
    if uncounted:
        raise HTTPException(422, f"{uncounted} line(s) have not been counted yet")
    differences = await count(
        ctx, "SELECT count(*) FROM erp.stock_count_line WHERE count_id = :id AND variance <> 0",
        {"id": count_id})
    await rows(ctx, "UPDATE erp.stock_count SET submitted_by = :me, status = 'PENDING_APPROVAL' "
               "WHERE count_id = :id RETURNING count_id",
               {"me": ctx.principal.profile.app_user_id, "id": count_id})
    if differences == 0:
        await post_adjustments(ctx, count_id)  # nothing to approve: the books were right
    else:
        approval_id = await engine.submit(ctx, "count", count_id)
        await audit(ctx, "count.submit", "count", count_id,
                    {"count_no": head["count_no"], "differences": differences,
                     "approval_id": approval_id})
    result = await load(ctx, count_id)
    assert result is not None
    return result


# ------------------------------------------------------------------ posting and approval adapter
async def post_adjustments(ctx: Ctx, count_id: UUID) -> None:
    """Correct the books by each line's difference. Surplus opens a layer at the last cost."""
    head = await one(ctx, "SELECT unit_id, store_id, count_no FROM erp.stock_count WHERE "
                     "count_id = :id", {"id": count_id})
    assert head is not None
    lines = await rows(
        ctx, "SELECT count_line_id, item_id, lot_id, variance FROM erp.stock_count_line "
        "WHERE count_id = :id AND variance <> 0 ORDER BY lot_id", {"id": count_id})
    for line in lines:
        delta: Decimal = line["variance"]
        ledger_id = await ledger.move(
            ctx, unit_id=head["unit_id"], item_id=line["item_id"], lot_id=line["lot_id"],
            store_id=head["store_id"], delta=delta, movement_type="COUNT_ADJUST",
            reference_type="COUNT", reference_id=count_id,
            idempotency_key=f"count:{count_id}:{line['count_line_id']}")
        if delta < 0:
            await ledger.take_layers(ctx, unit_id=head["unit_id"], item_id=line["item_id"],
                                     quantity=-delta, issue_ledger_id=ledger_id)
        else:
            cost = await ledger.last_cost(ctx, head["unit_id"], line["item_id"])
            await ledger.add_layer(ctx, ledger_id=ledger_id, item_id=line["item_id"],
                                   lot_id=line["lot_id"], quantity=delta,
                                   unit_cost=cost or Decimal(0))
    await rows(ctx, "UPDATE erp.stock_count SET status = 'POSTED' WHERE count_id = :id "
               "RETURNING count_id", {"id": count_id})
    await audit(ctx, "count.post", "count", count_id,
                {"count_no": head["count_no"], "adjusted_lots": len(lines)})


async def load_for_approval(ctx: Ctx, count_id: UUID) -> DocInfo | None:
    head = await one(ctx, "SELECT count_no, status FROM erp.stock_count WHERE count_id = :id",
                     {"id": count_id})
    if head is None:
        return None
    return DocInfo(
        title=f"Stock count {head['count_no']}", amount=None, department_id=None,
        submittable=head["status"] == "PENDING_APPROVAL",
        reason=f"The count is {str(head['status']).lower().replace('_', ' ')}")


async def finalize_count(ctx: Ctx, count_id: UUID, approved: bool) -> None:
    if approved:
        await post_adjustments(ctx, count_id)
    else:
        await rows(ctx, "UPDATE erp.stock_count SET status = 'RECOUNT' WHERE count_id = :id "
                   "RETURNING count_id", {"id": count_id})


register(DocumentType("count", "inv.count", load_for_approval, finalize_count))
