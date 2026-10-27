"""Approvals: submit, inbox, decide, and the SLA sweep."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from heyyerp_api.masters.common import Ctx, Page, Paging, Strict, read_ctx, requires
from heyyerp_api.workflow import engine

router = APIRouter(prefix="/approvals", tags=["approvals"])
sweep_permission = requires("admin")
State = Literal["PENDING", "APPROVED", "REJECTED", "CANCELLED"]


class SubmitIn(Strict):
    document_type: str = Field(min_length=1, max_length=30)
    document_id: UUID


class DecideIn(Strict):
    decision: Literal["APPROVE", "REJECT"]
    note: str | None = Field(default=None, max_length=500)
    step: int | None = Field(default=None, ge=1, description="step you saw; stale means 409")

    @model_validator(mode="after")
    def _reject_needs_a_reason(self) -> "DecideIn":
        if self.decision == "REJECT" and not (self.note and self.note.strip()):
            raise ValueError("A rejection needs a note explaining why")
        return self


class ApprovalSummary(BaseModel):
    approval_id: UUID
    document_type: str
    document_id: UUID
    title: str | None
    amount: Decimal | None
    status: State
    current_step: int
    step_label: str | None
    step_role: str | None
    submitted_by_name: str | None
    submitted_at: datetime
    due_at: datetime | None
    overdue: bool


class StepOut(BaseModel):
    seq: int
    label: str
    role: str | None
    state: Literal["APPROVED", "REJECTED", "SKIPPED", "CURRENT", "WAITING"]
    decided_by: str | None
    decision: str | None
    decided_at: datetime | None
    note: str | None


class ApprovalDetail(ApprovalSummary):
    rule: str
    steps: list[StepOut]


class CountOut(BaseModel):
    pending: int
    overdue: int


@router.post("", response_model=ApprovalDetail, status_code=status.HTTP_201_CREATED)
async def submit_for_approval(body: SubmitIn, ctx: Ctx = Depends(read_ctx)) -> ApprovalDetail:
    approval_id = await engine.submit(ctx, body.document_type, body.document_id)
    return ApprovalDetail(**await engine.detail(ctx, approval_id))


@router.get("", response_model=Page[ApprovalSummary])
async def list_approvals(
    scope: Literal["mine", "all"] = Query("mine"),
    state: State = Query("PENDING"),
    page: Paging = Depends(),
    ctx: Ctx = Depends(read_ctx),
) -> Page[ApprovalSummary]:
    """'mine' = waiting for me (my role or me by name); 'all' needs approval.view_all."""
    items, total = await engine.inbox(ctx, scope, state, page.limit, page.offset)
    return Page(items=[ApprovalSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/count", response_model=CountOut)
async def my_count(ctx: Ctx = Depends(read_ctx)) -> CountOut:
    items, total = await engine.inbox(ctx, "mine", "PENDING", 200, 0)
    return CountOut(pending=total, overdue=sum(1 for i in items if i["overdue"]))


@router.post("/sla-sweep", response_model=dict[str, int])
async def sla_sweep(ctx: Ctx = Depends(sweep_permission)) -> dict[str, int]:
    """Queue reminders and escalations for approvals near or past their SLA (idempotent)."""
    return await engine.sla_sweep(ctx)


@router.get("/{approval_id}", response_model=ApprovalDetail)
async def get_approval(approval_id: UUID, ctx: Ctx = Depends(read_ctx)) -> ApprovalDetail:
    await engine.ensure_visible(ctx, approval_id)
    return ApprovalDetail(**await engine.detail(ctx, approval_id))


@router.post("/{approval_id}/decide", response_model=ApprovalDetail)
async def decide(
    approval_id: UUID,
    body: DecideIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=100),
    ctx: Ctx = Depends(read_ctx),
) -> ApprovalDetail:
    """Approve or reject the current step. Repeating a request with the same key is harmless."""
    if idempotency_key is not None and not idempotency_key.strip():
        raise HTTPException(422, "Idempotency-Key must not be blank")
    await engine.decide(ctx, approval_id, body.decision, body.note,
                        idempotency_key or f"auto:{uuid4()}", body.step)
    return ApprovalDetail(**await engine.detail(ctx, approval_id))
