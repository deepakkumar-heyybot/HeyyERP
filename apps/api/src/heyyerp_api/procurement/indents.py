"""Purchase indents (requisitions): request materials, approve, then order (BRD FR-PRC-01).

DRAFT -> PENDING_APPROVAL -> APPROVED (or REJECTED) -> ORDERED (a purchase order covers it).
Type decides what may be requested: BOM indents ask for raw materials, consumable indents for
consumables, spares and other items, capital indents for capital items with a description and a
value (their total sets the approval band). Only a draft can be edited, only by its requester.
Raising needs indent.raise; reading needs the procurement module or being the requester.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
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
    db_errors,
    not_found,
    one,
    read_ctx,
    require_in_scope,
    requires,
    rows,
    scope_clause,
)
from heyyerp_api.procurement.numbering import next_number
from heyyerp_api.security import has_permission
from heyyerp_api.workflow import engine
from heyyerp_api.workflow.registry import DocInfo, DocumentType, register

router = APIRouter(prefix="/procurement/indents", tags=["procurement"])
raise_indent = requires("indent.raise")
IndentType = Literal["BOM", "CONSUMABLE", "CAPITAL"]
ALLOWED_ITEMS: dict[str, tuple[str, ...]] = {
    "BOM": ("RM",),
    "CONSUMABLE": ("CONSUMABLE", "SPARE", "OTHER"),
    "CAPITAL": ("CAPITAL",),
}


class LineIn(Strict):
    item_id: UUID
    requested_qty: Decimal = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)
    purpose: str | None = Field(default=None, max_length=200)
    capital_description: str | None = Field(default=None, max_length=200)
    capital_value: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)


class IndentIn(Strict):
    unit_id: UUID
    department_id: UUID | None = None
    indent_type: IndentType
    needed_by: date | None = None
    lines: list[LineIn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _lines_match_the_type(self) -> "IndentIn":
        capital = self.indent_type == "CAPITAL"
        for line in self.lines:
            has = bool(line.capital_description) or line.capital_value is not None
            if capital and not (line.capital_description and line.capital_value is not None):
                raise ValueError("Every capital line needs a description and a value")
            if not capital and has:
                raise ValueError("Only capital indents carry a capital description and value")
        return self


class IndentUpdate(Strict):
    department_id: UUID | None = None
    needed_by: date | None = None
    lines: list[LineIn] = Field(min_length=1, max_length=100)


class LineOut(BaseModel):
    line_no: int
    item_id: UUID
    item_code: str
    item_name: str
    uom_code: str
    requested_qty: Decimal
    purpose: str | None
    capital_description: str | None
    capital_value: Decimal | None


class IndentSummary(BaseModel):
    indent_id: UUID
    indent_no: str
    unit_id: UUID
    unit_code: str
    department_id: UUID | None
    requester_id: UUID
    requester_name: str
    indent_type: IndentType
    status: str
    needed_by: date | None
    created_at: datetime
    approval_id: UUID | None


class IndentOut(IndentSummary):
    capital_total: Decimal | None
    lines: list[LineOut]


IND_SQL = (
    "SELECT n.indent_id, n.indent_no, n.unit_id, un.code AS unit_code, n.department_id, "
    "n.requester_id, ru.display_name AS requester_name, n.indent_type, n.status, n.needed_by, "
    "n.created_at, a.approval_id FROM erp.indent n JOIN erp.unit un ON un.unit_id = n.unit_id "
    "JOIN erp.app_user ru ON ru.user_id = n.requester_id LEFT JOIN erp.approval_instance a "
    "ON a.document_type = 'indent' AND a.document_id = n.indent_id"
)


def sees_all(ctx: Ctx) -> bool:
    return "procurement" in ctx.principal.profile.modules


def visibility(ctx: Ctx) -> tuple[list[str], dict[str, Any]]:
    """Plant scope, and: without the procurement module you see only your own indents."""
    conditions: list[str] = []
    clause, params = scope_clause(ctx, "n.unit_id")
    if clause:
        conditions.append(clause)
    if not sees_all(ctx):
        conditions.append("n.requester_id = :me")
        params["me"] = ctx.principal.profile.app_user_id
    return conditions, params


async def load(ctx: Ctx, indent_id: UUID) -> IndentOut | None:
    conditions, params = visibility(ctx)
    conditions.insert(0, "n.indent_id = :id")
    where = " AND ".join(conditions)
    head = await one(ctx, f"{IND_SQL} WHERE {where}", {**params, "id": indent_id})
    if head is None:
        return None
    lines = await rows(
        ctx,
        "SELECT l.line_no, l.item_id, i.code AS item_code, i.name AS item_name, u.code AS "
        "uom_code, l.requested_qty, l.purpose, l.capital_description, l.capital_value "
        "FROM erp.indent_line l JOIN erp.item i ON i.item_id = l.item_id "
        "JOIN erp.uom u ON u.uom_id = i.uom_id WHERE l.indent_id = :id ORDER BY l.line_no",
        {"id": indent_id},
    )
    total = sum((ln["capital_value"] for ln in lines if ln["capital_value"] is not None),
                Decimal(0)) if head["indent_type"] == "CAPITAL" else None
    return IndentOut(**head, capital_total=total, lines=[LineOut(**ln) for ln in lines])


async def require_editable(ctx: Ctx, indent_id: UUID, *states: str) -> dict[str, Any]:
    indent = await one(
        ctx, "SELECT indent_id, unit_id, requester_id, indent_type, status FROM erp.indent "
        "WHERE indent_id = :id FOR UPDATE", {"id": indent_id},
    )
    if indent is None or not ctx.principal.may_see_unit(indent["unit_id"]) or (
        not sees_all(ctx) and indent["requester_id"] != ctx.principal.profile.app_user_id
    ):
        raise not_found("Indent")
    if indent["requester_id"] != ctx.principal.profile.app_user_id and not has_permission(
        ctx.principal, "admin"
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the requester can change an indent")
    if indent["status"] not in states:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The indent is {indent['status'].lower().replace('_', ' ')}")
    return indent


async def check_lines(ctx: Ctx, indent_type: str, lines: list[LineIn]) -> None:
    ids = [ln.item_id for ln in lines]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Each item may appear only once on an indent")
    found = {r["item_id"]: r for r in await rows(
        ctx, "SELECT item_id, code, item_type, active FROM erp.item "
        "WHERE item_id = ANY(CAST(:ids AS uuid[]))", {"ids": ids})}
    for line in lines:
        item = found.get(line.item_id)
        if item is None:
            raise HTTPException(422, "An item does not exist")
        if not item["active"]:
            raise HTTPException(422, f"Item {item['code']} is inactive")
        if item["item_type"] not in ALLOWED_ITEMS[indent_type]:
            raise HTTPException(
                422, f"A {indent_type.lower()} indent cannot request {item['item_type']} item "
                f"{item['code']}")


async def check_header(ctx: Ctx, unit_id: UUID, department_id: UUID | None,
                       needed_by: date | None) -> None:
    require_in_scope(ctx, unit_id)
    plant = await one(ctx, "SELECT active FROM erp.unit WHERE unit_id = :id", {"id": unit_id})
    if plant is None or not plant["active"]:
        raise HTTPException(422, "Plant does not exist or is inactive")
    if department_id is not None and await one(
        ctx, "SELECT 1 AS x FROM erp.department WHERE department_id = :id AND active",
        {"id": department_id},
    ) is None:
        raise HTTPException(422, "Department does not exist or is inactive")
    if needed_by is not None and needed_by < date.today():
        raise HTTPException(422, "The needed-by date cannot be in the past")


async def write_lines(ctx: Ctx, indent_id: UUID, lines: list[LineIn]) -> None:
    await rows(ctx, "DELETE FROM erp.indent_line WHERE indent_id = :id RETURNING indent_id",
               {"id": indent_id})
    for number, line in enumerate(lines, start=1):
        await one(
            ctx,
            "INSERT INTO erp.indent_line(tenant_id, indent_id, line_no, item_id, requested_qty, "
            "purpose, capital_description, capital_value) VALUES (erp.current_tenant_id(), "
            ":indent, :no, :item, :qty, :purpose, :desc, :value) RETURNING indent_line_id",
            {"indent": indent_id, "no": number, "item": line.item_id, "qty": line.requested_qty,
             "purpose": line.purpose, "desc": line.capital_description,
             "value": line.capital_value},
        )


@router.get("", response_model=Page[IndentSummary])
async def list_indents(
    indent_status: str | None = Query(None, alias="status"),
    indent_type: IndentType | None = None,
    unit_id: UUID | None = None,
    mine: bool = False,
    page: Paging = Depends(),
    ctx: Ctx = Depends(read_ctx),
) -> Page[IndentSummary]:
    conditions, params = visibility(ctx)
    for column, name, value in (("n.status", "status", indent_status and indent_status.upper()),
                                ("n.indent_type", "type", indent_type),
                                ("n.unit_id", "unit", unit_id)):
        if value is not None:
            conditions.append(f"{column} = :{name}")
            params[name] = value
    if mine:
        conditions.append("n.requester_id = :me")
        params["me"] = ctx.principal.profile.app_user_id
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{IND_SQL}{where} ORDER BY n.created_at DESC, n.indent_no DESC "
                       "LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.indent n{where}", params)
    return Page(items=[IndentSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/{indent_id}", response_model=IndentOut)
async def get_indent(indent_id: UUID, ctx: Ctx = Depends(read_ctx)) -> IndentOut:
    indent = await load(ctx, indent_id)
    if indent is None:
        raise not_found("Indent")
    return indent


@router.post("", response_model=IndentOut, status_code=status.HTTP_201_CREATED)
async def create_indent(body: IndentIn, ctx: Ctx = Depends(raise_indent)) -> IndentOut:
    await check_header(ctx, body.unit_id, body.department_id, body.needed_by)
    await check_lines(ctx, body.indent_type, body.lines)
    number = await next_number(ctx, "indent")
    with db_errors("Indent"):
        created = await one(
            ctx,
            "INSERT INTO erp.indent(tenant_id, unit_id, department_id, requester_id, indent_no, "
            "indent_type, needed_by) VALUES (erp.current_tenant_id(), :unit, :dept, :me, :no, "
            ":type, :needed) RETURNING indent_id",
            {"unit": body.unit_id, "dept": body.department_id,
             "me": ctx.principal.profile.app_user_id, "no": number, "type": body.indent_type,
             "needed": body.needed_by},
        )
    assert created is not None
    await write_lines(ctx, created["indent_id"], body.lines)
    await audit(ctx, "indent.create", "indent", created["indent_id"],
                {"indent_no": number, "type": body.indent_type, "lines": len(body.lines)})
    indent = await load(ctx, created["indent_id"])
    assert indent is not None
    return indent


@router.put("/{indent_id}", response_model=IndentOut)
async def update_indent(
    indent_id: UUID, body: IndentUpdate, ctx: Ctx = Depends(raise_indent)
) -> IndentOut:
    """Replace a draft's department, needed-by date and lines."""
    indent = await require_editable(ctx, indent_id, "DRAFT")
    await check_header(ctx, indent["unit_id"], body.department_id, body.needed_by)
    await check_lines(ctx, indent["indent_type"], body.lines)
    for line in body.lines:
        capital = indent["indent_type"] == "CAPITAL"
        if capital != (line.capital_value is not None):
            raise HTTPException(422, "Capital lines need a value; other lines must not have one")
        if capital and not line.capital_description:
            raise HTTPException(422, "Every capital line needs a description")
    await rows(ctx, "UPDATE erp.indent SET department_id = :d, needed_by = :n WHERE "
               "indent_id = :id RETURNING indent_id",
               {"d": body.department_id, "n": body.needed_by, "id": indent_id})
    await write_lines(ctx, indent_id, body.lines)
    await audit(ctx, "indent.update", "indent", indent_id, {"lines": len(body.lines)})
    result = await load(ctx, indent_id)
    assert result is not None
    return result


