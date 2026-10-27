"""Administration: ERP users (profiles) and the read-only role catalog. Needs the admin permission.

A person first signs up and joins the V-Rub organization through Better Auth; an administrator
then gives that account an ERP profile here: one role, an optional department, and a plant scope.
A deactivated or locked profile is refused on the person's very next request.
"""

from typing import Any, Literal, Self
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import text

from heyyerp_api.config import get_settings
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
    rows,
)

router = APIRouter(prefix="/admin", tags=["administration"])
manage = requires("admin")
UserStatus = Literal["ACTIVE", "DEACTIVATED", "LOCKED"]


class RoleOut(BaseModel):
    role_id: UUID
    code: str
    name: str
    is_system: bool
    permissions: list[str]
    modules: list[str]


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(ctx: Ctx = Depends(manage)) -> list[RoleOut]:
    roles = await rows(ctx, "SELECT role_id, code, name, is_system FROM erp.role ORDER BY code")
    perms = await rows(
        ctx, "SELECT rp.role_id, p.code FROM erp.role_permission rp "
        "JOIN erp.permission p ON p.permission_id = rp.permission_id ORDER BY p.code")
    mods = await rows(ctx, "SELECT role_id, module_code FROM erp.role_module ORDER BY module_code")
    return [
        RoleOut(
            **r,
            permissions=[p["code"] for p in perms if p["role_id"] == r["role_id"]],
            modules=[m["module_code"] for m in mods if m["role_id"] == r["role_id"]],
        )
        for r in roles
    ]


class UserIn(Strict):
    email: EmailStr
    role_code: str
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=20)
    department_id: UUID | None = None
    all_plants: bool = False
    unit_ids: list[UUID] = []

    @model_validator(mode="after")
    def _scope(self) -> Self:
        if self.all_plants == bool(self.unit_ids):
            raise ValueError("Give either all_plants=true or a non-empty unit_ids list")
        return self


class UserPatch(Strict):
    role_code: str | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=20)
    department_id: UUID | None = None
    status: UserStatus | None = None
    all_plants: bool | None = None
    unit_ids: list[UUID] | None = None

    @model_validator(mode="after")
    def _scope(self) -> Self:
        if self.all_plants is not None or self.unit_ids is not None:
            if bool(self.all_plants) == bool(self.unit_ids):
                raise ValueError("Give either all_plants=true or a non-empty unit_ids list")
        return self


class UserOut(BaseModel):
    user_id: UUID
    email: str | None
    display_name: str
    phone: str | None
    role: str
    department_id: UUID | None
    status: UserStatus
    all_plants: bool
    unit_ids: list[UUID]


USER_SQL = (
    "SELECT u.user_id, u.email, u.display_name, u.phone, r.code AS role, u.department_id, "
    "u.status, COALESCE((SELECT array_agg(s.unit_id ORDER BY s.unit_id) FROM "
    "erp.user_unit_scope s WHERE s.user_id = u.user_id), ARRAY[]::uuid[]) AS unit_ids "
    "FROM erp.app_user u JOIN erp.role r ON r.tenant_id = u.tenant_id AND r.role_id = u.role_id"
)


def user_out(row: dict[str, Any]) -> UserOut:
    return UserOut(**row, all_plants=not row["unit_ids"])


async def fetch_user(ctx: Ctx, user_id: UUID) -> dict[str, Any] | None:
    return await one(ctx, f"{USER_SQL} WHERE u.user_id = :id", {"id": user_id})


async def replace_scope(ctx: Ctx, user_id: UUID, unit_ids: list[UUID]) -> None:
    await ctx.session.execute(
        text("DELETE FROM erp.user_unit_scope WHERE user_id = :id"), {"id": user_id})
    if unit_ids:
        with db_errors("Plant scope"):
            await ctx.session.execute(
                text("INSERT INTO erp.user_unit_scope(tenant_id, user_id, unit_id) "
                     "SELECT erp.current_tenant_id(), :id, unnest(CAST(:units AS uuid[]))"),
                {"id": user_id, "units": unit_ids},
            )


async def other_active_admins(ctx: Ctx, excluding: UUID) -> int:
    return await count(
        ctx,
        "SELECT count(*) FROM erp.app_user u JOIN erp.role r ON r.role_id = u.role_id "
        "WHERE r.code = 'ADMIN' AND u.status = 'ACTIVE' AND u.user_id <> :id",
        {"id": excluding},
    )


