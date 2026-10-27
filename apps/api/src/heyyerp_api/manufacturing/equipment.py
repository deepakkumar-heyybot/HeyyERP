"""Machine and mould masters (BRD FR-MNT-01; shift entries and work orders refer to them).

Maintenance schedules and breakdowns come with Phase 11. Needs mfg.plan to change; reading needs
the manufacturing module. Codes are unique per plant; records are deactivated, never deleted.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

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
    require_in_scope,
    requires,
    requires_module,
    rows,
    scope_clause,
)

router = APIRouter(prefix="/manufacturing", tags=["manufacturing"])
manage = requires("mfg.plan")
read = requires_module("manufacturing")
CODE = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class MachineIn(Strict):
    unit_id: UUID
    code: str = CODE
    name: str = Field(min_length=2, max_length=200)
    machine_type: str | None = Field(default=None, max_length=60)


class MachinePatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    machine_type: str | None = Field(default=None, max_length=60)
    active: bool | None = None


class MachineOut(BaseModel):
    machine_id: UUID
    unit_id: UUID
    code: str
    name: str
    machine_type: str | None
    active: bool


class MouldIn(Strict):
    unit_id: UUID
    part_item_id: UUID
    code: str = CODE
    shots_limit: int | None = Field(default=None, gt=0, le=1_000_000_000)


class MouldPatch(Strict):
    shots_limit: int | None = Field(default=None, gt=0, le=1_000_000_000)
    active: bool | None = None


class MouldOut(BaseModel):
    mould_id: UUID
    unit_id: UUID
    part_item_id: UUID
    part_code: str
    code: str
    shots_limit: int | None
    active: bool


async def listing(
    ctx: Ctx, table: str, cols: str, unit_id: UUID | None, active: bool | None, page: Paging,
    alias: str = "t",
) -> tuple[list[dict[str, Any]], int]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, f"{alias}.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    if unit_id is not None:
        conditions.append(f"{alias}.unit_id = :unit")
        params["unit"] = unit_id
    if active is not None:
        conditions.append(f"{alias}.active = :active")
        params["active"] = active
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"SELECT {cols} FROM {table}{where} ORDER BY {alias}.code LIMIT :limit "
                       "OFFSET :offset", {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM {table}{where}", params)
    return items, total


# ------------------------------------------------------------------ machines
MACHINE_COLS = "t.machine_id, t.unit_id, t.code, t.name, t.machine_type, t.active"


@router.get("/machines", response_model=Page[MachineOut])
async def list_machines(
    unit_id: UUID | None = None, active: bool | None = Query(None),
    page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[MachineOut]:
    items, total = await listing(ctx, "erp.machine t", MACHINE_COLS, unit_id, active, page)
    return Page(items=[MachineOut(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/machines/{machine_id}", response_model=MachineOut)
async def get_machine(machine_id: UUID, ctx: Ctx = Depends(read)) -> MachineOut:
    row = await one(ctx, f"SELECT {MACHINE_COLS} FROM erp.machine t WHERE t.machine_id = :id",
                    {"id": machine_id})
    if row is None or not ctx.principal.may_see_unit(row["unit_id"]):
        raise not_found("Machine")
    return MachineOut(**row)


async def active_unit(ctx: Ctx, unit_id: UUID) -> None:
    require_in_scope(ctx, unit_id)
    unit = await one(ctx, "SELECT active FROM erp.unit WHERE unit_id = :id", {"id": unit_id})
    if unit is None or not unit["active"]:
        raise HTTPException(422, "The plant does not exist or is inactive")


@router.post("/machines", response_model=MachineOut, status_code=status.HTTP_201_CREATED)
async def create_machine(body: MachineIn, ctx: Ctx = Depends(manage)) -> MachineOut:
    await active_unit(ctx, body.unit_id)
    with db_errors("This plant already has a machine with this code"):
        row = await one(
            ctx, "INSERT INTO erp.machine(tenant_id, unit_id, code, name, machine_type) VALUES "
            "(erp.current_tenant_id(), :u, :c, :n, :t) RETURNING machine_id, unit_id, code, "
            "name, machine_type, active",
            {"u": body.unit_id, "c": body.code.upper(), "n": body.name, "t": body.machine_type})
    assert row is not None
    await audit(ctx, "machine.create", "machine", row["machine_id"], body.model_dump())
    return MachineOut(**row)


@router.patch("/machines/{machine_id}", response_model=MachineOut)
async def update_machine(
    machine_id: UUID, body: MachinePatch, ctx: Ctx = Depends(manage)
) -> MachineOut:
    current = await get_machine(machine_id, ctx)
    changes = body.model_dump(exclude_unset=True)
    merged = {**current.model_dump(), **changes}
    if merged["name"] is None or merged["active"] is None:
        raise HTTPException(422, "Fields cannot be null")
    row = await one(
        ctx, "UPDATE erp.machine SET name = :name, machine_type = :machine_type, active = :active "
        "WHERE machine_id = :id RETURNING machine_id, unit_id, code, name, machine_type, active",
        {**merged, "id": machine_id})
    assert row is not None
    await audit(ctx, "machine.update", "machine", machine_id, {"changes": changes})
    return MachineOut(**row)


# ------------------------------------------------------------------ moulds
MOULD_COLS = ("t.mould_id, t.unit_id, t.part_item_id, i.code AS part_code, t.code, "
              "t.shots_limit, t.active")
MOULD_FROM = "erp.mould t JOIN erp.item i ON i.item_id = t.part_item_id"


@router.get("/moulds", response_model=Page[MouldOut])
async def list_moulds(
    unit_id: UUID | None = None, active: bool | None = Query(None),
    page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[MouldOut]:
    items, total = await listing(ctx, MOULD_FROM, MOULD_COLS, unit_id, active, page)
    return Page(items=[MouldOut(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/moulds/{mould_id}", response_model=MouldOut)
async def get_mould(mould_id: UUID, ctx: Ctx = Depends(read)) -> MouldOut:
    row = await one(ctx, f"SELECT {MOULD_COLS} FROM {MOULD_FROM} WHERE t.mould_id = :id",
                    {"id": mould_id})
    if row is None or not ctx.principal.may_see_unit(row["unit_id"]):
        raise not_found("Mould")
    return MouldOut(**row)


@router.post("/moulds", response_model=MouldOut, status_code=status.HTTP_201_CREATED)
async def create_mould(body: MouldIn, ctx: Ctx = Depends(manage)) -> MouldOut:
    await active_unit(ctx, body.unit_id)
    part = await one(ctx, "SELECT active FROM erp.item WHERE item_id = :id",
                     {"id": body.part_item_id})
    if part is None or not part["active"]:
        raise HTTPException(422, "The part does not exist or is inactive")
    with db_errors("This plant already has a mould with this code"):
        row = await one(
            ctx, "INSERT INTO erp.mould(tenant_id, unit_id, part_item_id, code, shots_limit) "
            "VALUES (erp.current_tenant_id(), :u, :p, :c, :s) RETURNING mould_id",
            {"u": body.unit_id, "p": body.part_item_id, "c": body.code.upper(),
             "s": body.shots_limit})
    assert row is not None
    await audit(ctx, "mould.create", "mould", row["mould_id"], body.model_dump())
    return await get_mould(row["mould_id"], ctx)


@router.patch("/moulds/{mould_id}", response_model=MouldOut)
async def update_mould(mould_id: UUID, body: MouldPatch, ctx: Ctx = Depends(manage)) -> MouldOut:
    current = await get_mould(mould_id, ctx)
    changes = body.model_dump(exclude_unset=True)
    merged = {**current.model_dump(), **changes}
    if merged["active"] is None:
        raise HTTPException(422, "Fields cannot be null")
    await rows(ctx, "UPDATE erp.mould SET shots_limit = :shots_limit, active = :active WHERE "
               "mould_id = :id RETURNING mould_id", {**merged, "id": mould_id})
    await audit(ctx, "mould.update", "mould", mould_id, {"changes": changes})
    return await get_mould(mould_id, ctx)
