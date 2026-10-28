"""Scrap and old-asset sales that go through approval before invoicing (BRD FR-SAL-08).

A scrap sale is drafted with what is being sold, to whom and for how much, then submitted. The
approval engine routes it by the `scrap` rule (Plant Head, then Accounts). An approved sale can be
invoiced once, which gives it an invoice number; a rejected one is final. Needs scrap.sale to
draft, submit and invoice; reading needs the sales module.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
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
from heyyerp_api.workflow import engine
from heyyerp_api.workflow.registry import DocInfo, DocumentType, register

router = APIRouter(prefix="/sales", tags=["sales"])
manage = requires("scrap.sale")
read = requires_module("sales")


class ScrapIn(Strict):
    unit_id: UUID
    customer_id: UUID | None = None
    description: str = Field(min_length=3, max_length=300)
    quantity: Decimal = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)
    amount: Decimal = Field(ge=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=2)


class ScrapOut(BaseModel):
    scrap_sale_id: UUID
    sale_no: str
    unit_id: UUID
    customer_id: UUID | None
    customer_code: str | None
    description: str
    quantity: Decimal
    amount: Decimal
    status: str
    invoice_no: str | None
    invoiced_at: datetime | None
    approval_id: UUID | None


SCRAP_SQL = (
    "SELECT s.scrap_sale_id, s.sale_no, s.unit_id, s.customer_id, c.code AS customer_code, "
    "s.description, s.quantity, s.amount, s.status, s.invoice_no, s.invoiced_at, "
    "(SELECT a.approval_id FROM erp.approval_instance a WHERE a.document_type = 'scrap' AND "
    "a.document_id = s.scrap_sale_id ORDER BY a.submitted_at DESC LIMIT 1) AS approval_id FROM "
    "erp.scrap_sale s LEFT JOIN erp.customer c ON c.customer_id = s.customer_id"
)


async def load(ctx: Ctx, scrap_id: UUID) -> ScrapOut | None:
    clause, params = scope_clause(ctx, "s.unit_id")
    row = await one(ctx, f"{SCRAP_SQL} WHERE s.scrap_sale_id = :id"
                    f"{' AND ' + clause if clause else ''}", {**params, "id": scrap_id})
    return ScrapOut(**row) if row else None


async def require_scrap(ctx: Ctx, scrap_id: UUID, *allowed: str) -> dict[str, Any]:
    clause, params = scope_clause(ctx, "unit_id")
    head = await one(
        ctx, "SELECT scrap_sale_id, sale_no, status, amount FROM erp.scrap_sale WHERE "
        f"scrap_sale_id = :id{' AND ' + clause if clause else ''} FOR UPDATE",
        {**params, "id": scrap_id})
    if head is None:
        raise not_found("Scrap sale")
    if head["status"] not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The scrap sale is {str(head['status']).lower().replace('_', ' ')}; this needs it "
            f"to be {' or '.join(a.lower().replace('_', ' ') for a in allowed)}")
    return head


@router.post("/scrap-sales", response_model=ScrapOut, status_code=status.HTTP_201_CREATED)
async def draft_scrap_sale(body: ScrapIn, ctx: Ctx = Depends(manage)) -> ScrapOut:
    if not ctx.principal.may_see_unit(body.unit_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your plant scope")
    unit = await one(ctx, "SELECT active FROM erp.unit WHERE unit_id = :id", {"id": body.unit_id})
    if unit is None or not unit["active"]:
        raise HTTPException(422, "The plant does not exist or is inactive")
    if body.customer_id is not None and await one(
            ctx, "SELECT 1 AS x FROM erp.customer WHERE customer_id = :id AND active",
            {"id": body.customer_id}) is None:
        raise HTTPException(422, "The customer does not exist or is inactive")
    number = await next_number(ctx, "scrap_sale")
    created = await one(
        ctx, "INSERT INTO erp.scrap_sale(tenant_id, unit_id, customer_id, sale_no, description, "
        "quantity, amount, created_by) VALUES (erp.current_tenant_id(), :u, :c, :n, :d, :q, :a, "
        ":me) RETURNING scrap_sale_id",
        {"u": body.unit_id, "c": body.customer_id, "n": number, "d": body.description.strip(),
         "q": body.quantity, "a": body.amount, "me": ctx.principal.profile.app_user_id})
    assert created is not None
    await audit(ctx, "scrap.draft", "scrap_sale", created["scrap_sale_id"],
                {"sale_no": number, "amount": body.amount})
    result = await load(ctx, created["scrap_sale_id"])
    assert result is not None
    return result


@router.post("/scrap-sales/{scrap_id}/submit", response_model=ScrapOut)
async def submit_scrap_sale(scrap_id: UUID, ctx: Ctx = Depends(manage)) -> ScrapOut:
    head = await require_scrap(ctx, scrap_id, "DRAFT")
    await rows(ctx, "UPDATE erp.scrap_sale SET status = 'PENDING_APPROVAL' WHERE scrap_sale_id = "
               ":id RETURNING scrap_sale_id", {"id": scrap_id})
    approval_id = await engine.submit(ctx, "scrap", scrap_id)
    await audit(ctx, "scrap.submit", "scrap_sale", scrap_id,
                {"sale_no": head["sale_no"], "approval_id": approval_id})
    result = await load(ctx, scrap_id)
    assert result is not None
    return result


@router.post("/scrap-sales/{scrap_id}/cancel", response_model=ScrapOut)
async def cancel_scrap_sale(scrap_id: UUID, ctx: Ctx = Depends(manage)) -> ScrapOut:
    await require_scrap(ctx, scrap_id, "DRAFT")
    await rows(ctx, "UPDATE erp.scrap_sale SET status = 'CANCELLED' WHERE scrap_sale_id = :id "
               "RETURNING scrap_sale_id", {"id": scrap_id})
    await audit(ctx, "scrap.cancel", "scrap_sale", scrap_id, {})
    result = await load(ctx, scrap_id)
    assert result is not None
    return result


@router.post("/scrap-sales/{scrap_id}/invoice", response_model=ScrapOut)
async def invoice_scrap_sale(scrap_id: UUID, ctx: Ctx = Depends(manage)) -> ScrapOut:
    """Only an approved scrap sale can be invoiced, and only once."""
    head = await require_scrap(ctx, scrap_id, "APPROVED")
    number = await next_number(ctx, "invoice")
    await rows(ctx, "UPDATE erp.scrap_sale SET status = 'INVOICED', invoice_no = :n, invoiced_at "
               "= now() WHERE scrap_sale_id = :id RETURNING scrap_sale_id",
               {"n": number, "id": scrap_id})
    await audit(ctx, "scrap.invoice", "scrap_sale", scrap_id,
                {"sale_no": head["sale_no"], "invoice_no": number, "amount": head["amount"]})
    result = await load(ctx, scrap_id)
    assert result is not None
    return result


@router.get("/scrap-sales", response_model=Page[ScrapOut])
async def list_scrap_sales(
    scrap_status: str | None = Query(None, alias="status"), page: Paging = Depends(),
    ctx: Ctx = Depends(read),
) -> Page[ScrapOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "s.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    if scrap_status:
        conditions.append("s.status = :st")
        params["st"] = scrap_status.upper()
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{SCRAP_SQL}{where} ORDER BY s.sale_no DESC LIMIT :limit OFFSET "
                       ":offset", {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.scrap_sale s{where}", params)
    return Page(items=[ScrapOut(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/scrap-sales/{scrap_id}", response_model=ScrapOut)
async def get_scrap_sale(scrap_id: UUID, ctx: Ctx = Depends(read)) -> ScrapOut:
    found = await load(ctx, scrap_id)
    if found is None:
        raise not_found("Scrap sale")
    return found


# ------------------------------------------------------------------ approval adapter
async def load_for_approval(ctx: Ctx, scrap_id: UUID) -> DocInfo | None:
    head = await one(ctx, "SELECT sale_no, amount, status FROM erp.scrap_sale WHERE "
                     "scrap_sale_id = :id", {"id": scrap_id})
    if head is None:
        return None
    return DocInfo(
        title=f"Scrap sale {head['sale_no']}", amount=head["amount"], department_id=None,
        submittable=head["status"] == "PENDING_APPROVAL",
        reason=f"The scrap sale is {str(head['status']).lower().replace('_', ' ')}")


async def finalize_scrap(ctx: Ctx, scrap_id: UUID, approved: bool) -> None:
    await rows(ctx, "UPDATE erp.scrap_sale SET status = :s WHERE scrap_sale_id = :id RETURNING "
               "scrap_sale_id", {"s": "APPROVED" if approved else "REJECTED", "id": scrap_id})


register(DocumentType("scrap", "scrap.sale", load_for_approval, finalize_scrap))
