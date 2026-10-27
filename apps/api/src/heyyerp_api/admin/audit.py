"""Read access to the audit log (BRD FR-ADM-05, FR-ADM-08). Admin only; the log is append-only."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from heyyerp_api.masters.common import Ctx, Page, Paging, count, requires, rows

router = APIRouter(prefix="/admin/audit-log", tags=["administration"])
manage = requires("admin")


class AuditEntry(BaseModel):
    audit_id: int
    occurred_at: datetime
    action: str
    entity_type: str | None
    entity_id: str | None
    actor: str | None
    actor_role_code: str | None
    details: dict[str, Any]


@router.get("", response_model=Page[AuditEntry])
async def list_audit_log(
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    actor_user_id: UUID | None = None,
    page: Paging = Depends(),
    ctx: Ctx = Depends(manage),
) -> Page[AuditEntry]:
    """Newest first. `action` matches a prefix, e.g. `approval.` or `unit.create`."""
    conditions: list[str] = []
    params: dict[str, Any] = {}
    if entity_type:
        conditions.append("a.entity_type = :entity_type")
        params["entity_type"] = entity_type
    if entity_id:
        conditions.append("a.entity_id = :entity_id")
        params["entity_id"] = entity_id
    if action:
        conditions.append("a.action LIKE :action")
        escaped = action.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["action"] = escaped + "%"
    if actor_user_id:
        conditions.append("a.actor_user_id = :actor")
        params["actor"] = actor_user_id
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(
        ctx,
        "SELECT a.audit_id, a.occurred_at, a.action, a.entity_type, a.entity_id, "
        "a.actor_role_code, u.display_name AS actor, a.details FROM erp.audit_log a "
        f"LEFT JOIN erp.app_user u ON u.user_id = a.actor_user_id{where} "
        "ORDER BY a.audit_id DESC LIMIT :limit OFFSET :offset",
        {**params, "limit": page.limit, "offset": page.offset},
    )
    total = await count(ctx, f"SELECT count(*) FROM erp.audit_log a{where}", params)
    return Page(items=[AuditEntry(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)
