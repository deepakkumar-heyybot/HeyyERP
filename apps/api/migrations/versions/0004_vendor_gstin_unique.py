"""One vendor record per GSTIN within a tenant.

The baseline allowed duplicate vendors with the same GSTIN. FR-PRC-04 asks for GSTIN checks
at onboarding, and duplicate vendors split payments and ratings, so the database enforces it.
Customers are left alone: BRD FR-SAL-01 allows several billing codes for one customer.
"""

from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_vendor_tenant_gstin ON erp.vendor (tenant_id, gstin) "
        "WHERE gstin IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX erp.uq_vendor_tenant_gstin")
