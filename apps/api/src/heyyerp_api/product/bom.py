"""Bills of material with revisions, engineering changes and effective dates (BRD FR-MFG-10).

A BOM version moves DRAFT -> PENDING_APPROVAL -> APPROVED (or REJECTED). Only a DRAFT can be
edited. Submitting records an engineering change (why, who, from which version, effective date)
and starts an approval (document type `bom`: QA, then the Plant Head). When approved the version
takes over on its effective date and the one it replaces is marked SUPERSEDED with a valid_to the
day before, so for any date exactly one version is effective. Nothing is ever overwritten.
Writes need bom.manage; everyone signed in can read.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Self
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
    read_ctx,
    requires,
    rows,
)
from heyyerp_api.workflow import engine
from heyyerp_api.workflow.registry import DocInfo, DocumentType, register

router = APIRouter(prefix="/product", tags=["product engineering"])
manage = requires("bom.manage")
MADE_TYPES = ("WIP", "FG")
COMPONENT_TYPES = ("RM", "WIP", "FG", "CONSUMABLE")  # not spares, capital items or other
LIVE = ("DRAFT", "PENDING_APPROVAL", "APPROVED")


class LineIn(Strict):
    component_item_id: UUID
    quantity_per: Decimal = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=6)
    scrap_factor: Decimal = Field(default=Decimal(0), ge=0, lt=1, max_digits=8, decimal_places=6)


class BomCreate(Strict):
    item_id: UUID
    lines: list[LineIn] = Field(default_factory=list, max_length=200)


class SubmitIn(Strict):
    reason: str = Field(min_length=5, max_length=500)
    effective_from: date


class LinesIn(Strict):
    lines: list[LineIn] = Field(max_length=200)

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        ids = [line.component_item_id for line in self.lines]
        if len(set(ids)) != len(ids):
            raise ValueError("Each component may appear only once")
        return self


class LineOut(BaseModel):
    line_no: int
    component_item_id: UUID
    component_code: str
    component_name: str
    uom_code: str
    quantity_per: Decimal
    scrap_factor: Decimal


class BomOut(BaseModel):
    bom_id: UUID
    item_id: UUID
    item_code: str
    item_name: str
    version: int
    status: str
    valid_from: date
    valid_to: date | None
    approval_id: UUID | None
    lines: list[LineOut]


class ChangeOut(BaseModel):
    version: int
    from_version: int | None
    bom_id: UUID
    status: str
    reason: str
    effective_from: date
    requested_by: str | None
    requested_at: Any
    approval_id: UUID | None
    approval_status: str | None


BOM_SQL = (
    "SELECT b.bom_id, b.item_id, i.code AS item_code, i.name AS item_name, b.version, b.status, "
    "b.valid_from, b.valid_to, e.approval_id FROM erp.bom b JOIN erp.item i ON i.item_id = "
    "b.item_id LEFT JOIN erp.engineering_change e ON e.bom_id = b.bom_id"
)


async def load(ctx: Ctx, bom_id: UUID) -> BomOut | None:
    bom = await one(ctx, f"{BOM_SQL} WHERE b.bom_id = :id", {"id": bom_id})
    if bom is None:
        return None
    lines = await rows(
        ctx,
        "SELECT l.line_no, l.component_item_id, c.code AS component_code, c.name AS "
        "component_name, u.code AS uom_code, l.quantity_per, l.scrap_factor "
        "FROM erp.bom_line l JOIN erp.item c ON c.item_id = l.component_item_id "
        "JOIN erp.uom u ON u.uom_id = c.uom_id WHERE l.bom_id = :id ORDER BY l.line_no",
        {"id": bom_id},
    )
    return BomOut(**bom, lines=[LineOut(**line) for line in lines])


async def require_bom(ctx: Ctx, bom_id: UUID, lock: bool = False) -> dict[str, Any]:
    bom = await one(
        ctx, "SELECT bom_id, item_id, version, status, valid_from, valid_to FROM erp.bom "
        f"WHERE bom_id = :id{' FOR UPDATE' if lock else ''}", {"id": bom_id},
    )
    if bom is None:
        raise not_found("Bill of material")
    return bom


async def would_cycle(ctx: Ctx, parent_item: UUID, components: set[UUID]) -> bool:
    """True if any component, directly or through its own BOMs, contains the parent item."""
    seen: set[UUID] = set()
    stack = list(components)
    while stack:
        item = stack.pop()
        if item == parent_item:
            return True
        if item in seen:
            continue
        seen.add(item)
        if len(seen) > 2000:
            raise HTTPException(422, "Bill of material is nested too deeply to check")
        children = await rows(
            ctx,
            "SELECT DISTINCT l.component_item_id FROM erp.bom_line l JOIN erp.bom b "
            "ON b.bom_id = l.bom_id WHERE b.item_id = :i AND b.status = ANY(CAST(:live AS text[]))",
            {"i": item, "live": list(LIVE)},
        )
        stack.extend(c["component_item_id"] for c in children)
    return False


async def write_lines(ctx: Ctx, bom: dict[str, Any], lines: list[LineIn]) -> None:
    """Replace a draft's lines after checking each component and that nothing becomes circular."""
    ids = {line.component_item_id for line in lines}
    if len(ids) != len(lines):
        raise HTTPException(422, "Each component may appear only once")
    if bom["item_id"] in ids:
        raise HTTPException(422, "An item cannot be a component of itself")
    if ids:
        found = {r["item_id"]: r for r in await rows(
            ctx, "SELECT item_id, code, item_type, active FROM erp.item "
            "WHERE item_id = ANY(CAST(:ids AS uuid[]))", {"ids": list(ids)})}
        for line in lines:
            item = found.get(line.component_item_id)
            if item is None:
                raise HTTPException(422, "A component item does not exist")
            if not item["active"]:
                raise HTTPException(422, f"Component {item['code']} is inactive")
            if item["item_type"] not in COMPONENT_TYPES:
                raise HTTPException(
                    422, f"Component {item['code']} is a {item['item_type']} item, which cannot "
                    "go into a bill of material")
        if await would_cycle(ctx, bom["item_id"], ids):
            raise HTTPException(422, "These components would make the bill of material circular")
    await rows(ctx, "DELETE FROM erp.bom_line WHERE bom_id = :id RETURNING bom_line_id",
               {"id": bom["bom_id"]})
    for number, line in enumerate(lines, start=1):
        await one(
            ctx,
            "INSERT INTO erp.bom_line(tenant_id, bom_id, component_item_id, line_no, "
            "quantity_per, scrap_factor) VALUES (erp.current_tenant_id(), :bom, :item, :no, "
            ":qty, :scrap) RETURNING bom_line_id",
            {"bom": bom["bom_id"], "item": line.component_item_id, "no": number,
             "qty": line.quantity_per, "scrap": line.scrap_factor},
        )


