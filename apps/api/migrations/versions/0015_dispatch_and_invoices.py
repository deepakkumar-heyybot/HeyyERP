"""Phase 9, part 2: dispatch (pick, confirm) and customer invoices.

* dispatch: status check, vehicle and transport details, who confirmed it.
* customer_invoice: due date, status check, and only one live invoice per dispatch.
"""

from alembic import op

revision = "0015"
down_revision = "0014"

UPGRADE = """
ALTER TABLE erp.dispatch
    ADD COLUMN vehicle_no text, ADD COLUMN transporter text, ADD COLUMN notes text,
    ADD COLUMN confirmed_by uuid, ADD COLUMN store_id uuid,
    ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE erp.dispatch
    ADD CONSTRAINT fk_dispatch_confirmed_by FOREIGN KEY (tenant_id, confirmed_by)
        REFERENCES erp.app_user (tenant_id, user_id),
    ADD CONSTRAINT fk_dispatch_store FOREIGN KEY (tenant_id, store_id)
        REFERENCES erp.store (tenant_id, store_id),
    ADD CONSTRAINT ck_dispatch_status CHECK (status IN ('DRAFT', 'DISPATCHED', 'CANCELLED'));
CREATE INDEX ix_dispatch_so ON erp.dispatch (tenant_id, so_id);

ALTER TABLE erp.customer_invoice ADD COLUMN due_date date;
ALTER TABLE erp.customer_invoice
    ADD CONSTRAINT ck_customer_invoice_status CHECK (status IN ('DRAFT', 'ISSUED', 'CANCELLED'));
CREATE UNIQUE INDEX ux_customer_invoice_dispatch
    ON erp.customer_invoice (tenant_id, dispatch_id) WHERE status <> 'CANCELLED';
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP INDEX erp.ux_customer_invoice_dispatch")
    op.execute("ALTER TABLE erp.customer_invoice DROP CONSTRAINT ck_customer_invoice_status")
    op.execute("ALTER TABLE erp.customer_invoice DROP COLUMN due_date")
    op.execute("DROP INDEX erp.ix_dispatch_so")
    op.execute("ALTER TABLE erp.dispatch DROP CONSTRAINT ck_dispatch_status, "
               "DROP CONSTRAINT fk_dispatch_store, DROP CONSTRAINT fk_dispatch_confirmed_by")
    op.execute("ALTER TABLE erp.dispatch DROP COLUMN created_at, DROP COLUMN store_id, "
               "DROP COLUMN confirmed_by, DROP COLUMN notes, DROP COLUMN transporter, "
               "DROP COLUMN vehicle_no")
