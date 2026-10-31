"""Phase 11, part 1: preventive maintenance, shots and breakdowns.

* mould.shots_done: cumulative shots, so a mould's PM and life can follow production.
* pm_plan: plant, title, when and at how many shots it was last done; intervals must be positive.
* pm_task: the history of maintenance done (status DONE), who did it and when.
* breakdown: description, who closed it, and a closed ticket always has an end time.
"""

from alembic import op

revision = "0023"
down_revision = "0022"

UPGRADE = """
ALTER TABLE erp.mould ADD COLUMN shots_done bigint NOT NULL DEFAULT 0 CHECK (shots_done >= 0);

ALTER TABLE erp.pm_plan
    ADD COLUMN unit_id uuid,
    ADD COLUMN title text NOT NULL DEFAULT 'Preventive maintenance',
    ADD COLUMN created_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN last_done_at timestamptz,
    ADD COLUMN last_done_shots bigint NOT NULL DEFAULT 0 CHECK (last_done_shots >= 0),
    ADD CONSTRAINT ck_pm_interval_positive CHECK (
        (interval_days IS NULL OR interval_days > 0) AND (interval_shots IS NULL OR interval_shots > 0));
UPDATE erp.pm_plan p SET unit_id = COALESCE(
    (SELECT m.unit_id FROM erp.machine m WHERE m.machine_id = p.machine_id),
    (SELECT t.unit_id FROM erp.mould t WHERE t.mould_id = p.mould_id));
ALTER TABLE erp.pm_plan ALTER COLUMN unit_id SET NOT NULL,
    ADD CONSTRAINT fk_pm_plan_unit FOREIGN KEY (tenant_id, unit_id)
        REFERENCES erp.unit (tenant_id, unit_id);
CREATE INDEX ix_pm_plan_unit ON erp.pm_plan (unit_id) WHERE active;

ALTER TABLE erp.pm_task
    ADD COLUMN shots_at_done bigint,
    ADD CONSTRAINT ck_pm_task_status CHECK (status IN ('PLANNED', 'DONE', 'CANCELLED'));

ALTER TABLE erp.breakdown
    ADD COLUMN description text, ADD COLUMN closed_by uuid,
    ADD CONSTRAINT ck_breakdown_status CHECK (status IN ('OPEN', 'CLOSED')),
    ADD CONSTRAINT ck_breakdown_closed CHECK ((status = 'CLOSED') = (ended_at IS NOT NULL)),
    ADD CONSTRAINT fk_breakdown_closed_by FOREIGN KEY (tenant_id, closed_by)
        REFERENCES erp.app_user (tenant_id, user_id);
CREATE INDEX ix_breakdown_machine ON erp.breakdown (machine_id, started_at);
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP INDEX erp.ix_breakdown_machine")
    op.execute("ALTER TABLE erp.breakdown DROP CONSTRAINT fk_breakdown_closed_by, "
               "DROP CONSTRAINT ck_breakdown_closed, DROP CONSTRAINT ck_breakdown_status, "
               "DROP COLUMN closed_by, DROP COLUMN description")
    op.execute("ALTER TABLE erp.pm_task DROP CONSTRAINT ck_pm_task_status, "
               "DROP COLUMN shots_at_done")
    op.execute("DROP INDEX erp.ix_pm_plan_unit")
    op.execute("ALTER TABLE erp.pm_plan DROP CONSTRAINT fk_pm_plan_unit, "
               "DROP CONSTRAINT ck_pm_interval_positive, DROP COLUMN last_done_shots, "
               "DROP COLUMN last_done_at, DROP COLUMN created_at, DROP COLUMN title, "
               "DROP COLUMN unit_id")
    op.execute("ALTER TABLE erp.mould DROP COLUMN shots_done")
