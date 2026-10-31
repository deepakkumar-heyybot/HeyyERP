"""Resubmission: a document rejected or withdrawn can be submitted for approval again.

approval_instance.is_current marks the approval of a document's latest submission; earlier ones
stay as history. A document has one current approval (a unique index replaces the old unique
constraint on document type and id).
"""

from alembic import op

revision = "0027"
down_revision = "0026"

UPGRADE = """
ALTER TABLE erp.approval_instance ADD COLUMN is_current boolean NOT NULL DEFAULT true;
DO $$
DECLARE c text;
BEGIN
    FOR c IN SELECT conname FROM pg_constraint WHERE conrelid = 'erp.approval_instance'::regclass
        AND contype = 'u' AND pg_get_constraintdef(oid) LIKE
        'UNIQUE (tenant_id, document_type, document_id)' LOOP
        EXECUTE format('ALTER TABLE erp.approval_instance DROP CONSTRAINT %I', c);
    END LOOP;
END $$;
CREATE UNIQUE INDEX ux_approval_current ON erp.approval_instance
    (tenant_id, document_type, document_id) WHERE is_current;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP INDEX erp.ux_approval_current")
    op.execute("DELETE FROM erp.approval_instance WHERE NOT is_current")
    op.execute("ALTER TABLE erp.approval_instance ADD UNIQUE (tenant_id, document_type, document_id)")
    op.execute("ALTER TABLE erp.approval_instance DROP COLUMN is_current")
