from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from heyyerp_api.config import get_settings

settings = get_settings()


def _build_engine(url: str) -> AsyncEngine:
    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )


engine = _build_engine(settings.database_url)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def configure_database(url: str) -> None:
    """Point the API at another database (used by tests that build a scratch database)."""
    global engine, SessionFactory
    engine = _build_engine(url)
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a database session. Use get_tenant_session for tenant-owned data."""
    async with SessionFactory() as session:
        yield session


async def get_tenant_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Open a transaction with PostgreSQL RLS scoped to an authenticated tenant.

    Authentication middleware must set request.state.tenant_id after validating the
    caller's session. The tenant must never be trusted from a raw request header.
    """
    raw_tenant_id = getattr(request.state, "tenant_id", None)
    if raw_tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated tenant context is required",
        )

    try:
        tenant_id = UUID(str(raw_tenant_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authenticated tenant context",
        ) from exc

    async with SessionFactory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            yield session


async def check_database() -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose_database() -> None:
    await engine.dispose()
