"""Database tests: migrations from a clean database, and tenant isolation under RLS.

They create and drop a scratch database, so they only run when TEST_ADMIN_DATABASE_URL points
at a PostgreSQL server login that may create databases and roles, e.g.
  TEST_ADMIN_DATABASE_URL=postgresql://postgres:***@localhost:5432/postgres
"""

from uuid import uuid4

import psycopg
import pytest
from alembic import command

from heyyerp_api.config import get_settings

from .dbutil import ADMIN_URL, alembic_config, url_for

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_ADMIN_DATABASE_URL is not set")

SCRATCH_DB = "heyyerp_migration_test"
APP_ROLE, APP_PASSWORD = "heyyerp_app_test", "app-test-password"


@pytest.fixture(scope="module")
def scratch_db():
    admin = psycopg.connect(url_for("postgres"), autocommit=True)
    admin.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)')
    admin.execute(f'CREATE DATABASE "{SCRATCH_DB}"')
    mp = pytest.MonkeyPatch()
    mp.setenv("MIGRATION_DATABASE_URL", url_for(SCRATCH_DB))
    get_settings.cache_clear()
    command.upgrade(alembic_config(), "head")
    yield url_for(SCRATCH_DB)
    mp.undo()
    get_settings.cache_clear()
    admin.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)')
    admin.execute(f"DROP ROLE IF EXISTS {APP_ROLE}")
    admin.close()


def test_clean_database_reaches_head_with_the_full_schema(scratch_db):
    with psycopg.connect(scratch_db) as db:
        assert db.execute("select version_num from alembic_version").fetchone() == ("0005",)
        tables = db.execute(
            "select count(*) from information_schema.tables "
            "where table_schema = 'erp' and table_type = 'BASE TABLE'"
        ).fetchone()[0]
        assert tables == 126
        # The baseline's format()/RAISE '%' text must have survived intact.
        body = db.execute(
            "select prosrc from pg_proc where proname = 'prevent_immutable_row_change'"
        )
        assert "% is append-only" in body.fetchone()[0]


def test_every_tenant_table_has_rls_and_a_policy(scratch_db):
    with psycopg.connect(scratch_db) as db:
        missing = db.execute(
            """
            select c.relname from pg_class c
            join pg_namespace n on n.oid = c.relnamespace and n.nspname = 'erp'
            join information_schema.columns col
              on col.table_schema = 'erp' and col.table_name = c.relname
             and col.column_name = 'tenant_id'
            where c.relkind = 'r'
              and (not c.relrowsecurity
                   or not exists (select 1 from pg_policy p where p.polrelid = c.oid))
            """
        ).fetchall()
        assert missing == []


def test_upgrade_is_idempotent_and_round_trips(scratch_db):
    config = alembic_config()
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    with psycopg.connect(scratch_db) as db:
        assert db.execute("select to_regclass('erp.tenant')").fetchone() == (None,)
    command.upgrade(config, "head")
    with psycopg.connect(scratch_db) as db:
        assert db.execute("select to_regclass('erp.tenant')").fetchone() != (None,)


def test_app_role_cannot_see_or_write_another_tenants_rows(scratch_db):
    tenant_a, tenant_b = str(uuid4()), str(uuid4())
    with psycopg.connect(scratch_db, autocommit=True) as owner:
        owner.execute(
            f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' "
            "NOSUPERUSER NOBYPASSRLS IN ROLE heyyerp_app_rw"
        )
        for tenant_id, code in ((tenant_a, "a"), (tenant_b, "b")):
            owner.execute("insert into erp.tenant(tenant_id, code, name) values (%s, %s, %s)",
                          (tenant_id, code, code))
            owner.execute("insert into erp.unit(tenant_id, code, name) values (%s, 'P1', 'Plant')",
                          (tenant_id,))

    with psycopg.connect(url_for(SCRATCH_DB, APP_ROLE, APP_PASSWORD)) as app:
        def scope(tenant_id):
            app.execute("select set_config('app.tenant_id', %s, false)", (tenant_id or "",))

        scope(None)
        assert app.execute("select count(*) from erp.unit").fetchone() == (0,)

        scope(tenant_a)
        assert app.execute("select count(*) from erp.unit").fetchone() == (1,)
        assert app.execute("select tenant_id::text from erp.unit").fetchone() == (tenant_a,)

        # Another tenant's row is invisible, so UPDATE/DELETE touch nothing.
        update = app.execute("update erp.unit set name = 'x' where tenant_id = %s", (tenant_b,))
        delete = app.execute("delete from erp.unit where tenant_id = %s", (tenant_b,))
        assert (update.rowcount, delete.rowcount) == (0, 0)

        # Writing a row for another tenant is rejected by the policy.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app.execute(
                "insert into erp.unit(tenant_id, code, name) values (%s, 'P2', 'x')", (tenant_b,)
            )
        app.rollback()

        # The login cannot switch RLS off, and does not bypass it.
        scope(tenant_a)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app.execute("alter table erp.unit disable row level security")
        app.rollback()
        flags = app.execute(
            "select rolsuper or rolbypassrls from pg_roles where rolname = current_user"
        )
        assert flags.fetchone() == (False,)
