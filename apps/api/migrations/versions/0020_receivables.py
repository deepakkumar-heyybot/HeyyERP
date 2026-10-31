"""Phase 10, part 3: receivables. Invoices carry their journal and what has been paid.

* customer_invoice: the journal that booked it, the journal that reversed it, and the amount
  received against it so far (never more than the invoice).
* payment: customer receipts use status RECEIVED (supplier payments keep PROPOSED/PAID).
* payment_allocation: one allocation per receipt and invoice.
"""

from alembic import op

revision = "0020"
down_revision = "0019"

UPGRADE = """
ALTER TABLE erp.customer_invoice
    ADD COLUMN journal_id uuid, ADD COLUMN reversal_journal_id uuid,
    ADD COLUMN paid_amount numeric(18,2) NOT NULL DEFAULT 0 CHECK (paid_amount >= 0);
ALTER TABLE erp.customer_invoice
    ADD CONSTRAINT fk_customer_invoice_journal FOREIGN KEY (tenant_id, journal_id)
        REFERENCES erp.journal (tenant_id, journal_id),
    ADD CONSTRAINT fk_customer_invoice_reversal FOREIGN KEY (tenant_id, reversal_journal_id)
        REFERENCES erp.journal (tenant_id, journal_id),
    ADD CONSTRAINT ck_customer_invoice_paid CHECK (paid_amount <= taxable_amount + tax_amount);
CREATE INDEX ix_customer_invoice_open ON erp.customer_invoice (tenant_id, unit_id, customer_id, due_date)
    WHERE status = 'ISSUED';

ALTER TABLE erp.payment DROP CONSTRAINT ck_payment_status;
ALTER TABLE erp.payment ADD CONSTRAINT ck_payment_status CHECK (status IN
    ('PROPOSED', 'PAID', 'CANCELLED', 'RECEIVED'));
CREATE UNIQUE INDEX ux_payment_allocation_invoice ON erp.payment_allocation (payment_id, invoice_id)
    WHERE invoice_id IS NOT NULL;
CREATE INDEX ix_payment_allocation_invoice ON erp.payment_allocation (tenant_id, invoice_id);
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP INDEX erp.ix_payment_allocation_invoice")
    op.execute("DROP INDEX erp.ux_payment_allocation_invoice")
    op.execute("DELETE FROM erp.payment WHERE status = 'RECEIVED'")
    op.execute("ALTER TABLE erp.payment DROP CONSTRAINT ck_payment_status")
    op.execute("ALTER TABLE erp.payment ADD CONSTRAINT ck_payment_status CHECK (status IN "
               "('PROPOSED', 'PAID', 'CANCELLED'))")
    op.execute("DROP INDEX erp.ix_customer_invoice_open")
    op.execute("ALTER TABLE erp.customer_invoice DROP CONSTRAINT ck_customer_invoice_paid, "
               "DROP CONSTRAINT fk_customer_invoice_reversal, "
               "DROP CONSTRAINT fk_customer_invoice_journal")
    op.execute("ALTER TABLE erp.customer_invoice DROP COLUMN paid_amount, "
               "DROP COLUMN reversal_journal_id, DROP COLUMN journal_id")
