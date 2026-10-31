"""Yearly appraisals: self review, manager review, then HR approval (BRD FR-HR-04).

HR opens a year: every active employee who had joined by then gets an appraisal in SELF_REVIEW. The
employee scores themselves; the review then goes to their reporting manager (or anyone higher up the
line), who scores and submits it for approval (rule R-REV: the department head, then HR). While it
waits, HR can record its own score. Approval completes it with a final score (HR's if given, else
the manager's); a rejection ends it as REJECTED (HR reopens the year's review by raising a new
cycle after correcting it). Scores run from 0 up to the company rule `appraisalMaxScore`. The
employee sees only their own appraisal, a manager their team's, HR everyone's in their plants.
Opening and recording HR's score need hr.manage.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from heyyerp_api.business_rules import service as rules
from heyyerp_api.hr.employees import apply_leave, is_manager_of, manage, my_employee
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
    rows,
)
from heyyerp_api.security import has_permission
from heyyerp_api.workflow import engine
from heyyerp_api.workflow.registry import DocInfo, DocumentType, register

router = APIRouter(prefix="/hr", tags=["hr"])
Score = Field(ge=0, max_digits=5, decimal_places=2)


class OpenIn(Strict):
    fiscal_year: int = Field(ge=2000, le=2100)
    unit_id: UUID | None = None  # default: all the plants you can see


class ReviewIn(Strict):
    score: Decimal = Score
    comment: str | None = Field(default=None, max_length=1000)


class AppraisalOut(BaseModel):
    appraisal_id: UUID
    employee_id: UUID
    employee_no: str
    employee_name: str
    fiscal_year: int
    status: str
    self_score: Decimal | None
    self_comment: str | None
    manager_score: Decimal | None
    manager_comment: str | None
    hr_score: Decimal | None
    hr_comment: str | None
    final_score: Decimal | None
    approval_id: UUID | None


APP_SQL = (
    "SELECT a.appraisal_id, a.employee_id, e.employee_no, e.full_name AS employee_name, "
    "a.fiscal_year, a.status, a.self_score, a.self_comment, a.manager_score, a.manager_comment, "
    "a.hr_score, a.hr_comment, a.final_score, (SELECT i.approval_id FROM erp.approval_instance i "
    "WHERE i.document_type = 'review' AND i.document_id = a.appraisal_id) AS approval_id FROM "
    "erp.appraisal a JOIN erp.employee e ON e.employee_id = a.employee_id"
)


async def check_score(ctx: Ctx, score: Decimal) -> None:
    top = Decimal(int(await rules.get_value(ctx, "appraisalMaxScore")))
    if score > top:
        raise HTTPException(422, f"The score is at most {top}")


async def employee_of(ctx: Ctx, appraisal_id: UUID) -> dict[str, Any]:
    row = await one(
        ctx, "SELECT a.status, a.employee_id, e.unit_id, e.user_id, e.manager_id FROM "
        "erp.appraisal a JOIN erp.employee e ON e.employee_id = a.employee_id WHERE "
        "a.appraisal_id = :id FOR UPDATE OF a", {"id": appraisal_id})
    if row is None:
        raise not_found("Appraisal")
    return row


async def load(ctx: Ctx, appraisal_id: UUID) -> AppraisalOut:
    row = await one(ctx, f"{APP_SQL} WHERE a.appraisal_id = :id", {"id": appraisal_id})
    if row is None:
        raise not_found("Appraisal")
    boss = await one(ctx, "SELECT unit_id FROM erp.employee WHERE employee_id = :id",
                     {"id": row["employee_id"]})
    assert boss is not None
    mine = await my_employee(ctx)
    visible = (
        (has_permission(ctx.principal, "hr.manage") and ctx.principal.may_see_unit(boss["unit_id"]))
        or (mine is not None and mine["employee_id"] == row["employee_id"])
        or await is_manager_of(ctx, row["employee_id"]))
    if not visible:
        raise not_found("Appraisal")
    return AppraisalOut(**row)


@router.post("/appraisals/open")
async def open_cycle(body: OpenIn, ctx: Ctx = Depends(manage)) -> dict[str, int]:
    """Start the year's appraisals for every active employee who had joined by the year's end."""
    if body.unit_id is not None:
        require_in_scope(ctx, body.unit_id)
    conditions = ["e.employment_status = 'ACTIVE'",
                  "(e.date_joined IS NULL OR e.date_joined <= make_date(:y, 12, 31))"]
    params: dict[str, Any] = {"y": body.fiscal_year}
    if body.unit_id is not None:
        conditions.append("e.unit_id = :u")
        params["u"] = body.unit_id
    if ctx.principal.profile.unit_ids is not None:
        conditions.append("e.unit_id = ANY(CAST(:units AS uuid[]))")
        params["units"] = list(ctx.principal.profile.unit_ids)
    created = await rows(
        ctx, "INSERT INTO erp.appraisal(tenant_id, employee_id, fiscal_year) SELECT "
        "erp.current_tenant_id(), e.employee_id, :y FROM erp.employee e WHERE "
        + " AND ".join(conditions) + " ON CONFLICT (employee_id, fiscal_year) DO NOTHING "
        "RETURNING appraisal_id", params)
    await audit(ctx, "appraisal.open", "appraisal", f"year:{body.fiscal_year}",
                {"opened": len(created), "unit_id": body.unit_id})
    return {"opened": len(created)}


@router.post("/appraisals/{appraisal_id}/self", response_model=AppraisalOut)
async def self_review(
    appraisal_id: UUID, body: ReviewIn, ctx: Ctx = Depends(apply_leave)
) -> AppraisalOut:
    row = await employee_of(ctx, appraisal_id)
    if row["user_id"] != ctx.principal.profile.app_user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the employee scores themselves")
    if row["status"] != "SELF_REVIEW":
        raise HTTPException(status.HTTP_409_CONFLICT, "The self review is already done")
    await check_score(ctx, body.score)
    await rows(ctx, "UPDATE erp.appraisal SET self_score = :s, self_comment = :c, status = "
               "'MANAGER_REVIEW' WHERE appraisal_id = :id RETURNING appraisal_id",
               {"s": body.score, "c": body.comment, "id": appraisal_id})
    await audit(ctx, "appraisal.self", "appraisal", appraisal_id, {"score": body.score})
    return await load(ctx, appraisal_id)


@router.post("/appraisals/{appraisal_id}/manager", response_model=AppraisalOut)
async def manager_review(
    appraisal_id: UUID, body: ReviewIn, ctx: Ctx = Depends(apply_leave)
) -> AppraisalOut:
    """The reporting manager (or anyone above) scores and sends it for approval. HR may do it
    for an employee who has no manager."""
    row = await employee_of(ctx, appraisal_id)
    allowed = await is_manager_of(ctx, row["employee_id"]) or (
        row["manager_id"] is None and has_permission(ctx.principal, "hr.manage")
        and ctx.principal.may_see_unit(row["unit_id"]))
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the employee's manager reviews")
    if row["user_id"] == ctx.principal.profile.app_user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Nobody reviews their own appraisal")
    if row["status"] != "MANAGER_REVIEW":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "The employee's self review comes first" if row["status"] ==
                            "SELF_REVIEW" else "The manager review is already done")
    await check_score(ctx, body.score)
    await rows(ctx, "UPDATE erp.appraisal SET manager_score = :s, manager_comment = :c, status = "
               "'PENDING_APPROVAL' WHERE appraisal_id = :id RETURNING appraisal_id",
               {"s": body.score, "c": body.comment, "id": appraisal_id})
    approval_id = await engine.submit(ctx, "review", appraisal_id)
    await audit(ctx, "appraisal.manager", "appraisal", appraisal_id,
                {"score": body.score, "approval_id": approval_id})
    return await load(ctx, appraisal_id)


@router.post("/appraisals/{appraisal_id}/hr-score", response_model=AppraisalOut)
async def hr_score(
    appraisal_id: UUID, body: ReviewIn, ctx: Ctx = Depends(manage)
) -> AppraisalOut:
    """HR's own score while the review waits for approval; it becomes the final score."""
    row = await employee_of(ctx, appraisal_id)
    require_in_scope(ctx, row["unit_id"])
    if row["status"] != "PENDING_APPROVAL":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "HR scores a review that is awaiting approval")
    await check_score(ctx, body.score)
    await rows(ctx, "UPDATE erp.appraisal SET hr_score = :s, hr_comment = :c WHERE appraisal_id "
               "= :id RETURNING appraisal_id", {"s": body.score, "c": body.comment,
                                                "id": appraisal_id})
    await audit(ctx, "appraisal.hr_score", "appraisal", appraisal_id, {"score": body.score})
    return await load(ctx, appraisal_id)


