"""Shared plumbing for tenant-scoped master-data endpoints."""

import json
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from heyyerp_api.db import get_tenant_session
from heyyerp_api.security import Principal, has_permission, require_principal


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


@dataclass
class Ctx:
    session: AsyncSession
    principal: Principal


async def read_ctx(
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> Ctx:
    """Any signed-in V-Rub member may read master data of their own tenant."""
    return Ctx(session, principal)


def requires(permission: str) -> Callable[..., Awaitable[Ctx]]:
    async def dependency(ctx: Ctx = Depends(read_ctx)) -> Ctx:
        if not has_permission(ctx.principal, permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires permission {permission}")
        return ctx

    return dependency


class Paging:
    def __init__(self, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> None:
        self.limit, self.offset = limit, offset


@contextmanager
def db_errors(entity: str) -> Iterator[None]:
    """Turn constraint violations into clear 4xx responses."""
    try:
        yield
    except IntegrityError as exc:
        code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if code == "23505":
            raise HTTPException(status.HTTP_409_CONFLICT, f"{entity} already exists") from exc
        if code == "23503":
            raise HTTPException(
                422,
                f"{entity} refers to a record that does not exist",
            ) from exc
        if code in {"23502", "23514"}:
            raise HTTPException(
                422, f"{entity} has an invalid value"
            ) from exc
        raise


async def audit(
    ctx: Ctx, action: str, entity_type: str, entity_id: UUID | str, details: dict[str, Any]
) -> None:
    """Append an audit row. The actor is the Better Auth user until app_user rows exist."""
    await ctx.session.execute(
        text(
            "INSERT INTO erp.audit_log(tenant_id, actor_role_code, action, entity_type, "
            "entity_id, details) VALUES (erp.current_tenant_id(), :role, :action, "
            ":entity_type, :entity_id, CAST(:details AS jsonb))"
        ),
        {
            "role": ",".join(ctx.principal.roles),
            "action": action,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "details": json.dumps(
                {"auth_user_id": ctx.principal.user_id, "email": ctx.principal.email, **details},
                default=str,
            ),
        },
    )


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


def not_found(entity: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{entity} not found")
