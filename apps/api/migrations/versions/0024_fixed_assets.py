"""Phase 11, part 2: fixed asset register and depreciation runs.

* fixed_asset: residual value, depreciation method (SLM or WDV) and rate for the Companies Act
  book, the Income-tax WDV rate, disposal, and the machine the asset is.
* depreciation_run: which book it is for (COMPANIES_ACT is posted to the accounts, INCOME_TAX is a
  tax schedule), its journal and the month; one live run per plant, month and book.
* depreciation_line: opening and closing book value.
"""

from alembic import op

revision = "0024"
down_revision = "0023"

UPGRADE = """
ALTER TABLE erp.fixed_asset
    ADD COLUMN category text,
    ADD COLUMN salvage_value numeric(18,2) NOT NULL DEFAULT 0 CHECK (salvage_value >= 0),
    ADD COLUMN method text NOT NULL DEFAULT 'SLM' CHECK (method IN ('SLM', 'WDV')),
    ADD COLUMN wdv_rate numeric(7,4) CHECK (wdv_rate > 0 AND wdv_rate <= 100),
    ADD COLUMN it_rate numeric(7,4) CHECK (it_rate > 0 AND it_rate <= 100),
    ADD COLUMN machine_id uuid,
    ADD COLUMN disposed_on date,
    ADD COLUMN disposal_value numeric(18,2) CHECK (disposal_value >= 0),
    ADD CONSTRAINT ck_asset_status CHECK (status IN ('ACTIVE', 'DISPOSED')),
    ADD CONSTRAINT ck_asset_salvage CHECK (salvage_value <= acquisition_cost),
    ADD CONSTRAINT ck_asset_wdv CHECK (method <> 'WDV' OR wdv_rate IS NOT NULL),
    ADD CONSTRAINT ck_asset_disposed CHECK ((status = 'DISPOSED') = (disposed_on IS NOT NULL)),
    ADD CONSTRAINT fk_asset_machine FOREIGN KEY (tenant_id, machine_id)
        REFERENCES erp.machine (tenant_id, machine_id);

ALTER TABLE erp.depreciation_run
    ADD COLUMN books text NOT NULL DEFAULT 'COMPANIES_ACT'
        CHECK (books IN ('COMPANIES_ACT', 'INCOME_TAX')),
    ADD COLUMN journal_id uuid, ADD COLUMN posted_at timestamptz,
    ADD COLUMN created_by uuid,
    ADD CONSTRAINT ck_dep_run_status CHECK (status IN ('DRAFT', 'POSTED', 'CANCELLED')),
    ADD CONSTRAINT fk_dep_run_journal FOREIGN KEY (tenant_id, journal_id)
        REFERENCES erp.journal (tenant_id, journal_id);
CREATE UNIQUE INDEX ux_dep_run_month ON erp.depreciation_run (unit_id, period_id, books)
    WHERE status <> 'CANCELLED';

ALTER TABLE erp.depreciation_line
    ADD COLUMN opening_value numeric(18,2) NOT NULL DEFAULT 0,
    ADD COLUMN closing_value numeric(18,2) NOT NULL DEFAULT 0;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("ALTER TABLE erp.depreciation_line DROP COLUMN closing_value, "
               "DROP COLUMN opening_value")
    op.execute("DROP INDEX erp.ux_dep_run_month")
    op.execute("ALTER TABLE erp.depreciation_run DROP CONSTRAINT fk_dep_run_journal, "
               "DROP CONSTRAINT ck_dep_run_status, DROP COLUMN created_by, DROP COLUMN posted_at, "
               "DROP COLUMN journal_id, DROP COLUMN books")
    op.execute("ALTER TABLE erp.fixed_asset DROP CONSTRAINT fk_asset_machine, "
               "DROP CONSTRAINT ck_asset_disposed, DROP CONSTRAINT ck_asset_wdv, "
               "DROP CONSTRAINT ck_asset_salvage, DROP CONSTRAINT ck_asset_status, "
               "DROP COLUMN disposal_value, DROP COLUMN disposed_on, DROP COLUMN machine_id, "
               "DROP COLUMN it_rate, DROP COLUMN wdv_rate, DROP COLUMN method, "
               "DROP COLUMN salvage_value, DROP COLUMN category")
