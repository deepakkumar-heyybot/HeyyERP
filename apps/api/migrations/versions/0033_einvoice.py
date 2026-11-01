"""E-invoice (IRN) and e-way bill through a GST service provider.

* unit and customer: the address the e-invoice names (the GSTIN gives the state).
* customer_invoice: the IRN's status, acknowledgement date, signed QR code, cancellation, the
  e-way bill's validity and transport details, and the provider's last error.
* integration_job: the operation's parameters, when it finished, a status check, and at most one
  open job per operation and record.
"""

from alembic import op

revision = "0033"
down_revision = "0032"

UPGRADE = """
ALTER TABLE erp.unit ADD COLUMN address_line text, ADD COLUMN city text,
    ADD COLUMN pincode varchar(6) CHECK (pincode ~ '^[1-9][0-9]{5}$');
ALTER TABLE erp.customer ADD COLUMN address_line text, ADD COLUMN city text,
    ADD COLUMN pincode varchar(6) CHECK (pincode ~ '^[1-9][0-9]{5}$');

ALTER TABLE erp.customer_invoice
    ADD COLUMN irn_status text NOT NULL DEFAULT 'NONE' CHECK (irn_status IN
        ('NONE', 'QUEUED', 'GENERATED', 'FAILED', 'CANCELLED')),
    ADD COLUMN irn_ack_date timestamptz, ADD COLUMN signed_qr text,
    ADD COLUMN irn_cancelled_at timestamptz,
    ADD COLUMN ewb_valid_till timestamptz, ADD COLUMN ewb_vehicle_no text,
    ADD COLUMN ewb_distance_km integer CHECK (ewb_distance_km >= 0),
    ADD COLUMN ewb_transport_mode text CHECK (ewb_transport_mode IN ('ROAD', 'RAIL', 'AIR', 'SHIP')),
    ADD COLUMN gsp_error text;
UPDATE erp.customer_invoice SET irn_status = 'GENERATED' WHERE irn IS NOT NULL;

ALTER TABLE erp.integration_job
    ADD COLUMN payload jsonb NOT NULL DEFAULT '{}'::jsonb, ADD COLUMN completed_at timestamptz,
    ADD CONSTRAINT ck_integration_job_status CHECK (status IN
        ('QUEUED', 'RETRY', 'DONE', 'FAILED'));
CREATE UNIQUE INDEX ux_integration_job_open ON erp.integration_job
    (tenant_id, integration_type, operation, reference_id) WHERE status IN ('QUEUED', 'RETRY');
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP INDEX erp.ux_integration_job_open")
    op.execute("ALTER TABLE erp.integration_job DROP CONSTRAINT ck_integration_job_status, "
               "DROP COLUMN completed_at, DROP COLUMN payload")
    op.execute("ALTER TABLE erp.customer_invoice DROP COLUMN gsp_error, DROP COLUMN "
               "ewb_transport_mode, DROP COLUMN ewb_distance_km, DROP COLUMN ewb_vehicle_no, "
               "DROP COLUMN ewb_valid_till, DROP COLUMN irn_cancelled_at, DROP COLUMN signed_qr, "
               "DROP COLUMN irn_ack_date, DROP COLUMN irn_status")
    op.execute("ALTER TABLE erp.customer DROP COLUMN pincode, DROP COLUMN city, "
               "DROP COLUMN address_line")
    op.execute("ALTER TABLE erp.unit DROP COLUMN pincode, DROP COLUMN city, "
               "DROP COLUMN address_line")
