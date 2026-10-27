"""Helpers for tests that need a real PostgreSQL server (scratch databases)."""

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from alembic import command
from alembic.config import Config

from heyyerp_api.config import get_settings

ADMIN_URL = os.environ.get("TEST_ADMIN_DATABASE_URL")
API_DIR = Path(__file__).resolve().parent.parent


def url_for(database: str, user: str | None = None, password: str | None = None) -> str:
    assert ADMIN_URL
    parts = urlsplit(ADMIN_URL)
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    auth = f"{user}:{password}@" if user else parts.netloc.rsplit("@", 1)[0] + "@"
    return urlunsplit((parts.scheme, auth + host, f"/{database}", "", ""))


def alembic_config() -> Config:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(API_DIR / "migrations"))
    return config


def create_migrated_database(name: str) -> None:
    """Create an empty database and run every migration. Drops any previous copy."""
    with psycopg.connect(url_for("postgres"), autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{name}"')
    previous = os.environ.get("MIGRATION_DATABASE_URL")
    os.environ["MIGRATION_DATABASE_URL"] = url_for(name)
    get_settings.cache_clear()
    try:
        command.upgrade(alembic_config(), "head")
    finally:
        if previous is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = previous
        get_settings.cache_clear()


def drop_database(name: str, role: str | None = None) -> None:
    with psycopg.connect(url_for("postgres"), autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if role:
            admin.execute(f"DROP ROLE IF EXISTS {role}")
