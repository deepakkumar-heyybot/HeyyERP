"""Breakdown tickets, spares used and reliability (BRD FR-MNT-02, FR-MNT-03).

Any shop-floor user (production, operator, maintenance, plant head) can report a breakdown on a
machine; it is numbered BD-yy-nnnn and stays OPEN until maintenance closes it with the cause and the
action taken. Spares are issued from stores against the open ticket: only SPARE items, through the
normal issue (FEFO or FIFO lot, cost layers, month lock), and each lot used is recorded on the
ticket. A machine can have only one open breakdown. The reliability report gives, per machine over a
period, the breakdowns, the downtime, MTTR (mean time to repair) and MTBF (mean time between
failures, taken over the calendar hours of the period less the downtime). Reporting needs
mnt.breakdown; closing and issuing spares need mnt; reading needs the maintenance module.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from heyyerp_api.inventory.issues import IssueIn, LineIn, issue
from heyyerp_api.masters.common import (
    Ctx,
    Page,
    Paging,
    Strict,
    audit,
    count,
    not_found,
    one,
    require_in_scope,
    requires,
    requires_module,
    rows,
    scope_clause,
)
from heyyerp_api.procurement.numbering import next_number

router = APIRouter(prefix="/maintenance", tags=["maintenance"])
report = requires("mnt.breakdown")
manage = requires("mnt")
read = requires_module("maintenance")


class BreakdownIn(Strict):
    machine_id: UUID
    description: str = Field(min_length=3, max_length=500)
    started_at: datetime | None = None  # when it stopped; defaults to now


class CloseIn(Strict):
    cause: str = Field(min_length=3, max_length=500)
    corrective_action: str = Field(min_length=3, max_length=500)
    ended_at: datetime | None = None  # when it ran again; defaults to now


class SparesIn(Strict):
    store_id: UUID
    lines: list[LineIn] = Field(min_length=1, max_length=50)


class SpareUsed(BaseModel):
    item_id: UUID
    item_code: str
    lot_no: str
    quantity: Decimal


class BreakdownSummary(BaseModel):
    breakdown_id: UUID
    breakdown_no: str
    unit_id: UUID
    machine_id: UUID
    machine_code: str
    description: str | None
    reported_by_name: str | None
    started_at: datetime
    ended_at: datetime | None
    downtime_minutes: int
    status: str


class BreakdownOut(BreakdownSummary):
    cause: str | None
    corrective_action: str | None
    spares: list[SpareUsed]


SQL = (
    "SELECT b.breakdown_id, b.breakdown_no, b.unit_id, b.machine_id, m.code AS machine_code, "
    "b.description, u.display_name AS reported_by_name, b.started_at, b.ended_at, b.status, "
    "b.cause, b.corrective_action, GREATEST(0, floor(extract(epoch FROM (COALESCE(b.ended_at, "
    "now()) - b.started_at)) / 60))::int AS downtime_minutes FROM erp.breakdown b JOIN "
    "erp.machine m ON m.machine_id = b.machine_id LEFT JOIN erp.app_user u ON u.user_id = "
    "b.reported_by"
)


async def load(ctx: Ctx, breakdown_id: UUID) -> BreakdownOut:
    head = await one(ctx, f"{SQL} WHERE b.breakdown_id = :id", {"id": breakdown_id})
    if head is None:
        raise not_found("Breakdown")
    require_in_scope(ctx, head["unit_id"])
    spares = await rows(
        ctx, "SELECT s.item_id, i.code AS item_code, l.lot_no, s.quantity FROM erp.spare_issue s "
        "JOIN erp.item i ON i.item_id = s.item_id JOIN erp.lot l ON l.lot_id = s.lot_id WHERE "
        "s.breakdown_id = :id ORDER BY i.code, l.lot_no", {"id": breakdown_id})
    return BreakdownOut(**head, spares=[SpareUsed(**s) for s in spares])


async def lock_open(ctx: Ctx, breakdown_id: UUID) -> dict[str, Any]:
    head = await one(ctx, "SELECT breakdown_id, breakdown_no, unit_id, status, started_at FROM "
                     "erp.breakdown WHERE breakdown_id = :id FOR UPDATE", {"id": breakdown_id})
    if head is None:
        raise not_found("Breakdown")
    require_in_scope(ctx, head["unit_id"])
    if head["status"] != "OPEN":
        raise HTTPException(status.HTTP_409_CONFLICT, "The breakdown is already closed")
    return head


@router.post("/breakdowns", response_model=BreakdownOut, status_code=status.HTTP_201_CREATED)
async def report_breakdown(body: BreakdownIn, ctx: Ctx = Depends(report)) -> BreakdownOut:
    machine = await one(ctx, "SELECT unit_id, active FROM erp.machine WHERE machine_id = :id "
                        "FOR UPDATE", {"id": body.machine_id})
    if machine is None:
        raise HTTPException(422, "The machine does not exist")
    require_in_scope(ctx, machine["unit_id"])
    if not machine["active"]:
        raise HTTPException(422, "The machine is inactive")
    started = body.started_at or datetime.now(UTC)
    if started > datetime.now(UTC):
        raise HTTPException(422, "The breakdown cannot start in the future")
    if await one(ctx, "SELECT 1 AS x FROM erp.breakdown WHERE machine_id = :m AND status = 'OPEN'",
                 {"m": body.machine_id}):
        raise HTTPException(status.HTTP_409_CONFLICT, "This machine already has an open breakdown")
    number = await next_number(ctx, "breakdown")
    created = await one(
        ctx, "INSERT INTO erp.breakdown(tenant_id, unit_id, machine_id, reported_by, breakdown_no, "
        "description, started_at) VALUES (erp.current_tenant_id(), :u, :m, :me, :n, :d, :s) "
        "RETURNING breakdown_id",
        {"u": machine["unit_id"], "m": body.machine_id, "me": ctx.principal.profile.app_user_id,
         "n": number, "d": body.description, "s": started})
    assert created is not None
    await audit(ctx, "breakdown.report", "breakdown", created["breakdown_id"],
                {"breakdown_no": number, "machine_id": body.machine_id})
    return await load(ctx, created["breakdown_id"])


@router.post("/breakdowns/{breakdown_id}/spares", response_model=BreakdownOut)
async def use_spares(
    breakdown_id: UUID, body: SparesIn, ctx: Ctx = Depends(manage)
) -> BreakdownOut:
    """Issue spares from stores against the open ticket (SPARE items only)."""
    head = await lock_open(ctx, breakdown_id)
    store = await one(ctx, "SELECT unit_id FROM erp.store WHERE store_id = :id",
                      {"id": body.store_id})
    if store is None or store["unit_id"] != head["unit_id"]:
        raise HTTPException(422, "The store must be a store of the machine's plant")
    kinds = await rows(ctx, "SELECT item_id, item_type FROM erp.item WHERE item_id = "
                       "ANY(CAST(:ids AS uuid[]))", {"ids": [ln.item_id for ln in body.lines]})
    if len(kinds) != len({ln.item_id for ln in body.lines}) or any(
            k["item_type"] != "SPARE" for k in kinds):
        raise HTTPException(422, "Only spare parts (item type SPARE) can be issued to a breakdown")
    issue_id = await issue(ctx, IssueIn(
        store_id=body.store_id, lines=body.lines, purpose=f"Breakdown {head['breakdown_no']}",
        reference=head["breakdown_no"]))
    for moved in await rows(
            ctx, "SELECT ledger_id, item_id, lot_id, -qty_delta AS quantity FROM erp.stock_ledger "
            "WHERE reference_type = 'ISSUE' AND reference_id = :id ORDER BY ledger_id",
            {"id": issue_id}):
        await rows(
            ctx, "INSERT INTO erp.spare_issue(tenant_id, breakdown_id, item_id, lot_id, "
            "stock_ledger_id, quantity) VALUES (erp.current_tenant_id(), :b, :i, :l, :s, :q) "
            "RETURNING spare_issue_id",
            {"b": breakdown_id, "i": moved["item_id"], "l": moved["lot_id"],
             "s": moved["ledger_id"], "q": moved["quantity"]})
    await audit(ctx, "breakdown.spares", "breakdown", breakdown_id,
                {"issue_id": issue_id, "lines": len(body.lines)})
    return await load(ctx, breakdown_id)


@router.post("/breakdowns/{breakdown_id}/close", response_model=BreakdownOut)
async def close_breakdown(
    breakdown_id: UUID, body: CloseIn, ctx: Ctx = Depends(manage)
) -> BreakdownOut:
    head = await lock_open(ctx, breakdown_id)
    ended = body.ended_at or datetime.now(UTC)
    if ended > datetime.now(UTC):
        raise HTTPException(422, "The machine cannot have restarted in the future")
    if ended < head["started_at"]:
        raise HTTPException(422, "The end is before the start of the breakdown")
    await rows(
        ctx, "UPDATE erp.breakdown SET status = 'CLOSED', ended_at = :e, cause = :c, "
        "corrective_action = :a, closed_by = :me WHERE breakdown_id = :id RETURNING breakdown_id",
        {"e": ended, "c": body.cause, "a": body.corrective_action,
         "me": ctx.principal.profile.app_user_id, "id": breakdown_id})
    await audit(ctx, "breakdown.close", "breakdown", breakdown_id,
                {"cause": body.cause, "corrective_action": body.corrective_action})
    return await load(ctx, breakdown_id)


@router.get("/breakdowns", response_model=Page[BreakdownSummary])
async def list_breakdowns(
    unit_id: UUID | None = None, machine_id: UUID | None = None,
    breakdown_status: str | None = Query(None, alias="status"), page: Paging = Depends(),
    ctx: Ctx = Depends(read),
) -> Page[BreakdownSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "b.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for text_, key, value in (
            ("b.unit_id = :u", "u", unit_id), ("b.machine_id = :m", "m", machine_id),
            ("b.status = :s", "s", breakdown_status.upper() if breakdown_status else None)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    data = await rows(ctx, f"{SQL}{where} ORDER BY b.started_at DESC LIMIT :limit OFFSET :offset",
                      {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.breakdown b{where}", params)
    return Page(items=[BreakdownSummary(**d) for d in data], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/breakdowns/{breakdown_id}", response_model=BreakdownOut)
async def get_breakdown(breakdown_id: UUID, ctx: Ctx = Depends(read)) -> BreakdownOut:
    return await load(ctx, breakdown_id)


# ------------------------------------------------------------------ MTTR and MTBF
class MachineReliability(BaseModel):
    machine_id: UUID
    machine_code: str
    breakdowns: int
    downtime_hours: float
    mttr_hours: float | None
    mtbf_hours: float | None


class Reliability(BaseModel):
    unit_id: UUID
    date_from: date
    date_to: date
    window_hours: float
    machines: list[MachineReliability]


@router.get("/reliability", response_model=Reliability)
async def reliability(
    unit_id: UUID, date_from: date | None = None, date_to: date | None = None,
    ctx: Ctx = Depends(read),
) -> Reliability:
    """Per machine: breakdowns, downtime, MTTR and MTBF over the period (default last 90 days).

    A breakdown counts in the period it started in; its downtime is the part inside the period.
    MTBF is the period's calendar hours less downtime, divided by the breakdowns (machines with no
    breakdown have no MTBF: they did not fail).
    """
    require_in_scope(ctx, unit_id)
    end = date_to or date.today()
    start = date_from or end - timedelta(days=90)
    if end < start:
        raise HTTPException(422, "The end date is before the start date")
    first = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    after = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    window = (after - first).total_seconds() / 3600
    data = await rows(
        ctx, "SELECT m.machine_id, m.code AS machine_code, count(b.breakdown_id) AS breakdowns, "
        "COALESCE(sum(extract(epoch FROM (LEAST(COALESCE(b.ended_at, now()), :b) - GREATEST("
        "b.started_at, :a))) / 3600), 0) AS downtime FROM erp.machine m LEFT JOIN erp.breakdown b "
        "ON b.machine_id = m.machine_id AND b.started_at >= :a AND b.started_at < :b WHERE "
        "m.unit_id = :u AND m.active GROUP BY m.machine_id, m.code ORDER BY m.code",
        {"u": unit_id, "a": first, "b": after})
    out = []
    for d in data:
        n, down = d["breakdowns"], float(d["downtime"])
        out.append(MachineReliability(
            machine_id=d["machine_id"], machine_code=d["machine_code"], breakdowns=n,
            downtime_hours=round(down, 2), mttr_hours=round(down / n, 2) if n else None,
            mtbf_hours=round((window - down) / n, 2) if n else None))
    return Reliability(unit_id=unit_id, date_from=start, date_to=end,
                       window_hours=round(window, 2), machines=out)
