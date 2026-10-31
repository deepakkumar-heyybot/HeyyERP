"""Leave balances and applications with department-head approval (BRD FR-HR-02).

Applying creates the request and sends it for approval at once (rule R-LEAVE: the department's
head). Days are counted from the dates: Sundays are left out unless the company rule
`leaveCountSundays` is on, and a single day can be a half day. A balance is created the first time
it is needed with the type's yearly default; the leave year starts in the month given by the rule
`leaveYearStartMonth` and a leave cannot span two leave years. Pending days are held against the
balance so two applications cannot overdraw it; an unpaid type needs no balance. Approval adds the
days to those used; a rejected request frees them. A pending request can be withdrawn, an approved
one cancelled until it starts, which gives the days back. Overlapping leave is refused. The
employee applies for themselves (leave.apply); HR (hr.manage) can apply on someone's behalf,
adjust balances and see everyone's leave in their plants; managers see their team's.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from heyyerp_api.business_rules import service as rules
from heyyerp_api.hr.employees import apply_leave, manage, may_view, my_employee, read
from heyyerp_api.masters.common import (
    Ctx,
    Page,
    Paging,
    Strict,
    audit,
    count,
    not_found,
    one,
    rows,
)
from heyyerp_api.security import has_permission
from heyyerp_api.workflow import engine
from heyyerp_api.workflow.registry import DocInfo, DocumentType, register

router = APIRouter(prefix="/hr", tags=["hr"])
ZERO = Decimal(0)
HALF = Decimal("0.5")


class LeaveIn(Strict):
    employee_id: UUID | None = None  # default: the signed-in user's own employee record
    leave_type_id: UUID
    start_date: date
    end_date: date
    half_day: bool = False
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def check(self) -> "LeaveIn":
        if self.end_date < self.start_date:
            raise ValueError("The leave ends before it starts")
        if self.half_day and self.start_date != self.end_date:
            raise ValueError("A half day is a single day")
        return self


class AdjustIn(Strict):
    employee_id: UUID
    leave_type_id: UUID
    leave_year: int = Field(ge=2000, le=2100)
    days: Decimal = Field(max_digits=8, decimal_places=2)  # plus gives days, minus takes them
    reason: str = Field(min_length=3, max_length=200)


class LeaveOut(BaseModel):
    leave_request_id: UUID
    employee_id: UUID
    employee_no: str
    employee_name: str
    leave_type_id: UUID
    leave_type_code: str
    start_date: date
    end_date: date
    requested_days: Decimal
    half_day: bool
    reason: str | None
    status: str
    approval_id: UUID | None


class BalanceOut(BaseModel):
    leave_type_id: UUID
    leave_type_code: str
    leave_year: int
    opening_days: Decimal
    accrued_days: Decimal
    adjusted_days: Decimal
    used_days: Decimal
    pending_days: Decimal
    available_days: Decimal


LEAVE_SQL = (
    "SELECT l.leave_request_id, l.employee_id, e.employee_no, e.full_name AS employee_name, "
    "l.leave_type_id, t.code AS leave_type_code, l.start_date, l.end_date, l.requested_days, "
    "l.half_day, l.reason, l.status, (SELECT a.approval_id FROM erp.approval_instance a WHERE "
    "a.document_type = 'leave' AND a.document_id = l.leave_request_id) AS approval_id FROM "
    "erp.leave_request l JOIN erp.employee e ON e.employee_id = l.employee_id JOIN "
    "erp.leave_type t ON t.leave_type_id = l.leave_type_id"
)


def leave_year(day: date, start_month: int) -> int:
    """The leave year a day falls in, named by the calendar year it starts in."""
    return day.year if day.month >= start_month else day.year - 1


def count_days(start: date, end: date, half: bool, sundays: bool) -> Decimal:
    days = sum(1 for i in range((end - start).days + 1)
               if sundays or (start + timedelta(days=i)).weekday() != 6)
    return HALF if half and days else Decimal(days)


async def ensure_balance(ctx: Ctx, employee_id: UUID, type_id: UUID, year: int) -> dict[str, Any]:
    await rows(
        ctx, "INSERT INTO erp.leave_balance(tenant_id, employee_id, leave_type_id, fiscal_year, "
        "accrued_days) SELECT erp.current_tenant_id(), :e, :t, :y, annual_default_days FROM "
        "erp.leave_type WHERE leave_type_id = :t ON CONFLICT (employee_id, leave_type_id, "
        "fiscal_year) DO NOTHING RETURNING employee_id",
        {"e": employee_id, "t": type_id, "y": year})
    row = await one(
        ctx, "SELECT opening_days, accrued_days, adjusted_days, used_days FROM erp.leave_balance "
        "WHERE employee_id = :e AND leave_type_id = :t AND fiscal_year = :y FOR UPDATE",
        {"e": employee_id, "t": type_id, "y": year})
    assert row is not None
    return row


async def pending_days(ctx: Ctx, employee_id: UUID, type_id: UUID, year: int, start_month: int,
                       ) -> Decimal:
    found = await one(
        ctx, "SELECT COALESCE(sum(requested_days), 0) AS d FROM erp.leave_request WHERE "
        "employee_id = :e AND leave_type_id = :t AND status = 'PENDING_APPROVAL' AND "
        "CASE WHEN EXTRACT(month FROM start_date) >= :m THEN EXTRACT(year FROM start_date) "
        "ELSE EXTRACT(year FROM start_date) - 1 END = :y",
        {"e": employee_id, "t": type_id, "y": year, "m": start_month})
    assert found is not None
    return found["d"]  # type: ignore[no-any-return]


async def load(ctx: Ctx, request_id: UUID) -> LeaveOut:
    row = await one(ctx, f"{LEAVE_SQL} WHERE l.leave_request_id = :id", {"id": request_id})
    if row is None:
        raise not_found("Leave request")
    employee = await one(ctx, "SELECT employee_id, unit_id FROM erp.employee WHERE employee_id = "
                         ":id", {"id": row["employee_id"]})
    assert employee is not None
    if not await may_view(ctx, employee):
        raise not_found("Leave request")
    return LeaveOut(**row)


@router.post("/leaves", response_model=LeaveOut, status_code=status.HTTP_201_CREATED)
async def apply(body: LeaveIn, ctx: Ctx = Depends(apply_leave)) -> LeaveOut:
    mine = await my_employee(ctx)
    employee_id = body.employee_id or (mine["employee_id"] if mine else None)
    if employee_id is None:
        raise HTTPException(422, "No employee record is linked to your user; give the employee")
    employee = await one(
        ctx, "SELECT employee_id, unit_id, department_id, employment_status FROM erp.employee "
        "WHERE employee_id = :id FOR UPDATE", {"id": employee_id})
    if employee is None:
        raise HTTPException(422, "The employee does not exist")
    own = mine is not None and mine["employee_id"] == employee_id
    if not own and not (has_permission(ctx.principal, "hr.manage")
                        and ctx.principal.may_see_unit(employee["unit_id"])):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can apply only for yourself")
    if employee["employment_status"] != "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, "The employee has left")
    kind = await one(ctx, "SELECT code, unpaid, active FROM erp.leave_type WHERE leave_type_id = "
                     ":id", {"id": body.leave_type_id})
    if kind is None or not kind["active"]:
        raise HTTPException(422, "The leave type does not exist or is not in use")
    start_month = int(await rules.get_value(ctx, "leaveYearStartMonth"))
    year = leave_year(body.start_date, start_month)
    if leave_year(body.end_date, start_month) != year:
        raise HTTPException(422, "A leave cannot run across two leave years; apply for each part")
    days = count_days(body.start_date, body.end_date, body.half_day,
                      bool(await rules.get_value(ctx, "leaveCountSundays")))
    if days <= 0:
        raise HTTPException(422, "There are no leave days in those dates")
    if await one(
            ctx, "SELECT 1 AS x FROM erp.leave_request WHERE employee_id = :e AND status IN "
            "('PENDING_APPROVAL', 'APPROVED') AND start_date <= :end AND end_date >= :start",
            {"e": employee_id, "start": body.start_date, "end": body.end_date}):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "The employee already has leave on those days")
    if not kind["unpaid"]:
        balance = await ensure_balance(ctx, employee_id, body.leave_type_id, year)
        held = await pending_days(ctx, employee_id, body.leave_type_id, year, start_month)
        free = (balance["opening_days"] + balance["accrued_days"] + balance["adjusted_days"]
                - balance["used_days"] - held)
        if days > free:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Only {free} day(s) of {kind['code']} are available, {days} asked")
    created = await one(
        ctx, "INSERT INTO erp.leave_request(tenant_id, employee_id, leave_type_id, start_date, "
        "end_date, requested_days, half_day, reason, status, applied_by) VALUES "
        "(erp.current_tenant_id(), :e, :t, :s, :n, :d, :h, :r, 'PENDING_APPROVAL', :me) "
        "RETURNING leave_request_id",
        {"e": employee_id, "t": body.leave_type_id, "s": body.start_date, "n": body.end_date,
         "d": days, "h": body.half_day, "r": body.reason,
         "me": ctx.principal.profile.app_user_id})
    assert created is not None
    request_id: UUID = created["leave_request_id"]
    approval_id = await engine.submit(ctx, "leave", request_id)
    await audit(ctx, "leave.apply", "leave_request", request_id,
                {"employee_id": employee_id, "days": days, "approval_id": approval_id})
    return await load(ctx, request_id)


async def load_for_approval(ctx: Ctx, request_id: UUID) -> DocInfo | None:
    row = await one(
        ctx, "SELECT l.status, l.start_date, l.end_date, l.requested_days, t.code, e.full_name, "
        "e.department_id FROM erp.leave_request l JOIN erp.employee e ON e.employee_id = "
        "l.employee_id JOIN erp.leave_type t ON t.leave_type_id = l.leave_type_id WHERE "
        "l.leave_request_id = :id", {"id": request_id})
    if row is None:
        return None
    return DocInfo(
        title=f"{row['code']} leave, {row['full_name']}: {row['requested_days']} day(s) "
              f"{row['start_date']} to {row['end_date']}",
        amount=None, department_id=row["department_id"],
        submittable=row["status"] == "PENDING_APPROVAL",
        reason=f"The leave request is {str(row['status']).lower().replace('_', ' ')}")


async def finalize_leave(ctx: Ctx, request_id: UUID, approved: bool) -> None:
    row = await one(
        ctx, "SELECT l.employee_id, l.leave_type_id, l.start_date, l.requested_days, t.unpaid FROM "
        "erp.leave_request l JOIN erp.leave_type t ON t.leave_type_id = l.leave_type_id WHERE "
        "l.leave_request_id = :id FOR UPDATE OF l", {"id": request_id})
    if row is None:
        return
    if approved:
        start_month = int(await rules.get_value(ctx, "leaveYearStartMonth"))
        await ensure_balance(ctx, row["employee_id"], row["leave_type_id"],
                             leave_year(row["start_date"], start_month))
        await rows(
            ctx, "UPDATE erp.leave_balance SET used_days = used_days + :d WHERE employee_id = :e "
            "AND leave_type_id = :t AND fiscal_year = :y RETURNING employee_id",
            {"d": row["requested_days"], "e": row["employee_id"], "t": row["leave_type_id"],
             "y": leave_year(row["start_date"], start_month)})
    await rows(ctx, "UPDATE erp.leave_request SET status = :s, counted = :c WHERE "
               "leave_request_id = :id RETURNING leave_request_id",
               {"s": "APPROVED" if approved else "REJECTED", "c": approved, "id": request_id})


register(DocumentType("leave", "leave.apply", load_for_approval, finalize_leave))


@router.post("/leaves/{request_id}/cancel", response_model=LeaveOut)
async def cancel(request_id: UUID, ctx: Ctx = Depends(apply_leave)) -> LeaveOut:
    """Withdraw a pending request, or cancel an approved leave that has not started."""
    leave = await load(ctx, request_id)
    mine = await my_employee(ctx)
    if not ((mine is not None and mine["employee_id"] == leave.employee_id)
            or has_permission(ctx.principal, "hr.manage")):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Only the employee or HR can cancel this leave")
    if leave.status == "PENDING_APPROVAL":
        await rows(ctx, "UPDATE erp.approval_instance SET status = 'CANCELLED', version = version "
                   "+ 1 WHERE document_type = 'leave' AND document_id = :id AND status = "
                   "'PENDING' RETURNING approval_id", {"id": request_id})
    elif leave.status == "APPROVED":
        if leave.start_date <= date.today():
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "The leave has started; only the days still to come can be changed")
        start_month = int(await rules.get_value(ctx, "leaveYearStartMonth"))
        await rows(
            ctx, "UPDATE erp.leave_balance SET used_days = greatest(used_days - :d, 0) WHERE "
            "employee_id = :e AND leave_type_id = :t AND fiscal_year = :y RETURNING employee_id",
            {"d": leave.requested_days, "e": leave.employee_id, "t": leave.leave_type_id,
             "y": leave_year(leave.start_date, start_month)})
    else:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The leave is {leave.status.lower()} and cannot be cancelled")
    await rows(ctx, "UPDATE erp.leave_request SET status = 'CANCELLED', counted = false WHERE "
               "leave_request_id = :id RETURNING leave_request_id", {"id": request_id})
    await audit(ctx, "leave.cancel", "leave_request", request_id, {"was": leave.status})
    return await load(ctx, request_id)


@router.get("/leaves", response_model=Page[LeaveOut])
async def list_leaves(
    employee_id: UUID | None = None, leave_status: str | None = Query(None, alias="status"),
    date_from: date | None = None, date_to: date | None = None, page: Paging = Depends(),
    ctx: Ctx = Depends(apply_leave),
) -> Page[LeaveOut]:
    """HR sees all leave in their plants; everyone else sees their own and their team's."""
    hr = has_permission(ctx.principal, "hr.manage")
    conditions: list[str] = []
    params: dict[str, Any] = {"me": ctx.principal.profile.app_user_id}
    if hr:
        if ctx.principal.profile.unit_ids is not None:
            conditions.append("e.unit_id = ANY(CAST(:units AS uuid[]))")
            params["units"] = list(ctx.principal.profile.unit_ids)
    else:
        conditions.append(
            "(e.user_id = :me OR e.employee_id IN (WITH RECURSIVE down AS (SELECT x.employee_id "
            "FROM erp.employee x JOIN erp.employee b ON b.employee_id = x.manager_id WHERE "
            "b.user_id = :me UNION ALL SELECT y.employee_id FROM erp.employee y JOIN down ON "
            "y.manager_id = down.employee_id) SELECT employee_id FROM down))")
    for text_, key, value in (
            ("l.employee_id = :e", "e", employee_id),
            ("l.status = :s", "s", leave_status.upper() if leave_status else None),
            ("l.end_date >= :f", "f", date_from), ("l.start_date <= :t", "t", date_to)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    data = await rows(ctx, f"{LEAVE_SQL}{where} ORDER BY l.start_date DESC, e.employee_no "
                      "LIMIT :limit OFFSET :offset", {**params, "limit": page.limit,
                                                      "offset": page.offset})
    total = await count(
        ctx, "SELECT count(*) FROM erp.leave_request l JOIN erp.employee e ON e.employee_id = "
        f"l.employee_id{where}", params)
    return Page(items=[LeaveOut(**d) for d in data], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/leaves/{request_id}", response_model=LeaveOut)
async def get_leave(request_id: UUID, ctx: Ctx = Depends(apply_leave)) -> LeaveOut:
    return await load(ctx, request_id)


# ------------------------------------------------------------------ balances
@router.get("/leave-balances", response_model=list[BalanceOut])
async def balances(
    employee_id: UUID | None = None, leave_year_: int | None = Query(None, alias="year"),
    ctx: Ctx = Depends(apply_leave),
) -> list[BalanceOut]:
    """Balances of every leave type for the year; default: your own, this leave year."""
    mine = await my_employee(ctx)
    target = employee_id or (mine["employee_id"] if mine else None)
    if target is None:
        raise HTTPException(422, "No employee record is linked to your user; give the employee")
    employee = await one(ctx, "SELECT employee_id, unit_id FROM erp.employee WHERE employee_id = "
                         ":id", {"id": target})
    if employee is None or not await may_view(ctx, employee):
        raise not_found("Employee")
    start_month = int(await rules.get_value(ctx, "leaveYearStartMonth"))
    year = leave_year_ or leave_year(date.today(), start_month)
    out: list[BalanceOut] = []
    for kind in await rows(ctx, "SELECT leave_type_id, code, unpaid FROM erp.leave_type WHERE "
                           "active AND NOT unpaid ORDER BY code", {}):
        row = await one(
            ctx, "SELECT opening_days, accrued_days, adjusted_days, used_days FROM "
            "erp.leave_balance WHERE employee_id = :e AND leave_type_id = :t AND fiscal_year = :y",
            {"e": target, "t": kind["leave_type_id"], "y": year})
        if row is None:  # not used yet: show the yearly default
            default = await one(ctx, "SELECT annual_default_days FROM erp.leave_type WHERE "
                                "leave_type_id = :t", {"t": kind["leave_type_id"]})
            assert default is not None
            row = {"opening_days": ZERO, "accrued_days": default["annual_default_days"],
                   "adjusted_days": ZERO, "used_days": ZERO}
        held = await pending_days(ctx, target, kind["leave_type_id"], year, start_month)
        out.append(BalanceOut(
            leave_type_id=kind["leave_type_id"], leave_type_code=kind["code"], leave_year=year,
            **row, pending_days=held,
            available_days=row["opening_days"] + row["accrued_days"] + row["adjusted_days"]
            - row["used_days"] - held))
    return out


@router.post("/leave-balances/adjust", response_model=BalanceOut)
async def adjust(body: AdjustIn, ctx: Ctx = Depends(manage)) -> BalanceOut:
    """HR gives or takes days (carry-forward, encashment, a correction); audited."""
    employee = await one(ctx, "SELECT employee_id, unit_id FROM erp.employee WHERE employee_id = "
                         ":id", {"id": body.employee_id})
    if employee is None or not ctx.principal.may_see_unit(employee["unit_id"]):
        raise not_found("Employee")
    kind = await one(ctx, "SELECT unpaid FROM erp.leave_type WHERE leave_type_id = :id",
                     {"id": body.leave_type_id})
    if kind is None or kind["unpaid"]:
        raise HTTPException(422, "The leave type does not exist or has no balance")
    balance = await ensure_balance(ctx, body.employee_id, body.leave_type_id, body.leave_year)
    if (balance["opening_days"] + balance["accrued_days"] + balance["adjusted_days"]
            + body.days) < balance["used_days"]:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "That would take the balance below what is used")
    await rows(ctx, "UPDATE erp.leave_balance SET adjusted_days = adjusted_days + :d WHERE "
               "employee_id = :e AND leave_type_id = :t AND fiscal_year = :y RETURNING employee_id",
               {"d": body.days, "e": body.employee_id, "t": body.leave_type_id,
                "y": body.leave_year})
    await audit(ctx, "leave_balance.adjust", "employee", body.employee_id,
                {"leave_type_id": body.leave_type_id, "year": body.leave_year, "days": body.days,
                 "reason": body.reason})
    return next(b for b in await balances(body.employee_id, body.leave_year, ctx)
                if b.leave_type_id == body.leave_type_id)


class CalendarEntry(BaseModel):
    employee_id: UUID
    employee_no: str
    employee_name: str
    leave_type_code: str
    start_date: date
    end_date: date
    requested_days: Decimal


@router.get("/leave-calendar", response_model=list[CalendarEntry])
async def calendar(
    date_from: date, date_to: date, unit_id: UUID | None = None, ctx: Ctx = Depends(read)
) -> list[CalendarEntry]:
    """Who is away in the period (approved leave), for planning shifts."""
    if date_to < date_from or (date_to - date_from).days > 366:
        raise HTTPException(422, "Give a period of at most a year")
    conditions = ["l.status = 'APPROVED'", "l.start_date <= :t", "l.end_date >= :f"]
    params: dict[str, Any] = {"f": date_from, "t": date_to}
    if unit_id is not None:
        conditions.append("e.unit_id = :u")
        params["u"] = unit_id
    if ctx.principal.profile.unit_ids is not None:
        conditions.append("e.unit_id = ANY(CAST(:units AS uuid[]))")
        params["units"] = list(ctx.principal.profile.unit_ids)
    data = await rows(
        ctx, "SELECT e.employee_id, e.employee_no, e.full_name AS employee_name, t.code AS "
        "leave_type_code, l.start_date, l.end_date, l.requested_days FROM erp.leave_request l "
        "JOIN erp.employee e ON e.employee_id = l.employee_id JOIN erp.leave_type t ON "
        f"t.leave_type_id = l.leave_type_id WHERE {' AND '.join(conditions)} ORDER BY "
        "l.start_date, e.employee_no", params)
    return [CalendarEntry(**d) for d in data]

