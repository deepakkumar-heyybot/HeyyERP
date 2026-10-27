"""Customer price lists (BRD FR-SAL-02: rates from the customer's price list).

A price is what one customer pays for one item from a date. There is at most one open-ended price
per customer and item: adding a newer price closes the previous one the day before. Quotations and
sales orders take the price in force on their date when no rate is typed. Needs sales.quote.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from heyyerp_api.masters.common import (
    Ctx,
    Page,
    Paging,
    Strict,
    audit,
    count,
    not_found,
    one,
    requires,
    requires_module,
    rows,
)

router = APIRouter(prefix="/sales", tags=["sales"])
manage = requires("sales.quote")
read = requires_module("sales")


class PriceIn(Strict):
    customer_id: UUID
    item_id: UUID
    unit_price: Decimal = Field(ge=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=2)
    valid_from: date


class PriceOut(BaseModel):
    price_id: UUID
    customer_id: UUID
    customer_code: str
    item_id: UUID
    item_code: str
    unit_price: Decimal
    valid_from: date
    valid_to: date | None


PRICE_SQL = (
    "SELECT p.price_id, p.customer_id, c.code AS customer_code, p.item_id, i.code AS item_code, "
    "p.unit_price, p.valid_from, p.valid_to FROM erp.customer_price p JOIN erp.customer c ON "
    "c.customer_id = p.customer_id JOIN erp.item i ON i.item_id = p.item_id"
)


async def price_on(ctx: Ctx, customer_id: UUID, item_id: UUID, day: date) -> Decimal | None:
    """The price in force for the customer and item on a date, if any."""
    found = await one(
        ctx, "SELECT unit_price FROM erp.customer_price WHERE customer_id = :c AND item_id = :i "
        "AND valid_from <= :d AND (valid_to IS NULL OR valid_to >= :d) ORDER BY valid_from DESC "
        "LIMIT 1", {"c": customer_id, "i": item_id, "d": day})
    return found["unit_price"] if found else None


@router.post("/prices", response_model=PriceOut, status_code=status.HTTP_201_CREATED)
async def add_price(body: PriceIn, ctx: Ctx = Depends(manage)) -> PriceOut:
    customer = await one(ctx, "SELECT active FROM erp.customer WHERE customer_id = :id",
                         {"id": body.customer_id})
    if customer is None or not customer["active"]:
        raise HTTPException(422, "The customer does not exist or is inactive")
    item = await one(ctx, "SELECT active, item_type FROM erp.item WHERE item_id = :id",
                     {"id": body.item_id})
    if item is None or not item["active"] or item["item_type"] == "RM":
        raise HTTPException(422, "Prices are set for active items that are sold, not raw material")
    open_price = await one(
        ctx, "SELECT price_id, valid_from FROM erp.customer_price WHERE customer_id = :c AND "
        "item_id = :i AND valid_to IS NULL FOR UPDATE", {"c": body.customer_id, "i": body.item_id})
    later = await one(
        ctx, "SELECT 1 AS x FROM erp.customer_price WHERE customer_id = :c AND item_id = :i AND "
        "valid_from >= :d AND valid_to IS NOT NULL", {"c": body.customer_id, "i": body.item_id,
                                                      "d": body.valid_from})
    stale = open_price is not None and body.valid_from <= open_price["valid_from"]
    if later is not None or stale:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A newer price already exists; a new price must start after the latest one")
    if open_price is not None:
        await rows(ctx, "UPDATE erp.customer_price SET valid_to = :d WHERE price_id = :id "
                   "RETURNING price_id", {"d": body.valid_from - timedelta(days=1),
                                          "id": open_price["price_id"]})
    created = await one(
        ctx, "INSERT INTO erp.customer_price(tenant_id, customer_id, item_id, unit_price, "
        "valid_from, created_by) VALUES (erp.current_tenant_id(), :c, :i, :p, :d, :me) "
        "RETURNING price_id",
        {"c": body.customer_id, "i": body.item_id, "p": body.unit_price, "d": body.valid_from,
         "me": ctx.principal.profile.app_user_id})
    assert created is not None
    await audit(ctx, "price.add", "customer_price", created["price_id"], body.model_dump())
    result = await one(ctx, f"{PRICE_SQL} WHERE p.price_id = :id", {"id": created["price_id"]})
    assert result is not None
    return PriceOut(**result)


@router.get("/prices", response_model=Page[PriceOut])
async def list_prices(
    customer_id: UUID | None = None, item_id: UUID | None = None,
    page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[PriceOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    for text_, key, value in (("p.customer_id = :c", "c", customer_id),
                              ("p.item_id = :i", "i", item_id)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{PRICE_SQL}{where} ORDER BY c.code, i.code, p.valid_from DESC "
                       "LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.customer_price p{where}", params)
    return Page(items=[PriceOut(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/prices/effective", response_model=PriceOut)
async def effective_price(
    customer_id: UUID, item_id: UUID, on: date | None = None, ctx: Ctx = Depends(read)
) -> PriceOut:
    day = on or date.today()
    found = await one(
        ctx, f"{PRICE_SQL} WHERE p.customer_id = :c AND p.item_id = :i AND p.valid_from <= :d "
        "AND (p.valid_to IS NULL OR p.valid_to >= :d) ORDER BY p.valid_from DESC LIMIT 1",
        {"c": customer_id, "i": item_id, "d": day})
    if found is None:
        raise not_found(f"Price on {day}")
    return PriceOut(**found)
