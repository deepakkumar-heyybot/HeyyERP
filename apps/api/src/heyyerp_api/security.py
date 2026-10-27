"""Trusted request authentication.

Validates the Better Auth session cookie against the auth service, resolves the caller's
membership in the V-Rub organization, maps that organization to an ERP tenant, and only then
sets request.state.tenant_id for get_tenant_session. The tenant is never read from the client.
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
class Principal:
    user_id: str
    email: str
    email_verified: bool
    organization_id: str
    roles: tuple[str, ...]
    two_factor_enabled: bool

    @property
    def mfa_required(self) -> bool:
        return any(role in MFA_REQUIRED_ROLES for role in self.roles)


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
    if response.status_code >= 500:
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
    """Return (organization_id, roles) for the user in the V-Rub organization, if any."""
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


async def current_auth_session(request: Request) -> dict[str, Any] | None:
    return await fetch_auth_session(request.headers.get("cookie"))


async def current_membership(
    auth: dict[str, Any] | None = Depends(current_auth_session),
    db: AsyncSession = Depends(get_session),
) -> tuple[str, tuple[str, ...]] | None:
    return await fetch_membership(db, auth["user"]["id"]) if auth else None


async def authenticate(
    auth: dict[str, Any] | None = Depends(current_auth_session),
    membership: tuple[str, tuple[str, ...]] | None = Depends(current_membership),
) -> Principal:
    """Signed in, email verified, member of the V-Rub organization. MFA is not checked here."""
    if auth is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    user = auth["user"]
    if not user.get("emailVerified"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Email address is not verified")
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of the V-Rub organization")
    organization_id, roles = membership
    return Principal(
        user_id=user["id"],
        email=user.get("email", ""),
        email_verified=True,
        organization_id=organization_id,
        roles=roles,
        two_factor_enabled=bool(user.get("twoFactorEnabled")),
    )


async def require_principal(
    request: Request, principal: Principal = Depends(authenticate)
) -> Principal:
    """Full ERP access check; use on every protected route. Sets the trusted tenant."""
    if principal.mfa_required and not principal.two_factor_enabled:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Two-factor authentication is required for your role"
        )
    settings = get_settings()
    tenant_id: UUID | None = settings.org_tenant_map.get(settings.required_org_slug)
    if tenant_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Organization is not mapped to a tenant")
    request.state.tenant_id = tenant_id
    request.state.principal = principal
    return principal
