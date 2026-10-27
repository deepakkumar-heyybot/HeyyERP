"""Items (products and materials): definition, plant policies, specs, customer part numbers.

Item master data is tenant-wide; where an item applies is the plant policy (reorder point, reorder
quantity, default store): an item is applicable at a plant when it has a policy row there.
Writes need item.manage (customer part numbers need customer.manage). Items are deactivated, never
deleted, and an item that is still a component of a live bill of material cannot be deactivated.
"""

from decimal import Decimal
from typing import Any, Literal, Self
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator

from heyyerp_api.masters.common import (
    Ctx,
    Page,
    Paging,
    Strict,
    and_,
    audit,
    count,
    db_errors,
    not_found,
    one,
    read_ctx,
    require_in_scope,
    requires,
    rows,
    scope_clause,
)
from heyyerp_api.masters.validation import clean_hsn, clean_item_code

router = APIRouter(prefix="/product", tags=["product engineering"])
manage_items = requires("item.manage")
manage_parts = requires("customer.manage")
ItemType = Literal["RM", "WIP", "FG", "SPARE", "CONSUMABLE", "CAPITAL", "OTHER"]
MADE_TYPES = ("WIP", "FG")  # only items we make follow a production route
Name = Field(min_length=1, max_length=160)


# ------------------------------------------------------------------ items
class ItemIn(Strict):
    code: str
    name: str = Name
    item_type: ItemType
    uom_code: str = Field(min_length=1, max_length=10)
    category_id: UUID | None = None
    hsn_code: str | None = None
    shelf_life_days: int | None = Field(default=None, ge=0, le=36500)
    route_code: str | None = None

    _code = field_validator("code")(clean_item_code)
    _hsn = field_validator("hsn_code")(clean_hsn)


