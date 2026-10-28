"""Double-entry journals, vouchers, reversals, ledger statements and the trial balance
(BRD FR-FIN-01, FR-FIN-05).

A journal is a set of debit and credit lines in one plant that add up to the same total. It is
drafted, edited while a draft, then posted: posting checks the accounts, the balance and the
period lock, numbers it JV-yy-nnnn and makes it permanent. The database itself refuses to
change a posted journal or its lines, or to post one that does not balance. A mistake is
corrected by reversing the journal (a new journal with debit and credit swapped, linked to the
original), never by editing it. Other modules post through `post_entry`, which is idempotent per
source document. Needs fin.voucher to draft, post and reverse; reading needs the finance module.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Self
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from heyyerp_api.finance.period import ensure_accounting_open, month_start
from heyyerp_api.masters.common import (
    Ctx,
    Page,
    Paging,
    Strict,
    audit,
    count,
    not_found,
    one,
    require_in_scope,
    requires,
    requires_module,
    rows,
    scope_clause,
)
from heyyerp_api.procurement.numbering import next_number

router = APIRouter(prefix="/finance", tags=["finance"])
manage = requires("fin.voucher")
read = requires_module("finance")
Amount = Field(default=Decimal(0), ge=0, le=Decimal(10_000_000_000), max_digits=18,
               decimal_places=2)
PartyType = Literal["CUSTOMER", "VENDOR"]


class LineIn(Strict):
    account_id: UUID
    debit: Decimal = Amount
    credit: Decimal = Amount
    description: str | None = Field(default=None, max_length=200)
    party_type: PartyType | None = None
    party_id: UUID | None = None

    @model_validator(mode="after")
    def _one_side(self) -> Self:
        if (self.debit > 0) == (self.credit > 0):
            raise ValueError("A line is either a debit or a credit, with an amount above zero")
        if (self.party_type is None) != (self.party_id is None):
            raise ValueError("Give the party type and the party together")
        return self


class JournalIn(Strict):
    unit_id: UUID
    journal_date: date | None = None
    narration: str | None = Field(default=None, max_length=300)
    lines: list[LineIn] = Field(min_length=2, max_length=200)


class ReverseIn(Strict):
    reason: str = Field(min_length=3, max_length=200)
    journal_date: date | None = None


class LineOut(BaseModel):
    line_no: int
    account_id: UUID
    account_code: str
    account_name: str
    debit: Decimal
    credit: Decimal
    description: str | None
    party_type: str | None
    party_id: UUID | None


class JournalSummary(BaseModel):
    journal_id: UUID
    journal_no: str
    unit_id: UUID
    journal_date: date
    source_type: str
    source_id: UUID
    status: str
    narration: str | None
    total_debit: Decimal
    total_credit: Decimal
    posted_at: datetime | None
    posted_by_name: str | None
    reverses_journal_id: UUID | None


class JournalOut(JournalSummary):
    lines: list[LineOut]


JOURNAL_SQL = (
    "SELECT j.journal_id, j.journal_no, j.unit_id, j.journal_date, j.source_type, j.source_id, "
    "j.status, j.narration, COALESCE((SELECT sum(debit) FROM erp.journal_line l WHERE l.journal_id "
    "= j.journal_id), 0) AS total_debit, COALESCE((SELECT sum(credit) FROM erp.journal_line l "
    "WHERE l.journal_id = j.journal_id), 0) AS total_credit, j.posted_at, u.display_name AS "
    "posted_by_name, j.reverses_journal_id FROM erp.journal j LEFT JOIN erp.app_user u ON "
    "u.user_id = j.posted_by"
)


async def load(ctx: Ctx, journal_id: UUID) -> JournalOut | None:
    clause, params = scope_clause(ctx, "j.unit_id")
    head = await one(ctx, f"{JOURNAL_SQL} WHERE j.journal_id = :id"
                     f"{' AND ' + clause if clause else ''}", {**params, "id": journal_id})
    if head is None:
        return None
    lines = await rows(
        ctx, "SELECT l.line_no, l.account_id, a.code AS account_code, a.name AS account_name, "
        "l.debit, l.credit, l.description, l.party_type, l.party_id FROM erp.journal_line l JOIN "
        "erp.account a ON a.account_id = l.account_id WHERE l.journal_id = :id ORDER BY l.line_no",
        {"id": journal_id})
    return JournalOut(**head, lines=[LineOut(**ln) for ln in lines])


async def check_lines(ctx: Ctx, unit_id: UUID, lines: list[LineIn]) -> None:
    """Every account must be an active account of the plant; every party must exist."""
    accounts = {r["account_id"]: r for r in await rows(
        ctx, "SELECT account_id, unit_id, active, code FROM erp.account WHERE account_id = "
        "ANY(CAST(:ids AS uuid[]))", {"ids": [ln.account_id for ln in lines]})}
    for line in lines:
        account = accounts.get(line.account_id)
        if account is None or account["unit_id"] != unit_id:
            raise HTTPException(422, "Every account must belong to the journal's plant")
        if not account["active"]:
            raise HTTPException(422, f"Account {account['code']} is inactive")
        if line.party_id is not None:
            table = "customer" if line.party_type == "CUSTOMER" else "vendor"
            if await one(ctx, f"SELECT 1 AS x FROM erp.{table} WHERE {table}_id = :id",
                         {"id": line.party_id}) is None:
                raise HTTPException(422, f"The {table} does not exist")
    debit = sum((ln.debit for ln in lines), Decimal(0))
    credit = sum((ln.credit for ln in lines), Decimal(0))
    if debit != credit:
        raise HTTPException(422, f"The journal does not balance: debit {debit}, credit {credit}")


async def write_lines(ctx: Ctx, journal_id: UUID, lines: list[LineIn]) -> None:
    await rows(ctx, "DELETE FROM erp.journal_line WHERE journal_id = :id RETURNING journal_line_id",
               {"id": journal_id})
    for number, line in enumerate(lines, start=1):
        await rows(
            ctx, "INSERT INTO erp.journal_line(tenant_id, journal_id, account_id, line_no, "
            "description, debit, credit, party_type, party_id) VALUES (erp.current_tenant_id(), "
            ":j, :a, :n, :d, :dr, :cr, :pt, :pid) RETURNING journal_line_id",
            {"j": journal_id, "a": line.account_id, "n": number, "d": line.description,
             "dr": line.debit, "cr": line.credit, "pt": line.party_type, "pid": line.party_id})


async def create_draft(
    ctx: Ctx, unit_id: UUID, journal_date: date, narration: str | None, lines: list[LineIn],
    source_type: str = "MANUAL", source_id: UUID | None = None, reverses: UUID | None = None,
) -> UUID:
    journal_id = uuid4()
    period = await one(
        ctx, "SELECT period_id FROM erp.fiscal_period WHERE unit_id = :u AND year_month = :m",
        {"u": unit_id, "m": month_start(journal_date)})
    if period is None:
        from heyyerp_api.finance.period import get_period

        period = await get_period(ctx, unit_id, journal_date)
    await rows(
        ctx, "INSERT INTO erp.journal(journal_id, tenant_id, unit_id, period_id, journal_no, "
        "source_type, source_id, journal_date, narration, created_by, reverses_journal_id) VALUES "
        "(:id, erp.current_tenant_id(), :u, :p, :no, :st, :sid, :d, :n, :me, :rv) "
        "RETURNING journal_id",
        {"id": journal_id, "u": unit_id, "p": period["period_id"],
         "no": f"DRAFT-{journal_id.hex[:8].upper()}", "st": source_type,
         "sid": source_id or journal_id, "d": journal_date, "n": narration,
         "me": ctx.principal.profile.app_user_id, "rv": reverses})
    await write_lines(ctx, journal_id, lines)
    return journal_id


async def post_draft(ctx: Ctx, journal_id: UUID) -> None:
    """Number and post a draft: checks the period lock and the accounts again at posting time."""
    head = await one(
        ctx, "SELECT journal_id, unit_id, journal_date, status, source_type FROM erp.journal "
        "WHERE journal_id = :id FOR UPDATE", {"id": journal_id})
    if head is None:
        raise not_found("Journal")
    if head["status"] != "DRAFT":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The journal is {str(head['status']).lower()}")
    period = await ensure_accounting_open(ctx, head["unit_id"], head["journal_date"])
    stored = await rows(
        ctx, "SELECT account_id, debit, credit, party_type, party_id FROM erp.journal_line WHERE "
        "journal_id = :id", {"id": journal_id})
    await check_lines(ctx, head["unit_id"], [LineIn(**s) for s in stored])
    await rows(
        ctx, "UPDATE erp.journal SET status = 'POSTED', journal_no = :no, period_id = :p, "
        "posted_at = now(), posted_by = :me WHERE journal_id = :id RETURNING journal_id",
        {"no": await next_number(ctx, "journal", head["journal_date"]), "p": period["period_id"],
         "me": ctx.principal.profile.app_user_id, "id": journal_id})


async def post_entry(
    ctx: Ctx, *, unit_id: UUID, journal_date: date, source_type: str, source_id: UUID,
    narration: str, lines: list[LineIn], reverses: UUID | None = None,
) -> UUID:
    """Create and post a journal for a source document, once. Used by the other modules."""
    existing = await one(
        ctx, "SELECT journal_id FROM erp.journal WHERE source_type = :t AND source_id = :s AND "
        "status <> 'REVERSED'", {"t": source_type, "s": source_id})
    if existing is not None:
        return existing["journal_id"]  # type: ignore[no-any-return]
    await check_lines(ctx, unit_id, lines)
    journal_id = await create_draft(ctx, unit_id, journal_date, narration, lines, source_type,
                                    source_id, reverses)
    await post_draft(ctx, journal_id)
    return journal_id


# ------------------------------------------------------------------ endpoints
@router.post("/journals", response_model=JournalOut, status_code=status.HTTP_201_CREATED)
async def draft_journal(body: JournalIn, ctx: Ctx = Depends(manage)) -> JournalOut:
    require_in_scope(ctx, body.unit_id)
    unit = await one(ctx, "SELECT active FROM erp.unit WHERE unit_id = :id", {"id": body.unit_id})
    if unit is None or not unit["active"]:
        raise HTTPException(422, "The plant does not exist or is inactive")
    await check_lines(ctx, body.unit_id, body.lines)
    day = body.journal_date or date.today()
    journal_id = await create_draft(ctx, body.unit_id, day, body.narration, body.lines)
    await audit(ctx, "journal.draft", "journal", journal_id,
                {"unit_id": body.unit_id, "date": day, "lines": len(body.lines)})
    result = await load(ctx, journal_id)
    assert result is not None
    return result


async def require_draft(ctx: Ctx, journal_id: UUID) -> dict[str, Any]:
    clause, params = scope_clause(ctx, "unit_id")
    head = await one(
        ctx, "SELECT journal_id, journal_no, unit_id, status, journal_date FROM erp.journal "
        f"WHERE journal_id = :id{' AND ' + clause if clause else ''} FOR UPDATE",
        {**params, "id": journal_id})
    if head is None:
        raise not_found("Journal")
    if head["status"] != "DRAFT":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The journal is {str(head['status']).lower()} and cannot be changed")
    return head


@router.put("/journals/{journal_id}", response_model=JournalOut)
async def edit_draft(journal_id: UUID, body: JournalIn, ctx: Ctx = Depends(manage)) -> JournalOut:
    head = await require_draft(ctx, journal_id)
    if body.unit_id != head["unit_id"]:
        raise HTTPException(422, "A journal cannot move to another plant")
    await check_lines(ctx, body.unit_id, body.lines)
    day = body.journal_date or head["journal_date"]
    await rows(ctx, "UPDATE erp.journal SET journal_date = :d, narration = :n WHERE journal_id = "
               ":id RETURNING journal_id", {"d": day, "n": body.narration, "id": journal_id})
    await write_lines(ctx, journal_id, body.lines)
    await audit(ctx, "journal.edit", "journal", journal_id, {"lines": len(body.lines)})
    result = await load(ctx, journal_id)
    assert result is not None
    return result


@router.delete("/journals/{journal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft(journal_id: UUID, ctx: Ctx = Depends(manage)) -> None:
    """Only a draft can be thrown away; a posted journal is reversed instead."""
    await require_draft(ctx, journal_id)
    await rows(ctx, "DELETE FROM erp.journal_line WHERE journal_id = :id RETURNING journal_line_id",
               {"id": journal_id})
    await rows(ctx, "DELETE FROM erp.journal WHERE journal_id = :id RETURNING journal_id",
               {"id": journal_id})
    await audit(ctx, "journal.delete_draft", "journal", journal_id, {})


@router.post("/journals/{journal_id}/post", response_model=JournalOut)
async def post_journal(journal_id: UUID, ctx: Ctx = Depends(manage)) -> JournalOut:
    await require_draft(ctx, journal_id)
    await post_draft(ctx, journal_id)
    result = await load(ctx, journal_id)
    assert result is not None
    await audit(ctx, "journal.post", "journal", journal_id,
                {"journal_no": result.journal_no, "total": result.total_debit})
    return result


@router.post("/journals/{journal_id}/reverse", response_model=JournalOut,
             status_code=status.HTTP_201_CREATED)
async def reverse_journal(
    journal_id: UUID, body: ReverseIn, ctx: Ctx = Depends(manage)
) -> JournalOut:
    """Correct a posted journal with a new one that swaps debit and credit."""
    original = await load(ctx, journal_id)
    if original is None:
        raise not_found("Journal")
    if original.status != "POSTED":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The journal is {original.status.lower()} and cannot be reversed")
    if original.source_type == "REVERSAL":
        raise HTTPException(status.HTTP_409_CONFLICT, "A reversal cannot be reversed again")
    locked = await one(ctx, "SELECT status FROM erp.journal WHERE journal_id = :id FOR UPDATE",
                       {"id": journal_id})
    assert locked is not None
    if locked["status"] != "POSTED":
        raise HTTPException(status.HTTP_409_CONFLICT, "The journal was reversed meanwhile")
    swapped = [LineIn(account_id=ln.account_id, debit=ln.credit, credit=ln.debit,
                      description=f"Reversal: {body.reason.strip()}", party_type=ln.party_type,
                      party_id=ln.party_id) for ln in original.lines]
    reversal_id = await post_entry(
        ctx, unit_id=original.unit_id, journal_date=body.journal_date or date.today(),
        source_type="REVERSAL", source_id=journal_id,
        narration=f"Reversal of {original.journal_no}: {body.reason.strip()}", lines=swapped,
        reverses=journal_id)
    await rows(ctx, "UPDATE erp.journal SET status = 'REVERSED' WHERE journal_id = :id RETURNING "
               "journal_id", {"id": journal_id})
    await audit(ctx, "journal.reverse", "journal", journal_id,
                {"journal_no": original.journal_no, "reversal_id": reversal_id,
                 "reason": body.reason})
    result = await load(ctx, reversal_id)
    assert result is not None
    return result


@router.get("/journals", response_model=Page[JournalSummary])
async def list_journals(
    unit_id: UUID | None = None, journal_status: str | None = Query(None, alias="status"),
    source_type: str | None = None, date_from: date | None = None, date_to: date | None = None,
    account_id: UUID | None = None, page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[JournalSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "j.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for text_, key, value in (
            ("j.unit_id = :u", "u", unit_id),
            ("j.status = :st", "st", journal_status.upper() if journal_status else None),
            ("j.source_type = :src", "src", source_type.upper() if source_type else None),
            ("j.journal_date >= :f", "f", date_from), ("j.journal_date <= :t", "t", date_to)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    if account_id is not None:
        conditions.append("EXISTS (SELECT 1 FROM erp.journal_line l WHERE l.journal_id = "
                          "j.journal_id AND l.account_id = :acc)")
        params["acc"] = account_id
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{JOURNAL_SQL}{where} ORDER BY j.journal_date DESC, j.journal_no DESC "
                       "LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.journal j{where}", params)
    return Page(items=[JournalSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/journals/{journal_id}", response_model=JournalOut)
async def get_journal(journal_id: UUID, ctx: Ctx = Depends(read)) -> JournalOut:
    found = await load(ctx, journal_id)
    if found is None:
        raise not_found("Journal")
    return found


# ------------------------------------------------------------------ trial balance and ledger
class TrialRow(BaseModel):
    account_id: UUID
    code: str
    name: str
    account_type: str
    opening: Decimal  # positive is a debit balance, negative a credit balance
    debit: Decimal
    credit: Decimal
    closing: Decimal


class TrialBalance(BaseModel):
    unit_id: UUID
    date_from: date
    date_to: date
    rows: list[TrialRow]
    total_debit: Decimal
    total_credit: Decimal
    balanced: bool


def year_start(day: date) -> date:
    return date(day.year if day.month >= 4 else day.year - 1, 4, 1)


@router.get("/trial-balance", response_model=TrialBalance)
async def trial_balance(
    unit_id: UUID, date_from: date | None = None, date_to: date | None = None,
    ctx: Ctx = Depends(read),
) -> TrialBalance:
    """Per account: opening balance, debits and credits in the range, closing balance. Reversed
    journals count (their reversal offsets them), drafts do not."""
    require_in_scope(ctx, unit_id)
    end = date_to or date.today()
    start = date_from or year_start(end)
    if end < start:
        raise HTTPException(422, "The end date is before the start date")
    data = await rows(
        ctx,
        "SELECT a.account_id, a.code, a.name, a.account_type, COALESCE(sum(l.debit - l.credit) "
        "FILTER (WHERE j.journal_date < :f), 0) AS opening, COALESCE(sum(l.debit) FILTER (WHERE "
        "j.journal_date >= :f AND j.journal_date <= :t), 0) AS debit, COALESCE(sum(l.credit) "
        "FILTER (WHERE j.journal_date >= :f AND j.journal_date <= :t), 0) AS credit FROM "
        "erp.account a LEFT JOIN (erp.journal_line l JOIN erp.journal j ON j.journal_id = "
        "l.journal_id AND j.status IN ('POSTED', 'REVERSED') AND j.journal_date <= :t) ON "
        "l.account_id = a.account_id WHERE a.unit_id = :u GROUP BY a.account_id, a.code, a.name, "
        "a.account_type ORDER BY a.code", {"u": unit_id, "f": start, "t": end})
    out = [TrialRow(**d, closing=d["opening"] + d["debit"] - d["credit"]) for d in data
           if d["opening"] or d["debit"] or d["credit"]]
    total_debit = sum((r.debit for r in out), Decimal(0))
    total_credit = sum((r.credit for r in out), Decimal(0))
    return TrialBalance(
        unit_id=unit_id, date_from=start, date_to=end, rows=out, total_debit=total_debit,
        total_credit=total_credit,
        balanced=total_debit == total_credit and sum((r.closing for r in out), Decimal(0)) == 0)


class StatementLine(BaseModel):
    journal_id: UUID
    journal_no: str
    journal_date: date
    narration: str | None
    description: str | None
    debit: Decimal
    credit: Decimal
    balance: Decimal


class Statement(BaseModel):
    account_id: UUID
    code: str
    name: str
    date_from: date
    date_to: date
    opening: Decimal
    lines: list[StatementLine]
    closing: Decimal


@router.get("/accounts/{account_id}/ledger", response_model=Statement)
async def account_ledger(
    account_id: UUID, date_from: date | None = None, date_to: date | None = None,
    ctx: Ctx = Depends(read),
) -> Statement:
    account = await one(ctx, "SELECT account_id, unit_id, code, name FROM erp.account WHERE "
                        "account_id = :id", {"id": account_id})
    if account is None or not ctx.principal.may_see_unit(account["unit_id"]):
        raise not_found("Account")
    end = date_to or date.today()
    start = date_from or year_start(end)
    opening = await one(
        ctx, "SELECT COALESCE(sum(l.debit - l.credit), 0) AS v FROM erp.journal_line l JOIN "
        "erp.journal j ON j.journal_id = l.journal_id WHERE l.account_id = :a AND j.status IN "
        "('POSTED', 'REVERSED') AND j.journal_date < :f", {"a": account_id, "f": start})
    assert opening is not None
    data = await rows(
        ctx, "SELECT j.journal_id, j.journal_no, j.journal_date, j.narration, l.description, "
        "l.debit, l.credit FROM erp.journal_line l JOIN erp.journal j ON j.journal_id = "
        "l.journal_id WHERE l.account_id = :a AND j.status IN ('POSTED', 'REVERSED') AND "
        "j.journal_date >= :f AND j.journal_date <= :t ORDER BY j.journal_date, j.posted_at, "
        "j.journal_no, l.line_no", {"a": account_id, "f": start, "t": end})
    running = opening["v"]
    lines = []
    for d in data:
        running += d["debit"] - d["credit"]
        lines.append(StatementLine(**d, balance=running))
    return Statement(account_id=account_id, code=account["code"], name=account["name"],
                     date_from=start, date_to=end, opening=opening["v"], lines=lines,
                     closing=running)
