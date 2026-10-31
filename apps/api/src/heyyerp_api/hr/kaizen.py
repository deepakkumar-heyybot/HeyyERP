"""Kaizen suggestions: submission, evaluation, approval and savings (BRD FR-HR-05).

Anyone can submit a kaizen (kaizen.submit): a problem, the change proposed, a baseline and the
estimated saving. It is numbered KZ-yy-nnnn, belongs to the submitter's department and plant and
goes straight to approval (rule R-KZ: the department head, then the Plant Head). While the
department head has not decided it is SUBMITTED; once the second step is reached it shows as
UNDER_EVALUATION (worked out from the approval, not stored). The people who evaluate (kaizen.manage:
Plant Head, HR) give it a score up to the company rule `kaizenMaxScore` and a verified saving
before the final approval. An approved kaizen can be marked IMPLEMENTED and the saving it really
made recorded month by month. The submitter can correct or withdraw it until the first step is
decided. Suggestions are visible to everyone with the kaizen module in their plants.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from heyyerp_api.business_rules import service as rules
from heyyerp_api.hr.employees import my_employee
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
from heyyerp_api.security import has_permission
from heyyerp_api.workflow import engine
from heyyerp_api.workflow.registry import DocInfo, DocumentType, register

router = APIRouter(prefix="/kaizen", tags=["kaizen"])
submit_perm = requires("kaizen.submit")
manage = requires("kaizen.manage")
read = requires_module("kaizen")
Category = Literal["COST", "QUALITY", "SAFETY", "PRODUCTIVITY", "ENVIRONMENT", "OTHER"]
Money = Field(ge=0, le=Decimal(10_000_000_000), max_digits=18, decimal_places=2)
OptValue = Field(default=None, le=Decimal(10_000_000_000), max_digits=18, decimal_places=3)
OptMoney = Field(default=None, ge=0, le=Decimal(10_000_000_000), max_digits=18, decimal_places=2)


class KaizenIn(Strict):
    unit_id: UUID | None = None  # default: the submitter's plant
    department_id: UUID | None = None  # default: the submitter's department
    title: str = Field(min_length=3, max_length=160)
    category: Category = "OTHER"
    problem_description: str = Field(min_length=3, max_length=2000)
    proposed_change: str = Field(min_length=3, max_length=2000)
    baseline_value: Decimal | None = OptValue
    estimated_saving: Decimal | None = OptMoney


class KaizenPatch(Strict):
    title: str | None = Field(default=None, min_length=3, max_length=160)
    category: Category | None = None
    problem_description: str | None = Field(default=None, min_length=3, max_length=2000)
    proposed_change: str | None = Field(default=None, min_length=3, max_length=2000)
    baseline_value: Decimal | None = OptValue
    estimated_saving: Decimal | None = OptMoney


class EvaluateIn(Strict):
    score: Decimal = Field(ge=0, max_digits=6, decimal_places=2)
    verified_saving: Decimal = Money
    note: str | None = Field(default=None, max_length=1000)


class ImplementedIn(Strict):
    implemented_on: date
    achieved_value: Decimal | None = OptValue


class SavingIn(Strict):
    saving_month: date  # any day of the month
    amount: Decimal = Money
    note: str | None = Field(default=None, max_length=300)


class KaizenOut(BaseModel):
    kaizen_id: UUID
    kaizen_no: str | None
    unit_id: UUID
    department_id: UUID
    department_name: str
    submitter_name: str | None
    title: str
    category: str
    problem_description: str
    proposed_change: str
    baseline_value: Decimal | None
    achieved_value: Decimal | None
    estimated_saving: Decimal | None
    verified_saving: Decimal | None
    score: Decimal | None
    evaluation_note: str | None
    status: str
    submitted_at: Any
    approved_at: Any
    implemented_on: date | None
    realised_saving: Decimal
    approval_id: UUID | None


class SavingOut(BaseModel):
    saving_month: date
    amount: Decimal
    note: str | None


STATE = (
    "CASE WHEN k.status = 'SUBMITTED' AND COALESCE((SELECT i.current_step FROM "
    "erp.approval_instance i WHERE i.document_type = 'kaizen' AND i.document_id = k.kaizen_id "
    "AND i.is_current AND i.status = 'PENDING'), 1) > 1 THEN 'UNDER_EVALUATION' ELSE k.status "
    "END"
)
KZ_SQL = (
    "SELECT k.kaizen_id, k.kaizen_no, k.unit_id, k.department_id, d.name AS department_name, "
    "u.display_name AS submitter_name, k.title, k.category, k.problem_description, "
    "k.proposed_change, k.baseline_value, k.achieved_value, k.estimated_saving, "
    "k.verified_saving, k.score, k.evaluation_note, " + STATE + " AS status, k.submitted_at, "
    "k.approved_at, k.implemented_on, COALESCE((SELECT sum(s.amount) FROM erp.kaizen_saving s "
    "WHERE s.kaizen_id = k.kaizen_id), 0) AS realised_saving, (SELECT i.approval_id FROM "
    "erp.approval_instance i WHERE i.document_type = 'kaizen' AND i.document_id = k.kaizen_id "
    "AND i.is_current) "
    "AS approval_id FROM erp.kaizen k JOIN erp.department d ON d.department_id = "
    "k.department_id LEFT JOIN erp.app_user u ON u.user_id = k.submitter_id"
)


async def load(ctx: Ctx, kaizen_id: UUID) -> KaizenOut:
    row = await one(ctx, f"{KZ_SQL} WHERE k.kaizen_id = :id", {"id": kaizen_id})
    if row is None or not ctx.principal.may_see_unit(row["unit_id"]):
        raise not_found("Kaizen")
    return KaizenOut(**row)


async def lock(ctx: Ctx, kaizen_id: UUID) -> dict[str, Any]:
    row = await one(
        ctx, "SELECT kaizen_id, unit_id, submitter_id, status, kaizen_no FROM erp.kaizen WHERE "
        "kaizen_id = :id FOR UPDATE", {"id": kaizen_id})
    if row is None or not ctx.principal.may_see_unit(row["unit_id"]):
        raise not_found("Kaizen")
    return row


def is_owner(ctx: Ctx, row: dict[str, Any]) -> bool:
    return bool(row["submitter_id"] == ctx.principal.profile.app_user_id)


async def untouched(ctx: Ctx, kaizen_id: UUID) -> bool:
    """True while no approval step has been decided (the submitter may still change it)."""
    acted = await one(
        ctx, "SELECT 1 AS x FROM erp.approval_action a JOIN erp.approval_instance i ON "
        "i.approval_id = a.approval_id WHERE i.document_type = 'kaizen' AND i.document_id = :id "
        "AND i.is_current AND a.decision <> 'SKIP' LIMIT 1", {"id": kaizen_id})
    return acted is None


@router.post("", response_model=KaizenOut, status_code=status.HTTP_201_CREATED)
async def submit(body: KaizenIn, ctx: Ctx = Depends(submit_perm)) -> KaizenOut:
    mine = await my_employee(ctx)
    me = await one(ctx, "SELECT department_id FROM erp.app_user WHERE user_id = :u",
                   {"u": ctx.principal.profile.app_user_id})
    department_id = body.department_id or (mine["department_id"] if mine else None) or (
        me["department_id"] if me else None)
    unit_id = body.unit_id or (mine["unit_id"] if mine else None)
    if department_id is None or unit_id is None:
        raise HTTPException(422, "Say the plant and the department; none is linked to your user")
    require_in_scope(ctx, unit_id)
    dept = await one(ctx, "SELECT unit_id, active FROM erp.department WHERE department_id = :id",
                     {"id": department_id})
    if dept is None or not dept["active"] or (
            dept["unit_id"] is not None and dept["unit_id"] != unit_id):
        raise HTTPException(
            422, "The department does not exist, is inactive or is in another plant")
    number = await next_number(ctx, "kaizen")
    created = await one(
        ctx, "INSERT INTO erp.kaizen(tenant_id, unit_id, department_id, submitter_id, kaizen_no, "
        "title, category, problem_description, proposed_change, baseline_value, estimated_saving, "
        "status) VALUES (erp.current_tenant_id(), :u, :d, :me, :n, :t, :c, :p, :ch, :b, :s, "
        "'SUBMITTED') RETURNING kaizen_id",
        {"u": unit_id, "d": department_id, "me": ctx.principal.profile.app_user_id, "n": number,
         "t": body.title, "c": body.category, "p": body.problem_description,
         "ch": body.proposed_change, "b": body.baseline_value, "s": body.estimated_saving})
    assert created is not None
    kaizen_id: UUID = created["kaizen_id"]
    approval_id = await engine.submit(ctx, "kaizen", kaizen_id)
    await audit(ctx, "kaizen.submit", "kaizen", kaizen_id,
                {"kaizen_no": number, "approval_id": approval_id})
    return await load(ctx, kaizen_id)


async def load_for_approval(ctx: Ctx, kaizen_id: UUID) -> DocInfo | None:
    row = await one(
        ctx, "SELECT kaizen_no, title, status, department_id, estimated_saving FROM erp.kaizen "
        "WHERE kaizen_id = :id", {"id": kaizen_id})
    if row is None:
        return None
    return DocInfo(
        title=f"Kaizen {row['kaizen_no']}: {row['title']}", amount=row["estimated_saving"],
        department_id=row["department_id"], submittable=row["status"] == "SUBMITTED",
        reason=f"The kaizen is {str(row['status']).lower()}")


async def finalize_kaizen(ctx: Ctx, kaizen_id: UUID, approved: bool) -> None:
    await rows(ctx, "UPDATE erp.kaizen SET status = :s, approved_at = CASE WHEN :ok THEN now() END "
               "WHERE kaizen_id = :id RETURNING kaizen_id",
               {"s": "APPROVED" if approved else "REJECTED", "ok": approved, "id": kaizen_id})


register(DocumentType("kaizen", "kaizen.submit", load_for_approval, finalize_kaizen))


@router.patch("/{kaizen_id}", response_model=KaizenOut)
async def correct(kaizen_id: UUID, body: KaizenPatch, ctx: Ctx = Depends(submit_perm)) -> KaizenOut:
    row = await lock(ctx, kaizen_id)
    if not is_owner(ctx, row):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the submitter changes a kaizen")
    if row["status"] != "SUBMITTED" or not await untouched(ctx, kaizen_id):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "The kaizen is already being reviewed and can no longer be changed")
    changes = body.model_dump(exclude_unset=True)
    if changes:
        sets = ", ".join(f"{k} = :{k}" for k in changes)
        await rows(ctx, f"UPDATE erp.kaizen SET {sets} WHERE kaizen_id = :id RETURNING kaizen_id",
                   {**changes, "id": kaizen_id})
    await audit(ctx, "kaizen.update", "kaizen", kaizen_id, changes)
    return await load(ctx, kaizen_id)


@router.post("/{kaizen_id}/cancel", response_model=KaizenOut)
async def cancel(kaizen_id: UUID, ctx: Ctx = Depends(submit_perm)) -> KaizenOut:
    row = await lock(ctx, kaizen_id)
    if not (is_owner(ctx, row) or has_permission(ctx.principal, "kaizen.manage")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the submitter or an evaluator cancels")
    if row["status"] != "SUBMITTED":
        raise HTTPException(status.HTTP_409_CONFLICT, f"The kaizen is {row['status'].lower()}")
    await rows(ctx, "UPDATE erp.approval_instance SET status = 'CANCELLED', version = version + 1 "
               "WHERE document_type = 'kaizen' AND document_id = :id AND status = 'PENDING' "
               "RETURNING approval_id", {"id": kaizen_id})
    await rows(ctx, "UPDATE erp.kaizen SET status = 'CANCELLED' WHERE kaizen_id = :id RETURNING "
               "kaizen_id", {"id": kaizen_id})
    await audit(ctx, "kaizen.cancel", "kaizen", kaizen_id, {})
    return await load(ctx, kaizen_id)


@router.post("/{kaizen_id}/resubmit", response_model=KaizenOut)
async def resubmit(
    kaizen_id: UUID, body: KaizenPatch, ctx: Ctx = Depends(submit_perm)
) -> KaizenOut:
    """Send a rejected kaizen for approval again, with corrections if the submitter makes any;
    the earlier round stays in the approval history."""
    row = await lock(ctx, kaizen_id)
    if not is_owner(ctx, row):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the submitter resubmits a kaizen")
    if row["status"] != "REJECTED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a rejected kaizen can be resubmitted")
    changes = body.model_dump(exclude_unset=True)
    sets = "".join(f", {k} = :{k}" for k in changes)
    await rows(ctx, "UPDATE erp.kaizen SET status = 'SUBMITTED', score = NULL, verified_saving = "
               f"NULL, evaluation_note = NULL{sets} WHERE kaizen_id = :id RETURNING kaizen_id",
               {**changes, "id": kaizen_id})
    approval_id = await engine.submit(ctx, "kaizen", kaizen_id)
    await audit(ctx, "kaizen.resubmit", "kaizen", kaizen_id,
                {"approval_id": approval_id, **changes})
    return await load(ctx, kaizen_id)


@router.post("/{kaizen_id}/evaluate", response_model=KaizenOut)
async def evaluate(kaizen_id: UUID, body: EvaluateIn, ctx: Ctx = Depends(manage)) -> KaizenOut:
    """The evaluator's score and verified saving, given before the final approval."""
    row = await lock(ctx, kaizen_id)
    if row["status"] != "SUBMITTED":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Only a kaizen awaiting approval can be evaluated")
    if is_owner(ctx, row):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Nobody evaluates their own kaizen")
    top = Decimal(int(await rules.get_value(ctx, "kaizenMaxScore")))
    if body.score > top:
        raise HTTPException(422, f"The score is at most {top}")
    await rows(
        ctx, "UPDATE erp.kaizen SET score = :s, verified_saving = :v, evaluation_note = :n, "
        "evaluated_by = :me, evaluated_at = now() WHERE kaizen_id = :id RETURNING kaizen_id",
        {"s": body.score, "v": body.verified_saving, "n": body.note,
         "me": ctx.principal.profile.app_user_id, "id": kaizen_id})
    await audit(ctx, "kaizen.evaluate", "kaizen", kaizen_id,
                {"score": body.score, "verified_saving": body.verified_saving})
    return await load(ctx, kaizen_id)