@router.get("/users", response_model=Page[UserOut])
async def list_users(
    user_status: UserStatus | None = None,
    role: str | None = None,
    page: Paging = Depends(),
    ctx: Ctx = Depends(manage),
) -> Page[UserOut]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    if user_status:
        conditions.append("u.status = :status")
        params["status"] = user_status
    if role:
        conditions.append("r.code = :role")
        params["role"] = role.upper()
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(
        ctx, f"{USER_SQL}{where} ORDER BY u.display_name LIMIT :limit OFFSET :offset",
        {**params, "limit": page.limit, "offset": page.offset},
    )
    total = await count(
        ctx, "SELECT count(*) FROM erp.app_user u JOIN erp.role r ON r.role_id = u.role_id"
        f"{where}", params)
    return Page(items=[user_out(i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/users/{user_id}", response_model=UserOut)
async def get_user(user_id: UUID, ctx: Ctx = Depends(manage)) -> UserOut:
    row = await fetch_user(ctx, user_id)
    if row is None:
        raise not_found("User")
    return user_out(row)


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserIn, ctx: Ctx = Depends(manage)) -> UserOut:
    account = await one(
        ctx, 'SELECT id, name, email FROM auth."user" WHERE lower(email) = lower(:email)',
        {"email": str(body.email)},
    )
    if account is None:
        raise HTTPException(404, "No sign-in with that email; the person must sign up first")
    member = await one(
        ctx, 'SELECT 1 AS x FROM auth.member m JOIN auth.organization o '
        'ON o.id = m."organizationId" WHERE m."userId" = :id AND o.slug = :slug',
        {"id": account["id"], "slug": get_settings().required_org_slug},
    )
    if member is None:
        raise HTTPException(422, "That person is not a member of the V-Rub organization yet")
    with db_errors("This account already has an ERP profile; user"):
        created = await one(
            ctx,
            "INSERT INTO erp.app_user(tenant_id, auth_subject, role_id, department_id, "
            "display_name, email, phone) SELECT erp.current_tenant_id(), :sub, role_id, "
            ":department_id, :name, :email, :phone FROM erp.role WHERE code = :role "
            "RETURNING user_id",
            {"sub": account["id"], "department_id": body.department_id,
             "name": body.display_name or account["name"], "email": account["email"],
             "phone": body.phone, "role": body.role_code.upper()},
        )
    if created is None:
        raise HTTPException(422, f"Unknown role {body.role_code}")
    await replace_scope(ctx, created["user_id"], [] if body.all_plants else body.unit_ids)
    await audit(ctx, "user.create", "app_user", created["user_id"],
                {"role": body.role_code.upper(), "all_plants": body.all_plants,
                 "unit_ids": body.unit_ids})
    row = await fetch_user(ctx, created["user_id"])
    assert row is not None
    return user_out(row)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: UUID, body: UserPatch, ctx: Ctx = Depends(manage)) -> UserOut:
    current = await fetch_user(ctx, user_id)
    if current is None:
        raise not_found("User")
    changes = body.model_dump(exclude_unset=True)
    new_role = (body.role_code or current["role"]).upper()
    if "role_code" in changes and not await one(
        ctx, "SELECT 1 AS x FROM erp.role WHERE code = :r", {"r": new_role}
    ):
        raise HTTPException(422, f"Unknown role {body.role_code}")
    new_status = body.status or current["status"]
    losing_admin = current["role"] == "ADMIN" and current["status"] == "ACTIVE" and (
        new_role != "ADMIN" or new_status != "ACTIVE")
    if losing_admin and (
        user_id == ctx.principal.profile.app_user_id
        or await other_active_admins(ctx, user_id) == 0
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This would leave no active administrator, or lock you out of your own account",
        )
    if "role_code" in changes or "department_id" in changes or "display_name" in changes \
            or "phone" in changes or "status" in changes:
        with db_errors("User"):
            updated = await one(
                ctx,
                "UPDATE erp.app_user SET role_id = (SELECT role_id FROM erp.role "
                "WHERE code = :role), department_id = :department_id, "
                "display_name = :name, phone = :phone, status = :status "
                "WHERE user_id = :id RETURNING user_id",
                {"role": new_role, "department_id": changes.get(
                    "department_id", current["department_id"]),
                 "name": body.display_name or current["display_name"],
                 "phone": changes.get("phone", current["phone"]),
                 "status": new_status, "id": user_id},
            )
        assert updated is not None
    if body.all_plants is not None or body.unit_ids is not None:
        await replace_scope(ctx, user_id, [] if body.all_plants else (body.unit_ids or []))
    await audit(ctx, "user.update", "app_user", user_id, {"changes": changes})
    row = await fetch_user(ctx, user_id)
    assert row is not None
    return user_out(row)
