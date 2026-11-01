"""Queueing notifications: what is said, to whom, on which channels.

A notification is one row per person and channel. In-app ones are visible at once; e-mail and
WhatsApp ones wait in the queue for the delivery job. The same message to the same person on the
same channel for the same record is never queued twice, so every job may be repeated safely.
The company decides which channels are used (rules `notifyEmail`, `notifyWhatsapp`); in-app is
always on.
"""

from typing import Any
from uuid import UUID

from heyyerp_api.business_rules import service as rules
from heyyerp_api.config import get_settings
from heyyerp_api.masters.common import Ctx, one, rows

# Which channels each kind of message goes on (BRD section 5): a first reminder is in-app and
# e-mail, the second adds WhatsApp, an overdue approval is everywhere.
CHANNELS = {
    "APPROVAL_PENDING": ("IN_APP", "EMAIL"),
    "APPROVAL_REMINDER_50": ("IN_APP", "EMAIL"),
    "APPROVAL_REMINDER_90": ("IN_APP", "WHATSAPP"),
    "APPROVAL_OVERDUE": ("IN_APP", "WHATSAPP", "EMAIL"),
    "APPROVAL_DECIDED": ("IN_APP", "EMAIL"),
}


async def approval_data_for(ctx: Ctx, approval_id: UUID) -> dict[str, Any]:
    """What the message templates say about an approval."""
    row = await one(ctx, "SELECT route_snapshot->>'title' AS title, current_step FROM "
                    "erp.approval_instance WHERE approval_id = :id", {"id": approval_id})
    return {"approval_id": approval_id, "title": (row or {}).get("title"),
            "step": (row or {}).get("current_step")}


def base(template_code: str) -> str:
    return template_code.split(":")[0]


def render(template_code: str, data: dict[str, Any]) -> tuple[str, str]:
    """The subject and the text of a message."""
    title = data.get("title") or "a document"
    link = f"{get_settings().web_base_url}/approvals/{data.get('approval_id', '')}"
    kind = base(template_code)
    if kind == "APPROVAL_PENDING":
        return (f"Approval needed: {title}",
                f"{title} is waiting for your approval (step {data.get('step')}).\n{link}")
    if kind == "APPROVAL_REMINDER_50":
        return (f"Reminder: {title}",
                f"{title} has used half of its approval time and is still waiting for you.\n{link}")
    if kind == "APPROVAL_REMINDER_90":
        return (f"Urgent reminder: {title}",
                f"{title} is nearly out of approval time. Please decide now.\n{link}")
    if kind == "APPROVAL_OVERDUE":
        return (f"Overdue: {title}",
                f"{title} passed its approval time and has been escalated.\n{link}")
    if kind == "APPROVAL_DECIDED":
        verdict = str(data.get("status", "decided")).lower()
        return f"{title} was {verdict}", f"Your request {title} was {verdict}.\n{link}"
    return title, str(data.get("text", title))


async def enabled(ctx: Ctx, channel: str) -> bool:
    if channel == "IN_APP":
        return True
    return bool(await rules.get_value(ctx, "notifyEmail" if channel == "EMAIL"
                                       else "notifyWhatsapp"))


async def queue(
    ctx: Ctx, recipients: list[UUID], template_code: str, ref_type: str, ref_id: UUID,
    data: dict[str, Any],
) -> int:
    """Queue the message for every recipient on each channel the company uses; returns how many
    in-app notifications were new (so a repeated job reports zero)."""
    subject, body = render(template_code, data)
    fresh = 0
    for channel in CHANNELS.get(base(template_code), ("IN_APP",)):
        if not await enabled(ctx, channel):
            continue
        made = await rows(
            ctx, "INSERT INTO erp.notification(tenant_id, recipient_user_id, channel, "
            "template_code, reference_type, reference_id, status, subject, body, sent_at) "
            "SELECT erp.current_tenant_id(), u.user_id, :ch, :t, :rt, :rid, CASE WHEN :ch = "
            "'IN_APP' THEN 'SENT' ELSE 'QUEUED' END, :s, :b, CASE WHEN :ch = 'IN_APP' THEN now() "
            "END FROM erp.app_user u WHERE u.user_id = ANY(CAST(:ids AS uuid[])) AND u.status = "
            "'ACTIVE' ON CONFLICT DO NOTHING RETURNING notification_id",
            {"ch": channel, "t": template_code, "rt": ref_type, "rid": ref_id, "s": subject,
             "b": body, "ids": list(recipients)})
        if channel == "IN_APP":
            fresh = len(made)
    return fresh
