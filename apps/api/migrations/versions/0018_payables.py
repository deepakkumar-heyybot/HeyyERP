"""Phase 10, part 2: supplier bills with 3-way match, payments and vendor bank accounts.

* supplier_bill: due date (payment term, MSME limit), status, who and when it was booked, the
  journal of its booking, the amount paid so far and why it needs approval.
* supplier_bill_line: which PO line and goods-receipt line it bills, the expense account for
  lines that match nothing, and the line's match result.
* payment: who created and paid it, the journal, the bank reference and the payment run it
  belongs to; one allocation per bill and payment.
* vendor_bank_account: where a vendor is paid (kept apart from the vendor master).
"""

from alembic import op

revision = "0018"
down_revision = "0017"

UPGRADE = """
ALTER TABLE erp.supplier_bill
    ADD COLUMN due_date date, ADD COLUMN po_id uuid, ADD COLUMN created_by uuid,
    ADD COLUMN booked_at timestamptz, ADD COLUMN journal_id uuid,
    ADD COLUMN reversal_journal_id uuid, ADD COLUMN exception_note text,
    ADD COLUMN paid_amount numeric(18,2) NOT NULL DEFAULT 0 CHECK (paid_amount >= 0);
ALTER TABLE erp.supplier_bill ALTER COLUMN status SET DEFAULT 'MATCHED';
ALTER TABLE erp.supplier_bill
    ADD CONSTRAINT fk_supplier_bill_po FOREIGN KEY (tenant_id, po_id)
        REFERENCES erp.purchase_order (tenant_id, po_id),
    ADD CONSTRAINT fk_supplier_bill_created_by FOREIGN KEY (tenant_id, created_by)
        REFERENCES erp.app_user (tenant_id, user_id),
    ADD CONSTRAINT fk_supplier_bill_journal FOREIGN KEY (tenant_id, journal_id)
        REFERENCES erp.journal (tenant_id, journal_id),
    ADD CONSTRAINT fk_supplier_bill_reversal FOREIGN KEY (tenant_id, reversal_journal_id)
        REFERENCES erp.journal (tenant_id, journal_id),
    ADD CONSTRAINT ck_supplier_bill_status CHECK (status IN
        ('MATCHED', 'EXCEPTION', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'BOOKED',
         'PARTIALLY_PAID', 'PAID', 'CANCELLED')),
    ADD CONSTRAINT ck_supplier_bill_paid CHECK (paid_amount <= taxable_amount + tax_amount);
CREATE INDEX ix_supplier_bill_due ON erp.supplier_bill (tenant_id, unit_id, due_date)
    WHERE status IN ('BOOKED', 'PARTIALLY_PAID');

ALTER TABLE erp.supplier_bill_line
    ADD COLUMN po_line_id uuid, ADD COLUMN grn_line_id uuid, ADD COLUMN expense_account_id uuid,
    ADD COLUMN match_status text NOT NULL DEFAULT 'NONE' CHECK (match_status IN
        ('NONE', 'QTY', 'RATE', 'QTY_RATE', 'UNMATCHED'));
ALTER TABLE erp.supplier_bill_line
    ADD CONSTRAINT fk_bill_line_po_line FOREIGN KEY (tenant_id, po_line_id)
        REFERENCES erp.po_line (tenant_id, po_line_id),
    ADD CONSTRAINT fk_bill_line_grn_line FOREIGN KEY (tenant_id, grn_line_id)
        REFERENCES erp.grn_line (tenant_id, grn_line_id),
    ADD CONSTRAINT fk_bill_line_expense FOREIGN KEY (tenant_id, expense_account_id)
        REFERENCES erp.account (tenant_id, account_id);
CREATE INDEX ix_bill_match_grn ON erp.bill_match (tenant_id, grn_line_id);

ALTER TABLE erp.bill_match ADD CONSTRAINT ck_bill_match_status CHECK (exception_status IN
    ('NONE', 'QTY', 'RATE', 'QTY_RATE'));

ALTER TABLE erp.payment
    ADD COLUMN created_by uuid, ADD COLUMN paid_by uuid, ADD COLUMN paid_at timestamptz,
    ADD COLUMN journal_id uuid, ADD COLUMN bank_reference text, ADD COLUMN run_id uuid,
    ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE erp.payment ALTER COLUMN status SET DEFAULT 'PROPOSED';
ALTER TABLE erp.payment
    ADD CONSTRAINT fk_payment_created_by FOREIGN KEY (tenant_id, created_by)
        REFERENCES erp.app_user (tenant_id, user_id),
    ADD CONSTRAINT fk_payment_paid_by FOREIGN KEY (tenant_id, paid_by)
        REFERENCES erp.app_user (tenant_id, user_id),
    ADD CONSTRAINT fk_payment_journal FOREIGN KEY (tenant_id, journal_id)
        REFERENCES erp.journal (tenant_id, journal_id),
    ADD CONSTRAINT ck_payment_status CHECK (status IN ('PROPOSED', 'PAID', 'CANCELLED'));
CREATE INDEX ix_payment_run ON erp.payment (tenant_id, run_id);
CREATE UNIQUE INDEX ux_payment_allocation_bill ON erp.payment_allocation (payment_id, bill_id)
    WHERE bill_id IS NOT NULL;

CREATE TABLE erp.vendor_bank_account (
    vendor_id uuid NOT NULL,
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    account_holder text NOT NULL,
    account_no text NOT NULL,
    ifsc text NOT NULL CHECK (ifsc ~ '^[A-Z]{4}0[A-Z0-9]{6}$'),
    bank_name text,
    updated_by uuid,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (vendor_id),
    FOREIGN KEY (tenant_id, vendor_id) REFERENCES erp.vendor (tenant_id, vendor_id),
    FOREIGN KEY (tenant_id, updated_by) REFERENCES erp.app_user (tenant_id, user_id)
);
ALTER TABLE erp.vendor_bank_account ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.vendor_bank_account
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON erp.vendor_bank_account TO heyyerp_app_rw;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP TABLE erp.vendor_bank_account")
    op.execute("DROP INDEX erp.ux_payment_allocation_bill")
    op.execute("DROP INDEX erp.ix_payment_run")
    op.execute("ALTER TABLE erp.payment DROP CONSTRAINT ck_payment_status, "
               "DROP CONSTRAINT fk_payment_journal, DROP CONSTRAINT fk_payment_paid_by, "
               "DROP CONSTRAINT fk_payment_created_by")
    op.execute("ALTER TABLE erp.payment DROP COLUMN created_at, DROP COLUMN run_id, "
               "DROP COLUMN bank_reference, DROP COLUMN journal_id, DROP COLUMN paid_at, "
               "DROP COLUMN paid_by, DROP COLUMN created_by")
    op.execute("ALTER TABLE erp.bill_match DROP CONSTRAINT ck_bill_match_status")
    op.execute("DROP INDEX erp.ix_bill_match_grn")
    op.execute("ALTER TABLE erp.supplier_bill_line DROP CONSTRAINT fk_bill_line_expense, "
               "DROP CONSTRAINT fk_bill_line_grn_line, DROP CONSTRAINT fk_bill_line_po_line")
    op.execute("ALTER TABLE erp.supplier_bill_line DROP COLUMN match_status, "
               "DROP COLUMN expense_account_id, DROP COLUMN grn_line_id, DROP COLUMN po_line_id")
    op.execute("DROP INDEX erp.ix_supplier_bill_due")
    op.execute("ALTER TABLE erp.supplier_bill DROP CONSTRAINT ck_supplier_bill_paid, "
               "DROP CONSTRAINT ck_supplier_bill_status, DROP CONSTRAINT fk_supplier_bill_reversal, "
               "DROP CONSTRAINT fk_supplier_bill_journal, "
               "DROP CONSTRAINT fk_supplier_bill_created_by, DROP CONSTRAINT fk_supplier_bill_po")
    op.execute("ALTER TABLE erp.supplier_bill ALTER COLUMN status SET DEFAULT 'DRAFT'")
    op.execute("ALTER TABLE erp.supplier_bill DROP COLUMN paid_amount, DROP COLUMN exception_note, "
               "DROP COLUMN reversal_journal_id, DROP COLUMN journal_id, DROP COLUMN booked_at, "
               "DROP COLUMN created_by, DROP COLUMN po_id, DROP COLUMN due_date")
    op.execute("ALTER TABLE erp.payment ALTER COLUMN status SET DEFAULT 'PROPOSED'")
