"""Production costing and standard costs (BRD FR-FIN-01), configurable per company.

A production lot carries the cost of what it used: raw material at the FIFO cost it was issued at,
compound at the cost of the batch it was weighed in, metal inserts at the cost of the lot they
were made in. Packing values the finished goods: ACTUAL (rule `costMethod`) is that material cost
plus the overhead (`overheadPct` of material) spread over the pieces packed, so lost pieces make
the good ones dearer; STANDARD uses the item's standard cost and books the difference to
production variance. An item without a standard cost is valued actual and the lot says so. The
company changes the rules (`costMethod`, `overheadPct`, `cogsTiming`, `productionPosting`) in the
business rules without a release. Setting a standard cost needs fin.voucher.
"""

from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from heyyerp_api.business_rules import service as rules
from heyyerp_api.inventory.ledger import money
from heyyerp_api.masters.common import (
    Ctx,
    Strict,
    audit,
    not_found,
    one,
    require_in_scope,
    requires,
    requires_module,
)

router = APIRouter(prefix="/finance/costing", tags=["finance"])
manage = requires("fin.voucher")
read = requires_module("finance")
ZERO = Decimal(0)
SIX = Decimal("0.000001")


async def lot_unit_cost(ctx: Ctx, prod_lot_id: UUID) -> Decimal:
    """Cost per piece of a production lot's material (used when its pieces feed another lot)."""
    lot = await one(
        ctx, "SELECT l.material_cost, COALESCE((SELECT sum(m.quantity_out) FROM erp.prod_lot_move "
        "m WHERE m.prod_lot_id = l.prod_lot_id AND m.from_stage_id IS NULL), 0) AS started FROM "
        "erp.prod_lot l WHERE l.prod_lot_id = :id", {"id": prod_lot_id})
    if lot is None or lot["started"] <= 0:
        return ZERO
    return (lot["material_cost"] / lot["started"]).quantize(SIX)  # type: ignore[no-any-return]


async def finished_cost(
    ctx: Ctx, lot: dict[str, Any], packed: Decimal
) -> tuple[Decimal, Decimal, Decimal, Literal["ACTUAL", "STANDARD"]]:
    """(unit cost, material cost, conversion cost, basis) for the pieces packed from a lot."""
    material: Decimal = lot["material_cost"]
    pct = Decimal(str(await rules.get_value(ctx, "overheadPct")))
    conversion = money(material * pct / 100)
    actual = ((material + conversion) / packed).quantize(SIX)
    if await rules.get_value(ctx, "costMethod") == "STANDARD":
        item = await one(ctx, "SELECT standard_cost FROM erp.item WHERE item_id = :i",
                         {"i": lot["item_id"]})
        if item is not None and item["standard_cost"] is not None:
            return item["standard_cost"], material, conversion, "STANDARD"
    return actual, material, conversion, "ACTUAL"


class StandardIn(Strict):
    standard_cost: Decimal | None = Field(ge=0, le=Decimal(1_000_000), max_digits=18,
                                          decimal_places=6)  # empty clears it


class StandardOut(BaseModel):
    item_id: UUID
    code: str
    standard_cost: Decimal | None


@router.put("/standard/{item_id}", response_model=StandardOut)
async def set_standard(item_id: UUID, body: StandardIn, ctx: Ctx = Depends(manage)) -> StandardOut:
    found = await one(ctx, "UPDATE erp.item SET standard_cost = :c WHERE item_id = :id "
                      "RETURNING item_id, code, standard_cost",
                      {"c": body.standard_cost, "id": item_id})
    if found is None:
        raise not_found("Item")
    await audit(ctx, "costing.standard", "item", item_id, {"standard_cost": body.standard_cost})
    return StandardOut(**found)


class LotCost(BaseModel):
    prod_lot_id: UUID
    lot_no: str
    quantity: Decimal
    material_cost: Decimal
    conversion_cost: Decimal
    total_cost: Decimal
    unit_cost: Decimal | None
    cost_basis: str | None
    variance: Decimal | None  # booked to production variance when valued at standard


@router.get("/lots/{prod_lot_id}", response_model=LotCost)
async def lot_cost(prod_lot_id: UUID, ctx: Ctx = Depends(read)) -> LotCost:
    lot = await one(
        ctx, "SELECT l.prod_lot_id, l.lot_no, l.unit_id, l.material_cost, l.conversion_cost, "
        "l.unit_cost, l.cost_basis, COALESCE((SELECT p.packed_qty FROM erp.packing p WHERE "
        "p.prod_lot_id = l.prod_lot_id LIMIT 1), l.quantity) AS qty FROM erp.prod_lot l WHERE "
        "l.prod_lot_id = :id", {"id": prod_lot_id})
    if lot is None:
        raise not_found("Production lot")
    require_in_scope(ctx, lot["unit_id"])
    total = lot["material_cost"] + lot["conversion_cost"]
    value = money(lot["unit_cost"] * lot["qty"]) if lot["unit_cost"] is not None else None
    return LotCost(prod_lot_id=lot["prod_lot_id"], lot_no=lot["lot_no"], quantity=lot["qty"],
                   material_cost=lot["material_cost"], conversion_cost=lot["conversion_cost"],
                   total_cost=total, unit_cost=lot["unit_cost"], cost_basis=lot["cost_basis"],
                   variance=None if value is None else value - total)

