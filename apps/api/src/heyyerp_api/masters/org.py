"""Organization master data: plants (units), departments and stores.

Records are never hard-deleted (other modules reference them); they are deactivated.
Writes need org.manage (ADMIN / MD); every signed-in V-Rub member can read.
"""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text

from heyyerp_api.masters.common import (
    Ctx,
    Page,
    Paging,
    audit,
    db_errors,
    not_found,
    read_ctx,
    requires,
)
from heyyerp_api.masters.validation import clean_code, clean_gstin, reconcile_state

router = APIRouter(prefix="/org", tags=["organization"])
manage_org = requires("org.manage")
Name = Field(min_length=1, max_length=120)


class Strict(BaseModel):
    # extra="forbid" also rejects a client-supplied tenant_id.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


async def rows(ctx: Ctx, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(r) for r in (await ctx.session.execute(text(sql), params or {})).mappings().all()]


async def one(ctx: Ctx, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    found = await rows(ctx, sql, params)
    return found[0] if found else None


async def count(ctx: Ctx, sql: str, params: dict[str, Any] | None = None) -> int:
    return int((await ctx.session.execute(text(sql), params or {})).scalar_one())


# ---------------------------------------------------------------- plants (units)
class UnitIn(Strict):
    code: str
    name: str = Name
    gstin: str | None = None
    state_code: str | None = None

    _code = field_validator("code")(clean_code)
    _gstin = field_validator("gstin")(clean_gstin)

    @model_validator(mode="after")
    def _state(self) -> "UnitIn":
        self.state_code = reconcile_state(self.gstin, self.state_code)
        return self


class UnitPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    gstin: str | None = None
    state_code: str | None = None
    active: bool | None = None

    _gstin = field_validator("gstin")(clean_gstin)


class UnitOut(BaseModel):
    unit_id: UUID
    code: str
    name: str
    gstin: str | None
    state_code: str | None
    active: bool


UNIT_COLS = "unit_id, code, name, gstin, state_code, active"


@router.get("/units", response_model=Page[UnitOut])
async def list_units(
    active: bool | None = None, page: Paging = Depends(), ctx: Ctx = Depends(read_ctx)
) -> Page[UnitOut]:
    where = "WHERE active = :active" if active is not None else ""
    params: dict[str, Any] = {"active": active, "limit": page.limit, "offset": page.offset}
    items = await rows(
        ctx,
        f"SELECT {UNIT_COLS} FROM erp.unit {where} ORDER BY code LIMIT :limit OFFSET :offset",
        params,
    )
    total = await count(ctx, f"SELECT count(*) FROM erp.unit {where}", params)
    return Page(items=[UnitOut(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/units/{unit_id}", response_model=UnitOut)
async def get_unit(unit_id: UUID, ctx: Ctx = Depends(read_ctx)) -> UnitOut:
    row = await one(ctx, f"SELECT {UNIT_COLS} FROM erp.unit WHERE unit_id = :id", {"id": unit_id})
    if row is None:
        raise not_found("Plant")
    return UnitOut(**row)


@router.post("/units", response_model=UnitOut, status_code=status.HTTP_201_CREATED)
async def create_unit(body: UnitIn, ctx: Ctx = Depends(manage_org)) -> UnitOut:
    with db_errors("Plant code or GSTIN"):
        row = await one(
            ctx,
            "INSERT INTO erp.unit(tenant_id, code, name, gstin, state_code) VALUES "
            f"(erp.current_tenant_id(), :code, :name, :gstin, :state_code) RETURNING {UNIT_COLS}",
            body.model_dump(),
        )
    assert row is not None
    await audit(ctx, "unit.create", "unit", row["unit_id"], body.model_dump())
    return UnitOut(**row)


@router.patch("/units/{unit_id}", response_model=UnitOut)
async def update_unit(
    unit_id: UUID, body: UnitPatch, ctx: Ctx = Depends(manage_org)
) -> UnitOut:
    current = await one(
        ctx, f"SELECT {UNIT_COLS} FROM erp.unit WHERE unit_id = :id FOR UPDATE", {"id": unit_id}
    )
    if current is None:
        raise not_found("Plant")
    changes = body.model_dump(exclude_unset=True)
    merged = {**current, **changes}
    try:
        merged["state_code"] = reconcile_state(
            merged["gstin"], None if "gstin" in changes and "state_code" not in changes
            else merged["state_code"],
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if merged["name"] is None or merged["active"] is None:
        raise HTTPException(422, "name and active cannot be null")
    if current["active"] and not merged["active"]:
        blockers = await count(
            ctx,
            "SELECT (SELECT count(*) FROM erp.store WHERE unit_id = :id AND active) + "
            "(SELECT count(*) FROM erp.department WHERE unit_id = :id AND active)",
            {"id": unit_id},
        )
        if blockers:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Deactivate this plant's stores and departments first"
            )
    with db_errors("Plant GSTIN"):
        row = await one(
            ctx,
            "UPDATE erp.unit SET name = :name, gstin = :gstin, state_code = :state_code, "
            f"active = :active WHERE unit_id = :id RETURNING {UNIT_COLS}",
            {**merged, "id": unit_id},
        )
    assert row is not None
    await audit(ctx, "unit.update", "unit", unit_id, {"changes": changes})
    return UnitOut(**row)


# ---------------------------------------------------------------- departments
class DepartmentIn(Strict):
    code: str
    name: str = Name
    unit_id: UUID | None = None
    parent_department_id: UUID | None = None

    _code = field_validator("code")(clean_code)


class DepartmentPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    unit_id: UUID | None = None
    parent_department_id: UUID | None = None
    active: bool | None = None


class DepartmentOut(BaseModel):
    department_id: UUID
    code: str
    name: str
    unit_id: UUID | None
    parent_department_id: UUID | None
    active: bool


DEPT_COLS = "department_id, code, name, unit_id, parent_department_id, active"


async def require_active_unit(ctx: Ctx, unit_id: UUID | None) -> None:
    if unit_id is None:
        return
    unit = await one(ctx, "SELECT active FROM erp.unit WHERE unit_id = :id", {"id": unit_id})
    if unit is None:
        raise HTTPException(422, "Plant does not exist")
    if not unit["active"]:
        raise HTTPException(422, "Plant is inactive")


async def require_active_parent(ctx: Ctx, parent_id: UUID | None, own_id: UUID | None) -> None:
    if parent_id is None:
        return
    parent = await one(
        ctx, "SELECT active FROM erp.department WHERE department_id = :id", {"id": parent_id}
    )
    if parent is None:
        raise HTTPException(
            422, "Parent department does not exist"
        )
    if not parent["active"]:
        raise HTTPException(422, "Parent department is inactive")
    if own_id is not None:
        loop = await one(
            ctx,
            "WITH RECURSIVE up AS (SELECT department_id, parent_department_id FROM erp.department "
            "WHERE department_id = :parent UNION ALL SELECT d.department_id, "
            "d.parent_department_id FROM erp.department d JOIN up ON d.department_id = "
            "up.parent_department_id) SELECT 1 AS hit FROM up WHERE department_id = :own",
            {"parent": parent_id, "own": own_id},
        )
        if loop:
            raise HTTPException(
                422, "A department cannot be its own ancestor"
            )


@router.get("/departments", response_model=Page[DepartmentOut])
async def list_departments(
    active: bool | None = None,
    unit_id: UUID | None = None,
    page: Paging = Depends(),
    ctx: Ctx = Depends(read_ctx),
) -> Page[DepartmentOut]:
    conditions = []
    if active is not None:
        conditions.append("active = :active")
    if unit_id is not None:
        conditions.append("unit_id = :unit_id")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params: dict[str, Any] = {
        "active": active, "unit_id": unit_id, "limit": page.limit, "offset": page.offset,
    }
    sql_params = {k: v for k, v in params.items() if k in {"limit", "offset"} or f":{k}" in where}
    items = await rows(
        ctx,
        f"SELECT {DEPT_COLS} FROM erp.department {where} ORDER BY code LIMIT :limit OFFSET :offset",
        sql_params,
    )
    total = await count(
        ctx, f"SELECT count(*) FROM erp.department {where}",
        {k: v for k, v in sql_params.items() if k not in {"limit", "offset"}},
    )
    return Page(items=[DepartmentOut(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/departments/{department_id}", response_model=DepartmentOut)
async def get_department(department_id: UUID, ctx: Ctx = Depends(read_ctx)) -> DepartmentOut:
    row = await one(
        ctx, f"SELECT {DEPT_COLS} FROM erp.department WHERE department_id = :id",
        {"id": department_id},
    )
    if row is None:
        raise not_found("Department")
    return DepartmentOut(**row)


@router.post("/departments", response_model=DepartmentOut, status_code=status.HTTP_201_CREATED)
async def create_department(
    body: DepartmentIn, ctx: Ctx = Depends(manage_org)
) -> DepartmentOut:
    await require_active_unit(ctx, body.unit_id)
    await require_active_parent(ctx, body.parent_department_id, None)
    with db_errors("Department code"):
        row = await one(
            ctx,
            "INSERT INTO erp.department(tenant_id, unit_id, parent_department_id, code, name) "
            "VALUES (erp.current_tenant_id(), :unit_id, :parent_department_id, :code, :name) "
            f"RETURNING {DEPT_COLS}",
            body.model_dump(),
        )
    assert row is not None
    await audit(ctx, "department.create", "department", row["department_id"], body.model_dump())
    return DepartmentOut(**row)


@router.patch("/departments/{department_id}", response_model=DepartmentOut)
async def update_department(
    department_id: UUID, body: DepartmentPatch, ctx: Ctx = Depends(manage_org)
) -> DepartmentOut:
    current = await one(
        ctx, f"SELECT {DEPT_COLS} FROM erp.department WHERE department_id = :id FOR UPDATE",
        {"id": department_id},
    )
    if current is None:
        raise not_found("Department")
    changes = body.model_dump(exclude_unset=True)
    merged = {**current, **changes}
    if merged["name"] is None or merged["active"] is None:
        raise HTTPException(422, "name and active cannot be null")
    if "unit_id" in changes:
        await require_active_unit(ctx, merged["unit_id"])
    if "parent_department_id" in changes:
        await require_active_parent(ctx, merged["parent_department_id"], department_id)
    if current["active"] and not merged["active"]:
        children = await count(
            ctx,
            "SELECT count(*) FROM erp.department WHERE parent_department_id = :id AND active",
            {"id": department_id},
        )
        if children:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Deactivate the sub-departments first"
            )
    with db_errors("Department"):
        row = await one(
            ctx,
            "UPDATE erp.department SET name = :name, unit_id = :unit_id, "
            "parent_department_id = :parent_department_id, active = :active "
            f"WHERE department_id = :id RETURNING {DEPT_COLS}",
            {**merged, "id": department_id},
        )
    assert row is not None
    await audit(ctx, "department.update", "department", department_id, {"changes": changes})
    return DepartmentOut(**row)


# ---------------------------------------------------------------- stores
StoreType = Literal["RAW_MATERIAL", "WIP", "FINISHED_GOODS", "SPARES", "OTHER"]


class StoreIn(Strict):
    unit_id: UUID
    code: str
    name: str = Name
    store_type: StoreType

    _code = field_validator("code")(clean_code)


class StorePatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    store_type: StoreType | None = None
    active: bool | None = None


class StoreOut(BaseModel):
    store_id: UUID
    unit_id: UUID
    code: str
    name: str
    store_type: StoreType
    active: bool


STORE_COLS = "store_id, unit_id, code, name, store_type, active"


@router.get("/stores", response_model=Page[StoreOut])
async def list_stores(
    active: bool | None = None,
    unit_id: UUID | None = None,
    store_type: StoreType | None = Query(None),
    page: Paging = Depends(),
    ctx: Ctx = Depends(read_ctx),
) -> Page[StoreOut]:
    filters = {"active": active, "unit_id": unit_id, "store_type": store_type}
    used = {k: v for k, v in filters.items() if v is not None}
    where = ("WHERE " + " AND ".join(f"{k} = :{k}" for k in used)) if used else ""
    items = await rows(
        ctx,
        f"SELECT {STORE_COLS} FROM erp.store {where} ORDER BY code LIMIT :limit OFFSET :offset",
        {**used, "limit": page.limit, "offset": page.offset},
    )
    total = await count(ctx, f"SELECT count(*) FROM erp.store {where}", used)
    return Page(items=[StoreOut(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/stores/{store_id}", response_model=StoreOut)
async def get_store(store_id: UUID, ctx: Ctx = Depends(read_ctx)) -> StoreOut:
    row = await one(
        ctx, f"SELECT {STORE_COLS} FROM erp.store WHERE store_id = :id", {"id": store_id}
    )
    if row is None:
        raise not_found("Store")
    return StoreOut(**row)


@router.post("/stores", response_model=StoreOut, status_code=status.HTTP_201_CREATED)
async def create_store(body: StoreIn, ctx: Ctx = Depends(manage_org)) -> StoreOut:
    await require_active_unit(ctx, body.unit_id)
    with db_errors("Store code in this plant"):
        row = await one(
            ctx,
            "INSERT INTO erp.store(tenant_id, unit_id, code, name, store_type) VALUES "
            "(erp.current_tenant_id(), :unit_id, :code, :name, :store_type) "
            f"RETURNING {STORE_COLS}",
            body.model_dump(),
        )
    assert row is not None
    await audit(ctx, "store.create", "store", row["store_id"], body.model_dump())
    return StoreOut(**row)


@router.patch("/stores/{store_id}", response_model=StoreOut)
async def update_store(
    store_id: UUID, body: StorePatch, ctx: Ctx = Depends(manage_org)
) -> StoreOut:
    current = await one(
        ctx, f"SELECT {STORE_COLS} FROM erp.store WHERE store_id = :id FOR UPDATE",
        {"id": store_id},
    )
    if current is None:
        raise not_found("Store")
    changes = body.model_dump(exclude_unset=True)
    merged = {**current, **changes}
    if any(merged[k] is None for k in ("name", "store_type", "active")):
        raise HTTPException(422, "Fields cannot be null")
    # ponytail: no stock check yet; Phase 6 must block deactivating a store that holds stock.
    row = await one(
        ctx,
        "UPDATE erp.store SET name = :name, store_type = :store_type, active = :active "
        f"WHERE store_id = :id RETURNING {STORE_COLS}",
        {**merged, "id": store_id},
    )
    assert row is not None
    await audit(ctx, "store.update", "store", store_id, {"changes": changes})
    return StoreOut(**row)
