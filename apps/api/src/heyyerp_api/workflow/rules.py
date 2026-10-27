"""Administration of approval rules (BRD FR-WF, FR-ADM-08). Needs the admin permission.

Changing a rule affects only documents submitted afterwards: each instance keeps the route it
started with. Rules are retired (active_to set), never deleted, so history stays explainable.
"""

import json
from decimal import Decimal
from typing import Any, Literal, Self
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from heyyerp_api.masters.common import Ctx, Strict, audit, db_errors, not_found, one, requires, rows
from heyyerp_api.workflow.registry import KNOWN_TYPES

router = APIRouter(prefix="/admin/approval-rules", tags=["administration"])
manage = requires("admin")


class StepIn(Strict):
    kind: Literal["ROLE", "DEPARTMENT_HEAD", "USER"]
    role_code: str | None = None
    user_id: UUID | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        needs = {"ROLE": "a role_code", "USER": "a user_id",
                 "DEPARTMENT_HEAD": "neither a role_code nor a user_id"}[self.kind]
        wanted = (self.kind == "ROLE", self.kind == "USER")
        if (bool(self.role_code), bool(self.user_id)) != wanted:
            raise ValueError(f"A {self.kind} step needs {needs}")
        return self


class RuleIn(Strict):
    name: str = Field(min_length=1, max_length=40)
    document_type: str
    amount_min: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    amount_max: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    priority: int = Field(default=100, ge=1, le=10000)
    sla_hours: int = Field(ge=1, le=24 * 60)
    steps: list[StepIn] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def _bands(self) -> Self:
        if self.document_type not in KNOWN_TYPES:
            raise ValueError(f"Unknown document type; use one of {sorted(KNOWN_TYPES)}")
        if self.amount_min is not None and self.amount_max is not None \
                and self.amount_max < self.amount_min:
            raise ValueError("amount_max must not be below amount_min")
        return self


class StepOut(BaseModel):
    seq: int
    kind: str
    role_code: str | None
    user_id: UUID | None


class RuleOut(BaseModel):
    rule_id: UUID
    name: str | None
    document_type: str
    amount_min: Decimal | None
    amount_max: Decimal | None
    priority: int
    version: int
    sla_hours: int | None
    active: bool
    steps: list[StepOut]


RULE_SQL = (
    "SELECT r.rule_id, r.name, r.document_type, r.amount_min, r.amount_max, r.priority, "
    "r.version, s.hours AS sla_hours, "
    "(r.active_from <= now() AND (r.active_to IS NULL OR r.active_to > now())) AS active "
    "FROM erp.approval_rule r LEFT JOIN erp.sla_policy s ON s.sla_policy_id = r.sla_policy_id"
)


async def load(ctx: Ctx, rule_id: UUID) -> RuleOut | None:
    rule = await one(ctx, f"{RULE_SQL} WHERE r.rule_id = :id", {"id": rule_id})
    if rule is None:
        return None
    steps = await rows(
        ctx,
        "SELECT s.sequence_no AS seq, s.approver_kind AS kind, r.code AS role_code, s.user_id "
        "FROM erp.approval_rule_step s LEFT JOIN erp.role r ON r.role_id = s.role_id "
        "WHERE s.rule_id = :id ORDER BY s.sequence_no", {"id": rule_id},
    )
    return RuleOut(**rule, steps=[StepOut(**s) for s in steps])


async def write_steps(ctx: Ctx, rule_id: UUID, steps: list[StepIn]) -> None:
    await rows(ctx, "DELETE FROM erp.approval_rule_step WHERE rule_id = :id RETURNING rule_id",
               {"id": rule_id})
    for seq, step in enumerate(steps, start=1):
        role_id = None
        if step.kind == "ROLE":
            role = await one(ctx, "SELECT role_id FROM erp.role WHERE code = :c",
                             {"c": (step.role_code or "").upper()})
            if role is None:
                raise HTTPException(422, f"Unknown role {step.role_code}")
            role_id = role["role_id"]
        if step.kind == "USER" and await one(
            ctx, "SELECT 1 AS x FROM erp.app_user WHERE user_id = :id AND status = 'ACTIVE'",
            {"id": step.user_id},
        ) is None:
            raise HTTPException(422, "Approver user does not exist or is not active")
        await one(
            ctx,
            "INSERT INTO erp.approval_rule_step(tenant_id, rule_id, sequence_no, approver_kind, "
            "role_id, user_id) VALUES (erp.current_tenant_id(), :rule, :seq, :kind, :role, :user) "
            "RETURNING rule_step_id",
            {"rule": rule_id, "seq": seq, "kind": step.kind, "role": role_id,
             "user": step.user_id},
        )