@router.get("/boms", response_model=Page[BomOut])
async def list_boms(
    item_id: UUID | None = None,
    bom_status: str | None = Query(None, alias="status"),
    page: Paging = Depends(),
    ctx: Ctx = Depends(read_ctx),
) -> Page[BomOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    if item_id:
        conditions.append("b.item_id = :item_id")
        params["item_id"] = item_id
    if bom_status:
        conditions.append("b.status = :status")
        params["status"] = bom_status.upper()
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    found = await rows(
        ctx, f"SELECT b.bom_id FROM erp.bom b{where} ORDER BY b.item_id, b.version DESC "
        "LIMIT :limit OFFSET :offset", {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.bom b{where}", params)
    boms = [b for b in [await load(ctx, f["bom_id"]) for f in found] if b is not None]
    return Page(items=boms, total=total, limit=page.limit, offset=page.offset)


@router.get("/boms/{bom_id}", response_model=BomOut)
async def get_bom(bom_id: UUID, ctx: Ctx = Depends(read_ctx)) -> BomOut:
    bom = await load(ctx, bom_id)
    if bom is None:
        raise not_found("Bill of material")
    return bom


@router.post("/boms", response_model=BomOut, status_code=status.HTTP_201_CREATED)
async def create_bom(body: BomCreate, ctx: Ctx = Depends(manage)) -> BomOut:
    """Start the first BOM (version 1, a draft) for a made item. Later versions use /revise."""
    item = await one(ctx, "SELECT item_id, item_type, active FROM erp.item WHERE item_id = :id",
                     {"id": body.item_id})
    if item is None:
        raise HTTPException(422, "Item does not exist")
    if not item["active"] or item["item_type"] not in MADE_TYPES:
        raise HTTPException(422, "A BOM belongs to an active work-in-progress or finished item")
    if await one(ctx, "SELECT 1 AS x FROM erp.bom WHERE item_id = :id", {"id": body.item_id}):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This item already has a BOM; revise it to make a new version")
    with db_errors("Bill of material"):
        created = await one(
            ctx,
            "INSERT INTO erp.bom(tenant_id, item_id, version, valid_from, status) VALUES "
            "(erp.current_tenant_id(), :item, 1, current_date, 'DRAFT') RETURNING bom_id, item_id",
            {"item": body.item_id},
        )
    assert created is not None
    await write_lines(ctx, created, body.lines)
    await audit(ctx, "bom.create", "bom", created["bom_id"],
                {"item_id": body.item_id, "lines": len(body.lines)})
    bom = await load(ctx, created["bom_id"])
    assert bom is not None
    return bom


@router.put("/boms/{bom_id}/lines", response_model=BomOut)
async def set_lines(bom_id: UUID, body: LinesIn, ctx: Ctx = Depends(manage)) -> BomOut:
    """Replace the lines of a draft. Approved and pending versions are frozen."""
    bom = await require_bom(ctx, bom_id, lock=True)
    if bom["status"] != "DRAFT":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Only a draft can be edited; this version is {bom['status']}")
    await write_lines(ctx, bom, body.lines)
    await audit(ctx, "bom.lines", "bom", bom_id, {"lines": len(body.lines)})
    result = await load(ctx, bom_id)
    assert result is not None
    return result


@router.post("/boms/{bom_id}/revise", response_model=BomOut, status_code=status.HTTP_201_CREATED)
async def revise_bom(bom_id: UUID, ctx: Ctx = Depends(manage)) -> BomOut:
    """Start the next version as a draft copy of an approved one (the engineering change)."""
    source = await require_bom(ctx, bom_id, lock=True)
    if source["status"] not in ("APPROVED", "SUPERSEDED"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an approved version can be revised")
    latest = await one(ctx, "SELECT max(version) AS v FROM erp.bom WHERE item_id = :i",
                       {"i": source["item_id"]})
    open_one = await one(
        ctx, "SELECT version FROM erp.bom WHERE item_id = :i AND status IN "
        "('DRAFT', 'PENDING_APPROVAL')", {"i": source["item_id"]})
    if open_one is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Version {open_one['version']} is already being prepared")
    assert latest is not None
    created = await one(
        ctx,
        "INSERT INTO erp.bom(tenant_id, item_id, version, valid_from, status) VALUES "
        "(erp.current_tenant_id(), :item, :version, current_date, 'DRAFT') "
        "RETURNING bom_id, item_id",
        {"item": source["item_id"], "version": latest["v"] + 1},
    )
    assert created is not None
    await rows(
        ctx,
        "INSERT INTO erp.bom_line(tenant_id, bom_id, component_item_id, line_no, quantity_per, "
        "scrap_factor) SELECT tenant_id, :new, component_item_id, line_no, quantity_per, "
        "scrap_factor FROM erp.bom_line WHERE bom_id = :old RETURNING bom_line_id",
        {"new": created["bom_id"], "old": bom_id},
    )
    await audit(ctx, "bom.revise", "bom", created["bom_id"],
                {"from_bom_id": bom_id, "version": latest["v"] + 1})
    result = await load(ctx, created["bom_id"])
    assert result is not None
    return result


@router.post("/boms/{bom_id}/submit", response_model=BomOut)
async def submit_bom(bom_id: UUID, body: SubmitIn, ctx: Ctx = Depends(manage)) -> BomOut:
    """Send a draft for approval with the reason and the date it should take effect."""
    bom = await require_bom(ctx, bom_id, lock=True)
    if bom["status"] != "DRAFT":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This version is {bom['status']}")
    if body.effective_from < date.today():
        raise HTTPException(422, "The effective date cannot be in the past")
    if not await count(ctx, "SELECT count(*) FROM erp.bom_line WHERE bom_id = :id", {"id": bom_id}):
        raise HTTPException(422, "A bill of material needs at least one component")
    previous = await one(
        ctx, "SELECT bom_id, version, valid_from FROM erp.bom WHERE item_id = :i AND status IN "
        "('APPROVED', 'SUPERSEDED') ORDER BY valid_from DESC, version DESC LIMIT 1",
        {"i": bom["item_id"]},
    )
    if previous is not None and body.effective_from <= previous["valid_from"]:
        raise HTTPException(
            422, f"The effective date must be after version {previous['version']} takes effect "
            f"({previous['valid_from']})")
    await rows(ctx, "UPDATE erp.bom SET status = 'PENDING_APPROVAL', valid_from = :d "
               "WHERE bom_id = :id RETURNING bom_id", {"d": body.effective_from, "id": bom_id})
    change = await one(
        ctx,
        "INSERT INTO erp.engineering_change(tenant_id, item_id, bom_id, from_bom_id, reason, "
        "effective_from, requested_by) VALUES (erp.current_tenant_id(), :item, :bom, :prev, "
        ":reason, :d, :me) RETURNING change_id",
        {"item": bom["item_id"], "bom": bom_id, "prev": previous["bom_id"] if previous else None,
         "reason": body.reason.strip(), "d": body.effective_from,
         "me": ctx.principal.profile.app_user_id},
    )
    assert change is not None
    approval_id = await engine.submit(ctx, "bom", bom_id)
    await rows(ctx, "UPDATE erp.engineering_change SET approval_id = :a WHERE change_id = :c "
               "RETURNING change_id", {"a": approval_id, "c": change["change_id"]})
    await audit(ctx, "bom.submit", "bom", bom_id,
                {"reason": body.reason, "effective_from": body.effective_from,
                 "approval_id": approval_id})
    result = await load(ctx, bom_id)
    assert result is not None
    return result


# ------------------------------------------------------------------ item views
@router.get("/items/{item_id}/effective-bom", response_model=BomOut)
async def effective_bom(
    item_id: UUID, on: date | None = None, ctx: Ctx = Depends(read_ctx)
) -> BomOut:
    """The BOM in force for an item on a date (default today): exactly one, or 404."""
    day = on or date.today()
    found = await one(
        ctx, "SELECT bom_id FROM erp.bom WHERE item_id = :i AND status IN ('APPROVED', "
        "'SUPERSEDED') AND valid_from <= :d AND (valid_to IS NULL OR valid_to >= :d) "
        "ORDER BY valid_from DESC, version DESC LIMIT 1", {"i": item_id, "d": day},
    )
    bom = await load(ctx, found["bom_id"]) if found else None
    if bom is None:
        raise not_found(f"Effective bill of material on {day}")
    return bom


@router.get("/items/{item_id}/engineering-changes", response_model=list[ChangeOut])
async def engineering_changes(item_id: UUID, ctx: Ctx = Depends(read_ctx)) -> list[ChangeOut]:
    """Who changed this item's BOM, why, and what the approval decided. Newest first."""
    found = await rows(
        ctx,
        "SELECT b.version, pb.version AS from_version, e.bom_id, b.status, e.reason, "
        "e.effective_from, u.display_name AS requested_by, e.requested_at, e.approval_id, "
        "a.status AS approval_status FROM erp.engineering_change e "
        "JOIN erp.bom b ON b.bom_id = e.bom_id LEFT JOIN erp.bom pb ON pb.bom_id = e.from_bom_id "
        "LEFT JOIN erp.app_user u ON u.user_id = e.requested_by "
        "LEFT JOIN erp.approval_instance a ON a.approval_id = e.approval_id "
        "WHERE e.item_id = :id ORDER BY b.version DESC", {"id": item_id},
    )
    return [ChangeOut(**c) for c in found]


@router.get("/items/{item_id}/where-used", response_model=list[BomOut])
async def where_used(item_id: UUID, ctx: Ctx = Depends(read_ctx)) -> list[BomOut]:
    """Live BOM versions that use this item as a component."""
    found = await rows(
        ctx,
        "SELECT DISTINCT b.bom_id, b.item_id, b.version FROM erp.bom_line l JOIN erp.bom b "
        "ON b.bom_id = l.bom_id WHERE l.component_item_id = :id AND b.status = "
        "ANY(CAST(:live AS text[])) ORDER BY b.item_id, b.version DESC",
        {"id": item_id, "live": list(LIVE)},
    )
    return [b for b in [await load(ctx, f["bom_id"]) for f in found] if b is not None]


# ------------------------------------------------------------------ approval adapter
async def load_for_approval(ctx: Ctx, bom_id: UUID) -> DocInfo | None:
    bom = await one(
        ctx, "SELECT b.version, b.status, i.code FROM erp.bom b JOIN erp.item i "
        "ON i.item_id = b.item_id WHERE b.bom_id = :id", {"id": bom_id},
    )
    if bom is None:
        return None
    return DocInfo(
        title=f"BOM {bom['code']} version {bom['version']}", amount=None, department_id=None,
        submittable=bom["status"] == "PENDING_APPROVAL",
        reason=f"This BOM version is {str(bom['status']).lower().replace('_', ' ')}",
    )


async def finalize_bom(ctx: Ctx, bom_id: UUID, approved: bool) -> None:
    bom = await require_bom(ctx, bom_id, lock=True)
    if not approved:
        await rows(ctx, "UPDATE erp.bom SET status = 'REJECTED' WHERE bom_id = :id "
                   "RETURNING bom_id", {"id": bom_id})
        return
    # A change cannot take effect in the past: if approval came after the requested date,
    # it takes effect today.
    effective = max(bom["valid_from"], date.today())
    previous = await one(
        ctx, "SELECT bom_id FROM erp.bom WHERE item_id = :i AND bom_id <> :id AND status IN "
        "('APPROVED', 'SUPERSEDED') AND valid_to IS NULL ORDER BY valid_from DESC LIMIT 1",
        {"i": bom["item_id"], "id": bom_id},
    )
    if previous is not None:
        await rows(ctx, "UPDATE erp.bom SET status = 'SUPERSEDED', valid_to = :d "
                   "WHERE bom_id = :id RETURNING bom_id",
                   {"d": effective - timedelta(days=1), "id": previous["bom_id"]})
    await rows(ctx, "UPDATE erp.bom SET status = 'APPROVED', valid_from = :d WHERE bom_id = :id "
               "RETURNING bom_id", {"d": effective, "id": bom_id})


register(DocumentType("bom", "bom.manage", load_for_approval, finalize_bom))
