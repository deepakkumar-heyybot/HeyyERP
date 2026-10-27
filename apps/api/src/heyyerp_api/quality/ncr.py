"""Non-conformance reports with 8D steps, disposition and closure (BRD FR-QA-04).

An NCR is raised against a GRN line, stock lot, compound batch, production lot, supplier or
customer complaint, and starts with the eight 8D steps (D1 to D8). Steps are completed in order,
each with the action taken, an owner, a due date and an evidence reference. Raising it with
`hold` freezes the lot through a quality hold. The disposition decides what happens to the goods:
use as is, rework or sort release the hold; scrap or return to the supplier remove the goods for
good. A report is closed only when all eight steps are done, a disposition is recorded for
goods-related reports and no hold of the report is still open. Needs the ncr permission.
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
from heyyerp_api.procurement.numbering import next_number
from heyyerp_api.quality import holds
from heyyerp_api.quality.inspections import resolve_subject

router = APIRouter(prefix="/quality", tags=["quality"])
manage = requires("ncr")
read = requires_module("quality")
SourceType = Literal["GRN_LINE", "STOCK_LOT", "COMPOUND_BATCH", "PROD_LOT", "SUPPLIER",
                     "CUSTOMER_COMPLAINT"]
Disposition = Literal["USE_AS_IS", "REWORK", "SORT", "SCRAP", "RETURN_TO_SUPPLIER"]
STEPS = {"D1": "Form the team", "D2": "Describe the problem", "D3": "Contain the problem",
         "D4": "Find the root cause", "D5": "Choose the corrective action",
         "D6": "Implement the corrective action", "D7": "Prevent recurrence",
         "D8": "Recognise the team"}
GOODS = ("GRN_LINE", "STOCK_LOT", "COMPOUND_BATCH", "PROD_LOT")


class NcrIn(Strict):
    source_type: SourceType
    source_id: UUID
    title: str = Field(min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    severity: Literal["MINOR", "MAJOR", "CRITICAL"] = "MINOR"
    owner_user_id: UUID | None = None
    hold: bool = False


class StepIn(Strict):
    action_text: str | None = Field(default=None, min_length=3, max_length=2000)
    owner_user_id: UUID | None = None
    due_at: datetime | None = None
    evidence_ref: str | None = Field(default=None, max_length=300)
    complete: bool = False


class DispositionIn(Strict):
    disposition: Disposition
    note: str = Field(min_length=3, max_length=500)


class StepOut(BaseModel):
    step_code: str
    title: str
    owner_name: str | None
    due_at: datetime | None
    completed_at: datetime | None
    completed_by_name: str | None
    action_text: str | None
    evidence_ref: str | None


class NcrSummary(BaseModel):
    ncr_id: UUID
    ncr_no: str
    source_type: str
    source_id: UUID
    title: str
    severity: str
    status: str
    owner_name: str | None
    opened_at: datetime
    disposition: str | None
    closed_at: datetime | None
    unit_id: UUID | None


class NcrOut(NcrSummary):
    description: str | None
    source_label: str | None
    disposition_note: str | None
    disposition_by_name: str | None
    steps: list[StepOut]
    open_hold_id: UUID | None


NCR_SQL = (
    "SELECT n.ncr_id, n.ncr_no, n.source_type, n.source_id, n.title, n.severity, n.status, "
    "o.display_name AS owner_name, n.opened_at, n.disposition, n.closed_at, n.unit_id FROM "
    "erp.ncr n LEFT JOIN erp.app_user o ON o.user_id = n.owner_user_id"
)


def visible(ctx: Ctx, unit_id: UUID | None) -> bool:
    return unit_id is None or bool(ctx.principal.may_see_unit(unit_id))


async def source_label(ctx: Ctx, source_type: str, source_id: UUID) -> str | None:
    if source_type in GOODS:
        return str((await resolve_subject(ctx, source_type, source_id))["label"])
    table = "vendor" if source_type == "SUPPLIER" else "customer"
    row = await one(ctx, f"SELECT name FROM erp.{table} WHERE {table}_id = :id", {"id": source_id})
    if row is None:
        raise HTTPException(422, f"The {table} does not exist")
    return str(row["name"])


async def load(ctx: Ctx, ncr_id: UUID) -> NcrOut | None:
    head = await one(ctx, f"{NCR_SQL} WHERE n.ncr_id = :id", {"id": ncr_id})
    if head is None or not visible(ctx, head["unit_id"]):
        return None
    extra = await one(
        ctx, "SELECT n.description, n.disposition_note, d.display_name AS disposition_by_name, "
        "(SELECT hold_id FROM erp.quality_hold h WHERE h.ncr_id = n.ncr_id AND h.released_at IS "
        "NULL LIMIT 1) AS open_hold_id FROM erp.ncr n LEFT JOIN erp.app_user d ON d.user_id = "
        "n.disposition_by WHERE n.ncr_id = :id", {"id": ncr_id})
    assert extra is not None
    steps = await rows(
        ctx, "SELECT s.step_code, s.step_code AS title, o.display_name AS owner_name, s.due_at, "
        "s.completed_at, c.display_name AS completed_by_name, s.action_text, "
        "s.evidence_file_key AS evidence_ref FROM erp.ncr_step s LEFT JOIN erp.app_user o ON "
        "o.user_id = s.owner_user_id LEFT JOIN erp.app_user c ON c.user_id = s.completed_by "
        "WHERE s.ncr_id = :id ORDER BY s.step_code", {"id": ncr_id})
    for step in steps:
        step["title"] = STEPS[step["step_code"]]
    try:
        label = await source_label(ctx, head["source_type"], head["source_id"])
    except HTTPException:
        label = None
    return NcrOut(**head, **extra, source_label=label, steps=[StepOut(**s) for s in steps])


async def require_ncr(ctx: Ctx, ncr_id: UUID, *allowed: str) -> dict[str, Any]:
    row = await one(
        ctx, "SELECT ncr_id, ncr_no, source_type, source_id, status, unit_id, disposition FROM "
        "erp.ncr WHERE ncr_id = :id FOR UPDATE", {"id": ncr_id})
    if row is None or not visible(ctx, row["unit_id"]):
        raise not_found("NCR")
    if row["status"] not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The NCR is {str(row['status']).lower().replace('_', ' ')}; this needs it to be "
            f"{' or '.join(a.lower().replace('_', ' ') for a in allowed)}")
    return row


async def holdable(ctx: Ctx, source_type: str, source_id: UUID) -> tuple[str, UUID] | None:
    """The thing a hold would freeze for an NCR source, if any."""
    if source_type == "GRN_LINE":
        line = await one(ctx, "SELECT lot_id FROM erp.grn_line WHERE grn_line_id = :id",
                         {"id": source_id})
        return ("STOCK_LOT", line["lot_id"]) if line else None
    if source_type in ("STOCK_LOT", "COMPOUND_BATCH", "PROD_LOT"):
        return source_type, source_id
    return None


@router.post("/ncrs", response_model=NcrOut, status_code=status.HTTP_201_CREATED)
async def raise_ncr(body: NcrIn, ctx: Ctx = Depends(manage)) -> NcrOut:
    unit_id: UUID | None = None
    label = await source_label(ctx, body.source_type, body.source_id)
    if body.source_type in GOODS:
        unit_id = (await resolve_subject(ctx, body.source_type, body.source_id))["unit_id"]
    if body.owner_user_id is not None and await one(
            ctx, "SELECT 1 AS x FROM erp.app_user WHERE user_id = :id AND status = 'ACTIVE'",
            {"id": body.owner_user_id}) is None:
        raise HTTPException(422, "The owner is not an active user")
    number = await next_number(ctx, "ncr")
    created = await one(
        ctx, "INSERT INTO erp.ncr(tenant_id, ncr_no, source_type, source_id, owner_user_id, "
        "title, description, severity, unit_id) VALUES (erp.current_tenant_id(), :n, :st, :sid, "
        ":o, :t, :d, :sev, :u) RETURNING ncr_id",
        {"n": number, "st": body.source_type, "sid": body.source_id,
         "o": body.owner_user_id or ctx.principal.profile.app_user_id, "t": body.title.strip(),
         "d": body.description, "sev": body.severity, "u": unit_id})
    assert created is not None
    ncr_id: UUID = created["ncr_id"]
    for code in STEPS:
        await rows(ctx, "INSERT INTO erp.ncr_step(tenant_id, ncr_id, step_code) VALUES "
                   "(erp.current_tenant_id(), :n, :c) RETURNING ncr_step_id",
                   {"n": ncr_id, "c": code})
    if body.hold:
        target = await holdable(ctx, body.source_type, body.source_id)
        if target is None:
            raise HTTPException(422, "There is no lot to hold for a supplier or customer NCR")
        await holds.place(ctx, target[0], target[1], f"NCR {number}: {body.title.strip()}", ncr_id)
    await audit(ctx, "ncr.raise", "ncr", ncr_id,
                {"ncr_no": number, "source_type": body.source_type, "source": label,
                 "severity": body.severity, "hold": body.hold})
    result = await load(ctx, ncr_id)
    assert result is not None
    return result


@router.get("/ncrs", response_model=Page[NcrSummary])
async def list_ncrs(
    ncr_status: str | None = Query(None, alias="status"), source_type: SourceType | None = None,
    severity: str | None = None, page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[NcrSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "n.unit_id", allow_null=True)
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for text_, key, value in (("n.status = :status", "status",
                               ncr_status.upper() if ncr_status else None),
                              ("n.source_type = :st", "st", source_type),
                              ("n.severity = :sev", "sev", severity.upper() if severity else None)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{NCR_SQL}{where} ORDER BY n.opened_at DESC, n.ncr_no DESC LIMIT "
                       ":limit OFFSET :offset", {**params, "limit": page.limit,
                                                 "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.ncr n{where}", params)
    return Page(items=[NcrSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/ncrs/{ncr_id}", response_model=NcrOut)
async def get_ncr(ncr_id: UUID, ctx: Ctx = Depends(read)) -> NcrOut:
    found = await load(ctx, ncr_id)
    if found is None:
        raise not_found("NCR")
    return found


@router.patch("/ncrs/{ncr_id}/steps/{step_code}", response_model=NcrOut)
async def update_step(
    ncr_id: UUID, step_code: Literal["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"],
    body: StepIn, ctx: Ctx = Depends(manage),
) -> NcrOut:
    """Record the work on a step; steps are completed in order."""
    await require_ncr(ctx, ncr_id, "OPEN", "IN_PROGRESS")
    step = await one(ctx, "SELECT action_text, completed_at FROM erp.ncr_step WHERE ncr_id = :n "
                     "AND step_code = :c FOR UPDATE", {"n": ncr_id, "c": step_code})
    assert step is not None
    if step["completed_at"] is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{step_code} is already completed")
    if body.owner_user_id is not None and await one(
            ctx, "SELECT 1 AS x FROM erp.app_user WHERE user_id = :id AND status = 'ACTIVE'",
            {"id": body.owner_user_id}) is None:
        raise HTTPException(422, "The owner is not an active user")
    action = body.action_text if body.action_text is not None else step["action_text"]
    if body.complete:
        if not action:
            raise HTTPException(422, "Describe the action taken before completing the step")
        earlier = [c for c in STEPS if c < step_code]
        if earlier and await count(
                ctx, "SELECT count(*) FROM erp.ncr_step WHERE ncr_id = :n AND step_code = "
                "ANY(CAST(:codes AS text[])) AND completed_at IS NULL",
                {"n": ncr_id, "codes": earlier}):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Complete the earlier steps before {step_code}")
    await rows(
        ctx, "UPDATE erp.ncr_step SET action_text = :a, owner_user_id = COALESCE(:o, "
        "owner_user_id), due_at = COALESCE(:d, due_at), evidence_file_key = COALESCE(:e, "
        "evidence_file_key), completed_at = CASE WHEN :done THEN now() END, completed_by = "
        "CASE WHEN :done THEN CAST(:me AS uuid) END WHERE ncr_id = :n AND step_code = :c "
        "RETURNING ncr_step_id",
        {"a": action, "o": body.owner_user_id, "d": body.due_at, "e": body.evidence_ref,
         "done": body.complete, "me": ctx.principal.profile.app_user_id, "n": ncr_id,
         "c": step_code})
    await rows(ctx, "UPDATE erp.ncr SET status = 'IN_PROGRESS' WHERE ncr_id = :id AND status = "
               "'OPEN' RETURNING ncr_id", {"id": ncr_id})
    await audit(ctx, "ncr.step", "ncr", ncr_id, {"step": step_code, "completed": body.complete})
    result = await load(ctx, ncr_id)
    assert result is not None
    return result


@router.post("/ncrs/{ncr_id}/disposition", response_model=NcrOut)
async def decide_disposition(
    ncr_id: UUID, body: DispositionIn, ctx: Ctx = Depends(manage)
) -> NcrOut:
    """Decide what happens to the goods; the open hold of this report follows the decision."""
    ncr = await require_ncr(ctx, ncr_id, "OPEN", "IN_PROGRESS")
    if ncr["disposition"] is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A disposition was already recorded")
    if ncr["source_type"] not in GOODS:
        raise HTTPException(422, "Only a report about goods has a disposition")
    final = body.disposition in ("SCRAP", "RETURN_TO_SUPPLIER")
    hold = await one(
        ctx, "SELECT hold_id, unit_id, subject_type, subject_id, previous_status, released_at "
        "FROM erp.quality_hold WHERE ncr_id = :n AND released_at IS NULL FOR UPDATE",
        {"n": ncr_id})
    if hold is not None:
        await holds.close(ctx, hold, f"{body.disposition}: {body.note.strip()}",
                          permanent=body.disposition if final else None)
    elif final:
        target = await holdable(ctx, ncr["source_type"], ncr["source_id"])
        if target is not None and target[0] == "STOCK_LOT":
            await rows(ctx, "UPDATE erp.lot SET status = 'BLOCKED' WHERE lot_id = :id AND status "
                       "IN ('AVAILABLE', 'QA_HOLD', 'QUARANTINE') RETURNING lot_id",
                       {"id": target[1]})
        elif target is not None and target[0] == "PROD_LOT":
            await rows(ctx, "UPDATE erp.prod_lot SET status = 'SCRAPPED' WHERE prod_lot_id = :id "
                       "AND status IN ('IN_PROCESS', 'READY') RETURNING prod_lot_id",
                       {"id": target[1]})
        elif target is not None:
            await rows(ctx, "UPDATE erp.compound_batch SET qa_status = 'FAILED' WHERE batch_id = "
                       ":id RETURNING batch_id", {"id": target[1]})
    await rows(
        ctx, "UPDATE erp.ncr SET disposition = :d, disposition_note = :n, disposition_by = :me, "
        "disposition_at = now(), status = 'IN_PROGRESS' WHERE ncr_id = :id RETURNING ncr_id",
        {"d": body.disposition, "n": body.note.strip(), "me": ctx.principal.profile.app_user_id,
         "id": ncr_id})
    await audit(ctx, "ncr.disposition", "ncr", ncr_id,
                {"ncr_no": ncr["ncr_no"], "disposition": body.disposition})
    result = await load(ctx, ncr_id)
    assert result is not None
    return result


@router.post("/ncrs/{ncr_id}/close", response_model=NcrOut)
async def close_ncr(ncr_id: UUID, ctx: Ctx = Depends(manage)) -> NcrOut:
    ncr = await require_ncr(ctx, ncr_id, "OPEN", "IN_PROGRESS")
    undone = await rows(ctx, "SELECT step_code FROM erp.ncr_step WHERE ncr_id = :n AND "
                        "completed_at IS NULL ORDER BY step_code", {"n": ncr_id})
    if undone:
        raise HTTPException(status.HTTP_409_CONFLICT, "Steps not completed: "
                            + ", ".join(s["step_code"] for s in undone))
    if ncr["source_type"] in GOODS and ncr["disposition"] is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Record the disposition before closing")
    if await one(ctx, "SELECT 1 AS x FROM erp.quality_hold WHERE ncr_id = :n AND released_at IS "
                 "NULL", {"n": ncr_id}):
        raise HTTPException(status.HTTP_409_CONFLICT, "The hold of this report is still open")
    await rows(ctx, "UPDATE erp.ncr SET status = 'CLOSED', closed_by = :me, closed_at = now() "
               "WHERE ncr_id = :id RETURNING ncr_id",
               {"me": ctx.principal.profile.app_user_id, "id": ncr_id})
    await audit(ctx, "ncr.close", "ncr", ncr_id, {"ncr_no": ncr["ncr_no"]})
    result = await load(ctx, ncr_id)
    assert result is not None
    return result


@router.post("/ncrs/{ncr_id}/cancel", response_model=NcrOut)
async def cancel_ncr(ncr_id: UUID, ctx: Ctx = Depends(manage)) -> NcrOut:
    ncr = await require_ncr(ctx, ncr_id, "OPEN", "IN_PROGRESS")
    if ncr["disposition"] is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A disposition was already acted on")
    if await one(ctx, "SELECT 1 AS x FROM erp.quality_hold WHERE ncr_id = :n AND released_at IS "
                 "NULL", {"n": ncr_id}):
        raise HTTPException(status.HTTP_409_CONFLICT, "Release the hold of this report first")
    await rows(ctx, "UPDATE erp.ncr SET status = 'CANCELLED' WHERE ncr_id = :id RETURNING ncr_id",
               {"id": ncr_id})
    await audit(ctx, "ncr.cancel", "ncr", ncr_id, {"ncr_no": ncr["ncr_no"]})
    result = await load(ctx, ncr_id)
    assert result is not None
    return result
