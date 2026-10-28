"""Fiscal periods and the month-end lock (BRD FR-FIN-05, BR-09).

Every plant has one period per calendar month, created when first needed. Accounting postings
(journals) are refused for a month once its lock date has passed (the rule `lockDay`; 0 means the
last day of the month) or once the period has been closed by hand. Production postings stay open
`prodLockExtraDays` longer (default 1, so up to day 1 of the next month). Nobody may post into a
closed period: a closed month is reopened by an administrator, who is then answerable for it.
Closing needs fin.voucher and refuses while draft journals of the month exist; reopening needs the
admin permission. Stock movements are also refused while the current month of their plant is closed.
"""

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from heyyerp_api.business_rules import service as rules
from heyyerp_api.masters.common import (
    Ctx,
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

router = APIRouter(prefix="/finance", tags=["finance"])
manage = requires("fin.voucher")
admin = requires("admin")
read = requires_module("finance")


def month_start(day: date) -> date:
    return day.replace(day=1)


def last_open_day(work_date: date, lock_day: int, extra_days: int) -> date:
    """The last day on which the month of work_date may still be posted (plus extra days)."""
    last = monthrange(work_date.year, work_date.month)[1]
    lock = date(work_date.year, work_date.month, last if lock_day == 0 else min(lock_day, last))
    return lock + timedelta(days=extra_days)


async def get_period(ctx: Ctx, unit_id: UUID, day: date) -> dict[str, Any]:
    """The plant's period for the month of `day`, created open if it does not exist yet."""
    month = month_start(day)
    await rows(
        ctx, "INSERT INTO erp.fiscal_period(tenant_id, unit_id, year_month) VALUES "
        "(erp.current_tenant_id(), :u, :m) ON CONFLICT (unit_id, year_month) DO NOTHING "
        "RETURNING period_id", {"u": unit_id, "m": month})
    period = await one(ctx, "SELECT period_id, unit_id, year_month, status, closed_at FROM "
                       "erp.fiscal_period WHERE unit_id = :u AND year_month = :m",
                       {"u": unit_id, "m": month})
    assert period is not None
    return period


async def ensure_accounting_open(ctx: Ctx, unit_id: UUID, day: date) -> dict[str, Any]:
    """Refuse a posting dated `day` when its month is closed or past its lock date."""
    period = await get_period(ctx, unit_id, day)
    if period["status"] == "CLOSED":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The period {period['year_month']:%B %Y} is closed")
    closes = last_open_day(day, await rules.get_value(ctx, "lockDay"), 0)
    if date.today() > closes:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Postings for {day:%B %Y} were locked on {closes}; nobody may post into a "
            "closed month")
    return period


async def ensure_stock_open(ctx: Ctx, unit_id: UUID) -> None:
    """Stock moves are dated now: refuse them while this month of the plant has been closed."""
    closed = await one(
        ctx, "SELECT 1 AS x FROM erp.fiscal_period WHERE unit_id = :u AND year_month = "
        "date_trunc('month', CURRENT_DATE)::date AND status = 'CLOSED'", {"u": unit_id})
    if closed is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This month is closed for the plant; stock cannot move until it is "
                            "reopened by an administrator")


# ------------------------------------------------------------------ the periods themselves
class PeriodOut(BaseModel):
    period_id: UUID
    unit_id: UUID
    year_month: date
    status: str
    closed_at: datetime | None
    closed_by_name: str | None
    lock_date: date
    locked_by_date: bool  # the lock date has passed even if nobody closed the month by hand
    draft_journals: int


class MonthIn(Strict):
    unit_id: UUID
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM")


PERIOD_SQL = (
    "SELECT p.period_id, p.unit_id, p.year_month, p.status, p.closed_at, u.display_name AS "
    "closed_by_name FROM erp.fiscal_period p LEFT JOIN erp.app_user u ON u.user_id = p.closed_by"
)


