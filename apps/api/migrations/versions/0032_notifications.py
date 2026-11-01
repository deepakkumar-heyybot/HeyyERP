"""Phase 12: notifications that are delivered, retried and visible when they fail.

* notification: the subject and text, when it was read, how many delivery attempts, the last error
  and when to try again; one notification per recipient, channel, template and record.
* outbox_event: the last error of a relay that failed.
"""

from alembic import op

revision = "0032"
down_revision = "0031"

UPGRADE = """
ALTER TABLE erp.notification
    ADD COLUMN subject text, ADD COLUMN body text, ADD COLUMN read_at timestamptz,
    ADD COLUMN attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    ADD COLUMN last_error text, ADD COLUMN next_attempt_at timestamptz,
    ADD CONSTRAINT ck_notification_status CHECK (status IN ('QUEUED', 'SENT', 'FAILED', 'SKIPPED'));
UPDATE erp.notification SET status = 'SENT', sent_at = created_at
    WHERE channel = 'IN_APP' AND status = 'QUEUED';
CREATE UNIQUE INDEX ux_notification_once ON erp.notification
    (tenant_id, recipient_user_id, channel, template_code, reference_id)
    WHERE reference_id IS NOT NULL;
CREATE INDEX ix_notification_inbox ON erp.notification (recipient_user_id, created_at DESC)
    WHERE channel = 'IN_APP';
CREATE INDEX ix_notification_due ON erp.notification (next_attempt_at)
    WHERE status = 'QUEUED' AND channel <> 'IN_APP';
ALTER TABLE erp.outbox_event ADD COLUMN last_error text;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("ALTER TABLE erp.outbox_event DROP COLUMN last_error")
    op.execute("DROP INDEX erp.ix_notification_due")
    op.execute("DROP INDEX erp.ix_notification_inbox")
    op.execute("DROP INDEX erp.ux_notification_once")
    op.execute("ALTER TABLE erp.notification DROP CONSTRAINT ck_notification_status, "
               "DROP COLUMN next_attempt_at, DROP COLUMN last_error, DROP COLUMN attempt_count, "
               "DROP COLUMN read_at, DROP COLUMN body, DROP COLUMN subject")
