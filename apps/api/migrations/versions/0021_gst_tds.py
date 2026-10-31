"""Phase 10, part 4a: GST return data and TDS.

* gst_return_data: one row per plant, month and return type (GSTR1, GSTR3B, GSTR2B, TDS); a row is
  PREPARED (rebuilt on request) or FILED (kept as it was filed).
* tds_entry: tax deducted on a supplier bill, with the section, rate, the journal that booked it
  and, once paid to the government, the challan.
"""

from alembic import op

revision = "0021"
down_revision = "0020"

UPGRADE = """
ALTER TABLE erp.gst_return_data
    ADD COLUMN generated_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN filed_reference text,
    ADD CONSTRAINT ck_gst_return_status CHECK (status IN ('PREPARED', 'FILED'));
CREATE UNIQUE INDEX ux_gst_return_data ON erp.gst_return_data (unit_id, period_id, return_type);

ALTER TABLE erp.tds_entry
    ADD COLUMN rate numeric(7,4) NOT NULL DEFAULT 0 CHECK (rate >= 0),
    ADD COLUMN bill_id uuid, ADD COLUMN journal_id uuid, ADD COLUMN deposit_journal_id uuid,
    ADD COLUMN challan_no text, ADD COLUMN deposited_on date,
    ADD COLUMN created_at timestamptz NOT NULL DEFAULT now(),
    ADD CONSTRAINT ck_tds_entry_status CHECK (status IN ('OPEN', 'DEPOSITED', 'CANCELLED')),
    ADD CONSTRAINT fk_tds_entry_bill FOREIGN KEY (tenant_id, bill_id)
        REFERENCES erp.supplier_bill (tenant_id, bill_id),
    ADD CONSTRAINT fk_tds_entry_journal FOREIGN KEY (tenant_id, journal_id)
        REFERENCES erp.journal (tenant_id, journal_id),
    ADD CONSTRAINT fk_tds_entry_deposit FOREIGN KEY (tenant_id, deposit_journal_id)
        REFERENCES erp.journal (tenant_id, journal_id);
CREATE UNIQUE INDEX ux_tds_entry_bill ON erp.tds_entry (bill_id)
    WHERE bill_id IS NOT NULL AND status <> 'CANCELLED';
CREATE INDEX ix_tds_entry_period ON erp.tds_entry (unit_id, period_id, status);
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP INDEX erp.ix_tds_entry_period")
    op.execute("DROP INDEX erp.ux_tds_entry_bill")
    op.execute("ALTER TABLE erp.tds_entry DROP CONSTRAINT fk_tds_entry_deposit, "
               "DROP CONSTRAINT fk_tds_entry_journal, DROP CONSTRAINT fk_tds_entry_bill, "
               "DROP CONSTRAINT ck_tds_entry_status, DROP COLUMN created_at, "
               "DROP COLUMN deposited_on, DROP COLUMN challan_no, "
               "DROP COLUMN deposit_journal_id, DROP COLUMN journal_id, DROP COLUMN bill_id, "
               "DROP COLUMN rate")
    op.execute("DROP INDEX erp.ux_gst_return_data")
    op.execute("ALTER TABLE erp.gst_return_data DROP CONSTRAINT ck_gst_return_status, "
               "DROP COLUMN filed_reference, DROP COLUMN generated_at")
