"""The company's holiday calendar and shift master, kept by HR (hr.manage) and read by everyone.

Nothing is fixed in code. A holiday is a date with a name, for every plant or for one; it is never
deleted, only switched off. HR can add a whole year's list in one go (dates already there are left
alone). A shift is a code with start and end times and grace minutes, for every plant or one (a
plant's own shift wins over the general one of the same code); an end before the start means
the shift runs past midnight. Leave and attendance use these: whether holidays count as leave
days is the company rule `leaveHolidaysCount`, and a late arrival is a check-in after the
shift's start plus its grace.
"""

from datetime import date, time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from heyyerp_api.hr.employees import apply_leave, manage
from heyyerp_api.masters.common import (
    Ctx,
    Strict,
    audit,
    db_errors,
    not_found,
    one,
    require_in_scope,
    rows,
)

router = APIRouter(prefix="/hr", tags=["hr"])


class HolidayIn(Strict):
    holiday_date: date
    name: str = Field(min_length=2, max_length=120)
    unit_id: UUID | None = None  # empty: every plant


class HolidayBulk(Strict):
    holidays: list[HolidayIn] = Field(min_length=1, max_length=100)


class HolidayPatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    active: bool | None = None


class HolidayOut(BaseModel):
    holiday_id: UUID
    unit_id: UUID | None
    holiday_date: date
    name: str
    active: bool


class ShiftIn(Strict):
    code: str = Field(min_length=1, max_length=10, pattern=r"^[A-Za-z0-9]+$")
    name: str = Field(min_length=2, max_length=60)
    start_time: time
    end_time: time
    grace_minutes: int = Field(default=0, ge=0, le=240)
    unit_id: UUID | None = None


class ShiftPatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=60)
    start_time: time | None = None
    end_time: time | None = None
    grace_minutes: int | None = Field(default=None, ge=0, le=240)
    active: bool | None = None


class ShiftOut(BaseModel):
    shift_id: UUID
    unit_id: UUID | None
    code: str
    name: str
    start_time: time
    end_time: time
    grace_minutes: int
    active: bool


HOLIDAY_SQL = "SELECT holiday_id, unit_id, holiday_date, name, active FROM erp.holiday"
SHIFT_SQL = ("SELECT shift_id, unit_id, code, name, start_time, end_time, grace_minutes, active "
             "FROM erp.shift")


async def holidays_between(ctx: Ctx, unit_id: UUID, start: date, end: date) -> set[date]:
    """The days in the range that are active holidays for the plant (its own or general)."""
    data = await rows(
        ctx, "SELECT DISTINCT holiday_date FROM erp.holiday WHERE active AND holiday_date BETWEEN "
        ":s AND :e AND (unit_id IS NULL OR unit_id = :u)", {"s": start, "e": end, "u": unit_id})
    return {d["holiday_date"] for d in data}


async def shifts_for(ctx: Ctx, unit_id: UUID) -> dict[str, dict[str, Any]]:
    """The plant's active shifts by code; a plant's own shift replaces the general one."""
    found: dict[str, dict[str, Any]] = {}
    for s in await rows(
            ctx, "SELECT code, start_time, end_time, grace_minutes, unit_id FROM erp.shift WHERE "
            "active AND (unit_id IS NULL OR unit_id = :u) ORDER BY unit_id NULLS FIRST",
            {"u": unit_id}):
        found[s["code"]] = s
    return found


# ------------------------------------------------------------------ holidays
@router.post("/holidays", response_model=HolidayOut, status_code=status.HTTP_201_CREATED)
async def add_holiday(body: HolidayIn, ctx: Ctx = Depends(manage)) -> HolidayOut:
    if body.unit_id is not None:
        require_in_scope(ctx, body.unit_id)
    elif ctx.principal.profile.unit_ids is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "A holiday for every plant needs access to all plants")
    with db_errors("That day is already a holiday there"):
        created = await one(
            ctx, "INSERT INTO erp.holiday(tenant_id, unit_id, holiday_date, name, created_by) "
            "VALUES (erp.current_tenant_id(), :u, :d, :n, :me) RETURNING holiday_id, unit_id, "
            "holiday_date, name, active",
            {"u": body.unit_id, "d": body.holiday_date, "n": body.name,
             "me": ctx.principal.profile.app_user_id})
    assert created is not None
    await audit(ctx, "holiday.add", "holiday", created["holiday_id"], body.model_dump())
    return HolidayOut(**created)


@router.post("/holidays/bulk", response_model=list[HolidayOut])
async def add_holidays(body: HolidayBulk, ctx: Ctx = Depends(manage)) -> list[HolidayOut]:
    """Add a list (a year's calendar); days already listed are left as they are."""
    added: list[HolidayOut] = []
    for h in body.holidays:
        if h.unit_id is not None:
            require_in_scope(ctx, h.unit_id)
        elif ctx.principal.profile.unit_ids is not None:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "A holiday for every plant needs access to all plants")
        created = await one(
            ctx, "INSERT INTO erp.holiday(tenant_id, unit_id, holiday_date, name, created_by) "
            "VALUES (erp.current_tenant_id(), :u, :d, :n, :me) ON CONFLICT DO NOTHING RETURNING "
            "holiday_id, unit_id, holiday_date, name, active",
            {"u": h.unit_id, "d": h.holiday_date, "n": h.name,
             "me": ctx.principal.profile.app_user_id})
        if created is not None:
            added.append(HolidayOut(**created))
    await audit(ctx, "holiday.bulk", "holiday", "bulk", {"added": len(added)})
    return added


