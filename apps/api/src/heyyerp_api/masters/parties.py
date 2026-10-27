"""Business partners: customers and vendors.

Customers need customer.manage (SALES, PLANT_HEAD, ADMIN, MD); vendors need vendor.manage
(PURCHASE, PLANT_HEAD, ADMIN, MD). A new vendor is always PENDING_APPROVAL and cannot receive
purchase orders until the Phase 3 approval workflow approves it, so the client never sets status.
"""

from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator

from heyyerp_api.masters.common import (
    Ctx,
    Page,
    Paging,
    Strict,
    audit,
    count,
    db_errors,
    not_found,
    one,
    read_ctx,
    requires,
    rows,
)
from heyyerp_api.masters.validation import clean_code, clean_gstin, clean_pan

router = APIRouter(prefix="/parties", tags=["customers and vendors"])
manage_customers = requires("customer.manage")
manage_vendors = requires("vendor.manage")
Name = Field(min_length=1, max_length=160)
VendorStatus = Literal["PENDING_APPROVAL", "APPROVED", "REJECTED", "INACTIVE"]


def like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def require_payment_term(ctx: Ctx, term_id: UUID | None) -> None:
    if term_id is None:
        return
    found = await one(
        ctx, "SELECT 1 AS x FROM erp.payment_term WHERE payment_term_id = :id", {"id": term_id}
    )
    if found is None:
        raise HTTPException(422, "Payment term does not exist")


def check_pan_matches_gstin(gstin: str | None, pan: str | None) -> None:
    if gstin and pan and gstin[2:12] != pan:
        raise ValueError("PAN does not match the GSTIN")


# ---------------------------------------------------------------- customers
class CustomerIn(Strict):
    code: str
    name: str = Name
    gstin: str | None = None
    payment_term_id: UUID | None = None
    credit_limit: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)

    _code = field_validator("code")(clean_code)
    _gstin = field_validator("gstin")(clean_gstin)


class CustomerPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    gstin: str | None = None
    payment_term_id: UUID | None = None
    credit_limit: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    active: bool | None = None

    _gstin = field_validator("gstin")(clean_gstin)


class CustomerOut(BaseModel):
    customer_id: UUID
    code: str
    name: str
    gstin: str | None
    payment_term_id: UUID | None
    credit_limit: Decimal | None
    active: bool


CUST_COLS = "customer_id, code, name, gstin, payment_term_id, credit_limit, active"


