"""Quality holds: freeze a lot, compound batch or production lot until QA releases it
(BRD BR-06, FR-QA-01, FR-QA-04).

A held stock lot becomes QUARANTINE, so it can neither be issued, transferred nor drawn; a held
compound batch shows as ON_HOLD and is never allocated; a held production lot becomes HOLD and
cannot move, be packed or have its inserts drawn. Releasing puts the subject back where it was.
A hold can be linked to an NCR. Dispositions that remove the subject for good (scrap, return to the
supplier) close the hold with the subject blocked, scrapped or failed. Needs qa.approve.
"""

from datetime import datetime
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
    not_found,
    one,
    requires,
    requires_module,
    rows,
    scope_clause,
)
from heyyerp_api.quality.inspections import resolve_subject

router = APIRouter(prefix="/quality", tags=["quality"])
manage = requires("qa.approve")
read = requires_module("quality")
HoldType = Literal["STOCK_LOT", "COMPOUND_BATCH", "PROD_LOT"]


class HoldIn(Strict):
    subject_type: HoldType
    subject_id: UUID
    reason: str = Field(min_length=3, max_length=300)


class ReleaseIn(Strict):
    note: str = Field(min_length=3, max_length=300)


class HoldOut(BaseModel):
    hold_id: UUID
    unit_id: UUID
    subject_type: str
    subject_id: UUID
    subject_label: str | None
    reason: str
    ncr_id: UUID | None
    placed_by_name: str
    placed_at: datetime
    released_by_name: str | None
    released_at: datetime | None
    release_note: str | None
    active: bool


HOLD_SQL = (
    "SELECT h.hold_id, h.unit_id, h.subject_type, h.subject_id, COALESCE(t.lot_no, b.batch_no, "
    "p.lot_no) AS subject_label, h.reason, h.ncr_id, u.display_name AS placed_by_name, "
    "h.placed_at, r.display_name AS released_by_name, h.released_at, h.release_note, "
    "h.released_at IS NULL AS active FROM erp.quality_hold h JOIN erp.app_user u ON "
    "u.user_id = h.placed_by LEFT JOIN erp.app_user r ON r.user_id = h.released_by LEFT JOIN "
    "erp.lot t ON h.subject_type = 'STOCK_LOT' AND t.lot_id = h.subject_id LEFT JOIN "
    "erp.compound_batch b ON h.subject_type = 'COMPOUND_BATCH' AND b.batch_id = h.subject_id "
    "LEFT JOIN erp.prod_lot p ON h.subject_type = 'PROD_LOT' AND p.prod_lot_id = h.subject_id"
)


async def load(ctx: Ctx, hold_id: UUID) -> HoldOut | None:
    row = await one(ctx, f"{HOLD_SQL} WHERE h.hold_id = :id", {"id": hold_id})
    if row is None or not ctx.principal.may_see_unit(row["unit_id"]):
        return None
    return HoldOut(**row)


async def place(
    ctx: Ctx, subject_type: str, subject_id: UUID, reason: str, ncr_id: UUID | None = None
) -> UUID:
    """Freeze the subject. Used by the hold endpoint and by NCRs raised with hold=true."""
    subject = await resolve_subject(ctx, subject_type, subject_id)
    previous: str | None
    if subject_type == "STOCK_LOT":
        lot = await one(ctx, "SELECT status FROM erp.lot WHERE lot_id = :id FOR UPDATE",
                        {"id": subject_id})
        assert lot is not None
        if lot["status"] != "AVAILABLE":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"The lot is {str(lot['status']).lower()}, not available")
        previous = lot["status"]
        await rows(ctx, "UPDATE erp.lot SET status = 'QUARANTINE' WHERE lot_id = :id "
                   "RETURNING lot_id", {"id": subject_id})
    elif subject_type == "PROD_LOT":
        lot = await one(ctx, "SELECT status FROM erp.prod_lot WHERE prod_lot_id = :id FOR UPDATE",
                        {"id": subject_id})
        assert lot is not None
        if lot["status"] not in ("IN_PROCESS", "READY"):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"The lot is {str(lot['status']).lower()} and cannot be held")
        previous = lot["status"]
        await rows(ctx, "UPDATE erp.prod_lot SET status = 'HOLD' WHERE prod_lot_id = :id "
                   "RETURNING prod_lot_id", {"id": subject_id})
    else:
        batch = await one(
            ctx, "SELECT status FROM erp.v_compound_batch_status WHERE batch_id = :id",
            {"id": subject_id})
        assert batch is not None
        if batch["status"] in ("CONSUMED", "REJECTED", "EXPIRED", "ON_HOLD"):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"The batch is {str(batch['status']).lower().replace('_', ' ')}")
        previous = batch["status"]
    created = await one(
        ctx, "INSERT INTO erp.quality_hold(tenant_id, unit_id, subject_type, subject_id, reason, "
        "previous_status, ncr_id, placed_by) VALUES (erp.current_tenant_id(), :u, :t, :s, :r, :p, "
        ":n, :me) RETURNING hold_id",
        {"u": subject["unit_id"], "t": subject_type, "s": subject_id, "r": reason, "p": previous,
         "n": ncr_id, "me": ctx.principal.profile.app_user_id})
    assert created is not None
    await audit(ctx, "hold.place", "quality_hold", created["hold_id"],
                {"subject_type": subject_type, "subject": subject["label"], "reason": reason})
    return created["hold_id"]  # type: ignore[no-any-return]


