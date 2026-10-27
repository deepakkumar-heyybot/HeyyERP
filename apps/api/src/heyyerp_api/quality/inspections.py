"""Inspection plans, inspection records and the QA queue (BRD FR-QA-01 to FR-QA-03, BR-06).

A plan lists what to measure for an item at incoming, in-process or final inspection, with limits;
one plan is active per item and kind, and a new plan retires the old one. An inspection is a
record of what QA measured on a GRN line, compound batch, production lot or stock lot. When the
item has a plan, every parameter must be measured and each value is judged against the limits; a
failing value cannot be recorded as ACCEPT. The release steps check this record: a GRN line cannot
be accepted, and a lot cannot leave a QA gate, without a passing inspection after it arrived when
the item has a plan, and never after a REJECT or HOLD inspection. Where there is no plan, QA's
decision is recorded automatically so the history is complete. Needs qa.approve to record or
change plans; reading needs the quality module.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Self
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

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
from heyyerp_api.quality import instruments

router = APIRouter(prefix="/quality", tags=["quality"])
manage = requires("qa.approve")
read = requires_module("quality")
Kind = Literal["INCOMING", "IN_PROCESS", "FINAL"]
SubjectType = Literal["GRN_LINE", "COMPOUND_BATCH", "PROD_LOT", "STOCK_LOT"]
Result = Literal["ACCEPT", "PARTIAL", "REJECT", "HOLD"]
LIMIT = Field(default=None, max_digits=18, decimal_places=6)
CODE = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


# ------------------------------------------------------------------ plans
class ParamIn(Strict):
    parameter_code: str = CODE
    name: str = Field(min_length=2, max_length=120)
    unit: str | None = Field(default=None, max_length=20)
    min_value: Decimal | None = LIMIT
    max_value: Decimal | None = LIMIT
    is_key: bool = False

    @model_validator(mode="after")
    def _limits(self) -> Self:
        if self.min_value is None and self.max_value is None:
            raise ValueError("A parameter needs a minimum, a maximum or both")
        if (self.min_value is not None and self.max_value is not None
                and self.max_value < self.min_value):
            raise ValueError("The maximum is below the minimum")
        return self


class PlanIn(Strict):
    item_id: UUID
    kind: Kind
    name: str = Field(min_length=2, max_length=200)
    params: list[ParamIn] = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        codes = [p.parameter_code.upper() for p in self.params]
        if len(set(codes)) != len(codes):
            raise ValueError("Each parameter code may appear only once")
        return self


class ParamOut(BaseModel):
    parameter_code: str
    name: str
    unit: str | None
    min_value: Decimal | None
    max_value: Decimal | None
    is_key: bool


class PlanOut(BaseModel):
    plan_id: UUID
    item_id: UUID
    item_code: str
    kind: str
    name: str
    active: bool
    created_at: datetime
    params: list[ParamOut]


PLAN_SQL = ("SELECT p.plan_id, p.item_id, i.code AS item_code, p.kind, p.name, p.active, "
            "p.created_at FROM erp.inspection_plan p JOIN erp.item i ON i.item_id = p.item_id")


async def load_plan(ctx: Ctx, plan_id: UUID) -> PlanOut | None:
    head = await one(ctx, f"{PLAN_SQL} WHERE p.plan_id = :id", {"id": plan_id})
    if head is None:
        return None
    params = await rows(
        ctx, "SELECT parameter_code, name, unit, min_value, max_value, is_key FROM "
        "erp.inspection_plan_param WHERE plan_id = :id ORDER BY line_no", {"id": plan_id})
    return PlanOut(**head, params=[ParamOut(**p) for p in params])


async def active_plan(ctx: Ctx, item_id: UUID | None, kind: str) -> PlanOut | None:
    if item_id is None:
        return None
    found = await one(ctx, "SELECT plan_id FROM erp.inspection_plan WHERE item_id = :i AND "
                      "kind = :k AND active", {"i": item_id, "k": kind})
    return await load_plan(ctx, found["plan_id"]) if found else None


@router.post("/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(body: PlanIn, ctx: Ctx = Depends(manage)) -> PlanOut:
    """Define what to measure; the item's previous plan of this kind is retired."""
    item = await one(ctx, "SELECT active FROM erp.item WHERE item_id = :id", {"id": body.item_id})
    if item is None or not item["active"]:
        raise HTTPException(422, "The item does not exist or is inactive")
    await rows(ctx, "UPDATE erp.inspection_plan SET active = false WHERE item_id = :i AND "
               "kind = :k AND active RETURNING plan_id", {"i": body.item_id, "k": body.kind})
    created = await one(
        ctx, "INSERT INTO erp.inspection_plan(tenant_id, item_id, kind, name, created_by) VALUES "
        "(erp.current_tenant_id(), :i, :k, :n, :me) RETURNING plan_id",
        {"i": body.item_id, "k": body.kind, "n": body.name,
         "me": ctx.principal.profile.app_user_id})
    assert created is not None
    for number, p in enumerate(body.params, start=1):
        await rows(
            ctx, "INSERT INTO erp.inspection_plan_param(tenant_id, plan_id, line_no, "
            "parameter_code, name, unit, min_value, max_value, is_key) VALUES "
            "(erp.current_tenant_id(), :pl, :n, :c, :nm, :u, :mn, :mx, :k) RETURNING param_id",
            {"pl": created["plan_id"], "n": number, "c": p.parameter_code.upper(), "nm": p.name,
             "u": p.unit, "mn": p.min_value, "mx": p.max_value, "k": p.is_key})
    await audit(ctx, "plan.create", "inspection_plan", created["plan_id"],
                {"item_id": body.item_id, "kind": body.kind, "parameters": len(body.params)})
    result = await load_plan(ctx, created["plan_id"])
    assert result is not None
    return result


