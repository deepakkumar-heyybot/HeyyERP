"""Instruments and their calibration schedule with due alerts (BRD FR-QA-05).

Each calibration records when it was done, when the next one is due and the result. An instrument's
status is derived from its latest calibration: NEVER (none yet), FAILED, OVERDUE, DUE_SOON (inside
the warning window) or OK. An instrument that is inactive, failed or overdue cannot be used for an
inspection. Needs qa.approve to change; reading needs the quality module.
"""

from datetime import date
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

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

router = APIRouter(prefix="/quality", tags=["quality"])
manage = requires("qa.approve")
read = requires_module("quality")
WARN_DAYS = 30
CODE = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class InstrumentIn(Strict):
    unit_id: UUID
    code: str = CODE
    name: str = Field(min_length=2, max_length=200)


class InstrumentPatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    active: bool | None = None


class CalibrationIn(Strict):
    calibrated_at: date
    due_at: date
    result: Literal["PASS", "FAIL", "CONDITIONAL"]
    certificate_ref: str | None = Field(default=None, max_length=120)


class InstrumentOut(BaseModel):
    instrument_id: UUID
    unit_id: UUID
    code: str
    name: str
    active: bool
    last_calibrated_at: date | None
    due_at: date | None
    last_result: str | None
    calibration_status: str
    days_to_due: int | None


STATUS_SQL = (
    "SELECT i.instrument_id, i.unit_id, i.code, i.name, i.active, c.calibrated_at AS "
    "last_calibrated_at, c.due_at, c.result AS last_result, CASE WHEN c.calibration_id IS NULL "
    "THEN 'NEVER' WHEN c.result = 'FAIL' THEN 'FAILED' WHEN c.due_at < CURRENT_DATE THEN "
    "'OVERDUE' WHEN c.due_at <= CURRENT_DATE + CAST(:warn AS int) THEN 'DUE_SOON' ELSE 'OK' END AS "
    "calibration_status, c.due_at - CURRENT_DATE AS days_to_due FROM erp.instrument i LEFT JOIN "
    "LATERAL (SELECT * FROM erp.calibration k WHERE k.instrument_id = i.instrument_id ORDER BY "
    "k.calibrated_at DESC, k.due_at DESC, k.calibration_id LIMIT 1) c ON true"
)


async def load(ctx: Ctx, instrument_id: UUID) -> InstrumentOut | None:
    row = await one(ctx, f"{STATUS_SQL} WHERE i.instrument_id = :id",
                    {"id": instrument_id, "warn": WARN_DAYS})
    if row is None or not ctx.principal.may_see_unit(row["unit_id"]):
        return None
    return InstrumentOut(**row)


async def usable(ctx: Ctx, instrument_id: UUID) -> InstrumentOut:
    """The instrument, or an error saying why it cannot be used for a measurement."""
    found = await load(ctx, instrument_id)
    if found is None:
        raise HTTPException(422, "The instrument does not exist")
    if not found.active:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Instrument {found.code} is inactive")
    if found.calibration_status in ("NEVER", "FAILED", "OVERDUE"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Instrument {found.code} cannot be used: calibration is "
            f"{found.calibration_status.lower()}")
    return found


