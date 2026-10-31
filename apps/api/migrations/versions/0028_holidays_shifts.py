"""Holidays, shifts and leave accrual, all maintained by the company.

* holiday: a company holiday for every plant (no unit) or for one plant; it never counts as a working
  or leave day when the company says so.
* shift: a shift's start, end and grace minutes, for every plant or one, used to find late arrivals and
  early leavers.
* leave_type.accrual: the type's own accrual (YEARLY or MONTHLY); empty follows the company rule.
"""

from alembic import op

revision = "0028"
down_revision = "0027"

UPGRADE = """
CREATE TABLE erp.holiday (
    holiday_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    unit_id uuid,
    holiday_date date NOT NULL,
    name text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES erp.unit (tenant_id, unit_id),
    FOREIGN KEY (tenant_id, created_by) REFERENCES erp.app_user (tenant_id, user_id)
);
CREATE UNIQUE INDEX ux_holiday_day ON erp.holiday
    (tenant_id, COALESCE(unit_id, '00000000-0000-0000-0000-000000000000'::uuid), holiday_date);
ALTER TABLE erp.holiday ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.holiday
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON erp.holiday TO heyyerp_app_rw;

CREATE TABLE erp.shift (
    shift_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    unit_id uuid,
    code text NOT NULL,
    name text NOT NULL,
    start_time time NOT NULL,
    end_time time NOT NULL,
    grace_minutes integer NOT NULL DEFAULT 0 CHECK (grace_minutes BETWEEN 0 AND 240),
    active boolean NOT NULL DEFAULT true,
    FOREIGN KEY (tenant_id, unit_id) REFERENCES erp.unit (tenant_id, unit_id),
    CHECK (start_time <> end_time)
);
CREATE UNIQUE INDEX ux_shift_code ON erp.shift
    (tenant_id, COALESCE(unit_id, '00000000-0000-0000-0000-000000000000'::uuid), code);
ALTER TABLE erp.shift ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.shift
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON erp.shift TO heyyerp_app_rw;

ALTER TABLE erp.leave_type
    ADD COLUMN accrual text CHECK (accrual IN ('YEARLY', 'MONTHLY'));
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("ALTER TABLE erp.leave_type DROP COLUMN accrual")
    op.execute("DROP TABLE erp.shift")
    op.execute("DROP TABLE erp.holiday")
