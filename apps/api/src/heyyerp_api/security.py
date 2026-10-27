"""Trusted request authentication and authorisation.

A request is trusted only after four checks: the Better Auth session is valid (asked of the auth
service), the person belongs to the V-Rub organization, the organization maps to an ERP tenant,
and the account has an active ERP profile (app_user) whose role grants permissions and modules.
Only then is request.state.tenant_id set for get_tenant_session. The tenant is never read from
the client, and permissions come from the database, never from the browser.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from heyyerp_api.config import get_settings
from heyyerp_api.db import get_session

# BRD section 9.4. Keep in sync with MFA_REQUIRED_ROLES in src/auth-contract.ts.
MFA_REQUIRED_ROLES = frozenset({"ADMIN", "MD", "ACCOUNTS"})


@dataclass(frozen=True)
class Profile:
    """The caller's ERP profile: one role, its permissions and modules, and plant scope."""

    app_user_id: UUID
    display_name: str
    role: str
    permissions: frozenset[str]
    modules: frozenset[str]
    unit_ids: frozenset[UUID] | None  # None means all plants


@dataclass(frozen=True)
class Principal:
    user_id: str  # Better Auth account id
    email: str
    email_verified: bool
    organization_id: str
    two_factor_enabled: bool
    tenant_id: UUID
    profile: Profile

    @property
    def role(self) -> str:
        return self.profile.role

    @property
    def mfa_required(self) -> bool:
        return self.profile.role in MFA_REQUIRED_ROLES

    def may_see_unit(self, unit_id: UUID | None) -> bool:
        scope = self.profile.unit_ids
        return scope is None or (unit_id is not None and unit_id in scope)


async def fetch_auth_session(
    cookie: str | None, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any] | None:
    """Ask the auth service who the cookie belongs to. None means not signed in."""
    if not cookie:
        return None
    # ponytail: one HTTP call per request; enable Better Auth cookieCache if latency matters.
    async with httpx.AsyncClient(
        base_url=get_settings().auth_base_url, timeout=5.0, transport=transport
    ) as client:
        try:
            response = await client.get("/api/auth/get-session", headers={"cookie": cookie})
        except httpx.HTTPError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication service unavailable"
            ) from exc
    if response.status_code >= 500 or response.status_code == 429:
        # Throttled or down is not the same as signed out; never report it as a 401.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication service unavailable"
        )
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) and body.get("user") else None


async def fetch_membership(db: AsyncSession, user_id: str) -> tuple[str, tuple[str, ...]] | None:
    """Return (organization_id, Better Auth roles) for the user in the V-Rub organization."""
    row = (
        await db.execute(
            text(
                'SELECT o.id, m.role FROM auth.member m '
                'JOIN auth.organization o ON o.id = m."organizationId" '
                'WHERE m."userId" = :user_id AND o.slug = :slug'
            ),
            {"user_id": user_id, "slug": get_settings().required_org_slug},
        )
    ).first()
    if row is None:
        return None
    # Better Auth stores several roles as a comma-separated string.
    roles = tuple(part.strip().upper() for part in str(row[1]).split(",") if part.strip())
    return str(row[0]), roles


def mapped_tenant() -> UUID:
    settings = get_settings()
    tenant_id = settings.org_tenant_map.get(settings.required_org_slug)
    if tenant_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Organization is not mapped to a tenant")
    return tenant_id


async def fetch_profile(db: AsyncSession, tenant_id: UUID, auth_user_id: str) -> Profile | None:
    """Load the ERP profile for a sign-in. Runs under the tenant's row-level security."""
    await db.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)}
    )
    user = (
        await db.execute(
            text(
                "SELECT u.user_id, u.display_name, u.status, u.role_id, r.code AS role "
                "FROM erp.app_user u JOIN erp.role r "
                "ON r.tenant_id = u.tenant_id AND r.role_id = u.role_id "
                "WHERE u.auth_subject = :subject"
            ),
            {"subject": auth_user_id},
        )
    ).mappings().first()
    if user is None:
        return None
    if user["status"] != "ACTIVE":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"Your ERP account is {str(user['status']).lower()}"
        )
    role_id = {"role_id": user["role_id"]}

    async def column(sql: str, params: dict[str, Any]) -> list[Any]:
        return [row[0] for row in (await db.execute(text(sql), params)).all()]

    permissions = await column(
        "SELECT p.code FROM erp.role_permission rp JOIN erp.permission p "
        "ON p.permission_id = rp.permission_id WHERE rp.role_id = :role_id", role_id)
    modules = await column(
        "SELECT module_code FROM erp.role_module WHERE role_id = :role_id", role_id)
    units = await column(
        "SELECT unit_id FROM erp.user_unit_scope WHERE user_id = :user_id",
        {"user_id": user["user_id"]})
    return Profile(
        app_user_id=user["user_id"],
        display_name=user["display_name"],
        role=user["role"],
        permissions=frozenset(permissions),
        modules=frozenset(modules),
        unit_ids=frozenset(units) or None,
    )


async def current_auth_session(request: Request) -> dict[str, Any] | None:
    return await fetch_auth_session(request.headers.get("cookie"))


async def current_membership(
    auth: dict[str, Any] | None = Depends(current_auth_session),
    db: AsyncSession = Depends(get_session),
) -> tuple[str, tuple[str, ...]] | None:
    return await fetch_membership(db, auth["user"]["id"]) if auth else None


async def current_profile(
    auth: dict[str, Any] | None = Depends(current_auth_session),
    membership: tuple[str, tuple[str, ...]] | None = Depends(current_membership),
    db: AsyncSession = Depends(get_session),
) -> Profile | None:
    if auth is None or membership is None:
        return None
    return await fetch_profile(db, mapped_tenant(), auth["user"]["id"])


async def authenticate(
    auth: dict[str, Any] | None = Depends(current_auth_session),
    membership: tuple[str, tuple[str, ...]] | None = Depends(current_membership),
    profile: Profile | None = Depends(current_profile),
) -> Principal:
    """Signed in, email verified, V-Rub member with an active ERP profile. MFA is not checked."""
    if auth is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    user = auth["user"]
    if not user.get("emailVerified"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Email address is not verified")
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of the V-Rub organization")
    if profile is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "No ERP profile for this account; ask an administrator"
        )
    return Principal(
        user_id=user["id"],
        email=user.get("email", ""),
        email_verified=True,
        organization_id=membership[0],
        two_factor_enabled=bool(user.get("twoFactorEnabled")),
        tenant_id=mapped_tenant(),
        profile=profile,
    )


async def require_principal(
    request: Request, principal: Principal = Depends(authenticate)
) -> Principal:
    """Full ERP access check; use on every protected route. Sets the trusted tenant."""
    if principal.mfa_required and not principal.two_factor_enabled:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Two-factor authentication is required for your role"
        )
    request.state.tenant_id = principal.tenant_id
    request.state.principal = principal
    return principal


def has_permission(principal: Principal, permission: str) -> bool:
    return permission in principal.profile.permissions