@router.post("/{indent_id}/submit", response_model=IndentOut)
async def submit_indent(indent_id: UUID, ctx: Ctx = Depends(raise_indent)) -> IndentOut:
    """Send a draft for approval; the approval rules decide the route (capital: by value)."""
    await require_editable(ctx, indent_id, "DRAFT")
    await rows(ctx, "UPDATE erp.indent SET status = 'PENDING_APPROVAL' WHERE indent_id = :id "
               "RETURNING indent_id", {"id": indent_id})
    approval_id = await engine.submit(ctx, "indent", indent_id)
    await audit(ctx, "indent.submit", "indent", indent_id, {"approval_id": approval_id})
    result = await load(ctx, indent_id)
    assert result is not None
    return result


@router.post("/{indent_id}/cancel", response_model=IndentOut)
async def cancel_indent(indent_id: UUID, ctx: Ctx = Depends(raise_indent)) -> IndentOut:
    """Cancel a draft, or an approved indent nobody has ordered yet."""
    await require_editable(ctx, indent_id, "DRAFT", "APPROVED")
    await rows(ctx, "UPDATE erp.indent SET status = 'CANCELLED' WHERE indent_id = :id "
               "RETURNING indent_id", {"id": indent_id})
    await audit(ctx, "indent.cancel", "indent", indent_id, {})
    result = await load(ctx, indent_id)
    assert result is not None
    return result


