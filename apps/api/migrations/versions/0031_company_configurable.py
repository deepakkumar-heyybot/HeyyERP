"""Company-configurable costs, GST on scrap, kaizen rewards and file attachments.

* scrap_sale: the taxable value and the GST worked out when it is invoiced.
* machine: the hourly machine rate and hourly labour cost used in production costing.
* kaizen: when the reward was paid.
* attachment: files (photos, PDFs) kept against a record, size-limited by a company rule.
"""

from alembic import op

revision = "0031"
down_revision = "0030"

UPGRADE = """
ALTER TABLE erp.scrap_sale
    ADD COLUMN taxable_amount numeric(18,2) NOT NULL DEFAULT 0 CHECK (taxable_amount >= 0),
    ADD COLUMN tax_amount numeric(18,2) NOT NULL DEFAULT 0 CHECK (tax_amount >= 0);

ALTER TABLE erp.machine
    ADD COLUMN hourly_rate numeric(18,2) NOT NULL DEFAULT 0 CHECK (hourly_rate >= 0),
    ADD COLUMN labour_per_hour numeric(18,2) NOT NULL DEFAULT 0 CHECK (labour_per_hour >= 0);

ALTER TABLE erp.kaizen ADD COLUMN reward_paid_on date, ADD COLUMN reward_paid_by uuid,
    ADD CONSTRAINT fk_kaizen_reward_by FOREIGN KEY (tenant_id, reward_paid_by)
        REFERENCES erp.app_user (tenant_id, user_id);

CREATE TABLE erp.attachment (
    attachment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    entity_type text NOT NULL CHECK (entity_type IN ('kaizen', 'breakdown')),
    entity_id uuid NOT NULL,
    file_name text NOT NULL,
    content_type text NOT NULL,
    size_bytes integer NOT NULL CHECK (size_bytes > 0),
    content bytea NOT NULL,
    active boolean NOT NULL DEFAULT true,
    uploaded_by uuid,
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, uploaded_by) REFERENCES erp.app_user (tenant_id, user_id)
);
CREATE INDEX ix_attachment_entity ON erp.attachment (tenant_id, entity_type, entity_id)
    WHERE active;
ALTER TABLE erp.attachment ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.attachment
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON erp.attachment TO heyyerp_app_rw;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP TABLE erp.attachment")
    op.execute("ALTER TABLE erp.kaizen DROP CONSTRAINT fk_kaizen_reward_by, "
               "DROP COLUMN reward_paid_by, DROP COLUMN reward_paid_on")
    op.execute("ALTER TABLE erp.machine DROP COLUMN labour_per_hour, DROP COLUMN hourly_rate")
    op.execute("ALTER TABLE erp.scrap_sale DROP COLUMN tax_amount, DROP COLUMN taxable_amount")
