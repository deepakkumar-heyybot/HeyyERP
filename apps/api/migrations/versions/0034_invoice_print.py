"""Phase 12: what the company prints on its tax invoices.

* print_profile: per plant, the legal name, bank details, terms, signatory and footer, written by
  the company itself (nothing is fixed in the program).
"""

from alembic import op

revision = "0034"
down_revision = "0033"

UPGRADE = """
CREATE TABLE erp.print_profile (
    print_profile_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    unit_id uuid NOT NULL,
    legal_name text,
    bank_details text,
    terms text,
    signatory text,
    footer text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, unit_id),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES erp.unit (tenant_id, unit_id)
);
ALTER TABLE erp.print_profile ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.print_profile
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON erp.print_profile TO heyyerp_app_rw;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP TABLE erp.print_profile")
