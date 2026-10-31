"""Phase 10, part 4b: production costing, cost of sales and inter-unit postings.

* item.standard_cost: the cost a company may fix per item (used when the cost method is STANDARD).
* lot_consumption.cost / returned_cost: what the material of a production lot cost when issued, and
  the part handed back to stores.
* compound_batch.material_cost: the raw material cost weighed into a batch.
* prod_lot: material cost, conversion cost (overhead), the finished-goods unit cost and its basis.
* dispatch.cost_amount and the journals that booked cost of sales (on dispatch or on invoice).
* inter_unit_invoice: the journals of the issuing and the receiving plant.
"""

from alembic import op

revision = "0022"
down_revision = "0021"

UPGRADE = """
ALTER TABLE erp.item ADD COLUMN standard_cost numeric(18,6) CHECK (standard_cost >= 0);
ALTER TABLE erp.lot_consumption
    ADD COLUMN cost numeric(18,2) NOT NULL DEFAULT 0 CHECK (cost >= 0),
    ADD COLUMN returned_cost numeric(18,2) NOT NULL DEFAULT 0 CHECK (returned_cost >= 0);
ALTER TABLE erp.compound_batch
    ADD COLUMN material_cost numeric(18,2) NOT NULL DEFAULT 0 CHECK (material_cost >= 0);
ALTER TABLE erp.prod_lot
    ADD COLUMN material_cost numeric(18,2) NOT NULL DEFAULT 0 CHECK (material_cost >= 0),
    ADD COLUMN conversion_cost numeric(18,2) NOT NULL DEFAULT 0 CHECK (conversion_cost >= 0),
    ADD COLUMN unit_cost numeric(18,6) CHECK (unit_cost >= 0),
    ADD COLUMN cost_basis text CHECK (cost_basis IN ('ACTUAL', 'STANDARD'));
ALTER TABLE erp.dispatch
    ADD COLUMN cost_amount numeric(18,2) NOT NULL DEFAULT 0 CHECK (cost_amount >= 0),
    ADD COLUMN cogs_journal_id uuid,
    ADD CONSTRAINT fk_dispatch_cogs FOREIGN KEY (tenant_id, cogs_journal_id)
        REFERENCES erp.journal (tenant_id, journal_id);
ALTER TABLE erp.customer_invoice
    ADD COLUMN cogs_journal_id uuid,
    ADD CONSTRAINT fk_customer_invoice_cogs FOREIGN KEY (tenant_id, cogs_journal_id)
        REFERENCES erp.journal (tenant_id, journal_id);
ALTER TABLE erp.inter_unit_invoice
    ADD COLUMN from_journal_id uuid, ADD COLUMN to_journal_id uuid,
    ADD CONSTRAINT fk_iu_from_journal FOREIGN KEY (tenant_id, from_journal_id)
        REFERENCES erp.journal (tenant_id, journal_id),
    ADD CONSTRAINT fk_iu_to_journal FOREIGN KEY (tenant_id, to_journal_id)
        REFERENCES erp.journal (tenant_id, journal_id);
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("ALTER TABLE erp.inter_unit_invoice DROP CONSTRAINT fk_iu_to_journal, "
               "DROP CONSTRAINT fk_iu_from_journal, DROP COLUMN to_journal_id, "
               "DROP COLUMN from_journal_id")
    op.execute("ALTER TABLE erp.customer_invoice DROP CONSTRAINT fk_customer_invoice_cogs, "
               "DROP COLUMN cogs_journal_id")
    op.execute("ALTER TABLE erp.dispatch DROP CONSTRAINT fk_dispatch_cogs, "
               "DROP COLUMN cogs_journal_id, DROP COLUMN cost_amount")
    op.execute("ALTER TABLE erp.prod_lot DROP COLUMN cost_basis, DROP COLUMN unit_cost, "
               "DROP COLUMN conversion_cost, DROP COLUMN material_cost")
    op.execute("ALTER TABLE erp.compound_batch DROP COLUMN material_cost")
    op.execute("ALTER TABLE erp.lot_consumption DROP COLUMN returned_cost, DROP COLUMN cost")
    op.execute("ALTER TABLE erp.item DROP COLUMN standard_cost")
