"""E-invoice (IRN) and e-way bill for sales invoices, through the GST provider.

Whether e-invoices are used is a company rule (`eInvoice`: OFF, MANUAL = raised on request, AUTO =
queued when the invoice is made). A request tries the provider at once and, when the provider is
down, leaves the job queued for the worker. The plant and customer addresses the portal needs are
set here (`PUT /org/units/{id}/address`, `PUT /parties/customers/{id}/address`).
"""

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from heyyerp_api.business_rules import service as rules
from heyyerp_api.gsp import service
from heyyerp_api.masters.common import (
    Ctx,
    Strict,
    audit,
    not_found,
    one,
    requires,
    requires_module,
    rows,
    scope_clause,
)
from heyyerp_api.security import has_permission

router = APIRouter(tags=["e-invoice and e-way bill"])
read = requires_module("sales")
org_manage = requires("org.manage")
customer_manage = requires("customer.manage")
admin = requires("admin")
VEHICLE = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$|^TRANS[0-9]{3,8}$")


def clean_vehicle(value: str) -> str:
    value = re.sub(r"[\s-]", "", value.upper())
    if not VEHICLE.match(value):
        raise ValueError("Give the vehicle number as on the registration, e.g. MH12AB1234")
    return value


def raiser(ctx: Ctx) -> Ctx:
    mine = ctx.principal
    if not (has_permission(mine, "gst") or has_permission(mine, "sales.dispatch")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Requires permission gst or sales.dispatch")
    return ctx


async def acting(ctx: Ctx = Depends(read)) -> Ctx:
    return raiser(ctx)


class AddressIn(Strict):
    address_line: str = Field(min_length=3, max_length=100)
    city: str = Field(min_length=2, max_length=50)
    pincode: str

    @field_validator("pincode")
    @classmethod
    def six_digits(cls, value: str) -> str:
        if not re.fullmatch(r"[1-9][0-9]{5}", value):
            raise ValueError("A pincode is six digits and does not start with 0")
        return value


class AddressOut(BaseModel):
    address_line: str | None
    city: str | None
    pincode: str | None


class TransportIn(Strict):
    vehicle_no: str = Field(min_length=6, max_length=15)
    distance_km: int = Field(ge=1, le=4000)
    transport_mode: Literal["ROAD", "RAIL", "AIR", "SHIP"] = "ROAD"

    _vehicle = field_validator("vehicle_no")(clean_vehicle)


class CancelIn(Strict):
    reason: Literal["1", "2", "3", "4"]  # duplicate, data entry mistake, order cancelled, other
    remark: str = Field(min_length=3, max_length=100)


class VehicleIn(Strict):
    vehicle_no: str = Field(min_length=6, max_length=15)
    reason: Literal["1", "2", "3", "4"]  # breakdown, transhipment, others, first time
    remark: str = Field(min_length=3, max_length=100)

    _vehicle = field_validator("vehicle_no")(clean_vehicle)


class EinvoiceOut(BaseModel):
    invoice_id: UUID
    invoice_no: str
    mode: str
    required: bool
    irn_status: str
    irn: str | None
    irn_ack_no: str | None
    irn_ack_date: datetime | None
    signed_qr: str | None
    irn_cancelled_at: datetime | None
    ewb_no: str | None
    ewb_valid_till: datetime | None
    ewb_vehicle_no: str | None
    ewb_distance_km: int | None
    ewb_transport_mode: str | None
    ewb_needed: bool
    gsp_error: str | None
    pending_jobs: list[str]


class GspJobOut(BaseModel):
    job_id: UUID
    operation: str
    invoice_id: UUID
    invoice_no: str | None
    status: str
    attempt_count: int
    last_error: str | None
    next_attempt_at: datetime | None
    created_at: datetime


async def require_issued(ctx: Ctx, invoice_id: UUID) -> dict[str, Any]:
    clause, params = scope_clause(ctx, "v.unit_id")
    head = await one(
        ctx, "SELECT v.invoice_id, v.invoice_no, v.status, v.irn, v.irn_status, v.irn_ack_date, "
        "v.ewb_no, v.taxable_amount + v.tax_amount AS gross, c.gstin AS b_gstin FROM "
        "erp.customer_invoice v JOIN erp.customer c ON c.customer_id = v.customer_id WHERE "
        f"v.invoice_id = :id{' AND ' + clause if clause else ''} FOR UPDATE OF v",
        {**params, "id": invoice_id})
    if head is None:
        raise not_found("Invoice")
    return head


async def view(ctx: Ctx, invoice_id: UUID) -> EinvoiceOut:
    row = await one(
        ctx, "SELECT v.invoice_id, v.invoice_no, v.irn_status, v.irn, v.irn_ack_no, "
        "v.irn_ack_date, v.signed_qr, v.irn_cancelled_at, v.ewb_no, v.ewb_valid_till, "
        "v.ewb_vehicle_no, v.ewb_distance_km, v.ewb_transport_mode, v.gsp_error, "
        "v.taxable_amount + v.tax_amount AS gross, c.gstin AS b_gstin FROM erp.customer_invoice v "
        "JOIN erp.customer c ON c.customer_id = v.customer_id WHERE v.invoice_id = :id",
        {"id": invoice_id})
    assert row is not None
    mode = str(await rules.get_value(ctx, "eInvoice"))
    threshold = Decimal(str(await rules.get_value(ctx, "ewbThreshold")))
    jobs = await rows(ctx, "SELECT operation FROM erp.integration_job WHERE integration_type = "
                      "'GSP' AND reference_id = :id AND status IN ('QUEUED', 'RETRY')",
                      {"id": invoice_id})
    return EinvoiceOut(
        **{k: v for k, v in row.items() if k not in ("gross", "b_gstin")}, mode=mode,
        required=mode != "OFF" and bool(row["b_gstin"]),
        ewb_needed=row["gross"] >= threshold and not row["ewb_no"],
        pending_jobs=[j["operation"] for j in jobs])


async def start(ctx: Ctx, operation: str, invoice_id: UUID, params: dict[str, Any]) -> EinvoiceOut:
    """Queue the operation and try it now; a provider that is down leaves it for the worker."""
    job_id = await service.enqueue(ctx, operation, invoice_id, params)
    await service.attempt(ctx, await service.job_row(ctx, job_id))
    return await view(ctx, invoice_id)


@router.get("/sales/invoices/{invoice_id}/einvoice", response_model=EinvoiceOut)
async def get_einvoice(invoice_id: UUID, ctx: Ctx = Depends(read)) -> EinvoiceOut:
    await require_issued(ctx, invoice_id)
    return await view(ctx, invoice_id)


@router.post("/sales/invoices/{invoice_id}/irn", response_model=EinvoiceOut)
async def raise_irn(invoice_id: UUID, ctx: Ctx = Depends(acting)) -> EinvoiceOut:
    head = await require_issued(ctx, invoice_id)
    if await rules.get_value(ctx, "eInvoice") == "OFF":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "E-invoicing is not switched on for this company")
    if head["status"] != "ISSUED":
        raise HTTPException(status.HTTP_409_CONFLICT, f"The invoice is {head['status'].lower()}")
    if not head["b_gstin"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "An e-invoice is for a customer with a GSTIN; this is a consumer sale")
    if head["irn"] and head["irn_status"] != "CANCELLED":
        raise HTTPException(status.HTTP_409_CONFLICT, "This invoice already has an IRN")
    if head["irn_status"] == "CANCELLED":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "The IRN was cancelled; a new invoice must be raised")
    await audit(ctx, "invoice.irn", "invoice", invoice_id, {"invoice_no": head["invoice_no"]})
    return await start(ctx, service.IRN_GENERATE, invoice_id, {})


