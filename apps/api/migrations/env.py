"""Alembic environment. Migrations run as the schema owner, never as the API's app role."""

from alembic import context
from sqlalchemy import create_engine, pool

from heyyerp_api.config import get_settings


def database_url() -> str:
    url = get_settings().migration_database_url
    if not url:
        raise SystemExit("Set MIGRATION_DATABASE_URL to a role that owns the erp schema.")
    for prefix in ("postgresql+asyncpg://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


engine = create_engine(database_url(), poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
