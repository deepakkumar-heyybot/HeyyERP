"""Calls to the GST provider as queued jobs: tried at once or by the worker, retried, never lost.

Raising an e-invoice or e-way bill, or cancelling one, is a job in `integration_job`. A job is
attempted (when a person asks, or by the worker); on success the invoice gets the numbers, on a
failure the provider could cure by waiting (timeout, server error) it is retried with a growing
delay, and after `gspMaxAttempts` or on a rejection that waiting cannot cure (missing GSTIN,
invalid data) it is FAILED with the reason, shown on the invoice and in the administrator's job
monitor, where it can be retried once the cause is fixed. There is at most one open job per
operation and invoice, so asking twice never sends twice.
"""

import json
import logging
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from heyyerp_api.business_rules import service as rules
from heyyerp_api.gsp import payload
from heyyerp_api.gsp.providers import GspError, provider
from heyyerp_api.masters.common import Ctx, one, rows

log = logging.getLogger("heyyerp.gsp")
IRN_GENERATE, IRN_CANCEL, EWB_GENERATE, EWB_CANCEL = (
    "IRN_GENERATE", "IRN_CANCEL", "EWB_GENERATE", "EWB_CANCEL")


async def enqueue(ctx: Ctx, operation: str, invoice_id: UUID,
                  params: dict[str, Any] | None = None) -> UUID:
    """Queue the operation for the invoice (the open one, if there already is one)."""
    made = await one(
        ctx, "INSERT INTO erp.integration_job(tenant_id, integration_type, operation, "
        "reference_type, reference_id, status, next_attempt_at, payload) VALUES "
        "(erp.current_tenant_id(), 'GSP', :op, 'invoice', :id, 'QUEUED', now(), "
        "CAST(:p AS jsonb)) ON CONFLICT (tenant_id, integration_type, operation, reference_id) "
        "WHERE status IN ('QUEUED', 'RETRY') DO NOTHING RETURNING job_id",
        {"op": operation, "id": invoice_id, "p": json.dumps(params or {})})
    if made is None:
        made = await one(
            ctx, "SELECT job_id FROM erp.integration_job WHERE integration_type = 'GSP' AND "
            "operation = :op AND reference_id = :id AND status IN ('QUEUED', 'RETRY')",
            {"op": operation, "id": invoice_id})
    assert made is not None
    if operation == IRN_GENERATE:
        await rows(ctx, "UPDATE erp.customer_invoice SET irn_status = 'QUEUED', gsp_error = NULL "
                   "WHERE invoice_id = :id AND irn_status IN ('NONE', 'FAILED') RETURNING "
                   "invoice_id", {"id": invoice_id})
    return made["job_id"]  # type: ignore[no-any-return]


async def execute(ctx: Ctx, operation: str, invoice_id: UUID, params: dict[str, Any]) -> None:
    """Do the call and record its numbers on the invoice."""
    gsp = provider()
    if operation == IRN_GENERATE:
        result = await gsp.generate_irn(await payload.irn_payload(ctx, invoice_id))
        await rows(
            ctx, "UPDATE erp.customer_invoice SET irn = :irn, irn_ack_no = :ack, irn_ack_date = "
            ":d, signed_qr = :qr, irn_status = 'GENERATED', gsp_error = NULL WHERE invoice_id = "
            ":id RETURNING invoice_id",
            {"irn": result.irn, "ack": result.ack_no, "d": result.ack_date, "qr": result.signed_qr,
             "id": invoice_id})
    elif operation == EWB_GENERATE:
        out = await gsp.generate_ewb(await payload.ewb_payload(ctx, invoice_id, params))
        await rows(
            ctx, "UPDATE erp.customer_invoice SET ewb_no = :n, ewb_valid_till = :v, ewb_vehicle_no "
            "= :veh, ewb_distance_km = :km, ewb_transport_mode = :mode, gsp_error = NULL WHERE "
            "invoice_id = :id RETURNING invoice_id",
            {"n": out.ewb_no, "v": out.valid_till, "veh": params["vehicle_no"],
             "km": params["distance_km"], "mode": params["transport_mode"], "id": invoice_id})
    elif operation == IRN_CANCEL:
        head = await one(ctx, "SELECT irn FROM erp.customer_invoice WHERE invoice_id = :id",
                         {"id": invoice_id})
        assert head is not None and head["irn"]
        await gsp.cancel_irn(head["irn"], params["reason"], params["remark"])
        await rows(ctx, "UPDATE erp.customer_invoice SET irn_status = 'CANCELLED', "
                   "irn_cancelled_at = now(), gsp_error = NULL WHERE invoice_id = :id RETURNING "
                   "invoice_id", {"id": invoice_id})
    elif operation == EWB_CANCEL:
        head = await one(ctx, "SELECT ewb_no FROM erp.customer_invoice WHERE invoice_id = :id",
                         {"id": invoice_id})
        assert head is not None and head["ewb_no"]
        await gsp.cancel_ewb(head["ewb_no"], params["reason"], params["remark"])
        await rows(ctx, "UPDATE erp.customer_invoice SET ewb_no = NULL, ewb_valid_till = NULL, "
                   "gsp_error = NULL WHERE invoice_id = :id RETURNING invoice_id",
                   {"id": invoice_id})
    else:
        raise GspError(f"Unknown operation {operation}", retryable=False)


