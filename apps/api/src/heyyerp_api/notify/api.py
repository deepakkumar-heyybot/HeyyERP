"""Notifications for the signed-in person, and the administrator's view of the background jobs.

Everyone sees their own in-app notifications (newest first, unread count, mark as read). The
administrator sees the state of the delivery queue and of the event relay, the messages that
failed with the reason, can retry one, and can run a cycle of the jobs on demand. Whether the
background worker runs on its own is a server setting (`BACKGROUND_WORKER`).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from heyyerp_api.masters.common import Ctx, audit, not_found, one, read_ctx, requires, rows
from heyyerp_api.notify.jobs import run_tenant

router = APIRouter(tags=["notifications"])
admin = requires("admin")


class NotificationOut(BaseModel):
    notification_id: UUID
    template_code: str
    subject: str | None
    body: str | None
    reference_type: str | None
    reference_id: UUID | None
    created_at: datetime
    read_at: datetime | None


class UnreadOut(BaseModel):
    unread: int


class FailedOut(BaseModel):
    notification_id: UUID
    channel: str
    status: str
    template_code: str
    recipient: str | None
    attempt_count: int
    last_error: str | None
    created_at: datetime


class JobsOut(BaseModel):
    queue: dict[str, int]  # "CHANNEL STATUS" -> number
    outbox_waiting: int
    outbox_failed: int
    failures: list[FailedOut]


@router.get("/notifications", response_model=list[NotificationOut])
async def mine(
    unread: bool = False, limit: int = Query(30, ge=1, le=100), ctx: Ctx = Depends(read_ctx)
) -> list[NotificationOut]:
    data = await rows(
        ctx, "SELECT notification_id, template_code, subject, body, reference_type, reference_id, "
        "created_at, read_at FROM erp.notification WHERE recipient_user_id = :me AND channel = "
        f"'IN_APP' {'AND read_at IS NULL ' if unread else ''}ORDER BY created_at DESC LIMIT :n",
        {"me": ctx.principal.profile.app_user_id, "n": limit})
    return [NotificationOut(**d) for d in data]


@router.get("/notifications/count", response_model=UnreadOut)
async def unread_count(ctx: Ctx = Depends(read_ctx)) -> UnreadOut:
    row = await one(ctx, "SELECT count(*) AS n FROM erp.notification WHERE recipient_user_id = "
                    ":me AND channel = 'IN_APP' AND read_at IS NULL",
                    {"me": ctx.principal.profile.app_user_id})
    assert row is not None
    return UnreadOut(unread=row["n"])


@router.post("/notifications/read-all", response_model=UnreadOut)
async def read_all(ctx: Ctx = Depends(read_ctx)) -> UnreadOut:
    await rows(ctx, "UPDATE erp.notification SET read_at = now() WHERE recipient_user_id = :me "
               "AND channel = 'IN_APP' AND read_at IS NULL RETURNING notification_id",
               {"me": ctx.principal.profile.app_user_id})
    return UnreadOut(unread=0)


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
async def read_one(notification_id: UUID, ctx: Ctx = Depends(read_ctx)) -> NotificationOut:
    row = await one(
        ctx, "UPDATE erp.notification SET read_at = COALESCE(read_at, now()) WHERE "
        "notification_id = :id AND recipient_user_id = :me AND channel = 'IN_APP' RETURNING "
        "notification_id, template_code, subject, body, reference_type, reference_id, created_at, "
        "read_at", {"id": notification_id, "me": ctx.principal.profile.app_user_id})
    if row is None:
        raise not_found("Notification")
    return NotificationOut(**row)


@router.get("/admin/jobs", response_model=JobsOut)
async def jobs(ctx: Ctx = Depends(admin)) -> JobsOut:
    counts = await rows(ctx, "SELECT channel || ' ' || status AS k, count(*) AS n FROM "
                        "erp.notification GROUP BY 1 ORDER BY 1")
    outbox = await one(
        ctx, "SELECT count(*) FILTER (WHERE published_at IS NULL) AS waiting, count(*) FILTER "
        "(WHERE published_at IS NULL AND attempt_count >= 5) AS failed FROM erp.outbox_event")
    assert outbox is not None
    bad = await rows(
        ctx, "SELECT n.notification_id, n.channel, n.status, n.template_code, u.display_name AS "
        "recipient, n.attempt_count, n.last_error, n.created_at FROM erp.notification n LEFT JOIN "
        "erp.app_user u ON u.user_id = n.recipient_user_id WHERE n.status IN ('FAILED', 'SKIPPED') "
        "OR (n.status = 'QUEUED' AND n.attempt_count > 0) ORDER BY n.created_at DESC LIMIT 50")
    return JobsOut(queue={c["k"]: c["n"] for c in counts}, outbox_waiting=outbox["waiting"],
                   outbox_failed=outbox["failed"], failures=[FailedOut(**b) for b in bad])


@router.post("/admin/jobs/run", response_model=dict[str, int])
async def run_now(ctx: Ctx = Depends(admin)) -> dict[str, int]:
    """Run one cycle of the jobs for this company now (the worker does this on its own)."""
    result = await run_tenant(ctx)
    await audit(ctx, "jobs.run", "jobs", "manual", dict(result))
    return result


@router.post("/admin/notifications/{notification_id}/retry", response_model=dict[str, Any])
async def retry(notification_id: UUID, ctx: Ctx = Depends(admin)) -> dict[str, Any]:
    """Put a failed or skipped e-mail or WhatsApp message back in the queue."""
    row = await one(
        ctx, "UPDATE erp.notification SET status = 'QUEUED', attempt_count = 0, last_error = NULL, "
        "next_attempt_at = NULL WHERE notification_id = :id AND channel <> 'IN_APP' AND status IN "
        "('FAILED', 'SKIPPED') RETURNING notification_id", {"id": notification_id})
    if row is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Only a failed or skipped e-mail or WhatsApp message can be retried")
    await audit(ctx, "notification.retry", "notification", notification_id, {})
    return {"notification_id": str(notification_id), "status": "QUEUED"}