@router.post("/{kaizen_id}/implemented", response_model=KaizenOut)
async def implemented(
    kaizen_id: UUID, body: ImplementedIn, ctx: Ctx = Depends(submit_perm)
) -> KaizenOut:
    row = await lock(ctx, kaizen_id)
    if not (is_owner(ctx, row) or has_permission(ctx.principal, "kaizen.manage")):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Only the submitter or an evaluator records the implementation")
    if row["status"] != "APPROVED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an approved kaizen is implemented")
    if body.implemented_on > date.today():
        raise HTTPException(422, "The implementation date is in the future")
    await rows(ctx, "UPDATE erp.kaizen SET status = 'IMPLEMENTED', implemented_on = :d, "
               "achieved_value = :a WHERE kaizen_id = :id RETURNING kaizen_id",
               {"d": body.implemented_on, "a": body.achieved_value, "id": kaizen_id})
    await audit(ctx, "kaizen.implemented", "kaizen", kaizen_id,
                {"implemented_on": body.implemented_on})
    return await load(ctx, kaizen_id)


@router.post("/{kaizen_id}/savings", response_model=list[SavingOut])
async def record_saving(
    kaizen_id: UUID, body: SavingIn, ctx: Ctx = Depends(manage)
) -> list[SavingOut]:
    """The saving a month really made (replaces what was recorded for that month)."""
    row = await lock(ctx, kaizen_id)
    if row["status"] not in ("APPROVED", "IMPLEMENTED"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Savings are recorded for an approved kaizen")
    month = body.saving_month.replace(day=1)
    if month > date.today().replace(day=1):
        raise HTTPException(422, "That month has not started")
    await rows(
        ctx, "INSERT INTO erp.kaizen_saving(tenant_id, kaizen_id, saving_month, amount, note, "
        "recorded_by) VALUES (erp.current_tenant_id(), :k, :m, :a, :n, :me) ON CONFLICT "
        "(kaizen_id, saving_month) DO UPDATE SET amount = EXCLUDED.amount, note = EXCLUDED.note, "
        "recorded_by = EXCLUDED.recorded_by, recorded_at = now() RETURNING kaizen_saving_id",
        {"k": kaizen_id, "m": month, "a": body.amount, "n": body.note,
         "me": ctx.principal.profile.app_user_id})
    await audit(ctx, "kaizen.saving", "kaizen", kaizen_id,
                {"month": month.isoformat(), "amount": body.amount})
    return await savings(kaizen_id, ctx)


@router.get("/{kaizen_id}/savings", response_model=list[SavingOut])
async def savings(kaizen_id: UUID, ctx: Ctx = Depends(read)) -> list[SavingOut]:
    await load(ctx, kaizen_id)
    data = await rows(ctx, "SELECT saving_month, amount, note FROM erp.kaizen_saving WHERE "
                      "kaizen_id = :id ORDER BY saving_month", {"id": kaizen_id})
    return [SavingOut(**d) for d in data]


@router.get("", response_model=Page[KaizenOut])
async def list_kaizen(
    unit_id: UUID | None = None, department_id: UUID | None = None, category: str | None = None,
    kaizen_status: str | None = Query(None, alias="status"), mine: bool = False,
    q: str | None = None, page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[KaizenOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "k.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    if mine:
        conditions.append("k.submitter_id = :me")
        params["me"] = ctx.principal.profile.app_user_id
    for text_, key, value in (
            ("k.unit_id = :u", "u", unit_id), ("k.department_id = :d", "d", department_id),
            ("k.category = :c", "c", category.upper() if category else None),
            (f"({STATE}) = :s", "s", kaizen_status.upper() if kaizen_status else None),
            ("(k.title ILIKE :q OR k.kaizen_no ILIKE :q)", "q", f"%{q}%" if q else None)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    data = await rows(ctx, f"{KZ_SQL}{where} ORDER BY k.submitted_at DESC LIMIT :limit OFFSET "
                      ":offset", {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.kaizen k{where}", params)
    return Page(items=[KaizenOut(**d) for d in data], total=total, limit=page.limit,
                offset=page.offset)


class Contributor(BaseModel):
    submitter_name: str | None
    kaizens: int
    approved: int
    verified_saving: Decimal


class Summary(BaseModel):
    year: int
    by_status: dict[str, int]
    estimated_saving: Decimal  # of approved and implemented kaizens
    verified_saving: Decimal
    realised_saving: Decimal
    top_contributors: list[Contributor]


@router.get("/summary/year", response_model=Summary)
async def summary(
    year: int | None = None, unit_id: UUID | None = None, ctx: Ctx = Depends(read)
) -> Summary:
    """The year's kaizens by status, the savings expected, verified and realised, and who gave
    the most."""
    chosen = year or date.today().year
    conditions = ["k.submitted_at >= make_date(:y, 1, 1)",
                  "k.submitted_at < make_date(:y + 1, 1, 1)"]
    params: dict[str, Any] = {"y": chosen}
    clause, scope_params = scope_clause(ctx, "k.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    if unit_id is not None:
        require_in_scope(ctx, unit_id)
        conditions.append("k.unit_id = :u")
        params["u"] = unit_id
    where = " AND ".join(conditions)
    by_status = {r["s"]: r["n"] for r in await rows(
        ctx, f"SELECT ({STATE}) AS s, count(*) AS n FROM erp.kaizen k WHERE {where} GROUP BY 1",
        params)}
    money = await one(
        ctx, "SELECT COALESCE(sum(k.estimated_saving), 0) AS est, COALESCE(sum(k.verified_saving), "
        "0) AS ver, COALESCE(sum((SELECT sum(s.amount) FROM erp.kaizen_saving s WHERE "
        f"s.kaizen_id = k.kaizen_id)), 0) AS real FROM erp.kaizen k WHERE {where} AND k.status IN "
        "('APPROVED', 'IMPLEMENTED')", params)
    assert money is not None
    top = await rows(
        ctx, "SELECT u.display_name AS submitter_name, count(*) AS kaizens, count(*) FILTER "
        "(WHERE k.status IN ('APPROVED', 'IMPLEMENTED')) AS approved, COALESCE(sum("
        "k.verified_saving) FILTER (WHERE k.status IN ('APPROVED', 'IMPLEMENTED')), 0) AS "
        f"verified_saving FROM erp.kaizen k LEFT JOIN erp.app_user u ON u.user_id = k.submitter_id "
        f"WHERE {where} GROUP BY u.user_id, u.display_name ORDER BY approved DESC, "
        "verified_saving DESC, kaizens DESC LIMIT 10", params)
    return Summary(year=chosen, by_status=by_status, estimated_saving=money["est"],
                   verified_saving=money["ver"], realised_saving=money["real"],
                   top_contributors=[Contributor(**t) for t in top])


@router.get("/{kaizen_id}", response_model=KaizenOut)
async def get_kaizen(kaizen_id: UUID, ctx: Ctx = Depends(read)) -> KaizenOut:
    return await load(ctx, kaizen_id)