@router.get("", response_model=list[RuleOut])
async def list_rules(document_type: str | None = None, ctx: Ctx = Depends(manage)) -> list[RuleOut]:
    where = " WHERE r.document_type = :t" if document_type else ""
    found = await rows(ctx, f"{RULE_SQL}{where} ORDER BY r.document_type, r.priority, r.name",
                       {"t": document_type} if document_type else {})
    result = [await load(ctx, r["rule_id"]) for r in found]
    return [r for r in result if r is not None]


@router.get("/{rule_id}", response_model=RuleOut)
async def get_rule(rule_id: UUID, ctx: Ctx = Depends(manage)) -> RuleOut:
    rule = await load(ctx, rule_id)
    if rule is None:
        raise not_found("Approval rule")
    return rule


@router.post("", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(body: RuleIn, ctx: Ctx = Depends(manage)) -> RuleOut:
    sla = await one(
        ctx, "INSERT INTO erp.sla_policy(tenant_id, name, hours, reminder_config, "
        "escalation_config) VALUES (erp.current_tenant_id(), :name, :hours, "
        "'{\"at_percent\": [50, 90]}', '{\"on_breach\": \"next_level\"}') RETURNING sla_policy_id",
        {"name": body.name, "hours": body.sla_hours},
    )
    assert sla is not None
    with db_errors("Approval rule name"):
        created = await one(
            ctx,
            "INSERT INTO erp.approval_rule(tenant_id, name, sla_policy_id, document_type, "
            "amount_min, amount_max, priority) VALUES (erp.current_tenant_id(), :name, :sla, "
            ":type, :min, :max, :priority) RETURNING rule_id",
            {"name": body.name, "sla": sla["sla_policy_id"], "type": body.document_type,
             "min": body.amount_min, "max": body.amount_max, "priority": body.priority},
        )
    assert created is not None
    await write_steps(ctx, created["rule_id"], body.steps)
    await audit(ctx, "approval_rule.create", "approval_rule", created["rule_id"],
                json.loads(body.model_dump_json()))
    rule = await load(ctx, created["rule_id"])
    assert rule is not None
    return rule


@router.put("/{rule_id}", response_model=RuleOut)
async def replace_rule(rule_id: UUID, body: RuleIn, ctx: Ctx = Depends(manage)) -> RuleOut:
    """Replace a rule. Documents already submitted keep the route they started with."""
    current = await one(
        ctx, "SELECT rule_id, sla_policy_id FROM erp.approval_rule WHERE rule_id = :id FOR UPDATE",
        {"id": rule_id},
    )
    if current is None:
        raise not_found("Approval rule")
    if current["sla_policy_id"] is not None:
        await rows(ctx, "UPDATE erp.sla_policy SET name = :name, hours = :hours "
                   "WHERE sla_policy_id = :id RETURNING sla_policy_id",
                   {"name": body.name, "hours": body.sla_hours, "id": current["sla_policy_id"]})
    with db_errors("Approval rule name"):
        await rows(
            ctx,
            "UPDATE erp.approval_rule SET name = :name, document_type = :type, amount_min = :min, "
            "amount_max = :max, priority = :priority, version = version + 1 "
            "WHERE rule_id = :id RETURNING rule_id",
            {"name": body.name, "type": body.document_type, "min": body.amount_min,
             "max": body.amount_max, "priority": body.priority, "id": rule_id},
        )
    await write_steps(ctx, rule_id, body.steps)
    await audit(ctx, "approval_rule.update", "approval_rule", rule_id,
                json.loads(body.model_dump_json()))
    rule = await load(ctx, rule_id)
    assert rule is not None
    return rule


@router.post("/{rule_id}/retire", response_model=RuleOut)
async def retire_rule(rule_id: UUID, ctx: Ctx = Depends(manage)) -> RuleOut:
    """Stop using a rule for new documents. In-flight documents are unaffected."""
    changed: list[dict[str, Any]] = await rows(
        ctx, "UPDATE erp.approval_rule SET active_to = now() WHERE rule_id = :id "
        "AND (active_to IS NULL OR active_to > now()) RETURNING rule_id", {"id": rule_id})
    if not changed:
        if await one(ctx, "SELECT 1 AS x FROM erp.approval_rule WHERE rule_id = :id",
                     {"id": rule_id}) is None:
            raise not_found("Approval rule")
        raise HTTPException(status.HTTP_409_CONFLICT, "Rule is already retired")
    await audit(ctx, "approval_rule.retire", "approval_rule", rule_id, {})
    rule = await load(ctx, rule_id)
    assert rule is not None
    return rule
