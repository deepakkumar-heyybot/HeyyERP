"""Phase 6 support: stock issues, reservations, count progress, transfer cost, status checks.

* stock_issue: the document behind an issue of stock to production (the ledger rows are its lines).
* stock_reservation: who is holding stock and for what, so a reservation can be released or used.
* stock_count_line.counted_at: a line is "counted" only once someone entered a quantity.
* stock_transfer_line.unit_cost: the cost carried between plants (inter-unit transfers).
* CHECK constraints on the free-text status columns of GRN, transfer and count.
"""

from alembic import op

revision = "0008"
down_revision = "0007"

UPGRADE = """
CREATE TABLE erp.stock_issue (
    issue_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    unit_id uuid NOT NULL,
    store_id uuid NOT NULL,
    issue_no text NOT NULL,
    purpose text NOT NULL,
    reference text,
    issued_by uuid,
    issued_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, issue_id),
    UNIQUE (tenant_id, issue_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES erp.unit (tenant_id, unit_id),
    FOREIGN KEY (tenant_id, store_id) REFERENCES erp.store (tenant_id, store_id),
    FOREIGN KEY (tenant_id, issued_by) REFERENCES erp.app_user (tenant_id, user_id)
);

CREATE TABLE erp.stock_reservation (
    reservation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    unit_id uuid NOT NULL,
    store_id uuid NOT NULL,
    item_id uuid NOT NULL,
    lot_id uuid NOT NULL,
    quantity numeric(18,3) NOT NULL CHECK (quantity > 0),
    reference_type text NOT NULL,
    reference text NOT NULL,
    status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RELEASED', 'CONSUMED')),
    created_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, reservation_id),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES erp.unit (tenant_id, unit_id),
    FOREIGN KEY (tenant_id, store_id) REFERENCES erp.store (tenant_id, store_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES erp.item (tenant_id, item_id),
    FOREIGN KEY (tenant_id, lot_id) REFERENCES erp.lot (tenant_id, lot_id),
    FOREIGN KEY (tenant_id, created_by) REFERENCES erp.app_user (tenant_id, user_id)
);
CREATE INDEX ix_stock_reservation_ref
    ON erp.stock_reservation (tenant_id, reference_type, reference);
CREATE INDEX ix_stock_reservation_lot ON erp.stock_reservation (tenant_id, lot_id, status);

ALTER TABLE erp.stock_issue ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.stock_issue
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
ALTER TABLE erp.stock_reservation ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.stock_reservation
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON erp.stock_issue, erp.stock_reservation
    TO heyyerp_app_rw;

ALTER TABLE erp.stock_count_line ADD COLUMN counted_at timestamptz;
ALTER TABLE erp.stock_transfer_line ADD COLUMN unit_cost numeric(18,6)
    CHECK (unit_cost IS NULL OR unit_cost >= 0);

ALTER TABLE erp.grn ADD CONSTRAINT ck_grn_status CHECK (status IN
    ('DRAFT', 'POSTED', 'CANCELLED'));
ALTER TABLE erp.stock_transfer ADD CONSTRAINT ck_stock_transfer_status CHECK (status IN
    ('DRAFT', 'DISPATCHED', 'RECEIVED', 'CANCELLED'));
ALTER TABLE erp.stock_count ADD CONSTRAINT ck_stock_count_status CHECK (status IN
    ('DRAFT', 'PENDING_APPROVAL', 'POSTED', 'RECOUNT', 'CANCELLED'));
ALTER TABLE erp.purchase_return ADD CONSTRAINT ck_purchase_return_status CHECK (status IN
    ('DRAFT', 'DEBIT_NOTED', 'CLOSED'));
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("ALTER TABLE erp.purchase_return DROP CONSTRAINT ck_purchase_return_status")
    op.execute("ALTER TABLE erp.stock_count DROP CONSTRAINT ck_stock_count_status")
    op.execute("ALTER TABLE erp.stock_transfer DROP CONSTRAINT ck_stock_transfer_status")
    op.execute("ALTER TABLE erp.grn DROP CONSTRAINT ck_grn_status")
    op.execute("ALTER TABLE erp.stock_transfer_line DROP COLUMN unit_cost")
    op.execute("ALTER TABLE erp.stock_count_line DROP COLUMN counted_at")
    op.execute("DROP TABLE erp.stock_reservation")
    op.execute("DROP TABLE erp.stock_issue")
