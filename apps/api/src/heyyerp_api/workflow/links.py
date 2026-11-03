"""Approve or reject from an e-mail or WhatsApp link, once.

The message to an approver carries a link with a long random secret; only a hash of it is kept. The
page behind it shows the document (opening it changes nothing, so mail scanners that open links
do no harm) and the person confirms Approve or Reject. The link is for one person and one step,
works once, expires after `approvalLinkHours`, and stops working the moment the step is decided
any other way. The decision goes through the same engine as a decision in the application, so the
same rules apply, and is recorded as made through a link. The company switches this on
(`approvalByLink`) and can cap the amount (`approvalLinkMaxAmount`); roles that must sign in with
a second factor (administrator, MD, accounts) never get a link.
"""

import hashlib
import secrets
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text

from heyyerp_api.business_rules import service as rules
from heyyerp_api.config import get_settings
from heyyerp_api.masters.common import Ctx, one
from heyyerp_api.security import MFA_REQUIRED_ROLES, Principal, fetch_profile
from heyyerp_api.workflow import engine


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def url(token: str) -> str:
    return f"{get_settings().web_base_url}/approve/{token}"


async def issue(ctx: Ctx, approval_id: UUID, step: int, user_id: UUID) -> str | None:
    """A new link for the person to decide this step, or None when the company or the rules do
    not allow one (switched off, too large an amount, a role that needs a second factor, not the
    person who holds the step, or the step is no longer open)."""
    if not await rules.get_value(ctx, "approvalByLink"):
        return None
    row = await one(
        ctx, "SELECT i.status, i.current_step, i.route_snapshot, (i.route_snapshot->>'amount')::"
        "numeric AS amount, r.code AS role FROM erp.approval_instance i, erp.app_user u JOIN "
        "erp.role r ON r.role_id = u.role_id WHERE i.approval_id = :a AND u.user_id = :u AND "
        "u.status = 'ACTIVE'", {"a": approval_id, "u": user_id})
    if row is None or row["status"] != "PENDING" or row["current_step"] != step:
        return None
    if row["role"] in MFA_REQUIRED_ROLES or not engine.holds_step(
            row["route_snapshot"]["steps"][step - 1], user_id, row["role"]):
        return None
    cap = Decimal(str(await rules.get_value(ctx, "approvalLinkMaxAmount")))
    if row["amount"] is not None and row["amount"] > cap:
        return None
    token = secrets.token_urlsafe(32)
    hours = int(await rules.get_value(ctx, "approvalLinkHours"))
    await one(
        ctx, "INSERT INTO erp.approval_link(tenant_id, approval_id, step_no, user_id, "
        "token_hash, expires_at) VALUES (erp.current_tenant_id(), :a, :s, :u, :h, now() + "
        "make_interval(hours => :hours)) RETURNING link_id",
        {"a": approval_id, "s": step, "u": user_id, "h": digest(token), "hours": hours})
    return token


async def find(ctx: Ctx, token: str, lock: bool = False) -> dict[str, Any]:
    """The open link for a token; 404 if there is none, 410 with the reason if it cannot be used."""
    row = await one(
        ctx, "SELECT l.link_id, l.approval_id, l.step_no, l.user_id, l.expires_at, l.used_at, "
        "i.status, i.current_step, i.route_snapshot->>'title' AS title, (i.route_snapshot->>"
        "'amount')::numeric AS amount, i.route_snapshot->'steps'->(l.step_no - 1)->>'label' AS "
        "step_label, i.document_type, s.display_name AS requested_by, now() > l.expires_at AS "
        "expired FROM erp.approval_link l JOIN erp.approval_instance i ON i.approval_id = "
        "l.approval_id LEFT JOIN erp.app_user s ON s.user_id = i.submitted_by WHERE "
        f"l.token_hash = :h{' FOR UPDATE OF l' if lock else ''}", {"h": digest(token)})
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This link is not valid")
    if row["used_at"] is not None:
        raise HTTPException(status.HTTP_410_GONE, "This link has already been used")
    if row["expired"]:
        raise HTTPException(status.HTTP_410_GONE, "This link has expired")
    if row["status"] != "PENDING" or row["current_step"] != row["step_no"]:
        raise HTTPException(status.HTTP_410_GONE, "This step has already been decided")
    return row


async def principal_for(ctx: Ctx, user_id: UUID) -> Principal:
    """The signed-in-equivalent of the person the link is for."""
    who = await one(ctx, "SELECT auth_subject, email FROM erp.app_user WHERE user_id = :u",
                    {"u": user_id})
    tenant: UUID = (await ctx.session.execute(text("SELECT erp.current_tenant_id()"))).scalar_one()
    profile = await fetch_profile(ctx.session, tenant, who["auth_subject"]) if who else None
    if who is None or profile is None:
        raise HTTPException(status.HTTP_410_GONE, "This link is no longer valid")
    return Principal(user_id=who["auth_subject"], email=who["email"] or "", email_verified=True,
                     organization_id="", two_factor_enabled=False, tenant_id=tenant,
                     profile=profile)


async def decide(ctx: Ctx, token: str, decision: str, note: str | None) -> str:
    """Make the decision the link offers; returns the approval's status afterwards."""
    link = await find(ctx, token, lock=True)
    person = Ctx(ctx.session, await principal_for(ctx, link["user_id"]))
    await engine.decide(person, link["approval_id"], decision, note, f"link:{link['link_id']}",
                        link["step_no"], via="LINK")
    await one(ctx, "UPDATE erp.approval_link SET used_at = now(), used_decision = :d WHERE "
              "link_id = :l RETURNING link_id", {"d": decision, "l": link["link_id"]})
    after = await one(ctx, "SELECT status FROM erp.approval_instance WHERE approval_id = :a",
                      {"a": link["approval_id"]})
    assert after is not None
    return str(after["status"])
