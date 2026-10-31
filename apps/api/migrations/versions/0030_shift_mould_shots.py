"""Shift entries can name the mould that ran and its shots, which count towards the mould's life."""

from alembic import op

revision = "0030"
down_revision = "0029"

UPGRADE = """
ALTER TABLE erp.shift_production
    ADD COLUMN mould_id uuid, ADD COLUMN shots integer CHECK (shots > 0),
    ADD CONSTRAINT ck_shift_mould_shots CHECK ((mould_id IS NULL) = (shots IS NULL)),
    ADD CONSTRAINT fk_shift_mould FOREIGN KEY (tenant_id, mould_id)
        REFERENCES erp.mould (tenant_id, mould_id);
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("ALTER TABLE erp.shift_production DROP CONSTRAINT fk_shift_mould, "
               "DROP CONSTRAINT ck_shift_mould_shots, DROP COLUMN shots, DROP COLUMN mould_id")