async def load_for_approval(ctx: Ctx, appraisal_id: UUID) -> DocInfo | None:
    row = await one(
        ctx, "SELECT a.status, a.fiscal_year, e.full_name, e.department_id FROM erp.appraisal a "
        "JOIN erp.employee e ON e.employee_id = a.employee_id WHERE a.appraisal_id = :id",
        {"id": appraisal_id})
    if row is None:
        return None
    return DocInfo(
        title=f"Appraisal {row['fiscal_year']}, {row['full_name']}", amount=None,
        department_id=row["department_id"], submittable=row["status"] == "PENDING_APPROVAL",
        reason=f"The appraisal is {str(row['status']).lower().replace('_', ' ')}")


async def finalize_review(ctx: Ctx, appraisal_id: UUID, approved: bool) -> None:
    await rows(
        ctx, "UPDATE erp.appraisal SET status = :s, final_score = CASE WHEN :ok THEN "
        "COALESCE(hr_score, manager_score) END WHERE appraisal_id = :id RETURNING appraisal_id",
        {"s": "COMPLETED" if approved else "REJECTED", "ok": approved, "id": appraisal_id})


register(DocumentType("review", "leave.apply", load_for_approval, finalize_review))


@router.get("/appraisals", response_model=Page[AppraisalOut])
async def list_appraisals(
    fiscal_year: int | None = None, appraisal_status: str | None = Query(None, alias="status"),
    employee_id: UUID | None = None, page: Paging = Depends(), ctx: Ctx = Depends(apply_leave),
) -> Page[AppraisalOut]:
    """HR sees every appraisal in their plants; others their own and their team's."""
    conditions: list[str] = []
    params: dict[str, Any] = {"me": ctx.principal.profile.app_user_id}
    if has_permission(ctx.principal, "hr.manage"):
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
            ("a.fiscal_year = :y", "y", fiscal_year), ("a.employee_id = :e", "e", employee_id),
            ("a.status = :s", "s", appraisal_status.upper() if appraisal_status else None)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    data = await rows(ctx, f"{APP_SQL}{where} ORDER BY a.fiscal_year DESC, e.employee_no LIMIT "
                      ":limit OFFSET :offset", {**params, "limit": page.limit,
                                                "offset": page.offset})
    total = await count(ctx, "SELECT count(*) FROM erp.appraisal a JOIN erp.employee e ON "
                        f"e.employee_id = a.employee_id{where}", params)
    return Page(items=[AppraisalOut(**d) for d in data], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/appraisals/{appraisal_id}", response_model=AppraisalOut)
async def get_appraisal(appraisal_id: UUID, ctx: Ctx = Depends(apply_leave)) -> AppraisalOut:
    return await load(ctx, appraisal_id)
