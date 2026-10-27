"""Phase 7, part 2: work orders, production lots, consumption sources and packing to FG stock.

* lot_consumption can now name a compound batch or a metal production lot as its source (it could
  only name a stock lot): exactly one source per row, matching the consumption type.
* packing.fg_lot_id: the finished-goods stock lot that packing created.
* CHECK constraints on the work order and production lot statuses.
"""

from alembic import op

revision = "0010"
down_revision = "0009"

UPGRADE = """
ALTER TABLE erp.lot_consumption ALTER COLUMN source_lot_id DROP NOT NULL;
ALTER TABLE erp.lot_consumption ADD COLUMN source_prod_lot_id uuid;
ALTER TABLE erp.lot_consumption ADD CONSTRAINT fk_lot_consumption_source_prod_lot
    FOREIGN KEY (tenant_id, source_prod_lot_id) REFERENCES erp.prod_lot (tenant_id, prod_lot_id);
ALTER TABLE erp.lot_consumption ADD CONSTRAINT ck_lot_consumption_source CHECK (
    (consumption_type = 'RAW_MATERIAL' AND source_lot_id IS NOT NULL
        AND source_batch_id IS NULL AND source_prod_lot_id IS NULL)
 OR (consumption_type = 'COMPOUND_BATCH' AND source_batch_id IS NOT NULL
        AND source_lot_id IS NULL AND source_prod_lot_id IS NULL)
 OR (consumption_type = 'METAL_PROD_LOT' AND source_prod_lot_id IS NOT NULL
        AND source_lot_id IS NULL AND source_batch_id IS NULL)
 OR consumption_type = 'OTHER');
CREATE INDEX ix_lot_consumption_prod_lot ON erp.lot_consumption (tenant_id, prod_lot_id);
CREATE INDEX ix_lot_consumption_batch ON erp.lot_consumption (tenant_id, source_batch_id);

ALTER TABLE erp.packing ADD COLUMN fg_lot_id uuid;
ALTER TABLE erp.packing ADD CONSTRAINT fk_packing_fg_lot
    FOREIGN KEY (tenant_id, fg_lot_id) REFERENCES erp.lot (tenant_id, lot_id);

ALTER TABLE erp.work_order ADD CONSTRAINT ck_work_order_status CHECK (status IN
    ('PLANNED', 'RELEASED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'));
ALTER TABLE erp.work_order ADD CONSTRAINT ck_work_order_dates
    CHECK (planned_finish IS NULL OR planned_start IS NULL OR planned_finish >= planned_start);
ALTER TABLE erp.prod_lot ADD CONSTRAINT ck_prod_lot_status CHECK (status IN
    ('IN_PROCESS', 'READY', 'COMPLETED', 'CONSUMED', 'SCRAPPED'));
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("ALTER TABLE erp.prod_lot DROP CONSTRAINT ck_prod_lot_status")
    op.execute("ALTER TABLE erp.work_order DROP CONSTRAINT ck_work_order_dates")
    op.execute("ALTER TABLE erp.work_order DROP CONSTRAINT ck_work_order_status")
    op.execute("ALTER TABLE erp.packing DROP CONSTRAINT fk_packing_fg_lot")
    op.execute("ALTER TABLE erp.packing DROP COLUMN fg_lot_id")
    op.execute("DROP INDEX erp.ix_lot_consumption_batch")
    op.execute("DROP INDEX erp.ix_lot_consumption_prod_lot")
    op.execute("ALTER TABLE erp.lot_consumption DROP CONSTRAINT ck_lot_consumption_source")
    op.execute("ALTER TABLE erp.lot_consumption DROP CONSTRAINT fk_lot_consumption_source_prod_lot")
    op.execute("ALTER TABLE erp.lot_consumption DROP COLUMN source_prod_lot_id")
    op.execute("ALTER TABLE erp.lot_consumption ALTER COLUMN source_lot_id SET NOT NULL")
