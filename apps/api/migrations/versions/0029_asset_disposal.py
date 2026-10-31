"""Fixed asset disposal is booked: the journal and the last depreciation it carried."""

from alembic import op

revision = "0029"
down_revision = "0028"

UPGRADE = """
ALTER TABLE erp.fixed_asset
    ADD COLUMN disposal_journal_id uuid,
    ADD COLUMN disposal_depreciation numeric(18,2) NOT NULL DEFAULT 0 CHECK (disposal_depreciation >= 0),
    ADD CONSTRAINT fk_asset_disposal_journal FOREIGN KEY (tenant_id, disposal_journal_id)
        REFERENCES erp.journal (tenant_id, journal_id);
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("ALTER TABLE erp.fixed_asset DROP CONSTRAINT fk_asset_disposal_journal, "
               "DROP COLUMN disposal_depreciation, DROP COLUMN disposal_journal_id")