@router.get("/instruments", response_model=Page[InstrumentOut])
async def list_instruments(
    unit_id: UUID | None = None, calibration_status: str | None = Query(None),
    active: bool | None = Query(None), page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[InstrumentOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {"warn": WARN_DAYS}
    clause, scope_params = scope_clause(ctx, "i.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    if unit_id is not None:
        conditions.append("i.unit_id = :unit")
        params["unit"] = unit_id
    if active is not None:
        conditions.append("i.active = :active")
        params["active"] = active
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    wrapped = f"SELECT * FROM ({STATUS_SQL}{where}) s"
    if calibration_status:
        wrapped += " WHERE s.calibration_status = :cs"
        params["cs"] = calibration_status.upper()
    items = await rows(ctx, f"{wrapped} ORDER BY s.code LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM ({wrapped}) t", params)
    return Page(items=[InstrumentOut(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/instruments/{instrument_id}", response_model=InstrumentOut)
async def get_instrument(instrument_id: UUID, ctx: Ctx = Depends(read)) -> InstrumentOut:
    found = await load(ctx, instrument_id)
    if found is None:
        raise not_found("Instrument")
    return found


@router.post("/instruments", response_model=InstrumentOut, status_code=status.HTTP_201_CREATED)
async def create_instrument(body: InstrumentIn, ctx: Ctx = Depends(manage)) -> InstrumentOut:
    require_in_scope(ctx, body.unit_id)
    unit = await one(ctx, "SELECT active FROM erp.unit WHERE unit_id = :id", {"id": body.unit_id})
    if unit is None or not unit["active"]:
        raise HTTPException(422, "The plant does not exist or is inactive")
    with db_errors("This plant already has an instrument with this code"):
        created = await one(
            ctx, "INSERT INTO erp.instrument(tenant_id, unit_id, code, name) VALUES "
            "(erp.current_tenant_id(), :u, :c, :n) RETURNING instrument_id",
            {"u": body.unit_id, "c": body.code.upper(), "n": body.name})
    assert created is not None
    await audit(ctx, "instrument.create", "instrument", created["instrument_id"],
                body.model_dump())
    result = await load(ctx, created["instrument_id"])
    assert result is not None
    return result


@router.patch("/instruments/{instrument_id}", response_model=InstrumentOut)
async def update_instrument(
    instrument_id: UUID, body: InstrumentPatch, ctx: Ctx = Depends(manage)
) -> InstrumentOut:
    current = await get_instrument(instrument_id, ctx)
    changes = body.model_dump(exclude_unset=True)
    merged = {"name": current.name, "active": current.active, **changes}
    if merged["name"] is None or merged["active"] is None:
        raise HTTPException(422, "Fields cannot be null")
    await rows(ctx, "UPDATE erp.instrument SET name = :name, active = :active WHERE "
               "instrument_id = :id RETURNING instrument_id", {**merged, "id": instrument_id})
    await audit(ctx, "instrument.update", "instrument", instrument_id, {"changes": changes})
    result = await load(ctx, instrument_id)
    assert result is not None
    return result


@router.post("/instruments/{instrument_id}/calibrations", response_model=InstrumentOut,
             status_code=status.HTTP_201_CREATED)
async def record_calibration(
    instrument_id: UUID, body: CalibrationIn, ctx: Ctx = Depends(manage)
) -> InstrumentOut:
    await get_instrument(instrument_id, ctx)
    if body.calibrated_at > date.today():
        raise HTTPException(422, "A calibration cannot be dated in the future")
    if body.due_at <= body.calibrated_at:
        raise HTTPException(422, "The next due date must be after the calibration date")
    await rows(
        ctx, "INSERT INTO erp.calibration(tenant_id, instrument_id, calibrated_at, due_at, result, "
        "certificate_ref) VALUES (erp.current_tenant_id(), :i, :c, :d, :r, :ref) "
        "RETURNING calibration_id",
        {"i": instrument_id, "c": body.calibrated_at, "d": body.due_at, "r": body.result,
         "ref": body.certificate_ref})
    await audit(ctx, "instrument.calibrate", "instrument", instrument_id,
                {"calibrated_at": body.calibrated_at, "due_at": body.due_at,
                 "result": body.result})
    result = await load(ctx, instrument_id)
    assert result is not None
    return result


@router.get("/calibration/due", response_model=list[InstrumentOut])
async def calibration_alerts(
    within_days: int = Query(WARN_DAYS, ge=0, le=365), ctx: Ctx = Depends(read)
) -> list[InstrumentOut]:
    """The alert list: active instruments never calibrated, failed, overdue or due soon."""
    clause, params = scope_clause(ctx, "i.unit_id")
    items = await rows(
        ctx, f"SELECT * FROM ({STATUS_SQL} WHERE i.active{' AND ' + clause if clause else ''}) s "
        "WHERE s.calibration_status <> 'OK' ORDER BY s.due_at NULLS FIRST, s.code",
        {**params, "warn": within_days})
    return [InstrumentOut(**i) for i in items]
