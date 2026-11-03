"""Delegation of approvals (FR-WF-03): hand the approvals of your role to someone for a period.

While a delegation is in force the delegate sees the delegator's approvals in their inbox, is told
about them, and decides them as themselves: the record shows the decision, the role it was made as
and whom it was made for. The delegate cannot decide a document the delegator submitted, and
cannot pass the authority on. The longest period is the company's rule `delegationMaxDays`.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, model_validator

from heyyerp_api.business_rules import service as rules
from heyyerp_api.masters.common import Ctx, Strict, audit, not_found, one, read_ctx, rows
from heyyerp_api.security import has_permission

router = APIRouter(prefix="/approvals/delegations", tags=["approval delegation"])


class DelegationIn(Strict):
    to_user_id: UUID
    valid_from: datetime
    valid_to: datetime

    @model_validator(mode="after")
    def ordered(self) -> "DelegationIn":
        if self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        return self


class DelegationOut(BaseModel):
    delegation_id: UUID
    from_user_id: UUID
    from_name: str | None
    to_user_id: UUID
    to_name: str | None
    role: str | None
    valid_from: datetime
    valid_to: datetime
    active: bool
    in_force: bool


SQL = (
    "SELECT d.delegation_id, d.from_user_id, f.display_name AS from_name, d.to_user_id, "
    "t.display_name AS to_name, r.code AS role, d.valid_from, d.valid_to, d.active, "
    "(d.active AND now() >= d.valid_from AND now() <= d.valid_to) AS in_force FROM "
    "erp.approval_delegation d JOIN erp.app_user f ON f.user_id = d.from_user_id JOIN "
    "erp.app_user t ON t.user_id = d.to_user_id "
    "LEFT JOIN erp.role r ON r.role_id = f.role_id"
)


@router.post("", response_model=DelegationOut, status_code=status.HTTP_201_CREATED)
async def delegate(body: DelegationIn, ctx: Ctx = Depends(read_ctx)) -> DelegationOut:
    me = ctx.principal.profile
    if body.to_user_id == me.app_user_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "You cannot delegate to yourself")
    longest = int(await rules.get_value(ctx, "delegationMaxDays"))
    if (body.valid_to - body.valid_from).total_seconds() > longest * 86400:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"A delegation can last at most {longest} days")
    target = await one(ctx, "SELECT user_id FROM erp.app_user WHERE user_id = :u AND status = "
                       "'ACTIVE'", {"u": body.to_user_id})
    if target is None:
        raise not_found("User")
    if await one(ctx, "SELECT 1 AS x FROM erp.approval_delegation WHERE from_user_id = :f AND "
                 "to_user_id = :t AND active AND valid_from < :to AND valid_to > :fr",
                 {"f": me.app_user_id, "t": body.to_user_id, "fr": body.valid_from,
                  "to": body.valid_to}):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "You already delegate to this person for part of that period")
    made = await one(
        ctx, "INSERT INTO erp.approval_delegation(tenant_id, from_user_id, to_user_id, role_id, "
        "valid_from, valid_to) SELECT erp.current_tenant_id(), :f, :t, role_id, :fr, :to FROM "
        "erp.app_user WHERE user_id = :f RETURNING delegation_id",
        {"f": me.app_user_id, "t": body.to_user_id, "fr": body.valid_from, "to": body.valid_to})
    assert made is not None
    await audit(ctx, "approval.delegate", "approval_delegation", made["delegation_id"],
                {"to": str(body.to_user_id), "from": body.valid_from.isoformat(),
                 "until": body.valid_to.isoformat()})
    row = await one(ctx, f"{SQL} WHERE d.delegation_id = :id", {"id": made["delegation_id"]})
    assert row is not None
    return DelegationOut(**row)


@router.get("", response_model=list[DelegationOut])
async def list_delegations(all_: bool = False, ctx: Ctx = Depends(read_ctx)
                           ) -> list[DelegationOut]:
    """The ones you gave and the ones given to you; with `all_`, everyone's (needs the right to
    see every approval)."""
    if all_:
        if not has_permission(ctx.principal, "approval.view_all"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Requires permission approval.view_all")
        found = await rows(ctx, f"{SQL} ORDER BY d.valid_from DESC LIMIT 200")
    else:
        found = await rows(ctx, f"{SQL} WHERE d.from_user_id = :me OR d.to_user_id = :me "
                           "ORDER BY d.valid_from DESC LIMIT 200",
                           {"me": ctx.principal.profile.app_user_id})
    return [DelegationOut(**d) for d in found]


@router.post("/{delegation_id}/revoke", response_model=DelegationOut)
async def revoke(delegation_id: UUID, ctx: Ctx = Depends(read_ctx)) -> DelegationOut:
    """End a delegation now: by the person who gave it, or by someone who may override."""
    row = await one(ctx, "SELECT from_user_id, active FROM erp.approval_delegation WHERE "
                    "delegation_id = :id", {"id": delegation_id})
    if row is None:
        raise not_found("Delegation")
    if row["from_user_id"] != ctx.principal.profile.app_user_id and not has_permission(
            ctx.principal, "approval.override"):
        raise not_found("Delegation")
    if not row["active"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "This delegation has already ended")
    await rows(ctx, "UPDATE erp.approval_delegation SET active = false WHERE delegation_id = :id "
               "RETURNING delegation_id", {"id": delegation_id})
    await audit(ctx, "approval.delegation_revoke", "approval_delegation", delegation_id, {})
    found = await one(ctx, f"{SQL} WHERE d.delegation_id = :id", {"id": delegation_id})
    assert found is not None
    return DelegationOut(**found)