@router.get("/customers", response_model=Page[CustomerOut])
async def list_customers(
    active: bool | None = None,
    q: str | None = None,
    page: Paging = Depends(),
    ctx: Ctx = Depends(read_ctx),
) -> Page[CustomerOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    if active is not None:
        conditions.append("active = :active")
        params["active"] = active
    if q:
        conditions.append("(code ILIKE :q OR name ILIKE :q)")
        params["q"] = like(q)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(
        ctx, f"SELECT {CUST_COLS} FROM erp.customer {where} ORDER BY code "
        "LIMIT :limit OFFSET :offset", {**params, "limit": page.limit, "offset": page.offset},
    )
    total = await count(ctx, f"SELECT count(*) FROM erp.customer {where}", params)
    return Page(items=[CustomerOut(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/customers/{customer_id}", response_model=CustomerOut)
async def get_customer(customer_id: UUID, ctx: Ctx = Depends(read_ctx)) -> CustomerOut:
    row = await one(ctx, f"SELECT {CUST_COLS} FROM erp.customer WHERE customer_id = :id",
                    {"id": customer_id})
    if row is None:
        raise not_found("Customer")
    return CustomerOut(**row)


@router.post("/customers", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
async def create_customer(
    body: CustomerIn, ctx: Ctx = Depends(manage_customers)
) -> CustomerOut:
    await require_payment_term(ctx, body.payment_term_id)
    with db_errors("Customer code"):
        row = await one(
            ctx,
            "INSERT INTO erp.customer(tenant_id, code, name, gstin, payment_term_id, "
            "credit_limit) VALUES (erp.current_tenant_id(), :code, :name, :gstin, "
            f":payment_term_id, :credit_limit) RETURNING {CUST_COLS}",
            body.model_dump(),
        )
    assert row is not None
    await audit(ctx, "customer.create", "customer", row["customer_id"], body.model_dump())
    return CustomerOut(**row)


@router.patch("/customers/{customer_id}", response_model=CustomerOut)
async def update_customer(
    customer_id: UUID, body: CustomerPatch, ctx: Ctx = Depends(manage_customers)
) -> CustomerOut:
    current = await one(
        ctx, f"SELECT {CUST_COLS} FROM erp.customer WHERE customer_id = :id FOR UPDATE",
        {"id": customer_id},
    )
    if current is None:
        raise not_found("Customer")
    changes = body.model_dump(exclude_unset=True)
    merged = {**current, **changes}
    if merged["name"] is None or merged["active"] is None:
        raise HTTPException(422, "name and active cannot be null")
    if "payment_term_id" in changes:
        await require_payment_term(ctx, merged["payment_term_id"])
    row = await one(
        ctx,
        "UPDATE erp.customer SET name = :name, gstin = :gstin, payment_term_id = "
        ":payment_term_id, credit_limit = :credit_limit, active = :active "
        f"WHERE customer_id = :id RETURNING {CUST_COLS}",
        {**merged, "id": customer_id},
    )
    assert row is not None
    await audit(ctx, "customer.update", "customer", customer_id, {"changes": changes})
    return CustomerOut(**row)


# ---------------------------------------------------------------- vendors
class VendorIn(Strict):
    code: str
    name: str = Name
    gstin: str | None = None
    pan: str | None = None
    msme_flag: bool = False
    payment_term_id: UUID | None = None

    _code = field_validator("code")(clean_code)
    _gstin = field_validator("gstin")(clean_gstin)
    _pan = field_validator("pan")(clean_pan)

    @model_validator(mode="after")
    def _pan_matches(self) -> "VendorIn":
        check_pan_matches_gstin(self.gstin, self.pan)
        return self


class VendorPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    gstin: str | None = None
    pan: str | None = None
    msme_flag: bool | None = None
    payment_term_id: UUID | None = None

    _gstin = field_validator("gstin")(clean_gstin)
    _pan = field_validator("pan")(clean_pan)


class VendorOut(BaseModel):
    vendor_id: UUID
    code: str
    name: str
    gstin: str | None
    pan: str | None
    msme_flag: bool
    payment_term_id: UUID | None
    status: VendorStatus


VENDOR_COLS = "vendor_id, code, name, gstin, pan, msme_flag, payment_term_id, status"


@router.get("/vendors", response_model=Page[VendorOut])
async def list_vendors(
    vendor_status: VendorStatus | None = None,
    q: str | None = None,
    page: Paging = Depends(),
    ctx: Ctx = Depends(read_ctx),
) -> Page[VendorOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    if vendor_status is not None:
        conditions.append("status = :status")
        params["status"] = vendor_status
    if q:
        conditions.append("(code ILIKE :q OR name ILIKE :q)")
        params["q"] = like(q)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(
        ctx, f"SELECT {VENDOR_COLS} FROM erp.vendor {where} ORDER BY code "
        "LIMIT :limit OFFSET :offset", {**params, "limit": page.limit, "offset": page.offset},
    )
    total = await count(ctx, f"SELECT count(*) FROM erp.vendor {where}", params)
    return Page(items=[VendorOut(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/vendors/{vendor_id}", response_model=VendorOut)
async def get_vendor(vendor_id: UUID, ctx: Ctx = Depends(read_ctx)) -> VendorOut:
    row = await one(ctx, f"SELECT {VENDOR_COLS} FROM erp.vendor WHERE vendor_id = :id",
                    {"id": vendor_id})
    if row is None:
        raise not_found("Vendor")
    return VendorOut(**row)


@router.post("/vendors", response_model=VendorOut, status_code=status.HTTP_201_CREATED)
async def create_vendor(body: VendorIn, ctx: Ctx = Depends(manage_vendors)) -> VendorOut:
    await require_payment_term(ctx, body.payment_term_id)
    with db_errors("Vendor code or GSTIN"):
        row = await one(
            ctx,
            "INSERT INTO erp.vendor(tenant_id, code, name, gstin, pan, msme_flag, "
            "payment_term_id) VALUES (erp.current_tenant_id(), :code, :name, :gstin, :pan, "
            f":msme_flag, :payment_term_id) RETURNING {VENDOR_COLS}",
            body.model_dump(),
        )
    assert row is not None
    await audit(ctx, "vendor.create", "vendor", row["vendor_id"], body.model_dump())
    return VendorOut(**row)


@router.patch("/vendors/{vendor_id}", response_model=VendorOut)
async def update_vendor(
    vendor_id: UUID, body: VendorPatch, ctx: Ctx = Depends(manage_vendors)
) -> VendorOut:
    current = await one(
        ctx, f"SELECT {VENDOR_COLS} FROM erp.vendor WHERE vendor_id = :id FOR UPDATE",
        {"id": vendor_id},
    )
    if current is None:
        raise not_found("Vendor")
    changes = body.model_dump(exclude_unset=True)
    merged: dict[str, Any] = {**current, **changes}
    if merged["name"] is None or merged["msme_flag"] is None:
        raise HTTPException(422, "name and msme_flag cannot be null")
    try:
        check_pan_matches_gstin(merged["gstin"], merged["pan"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if "payment_term_id" in changes:
        await require_payment_term(ctx, merged["payment_term_id"])
    with db_errors("Vendor GSTIN"):
        row = await one(
            ctx,
            "UPDATE erp.vendor SET name = :name, gstin = :gstin, pan = :pan, msme_flag = "
            f":msme_flag, payment_term_id = :payment_term_id WHERE vendor_id = :id "
            f"RETURNING {VENDOR_COLS}",
            {**merged, "id": vendor_id},
        )
    assert row is not None
    await audit(ctx, "vendor.update", "vendor", vendor_id, {"changes": changes})
    return VendorOut(**row)


@router.post("/vendors/{vendor_id}/deactivate", response_model=VendorOut)
async def deactivate_vendor(vendor_id: UUID, ctx: Ctx = Depends(manage_vendors)) -> VendorOut:
    # ponytail: approval and reactivation arrive with the Phase 3 workflow engine.
    current = await one(
        ctx, "SELECT status FROM erp.vendor WHERE vendor_id = :id FOR UPDATE", {"id": vendor_id}
    )
    if current is None:
        raise not_found("Vendor")
    if current["status"] == "INACTIVE":
        raise HTTPException(409, "Vendor is already inactive")
    row = await one(
        ctx, f"UPDATE erp.vendor SET status = 'INACTIVE' WHERE vendor_id = :id "
        f"RETURNING {VENDOR_COLS}", {"id": vendor_id},
    )
    assert row is not None
    await audit(ctx, "vendor.deactivate", "vendor", vendor_id, {"from": current["status"]})
    return VendorOut(**row)
