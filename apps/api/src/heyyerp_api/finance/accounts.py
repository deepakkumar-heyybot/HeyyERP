"""Chart of accounts per plant and the accounts each business event posts to (BRD FR-FIN-01).

Every plant has its own accounts (code, name, type). `seed-default` creates a standard chart for a
plant and maps the account roles the automatic postings use (receivable, payable, bank, inventory,
GST, sales, cost of sales, ...). The chart and the mapping are a proposal until V-Rub's accountants
confirm them; they can be edited at any time. Accounts are deactivated, never deleted, and an
inactive account cannot be posted to. Needs fin.voucher to change; reading needs the finance module.
"""

from typing import Any, Literal
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

router = APIRouter(prefix="/finance", tags=["finance"])
manage = requires("fin.voucher")
read = requires_module("finance")
AccountType = Literal["ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE"]

# (code, name, type, role). The role names are what the postings in the other modules look up.
DEFAULT_CHART: list[tuple[str, str, str, str | None]] = [
    ("1000", "Cash in hand", "ASSET", "CASH"),
    ("1010", "Bank", "ASSET", "BANK"),
    ("1100", "Accounts receivable", "ASSET", "RECEIVABLE"),
    ("1200", "Inventory - raw material", "ASSET", "INVENTORY_RM"),
    ("1210", "Inventory - work in progress", "ASSET", "INVENTORY_WIP"),
    ("1220", "Inventory - finished goods", "ASSET", "INVENTORY_FG"),
    ("1300", "GST input credit", "ASSET", "GST_INPUT"),
    ("1400", "Advances to suppliers", "ASSET", None),
    ("1500", "Inter-unit receivable", "ASSET", "INTER_UNIT_RECEIVABLE"),
    ("2000", "Accounts payable", "LIABILITY", "PAYABLE"),
    ("2010", "Goods received not billed", "LIABILITY", "GRN_CLEARING"),
    ("2100", "GST output", "LIABILITY", "GST_OUTPUT"),
    ("2200", "TDS payable", "LIABILITY", "TDS_PAYABLE"),
    ("2500", "Inter-unit payable", "LIABILITY", "INTER_UNIT_PAYABLE"),
    ("3000", "Capital", "EQUITY", None),
    ("3100", "Retained earnings", "EQUITY", None),
    ("4000", "Sales", "INCOME", "SALES"),
    ("4100", "Scrap and asset sales", "INCOME", "SCRAP_SALES"),
    ("4200", "Inter-unit sales", "INCOME", "INTER_UNIT_SALES"),
    ("5000", "Cost of goods sold", "EXPENSE", "COST_OF_SALES"),
    ("5100", "Raw material consumed", "EXPENSE", "MATERIAL_CONSUMED"),
    ("5200", "Production variance", "EXPENSE", "PRODUCTION_VARIANCE"),
    ("5300", "Inventory adjustments", "EXPENSE", "INVENTORY_ADJUSTMENT"),
    ("5400", "Price and quantity differences", "EXPENSE", "PURCHASE_VARIANCE"),
    ("5500", "Freight and other expenses", "EXPENSE", None),
    ("5600", "Inter-unit purchases", "EXPENSE", "INTER_UNIT_PURCHASES"),
]
ROLES = tuple(r for _, _, _, r in DEFAULT_CHART if r)


class AccountIn(Strict):
    unit_id: UUID
    code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    name: str = Field(min_length=2, max_length=200)
    account_type: AccountType


class AccountPatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    active: bool | None = None


class AccountOut(BaseModel):
    account_id: UUID
    unit_id: UUID
    code: str
    name: str
    account_type: str
    active: bool
    roles: list[str]


class SeedIn(Strict):
    unit_id: UUID


class RoleIn(Strict):
    unit_id: UUID
    role: str
    account_id: UUID


class RoleOut(BaseModel):
    unit_id: UUID
    role: str
    account_id: UUID
    account_code: str
    account_name: str


ACCOUNT_SQL = (
    "SELECT a.account_id, a.unit_id, a.code, a.name, a.account_type, a.active, COALESCE((SELECT "
    "array_agg(r.role ORDER BY r.role) FROM erp.account_role r WHERE r.account_id = a.account_id), "
    "ARRAY[]::text[]) AS roles FROM erp.account a"
)


