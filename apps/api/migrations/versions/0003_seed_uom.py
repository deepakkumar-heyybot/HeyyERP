"""Seed common units of measure; the API only reads them.

Units of measure are global (no tenant_id). The seed is a sensible starting set; V-Rub confirms
the real list with its item master (BRD section 10.2). Revoking write access keeps a bug in the
API from changing a table every tenant shares.
"""

from alembic import op

revision = "0003"
down_revision = "0002"

UOMS = [
    ("KG", "Kilogram", 3), ("G", "Gram", 3), ("MT", "Metric tonne", 3),
    ("L", "Litre", 3), ("ML", "Millilitre", 3),
    ("M", "Metre", 3), ("MM", "Millimetre", 2), ("SQM", "Square metre", 3),
    ("NOS", "Numbers", 0), ("PCS", "Pieces", 0), ("SET", "Set", 0), ("PAIR", "Pair", 0),
    ("BOX", "Box", 0), ("ROLL", "Roll", 0),
]


def upgrade() -> None:
    values = ", ".join(f"('{code}', '{name}', {places})" for code, name, places in UOMS)
    op.execute(
        f"INSERT INTO erp.uom(code, name, decimal_places) VALUES {values} "
        "ON CONFLICT (code) DO NOTHING"
    )
    op.execute("REVOKE INSERT, UPDATE, DELETE ON erp.uom FROM heyyerp_app_rw")


def downgrade() -> None:
    op.execute("GRANT INSERT, UPDATE, DELETE ON erp.uom TO heyyerp_app_rw")
    codes = ", ".join(f"'{code}'" for code, _, _ in UOMS)
    op.execute(f"DELETE FROM erp.uom WHERE code IN ({codes})")
