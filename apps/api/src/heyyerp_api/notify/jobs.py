"""The background jobs: turn events into notifications, remind and escalate approvals, deliver.

One cycle does, for each tenant: relay the outbox (approval events become notifications), sweep
approvals against their SLA (first and second reminder, escalation), and deliver the queued e-mail
and WhatsApp messages. Every step can be repeated safely, a failure in one event or one message
never stops the others, and a message that cannot be delivered is retried with a growing delay and
finally marked FAILED with the reason, where the admin screen shows it. Only one worker runs at a
time (a database lock), so several server processes can all have the worker switched on.
The cycle runs inside the server when `BACKGROUND_WORKER=true`, or on demand by an administrator.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from heyyerp_api import db
from heyyerp_api.business_rules import service as rules
from heyyerp_api.config import get_settings
from heyyerp_api.masters.common import Ctx, one, rows
from heyyerp_api.notify.queue import approval_data_for, queue
from heyyerp_api.notify.senders import sender_for, whatsapp_number
from heyyerp_api.workflow import engine

log = logging.getLogger("heyyerp.jobs")
LOCK_KEY = 727001


@dataclass
class SystemCtx:
    """What a background job needs in place of a signed-in user: just the tenant's session."""

    session: AsyncSession
    principal: Any = None


async def step_recipients(ctx: Ctx, approval_id: Any, payload: dict[str, Any]) -> list[Any]:
    if payload.get("role"):
        return [r["user_id"] for r in await rows(
            ctx, "SELECT u.user_id FROM erp.app_user u JOIN erp.role r ON r.role_id = u.role_id "
            "WHERE r.code = :role AND u.status = 'ACTIVE'", {"role": payload["role"]})]
    row = await one(
        ctx, "SELECT route_snapshot->'steps'->(CAST(:step AS int) - 1)->>'user_id' AS uid FROM "
        "erp.approval_instance WHERE approval_id = :id", {"step": payload.get("step", 1),
                                                          "id": approval_id})
    return [row["uid"]] if row and row["uid"] else []


async def relay_outbox(ctx: Ctx, limit: int = 100) -> dict[str, int]:
    """Make notifications from the approval events not yet relayed; mark each one relayed."""
    events = await rows(
        ctx, "SELECT event_id, event_type, aggregate_id, payload FROM erp.outbox_event WHERE "
        "published_at IS NULL AND attempt_count < 5 ORDER BY created_at LIMIT :n FOR UPDATE "
        "SKIP LOCKED", {"n": limit})
    done = failed = 0
    for event in events:
        try:
            async with ctx.session.begin_nested():
                payload, kind = event["payload"], event["event_type"]
                if kind == "approval.step_pending":
                    people = await step_recipients(ctx, event["aggregate_id"], payload)
                    if people:
                        data = await approval_data_for(ctx, event["aggregate_id"])
                        await queue(ctx, people, f"APPROVAL_PENDING:{payload.get('step')}",
                                    "approval", event["aggregate_id"], data)
                elif kind == "approval.decided":
                    row = await one(ctx, "SELECT submitted_by FROM erp.approval_instance WHERE "
                                    "approval_id = :id", {"id": event["aggregate_id"]})
                    if row and row["submitted_by"]:
                        data = await approval_data_for(ctx, event["aggregate_id"])
                        data["status"] = payload.get("status")
                        await queue(ctx, [row["submitted_by"]],
                                    f"APPROVAL_DECIDED:{payload.get('status')}", "approval",
                                    event["aggregate_id"], data)
                await rows(ctx, "UPDATE erp.outbox_event SET published_at = now() WHERE event_id = "
                           ":id RETURNING event_id", {"id": event["event_id"]})
            done += 1
        except Exception as exc:  # one bad event must not stop the rest
            failed += 1
            log.exception("outbox event %s failed", event["event_id"])
            await rows(ctx, "UPDATE erp.outbox_event SET attempt_count = attempt_count + 1, "
                       "last_error = :e WHERE event_id = :id RETURNING event_id",
                       {"e": str(exc)[:300], "id": event["event_id"]})
    return {"relayed": done, "relay_failed": failed}