# ------------------------------------------------------------------ approval adapter
async def load_for_approval(ctx: Ctx, indent_id: UUID) -> DocInfo | None:
    head = await one(
        ctx, "SELECT indent_no, indent_type, status, department_id FROM erp.indent "
        "WHERE indent_id = :id", {"id": indent_id},
    )
    if head is None:
        return None
    amount = None
    if head["indent_type"] == "CAPITAL":
        total = await one(ctx, "SELECT COALESCE(sum(capital_value), 0) AS t FROM erp.indent_line "
                          "WHERE indent_id = :id", {"id": indent_id})
        amount = total["t"] if total else None
    return DocInfo(
        title=f"Indent {head['indent_no']} ({str(head['indent_type']).lower()})",
        amount=amount, department_id=head["department_id"],
        submittable=head["status"] == "PENDING_APPROVAL",
        reason=f"The indent is {str(head['status']).lower().replace('_', ' ')}",
    )


async def finalize_indent(ctx: Ctx, indent_id: UUID, approved: bool) -> None:
    await rows(ctx, "UPDATE erp.indent SET status = :s WHERE indent_id = :id RETURNING indent_id",
               {"s": "APPROVED" if approved else "REJECTED", "id": indent_id})


register(DocumentType("indent", "indent.raise", load_for_approval, finalize_indent))
