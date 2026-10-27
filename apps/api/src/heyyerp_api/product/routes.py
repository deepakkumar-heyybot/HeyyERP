"""Production routes: ordered stages with QA gates (BRD section 6.2), versioned.

A route version never changes once created. To change a route, revise it: the new version becomes
the active one and the old version is kept, so production lots already started keep the stages
they began with. Items point at a route by code and always use its active version.
Writes need route.manage; everyone signed in can read.
"""

from typing import Any, Self
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator

from heyyerp_api.masters.common import (
    Ctx,
    Strict,
    audit,
    db_errors,
    not_found,
    one,
    read_ctx,
    requires,
    rows,
)
from heyyerp_api.masters.validation import clean_code

router = APIRouter(prefix="/product/routes", tags=["product engineering"])
manage = requires("route.manage")


class StageIn(Strict):
    stage_code: str
    stage_name: str = Field(min_length=1, max_length=120)
    is_qa_gate: bool = False
    is_end: bool = False

    _code = field_validator("stage_code")(clean_code)


def check_stages(stages: list[StageIn]) -> None:
    codes = [s.stage_code for s in stages]
    if len(set(codes)) != len(codes):
        raise ValueError("Stage codes must be unique within a route")
    ends = [i for i, s in enumerate(stages) if s.is_end]
    if ends != [len(stages) - 1]:
        raise ValueError("Exactly one stage must be the end stage, and it must be the last")


class RouteIn(Strict):
    code: str
    name: str = Field(min_length=1, max_length=120)
    stages: list[StageIn] = Field(min_length=1, max_length=30)

    _code = field_validator("code")(clean_code)

    @model_validator(mode="after")
    def _stages(self) -> Self:
        check_stages(self.stages)
        return self


class RevisionIn(Strict):
    name: str = Field(min_length=1, max_length=120)
    stages: list[StageIn] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def _stages(self) -> Self:
        check_stages(self.stages)
        return self


class StageOut(BaseModel):
    sequence_no: int
    stage_code: str
    stage_name: str
    is_qa_gate: bool
    is_end: bool


class RouteOut(BaseModel):
    route_id: UUID
    code: str
    name: str
    version: int
    active: bool
    stages: list[StageOut]


async def load(ctx: Ctx, route_id: UUID) -> RouteOut | None:
    route = await one(ctx, "SELECT route_id, code, name, version, active FROM erp.route "
                      "WHERE route_id = :id", {"id": route_id})
    if route is None:
        return None
    stages = await rows(
        ctx, "SELECT sequence_no, stage_code, stage_name, is_qa_gate, is_end "
        "FROM erp.route_stage WHERE route_id = :id ORDER BY sequence_no", {"id": route_id})
    return RouteOut(**route, stages=[StageOut(**s) for s in stages])


async def write_stages(ctx: Ctx, route_id: UUID, stages: list[StageIn]) -> None:
    for sequence, stage in enumerate(stages, start=1):
        await one(
            ctx,
            "INSERT INTO erp.route_stage(tenant_id, route_id, sequence_no, stage_code, "
            "stage_name, is_qa_gate, is_end) VALUES (erp.current_tenant_id(), :route, :seq, "
            ":code, :name, :qa, :end) RETURNING route_stage_id",
            {"route": route_id, "seq": sequence, "code": stage.stage_code,
             "name": stage.stage_name, "qa": stage.is_qa_gate, "end": stage.is_end},
        )


@router.get("", response_model=list[RouteOut])
async def list_routes(
    active: bool | None = True, ctx: Ctx = Depends(read_ctx)
) -> list[RouteOut]:
    """Active versions by default; pass active=false for superseded ones, or omit for all."""
    where = " WHERE active = :active" if active is not None else ""
    found = await rows(ctx, f"SELECT route_id FROM erp.route{where} ORDER BY code, version",
                       {"active": active} if active is not None else {})
    return [r for r in [await load(ctx, f["route_id"]) for f in found] if r is not None]