class ItemPatch(Strict):
    """Type and unit of measure never change after creation: stock and BOMs depend on them."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    category_id: UUID | None = None
    hsn_code: str | None = None
    shelf_life_days: int | None = Field(default=None, ge=0, le=36500)
    route_code: str | None = None
    active: bool | None = None

    _hsn = field_validator("hsn_code")(clean_hsn)


class ItemOut(BaseModel):
    item_id: UUID
    code: str
    name: str
    item_type: ItemType
    uom_code: str
    category_id: UUID | None
    hsn_code: str | None
    shelf_life_days: int | None
    route_code: str | None
    active: bool


ITEM_SQL = (
    "SELECT i.item_id, i.code, i.name, i.item_type, u.code AS uom_code, i.category_id, "
    "i.hsn_code, i.shelf_life_days, i.route_code, i.active "
    "FROM erp.item i JOIN erp.uom u ON u.uom_id = i.uom_id"
)


async def fetch_item(ctx: Ctx, item_id: UUID) -> dict[str, Any] | None:
    return await one(ctx, f"{ITEM_SQL} WHERE i.item_id = :id", {"id": item_id})


async def require_item(ctx: Ctx, item_id: UUID) -> dict[str, Any]:
    item = await fetch_item(ctx, item_id)
    if item is None:
        raise not_found("Item")
    return item


async def check_route(ctx: Ctx, item_type: str, route_code: str | None) -> None:
    if route_code is None:
        return
    if item_type not in MADE_TYPES:
        raise HTTPException(422, "Only work-in-progress and finished items follow a route")
    if await one(ctx, "SELECT 1 AS x FROM erp.route WHERE code = :c AND active",
                 {"c": route_code}) is None:
        raise HTTPException(422, f"No active production route {route_code!r}")


@router.get("/items", response_model=Page[ItemOut])
async def list_items(
    item_type: ItemType | None = None,
    active: bool | None = None,
    category_id: UUID | None = None,
    unit_id: UUID | None = None,
    q: str | None = None,
    page: Paging = Depends(),
    ctx: Ctx = Depends(read_ctx),
) -> Page[ItemOut]:
    """`unit_id` lists only the items that apply at that plant (they have a policy there)."""
    conditions: list[str] = []
    params: dict[str, Any] = {}
    for column, name, value in (("i.item_type", "item_type", item_type),
                                ("i.active", "active", active),
                                ("i.category_id", "category_id", category_id)):
        if value is not None:
            conditions.append(f"{column} = :{name}")
            params[name] = value
    if unit_id is not None:
        if not ctx.principal.may_see_unit(unit_id):
            raise not_found("Plant")
        conditions.append("EXISTS (SELECT 1 FROM erp.item_unit_policy p WHERE p.item_id = "
                          "i.item_id AND p.unit_id = :unit_id)")
        params["unit_id"] = unit_id
    if q:
        conditions.append("(i.code ILIKE :q OR i.name ILIKE :q)")
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["q"] = f"%{escaped}%"
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{ITEM_SQL}{where} ORDER BY i.code LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.item i{where}", params)
    return Page(items=[ItemOut(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/items/{item_id}", response_model=ItemOut)
async def get_item(item_id: UUID, ctx: Ctx = Depends(read_ctx)) -> ItemOut:
    return ItemOut(**await require_item(ctx, item_id))


@router.post("/items", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
async def create_item(body: ItemIn, ctx: Ctx = Depends(manage_items)) -> ItemOut:
    uom = await one(ctx, "SELECT uom_id FROM erp.uom WHERE code = :c", {"c": body.uom_code.upper()})
    if uom is None:
        raise HTTPException(422, f"Unknown unit of measure {body.uom_code!r}")
    await check_route(ctx, body.item_type, body.route_code)
    with db_errors("Item code or category"):
        created = await one(
            ctx,
            "INSERT INTO erp.item(tenant_id, category_id, uom_id, code, name, item_type, "
            "hsn_code, shelf_life_days, route_code) VALUES (erp.current_tenant_id(), "
            ":category_id, :uom_id, :code, :name, :item_type, :hsn_code, :shelf_life_days, "
            ":route_code) RETURNING item_id",
            {**body.model_dump(), "uom_id": uom["uom_id"]},
        )
    assert created is not None
    await audit(ctx, "item.create", "item", created["item_id"], body.model_dump())
    return ItemOut(**await require_item(ctx, created["item_id"]))


@router.patch("/items/{item_id}", response_model=ItemOut)
async def update_item(item_id: UUID, body: ItemPatch, ctx: Ctx = Depends(manage_items)) -> ItemOut:
    current = await require_item(ctx, item_id)
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None or changes.get("active", True) is None:
        raise HTTPException(422, "name and active cannot be null")
    merged = {**current, **changes}
    if "route_code" in changes:
        await check_route(ctx, current["item_type"], merged["route_code"])
    if current["active"] and merged["active"] is False:
        live = await count(
            ctx,
            "SELECT count(*) FROM erp.bom_line l JOIN erp.bom b ON b.bom_id = l.bom_id "
            "WHERE l.component_item_id = :id AND b.status IN "
            "('DRAFT', 'PENDING_APPROVAL', 'APPROVED')", {"id": item_id},
        )
        if live:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This item is a component of a live bill of material; revise those first",
            )
    with db_errors("Item"):
        await one(
            ctx,
            "UPDATE erp.item SET name = :name, category_id = :category_id, hsn_code = :hsn_code, "
            "shelf_life_days = :shelf_life_days, route_code = :route_code, active = :active "
            "WHERE item_id = :id RETURNING item_id", {**merged, "id": item_id},
        )
    await audit(ctx, "item.update", "item", item_id, {"changes": changes})
    return ItemOut(**await require_item(ctx, item_id))


# ------------------------------------------------------------------ plant policies
class PolicyIn(Strict):
    reorder_point: Decimal = Field(default=Decimal(0), ge=0, max_digits=18, decimal_places=3)
    reorder_qty: Decimal = Field(default=Decimal(0), ge=0, max_digits=18, decimal_places=3)
    default_store_id: UUID | None = None


class PolicyOut(BaseModel):
    unit_id: UUID
    unit_code: str
    reorder_point: Decimal
    reorder_qty: Decimal
    default_store_id: UUID | None


POLICY_SQL = (
    "SELECT p.unit_id, u.code AS unit_code, p.reorder_point, p.reorder_qty, p.default_store_id "
    "FROM erp.item_unit_policy p JOIN erp.unit u ON u.unit_id = p.unit_id"
)


@router.get("/items/{item_id}/policies", response_model=list[PolicyOut])
async def list_policies(item_id: UUID, ctx: Ctx = Depends(read_ctx)) -> list[PolicyOut]:
    await require_item(ctx, item_id)
    clause, params = scope_clause(ctx, "p.unit_id")
    found = await rows(ctx, f"{POLICY_SQL} WHERE p.item_id = :id{and_(clause)} ORDER BY u.code",
                       {"id": item_id, **params})
    return [PolicyOut(**p) for p in found]


@router.put("/items/{item_id}/policies/{unit_id}", response_model=PolicyOut)
async def set_policy(
    item_id: UUID, unit_id: UUID, body: PolicyIn, ctx: Ctx = Depends(manage_items)
) -> PolicyOut:
    """Make the item applicable at a plant (or change its reorder levels and default store)."""
    require_in_scope(ctx, unit_id)
    item = await require_item(ctx, item_id)
    if not item["active"]:
        raise HTTPException(422, "Item is inactive")
    plant = await one(ctx, "SELECT active FROM erp.unit WHERE unit_id = :id", {"id": unit_id})
    if plant is None:
        raise not_found("Plant")
    if not plant["active"]:
        raise HTTPException(422, "Plant is inactive")
    if body.default_store_id is not None and await one(
        ctx, "SELECT 1 AS x FROM erp.store WHERE store_id = :s AND unit_id = :u AND active",
        {"s": body.default_store_id, "u": unit_id},
    ) is None:
        raise HTTPException(422, "Default store must be an active store of that plant")
    with db_errors("Item plant policy"):
        await one(
            ctx,
            "INSERT INTO erp.item_unit_policy(tenant_id, item_id, unit_id, default_store_id, "
            "reorder_point, reorder_qty) VALUES (erp.current_tenant_id(), :item, :unit, :store, "
            ":rop, :roq) ON CONFLICT (item_id, unit_id) DO UPDATE SET "
            "default_store_id = EXCLUDED.default_store_id, reorder_point = EXCLUDED.reorder_point, "
            "reorder_qty = EXCLUDED.reorder_qty RETURNING item_id",
            {"item": item_id, "unit": unit_id, "store": body.default_store_id,
             "rop": body.reorder_point, "roq": body.reorder_qty},
        )
    await audit(ctx, "item.policy.set", "item", item_id, {"unit_id": unit_id, **body.model_dump()})
    result = await one(ctx, f"{POLICY_SQL} WHERE p.item_id = :i AND p.unit_id = :u",
                       {"i": item_id, "u": unit_id})
    assert result is not None
    return PolicyOut(**result)


@router.delete("/items/{item_id}/policies/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_policy(item_id: UUID, unit_id: UUID, ctx: Ctx = Depends(manage_items)) -> None:
    """Stop the item applying at a plant. ponytail: Phase 6 must refuse this while stock exists."""
    require_in_scope(ctx, unit_id)
    removed = await rows(
        ctx, "DELETE FROM erp.item_unit_policy WHERE item_id = :i AND unit_id = :u "
        "RETURNING item_id", {"i": item_id, "u": unit_id})
    if not removed:
        raise not_found("Item plant policy")
    await audit(ctx, "item.policy.remove", "item", item_id, {"unit_id": unit_id})


# ------------------------------------------------------------------ technical specifications
class SpecIn(Strict):
    unit: str | None = Field(default=None, max_length=20)
    target_value: Decimal | None = Field(default=None, max_digits=18, decimal_places=6)
    min_value: Decimal | None = Field(default=None, max_digits=18, decimal_places=6)
    max_value: Decimal | None = Field(default=None, max_digits=18, decimal_places=6)
    text_value: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _meaningful(self) -> Self:
        numbers = (self.target_value, self.min_value, self.max_value)
        if all(n is None for n in numbers) and not (self.text_value or "").strip():
            raise ValueError("A specification needs a value, a limit or text")
        if self.min_value is not None and self.max_value is not None \
                and self.max_value < self.min_value:
            raise ValueError("max_value must not be below min_value")
        low, high = self.min_value, self.max_value
        if self.target_value is not None and (
            (low is not None and self.target_value < low)
            or (high is not None and self.target_value > high)
        ):
            raise ValueError("target_value must lie within min_value and max_value")
        return self


class SpecOut(SpecIn):
    name: str


SPEC_COLS = "name, unit, target_value, min_value, max_value, text_value, note"


@router.get("/items/{item_id}/specs", response_model=list[SpecOut])
async def list_specs(item_id: UUID, ctx: Ctx = Depends(read_ctx)) -> list[SpecOut]:
    await require_item(ctx, item_id)
    found = await rows(ctx, f"SELECT {SPEC_COLS} FROM erp.item_specification "
                       "WHERE item_id = :id ORDER BY name", {"id": item_id})
    return [SpecOut(**s) for s in found]


@router.put("/items/{item_id}/specs/{name}", response_model=SpecOut)
async def set_spec(
    item_id: UUID, name: str, body: SpecIn, ctx: Ctx = Depends(manage_items)
) -> SpecOut:
    """Create or replace one specification (for example Hardness, 60-70 Shore A)."""
    await require_item(ctx, item_id)
    name = name.strip()
    if not name or len(name) > 80:
        raise HTTPException(422, "Specification name must be 1-80 characters")
    with db_errors("Specification"):
        saved = await one(
            ctx,
            "INSERT INTO erp.item_specification(tenant_id, item_id, name, unit, target_value, "
            "min_value, max_value, text_value, note) VALUES (erp.current_tenant_id(), :item, "
            ":name, :unit, :target_value, :min_value, :max_value, :text_value, :note) "
            "ON CONFLICT (item_id, name) DO UPDATE SET unit = EXCLUDED.unit, "
            "target_value = EXCLUDED.target_value, min_value = EXCLUDED.min_value, "
            "max_value = EXCLUDED.max_value, text_value = EXCLUDED.text_value, "
            f"note = EXCLUDED.note RETURNING {SPEC_COLS}",
            {"item": item_id, "name": name, **body.model_dump()},
        )
    assert saved is not None
    await audit(ctx, "item.spec.set", "item", item_id, {"name": name, **body.model_dump()})
    return SpecOut(**saved)


@router.delete("/items/{item_id}/specs/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_spec(item_id: UUID, name: str, ctx: Ctx = Depends(manage_items)) -> None:
    removed = await rows(
        ctx, "DELETE FROM erp.item_specification WHERE item_id = :i AND name = :n "
        "RETURNING spec_id", {"i": item_id, "n": name.strip()})
    if not removed:
        raise not_found("Specification")
    await audit(ctx, "item.spec.remove", "item", item_id, {"name": name})


# ------------------------------------------------------------------ customer part numbers
class PartIn(Strict):
    customer_id: UUID
    customer_part_no: str = Field(min_length=1, max_length=60)


class PartOut(BaseModel):
    xref_id: UUID
    customer_id: UUID
    customer_code: str
    customer_part_no: str


PART_SQL = ("SELECT x.xref_id, x.customer_id, c.code AS customer_code, x.customer_part_no "
            "FROM erp.customer_part_xref x JOIN erp.customer c ON c.customer_id = x.customer_id")


@router.get("/items/{item_id}/customer-parts", response_model=list[PartOut])
async def list_parts(item_id: UUID, ctx: Ctx = Depends(read_ctx)) -> list[PartOut]:
    await require_item(ctx, item_id)
    found = await rows(ctx, f"{PART_SQL} WHERE x.item_id = :id ORDER BY c.code, "
                       "x.customer_part_no", {"id": item_id})
    return [PartOut(**p) for p in found]


@router.post("/items/{item_id}/customer-parts", response_model=PartOut,
             status_code=status.HTTP_201_CREATED)
async def add_part(item_id: UUID, body: PartIn, ctx: Ctx = Depends(manage_parts)) -> PartOut:
    """Map a customer's own part number to our item (used to match OEM schedules)."""
    item = await require_item(ctx, item_id)
    if item["item_type"] not in ("FG", "WIP"):
        raise HTTPException(
            422, "Only finished or work-in-progress items have customer part numbers"
        )
    customer = await one(ctx, "SELECT active FROM erp.customer WHERE customer_id = :id",
                         {"id": body.customer_id})
    if customer is None:
        raise HTTPException(422, "Customer does not exist")
    with db_errors("This customer part number"):
        created = await one(
            ctx,
            "INSERT INTO erp.customer_part_xref(tenant_id, customer_id, item_id, "
            "customer_part_no) VALUES (erp.current_tenant_id(), :c, :i, :p) RETURNING xref_id",
            {"c": body.customer_id, "i": item_id, "p": body.customer_part_no.strip()},
        )
    assert created is not None
    await audit(ctx, "item.customer_part.add", "item", item_id, body.model_dump())
    result = await one(ctx, f"{PART_SQL} WHERE x.xref_id = :id", {"id": created["xref_id"]})
    assert result is not None
    return PartOut(**result)


@router.delete("/customer-parts/{xref_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_part(xref_id: UUID, ctx: Ctx = Depends(manage_parts)) -> None:
    removed = await rows(ctx, "DELETE FROM erp.customer_part_xref WHERE xref_id = :id "
                         "RETURNING item_id", {"id": xref_id})
    if not removed:
        raise not_found("Customer part number")
    await audit(ctx, "item.customer_part.remove", "item", removed[0]["item_id"],
                {"xref_id": xref_id})
