"""Grants for the API's non-owner login.

Creates the NOLOGIN group role heyyerp_app_rw (no secrets in migrations). Create the actual
login per environment with:
  CREATE ROLE heyyerp_app LOGIN PASSWORD '...' NOSUPERUSER NOBYPASSRLS IN ROLE heyyerp_app_rw;
The login must not own the tables, otherwise PostgreSQL skips row-level security for it.
"""

from alembic import op

revision = "0002"
down_revision = "0001"

UPGRADE = """
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'heyyerp_app_rw') THEN
    CREATE ROLE heyyerp_app_rw NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;
GRANT USAGE ON SCHEMA erp TO heyyerp_app_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA erp TO heyyerp_app_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA erp
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO heyyerp_app_rw;
DO $$ BEGIN
  -- The auth schema belongs to the Better Auth service; its tables may not exist yet.
  IF to_regclass('auth.member') IS NOT NULL AND to_regclass('auth.organization') IS NOT NULL THEN
    GRANT USAGE ON SCHEMA auth TO heyyerp_app_rw;
    GRANT SELECT ON auth.member, auth.organization TO heyyerp_app_rw;
  END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA erp FROM heyyerp_app_rw")
    op.execute("REVOKE USAGE ON SCHEMA erp FROM heyyerp_app_rw")