@router.patch("/holidays/{holiday_id}", response_model=HolidayOut)
async def change_holiday(
    holiday_id: UUID, body: HolidayPatch, ctx: Ctx = Depends(manage)
) -> HolidayOut:
    current = await one(ctx, f"{HOLIDAY_SQL} WHERE holiday_id = :id", {"id": holiday_id})
    if current is None:
        raise not_found("Holiday")
    if current["unit_id"] is not None:
        require_in_scope(ctx, current["unit_id"])
    elif ctx.principal.profile.unit_ids is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only all-plant access changes this holiday")
    changes = body.model_dump(exclude_unset=True)
    if changes:
        sets = ", ".join(f"{k} = :{k}" for k in changes)
        await rows(ctx, f"UPDATE erp.holiday SET {sets} WHERE holiday_id = :id RETURNING "
                   "holiday_id", {**changes, "id": holiday_id})
    await audit(ctx, "holiday.update", "holiday", holiday_id, changes)
    row = await one(ctx, f"{HOLIDAY_SQL} WHERE holiday_id = :id", {"id": holiday_id})
    assert row is not None
    return HolidayOut(**row)


@router.get("/holidays", response_model=list[HolidayOut])
async def list_holidays(
    year: int | None = None, unit_id: UUID | None = None, active: bool | None = Query(True),
    ctx: Ctx = Depends(apply_leave),
) -> list[HolidayOut]:
    """The holidays of a year (default this one); with a plant: its own and the general ones."""
    chosen = year or date.today().year
    conditions = ["holiday_date >= make_date(:y, 1, 1)", "holiday_date < make_date(:y + 1, 1, 1)"]
    params: dict[str, Any] = {"y": chosen}
    if unit_id is not None:
        conditions.append("(unit_id IS NULL OR unit_id = :u)")
        params["u"] = unit_id
    if active is not None:
        conditions.append("active = :a")
        params["a"] = active
    data = await rows(ctx, f"{HOLIDAY_SQL} WHERE {' AND '.join(conditions)} ORDER BY "
                      "holiday_date, unit_id NULLS FIRST", params)
    return [HolidayOut(**d) for d in data]


# ------------------------------------------------------------------ shifts
@router.post("/shifts", response_model=ShiftOut, status_code=status.HTTP_201_CREATED)
async def add_shift(body: ShiftIn, ctx: Ctx = Depends(manage)) -> ShiftOut:
    if body.start_time == body.end_time:
        raise HTTPException(422, "A shift cannot start and end at the same time")
    if body.unit_id is not None:
        require_in_scope(ctx, body.unit_id)
    elif ctx.principal.profile.unit_ids is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "A shift for every plant needs access to all plants")
    with db_errors("That plant already has a shift with this code"):
        created = await one(
            ctx, "INSERT INTO erp.shift(tenant_id, unit_id, code, name, start_time, end_time, "
            "grace_minutes) VALUES (erp.current_tenant_id(), :u, :c, :n, :s, :e, :g) RETURNING "
            "shift_id, unit_id, code, name, start_time, end_time, grace_minutes, active",
            {"u": body.unit_id, "c": body.code.upper(), "n": body.name, "s": body.start_time,
             "e": body.end_time, "g": body.grace_minutes})
    assert created is not None
    await audit(ctx, "shift.add", "shift", created["shift_id"], body.model_dump(mode="json"))
    return ShiftOut(**created)


@router.patch("/shifts/{shift_id}", response_model=ShiftOut)
async def change_shift(shift_id: UUID, body: ShiftPatch, ctx: Ctx = Depends(manage)) -> ShiftOut:
    current = await one(ctx, f"{SHIFT_SQL} WHERE shift_id = :id", {"id": shift_id})
    if current is None:
        raise not_found("Shift")
    if current["unit_id"] is not None:
        require_in_scope(ctx, current["unit_id"])
    elif ctx.principal.profile.unit_ids is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only all-plant access changes this shift")
    changes = body.model_dump(exclude_unset=True)
    start = changes.get("start_time", current["start_time"])
    end = changes.get("end_time", current["end_time"])
    if start == end:
        raise HTTPException(422, "A shift cannot start and end at the same time")
    if changes:
        sets = ", ".join(f"{k} = :{k}" for k in changes)
        await rows(ctx, f"UPDATE erp.shift SET {sets} WHERE shift_id = :id RETURNING shift_id",
                   {**changes, "id": shift_id})
    await audit(ctx, "shift.update", "shift", shift_id, {k: str(v) for k, v in changes.items()})
    row = await one(ctx, f"{SHIFT_SQL} WHERE shift_id = :id", {"id": shift_id})
    assert row is not None
    return ShiftOut(**row)


@router.get("/shifts", response_model=list[ShiftOut])
async def list_shifts(
    unit_id: UUID | None = None, active: bool | None = Query(True), ctx: Ctx = Depends(apply_leave)
) -> list[ShiftOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    if unit_id is not None:
        conditions.append("(unit_id IS NULL OR unit_id = :u)")
        params["u"] = unit_id
    if active is not None:
        conditions.append("active = :a")
        params["a"] = active
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    data = await rows(ctx, f"{SHIFT_SQL}{where} ORDER BY code, unit_id NULLS FIRST", params)
    return [ShiftOut(**d) for d in data]
