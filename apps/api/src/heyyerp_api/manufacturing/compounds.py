"""Compound master: recipe in phr, maturation window and hardness spec (BRD FR-MFG-01, 6.3).

phr is parts per hundred of rubber; a batch of N kilos weighs each ingredient in proportion to its
phr. A compound may name the WIP item a BOM line uses to mean "draw this compound". The recipe can
only be edited while no batch has been made from the compound: the weighing records point at it.
Needs mfg.plan to change; reading needs the manufacturing module.
"""

from decimal import Decimal
from typing import Any, Literal, Self
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

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
    requires,
    requires_module,
    rows,
)

router = APIRouter(prefix="/manufacturing/compounds", tags=["manufacturing"])
manage = requires("mfg.plan")
read = requires_module("manufacturing")
Hours = Field(ge=0, le=100_000, max_digits=8, decimal_places=2)
Shore = Field(default=None, ge=0, le=100, max_digits=8, decimal_places=2)


class RecipeIn(Strict):
    item_id: UUID
    phr: Decimal = Field(gt=0, le=Decimal(10_000), max_digits=18, decimal_places=6)


class CompoundBase(Strict):
    name: str = Field(min_length=2, max_length=200)
    maturation_min_hours: Decimal = Hours
    maximum_age_hours: Decimal = Hours
    hardness_min: Decimal | None = Shore
    hardness_max: Decimal | None = Shore
    output_item_id: UUID | None = None

    @model_validator(mode="after")
    def _ranges(self) -> Self:
        if self.maximum_age_hours <= self.maturation_min_hours:
            raise ValueError("The maximum age must be longer than the minimum maturation")
        if (self.hardness_min is not None and self.hardness_max is not None
                and self.hardness_max < self.hardness_min):
            raise ValueError("The hardness maximum is below the minimum")
        return self


class CompoundIn(CompoundBase):
    code: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    lane: Literal["RUBBER", "EXTRUSION"]
    recipe: list[RecipeIn] = Field(default_factory=list, max_length=60)


class CompoundPatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    maturation_min_hours: Decimal | None = Field(default=None, ge=0, le=100_000, max_digits=8,
                                                 decimal_places=2)
    maximum_age_hours: Decimal | None = Field(default=None, ge=0, le=100_000, max_digits=8,
                                              decimal_places=2)
    hardness_min: Decimal | None = Shore
    hardness_max: Decimal | None = Shore
    output_item_id: UUID | None = None
    active: bool | None = None
    recipe: list[RecipeIn] | None = Field(default=None, max_length=60)


class RecipeLine(BaseModel):
    recipe_line_id: UUID
    line_no: int
    item_id: UUID
    item_code: str
    item_name: str
    phr: Decimal


class CompoundOut(BaseModel):
    compound_id: UUID
    code: str
    name: str
    lane: str
    maturation_min_hours: Decimal
    maximum_age_hours: Decimal
    hardness_min: Decimal | None
    hardness_max: Decimal | None
    output_item_id: UUID | None
    active: bool
    recipe: list[RecipeLine]


COLS = ("compound_id, code, name, lane, maturation_min_hours, maximum_age_hours, hardness_min, "
        "hardness_max, output_item_id, active")


async def load(ctx: Ctx, compound_id: UUID) -> CompoundOut | None:
    head = await one(ctx, f"SELECT {COLS} FROM erp.compound WHERE compound_id = :id",
                     {"id": compound_id})
    if head is None:
        return None
    recipe = await rows(
        ctx, "SELECT r.recipe_line_id, r.line_no, r.item_id, i.code AS item_code, i.name AS "
        "item_name, r.phr FROM erp.compound_recipe_line r JOIN erp.item i ON i.item_id = "
        "r.item_id WHERE r.compound_id = :id ORDER BY r.line_no", {"id": compound_id})
    return CompoundOut(**head, recipe=[RecipeLine(**r) for r in recipe])


async def check_items(ctx: Ctx, recipe: list[RecipeIn], output_item_id: UUID | None) -> None:
    ids = [r.item_id for r in recipe]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Each ingredient may appear only once in a recipe")
    found = {r["item_id"]: r for r in await rows(
        ctx, "SELECT item_id, item_type, active FROM erp.item WHERE item_id = ANY(CAST(:ids AS "
        "uuid[]))", {"ids": ids})}
    for item_id in ids:
        item = found.get(item_id)
        if item is None or not item["active"] or item["item_type"] not in ("RM", "CONSUMABLE"):
            raise HTTPException(
                422, "Every ingredient must be an active raw material or consumable")
    if output_item_id is not None:
        out = await one(ctx, "SELECT item_type, active FROM erp.item WHERE item_id = :id",
                        {"id": output_item_id})
        if out is None or not out["active"] or out["item_type"] != "WIP":
            raise HTTPException(422, "The output item must be an active WIP item")


