"""Phase 9, part 1: price lists, quotations, sales order status and shipped quantity, schedule import.

* customer_price: the rate a customer pays for an item from a date; one open-ended price per
  customer and item (a new price closes the previous one).
* quotation / quotation_line: what was offered to a customer; an accepted quotation becomes an SO.
* sales_order: status checks, who created it and the quotation it came from.
* so_line.shipped_qty: what dispatch has sent; so_schedule is unique per line and due date.
* schedule_import: the customer it belongs to, the outcome counts and when it was applied.
"""

from alembic import op

revision = "0014"
down_revision = "0013"

UPGRADE = """
CREATE TABLE erp.customer_price (
    price_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    customer_id uuid NOT NULL,
    item_id uuid NOT NULL,
    unit_price numeric(18,2) NOT NULL CHECK (unit_price >= 0),
    valid_from date NOT NULL,
    valid_to date,
    created_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES erp.customer (tenant_id, customer_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES erp.item (tenant_id, item_id),
    FOREIGN KEY (tenant_id, created_by) REFERENCES erp.app_user (tenant_id, user_id),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE INDEX ix_customer_price_lookup
    ON erp.customer_price (tenant_id, customer_id, item_id, valid_from DESC);
CREATE UNIQUE INDEX ux_customer_price_open
    ON erp.customer_price (tenant_id, customer_id, item_id) WHERE valid_to IS NULL;

CREATE TABLE erp.quotation (
    quote_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    unit_id uuid NOT NULL,
    customer_id uuid NOT NULL,
    quote_no text NOT NULL,
    enquiry_ref text,
    valid_until date NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN
        ('DRAFT', 'SENT', 'ACCEPTED', 'REJECTED', 'EXPIRED', 'CONVERTED')),
    so_id uuid,
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, quote_id),
    UNIQUE (tenant_id, quote_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES erp.unit (tenant_id, unit_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES erp.customer (tenant_id, customer_id),
    FOREIGN KEY (tenant_id, so_id) REFERENCES erp.sales_order (tenant_id, so_id),
    FOREIGN KEY (tenant_id, created_by) REFERENCES erp.app_user (tenant_id, user_id)
);
CREATE TABLE erp.quotation_line (
    quote_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    quote_id uuid NOT NULL,
    line_no integer NOT NULL CHECK (line_no > 0),
    item_id uuid NOT NULL,
    quantity numeric(18,3) NOT NULL CHECK (quantity > 0),
    unit_price numeric(18,2) NOT NULL CHECK (unit_price >= 0),
    tax_code_id uuid,
    UNIQUE (quote_id, line_no),
    FOREIGN KEY (tenant_id, quote_id) REFERENCES erp.quotation (tenant_id, quote_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES erp.item (tenant_id, item_id),
    FOREIGN KEY (tenant_id, tax_code_id) REFERENCES erp.tax_code (tenant_id, tax_code_id)
);

ALTER TABLE erp.sales_order ADD COLUMN quote_id uuid, ADD COLUMN created_by uuid;
ALTER TABLE erp.sales_order
    ADD CONSTRAINT fk_sales_order_quote FOREIGN KEY (tenant_id, quote_id)
        REFERENCES erp.quotation (tenant_id, quote_id),
    ADD CONSTRAINT fk_sales_order_created_by FOREIGN KEY (tenant_id, created_by)
        REFERENCES erp.app_user (tenant_id, user_id),
    ADD CONSTRAINT ck_sales_order_status CHECK (status IN
        ('OPEN', 'PARTIALLY_SHIPPED', 'SHIPPED', 'CLOSED', 'CANCELLED'));
CREATE UNIQUE INDEX ux_sales_order_customer_po
    ON erp.sales_order (tenant_id, customer_id, customer_po_no)
    WHERE customer_po_no IS NOT NULL AND status <> 'CANCELLED';

ALTER TABLE erp.so_line ADD COLUMN shipped_qty numeric(18,3) NOT NULL DEFAULT 0
    CHECK (shipped_qty >= 0 AND shipped_qty <= ordered_qty);
CREATE UNIQUE INDEX ux_so_schedule_line_date ON erp.so_schedule (so_line_id, due_date);

ALTER TABLE erp.schedule_import
    ADD COLUMN customer_id uuid, ADD COLUMN applied_at timestamptz, ADD COLUMN applied_by uuid;
ALTER TABLE erp.schedule_import
    ADD CONSTRAINT fk_schedule_import_customer FOREIGN KEY (tenant_id, customer_id)
        REFERENCES erp.customer (tenant_id, customer_id),
    ADD CONSTRAINT fk_schedule_import_applied_by FOREIGN KEY (tenant_id, applied_by)
        REFERENCES erp.app_user (tenant_id, user_id),
    ADD CONSTRAINT ck_schedule_import_status CHECK (status IN
        ('UPLOADED', 'PREVIEW', 'APPLIED', 'DISCARDED'));

ALTER TABLE erp.customer_price ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.customer_price
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
ALTER TABLE erp.quotation ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.quotation
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
ALTER TABLE erp.quotation_line ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.quotation_line
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON erp.customer_price, erp.quotation, erp.quotation_line
    TO heyyerp_app_rw;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("ALTER TABLE erp.schedule_import DROP CONSTRAINT ck_schedule_import_status, "
               "DROP CONSTRAINT fk_schedule_import_applied_by, "
               "DROP CONSTRAINT fk_schedule_import_customer")
    op.execute("ALTER TABLE erp.schedule_import DROP COLUMN applied_by, DROP COLUMN applied_at, "
               "DROP COLUMN customer_id")
    op.execute("DROP INDEX erp.ux_so_schedule_line_date")
    op.execute("ALTER TABLE erp.so_line DROP COLUMN shipped_qty")
    op.execute("DROP INDEX erp.ux_sales_order_customer_po")
    op.execute("ALTER TABLE erp.sales_order DROP CONSTRAINT ck_sales_order_status, "
               "DROP CONSTRAINT fk_sales_order_created_by, DROP CONSTRAINT fk_sales_order_quote")
    op.execute("ALTER TABLE erp.sales_order DROP COLUMN created_by, DROP COLUMN quote_id")
    op.execute("DROP TABLE erp.quotation_line")
    op.execute("DROP TABLE erp.quotation")
    op.execute("DROP TABLE erp.customer_price")
