"""Phase 7, part 3: shift losses, rework and returns of unused material.

* shift_loss: rejects by reason and downtime by reason behind each shift entry (the entry keeps
  the totals, computed from these rows).
* rework_record: rejected pieces taken back into a lot, who and why.
* lot_consumption.returned_qty: raw material handed back to stores after a lot used less.
"""

from alembic import op

revision = "0011"
down_revision = "0010"

UPGRADE = """
CREATE TABLE erp.shift_loss (
    loss_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    shift_entry_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('REJECT', 'DOWNTIME')),
    reason text NOT NULL,
    quantity numeric(18,3) NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    minutes integer NOT NULL DEFAULT 0 CHECK (minutes >= 0),
    FOREIGN KEY (shift_entry_id) REFERENCES erp.shift_production (shift_entry_id),
    CHECK ((kind = 'REJECT' AND quantity > 0 AND minutes = 0)
        OR (kind = 'DOWNTIME' AND minutes > 0 AND quantity = 0))
);
CREATE INDEX ix_shift_loss_entry ON erp.shift_loss (tenant_id, shift_entry_id);

CREATE TABLE erp.rework_record (
    rework_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    prod_lot_id uuid NOT NULL,
    quantity numeric(18,3) NOT NULL CHECK (quantity > 0),
    reason text NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, prod_lot_id) REFERENCES erp.prod_lot (tenant_id, prod_lot_id),
    FOREIGN KEY (tenant_id, created_by) REFERENCES erp.app_user (tenant_id, user_id)
);
CREATE INDEX ix_rework_lot ON erp.rework_record (tenant_id, prod_lot_id);

ALTER TABLE erp.lot_consumption ADD COLUMN returned_qty numeric(18,3) NOT NULL DEFAULT 0
    CHECK (returned_qty >= 0 AND returned_qty <= quantity);

ALTER TABLE erp.shift_loss ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.shift_loss
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
ALTER TABLE erp.rework_record ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.rework_record
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON erp.shift_loss, erp.rework_record TO heyyerp_app_rw;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("ALTER TABLE erp.lot_consumption DROP COLUMN returned_qty")
    op.execute("DROP TABLE erp.rework_record")
    op.execute("DROP TABLE erp.shift_loss")
