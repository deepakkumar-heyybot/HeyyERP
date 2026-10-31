"""Phase 11, part 3: employees, leave, attendance and yearly appraisals.

* employee: the date left (an exited employee always has one) and a user links to one employee only.
* leave_type: unpaid types (no balance needed) and active flag.
* leave_request: half days, who applied, when, and a status check (new requests wait for approval).
* appraisal: comments of each step, the final score and a status check.
"""

from alembic import op

revision = "0025"
down_revision = "0024"

UPGRADE = """
ALTER TABLE erp.employee
    ADD COLUMN date_left date,
    ADD CONSTRAINT ck_employee_status CHECK (employment_status IN ('ACTIVE', 'EXITED')),
    ADD CONSTRAINT ck_employee_left CHECK ((employment_status = 'EXITED') = (date_left IS NOT NULL)),
    ADD CONSTRAINT ck_employee_not_own_manager CHECK (manager_id IS NULL OR manager_id <> employee_id);
CREATE INDEX ix_employee_manager ON erp.employee (manager_id) WHERE manager_id IS NOT NULL;
CREATE INDEX ix_employee_unit ON erp.employee (unit_id, department_id);

ALTER TABLE erp.leave_type
    ADD COLUMN unpaid boolean NOT NULL DEFAULT false,
    ADD COLUMN active boolean NOT NULL DEFAULT true;

ALTER TABLE erp.leave_request
    ADD COLUMN half_day boolean NOT NULL DEFAULT false,
    ADD COLUMN applied_by uuid,
    ADD COLUMN created_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN counted boolean NOT NULL DEFAULT false,
    ALTER COLUMN status SET DEFAULT 'PENDING_APPROVAL',
    ADD CONSTRAINT ck_leave_status CHECK (status IN
        ('PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'CANCELLED')),
    ADD CONSTRAINT ck_leave_half_day CHECK (NOT half_day OR start_date = end_date),
    ADD CONSTRAINT fk_leave_applied_by FOREIGN KEY (tenant_id, applied_by)
        REFERENCES erp.app_user (tenant_id, user_id);
CREATE INDEX ix_leave_employee ON erp.leave_request (employee_id, start_date);
ALTER TABLE erp.leave_balance ADD CONSTRAINT ck_leave_balance_used CHECK (used_days >= 0);

ALTER TABLE erp.appraisal
    ADD COLUMN self_comment text, ADD COLUMN manager_comment text, ADD COLUMN hr_comment text,
    ADD COLUMN final_score numeric(5,2),
    ADD COLUMN created_at timestamptz NOT NULL DEFAULT now(),
    ADD CONSTRAINT ck_appraisal_status CHECK (status IN
        ('SELF_REVIEW', 'MANAGER_REVIEW', 'PENDING_APPROVAL', 'COMPLETED', 'REJECTED')),
    ADD CONSTRAINT ck_appraisal_scores CHECK (
        COALESCE(self_score, 0) >= 0 AND COALESCE(manager_score, 0) >= 0
        AND COALESCE(hr_score, 0) >= 0 AND COALESCE(final_score, 0) >= 0);
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("ALTER TABLE erp.appraisal DROP CONSTRAINT ck_appraisal_scores, "
               "DROP CONSTRAINT ck_appraisal_status, DROP COLUMN created_at, "
               "DROP COLUMN final_score, DROP COLUMN hr_comment, DROP COLUMN manager_comment, "
               "DROP COLUMN self_comment")
    op.execute("ALTER TABLE erp.leave_balance DROP CONSTRAINT ck_leave_balance_used")
    op.execute("DROP INDEX erp.ix_leave_employee")
    op.execute("ALTER TABLE erp.leave_request DROP CONSTRAINT fk_leave_applied_by, "
               "DROP CONSTRAINT ck_leave_half_day, DROP CONSTRAINT ck_leave_status, "
               "ALTER COLUMN status SET DEFAULT 'SUBMITTED', DROP COLUMN counted, "
               "DROP COLUMN created_at, DROP COLUMN applied_by, DROP COLUMN half_day")
    op.execute("ALTER TABLE erp.leave_type DROP COLUMN active, DROP COLUMN unpaid")
    op.execute("DROP INDEX erp.ix_employee_unit")
    op.execute("DROP INDEX erp.ix_employee_manager")
    op.execute("ALTER TABLE erp.employee DROP CONSTRAINT ck_employee_not_own_manager, "
               "DROP CONSTRAINT ck_employee_left, DROP CONSTRAINT ck_employee_status, "
               "DROP COLUMN date_left")
