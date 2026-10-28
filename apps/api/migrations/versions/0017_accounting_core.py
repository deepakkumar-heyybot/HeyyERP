"""Phase 10, part 1: chart of accounts, account roles, journals that cannot be altered once posted.

* account: account type check (asset, liability, equity, income, expense).
* account_role: which account a business event uses in a plant (receivable, payable, bank, ...).
* journal: date, narration, who created and posted it, the journal it reverses, status check.
* Database guards: a posted journal must balance and have at least two lines; the lines and
  header of a posted journal can never change, except posted -> reversed. Draft journals may
  be edited and deleted.
* fiscal_period: who closed it.
"""

from alembic import op

revision = "0017"
down_revision = "0016"

UPGRADE = """
ALTER TABLE erp.account ADD CONSTRAINT ck_account_type CHECK (account_type IN
    ('ASSET', 'LIABILITY', 'EQUITY', 'INCOME', 'EXPENSE'));

CREATE TABLE erp.account_role (
    tenant_id uuid NOT NULL REFERENCES erp.tenant (tenant_id),
    unit_id uuid NOT NULL,
    role text NOT NULL,
    account_id uuid NOT NULL,
    PRIMARY KEY (unit_id, role),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES erp.unit (tenant_id, unit_id),
    FOREIGN KEY (tenant_id, account_id) REFERENCES erp.account (tenant_id, account_id)
);
ALTER TABLE erp.account_role ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erp.account_role
    USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON erp.account_role TO heyyerp_app_rw;

ALTER TABLE erp.journal
    ADD COLUMN journal_date date NOT NULL DEFAULT CURRENT_DATE,
    ADD COLUMN narration text,
    ADD COLUMN created_by uuid,
    ADD COLUMN posted_by uuid,
    ADD COLUMN reverses_journal_id uuid;
ALTER TABLE erp.journal
    ADD CONSTRAINT fk_journal_created_by FOREIGN KEY (tenant_id, created_by)
        REFERENCES erp.app_user (tenant_id, user_id),
    ADD CONSTRAINT fk_journal_posted_by FOREIGN KEY (tenant_id, posted_by)
        REFERENCES erp.app_user (tenant_id, user_id),
    ADD CONSTRAINT fk_journal_reverses FOREIGN KEY (tenant_id, reverses_journal_id)
        REFERENCES erp.journal (tenant_id, journal_id),
    ADD CONSTRAINT ck_journal_status CHECK (status IN ('DRAFT', 'POSTED', 'REVERSED'));
CREATE INDEX ix_journal_unit_date ON erp.journal (tenant_id, unit_id, journal_date);
CREATE INDEX ix_journal_line_account ON erp.journal_line (tenant_id, account_id);
CREATE UNIQUE INDEX ux_journal_source
    ON erp.journal (tenant_id, source_type, source_id) WHERE status <> 'REVERSED' AND
    source_type <> 'MANUAL';

ALTER TABLE erp.fiscal_period ADD COLUMN closed_by uuid;
ALTER TABLE erp.fiscal_period ADD CONSTRAINT fk_fiscal_period_closed_by
    FOREIGN KEY (tenant_id, closed_by) REFERENCES erp.app_user (tenant_id, user_id);

CREATE FUNCTION erp.guard_journal_line() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_status text;
BEGIN
    SELECT status INTO parent_status FROM erp.journal
     WHERE journal_id = COALESCE(NEW.journal_id, OLD.journal_id);
    IF parent_status IS DISTINCT FROM 'DRAFT' THEN
        RAISE EXCEPTION 'The lines of a % journal cannot be changed', lower(parent_status);
    END IF;
    RETURN COALESCE(NEW, OLD);
END $$;
CREATE TRIGGER journal_line_guard BEFORE INSERT OR UPDATE OR DELETE ON erp.journal_line
    FOR EACH ROW EXECUTE FUNCTION erp.guard_journal_line();

CREATE FUNCTION erp.guard_journal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE total_debit numeric; total_credit numeric; line_count integer;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'A % journal cannot be deleted', lower(OLD.status);
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status = 'DRAFT' AND NEW.status = 'POSTED' THEN
        SELECT COALESCE(sum(debit), 0), COALESCE(sum(credit), 0), count(*)
          INTO total_debit, total_credit, line_count
          FROM erp.journal_line WHERE journal_id = NEW.journal_id;
        IF line_count < 2 OR total_debit <> total_credit OR total_debit = 0 THEN
            RAISE EXCEPTION 'A journal must have two or more lines and balance (debit % credit %)',
                total_debit, total_credit;
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status = 'POSTED' AND NEW.status = 'REVERSED' AND
       (NEW.journal_no, NEW.unit_id, NEW.journal_date, NEW.source_type, NEW.source_id,
        NEW.period_id, NEW.posted_at) IS NOT DISTINCT FROM
       (OLD.journal_no, OLD.unit_id, OLD.journal_date, OLD.source_type, OLD.source_id,
        OLD.period_id, OLD.posted_at) THEN
        RETURN NEW;
    END IF;
    IF OLD.status = 'DRAFT' AND NEW.status = 'DRAFT' THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'A % journal cannot be changed', lower(OLD.status);
END $$;
CREATE TRIGGER journal_guard BEFORE UPDATE OR DELETE ON erp.journal
    FOR EACH ROW EXECUTE FUNCTION erp.guard_journal();
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP TRIGGER journal_guard ON erp.journal")
    op.execute("DROP FUNCTION erp.guard_journal()")
    op.execute("DROP TRIGGER journal_line_guard ON erp.journal_line")
    op.execute("DROP FUNCTION erp.guard_journal_line()")
    op.execute("ALTER TABLE erp.fiscal_period DROP CONSTRAINT fk_fiscal_period_closed_by, "
               "DROP COLUMN closed_by")
    op.execute("DROP INDEX erp.ux_journal_source")
    op.execute("DROP INDEX erp.ix_journal_line_account")
    op.execute("DROP INDEX erp.ix_journal_unit_date")
    op.execute("ALTER TABLE erp.journal DROP CONSTRAINT ck_journal_status, "
               "DROP CONSTRAINT fk_journal_reverses, DROP CONSTRAINT fk_journal_posted_by, "
               "DROP CONSTRAINT fk_journal_created_by")
    op.execute("ALTER TABLE erp.journal DROP COLUMN reverses_journal_id, DROP COLUMN posted_by, "
               "DROP COLUMN created_by, DROP COLUMN narration, DROP COLUMN journal_date")
    op.execute("DROP TABLE erp.account_role")
    op.execute("ALTER TABLE erp.account DROP CONSTRAINT ck_account_type")
