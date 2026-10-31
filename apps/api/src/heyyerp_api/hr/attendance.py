"""Attendance, from the shop floor or biometric devices, and the month's summary (FR-HR-03).

HR records a day's attendance by hand or imports what a biometric device collected: one row per
employee, day and shift (the day is the check-in's date in India time), so importing the same file
again changes nothing. The import reports employee numbers it did not know and rows whose
check-out precedes the check-in instead of stopping. The monthly summary gives, per employee, the
days present, the hours worked, the approved leave days and the days absent: the working days of the
month (Sundays are off unless the company rule `leaveCountSundays` is on) less present and leave
days. Needs hr.manage; reading needs the hr module.
"""

from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from heyyerp_api.business_rules import service as rules
from heyyerp_api.hr.employees import manage, read
from heyyerp_api.hr.leave import count_days
from heyyerp_api.masters.common import (
    Ctx,
    Page,
    Paging,
    Strict,
    audit,
    count,
    one,
    require_in_scope,
    rows,
    scope_clause,
)

router = APIRouter(prefix="/hr", tags=["hr"])
Source = Literal["MANUAL", "BIOMETRIC", "IMPORT"]
SHIFT = Field(default="A", min_length=1, max_length=10, pattern=r"^[A-Za-z0-9]+$")
# ponytail: one time zone for the day boundary (all V-Rub plants are in India)
DAY = "(check_in AT TIME ZONE 'Asia/Kolkata')::date"


class AttendanceIn(Strict):
    employee_id: UUID
    shift_code: str = SHIFT
    check_in: datetime
    check_out: datetime | None = None


class DeviceRow(Strict):
    employee_no: str = Field(min_length=1, max_length=40)
    check_in: datetime
    check_out: datetime | None = None
    shift_code: str = SHIFT


class ImportIn(Strict):
    source: Source = "BIOMETRIC"
    records: list[DeviceRow] = Field(min_length=1, max_length=5000)


class ImportResult(BaseModel):
    imported: int
    updated: int
    unknown_employees: list[str]
    rejected: list[str]


class AttendanceOut(BaseModel):
    attendance_id: UUID
    employee_id: UUID
    employee_no: str
    employee_name: str
    work_date: date
    shift_code: str
    check_in: datetime | None
    check_out: datetime | None
    hours: Decimal | None
    source: str


class SummaryRow(BaseModel):
    employee_id: UUID
    employee_no: str
    employee_name: str
    present_days: int
    hours_worked: Decimal
    leave_days: Decimal
    absent_days: Decimal


ATT_SQL = (
    "SELECT a.attendance_id, a.employee_id, e.employee_no, e.full_name AS employee_name, "
    "a.work_date, a.shift_code, a.check_in, a.check_out, CASE WHEN a.check_in IS NOT NULL AND "
    "a.check_out IS NOT NULL THEN round(extract(epoch FROM (a.check_out - a.check_in)) / 3600, 2) "
    "END AS hours, a.source FROM erp.attendance a JOIN erp.employee e ON e.employee_id = "
    "a.employee_id"
)


def check_times(check_in: datetime, check_out: datetime | None) -> None:
    if check_out is not None and check_out < check_in:
        raise HTTPException(422, "The check-out is before the check-in")


async def save(ctx: Ctx, employee_id: UUID, shift: str, check_in: datetime,
               check_out: datetime | None, source: str) -> bool:
    """Insert or replace the day's row; True when it was new."""
    row = await one(
        ctx, "INSERT INTO erp.attendance(tenant_id, employee_id, work_date, shift_code, check_in, "
        "check_out, source) VALUES (erp.current_tenant_id(), :e, "
        "(CAST(:i AS timestamptz) AT TIME ZONE 'Asia/Kolkata')::date, :s, :i, :o, :src) ON "
        "CONFLICT (employee_id, work_date, shift_code) DO UPDATE SET check_in = EXCLUDED.check_in, "
        "check_out = EXCLUDED.check_out, source = EXCLUDED.source RETURNING (xmax = 0) AS fresh",
        {"e": employee_id, "s": shift.upper(), "i": check_in, "o": check_out, "src": source})
    assert row is not None
    return bool(row["fresh"])


@router.post("/attendance", response_model=AttendanceOut, status_code=201)
async def record(body: AttendanceIn, ctx: Ctx = Depends(manage)) -> AttendanceOut:
    check_times(body.check_in, body.check_out)
    employee = await one(ctx, "SELECT unit_id, employment_status FROM erp.employee WHERE "
                         "employee_id = :id", {"id": body.employee_id})
    if employee is None:
        raise HTTPException(422, "The employee does not exist")
    require_in_scope(ctx, employee["unit_id"])
    if body.check_in > datetime.now(body.check_in.tzinfo):
        raise HTTPException(422, "The check-in is in the future")
    await save(ctx, body.employee_id, body.shift_code, body.check_in, body.check_out, "MANUAL")
    saved = await one(ctx, f"{ATT_SQL} WHERE a.employee_id = :e AND a.shift_code = :s AND "
                      f"a.work_date = (CAST(:i AS timestamptz) AT TIME ZONE 'Asia/Kolkata')::date",
                      {"e": body.employee_id, "s": body.shift_code.upper(), "i": body.check_in})
    assert saved is not None
    await audit(ctx, "attendance.record", "attendance", saved["attendance_id"],
                {"employee_id": body.employee_id, "work_date": saved["work_date"]})
    return AttendanceOut(**saved)


