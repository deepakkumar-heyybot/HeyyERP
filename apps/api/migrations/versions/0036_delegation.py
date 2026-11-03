"""Phase 12: delegation of approvals (FR-WF-03).

* approval_action.on_behalf_of: whom the decision was made for, when a delegate decided.
* approval_delegation: an index for finding the delegations that reach a person.
"""

from alembic import op

revision = "0036"
down_revision = "0035"

UPGRADE = """
ALTER TABLE erp.approval_action
    ADD COLUMN on_behalf_of uuid,
    ADD CONSTRAINT fk_action_on_behalf FOREIGN KEY (tenant_id, on_behalf_of)
        REFERENCES erp.app_user (tenant_id, user_id);
CREATE INDEX ix_approval_delegation_to ON erp.approval_delegation (to_user_id, valid_from, valid_to)
    WHERE active;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP INDEX erp.ix_approval_delegation_to")
    op.execute("ALTER TABLE erp.approval_action DROP CONSTRAINT fk_action_on_behalf, "
               "DROP COLUMN on_behalf_of")
