"""Phase 11, part 4: kaizen suggestions, evaluation and savings.

* kaizen: a number, a category, the evaluation (score, verified saving, note, who and when),
  approval and implementation dates, and a status check.
* kaizen_saving: the saving realised each month once a kaizen is approved.
"""

from alembic import op

revision = "0026"
down_revision = "0025"

UPGRADE = """
ALTER TABLE erp.kaizen
    ADD COLUMN kaizen_no text,
    ADD COLUMN category text NOT NULL DEFAULT 'OTHER',
    ADD COLUMN verified_saving numeric(18,2) CHECK (verified_saving >= 0),
    ADD COLUMN evaluation_note text,
    ADD COLUMN evaluated_by uuid, ADD COLUMN evaluated_at timestamptz,
    ADD COLUMN approved_at timestamptz, ADD COLUMN implemented_on date,
    ADD CONSTRAINT ck_kaizen_status CHECK (status IN
        ('SUBMITTED', 'APPROVED', 'REJECTED', 'IMPLEMENTED', 'CANCELLED')),
    ADD CONSTRAINT ck_kaizen_category CHECK (category IN
        ('COST', 'QUALITY', 'SAFETY', 'PRODUCTIVITY', 'ENVIRONMENT', 'OTHER')),
    ADD CONSTRAINT ck_kaizen_values CHECK (COALESCE(estimated_saving, 0) >= 0
        AND COALESCE(score, 0) >= 0),
    ADD CONSTRAINT fk_kaizen_evaluator FOREIGN KEY (tenant_id, evaluated_by)
        REFERENCES erp.app_user (tenant_id, user_id);
CREATE UNIQUE INDEX ux_kaizen_no ON erp.kaizen (tenant_id, kaizen_no) WHERE kaizen_no IS NOT NULL;
CREATE INDEX ix_kaizen_unit ON erp.kaizen (unit_id, department_id, status);
ALTER TABLE erp.kaizen ADD CONSTRAINT uq_kaizen_tenant UNIQUE (tenant_id, kaizen_id);

CREATE TABLE erp.kaizen_saving (
    kaizen_saving_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    kaizen_id uuid NOT NULL,
    saving_month date NOT NULL CHECK (EXTRACT(DAY FROM saving_month) = 1),
    amount numeric(18,2) NOT NULL CHECK (amount >= 0),
    note text,
    recorded_by uuid,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kaizen_id, saving_month),
    FOREIGN KEY (tenant_id, kaizen_id) REFERENCES erp.kaizen (tenant_id, kaizen_id),
    FOREIGN KEY (tenant_id, recorded_by) REFERENCES erp.app_user (tenant_id, user_id)
);
ALTER TABLE erp.kaizen_saving ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.kaizen_saving
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON erp.kaizen_saving TO heyyerp_app_rw;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP TABLE erp.kaizen_saving")
    op.execute("ALTER TABLE erp.kaizen DROP CONSTRAINT uq_kaizen_tenant")
    op.execute("DROP INDEX erp.ix_kaizen_unit")
    op.execute("DROP INDEX erp.ux_kaizen_no")
    op.execute("ALTER TABLE erp.kaizen DROP CONSTRAINT fk_kaizen_evaluator, "
               "DROP CONSTRAINT ck_kaizen_values, DROP CONSTRAINT ck_kaizen_category, "
               "DROP CONSTRAINT ck_kaizen_status, DROP COLUMN implemented_on, "
               "DROP COLUMN approved_at, DROP COLUMN evaluated_at, DROP COLUMN evaluated_by, "
               "DROP COLUMN evaluation_note, DROP COLUMN verified_saving, DROP COLUMN category, "
               "DROP COLUMN kaizen_no")
