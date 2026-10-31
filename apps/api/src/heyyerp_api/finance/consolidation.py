"""Consolidated trial balance across plants with inter-unit elimination (BRD FR-FIN-06).

The plants' trial balances are added account by account (by account code). What plants owe each
other is not the group's money: inter-unit receivable and payable, and inter-unit sales and
purchases, are eliminated. When the two sides do not cancel (one plant booked an invoice the other
has not), the difference is reported so Accounts can chase it. Only plants the user may see are
included. Reading needs the finance module.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from heyyerp_api.finance.journals import year_start
from heyyerp_api.masters.common import Ctx, requires_module, rows, scope_clause

router = APIRouter(prefix="/finance", tags=["finance"])
read = requires_module("finance")
ZERO = Decimal(0)
BALANCE_PAIRS = (("INTER_UNIT_RECEIVABLE", "INTER_UNIT_PAYABLE"),
                 ("INTER_UNIT_SALES", "INTER_UNIT_PURCHASES"))


class ConsolidatedRow(BaseModel):
    code: str
    name: str
    account_type: str
    debit: Decimal
    credit: Decimal
    combined: Decimal  # the plants added together; positive is a debit balance
    eliminated: Decimal
    consolidated: Decimal
    inter_unit: bool


class Consolidation(BaseModel):
    date_from: date
    date_to: date
    unit_ids: list[UUID]
    rows: list[ConsolidatedRow]
    receivable_payable_difference: Decimal  # not zero: the plants' inter-unit books disagree
    sales_purchases_difference: Decimal
    balanced: bool


@router.get("/consolidation", response_model=Consolidation)
async def consolidation(
    date_from: date | None = None, date_to: date | None = None,
    unit_ids: list[UUID] | None = Query(None), ctx: Ctx = Depends(read),
) -> Consolidation:
    end = date_to or date.today()
    start = date_from or year_start(end)
    if end < start:
        raise HTTPException(422, "The end date is before the start date")
    clause, params = scope_clause(ctx, "a.unit_id")
    where = [clause] if clause else []
    if unit_ids:
        where.append("a.unit_id = ANY(CAST(:units AS uuid[]))")
        params["units"] = unit_ids
    data = await rows(
        ctx,
        "SELECT a.code, min(a.name) AS name, min(a.account_type) AS account_type, "
        "COALESCE(bool_or(r.role LIKE 'INTER_UNIT%'), false) AS inter_unit, "
        "COALESCE(sum(l.debit) FILTER (WHERE j.journal_date >= :f), 0) AS debit, "
        "COALESCE(sum(l.credit) FILTER (WHERE j.journal_date >= :f), 0) AS credit, "
        "COALESCE(sum(l.debit - l.credit), 0) AS combined FROM erp.account a LEFT JOIN "
        "erp.account_role r ON r.account_id = a.account_id LEFT JOIN (erp.journal_line l JOIN "
        "erp.journal j ON j.journal_id = l.journal_id AND j.status IN ('POSTED', 'REVERSED') AND "
        "j.journal_date <= :t) ON l.account_id = a.account_id"
        f"{' WHERE ' + ' AND '.join(where) if where else ''} GROUP BY a.code ORDER BY a.code",
        {**params, "f": start, "t": end})
    units = [u["unit_id"] for u in await rows(
        ctx, "SELECT DISTINCT a.unit_id FROM erp.account a"
        f"{' WHERE ' + ' AND '.join(where) if where else ''} ORDER BY a.unit_id", params)]
    out = [ConsolidatedRow(**d, eliminated=d["combined"] if d["inter_unit"] else ZERO,
                           consolidated=ZERO if d["inter_unit"] else d["combined"])
           for d in data if d["debit"] or d["credit"] or d["combined"]]
    roles = {role: ZERO for pair in BALANCE_PAIRS for role in pair}
    for r in await rows(
            ctx, "SELECT r.role, COALESCE(sum(l.debit - l.credit), 0) AS v FROM erp.account_role r "
            "JOIN erp.account a ON a.account_id = r.account_id LEFT JOIN (erp.journal_line l JOIN "
            "erp.journal j ON j.journal_id = l.journal_id AND j.status IN ('POSTED', 'REVERSED') "
            "AND j.journal_date <= :t) ON l.account_id = a.account_id WHERE r.role LIKE "
            "'INTER_UNIT%'" + (f" AND {clause}" if clause else "")
            + (" AND a.unit_id = ANY(CAST(:units AS uuid[]))" if unit_ids else "")
            + " GROUP BY r.role", {**params, "t": end}):
        roles[r["role"]] = r["v"]
    rp = roles["INTER_UNIT_RECEIVABLE"] + roles["INTER_UNIT_PAYABLE"]
    sp = roles["INTER_UNIT_SALES"] + roles["INTER_UNIT_PURCHASES"]
    return Consolidation(
        date_from=start, date_to=end, unit_ids=units, rows=out,
        receivable_payable_difference=rp, sales_purchases_difference=sp,
        balanced=sum((r.consolidated for r in out), ZERO) + sum(
            (r.eliminated for r in out), ZERO) == 0)
