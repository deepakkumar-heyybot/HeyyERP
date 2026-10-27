"""Phase 8, part 2: quality holds, non-conformance reports with 8D steps and dispositions.

* quality_hold: a lot, compound batch or production lot frozen by QA, with who/why/when and
  release. One open hold per subject. A held compound batch shows as ON_HOLD in the status view.
* prod_lot.status gains HOLD.
* ncr: plant, severity, description, disposition and closing; the 8D steps D1 to D8 are unique
  per report and record who completed them.
"""

from alembic import op

revision = "0013"
down_revision = "0012"

UPGRADE = """
ALTER TABLE erp.ncr DROP CONSTRAINT ncr_source_type_check;
ALTER TABLE erp.ncr ADD CONSTRAINT ncr_source_type_check CHECK (source_type IN
    ('GRN_LINE', 'COMPOUND_BATCH', 'PROD_LOT', 'STOCK_LOT', 'SUPPLIER', 'CUSTOMER_COMPLAINT'));
ALTER TABLE erp.ncr
    ADD COLUMN unit_id uuid,
    ADD COLUMN severity text NOT NULL DEFAULT 'MINOR'
        CHECK (severity IN ('MINOR', 'MAJOR', 'CRITICAL')),
    ADD COLUMN description text,
    ADD COLUMN disposition text CHECK (disposition IN
        ('USE_AS_IS', 'REWORK', 'SORT', 'SCRAP', 'RETURN_TO_SUPPLIER')),
    ADD COLUMN disposition_note text,
    ADD COLUMN disposition_by uuid,
    ADD COLUMN disposition_at timestamptz,
    ADD COLUMN closed_by uuid,
    ADD COLUMN closed_at timestamptz;
ALTER TABLE erp.ncr
    ADD CONSTRAINT fk_ncr_unit FOREIGN KEY (tenant_id, unit_id)
        REFERENCES erp.unit (tenant_id, unit_id),
    ADD CONSTRAINT fk_ncr_disposition_by FOREIGN KEY (tenant_id, disposition_by)
        REFERENCES erp.app_user (tenant_id, user_id),
    ADD CONSTRAINT fk_ncr_closed_by FOREIGN KEY (tenant_id, closed_by)
        REFERENCES erp.app_user (tenant_id, user_id),
    ADD CONSTRAINT ck_ncr_status CHECK (status IN ('OPEN', 'IN_PROGRESS', 'CLOSED', 'CANCELLED'));

ALTER TABLE erp.ncr_step
    ADD COLUMN completed_by uuid,
    ADD CONSTRAINT ck_ncr_step_code CHECK (step_code IN
        ('D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8')),
    ADD CONSTRAINT ux_ncr_step UNIQUE (ncr_id, step_code),
    ADD CONSTRAINT fk_ncr_step_completed_by FOREIGN KEY (tenant_id, completed_by)
        REFERENCES erp.app_user (tenant_id, user_id);

CREATE TABLE erp.quality_hold (
    hold_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    unit_id uuid NOT NULL,
    subject_type text NOT NULL CHECK (subject_type IN ('STOCK_LOT', 'COMPOUND_BATCH', 'PROD_LOT')),
    subject_id uuid NOT NULL,
    reason text NOT NULL,
    previous_status text,
    ncr_id uuid,
    placed_by uuid NOT NULL,
    placed_at timestamptz NOT NULL DEFAULT now(),
    released_by uuid,
    released_at timestamptz,
    release_note text,
    UNIQUE (tenant_id, hold_id),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES erp.unit (tenant_id, unit_id),
    FOREIGN KEY (tenant_id, ncr_id) REFERENCES erp.ncr (tenant_id, ncr_id),
    FOREIGN KEY (tenant_id, placed_by) REFERENCES erp.app_user (tenant_id, user_id),
    FOREIGN KEY (tenant_id, released_by) REFERENCES erp.app_user (tenant_id, user_id),
    CHECK ((released_at IS NULL) = (released_by IS NULL))
);
CREATE UNIQUE INDEX ux_quality_hold_open
    ON erp.quality_hold (tenant_id, subject_type, subject_id) WHERE released_at IS NULL;
ALTER TABLE erp.quality_hold ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.quality_hold
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON erp.quality_hold TO heyyerp_app_rw;

ALTER TABLE erp.prod_lot DROP CONSTRAINT ck_prod_lot_status;
ALTER TABLE erp.prod_lot ADD CONSTRAINT ck_prod_lot_status CHECK (status IN
    ('IN_PROCESS', 'READY', 'HOLD', 'COMPLETED', 'CONSUMED', 'SCRAPPED'));

CREATE OR REPLACE VIEW erp.v_compound_batch_status WITH (security_invoker = true) AS
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
         WHEN EXISTS (SELECT 1 FROM erp.quality_hold h WHERE h.subject_type = 'COMPOUND_BATCH'
                      AND h.subject_id = b.batch_id AND h.released_at IS NULL) THEN 'ON_HOLD'
         WHEN b.mixed_at IS NULL THEN
              CASE WHEN b.mix_started_at IS NULL THEN 'WEIGHED' ELSE 'MIXING' END
         WHEN extract(epoch FROM (now() - b.mixed_at)) / 3600 > c.maximum_age_hours THEN 'EXPIRED'
         WHEN extract(epoch FROM (now() - b.mixed_at)) / 3600 < c.maturation_min_hours
              THEN 'MATURING'
         WHEN b.qa_status = 'PENDING' THEN 'AWAITING_QA'
         ELSE 'READY'
       END AS status
FROM erp.compound_batch b JOIN erp.compound c ON c.compound_id = b.compound_id;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("""
    CREATE OR REPLACE VIEW erp.v_compound_batch_status WITH (security_invoker = true) AS
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
             WHEN extract(epoch FROM (now() - b.mixed_at)) / 3600 > c.maximum_age_hours
                  THEN 'EXPIRED'
             WHEN extract(epoch FROM (now() - b.mixed_at)) / 3600 < c.maturation_min_hours
                  THEN 'MATURING'
             WHEN b.qa_status = 'PENDING' THEN 'AWAITING_QA'
             ELSE 'READY'
           END AS status
    FROM erp.compound_batch b JOIN erp.compound c ON c.compound_id = b.compound_id
    """)
    op.execute("ALTER TABLE erp.prod_lot DROP CONSTRAINT ck_prod_lot_status")
    op.execute("ALTER TABLE erp.prod_lot ADD CONSTRAINT ck_prod_lot_status CHECK (status IN "
               "('IN_PROCESS', 'READY', 'COMPLETED', 'CONSUMED', 'SCRAPPED'))")
    op.execute("DROP TABLE erp.quality_hold")
    op.execute("ALTER TABLE erp.ncr_step DROP CONSTRAINT fk_ncr_step_completed_by, "
               "DROP CONSTRAINT ux_ncr_step, DROP CONSTRAINT ck_ncr_step_code, "
               "DROP COLUMN completed_by")
    op.execute("ALTER TABLE erp.ncr DROP CONSTRAINT ck_ncr_status, "
               "DROP CONSTRAINT fk_ncr_closed_by, DROP CONSTRAINT fk_ncr_disposition_by, "
               "DROP CONSTRAINT fk_ncr_unit")
    op.execute("ALTER TABLE erp.ncr DROP COLUMN closed_at, DROP COLUMN closed_by, "
               "DROP COLUMN disposition_at, DROP COLUMN disposition_by, "
               "DROP COLUMN disposition_note, DROP COLUMN disposition, DROP COLUMN description, "
               "DROP COLUMN severity, DROP COLUMN unit_id")
    op.execute("ALTER TABLE erp.ncr DROP CONSTRAINT ncr_source_type_check")
    op.execute("ALTER TABLE erp.ncr ADD CONSTRAINT ncr_source_type_check CHECK (source_type IN "
               "('GRN_LINE', 'COMPOUND_BATCH', 'PROD_LOT', 'SUPPLIER', 'CUSTOMER_COMPLAINT'))")