async def write_recipe(ctx: Ctx, compound_id: UUID, recipe: list[RecipeIn]) -> None:
    await rows(ctx, "DELETE FROM erp.compound_recipe_line WHERE compound_id = :id "
               "RETURNING recipe_line_id", {"id": compound_id})
    for number, line in enumerate(recipe, start=1):
        await rows(
            ctx, "INSERT INTO erp.compound_recipe_line(tenant_id, compound_id, item_id, line_no, "
            "phr) VALUES (erp.current_tenant_id(), :c, :i, :n, :p) RETURNING recipe_line_id",
            {"c": compound_id, "i": line.item_id, "n": number, "p": line.phr})


@router.get("", response_model=Page[CompoundOut])
async def list_compounds(
    lane: str | None = Query(None), active: bool | None = Query(None),
    page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[CompoundOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    if lane:
        conditions.append("lane = :lane")
        params["lane"] = lane.upper()
    if active is not None:
        conditions.append("active = :active")
        params["active"] = active
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    heads = await rows(ctx, f"SELECT compound_id FROM erp.compound{where} ORDER BY code "
                       "LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    items = [c for h in heads if (c := await load(ctx, h["compound_id"])) is not None]
    total = await count(ctx, f"SELECT count(*) FROM erp.compound{where}", params)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/{compound_id}", response_model=CompoundOut)
async def get_compound(compound_id: UUID, ctx: Ctx = Depends(read)) -> CompoundOut:
    found = await load(ctx, compound_id)
    if found is None:
        raise not_found("Compound")
    return found


@router.post("", response_model=CompoundOut, status_code=status.HTTP_201_CREATED)
async def create_compound(body: CompoundIn, ctx: Ctx = Depends(manage)) -> CompoundOut:
    await check_items(ctx, body.recipe, body.output_item_id)
    with db_errors("A compound with this code already exists, or the output item is already "
                   "used by another compound"):
        created = await one(
            ctx,
            "INSERT INTO erp.compound(tenant_id, code, name, lane, maturation_min_hours, "
            "maximum_age_hours, hardness_min, hardness_max, output_item_id) VALUES "
            "(erp.current_tenant_id(), :code, :name, :lane, :mn, :mx, :hmin, :hmax, :out) "
            "RETURNING compound_id",
            {"code": body.code.upper(), "name": body.name, "lane": body.lane,
             "mn": body.maturation_min_hours, "mx": body.maximum_age_hours,
             "hmin": body.hardness_min, "hmax": body.hardness_max, "out": body.output_item_id})
    assert created is not None
    await write_recipe(ctx, created["compound_id"], body.recipe)
    await audit(ctx, "compound.create", "compound", created["compound_id"],
                {"code": body.code.upper(), "ingredients": len(body.recipe)})
    result = await load(ctx, created["compound_id"])
    assert result is not None
    return result


@router.patch("/{compound_id}", response_model=CompoundOut)
async def update_compound(
    compound_id: UUID, body: CompoundPatch, ctx: Ctx = Depends(manage)
) -> CompoundOut:
    current = await one(ctx, f"SELECT {COLS} FROM erp.compound WHERE compound_id = :id FOR UPDATE",
                        {"id": compound_id})
    if current is None:
        raise not_found("Compound")
    changes = body.model_dump(exclude_unset=True)
    recipe = body.recipe
    changes.pop("recipe", None)
    if any(changes.get(k, 0) is None for k in ("name", "maturation_min_hours", "maximum_age_hours",
                                                "active")):
        raise HTTPException(422, "Fields cannot be null")
    merged: dict[str, Any] = {**current, **changes}
    try:
        CompoundBase(**{k: merged[k] for k in CompoundBase.model_fields})  # re-check ranges
    except ValueError as exc:
        raise HTTPException(422, "The maturation hours or hardness range are not valid") from exc
    if recipe is not None:
        if await count(ctx, "SELECT count(*) FROM erp.compound_batch WHERE compound_id = :id",
                       {"id": compound_id}):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Batches were already made from this recipe; create a new compound code instead")
    await check_items(ctx, recipe or [], merged["output_item_id"])
    with db_errors("The output item is already used by another compound"):
        await rows(
            ctx, "UPDATE erp.compound SET name = :name, maturation_min_hours = "
            ":maturation_min_hours, maximum_age_hours = :maximum_age_hours, hardness_min = "
            ":hardness_min, hardness_max = :hardness_max, output_item_id = :output_item_id, "
            "active = :active WHERE "
            "compound_id = :id RETURNING compound_id", {**merged, "id": compound_id})
    if recipe is not None:
        await write_recipe(ctx, compound_id, recipe)
    await audit(ctx, "compound.update", "compound", compound_id,
                {"changes": changes, "recipe_replaced": recipe is not None})
    result = await load(ctx, compound_id)
    assert result is not None
    return result
