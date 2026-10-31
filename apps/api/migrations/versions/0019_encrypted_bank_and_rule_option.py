"""Bank account numbers are stored encrypted; approval rules can keep the requester's own step.

* vendor_bank_account: the account number is kept only as ciphertext (account_no_enc) plus its last
  four digits for display. The plaintext column is dropped. The feature is days old and held no
  real data; if any plaintext row exists the migration stops and says so, rather than losing it.
* approval_rule.skip_requester_role: by default (true) steps held by the requester's own role are
  skipped (BRD section 5). A company that wants a second person of the same role to approve sets it
  to false on that rule.
"""

from alembic import op

revision = "0019"
down_revision = "0018"

UPGRADE = """
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM erp.vendor_bank_account) THEN
        RAISE EXCEPTION 'erp.vendor_bank_account holds plaintext account numbers; '
                        'note them, delete the rows and re-enter them after this migration';
    END IF;
END $$;
ALTER TABLE erp.vendor_bank_account DROP COLUMN account_no;
ALTER TABLE erp.vendor_bank_account
    ADD COLUMN account_no_enc text NOT NULL,
    ADD COLUMN account_last4 text NOT NULL CHECK (account_last4 ~ '^[0-9]{1,4}$');

ALTER TABLE erp.approval_rule
    ADD COLUMN skip_requester_role boolean NOT NULL DEFAULT true;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("ALTER TABLE erp.approval_rule DROP COLUMN skip_requester_role")
    op.execute("DELETE FROM erp.vendor_bank_account")
    op.execute("ALTER TABLE erp.vendor_bank_account DROP COLUMN account_last4, "
               "DROP COLUMN account_no_enc")
    op.execute("ALTER TABLE erp.vendor_bank_account ADD COLUMN account_no text NOT NULL")