async def attempt(ctx: Ctx, job: dict[str, Any]) -> str:
    """One try of a job; returns DONE, RETRY or FAILED and records it."""
    top = int(await rules.get_value(ctx, "gspMaxAttempts"))
    attempts = job["attempt_count"] + 1
    failure: str | None = None
    retryable = True
    try:
        async with ctx.session.begin_nested():  # a half-done call leaves no half-written invoice
            await execute(ctx, job["operation"], job["reference_id"], job["payload"])
    except HTTPException as exc:  # missing data and the like: waiting will not cure it
        failure, retryable = str(exc.detail), False
    except GspError as exc:
        failure, retryable = str(exc), exc.retryable
    except Exception as exc:
        log.exception("GSP job %s failed", job["job_id"])
        failure = f"{type(exc).__name__}: {str(exc)[:200]}"
    if failure is None:
        await rows(ctx, "UPDATE erp.integration_job SET status = 'DONE', attempt_count = :a, "
                   "last_error = NULL, completed_at = now() WHERE job_id = :id RETURNING job_id",
                   {"a": attempts, "id": job["job_id"]})
        return "DONE"
    final = not retryable or attempts >= top
    await rows(
        ctx, "UPDATE erp.integration_job SET status = CASE WHEN :final THEN 'FAILED' ELSE 'RETRY' "
        "END, attempt_count = CAST(:a AS int), last_error = :e, next_attempt_at = now() + "
        "make_interval(mins => LEAST(power(2, CAST(:a AS int))::int, 360)) WHERE job_id = :id "
        "RETURNING job_id", {"final": final, "a": attempts, "e": failure[:300],
                             "id": job["job_id"]})
    await rows(
        ctx, "UPDATE erp.customer_invoice SET gsp_error = :e, irn_status = CASE WHEN :op = "
        "'IRN_GENERATE' AND :final THEN 'FAILED' ELSE irn_status END WHERE invoice_id = :id "
        "RETURNING invoice_id", {"e": failure[:300], "op": job["operation"], "final": final,
                                 "id": job["reference_id"]})
    return "FAILED" if final else "RETRY"


async def job_row(ctx: Ctx, job_id: UUID) -> dict[str, Any]:
    row = await one(ctx, "SELECT job_id, operation, reference_id, attempt_count, payload FROM "
                    "erp.integration_job WHERE job_id = :id", {"id": job_id})
    assert row is not None
    return row


async def run_due_jobs(ctx: Ctx, limit: int = 20) -> dict[str, int]:
    """The worker's part: try the GSP jobs that are due."""
    due = await rows(
        ctx, "SELECT job_id, operation, reference_id, attempt_count, payload FROM "
        "erp.integration_job WHERE integration_type = 'GSP' AND status IN ('QUEUED', 'RETRY') AND "
        "(next_attempt_at IS NULL OR next_attempt_at <= now()) ORDER BY created_at LIMIT :n FOR "
        "UPDATE SKIP LOCKED", {"n": limit})
    out = {"gsp_done": 0, "gsp_retry": 0, "gsp_failed": 0}
    for job in due:
        outcome = await attempt(ctx, job)
        out[{"DONE": "gsp_done", "RETRY": "gsp_retry", "FAILED": "gsp_failed"}[outcome]] += 1
    return out
