"""Shared reference data: payment terms, tax codes, item categories and units of measure.

Writes need refdata.manage (ADMIN / MD / PLANT_HEAD / ACCOUNTS). Units of measure are global,
seeded by migration and read-only through the API.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

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
from heyyerp_api.masters.validation import clean_code

router = APIRouter(prefix="/ref", tags=["reference data"])
manage_ref = requires("refdata.manage")


def _paged[T: BaseModel](model: type[T], items: list[dict[str, Any]], total: int,
                         page: Paging) -> Page[T]:
    return Page(items=[model(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


# ---------------------------------------------------------------- payment terms
class PaymentTermIn(Strict):
    code: str
    name: str = Field(min_length=1, max_length=120)
    due_days: int = Field(ge=0, le=365)

    _code = field_validator("code")(clean_code)


class PaymentTermPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    due_days: int | None = Field(default=None, ge=0, le=365)


class PaymentTermOut(BaseModel):
    payment_term_id: UUID
    code: str
    name: str
    due_days: int


TERM_COLS = "payment_term_id, code, name, due_days"


@router.get("/payment-terms", response_model=Page[PaymentTermOut])
async def list_payment_terms(
    page: Paging = Depends(), ctx: Ctx = Depends(read_ctx)
) -> Page[PaymentTermOut]:
    items = await rows(
        ctx, f"SELECT {TERM_COLS} FROM erp.payment_term ORDER BY code LIMIT :limit OFFSET :offset",
        {"limit": page.limit, "offset": page.offset},
    )
    total = await count(ctx, "SELECT count(*) FROM erp.payment_term")
    return _paged(PaymentTermOut, items, total, page)


@router.post("/payment-terms", response_model=PaymentTermOut, status_code=status.HTTP_201_CREATED)
async def create_payment_term(
    body: PaymentTermIn, ctx: Ctx = Depends(manage_ref)
) -> PaymentTermOut:
    with db_errors("Payment term code"):
        row = await one(
            ctx,
            "INSERT INTO erp.payment_term(tenant_id, code, name, due_days) VALUES "
            f"(erp.current_tenant_id(), :code, :name, :due_days) RETURNING {TERM_COLS}",
            body.model_dump(),
        )
    assert row is not None
    await audit(ctx, "payment_term.create", "payment_term", row["payment_term_id"],
                body.model_dump())
    return PaymentTermOut(**row)


@router.patch("/payment-terms/{term_id}", response_model=PaymentTermOut)
async def update_payment_term(
    term_id: UUID, body: PaymentTermPatch, ctx: Ctx = Depends(manage_ref)
) -> PaymentTermOut:
    current = await one(
        ctx, f"SELECT {TERM_COLS} FROM erp.payment_term WHERE payment_term_id = :id FOR UPDATE",
        {"id": term_id},
    )
    if current is None:
        raise not_found("Payment term")
    changes = body.model_dump(exclude_unset=True)
    merged = {**current, **changes}
    if merged["name"] is None or merged["due_days"] is None:
        raise HTTPException(422, "Fields cannot be null")
    row = await one(
        ctx,
        "UPDATE erp.payment_term SET name = :name, due_days = :due_days "
        f"WHERE payment_term_id = :id RETURNING {TERM_COLS}",
        {**merged, "id": term_id},
    )
    assert row is not None
    await audit(ctx, "payment_term.update", "payment_term", term_id, {"changes": changes})
    return PaymentTermOut(**row)


# ---------------------------------------------------------------- tax codes
GstRate = Field(ge=Decimal(0), le=Decimal(100), max_digits=7, decimal_places=4)


class TaxCodeIn(Strict):
    code: str
    description: str = Field(min_length=1, max_length=200)
    gst_rate: Decimal = GstRate

    _code = field_validator("code")(clean_code)


class TaxCodePatch(Strict):
    description: str | None = Field(default=None, min_length=1, max_length=200)
    gst_rate: Decimal | None = Field(default=None, ge=Decimal(0), le=Decimal(100),
                                     max_digits=7, decimal_places=4)
    active: bool | None = None


class TaxCodeOut(BaseModel):
    tax_code_id: UUID
    code: str
    description: str
    gst_rate: Decimal
    active: bool


TAX_COLS = "tax_code_id, code, description, gst_rate, active"


@router.get("/tax-codes", response_model=Page[TaxCodeOut])
async def list_tax_codes(
    active: bool | None = None, page: Paging = Depends(), ctx: Ctx = Depends(read_ctx)
) -> Page[TaxCodeOut]:
    where = "WHERE active = :active" if active is not None else ""
    filt = {"active": active} if active is not None else {}
    items = await rows(
        ctx, f"SELECT {TAX_COLS} FROM erp.tax_code {where} ORDER BY code "
        "LIMIT :limit OFFSET :offset", {**filt, "limit": page.limit, "offset": page.offset},
    )
    total = await count(ctx, f"SELECT count(*) FROM erp.tax_code {where}", filt)
    return _paged(TaxCodeOut, items, total, page)


@router.post("/tax-codes", response_model=TaxCodeOut, status_code=status.HTTP_201_CREATED)
async def create_tax_code(body: TaxCodeIn, ctx: Ctx = Depends(manage_ref)) -> TaxCodeOut:
    with db_errors("Tax code"):
        row = await one(
            ctx,
            "INSERT INTO erp.tax_code(tenant_id, code, description, gst_rate) VALUES "
            f"(erp.current_tenant_id(), :code, :description, :gst_rate) RETURNING {TAX_COLS}",
            body.model_dump(),
        )
    assert row is not None
    await audit(ctx, "tax_code.create", "tax_code", row["tax_code_id"], body.model_dump())
    return TaxCodeOut(**row)


@router.patch("/tax-codes/{tax_code_id}", response_model=TaxCodeOut)
async def update_tax_code(
    tax_code_id: UUID, body: TaxCodePatch, ctx: Ctx = Depends(manage_ref)
) -> TaxCodeOut:
    current = await one(
        ctx, f"SELECT {TAX_COLS} FROM erp.tax_code WHERE tax_code_id = :id FOR UPDATE",
        {"id": tax_code_id},
    )
    if current is None:
        raise not_found("Tax code")
    changes = body.model_dump(exclude_unset=True)
    merged = {**current, **changes}
    if any(merged[k] is None for k in ("description", "gst_rate", "active")):
        raise HTTPException(422, "Fields cannot be null")
    row = await one(
        ctx,
        "UPDATE erp.tax_code SET description = :description, gst_rate = :gst_rate, "
        f"active = :active WHERE tax_code_id = :id RETURNING {TAX_COLS}",
        {**merged, "id": tax_code_id},
    )
    assert row is not None
    await audit(ctx, "tax_code.update", "tax_code", tax_code_id, {"changes": changes})
    return TaxCodeOut(**row)


# ---------------------------------------------------------------- item categories
class CategoryIn(Strict):
    code: str
    name: str = Field(min_length=1, max_length=120)
    parent_category_id: UUID | None = None

    _code = field_validator("code")(clean_code)


class CategoryPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    parent_category_id: UUID | None = None


class CategoryOut(BaseModel):
    category_id: UUID
    code: str
    name: str
    parent_category_id: UUID | None


CAT_COLS = "category_id, code, name, parent_category_id"


async def check_parent(ctx: Ctx, parent_id: UUID | None, own_id: UUID | None) -> None:
    if parent_id is None:
        return
    if await one(ctx, "SELECT 1 AS x FROM erp.item_category WHERE category_id = :id",
                 {"id": parent_id}) is None:
        raise HTTPException(422, "Parent category does not exist")
    if own_id is not None:
        loop = await one(
            ctx,
            "WITH RECURSIVE up AS (SELECT category_id, parent_category_id FROM "
            "erp.item_category WHERE category_id = :parent UNION ALL SELECT c.category_id, "
            "c.parent_category_id FROM erp.item_category c JOIN up ON c.category_id = "
            "up.parent_category_id) SELECT 1 AS hit FROM up WHERE category_id = :own",
            {"parent": parent_id, "own": own_id},
        )
        if loop:
            raise HTTPException(422, "A category cannot be its own ancestor")


@router.get("/item-categories", response_model=Page[CategoryOut])
async def list_categories(
    page: Paging = Depends(), ctx: Ctx = Depends(read_ctx)
) -> Page[CategoryOut]:
    items = await rows(
        ctx, f"SELECT {CAT_COLS} FROM erp.item_category ORDER BY code LIMIT :limit OFFSET :offset",
        {"limit": page.limit, "offset": page.offset},
    )
    total = await count(ctx, "SELECT count(*) FROM erp.item_category")
    return _paged(CategoryOut, items, total, page)


@router.post("/item-categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(body: CategoryIn, ctx: Ctx = Depends(manage_ref)) -> CategoryOut:
    await check_parent(ctx, body.parent_category_id, None)
    with db_errors("Category code"):
        row = await one(
            ctx,
            "INSERT INTO erp.item_category(tenant_id, parent_category_id, code, name) VALUES "
            f"(erp.current_tenant_id(), :parent_category_id, :code, :name) RETURNING {CAT_COLS}",
            body.model_dump(),
        )
    assert row is not None
    await audit(ctx, "item_category.create", "item_category", row["category_id"],
                body.model_dump())
    return CategoryOut(**row)


@router.patch("/item-categories/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: UUID, body: CategoryPatch, ctx: Ctx = Depends(manage_ref)
) -> CategoryOut:
    current = await one(
        ctx, f"SELECT {CAT_COLS} FROM erp.item_category WHERE category_id = :id FOR UPDATE",
        {"id": category_id},
    )
    if current is None:
        raise not_found("Category")
    changes = body.model_dump(exclude_unset=True)
    merged = {**current, **changes}
    if merged["name"] is None:
        raise HTTPException(422, "name cannot be null")
    if "parent_category_id" in changes:
        await check_parent(ctx, merged["parent_category_id"], category_id)
    row = await one(
        ctx,
        "UPDATE erp.item_category SET name = :name, parent_category_id = :parent_category_id "
        f"WHERE category_id = :id RETURNING {CAT_COLS}",
        {**merged, "id": category_id},
    )
    assert row is not None
    await audit(ctx, "item_category.update", "item_category", category_id, {"changes": changes})
    return CategoryOut(**row)


# ---------------------------------------------------------------- units of measure (global)
class UomOut(BaseModel):
    uom_id: UUID
    code: str
    name: str
    decimal_places: int


@router.get("/uom", response_model=list[UomOut])
async def list_uom(ctx: Ctx = Depends(read_ctx)) -> list[UomOut]:
    return [UomOut(**r) for r in await rows(
        ctx, "SELECT uom_id, code, name, decimal_places FROM erp.uom ORDER BY code")]