@router.get("/by-code/{code}", response_model=RouteOut)
async def active_route(code: str, ctx: Ctx = Depends(read_ctx)) -> RouteOut:
    found = await one(ctx, "SELECT route_id FROM erp.route WHERE code = :c AND active "
                      "ORDER BY version DESC LIMIT 1", {"c": code.upper()})
    route = await load(ctx, found["route_id"]) if found else None
    if route is None:
        raise not_found("Active route")
    return route


@router.get("/{route_id}", response_model=RouteOut)
async def get_route(route_id: UUID, ctx: Ctx = Depends(read_ctx)) -> RouteOut:
    route = await load(ctx, route_id)
    if route is None:
        raise not_found("Route")
    return route


@router.post("", response_model=RouteOut, status_code=status.HTTP_201_CREATED)
async def create_route(body: RouteIn, ctx: Ctx = Depends(manage)) -> RouteOut:
    with db_errors("Route code"):
        created = await one(
            ctx,
            "INSERT INTO erp.route(tenant_id, code, name, version) VALUES "
            "(erp.current_tenant_id(), :code, :name, 1) RETURNING route_id",
            {"code": body.code, "name": body.name},
        )
    assert created is not None
    await write_stages(ctx, created["route_id"], body.stages)
    await audit(ctx, "route.create", "route", created["route_id"], body.model_dump())
    route = await load(ctx, created["route_id"])
    assert route is not None
    return route


@router.post("/{route_id}/revise", response_model=RouteOut, status_code=status.HTTP_201_CREATED)
async def revise_route(route_id: UUID, body: RevisionIn, ctx: Ctx = Depends(manage)) -> RouteOut:
    """Create the next version and make it the active one; earlier versions are kept."""
    current = await one(ctx, "SELECT code FROM erp.route WHERE route_id = :id FOR UPDATE",
                        {"id": route_id})
    if current is None:
        raise not_found("Route")
    latest = await one(ctx, "SELECT max(version) AS v FROM erp.route WHERE code = :c",
                       {"c": current["code"]})
    assert latest is not None
    await rows(ctx, "UPDATE erp.route SET active = false WHERE code = :c RETURNING route_id",
               {"c": current["code"]})
    created = await one(
        ctx,
        "INSERT INTO erp.route(tenant_id, code, name, version) VALUES "
        "(erp.current_tenant_id(), :code, :name, :version) RETURNING route_id",
        {"code": current["code"], "name": body.name, "version": latest["v"] + 1},
    )
    assert created is not None
    await write_stages(ctx, created["route_id"], body.stages)
    await audit(ctx, "route.revise", "route", created["route_id"],
                {"from_route_id": route_id, "version": latest["v"] + 1, **body.model_dump()})
    route = await load(ctx, created["route_id"])
    assert route is not None
    return route


@router.post("/{route_id}/retire", response_model=RouteOut)
async def retire_route(route_id: UUID, ctx: Ctx = Depends(manage)) -> RouteOut:
    """Stop using a route version. Refused while items still follow it and nothing replaces it."""
    route = await one(ctx, "SELECT code, active FROM erp.route WHERE route_id = :id FOR UPDATE",
                      {"id": route_id})
    if route is None:
        raise not_found("Route")
    if not route["active"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Route version is already inactive")
    others: dict[str, Any] | None = await one(
        ctx, "SELECT 1 AS x FROM erp.route WHERE code = :c AND active AND route_id <> :id",
        {"c": route["code"], "id": route_id})
    users = await one(ctx, "SELECT count(*) AS n FROM erp.item WHERE route_code = :c AND active",
                      {"c": route["code"]})
    if others is None and users and users["n"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{users['n']} active item(s) still follow this route"
        )
    await rows(ctx, "UPDATE erp.route SET active = false WHERE route_id = :id RETURNING route_id",
               {"id": route_id})
    await audit(ctx, "route.retire", "route", route_id, {})
    result = await load(ctx, route_id)
    assert result is not None
    return result
