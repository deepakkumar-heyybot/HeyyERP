"""Phase 8, part 1: inspection plans, inspections of any subject, measurements with limits.

* inspection_plan / inspection_plan_param: what to measure for an item at incoming, in-process
  and final inspection, with limits. One active plan per item and kind; a new plan retires the
  old one, which stays for history.
* qa_inspection: what was inspected (a GRN line, compound batch, production lot or stock lot),
  the kind, the plant, the plan used, the instrument used, and whether QA typed it or the
  system recorded it from a QA decision.
* qa_measurement: the limits that applied are copied onto each measurement.
"""

from alembic import op

revision = "0012"
down_revision = "0011"

UPGRADE = """
CREATE TABLE erp.inspection_plan (
    plan_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    item_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('INCOMING', 'IN_PROCESS', 'FINAL')),
    name text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, plan_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES erp.item (tenant_id, item_id),
    FOREIGN KEY (tenant_id, created_by) REFERENCES erp.app_user (tenant_id, user_id)
);
CREATE UNIQUE INDEX ux_inspection_plan_active
    ON erp.inspection_plan (tenant_id, item_id, kind) WHERE active;

CREATE TABLE erp.inspection_plan_param (
    param_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    plan_id uuid NOT NULL,
    line_no integer NOT NULL CHECK (line_no > 0),
    parameter_code text NOT NULL,
    name text NOT NULL,
    unit text,
    min_value numeric(18,6),
    max_value numeric(18,6),
    is_key boolean NOT NULL DEFAULT false,
    UNIQUE (plan_id, parameter_code),
    UNIQUE (plan_id, line_no),
    FOREIGN KEY (tenant_id, plan_id) REFERENCES erp.inspection_plan (tenant_id, plan_id),
    CHECK (min_value IS NULL OR max_value IS NULL OR max_value >= min_value),
    CHECK (min_value IS NOT NULL OR max_value IS NOT NULL)
);

ALTER TABLE erp.qa_inspection
    ADD COLUMN kind text CHECK (kind IN ('INCOMING', 'IN_PROCESS', 'FINAL')),
    ADD COLUMN unit_id uuid,
    ADD COLUMN subject_type text CHECK (subject_type IN
        ('GRN_LINE', 'COMPOUND_BATCH', 'PROD_LOT', 'STOCK_LOT')),
    ADD COLUMN subject_id uuid,
    ADD COLUMN plan_id uuid,
    ADD COLUMN instrument_id uuid,
    ADD COLUMN source text NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('MANUAL', 'AUTO'));
ALTER TABLE erp.qa_inspection
    ADD CONSTRAINT fk_qa_inspection_unit FOREIGN KEY (tenant_id, unit_id)
        REFERENCES erp.unit (tenant_id, unit_id),
    ADD CONSTRAINT fk_qa_inspection_plan FOREIGN KEY (tenant_id, plan_id)
        REFERENCES erp.inspection_plan (tenant_id, plan_id),
    ADD CONSTRAINT fk_qa_inspection_instrument FOREIGN KEY (tenant_id, instrument_id)
        REFERENCES erp.instrument (tenant_id, instrument_id);
CREATE INDEX ix_qa_inspection_subject
    ON erp.qa_inspection (tenant_id, subject_type, subject_id, inspected_at);

ALTER TABLE erp.qa_measurement ADD COLUMN min_value numeric(18,6),
    ADD COLUMN max_value numeric(18,6);

ALTER TABLE erp.inspection_plan_param ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.inspection_plan_param
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
ALTER TABLE erp.inspection_plan ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.inspection_plan
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON erp.inspection_plan, erp.inspection_plan_param
    TO heyyerp_app_rw;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("ALTER TABLE erp.qa_measurement DROP COLUMN max_value, DROP COLUMN min_value")
    op.execute("DROP INDEX erp.ix_qa_inspection_subject")
    op.execute("ALTER TABLE erp.qa_inspection DROP CONSTRAINT fk_qa_inspection_instrument, "
               "DROP CONSTRAINT fk_qa_inspection_plan, DROP CONSTRAINT fk_qa_inspection_unit")
    op.execute("ALTER TABLE erp.qa_inspection DROP COLUMN source, DROP COLUMN instrument_id, "
               "DROP COLUMN plan_id, DROP COLUMN subject_id, DROP COLUMN subject_type, "
               "DROP COLUMN unit_id, DROP COLUMN kind")
    op.execute("DROP TABLE erp.inspection_plan_param")
    op.execute("DROP TABLE erp.inspection_plan")
