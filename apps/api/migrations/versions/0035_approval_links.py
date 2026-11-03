"""Phase 12: approve or reject from an e-mail or WhatsApp link (FR-WF-04).

* approval_link: one row per link sent, keeping only a hash of the secret, who it is for, the step
  it is for, when it expires and when it was used; a link works once.
* approval_action.via: whether the decision was made in the application or through a link.
"""

from alembic import op

revision = "0035"
down_revision = "0034"

UPGRADE = """
CREATE TABLE erp.approval_link (
    link_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    approval_id uuid NOT NULL,
    step_no integer NOT NULL CHECK (step_no > 0),
    user_id uuid NOT NULL,
    token_hash text NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    used_decision text CHECK (used_decision IN ('APPROVE', 'REJECT')),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, approval_id) REFERENCES erp.approval_instance (tenant_id, approval_id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES erp.app_user (tenant_id, user_id)
);
CREATE INDEX ix_approval_link_approval ON erp.approval_link (approval_id, step_no);
ALTER TABLE erp.approval_link ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.approval_link
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON erp.approval_link TO heyyerp_app_rw;
ALTER TABLE erp.approval_action
    ADD COLUMN via text NOT NULL DEFAULT 'APP' CHECK (via IN ('APP', 'LINK'));
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("ALTER TABLE erp.approval_action DROP COLUMN via")
    op.execute("DROP TABLE erp.approval_link")