async def describe(ctx: Ctx, row: dict[str, Any]) -> PeriodOut:
    lock = last_open_day(row["year_month"], await rules.get_value(ctx, "lockDay"), 0)
    drafts = await count(
        ctx, "SELECT count(*) FROM erp.journal WHERE unit_id = :u AND status = 'DRAFT' AND "
        "date_trunc('month', journal_date) = :m", {"u": row["unit_id"], "m": row["year_month"]})
    return PeriodOut(**row, lock_date=lock, locked_by_date=date.today() > lock,
                     draft_journals=drafts)


@router.get("/periods", response_model=list[PeriodOut])
async def list_periods(
    unit_id: UUID | None = None, period_status: str | None = Query(None, alias="status"),
    ctx: Ctx = Depends(read),
) -> list[PeriodOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "p.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    if unit_id is not None:
        conditions.append("p.unit_id = :unit")
        params["unit"] = unit_id
    if period_status:
        conditions.append("p.status = :st")
        params["st"] = period_status.upper()
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    found = await rows(ctx, f"{PERIOD_SQL}{where} ORDER BY p.year_month DESC, p.unit_id LIMIT 240",
                       params)
    return [await describe(ctx, r) for r in found]


async def locate(ctx: Ctx, body: MonthIn) -> dict[str, Any]:
    require_in_scope(ctx, body.unit_id)
    year, month = (int(x) for x in body.month.split("-"))
    unit = await one(ctx, "SELECT 1 AS x FROM erp.unit WHERE unit_id = :id", {"id": body.unit_id})
    if unit is None:
        raise not_found("Plant")
    period = await get_period(ctx, body.unit_id, date(year, month, 1))
    locked = await one(ctx, f"{PERIOD_SQL} WHERE p.period_id = :id FOR UPDATE OF p",
                       {"id": period["period_id"]})
    assert locked is not None
    return locked


@router.post("/periods/close", response_model=PeriodOut)
async def close_period(body: MonthIn, ctx: Ctx = Depends(manage)) -> PeriodOut:
    row = await locate(ctx, body)
    if row["status"] == "CLOSED":
        raise HTTPException(status.HTTP_409_CONFLICT, "The period is already closed")
    if row["year_month"] > month_start(date.today()):
        raise HTTPException(422, "A future month cannot be closed")
    drafts = await count(
        ctx, "SELECT count(*) FROM erp.journal WHERE unit_id = :u AND status = 'DRAFT' AND "
        "date_trunc('month', journal_date) = :m", {"u": row["unit_id"], "m": row["year_month"]})
    if drafts:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{drafts} draft journal(s) of that month must be posted or "
                            "deleted first")
    await rows(ctx, "UPDATE erp.fiscal_period SET status = 'CLOSED', closed_at = now(), "
               "closed_by = :me WHERE period_id = :id RETURNING period_id",
               {"me": ctx.principal.profile.app_user_id, "id": row["period_id"]})
    await audit(ctx, "period.close", "fiscal_period", row["period_id"],
                {"unit_id": row["unit_id"], "month": body.month})
    fresh = await one(ctx, f"{PERIOD_SQL} WHERE p.period_id = :id", {"id": row["period_id"]})
    assert fresh is not None
    return await describe(ctx, fresh)


@router.post("/periods/reopen", response_model=PeriodOut)
async def reopen_period(body: MonthIn, ctx: Ctx = Depends(admin)) -> PeriodOut:
    """Only an administrator reopens a closed month; the audit log records who and when."""
    row = await locate(ctx, body)
    if row["status"] != "CLOSED":
        raise HTTPException(status.HTTP_409_CONFLICT, "The period is not closed")
    await rows(ctx, "UPDATE erp.fiscal_period SET status = 'OPEN', closed_at = NULL, closed_by = "
               "NULL WHERE period_id = :id RETURNING period_id", {"id": row["period_id"]})
    await audit(ctx, "period.reopen", "fiscal_period", row["period_id"],
                {"unit_id": row["unit_id"], "month": body.month})
    fresh = await one(ctx, f"{PERIOD_SQL} WHERE p.period_id = :id", {"id": row["period_id"]})
    assert fresh is not None
    return await describe(ctx, fresh)