@router.get("/plans", response_model=Page[PlanOut])
async def list_plans(
    item_id: UUID | None = None, kind: Kind | None = None, active: bool | None = Query(None),
    page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[PlanOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    for text_, key, value in (("p.item_id = :item", "item", item_id), ("p.kind = :kind", "kind",
                              kind), ("p.active = :active", "active", active)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    heads = await rows(ctx, f"SELECT p.plan_id FROM erp.inspection_plan p{where} ORDER BY "
                       "p.created_at DESC LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    items = [x for h in heads if (x := await load_plan(ctx, h["plan_id"])) is not None]
    total = await count(ctx, f"SELECT count(*) FROM erp.inspection_plan p{where}", params)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/plans/{plan_id}", response_model=PlanOut)
async def get_plan(plan_id: UUID, ctx: Ctx = Depends(read)) -> PlanOut:
    found = await load_plan(ctx, plan_id)
    if found is None:
        raise not_found("Inspection plan")
    return found


@router.post("/plans/{plan_id}/retire", response_model=PlanOut)
async def retire_plan(plan_id: UUID, ctx: Ctx = Depends(manage)) -> PlanOut:
    plan = await get_plan(plan_id, ctx)
    if not plan.active:
        raise HTTPException(status.HTTP_409_CONFLICT, "The plan is already retired")
    await rows(ctx, "UPDATE erp.inspection_plan SET active = false WHERE plan_id = :id "
               "RETURNING plan_id", {"id": plan_id})
    await audit(ctx, "plan.retire", "inspection_plan", plan_id, {})
    return await get_plan(plan_id, ctx)


# ------------------------------------------------------------------ inspections
class MeasureIn(Strict):
    parameter_code: str = CODE
    value: Decimal = Field(max_digits=18, decimal_places=6)
    unit: str | None = Field(default=None, max_length=20)


class InspectionIn(Strict):
    kind: Kind
    subject_type: SubjectType
    subject_id: UUID
    result: Result
    notes: str | None = Field(default=None, max_length=500)
    instrument_id: UUID | None = None
    measurements: list[MeasureIn] = Field(default_factory=list, max_length=60)


class MeasurementOut(BaseModel):
    parameter_code: str
    measured_value: Decimal | None
    unit: str | None
    min_value: Decimal | None
    max_value: Decimal | None
    result: str | None


class InspectionSummary(BaseModel):
    inspection_id: UUID
    inspection_no: str
    kind: str | None
    subject_type: str | None
    subject_id: UUID | None
    result: str
    source: str
    inspected_by_name: str
    inspected_at: datetime
    notes: str | None
    unit_id: UUID | None


class InspectionOut(InspectionSummary):
    plan_name: str | None
    instrument_code: str | None
    measurements: list[MeasurementOut]


INSPECTION_SQL = (
    "SELECT q.inspection_id, q.inspection_no, q.kind, q.subject_type, q.subject_id, q.result, "
    "q.source, u.display_name AS inspected_by_name, q.inspected_at, q.notes, q.unit_id FROM "
    "erp.qa_inspection q JOIN erp.app_user u ON u.user_id = q.inspected_by"
)


async def load_inspection(ctx: Ctx, inspection_id: UUID) -> InspectionOut | None:
    head = await one(ctx, f"{INSPECTION_SQL} WHERE q.inspection_id = :id", {"id": inspection_id})
    if head is None or (head["unit_id"] is not None
                        and not ctx.principal.may_see_unit(head["unit_id"])):
        return None
    extra = await one(
        ctx, "SELECT p.name AS plan_name, i.code AS instrument_code FROM erp.qa_inspection q "
        "LEFT JOIN erp.inspection_plan p ON p.plan_id = q.plan_id LEFT JOIN erp.instrument i ON "
        "i.instrument_id = q.instrument_id WHERE q.inspection_id = :id", {"id": inspection_id})
    assert extra is not None
    measures = await rows(
        ctx, "SELECT parameter_code, measured_value, unit, min_value, max_value, result FROM "
        "erp.qa_measurement WHERE inspection_id = :id ORDER BY parameter_code",
        {"id": inspection_id})
    return InspectionOut(**head, **extra, measurements=[MeasurementOut(**m) for m in measures])


async def resolve_subject(ctx: Ctx, subject_type: str, subject_id: UUID) -> dict[str, Any]:
    """Plant, item, label and arrival time of what is being inspected; 404 outside the scope."""
    queries = {
        "GRN_LINE": (
            "SELECT g.unit_id, l.item_id, g.grn_no || '/' || t.lot_no AS label, g.received_at AS "
            "since FROM erp.grn_line l JOIN erp.grn g ON g.grn_id = l.grn_id JOIN erp.lot t ON "
            "t.lot_id = l.lot_id WHERE l.grn_line_id = :id"),
        "COMPOUND_BATCH": (
            "SELECT b.unit_id, c.output_item_id AS item_id, b.batch_no AS label, "
            "COALESCE(b.mixed_at, b.weighed_at) AS since FROM erp.compound_batch b JOIN "
            "erp.compound c ON c.compound_id = b.compound_id WHERE b.batch_id = :id"),
        "PROD_LOT": (
            "SELECT l.unit_id, l.item_id, l.lot_no AS label, (SELECT max(m.moved_at) FROM "
            "erp.prod_lot_move m WHERE m.prod_lot_id = l.prod_lot_id) AS since FROM "
            "erp.prod_lot l WHERE l.prod_lot_id = :id"),
        "STOCK_LOT": ("SELECT unit_id, item_id, lot_no AS label, received_at AS since FROM "
                      "erp.lot WHERE lot_id = :id"),
    }
    found = await one(ctx, queries[subject_type], {"id": subject_id})
    if found is None or not ctx.principal.may_see_unit(found["unit_id"]):
        raise not_found("Inspected item")
    return found


def judge(value: Decimal, low: Decimal | None, high: Decimal | None) -> str:
    return "FAIL" if (low is not None and value < low) or (high is not None and value > high) \
        else "PASS"


async def save(
    ctx: Ctx, *, kind: str, subject_type: str, subject_id: UUID, unit_id: UUID, result: str,
    notes: str | None, source: str, plan_id: UUID | None = None, instrument_id: UUID | None = None,
    measures: list[dict[str, Any]] | None = None,
) -> UUID:
    number = await next_number(ctx, "inspection")
    created = await one(
        ctx, "INSERT INTO erp.qa_inspection(tenant_id, inspection_no, inspected_by, result, notes, "
        "kind, unit_id, subject_type, subject_id, plan_id, instrument_id, source) VALUES "
        "(erp.current_tenant_id(), :n, :me, :r, :notes, :k, :u, :st, :sid, :p, :i, :src) "
        "RETURNING inspection_id",
        {"n": number, "me": ctx.principal.profile.app_user_id, "r": result, "notes": notes,
         "k": kind, "u": unit_id, "st": subject_type, "sid": subject_id, "p": plan_id,
         "i": instrument_id, "src": source})
    assert created is not None
    for m in measures or []:
        await rows(
            ctx, "INSERT INTO erp.qa_measurement(tenant_id, inspection_id, parameter_code, "
            "measured_value, unit, result, min_value, max_value) VALUES (erp.current_tenant_id(), "
            ":i, :c, :v, :u, :r, :mn, :mx) RETURNING measurement_id",
            {"i": created["inspection_id"], "c": m["code"], "v": m["value"], "u": m["unit"],
             "r": m["result"], "mn": m["min"], "mx": m["max"]})
    return created["inspection_id"]  # type: ignore[no-any-return]


@router.post("/inspections", response_model=InspectionOut, status_code=status.HTTP_201_CREATED)
async def record_inspection(body: InspectionIn, ctx: Ctx = Depends(manage)) -> InspectionOut:
    subject = await resolve_subject(ctx, body.subject_type, body.subject_id)
    plan = await active_plan(ctx, subject["item_id"], body.kind)
    if body.instrument_id is not None:
        used = await instruments.usable(ctx, body.instrument_id)
        if used.unit_id != subject["unit_id"]:
            raise HTTPException(422, "The instrument belongs to another plant")
    codes = [m.parameter_code.upper() for m in body.measurements]
    if len(set(codes)) != len(codes):
        raise HTTPException(422, "Each parameter may be measured only once")
    limits = {p.parameter_code: p for p in plan.params} if plan else {}
    if plan:
        missing = [c for c in limits if c not in codes]
        unknown = [c for c in codes if c not in limits]
        if missing or unknown:
            raise HTTPException(
                422, "The inspection plan needs exactly its parameters"
                + (f"; missing {', '.join(missing)}" if missing else "")
                + (f"; not in the plan: {', '.join(unknown)}" if unknown else ""))
    measures = []
    for m in body.measurements:
        limit = limits.get(m.parameter_code.upper())
        low, high = (limit.min_value, limit.max_value) if limit else (None, None)
        measures.append({"code": m.parameter_code.upper(), "value": m.value,
                         "unit": m.unit or (limit.unit if limit else None), "min": low,
                         "max": high, "result": judge(m.value, low, high) if limit else "INFO"})
    failed: list[str] = [str(m["code"]) for m in measures if m["result"] == "FAIL"]
    if failed and body.result in ("ACCEPT", "PARTIAL"):
        raise HTTPException(
            422, f"Measurements outside the limits ({', '.join(failed)}) cannot be accepted; "
            "record REJECT or HOLD")
    inspection_id = await save(
        ctx, kind=body.kind, subject_type=body.subject_type, subject_id=body.subject_id,
        unit_id=subject["unit_id"], result=body.result, notes=body.notes, source="MANUAL",
        plan_id=plan.plan_id if plan else None, instrument_id=body.instrument_id, measures=measures)
    await audit(ctx, "inspection.record", "inspection", inspection_id,
                {"subject_type": body.subject_type, "subject": subject["label"],
                 "result": body.result, "failed": failed})
    result = await load_inspection(ctx, inspection_id)
    assert result is not None
    return result


@router.get("/inspections", response_model=Page[InspectionSummary])
async def list_inspections(
    subject_type: SubjectType | None = None, subject_id: UUID | None = None,
    kind: Kind | None = None, result: Result | None = None,
    page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[InspectionSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "q.unit_id", allow_null=True)
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for text_, key, value in (("q.subject_type = :st", "st", subject_type),
                              ("q.subject_id = :sid", "sid", subject_id),
                              ("q.kind = :kind", "kind", kind), ("q.result = :res", "res", result)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{INSPECTION_SQL}{where} ORDER BY q.inspected_at DESC, "
                       "q.inspection_no DESC LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.qa_inspection q{where}", params)
    return Page(items=[InspectionSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/inspections/{inspection_id}", response_model=InspectionOut)
async def get_inspection(inspection_id: UUID, ctx: Ctx = Depends(read)) -> InspectionOut:
    found = await load_inspection(ctx, inspection_id)
    if found is None:
        raise not_found("Inspection")
    return found


# ------------------------------------------------------------------ hooks for the release steps
async def clearance(
    ctx: Ctx, *, kind: str, subject_type: str, subject_id: UUID, item_id: UUID | None,
    since: datetime | None,
) -> None:
    """Refuse a release unless the inspection record allows it.

    The latest inspection after the subject arrived must not be REJECT or HOLD; and when the item
    has an active plan for this kind, there must be an ACCEPT or PARTIAL inspection after arrival.
    """
    latest = await one(
        ctx, "SELECT result, source, inspection_no FROM erp.qa_inspection WHERE subject_type = :t "
        "AND subject_id = :i AND source = 'MANUAL' AND (CAST(:since AS timestamptz) IS NULL OR "
        "inspected_at >= :since) ORDER BY inspected_at DESC LIMIT 1",
        {"t": subject_type, "i": subject_id, "since": since})
    if latest is not None and latest["result"] in ("REJECT", "HOLD"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Inspection {latest['inspection_no']} is {latest['result'].lower()}; it cannot be "
            "released until QA records a passing inspection or resolves it")
    if latest is None and await active_plan(ctx, item_id, kind) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Record the {kind.lower().replace('_', '-')} inspection first: the item has an "
            "inspection plan")


async def record_decision(
    ctx: Ctx, *, kind: str, subject_type: str, subject_id: UUID, unit_id: UUID, result: str,
    notes: str | None,
) -> None:
    """Keep the history complete: QA's release decision itself is recorded as an inspection."""
    await save(ctx, kind=kind, subject_type=subject_type, subject_id=subject_id, unit_id=unit_id,
               result=result, notes=notes, source="AUTO")


# ------------------------------------------------------------------ the QA queue
class QueueRow(BaseModel):
    subject_type: str
    subject_id: UUID
    reference: str
    item_code: str | None
    quantity: Decimal
    stage: str | None
    waiting_since: datetime
    unit_id: UUID
    inspected: bool


@router.get("/queue", response_model=list[QueueRow])
async def qa_queue(
    subject_type: SubjectType | None = None, ctx: Ctx = Depends(read)
) -> list[QueueRow]:
    """Everything waiting for a QA decision: GRN lots, compound batches and stage lots."""
    out: list[QueueRow] = []
    clause, params = scope_clause(ctx, "{u}")

    def scoped(column: str) -> tuple[str, dict[str, Any]]:
        return (f" AND {clause.replace('{u}', column)}" if clause else ""), params

    def rewrite(sql: str, column: str) -> str:
        return sql.replace("{scope}", scoped(column)[0])

    if subject_type in (None, "GRN_LINE"):
        data = await rows(
            ctx, rewrite(
                "SELECT l.grn_line_id AS subject_id, g.grn_no || '/' || t.lot_no AS reference, "
                "i.code AS item_code, l.received_qty AS quantity, g.received_at AS waiting_since, "
                "g.unit_id, EXISTS (SELECT 1 FROM erp.qa_inspection q WHERE q.subject_type = "
                "'GRN_LINE' AND q.subject_id = l.grn_line_id AND q.source = 'MANUAL') AS "
                "inspected FROM erp.grn_line l JOIN erp.grn g ON g.grn_id = l.grn_id JOIN "
                "erp.lot t ON t.lot_id = l.lot_id JOIN erp.item i ON i.item_id = l.item_id WHERE "
                "l.accepted_qty + l.rejected_qty = 0 AND t.status = 'QA_HOLD'{scope} ORDER BY "
                "g.received_at", "g.unit_id"), params)
        out += [QueueRow(subject_type="GRN_LINE", stage=None, **d) for d in data]
    if subject_type in (None, "COMPOUND_BATCH"):
        data = await rows(
            ctx, rewrite(
                "SELECT b.batch_id AS subject_id, b.batch_no AS reference, b.compound_code AS "
                "item_code, b.remaining_kg AS quantity, b.mixed_at AS waiting_since, b.unit_id, "
                "EXISTS (SELECT 1 FROM erp.qa_inspection q WHERE q.subject_type = "
                "'COMPOUND_BATCH' AND q.subject_id = b.batch_id AND q.source = 'MANUAL') AS "
                "inspected FROM erp.v_compound_batch_status b WHERE b.status = 'AWAITING_QA'"
                "{scope} ORDER BY b.mixed_at", "b.unit_id"), params)
        out += [QueueRow(subject_type="COMPOUND_BATCH", stage="AWAITING_QA", **d) for d in data]
    if subject_type in (None, "PROD_LOT"):
        data = await rows(
            ctx, rewrite(
                "SELECT l.prod_lot_id AS subject_id, l.lot_no AS reference, i.code AS item_code, "
                "l.quantity, s.stage_code AS stage, (SELECT max(m.moved_at) FROM "
                "erp.prod_lot_move m WHERE m.prod_lot_id = l.prod_lot_id) AS waiting_since, "
                "l.unit_id, EXISTS (SELECT 1 FROM erp.qa_inspection q WHERE q.subject_type = "
                "'PROD_LOT' AND q.subject_id = l.prod_lot_id AND q.source = 'MANUAL' AND "
                "q.inspected_at >= (SELECT max(m.moved_at) FROM erp.prod_lot_move m WHERE "
                "m.prod_lot_id = l.prod_lot_id)) AS inspected FROM erp.prod_lot l JOIN "
                "erp.route_stage s ON s.route_stage_id = l.current_stage_id JOIN erp.item i ON "
                "i.item_id = l.item_id WHERE l.status = 'IN_PROCESS' AND s.is_qa_gate{scope} "
                "ORDER BY waiting_since", "l.unit_id"), params)
        out += [QueueRow(subject_type="PROD_LOT", **d) for d in data]
    return out
