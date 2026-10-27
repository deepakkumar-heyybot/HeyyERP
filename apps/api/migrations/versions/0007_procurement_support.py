"""Phase 5 support: document numbers, PO-to-RFQ link and status checks.

* document_sequence: one counter per tenant, document type and financial year, so numbers read
  like PO-26-0861 (26 = the financial year starting April 2026) and restart each year. The
  counter is bumped inside the same transaction as the document, so a failed request leaves no gap.
* purchase_order.rfq_id: which tendering round a PO came from (the "minimum quotes" rule checks it).
* CHECK constraints on the free-text status columns, so a typo can never become a status.
"""

from alembic import op

revision = "0007"
down_revision = "0006"

UPGRADE = """
CREATE TABLE erp.document_sequence (
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    doc_type text NOT NULL,
    fy_label text NOT NULL,
    last_no integer NOT NULL DEFAULT 0 CHECK (last_no >= 0),
    PRIMARY KEY (tenant_id, doc_type, fy_label)
);
ALTER TABLE erp.document_sequence ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.document_sequence
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON erp.document_sequence TO heyyerp_app_rw;

ALTER TABLE erp.purchase_order ADD COLUMN rfq_id uuid;
ALTER TABLE erp.purchase_order ADD CONSTRAINT fk_purchase_order_rfq_id_rfq
    FOREIGN KEY (tenant_id, rfq_id) REFERENCES erp.rfq (tenant_id, rfq_id);

ALTER TABLE erp.indent ADD CONSTRAINT ck_indent_status CHECK (status IN
    ('DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'ORDERED', 'CANCELLED'));
ALTER TABLE erp.rfq ADD CONSTRAINT ck_rfq_status CHECK (status IN
    ('DRAFT', 'SENT', 'AWARDED', 'CLOSED', 'CANCELLED'));
ALTER TABLE erp.purchase_order ADD CONSTRAINT ck_purchase_order_status CHECK (status IN
    ('DRAFT', 'PENDING_APPROVAL', 'OPEN', 'PARTIALLY_RECEIVED', 'RECEIVED', 'CLOSED',
     'REJECTED', 'CANCELLED'));
ALTER TABLE erp.rfq_vendor_invitation ADD CONSTRAINT ck_rfq_invitation_status CHECK
    (response_status IN ('INVITED', 'QUOTED', 'DECLINED'));
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("ALTER TABLE erp.rfq_vendor_invitation DROP CONSTRAINT ck_rfq_invitation_status")
    op.execute("ALTER TABLE erp.purchase_order DROP CONSTRAINT ck_purchase_order_status")
    op.execute("ALTER TABLE erp.rfq DROP CONSTRAINT ck_rfq_status")
    op.execute("ALTER TABLE erp.indent DROP CONSTRAINT ck_indent_status")
    op.execute("ALTER TABLE erp.purchase_order DROP CONSTRAINT fk_purchase_order_rfq_id_rfq")
    op.execute("ALTER TABLE erp.purchase_order DROP COLUMN rfq_id")
    op.execute("DROP TABLE erp.document_sequence")