@router.post("/sales/invoices/{invoice_id}/irn/cancel", response_model=EinvoiceOut)
async def cancel_irn(invoice_id: UUID, body: CancelIn, ctx: Ctx = Depends(acting)) -> EinvoiceOut:
    head = await require_issued(ctx, invoice_id)
    if head["irn_status"] != "GENERATED":
        raise HTTPException(status.HTTP_409_CONFLICT, "There is no live IRN to cancel")
    age = await one(ctx, "SELECT now() - :d > interval '24 hours' AS late",
                    {"d": head["irn_ack_date"]})
    if age is not None and age["late"]:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "An IRN can be cancelled only within 24 hours; raise a credit note")
    if head["ewb_no"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cancel the e-way bill first")
    await audit(ctx, "invoice.irn_cancel", "invoice", invoice_id,
                {"invoice_no": head["invoice_no"], "reason": body.reason})
    return await start(ctx, service.IRN_CANCEL, invoice_id, body.model_dump())


@router.post("/sales/invoices/{invoice_id}/ewb", response_model=EinvoiceOut)
async def raise_ewb(invoice_id: UUID, body: TransportIn,
                    ctx: Ctx = Depends(acting)) -> EinvoiceOut:
    head = await require_issued(ctx, invoice_id)
    if head["status"] != "ISSUED":
        raise HTTPException(status.HTTP_409_CONFLICT, f"The invoice is {head['status'].lower()}")
    if head["ewb_no"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "This invoice already has an e-way bill")
    if head["irn_status"] == "CANCELLED":
        raise HTTPException(status.HTTP_409_CONFLICT, "The IRN of this invoice was cancelled")
    await audit(ctx, "invoice.ewb", "invoice", invoice_id, {"invoice_no": head["invoice_no"]})
    return await start(ctx, service.EWB_GENERATE, invoice_id, body.model_dump())


@router.post("/sales/invoices/{invoice_id}/ewb/vehicle", response_model=EinvoiceOut)
async def change_vehicle(invoice_id: UUID, body: VehicleIn,
                         ctx: Ctx = Depends(acting)) -> EinvoiceOut:
    """Part B of the e-way bill: the goods move to another vehicle."""
    head = await require_issued(ctx, invoice_id)
    if not head["ewb_no"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "There is no e-way bill to update")
    await audit(ctx, "invoice.ewb_vehicle", "invoice", invoice_id,
                {"invoice_no": head["invoice_no"], "vehicle_no": body.vehicle_no})
    return await start(ctx, service.EWB_VEHICLE, invoice_id, body.model_dump())


@router.post("/sales/invoices/{invoice_id}/ewb/cancel", response_model=EinvoiceOut)
async def cancel_ewb(invoice_id: UUID, body: CancelIn, ctx: Ctx = Depends(acting)) -> EinvoiceOut:
    head = await require_issued(ctx, invoice_id)
    if not head["ewb_no"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "There is no e-way bill to cancel")
    await audit(ctx, "invoice.ewb_cancel", "invoice", invoice_id,
                {"invoice_no": head["invoice_no"], "reason": body.reason})
    return await start(ctx, service.EWB_CANCEL, invoice_id, body.model_dump())


@router.put("/org/units/{unit_id}/address", response_model=AddressOut)
async def set_unit_address(unit_id: UUID, body: AddressIn,
                           ctx: Ctx = Depends(org_manage)) -> AddressOut:
    row = await one(ctx, "UPDATE erp.unit SET address_line = :a, city = :c, pincode = :p WHERE "
                    "unit_id = :id RETURNING address_line, city, pincode",
                    {"a": body.address_line, "c": body.city, "p": body.pincode, "id": unit_id})
    if row is None:
        raise not_found("Unit")
    await audit(ctx, "unit.address", "unit", unit_id, {"pincode": body.pincode})
    return AddressOut(**row)


@router.put("/parties/customers/{customer_id}/address", response_model=AddressOut)
async def set_customer_address(customer_id: UUID, body: AddressIn,
                               ctx: Ctx = Depends(customer_manage)) -> AddressOut:
    row = await one(ctx, "UPDATE erp.customer SET address_line = :a, city = :c, pincode = :p "
                    "WHERE customer_id = :id RETURNING address_line, city, pincode",
                    {"a": body.address_line, "c": body.city, "p": body.pincode, "id": customer_id})
    if row is None:
        raise not_found("Customer")
    await audit(ctx, "customer.address", "customer", customer_id, {"pincode": body.pincode})
    return AddressOut(**row)


@router.get("/admin/gsp-jobs", response_model=list[GspJobOut])
async def gsp_jobs(ctx: Ctx = Depends(admin)) -> list[GspJobOut]:
    """Open and failed GST provider jobs, newest first."""
    found = await rows(
        ctx, "SELECT j.job_id, j.operation, j.reference_id AS invoice_id, v.invoice_no, j.status, "
        "j.attempt_count, j.last_error, j.next_attempt_at, j.created_at FROM erp.integration_job "
        "j LEFT JOIN erp.customer_invoice v ON v.invoice_id = j.reference_id WHERE "
        "j.integration_type = 'GSP' AND j.status IN ('QUEUED', 'RETRY', 'FAILED') ORDER BY "
        "j.created_at DESC LIMIT 100")
    return [GspJobOut(**j) for j in found]


@router.post("/admin/gsp-jobs/{job_id}/retry", response_model=dict[str, str])
async def retry_gsp_job(job_id: UUID, ctx: Ctx = Depends(admin)) -> dict[str, str]:
    """Put a failed job back in the queue and try it now (after the cause is fixed)."""
    job = await one(
        ctx, "UPDATE erp.integration_job SET status = 'QUEUED', attempt_count = 0, last_error = "
        "NULL, next_attempt_at = now() WHERE job_id = :id AND integration_type = 'GSP' AND "
        "status = 'FAILED' RETURNING job_id", {"id": job_id})
    if job is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a failed GSP job can be retried")
    outcome = await service.attempt(ctx, await service.job_row(ctx, job_id))
    await audit(ctx, "gsp.retry", "integration_job", job_id, {"outcome": outcome})
    return {"outcome": outcome}
