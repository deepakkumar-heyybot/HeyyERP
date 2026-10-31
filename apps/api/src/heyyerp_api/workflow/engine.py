"""The approval engine (BRD section 5): one engine for every document type.

Routing: the first active rule for the document type whose amount band holds the amount.
The route is resolved once, at submission, and stored on the instance (route_snapshot), so a
later rule change never alters a document already in flight. A step is held by a role, a
named user, or the requesting department's head role. Leading steps held by the requester's
own role are skipped; if nothing is left the document is approved on the spot.

Deciding locks the instance row, so two people acting at once cannot both decide a step, and
a replayed request with the same Idempotency-Key returns the existing result.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status

from heyyerp_api.masters.common import Ctx, audit, count, db_errors, not_found, one, rows
from heyyerp_api.rbac.catalog import ROLES
from heyyerp_api.security import has_permission
from heyyerp_api.workflow import documents  # noqa: F401  (registers the vendor adapter)
from heyyerp_api.workflow.registry import REGISTRY, DocInfo

FALLBACK_HEAD_ROLE = "PLANT_HEAD"
REMINDER_AT = (50, 90)  # percent of the step's SLA (BRD: reminders at 50% and 90%)


def role_label(code: str) -> str:
    return str(ROLES.get(code, {}).get("name", code))


# ------------------------------------------------------------------ routing
async def find_rule(ctx: Ctx, document_type: str, amount: Decimal | None) -> dict[str, Any] | None:
    return await one(
        ctx,
        "SELECT r.rule_id, r.name, r.skip_requester_role, COALESCE(s.hours, 0) AS sla_hours "
        "FROM erp.approval_rule r LEFT JOIN erp.sla_policy s ON s.sla_policy_id = r.sla_policy_id "
        "WHERE r.document_type = :type AND r.active_from <= now() "
        "AND (r.active_to IS NULL OR r.active_to > now()) AND ("
        "  (CAST(:amount AS numeric) IS NULL AND r.amount_min IS NULL AND r.amount_max IS NULL) OR "
        "  (CAST(:amount AS numeric) IS NOT NULL "
        "   AND (r.amount_min IS NULL OR CAST(:amount AS numeric) >= r.amount_min) "
        "   AND (r.amount_max IS NULL OR CAST(:amount AS numeric) <= r.amount_max))) "
        "ORDER BY r.priority, r.version DESC LIMIT 1",
        {"type": document_type, "amount": amount},
    )


async def build_route(
    ctx: Ctx, rule: dict[str, Any], department_id: UUID | None
) -> list[dict[str, Any]]:
    steps = await rows(
        ctx,
        "SELECT s.sequence_no, s.approver_kind, s.user_id, r.code AS role, u.display_name "
        "FROM erp.approval_rule_step s LEFT JOIN erp.role r ON r.role_id = s.role_id "
        "LEFT JOIN erp.app_user u ON u.user_id = s.user_id "
        "WHERE s.rule_id = :rule ORDER BY s.sequence_no",
        {"rule": rule["rule_id"]},
    )
    head_role = FALLBACK_HEAD_ROLE
    if department_id is not None:
        dept = await one(
            ctx,
            "SELECT r.code FROM erp.department d JOIN erp.role r ON r.role_id = d.head_role_id "
            "WHERE d.department_id = :id", {"id": department_id},
        )
        if dept:
            head_role = dept["code"]
    route: list[dict[str, Any]] = []
    for step in steps:
        if step["approver_kind"] == "USER":
            entry = {"kind": "USER", "role": None, "user_id": str(step["user_id"]),
                     "label": step["display_name"]}
        else:
            role = head_role if step["approver_kind"] == "DEPARTMENT_HEAD" else step["role"]
            entry = {"kind": step["approver_kind"], "role": role, "user_id": None,
                     "label": role_label(role)}
        # Consecutive steps held by the same approver collapse into one.
        previous = (route[-1]["role"], route[-1]["user_id"]) if route else None
        if previous == (entry["role"], entry["user_id"]):
            continue
        route.append({**entry, "seq": len(route) + 1, "sla_hours": rule["sla_hours"],
                      "skipped": False})
    return route


def holds_step(step: dict[str, Any], user_id: UUID, role: str) -> bool:
    if step["user_id"] is not None:
        return bool(step["user_id"] == str(user_id))
    return bool(step["role"] == role)


async def emit(ctx: Ctx, event_type: str, approval_id: UUID, payload: dict[str, Any]) -> None:
    """Outbox row; the Phase 12 relay turns these into notifications and real-time updates."""
    await one(
        ctx,
        "INSERT INTO erp.outbox_event(tenant_id, event_type, aggregate_type, aggregate_id, "
        "payload) VALUES (erp.current_tenant_id(), :type, 'approval', :id, "
        "CAST(:payload AS jsonb)) RETURNING event_id",
        {"type": event_type, "id": approval_id, "payload": json.dumps(payload, default=str)},
    )


# ------------------------------------------------------------------ submit
async def submit(ctx: Ctx, document_type: str, document_id: UUID) -> UUID:
    doc_type = REGISTRY.get(document_type)
    if doc_type is None:
        raise HTTPException(422, f"Approval is not set up for document type {document_type!r}")
    if not has_permission(ctx.principal, doc_type.submit_permission):
        raise HTTPException(403, f"Requires permission {doc_type.submit_permission}")
    info: DocInfo | None = await doc_type.load(ctx, document_id)
    if info is None:
        raise not_found("Document")
    if not info.submittable:
        raise HTTPException(status.HTTP_409_CONFLICT, info.reason or "Document cannot be submitted")
    if await one(
        ctx, "SELECT 1 AS x FROM erp.approval_instance WHERE document_type = :t "
        "AND document_id = :id", {"t": document_type, "id": document_id},
    ):
        # ponytail: one approval per document; resubmission after a rejection needs a round number.
        raise HTTPException(status.HTTP_409_CONFLICT, "This document has already been submitted")
    rule = await find_rule(ctx, document_type, info.amount)
    if rule is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"No approval rule covers this {document_type} document"
        )
    route = await build_route(ctx, rule, info.department_id)
    me = ctx.principal.profile
    for step in route:  # BRD: leading steps held by the requester's own role are skipped,
        if not rule["skip_requester_role"]:  # unless the company turned that off for this rule
            break
        if not holds_step(step, me.app_user_id, me.role):
            break
        step["skipped"] = True
    remaining = [s for s in route if not s["skipped"]]
    approval_id = uuid4()
    snapshot = {"title": info.title, "amount": info.amount, "department_id": info.department_id,
                "rule": rule["name"], "steps": route}
    with db_errors("Approval"):
        await one(
            ctx,
            "INSERT INTO erp.approval_instance(approval_id, tenant_id, rule_id, document_type, "
            "document_id, status, current_step, route_snapshot, submitted_by) VALUES (:id, "
            "erp.current_tenant_id(), :rule, :type, :doc, :status, :step, "
            "CAST(:snapshot AS jsonb), :me) RETURNING approval_id",
            {"id": approval_id, "rule": rule["rule_id"], "type": document_type,
             "doc": document_id, "status": "PENDING" if remaining else "APPROVED",
             "step": remaining[0]["seq"] if remaining else len(route),
             "snapshot": json.dumps(snapshot, default=str), "me": me.app_user_id},
        )
    for step in route:
        if step["skipped"]:
            await one(
                ctx,
                "INSERT INTO erp.approval_action(tenant_id, approval_id, step_no, actor_user_id, "
                "decision, acting_as_role, note, idempotency_key) VALUES "
                "(erp.current_tenant_id(), :id, :step, :me, 'SKIP', :role, "
                "'Skipped - raised by this role', :key) RETURNING approval_action_id",
                {"id": approval_id, "step": step["seq"], "me": me.app_user_id,
                 "role": step["role"], "key": f"skip:{approval_id}:{step['seq']}"},
            )
    if remaining:
        await emit(ctx, "approval.step_pending", approval_id,
                   {"document_type": document_type, "document_id": document_id,
                    "step": remaining[0]["seq"], "role": remaining[0]["role"]})
    else:
        await doc_type.finalize(ctx, document_id, True)
        await emit(ctx, "approval.decided", approval_id,
                   {"document_type": document_type, "document_id": document_id,
                    "status": "APPROVED", "auto": True})
    await audit(ctx, "approval.submit", "approval", approval_id,
                {"document_type": document_type, "document_id": document_id,
                 "rule": rule["name"], "auto_approved": not remaining})
    return approval_id


# ------------------------------------------------------------------ decide
async def decide(
    ctx: Ctx,
    approval_id: UUID,
    decision: str,
    note: str | None,
    idempotency_key: str,
    expected_step: int | None = None,
) -> None:
    replay = await one(
        ctx, "SELECT approval_id FROM erp.approval_action WHERE idempotency_key = :key",
        {"key": idempotency_key},
    )
    if replay is not None:
        if replay["approval_id"] != approval_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Idempotency key already used")
        return  # same request again: nothing more to do, the caller re-reads the result
    instance = await one(
        ctx, "SELECT * FROM erp.approval_instance WHERE approval_id = :id FOR UPDATE",
        {"id": approval_id},
    )
    if instance is None:
        raise not_found("Approval")
    if instance["status"] != "PENDING" or (
        expected_step is not None and expected_step != instance["current_step"]
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "Already decided")
    me = ctx.principal.profile
    if instance["submitted_by"] == me.app_user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot decide your own submission")
    steps = instance["route_snapshot"]["steps"]
    step = steps[instance["current_step"] - 1]
    override = False
    if not holds_step(step, me.app_user_id, me.role):
        if not has_permission(ctx.principal, "approval.override"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not the approver for this step")
        override = True
    approve = decision == "APPROVE"
    recorded = ("OVERRIDE_" if override else "") + ("APPROVE" if approve else "REJECT")
    with db_errors("Approval step"):
        await one(
            ctx,
            "INSERT INTO erp.approval_action(tenant_id, approval_id, step_no, actor_user_id, "
            "decision, acting_as_role, note, idempotency_key) VALUES (erp.current_tenant_id(), "
            ":id, :step, :me, :decision, :role, :note, :key) RETURNING approval_action_id",
            {"id": approval_id, "step": step["seq"], "me": me.app_user_id,
             "decision": recorded, "role": step["role"], "note": note, "key": idempotency_key},
        )
    last = instance["current_step"] >= len(steps)
    new_status = "PENDING" if approve and not last else ("APPROVED" if approve else "REJECTED")
    await one(
        ctx,
        "UPDATE erp.approval_instance SET status = :status, current_step = :step, "
        "version = version + 1 WHERE approval_id = :id RETURNING approval_id",
        {"status": new_status, "id": approval_id,
         "step": instance["current_step"] + (1 if new_status == "PENDING" else 0)},
    )
    document_type, document_id = instance["document_type"], instance["document_id"]
    if new_status == "PENDING":
        await emit(ctx, "approval.step_pending", approval_id,
                   {"document_type": document_type, "document_id": document_id,
                    "step": instance["current_step"] + 1,
                    "role": steps[instance["current_step"]]["role"]})
    else:
        doc_type = REGISTRY.get(document_type)
        if doc_type is not None:
            await doc_type.finalize(ctx, document_id, new_status == "APPROVED")
        await emit(ctx, "approval.decided", approval_id,
                   {"document_type": document_type, "document_id": document_id,
                    "status": new_status})
    await audit(ctx, "approval.decide", "approval", approval_id,
                {"decision": recorded, "step": step["seq"], "note": note,
                 "result": new_status})


# ------------------------------------------------------------------ reading
STARTED = (
    "CROSS JOIN LATERAL (SELECT COALESCE((SELECT max(decided_at) FROM erp.approval_action a "
    "WHERE a.approval_id = i.approval_id), i.submitted_at) AS started) st"
)
CURRENT = "i.route_snapshot->'steps'->(i.current_step - 1)"
LIST_SQL = (
    "SELECT i.approval_id, i.document_type, i.document_id, i.status, i.current_step, "
    "i.route_snapshot->>'title' AS title, (i.route_snapshot->>'amount')::numeric AS amount, "
    f"{CURRENT}->>'label' AS step_label, {CURRENT}->>'role' AS step_role, "
    "u.display_name AS submitted_by_name, i.submitted_at, st.started, "
    f"COALESCE(({CURRENT}->>'sla_hours')::int, 0) AS sla_hours "
    f"FROM erp.approval_instance i LEFT JOIN erp.app_user u ON u.user_id = i.submitted_by {STARTED}"
)


def with_due(row: dict[str, Any]) -> dict[str, Any]:
    due = None
    if row["status"] == "PENDING" and row["sla_hours"] > 0:
        due = row["started"] + timedelta(hours=row["sla_hours"])
    return {**row, "due_at": due,
            "overdue": bool(due and datetime.now(UTC) > due)}


async def inbox(
    ctx: Ctx, scope: str, state: str, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    me = ctx.principal.profile
    conditions = ["i.status = :state"]
    params: dict[str, Any] = {"state": state}
    if scope == "mine":
        conditions.append(
            f"(({CURRENT}->>'user_id') = :uid OR (({CURRENT}->>'user_id') IS NULL "
            f"AND ({CURRENT}->>'role') = :role))"
        )
        params |= {"uid": str(me.app_user_id), "role": me.role}
    elif not (has_permission(ctx.principal, "approval.view_all")
              or has_permission(ctx.principal, "approval.override")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Requires permission approval.view_all")
    where = " WHERE " + " AND ".join(conditions)
    items = await rows(
        ctx, f"{LIST_SQL}{where} ORDER BY i.submitted_at LIMIT :limit OFFSET :offset",
        {**params, "limit": limit, "offset": offset},
    )
    total = await count(
        ctx, f"SELECT count(*) FROM erp.approval_instance i{where}", params)
    return [with_due(i) for i in items], total


async def ensure_visible(ctx: Ctx, approval_id: UUID) -> None:
    """Leave, kaizen and review approvals are personal: show an approval only to people who
    took part in it (the requester or anyone on its route) or who oversee approvals.
    Everyone else gets 404, so its existence is not revealed either."""
    row = await one(
        ctx, "SELECT submitted_by, route_snapshot FROM erp.approval_instance "
        "WHERE approval_id = :id", {"id": approval_id},
    )
    if row is None:
        raise not_found("Approval")
    me = ctx.principal.profile
    if (
        has_permission(ctx.principal, "approval.view_all")
        or has_permission(ctx.principal, "approval.override")
        or row["submitted_by"] == me.app_user_id
        or any(holds_step(s, me.app_user_id, me.role) for s in row["route_snapshot"]["steps"])
    ):
        return
    raise not_found("Approval")


async def detail(ctx: Ctx, approval_id: UUID) -> dict[str, Any]:
    row = await one(ctx, f"{LIST_SQL} WHERE i.approval_id = :id", {"id": approval_id})
    if row is None:
        raise not_found("Approval")
    actions = await rows(
        ctx,
        "SELECT a.step_no, a.decision, a.acting_as_role, a.note, a.decided_at, "
        "u.display_name AS actor FROM erp.approval_action a "
        "JOIN erp.app_user u ON u.user_id = a.actor_user_id "
        "WHERE a.approval_id = :id ORDER BY a.step_no", {"id": approval_id},
    )
    snapshot = (await one(
        ctx, "SELECT route_snapshot FROM erp.approval_instance WHERE approval_id = :id",
        {"id": approval_id}) or {})["route_snapshot"]
    by_step = {a["step_no"]: a for a in actions}
    steps = []
    for step in snapshot["steps"]:
        action = by_step.get(step["seq"])
        if action is not None:
            state = "SKIPPED" if action["decision"] == "SKIP" else (
                "REJECTED" if action["decision"].endswith("REJECT") else "APPROVED")
        elif row["status"] == "PENDING" and step["seq"] == row["current_step"]:
            state = "CURRENT"
        else:
            state = "WAITING"
        steps.append({"seq": step["seq"], "label": step["label"], "role": step["role"],
                      "state": state, "decided_by": action and action["actor"],
                      "decision": action and action["decision"],
                      "decided_at": action and action["decided_at"],
                      "note": action and action["note"]})
    return {**with_due(row), "rule": snapshot["rule"], "steps": steps}


# ------------------------------------------------------------------ SLA reminders and escalation
async def sla_sweep(ctx: Ctx) -> dict[str, int]:
    """Queue in-app notifications for approvals past 50% / 90% / 100% of their step SLA.

    Safe to run as often as wanted: each notification is sent once per approval step.
    Past the SLA the approval is escalated one level (Plant Head, or MD if it is already with
    the Plant Head) and the requester is told (BRD section 5).
    """
    pending = [with_due(r) for r in await rows(
        ctx, f"{LIST_SQL} WHERE i.status = 'PENDING'")]
    now = datetime.now(UTC)
    sent = {"reminders": 0, "escalations": 0}
    for item in pending:
        if item["sla_hours"] <= 0:
            continue
        used = (now - item["started"]).total_seconds() / (item["sla_hours"] * 3600) * 100
        step_role = item["step_role"]
        for percent in REMINDER_AT:
            if used >= percent:
                key = f"APPROVAL_REMINDER_{percent}:{item['current_step']}"
                sent["reminders"] += await notify_role(ctx, item["approval_id"], key, step_role)
        if used >= 100:
            key = f"APPROVAL_OVERDUE:{item['current_step']}"
            boss = "MD" if step_role == FALLBACK_HEAD_ROLE else FALLBACK_HEAD_ROLE
            made = await notify_role(ctx, item["approval_id"], key, boss)
            await notify_requester(ctx, item["approval_id"], key)
            sent["escalations"] += made
    return sent


async def notify_role(ctx: Ctx, approval_id: UUID, template: str, role: str | None) -> int:
    if role is None or await one(
        ctx, "SELECT 1 AS x FROM erp.notification WHERE reference_id = :id AND template_code = :t "
        "AND recipient_user_id IN (SELECT u.user_id FROM erp.app_user u JOIN erp.role r "
        "ON r.role_id = u.role_id WHERE r.code = :role)",
        {"id": approval_id, "t": template, "role": role},
    ):
        return 0
    made = await rows(
        ctx,
        "INSERT INTO erp.notification(tenant_id, recipient_user_id, channel, template_code, "
        "reference_type, reference_id) SELECT erp.current_tenant_id(), u.user_id, 'IN_APP', :t, "
        "'approval', :id FROM erp.app_user u JOIN erp.role r ON r.role_id = u.role_id "
        "WHERE r.code = :role AND u.status = 'ACTIVE' RETURNING notification_id",
        {"id": approval_id, "t": template, "role": role},
    )
    return len(made)


async def notify_requester(ctx: Ctx, approval_id: UUID, template: str) -> None:
    await rows(
        ctx,
        "INSERT INTO erp.notification(tenant_id, recipient_user_id, channel, template_code, "
        "reference_type, reference_id) SELECT erp.current_tenant_id(), i.submitted_by, 'IN_APP', "
        ":t, 'approval', i.approval_id FROM erp.approval_instance i WHERE i.approval_id = :id "
        "AND i.submitted_by IS NOT NULL AND NOT EXISTS (SELECT 1 FROM erp.notification n "
        "WHERE n.reference_id = i.approval_id AND n.template_code = :t "
        "AND n.recipient_user_id = i.submitted_by) RETURNING notification_id",
        {"id": approval_id, "t": template},
    )