async def role_account(ctx: Ctx, unit_id: UUID, role: str) -> UUID:
    """The account a business event uses in a plant; says what to fix when it is not set up."""
    found = await one(
        ctx, "SELECT a.account_id, a.active FROM erp.account_role r JOIN erp.account a ON "
        "a.account_id = r.account_id WHERE r.unit_id = :u AND r.role = :r",
        {"u": unit_id, "r": role})
    if found is None or not found["active"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The plant has no active account for {role}; set up its chart of accounts first")
    return found["account_id"]  # type: ignore[no-any-return]


@router.post("/accounts/seed-default", response_model=list[AccountOut])
async def seed_default(body: SeedIn, ctx: Ctx = Depends(manage)) -> list[AccountOut]:
    """Create the standard chart and role mapping for a plant; adds only what is missing."""
    require_in_scope(ctx, body.unit_id)
    unit = await one(ctx, "SELECT active FROM erp.unit WHERE unit_id = :id", {"id": body.unit_id})
    if unit is None or not unit["active"]:
        raise HTTPException(422, "The plant does not exist or is inactive")
    for code, name, kind, role in DEFAULT_CHART:
        await rows(
            ctx, "INSERT INTO erp.account(tenant_id, unit_id, code, name, account_type) VALUES "
            "(erp.current_tenant_id(), :u, :c, :n, :t) ON CONFLICT (unit_id, code) DO NOTHING "
            "RETURNING account_id", {"u": body.unit_id, "c": code, "n": name, "t": kind})
        if role:
            await rows(
                ctx, "INSERT INTO erp.account_role(tenant_id, unit_id, role, account_id) SELECT "
                "erp.current_tenant_id(), :u, :r, account_id FROM erp.account WHERE unit_id = :u "
                "AND code = :c ON CONFLICT (unit_id, role) DO NOTHING RETURNING role",
                {"u": body.unit_id, "r": role, "c": code})
    await audit(ctx, "account.seed_default", "unit", body.unit_id, {"accounts": len(DEFAULT_CHART)})
    found = await rows(ctx, f"{ACCOUNT_SQL} WHERE a.unit_id = :u ORDER BY a.code",
                       {"u": body.unit_id})
    return [AccountOut(**a) for a in found]


@router.post("/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(body: AccountIn, ctx: Ctx = Depends(manage)) -> AccountOut:
    require_in_scope(ctx, body.unit_id)
    unit = await one(ctx, "SELECT active FROM erp.unit WHERE unit_id = :id", {"id": body.unit_id})
    if unit is None or not unit["active"]:
        raise HTTPException(422, "The plant does not exist or is inactive")
    with db_errors("This plant already has an account with this code"):
        created = await one(
            ctx, "INSERT INTO erp.account(tenant_id, unit_id, code, name, account_type) VALUES "
            "(erp.current_tenant_id(), :u, :c, :n, :t) RETURNING account_id",
            {"u": body.unit_id, "c": body.code.upper(), "n": body.name, "t": body.account_type})
    assert created is not None
    await audit(ctx, "account.create", "account", created["account_id"], body.model_dump())
    result = await one(ctx, f"{ACCOUNT_SQL} WHERE a.account_id = :id",
                       {"id": created["account_id"]})
    assert result is not None
    return AccountOut(**result)


@router.get("/accounts", response_model=Page[AccountOut])
async def list_accounts(
    unit_id: UUID | None = None, account_type: AccountType | None = None,
    active: bool | None = Query(None), page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[AccountOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "a.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for text_, key, value in (("a.unit_id = :u", "u", unit_id), ("a.account_type = :t", "t",
                              account_type), ("a.active = :a", "a", active)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{ACCOUNT_SQL}{where} ORDER BY a.unit_id, a.code LIMIT :limit OFFSET "
                       ":offset", {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.account a{where}", params)
    return Page(items=[AccountOut(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


async def load_account(ctx: Ctx, account_id: UUID) -> AccountOut | None:
    row = await one(ctx, f"{ACCOUNT_SQL} WHERE a.account_id = :id", {"id": account_id})
    if row is None or not ctx.principal.may_see_unit(row["unit_id"]):
        return None
    return AccountOut(**row)


@router.get("/accounts/{account_id}", response_model=AccountOut)
async def get_account(account_id: UUID, ctx: Ctx = Depends(read)) -> AccountOut:
    found = await load_account(ctx, account_id)
    if found is None:
        raise not_found("Account")
    return found


@router.patch("/accounts/{account_id}", response_model=AccountOut)
async def update_account(
    account_id: UUID, body: AccountPatch, ctx: Ctx = Depends(manage)
) -> AccountOut:
    current = await get_account(account_id, ctx)
    changes = body.model_dump(exclude_unset=True)
    merged = {"name": current.name, "active": current.active, **changes}
    if merged["name"] is None or merged["active"] is None:
        raise HTTPException(422, "Fields cannot be null")
    if not merged["active"] and current.roles:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The account is used for {', '.join(current.roles)}; map those roles to another "
            "account first")
    await rows(ctx, "UPDATE erp.account SET name = :name, active = :active WHERE account_id = :id "
               "RETURNING account_id", {**merged, "id": account_id})
    await audit(ctx, "account.update", "account", account_id, {"changes": changes})
    result = await load_account(ctx, account_id)
    assert result is not None
    return result


@router.get("/account-roles", response_model=list[RoleOut])
async def list_roles(unit_id: UUID, ctx: Ctx = Depends(read)) -> list[RoleOut]:
    require_in_scope(ctx, unit_id)
    found = await rows(
        ctx, "SELECT r.unit_id, r.role, r.account_id, a.code AS account_code, a.name AS "
        "account_name FROM erp.account_role r JOIN erp.account a ON a.account_id = r.account_id "
        "WHERE r.unit_id = :u ORDER BY r.role", {"u": unit_id})
    return [RoleOut(**r) for r in found]


@router.put("/account-roles", response_model=RoleOut)
async def set_role(body: RoleIn, ctx: Ctx = Depends(manage)) -> RoleOut:
    """Point a posting role (for example BANK) at another account of the same plant."""
    require_in_scope(ctx, body.unit_id)
    if body.role not in ROLES:
        raise HTTPException(422, f"Unknown role; use one of {', '.join(ROLES)}")
    account = await one(ctx, "SELECT unit_id, active FROM erp.account WHERE account_id = :id",
                        {"id": body.account_id})
    if account is None or account["unit_id"] != body.unit_id or not account["active"]:
        raise HTTPException(422, "The account must be an active account of the same plant")
    await rows(
        ctx, "INSERT INTO erp.account_role(tenant_id, unit_id, role, account_id) VALUES "
        "(erp.current_tenant_id(), :u, :r, :a) ON CONFLICT (unit_id, role) DO UPDATE SET "
        "account_id = EXCLUDED.account_id RETURNING role",
        {"u": body.unit_id, "r": body.role, "a": body.account_id})
    await audit(ctx, "account.role", "unit", body.unit_id,
                {"role": body.role, "account_id": body.account_id})
    found = await list_roles(body.unit_id, ctx)
    return next(r for r in found if r.role == body.role)
