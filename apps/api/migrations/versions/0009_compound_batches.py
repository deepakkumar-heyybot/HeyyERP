"""Phase 7, part 1: compounds linked to items, batch mixing/QA fields and the derived status view.

* compound.output_item_id: the WIP item a BOM line names when it means "draw this compound".
* compound_batch: when mixing started, who passed or failed it in QA and why, the store the raw
  material came from.
* v_compound_batch_status: the batch status derived from time, QA result and kilos left (BRD 6.3);
  it is never stored, so it cannot go stale. security_invoker keeps row-level security in force.
"""

from alembic import op

revision = "0009"
down_revision = "0008"

UPGRADE = """
ALTER TABLE erp.compound ADD COLUMN output_item_id uuid;
ALTER TABLE erp.compound ADD CONSTRAINT fk_compound_output_item
    FOREIGN KEY (tenant_id, output_item_id) REFERENCES erp.item (tenant_id, item_id);
CREATE UNIQUE INDEX ux_compound_output_item ON erp.compound (tenant_id, output_item_id)
    WHERE output_item_id IS NOT NULL;

ALTER TABLE erp.compound_batch
    ADD COLUMN store_id uuid,
    ADD COLUMN mix_started_at timestamptz,
    ADD COLUMN qa_by uuid,
    ADD COLUMN qa_at timestamptz,
    ADD COLUMN qa_note text;
ALTER TABLE erp.compound_batch ADD CONSTRAINT fk_compound_batch_store
    FOREIGN KEY (tenant_id, store_id) REFERENCES erp.store (tenant_id, store_id);
ALTER TABLE erp.compound_batch ADD CONSTRAINT fk_compound_batch_qa_by
    FOREIGN KEY (tenant_id, qa_by) REFERENCES erp.app_user (tenant_id, user_id);

CREATE VIEW erp.v_compound_batch_status WITH (security_invoker = true) AS
SELECT b.batch_id, b.tenant_id, b.unit_id, b.compound_id, b.batch_no, b.weighed_at,
       b.mix_started_at, b.mixed_at, b.total_kg, b.remaining_kg, b.qa_status, b.hardness,
       b.qa_by, b.qa_at, b.qa_note, b.store_id, b.created_by,
       c.code AS compound_code, c.name AS compound_name,
       c.maturation_min_hours, c.maximum_age_hours, c.hardness_min, c.hardness_max,
       CASE WHEN b.mixed_at IS NULL THEN NULL
            ELSE extract(epoch FROM (now() - b.mixed_at)) / 3600 END AS age_hours,
       CASE
         WHEN b.qa_status = 'FAILED' THEN 'REJECTED'
         WHEN b.remaining_kg = 0 THEN 'CONSUMED'
         WHEN b.mixed_at IS NULL THEN
              CASE WHEN b.mix_started_at IS NULL THEN 'WEIGHED' ELSE 'MIXING' END
         WHEN extract(epoch FROM (now() - b.mixed_at)) / 3600 > c.maximum_age_hours THEN 'EXPIRED'
         WHEN extract(epoch FROM (now() - b.mixed_at)) / 3600 < c.maturation_min_hours
              THEN 'MATURING'
         WHEN b.qa_status = 'PENDING' THEN 'AWAITING_QA'
         ELSE 'READY'
       END AS status
FROM erp.compound_batch b JOIN erp.compound c ON c.compound_id = b.compound_id;
GRANT SELECT ON erp.v_compound_batch_status TO heyyerp_app_rw;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP VIEW erp.v_compound_batch_status")
    op.execute("ALTER TABLE erp.compound_batch DROP CONSTRAINT fk_compound_batch_qa_by, "
               "DROP CONSTRAINT fk_compound_batch_store")
    op.execute("ALTER TABLE erp.compound_batch DROP COLUMN qa_note, DROP COLUMN qa_at, "
               "DROP COLUMN qa_by, DROP COLUMN mix_started_at, DROP COLUMN store_id")
    op.execute("DROP INDEX erp.ux_compound_output_item")
    op.execute("ALTER TABLE erp.compound DROP CONSTRAINT fk_compound_output_item")
    op.execute("ALTER TABLE erp.compound DROP COLUMN output_item_id")
