"""Baseline erp schema: tables, constraints, RLS policies and views.

The SQL is a frozen copy of heyyERP_postgresql_schema.sql at the time migrations were
introduced. Change the schema through new revisions, not by editing this file.
"""

import re
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None

BASELINE = Path(__file__).resolve().parent.parent / "sql" / "0001_baseline.sql"


def upgrade() -> None:
    # Alembic owns the transaction, so drop the script's own BEGIN / COMMIT.
    script = re.sub(r"^(BEGIN|COMMIT);\s*$", "", BASELINE.read_text(encoding="utf-8"), flags=re.M)
    # Raw cursor: the script has % characters (format(), RAISE) that must not be parameter-parsed.
    cursor = op.get_bind().connection.cursor()
    cursor.execute(script)
    cursor.execute("RESET search_path")


def downgrade() -> None:
    op.execute("DROP SCHEMA erp CASCADE")
