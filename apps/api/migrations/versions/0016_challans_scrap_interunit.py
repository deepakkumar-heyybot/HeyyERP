"""Phase 9, part 3: challans with due-back tracking, scrap sales with approval, inter-unit invoices.

* challan: store, reason, who raised it, status check.
* scrap_sale: draft state, who raised it, invoice number and date, status check.
* inter_unit_invoice: value at transfer cost, issue date, who raised it, status check and one
  live invoice per transfer.
"""

from alembic import op

revision = "0016"
down_revision = "0015"

UPGRADE = """
ALTER TABLE erp.challan
    ADD COLUMN store_id uuid, ADD COLUMN reason text, ADD COLUMN created_by uuid;
ALTER TABLE erp.challan
    ADD CONSTRAINT fk_challan_store FOREIGN KEY (tenant_id, store_id)
        REFERENCES erp.store (tenant_id, store_id),
    ADD CONSTRAINT fk_challan_created_by FOREIGN KEY (tenant_id, created_by)
        REFERENCES erp.app_user (tenant_id, user_id),
    ADD CONSTRAINT ck_challan_status CHECK (status IN ('OUT', 'PARTIALLY_RETURNED', 'RETURNED'));
CREATE INDEX ix_challan_due ON erp.challan (tenant_id, due_back) WHERE status <> 'RETURNED';

ALTER TABLE erp.scrap_sale
    ADD COLUMN created_by uuid, ADD COLUMN invoice_no text, ADD COLUMN invoiced_at timestamptz;
ALTER TABLE erp.scrap_sale ALTER COLUMN status SET DEFAULT 'DRAFT';
ALTER TABLE erp.scrap_sale
    ADD CONSTRAINT fk_scrap_sale_created_by FOREIGN KEY (tenant_id, created_by)
        REFERENCES erp.app_user (tenant_id, user_id),
    ADD CONSTRAINT ck_scrap_sale_status CHECK (status IN
        ('DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'INVOICED', 'CANCELLED'));

ALTER TABLE erp.inter_unit_invoice
    ADD COLUMN taxable_amount numeric(18,2) NOT NULL DEFAULT 0 CHECK (taxable_amount >= 0),
    ADD COLUMN tax_amount numeric(18,2) NOT NULL DEFAULT 0 CHECK (tax_amount >= 0),
    ADD COLUMN issue_date date, ADD COLUMN irn_ack_no text, ADD COLUMN created_by uuid;
ALTER TABLE erp.inter_unit_invoice
    ADD CONSTRAINT fk_inter_unit_created_by FOREIGN KEY (tenant_id, created_by)
        REFERENCES erp.app_user (tenant_id, user_id),
    ADD CONSTRAINT ck_inter_unit_status CHECK (status IN ('DRAFT', 'ISSUED', 'CANCELLED'));
CREATE UNIQUE INDEX ux_inter_unit_transfer
    ON erp.inter_unit_invoice (tenant_id, transfer_id) WHERE status <> 'CANCELLED';
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP INDEX erp.ux_inter_unit_transfer")
    op.execute("ALTER TABLE erp.inter_unit_invoice DROP CONSTRAINT ck_inter_unit_status, "
               "DROP CONSTRAINT fk_inter_unit_created_by")
    op.execute("ALTER TABLE erp.inter_unit_invoice DROP COLUMN created_by, "
               "DROP COLUMN irn_ack_no, DROP COLUMN issue_date, DROP COLUMN tax_amount, "
               "DROP COLUMN taxable_amount")
    op.execute("ALTER TABLE erp.scrap_sale DROP CONSTRAINT ck_scrap_sale_status, "
               "DROP CONSTRAINT fk_scrap_sale_created_by")
    op.execute("ALTER TABLE erp.scrap_sale ALTER COLUMN status SET DEFAULT 'PENDING_APPROVAL'")
    op.execute("ALTER TABLE erp.scrap_sale DROP COLUMN invoiced_at, DROP COLUMN invoice_no, "
               "DROP COLUMN created_by")
    op.execute("DROP INDEX erp.ix_challan_due")
    op.execute("ALTER TABLE erp.challan DROP CONSTRAINT ck_challan_status, "
               "DROP CONSTRAINT fk_challan_created_by, DROP CONSTRAINT fk_challan_store")
    op.execute("ALTER TABLE erp.challan DROP COLUMN created_by, DROP COLUMN reason, "
               "DROP COLUMN store_id")