async def close(ctx: Ctx, hold: dict[str, Any], note: str, *, permanent: str | None = None) -> None:
    """Release a hold. permanent says what happens to the subject instead of returning it to use:
    BLOCKED / SCRAPPED / FAILED depending on its kind."""
    kind, sid = hold["subject_type"], hold["subject_id"]
    if kind == "STOCK_LOT":
        target = "BLOCKED" if permanent else hold["previous_status"] or "AVAILABLE"
        await rows(ctx, "UPDATE erp.lot SET status = :s WHERE lot_id = :id AND status = "
                   "'QUARANTINE' RETURNING lot_id", {"s": target, "id": sid})
    elif kind == "PROD_LOT":
        target = "SCRAPPED" if permanent else hold["previous_status"] or "IN_PROCESS"
        await rows(ctx, "UPDATE erp.prod_lot SET status = :s WHERE prod_lot_id = :id AND "
                   "status = 'HOLD' RETURNING prod_lot_id", {"s": target, "id": sid})
    elif permanent:
        await rows(ctx, "UPDATE erp.compound_batch SET qa_status = 'FAILED', qa_note = :n, "
                   "qa_by = :me, qa_at = now() WHERE batch_id = :id RETURNING batch_id",
                   {"n": note, "me": ctx.principal.profile.app_user_id, "id": sid})
    await rows(ctx, "UPDATE erp.quality_hold SET released_by = :me, released_at = now(), "
               "release_note = :n WHERE hold_id = :id RETURNING hold_id",
               {"me": ctx.principal.profile.app_user_id, "n": note, "id": hold["hold_id"]})
    await audit(ctx, "hold.release", "quality_hold", hold["hold_id"],
                {"note": note, "permanent": permanent})


async def open_hold(ctx: Ctx, hold_id: UUID, lock: bool = True) -> dict[str, Any]:
    found = await one(
        ctx, "SELECT hold_id, unit_id, subject_type, subject_id, previous_status, ncr_id, "
        f"released_at FROM erp.quality_hold WHERE hold_id = :id{' FOR UPDATE' if lock else ''}",
        {"id": hold_id})
    if found is None or not ctx.principal.may_see_unit(found["unit_id"]):
        raise not_found("Hold")
    if found["released_at"] is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The hold was already released")
    return found


@router.post("/holds", response_model=HoldOut, status_code=status.HTTP_201_CREATED)
async def place_hold(body: HoldIn, ctx: Ctx = Depends(manage)) -> HoldOut:
    hold_id = await place(ctx, body.subject_type, body.subject_id, body.reason.strip())
    result = await load(ctx, hold_id)
    assert result is not None
    return result


@router.post("/holds/{hold_id}/release", response_model=HoldOut)
async def release_hold(hold_id: UUID, body: ReleaseIn, ctx: Ctx = Depends(manage)) -> HoldOut:
    hold = await open_hold(ctx, hold_id)
    await close(ctx, hold, body.note.strip())
    result = await load(ctx, hold_id)
    assert result is not None
    return result


@router.get("/holds", response_model=Page[HoldOut])
async def list_holds(
    active: bool | None = Query(None), subject_type: HoldType | None = None,
    subject_id: UUID | None = None, page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[HoldOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "h.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    if active is not None:
        conditions.append("h.released_at IS NULL" if active else "h.released_at IS NOT NULL")
    for text_, key, value in (("h.subject_type = :st", "st", subject_type),
                              ("h.subject_id = :sid", "sid", subject_id)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{HOLD_SQL}{where} ORDER BY h.placed_at DESC LIMIT :limit "
                       "OFFSET :offset", {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.quality_hold h{where}", params)
    return Page(items=[HoldOut(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/holds/{hold_id}", response_model=HoldOut)
async def get_hold(hold_id: UUID, ctx: Ctx = Depends(read)) -> HoldOut:
    found = await load(ctx, hold_id)
    if found is None:
        raise not_found("Hold")
    return found
