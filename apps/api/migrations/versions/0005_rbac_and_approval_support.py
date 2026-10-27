"""Support for Phase 3: department head role, named approval rules, read access to sign-ins.

* department.head_role_id: which role approves for a department (a DEPARTMENT_HEAD step).
* approval_rule.name: administrators refer to rules as R-PO-1 etc.
* permission is a global catalog written only by the seed command, never by the API login.
* The API reads sign-in id / email / name (nothing else) to link an ERP profile to an account.
"""

from alembic import op

revision = "0005"
down_revision = "0004"

UPGRADE = """
ALTER TABLE erp.department ADD COLUMN head_role_id uuid;
ALTER TABLE erp.department ADD CONSTRAINT fk_department_head_role_id_role
  FOREIGN KEY (tenant_id, head_role_id) REFERENCES erp.role (tenant_id, role_id);
ALTER TABLE erp.approval_rule ADD COLUMN name text;
CREATE UNIQUE INDEX uq_approval_rule_tenant_name ON erp.approval_rule (tenant_id, name)
  WHERE name IS NOT NULL;
REVOKE INSERT, UPDATE, DELETE ON erp.permission FROM heyyerp_app_rw;
DO $$ BEGIN
  IF to_regclass('auth.user') IS NOT NULL THEN
    GRANT USAGE ON SCHEMA auth TO heyyerp_app_rw;
    GRANT SELECT (id, email, name) ON auth."user" TO heyyerp_app_rw;
  END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("GRANT INSERT, UPDATE, DELETE ON erp.permission TO heyyerp_app_rw")
    op.execute("DROP INDEX erp.uq_approval_rule_tenant_name")
    op.execute("ALTER TABLE erp.approval_rule DROP COLUMN name")
    op.execute("ALTER TABLE erp.department DROP CONSTRAINT fk_department_head_role_id_role")
    op.execute("ALTER TABLE erp.department DROP COLUMN head_role_id")