async def deliver_queued(ctx: Ctx, limit: int = 50) -> dict[str, int]:
    """Send the queued e-mail and WhatsApp messages that are due; retry or fail the others."""
    due = await rows(
        ctx, "SELECT n.notification_id, n.channel, n.subject, n.body, n.attempt_count, u.email, "
        "u.phone FROM erp.notification n JOIN erp.app_user u ON u.user_id = n.recipient_user_id "
        "WHERE n.status = 'QUEUED' AND n.channel <> 'IN_APP' AND (n.next_attempt_at IS NULL OR "
        "n.next_attempt_at <= now()) ORDER BY n.created_at LIMIT :n FOR UPDATE OF n SKIP LOCKED",
        {"n": limit})
    top = int(await rules.get_value(ctx, "notifyMaxAttempts"))
    sent = skipped = retried = failed = 0
    for n in due:
        address = n["email"] if n["channel"] == "EMAIL" else whatsapp_number(n["phone"])
        if not address:
            skipped += 1
            await rows(ctx, "UPDATE erp.notification SET status = 'SKIPPED', last_error = :e "
                       "WHERE notification_id = :id RETURNING notification_id",
                       {"e": "no usable e-mail address or phone number on file",
                        "id": n["notification_id"]})
            continue
        try:
            await sender_for(n["channel"]).send(address, n["subject"] or "", n["body"] or "")
        except Exception as exc:
            attempts = n["attempt_count"] + 1
            gave_up = attempts >= top
            retried += not gave_up
            failed += gave_up
            await rows(
                ctx, "UPDATE erp.notification SET attempt_count = CAST(:a AS int), last_error = "
                ":e, status = CASE WHEN :up THEN 'FAILED' ELSE 'QUEUED' END, next_attempt_at = "
                "now() + make_interval(mins => LEAST(power(2, CAST(:a AS int))::int, 360)) "
                "WHERE notification_id = :id RETURNING notification_id",
                {"a": attempts, "e": str(exc)[:300], "up": gave_up, "id": n["notification_id"]})
            continue
        sent += 1
        await rows(ctx, "UPDATE erp.notification SET status = 'SENT', sent_at = now(), "
                   "attempt_count = attempt_count + 1, last_error = NULL WHERE notification_id = "
                   ":id RETURNING notification_id", {"id": n["notification_id"]})
    return {"sent": sent, "skipped": skipped, "retried": retried, "failed": failed}


async def run_tenant(ctx: Ctx) -> dict[str, int]:
    """One tenant's cycle: relay, sweep the approval SLAs, deliver."""
    stats = await relay_outbox(ctx)
    swept = await engine.sla_sweep(ctx)
    stats.update({"reminders": swept["reminders"], "escalations": swept["escalations"]})
    stats.update(await deliver_queued(ctx))
    return stats


async def run_cycle() -> dict[str, Any]:
    """Run every configured tenant once, unless another worker holds the lock."""
    out: dict[str, Any] = {}
    async with db.SessionFactory() as locker:
        got = (await locker.execute(text("SELECT pg_try_advisory_lock(:k)"),
                                    {"k": LOCK_KEY})).scalar()
        if not got:
            return {"skipped": "another worker is running"}
        try:
            for tenant in get_settings().org_tenant_map.values():
                try:
                    async with db.SessionFactory() as session, session.begin():
                        await session.execute(
                            text("SELECT set_config('app.tenant_id', :t, true)"),
                            {"t": str(tenant)})
                        out[str(tenant)] = await run_tenant(cast(Ctx, SystemCtx(session)))
                except Exception:
                    log.exception("background cycle failed for tenant %s", tenant)
                    out[str(tenant)] = {"error": 1}
        finally:
            await locker.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
    return out


async def worker_loop() -> None:
    """Run cycles for as long as the server lives."""
    while True:
        try:
            await run_cycle()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("background cycle failed")
        await asyncio.sleep(get_settings().worker_interval_seconds)
