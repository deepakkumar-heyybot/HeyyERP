"""Phase 4 support: item routes, technical specifications and engineering changes.

* item.route_code: the production route a made item (WIP / FG) follows, by code; the active
  version of that route is used, so a new route version needs no change to every item.
* item_specification: the technical specification of an item (drawing no., hardness, dimensions,
  weight...) as typed rows; quality inspection limits come from here in Phase 8.
* engineering_change: why a BOM revision was made, who asked, when it takes effect, and the
  approval it went through. New tables get row-level security like every other tenant table.
"""

from alembic import op

revision = "0006"
down_revision = "0005"

UPGRADE = """
ALTER TABLE erp.item ADD COLUMN route_code text;

CREATE TABLE erp.item_specification (
    spec_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    item_id uuid NOT NULL,
    name text NOT NULL,
    unit text,
    target_value numeric(18,6),
    min_value numeric(18,6),
    max_value numeric(18,6),
    text_value text,
    note text,
    UNIQUE (tenant_id, spec_id),
    UNIQUE (item_id, name),
    FOREIGN KEY (tenant_id, item_id) REFERENCES erp.item (tenant_id, item_id),
    CHECK (min_value IS NULL OR max_value IS NULL OR max_value >= min_value),
    CHECK (target_value IS NOT NULL OR min_value IS NOT NULL OR max_value IS NOT NULL
           OR text_value IS NOT NULL)
);

CREATE TABLE erp.engineering_change (
    change_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    item_id uuid NOT NULL,
    bom_id uuid NOT NULL,
    from_bom_id uuid,
    reason text NOT NULL,
    effective_from date NOT NULL,
    requested_by uuid,
    requested_at timestamptz NOT NULL DEFAULT now(),
    approval_id uuid,
    UNIQUE (tenant_id, change_id),
    UNIQUE (tenant_id, bom_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES erp.item (tenant_id, item_id),
    FOREIGN KEY (tenant_id, bom_id) REFERENCES erp.bom (tenant_id, bom_id),
    FOREIGN KEY (tenant_id, from_bom_id) REFERENCES erp.bom (tenant_id, bom_id),
    FOREIGN KEY (tenant_id, requested_by) REFERENCES erp.app_user (tenant_id, user_id),
    FOREIGN KEY (tenant_id, approval_id) REFERENCES erp.approval_instance (tenant_id, approval_id)
);

CREATE INDEX ix_engineering_change_item
    ON erp.engineering_change (tenant_id, item_id, requested_at DESC);
CREATE INDEX ix_item_specification_item ON erp.item_specification (tenant_id, item_id);

ALTER TABLE erp.item_specification ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.item_specification
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
ALTER TABLE erp.engineering_change ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.engineering_change
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());

GRANT SELECT, INSERT, UPDATE, DELETE ON erp.item_specification, erp.engineering_change
    TO heyyerp_app_rw;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP TABLE erp.engineering_change")
    op.execute("DROP TABLE erp.item_specification")
    op.execute("ALTER TABLE erp.item DROP COLUMN route_code")