@router.post("/attendance/import", response_model=ImportResult)
async def import_attendance(body: ImportIn, ctx: Ctx = Depends(manage)) -> ImportResult:
    """Load a device's records; safe to repeat (one row per employee, day and shift)."""
    numbers = {r.employee_no.upper() for r in body.records}
    known = {e["employee_no"]: e for e in await rows(
        ctx, "SELECT employee_id, employee_no, unit_id FROM erp.employee WHERE employee_no = "
        "ANY(CAST(:n AS text[]))", {"n": sorted(numbers)})}
    result = ImportResult(imported=0, updated=0, unknown_employees=[], rejected=[])
    for r in body.records:
        employee = known.get(r.employee_no.upper())
        if employee is None or not ctx.principal.may_see_unit(employee["unit_id"]):
            if r.employee_no.upper() not in result.unknown_employees:
                result.unknown_employees.append(r.employee_no.upper())
            continue
        if r.check_out is not None and r.check_out < r.check_in:
            result.rejected.append(f"{r.employee_no.upper()} {r.check_in.isoformat()}")
            continue
        if await save(ctx, employee["employee_id"], r.shift_code, r.check_in, r.check_out,
                      body.source):
            result.imported += 1
        else:
            result.updated += 1
    await audit(ctx, "attendance.import", "attendance", "batch",
                {"source": body.source, "imported": result.imported, "updated": result.updated,
                 "unknown": len(result.unknown_employees), "rejected": len(result.rejected)})
    return result


@router.get("/attendance", response_model=Page[AttendanceOut])
async def list_attendance(
    employee_id: UUID | None = None, unit_id: UUID | None = None, date_from: date | None = None,
    date_to: date | None = None, page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[AttendanceOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "e.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for text_, key, value in (("a.employee_id = :e", "e", employee_id),
                              ("e.unit_id = :u", "u", unit_id),
                              ("a.work_date >= :f", "f", date_from),
                              ("a.work_date <= :t", "t", date_to)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    data = await rows(ctx, f"{ATT_SQL}{where} ORDER BY a.work_date DESC, e.employee_no LIMIT "
                      ":limit OFFSET :offset", {**params, "limit": page.limit,
                                                "offset": page.offset})
    total = await count(ctx, "SELECT count(*) FROM erp.attendance a JOIN erp.employee e ON "
                        f"e.employee_id = a.employee_id{where}", params)
    return Page(items=[AttendanceOut(**d) for d in data], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/attendance/summary", response_model=list[SummaryRow])
async def summary(unit_id: UUID, month: date, ctx: Ctx = Depends(read)) -> list[SummaryRow]:
    """Per employee for the month: days present, hours, approved leave days and days absent."""
    require_in_scope(ctx, unit_id)
    first = month.replace(day=1)
    last = first.replace(day=monthrange(first.year, first.month)[1])
    if first > date.today():
        raise HTTPException(422, "That month has not started")
    sundays = bool(await rules.get_value(ctx, "leaveCountSundays"))
    working = count_days(first, last, False, sundays)
    out: list[SummaryRow] = []
    people = await rows(
        ctx, "SELECT employee_id, employee_no, full_name FROM erp.employee WHERE unit_id = :u AND "
        "employment_status = 'ACTIVE' ORDER BY employee_no", {"u": unit_id})
    for p in people:
        present = await one(
            ctx, "SELECT count(DISTINCT work_date) AS d, COALESCE(sum(extract(epoch FROM "
            "(check_out - check_in)) / 3600) FILTER (WHERE check_out IS NOT NULL), 0) AS h FROM "
            "erp.attendance WHERE employee_id = :e AND work_date BETWEEN :f AND :t",
            {"e": p["employee_id"], "f": first, "t": last})
        assert present is not None
        leave = Decimal(0)
        for taken in await rows(
                ctx, "SELECT start_date, end_date, half_day FROM erp.leave_request WHERE "
                "employee_id = :e AND status = 'APPROVED' AND start_date <= :t AND end_date >= :f",
                {"e": p["employee_id"], "f": first, "t": last}):
            leave += count_days(max(taken["start_date"], first), min(taken["end_date"], last),
                                taken["half_day"], sundays)
        absent = max(Decimal(0), working - Decimal(present["d"]) - leave)
        out.append(SummaryRow(
            employee_id=p["employee_id"], employee_no=p["employee_no"], employee_name=p[
                "full_name"], present_days=present["d"], hours_worked=round(
                Decimal(present["h"]), 2), leave_days=leave, absent_days=absent))
    return out

