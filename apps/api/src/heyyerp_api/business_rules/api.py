"""Business rules: anyone signed in can read them; only an administrator changes them."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from heyyerp_api.business_rules import service
from heyyerp_api.masters.common import Ctx, Strict, read_ctx, requires

router = APIRouter(tags=["business rules"])
manage = requires("admin")


class RuleOut(BaseModel):
    key: str
    rule: str
    description: str
    kind: str
    options: list[str]
    nullable: bool
    value: Any
    default: Any
    is_default: bool
    status: str  # APPLIED, or AWAITING_DECISION while V-Rub has not answered
    changed_at: datetime | None
    changed_by: str | None


class RuleIn(Strict):
    value: Any


class RuleVersion(BaseModel):
    value: Any
    effective_from: datetime
    effective_to: datetime | None
    changed_by: str | None


@router.get("/settings", response_model=list[RuleOut])
async def list_rules(ctx: Ctx = Depends(read_ctx)) -> list[RuleOut]:
    return [RuleOut(**r) for r in await service.all_current(ctx)]


@router.get("/settings/{key}", response_model=RuleOut)
async def get_rule(key: str, ctx: Ctx = Depends(read_ctx)) -> RuleOut:
    service.definition(key)
    return RuleOut(**next(r for r in await service.all_current(ctx) if r["key"] == key))


@router.put("/admin/settings/{key}", response_model=RuleOut)
async def change_rule(key: str, body: RuleIn, ctx: Ctx = Depends(manage)) -> RuleOut:
    """Set a rule. The previous value is kept in the history; unchanged values are a no-op."""
    return RuleOut(**await service.set_value(ctx, key, body.value))


@router.get("/admin/settings/{key}/history", response_model=list[RuleVersion])
async def rule_history(key: str, ctx: Ctx = Depends(manage)) -> list[RuleVersion]:
    return [RuleVersion(**v) for v in await service.history(ctx, key)]
