"""Customer receipts, ageing, collections and statements (BRD FR-FIN-04, FR-SAL-09).

A receipt records money received from a customer: debit bank, credit the customer's receivable.
It can be applied to invoices at once or later; the part not applied stays "on account" and counts
in the customer's statement and credit position. An invoice is paid when its applied receipts add up
to its amount. A receipt that bounces or was entered wrongly is reversed: its journal is reversed,
its applications are removed and the invoices are owed again. Ageing groups what customers owe by
how late it is (not yet due, 1-30, 31-60, 61-90, over 90 days); the collections list is the overdue
invoices, latest first. Needs fin.voucher to enter or change receipts; reading needs the
finance module.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from heyyerp_api.finance.accounts import role_account
from heyyerp_api.finance.autopost import finance_enabled
from heyyerp_api.finance.journals import LineIn, ReverseIn, post_entry, reverse_journal
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
Money = Field(gt=0, le=Decimal(10_000_000_000), max_digits=18, decimal_places=2)


class AllocIn(Strict):
    invoice_id: UUID
    amount: Decimal = Money


class ReceiptIn(Strict):
    unit_id: UUID
    customer_id: UUID
    amount: Decimal = Money
    receipt_date: date | None = None
    reference: str = Field(min_length=1, max_length=60)  # cheque, UTR or transfer reference
    allocations: list[AllocIn] = Field(default_factory=list, max_length=200)


class AllocateIn(Strict):
    allocations: list[AllocIn] = Field(min_length=1, max_length=200)


class ReverseReceiptIn(Strict):
    reason: str = Field(min_length=3, max_length=200)


class ReceiptAllocation(BaseModel):
    invoice_id: UUID
    invoice_no: str
    allocated_amount: Decimal


class ReceiptSummary(BaseModel):
    payment_id: UUID
    receipt_no: str
    unit_id: UUID
    customer_id: UUID
    customer_code: str
    amount: Decimal
    applied: Decimal
    on_account: Decimal
    receipt_date: date | None
    reference: str | None
    status: str


class ReceiptOut(ReceiptSummary):
    allocations: list[ReceiptAllocation]
    journal_id: UUID | None


RECEIPT_SQL = (
    "SELECT p.payment_id, p.payment_no AS receipt_no, p.unit_id, p.customer_id, c.code AS "
    "customer_code, p.amount, COALESCE((SELECT sum(a.allocated_amount) FROM "
    "erp.payment_allocation a WHERE a.payment_id = p.payment_id), 0) AS applied, p.amount - "
    "COALESCE((SELECT sum(a.allocated_amount) FROM erp.payment_allocation a WHERE a.payment_id = "
    "p.payment_id), 0) AS on_account, p.payment_date AS receipt_date, p.bank_reference AS "
    "reference, p.status FROM erp.payment p JOIN erp.customer c ON c.customer_id = p.customer_id"
)


async def load(ctx: Ctx, payment_id: UUID) -> ReceiptOut | None:
    clause, params = scope_clause(ctx, "p.unit_id")
    head = await one(ctx, f"{RECEIPT_SQL} WHERE p.payment_id = :id"
                     f"{' AND ' + clause if clause else ''}", {**params, "id": payment_id})
    if head is None:
        return None
    allocations = await rows(
        ctx, "SELECT a.invoice_id, v.invoice_no, a.allocated_amount FROM erp.payment_allocation a "
        "JOIN erp.customer_invoice v ON v.invoice_id = a.invoice_id WHERE a.payment_id = :id "
        "ORDER BY v.invoice_no", {"id": payment_id})
    journal = await one(ctx, "SELECT journal_id FROM erp.payment WHERE payment_id = :id",
                        {"id": payment_id})
    assert journal is not None
    return ReceiptOut(**head, allocations=[ReceiptAllocation(**a) for a in allocations],
                      journal_id=journal["journal_id"])


async def apply_to_invoices(
    ctx: Ctx, payment_id: UUID, customer_id: UUID, unit_id: UUID, room: Decimal,
    allocations: list[AllocIn],
) -> None:
    """Apply part of a receipt to the customer's invoices; checks the room left and what is owed."""
    if not allocations:
        return
    ids = [a.invoice_id for a in allocations]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Each invoice may appear only once")
    total = sum((a.amount for a in allocations), Decimal(0))
    if total > room:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Only {room} of this receipt is left to apply, {total} asked")
    invoices = {r["invoice_id"]: r for r in await rows(
        ctx, "SELECT invoice_id, invoice_no, customer_id, unit_id, status, taxable_amount + "
        "tax_amount - paid_amount AS owed FROM erp.customer_invoice WHERE invoice_id = "
        "ANY(CAST(:ids AS uuid[])) ORDER BY invoice_id FOR UPDATE", {"ids": ids})}
    for a in allocations:
        inv = invoices.get(a.invoice_id)
        if inv is None or inv["customer_id"] != customer_id or inv["unit_id"] != unit_id:
            raise HTTPException(422, "Every invoice must belong to this customer and plant")
        if inv["status"] != "ISSUED":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Invoice {inv['invoice_no']} is {str(inv['status']).lower()}")
        if a.amount > inv["owed"]:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Invoice {inv['invoice_no']}: {a.amount} applied but only "
                                f"{inv['owed']} is owed")
    for a in allocations:
        await rows(
            ctx, "INSERT INTO erp.payment_allocation(tenant_id, payment_id, invoice_id, "
            "allocated_amount) VALUES (erp.current_tenant_id(), :p, :i, :a) ON CONFLICT "
            "(payment_id, invoice_id) WHERE invoice_id IS NOT NULL DO UPDATE SET "
            "allocated_amount = payment_allocation.allocated_amount + EXCLUDED.allocated_amount "
            "RETURNING allocation_id", {"p": payment_id, "i": a.invoice_id, "a": a.amount})
        await rows(ctx, "UPDATE erp.customer_invoice SET paid_amount = paid_amount + :a WHERE "
                   "invoice_id = :i RETURNING invoice_id", {"a": a.amount, "i": a.invoice_id})


@router.post("/receipts", response_model=ReceiptOut, status_code=status.HTTP_201_CREATED)
async def enter_receipt(body: ReceiptIn, ctx: Ctx = Depends(manage)) -> ReceiptOut:
    require_in_scope(ctx, body.unit_id)
    day = body.receipt_date or date.today()
    if day > date.today():
        raise HTTPException(422, "The receipt date is in the future")
    customer = await one(ctx, "SELECT active FROM erp.customer WHERE customer_id = :id",
                         {"id": body.customer_id})
    if customer is None or not customer["active"]:
        raise HTTPException(422, "The customer does not exist or is inactive")
    if not await finance_enabled(ctx, body.unit_id):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Set up the plant's chart of accounts before entering receipts")
    created = await one(
        ctx, "INSERT INTO erp.payment(tenant_id, unit_id, payment_no, customer_id, amount, "
        "payment_date, status, created_by, paid_by, paid_at, bank_reference) VALUES "
        "(erp.current_tenant_id(), :u, :n, :c, :a, :d, 'RECEIVED', :me, :me, now(), :ref) "
        "RETURNING payment_id",
        {"u": body.unit_id, "n": await next_number(ctx, "receipt"), "c": body.customer_id,
         "a": body.amount, "d": day, "me": ctx.principal.profile.app_user_id,
         "ref": body.reference})
    assert created is not None
    payment_id: UUID = created["payment_id"]
    await apply_to_invoices(ctx, payment_id, body.customer_id, body.unit_id, body.amount,
                            body.allocations)
    journal_id = await post_entry(
        ctx, unit_id=body.unit_id, journal_date=day, source_type="RECEIPT", source_id=payment_id,
        narration=f"Receipt {body.reference}",
        lines=[LineIn(account_id=await role_account(ctx, body.unit_id, "BANK"),
                      debit=body.amount),
               LineIn(account_id=await role_account(ctx, body.unit_id, "RECEIVABLE"),
                      credit=body.amount, party_type="CUSTOMER", party_id=body.customer_id,
                      description=f"Receipt {body.reference}")])
    await rows(ctx, "UPDATE erp.payment SET journal_id = :j WHERE payment_id = :id RETURNING "
               "payment_id", {"j": journal_id, "id": payment_id})
    await audit(ctx, "receipt.enter", "payment", payment_id,
                {"customer_id": body.customer_id, "amount": body.amount,
                 "reference": body.reference, "applied": len(body.allocations)})
    result = await load(ctx, payment_id)
    assert result is not None
    return result


async def require_receipt(ctx: Ctx, payment_id: UUID) -> dict[str, Any]:
    clause, params = scope_clause(ctx, "unit_id")
    head = await one(
        ctx, "SELECT payment_id, unit_id, customer_id, amount, status, journal_id, payment_no FROM "
        f"erp.payment WHERE payment_id = :id AND customer_id IS NOT NULL"
        f"{' AND ' + clause if clause else ''} FOR UPDATE", {**params, "id": payment_id})
    if head is None:
        raise not_found("Receipt")
    if head["status"] != "RECEIVED":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The receipt is {str(head['status']).lower()}")
    return head


@router.post("/receipts/{payment_id}/apply", response_model=ReceiptOut)
async def apply_receipt(
    payment_id: UUID, body: AllocateIn, ctx: Ctx = Depends(manage)
) -> ReceiptOut:
    """Apply the part of a receipt that is still on account to more invoices."""
    head = await require_receipt(ctx, payment_id)
    applied = await one(ctx, "SELECT COALESCE(sum(allocated_amount), 0) AS a FROM "
                        "erp.payment_allocation WHERE payment_id = :id", {"id": payment_id})
    assert applied is not None
    await apply_to_invoices(ctx, payment_id, head["customer_id"], head["unit_id"],
                            head["amount"] - applied["a"], body.allocations)
    await audit(ctx, "receipt.apply", "payment", payment_id,
                {"invoices": len(body.allocations)})
    result = await load(ctx, payment_id)
    assert result is not None
    return result


@router.post("/receipts/{payment_id}/reverse", response_model=ReceiptOut)
async def reverse_receipt(
    payment_id: UUID, body: ReverseReceiptIn, ctx: Ctx = Depends(manage)
) -> ReceiptOut:
    """A bounced or wrong receipt: reverse the books, release the invoices, mark it cancelled."""
    head = await require_receipt(ctx, payment_id)
    for a in await rows(ctx, "SELECT invoice_id, allocated_amount FROM erp.payment_allocation "
                        "WHERE payment_id = :id ORDER BY invoice_id", {"id": payment_id}):
        await rows(ctx, "UPDATE erp.customer_invoice SET paid_amount = paid_amount - :a WHERE "
                   "invoice_id = :i RETURNING invoice_id",
                   {"a": a["allocated_amount"], "i": a["invoice_id"]})
    await rows(ctx, "DELETE FROM erp.payment_allocation WHERE payment_id = :id RETURNING "
               "allocation_id", {"id": payment_id})
    if head["journal_id"] is not None:
        await reverse_journal(head["journal_id"], ReverseIn(
            reason=f"Receipt {head['payment_no']} reversed: {body.reason.strip()}"), ctx)
    await rows(ctx, "UPDATE erp.payment SET status = 'CANCELLED' WHERE payment_id = :id RETURNING "
               "payment_id", {"id": payment_id})
    await audit(ctx, "receipt.reverse", "payment", payment_id,
                {"payment_no": head["payment_no"], "reason": body.reason})
    result = await load(ctx, payment_id)
    assert result is not None
    return result


@router.get("/receipts", response_model=Page[ReceiptSummary])
async def list_receipts(
    unit_id: UUID | None = None, customer_id: UUID | None = None,
    receipt_status: str | None = Query(None, alias="status"),
    unapplied: bool | None = Query(None, description="only receipts with money still on account"),
    page: Paging = Depends(), ctx: Ctx = Depends(read),
) -> Page[ReceiptSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "p.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for text_, key, value in (
            ("p.unit_id = :u", "u", unit_id), ("p.customer_id = :c", "c", customer_id),
            ("p.status = :st", "st", receipt_status.upper() if receipt_status else None)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    if unapplied:
        conditions.append("p.status = 'RECEIVED' AND p.amount > COALESCE((SELECT "
                          "sum(a.allocated_amount) FROM erp.payment_allocation a WHERE "
                          "a.payment_id = p.payment_id), 0)")
    where = " WHERE p.customer_id IS NOT NULL" + "".join(f" AND {c}" for c in conditions)
    items = await rows(ctx, f"{RECEIPT_SQL}{where} ORDER BY p.created_at DESC, p.payment_no DESC "
                       "LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, "SELECT count(*) FROM erp.payment p JOIN erp.customer c ON "
                        f"c.customer_id = p.customer_id{where}", params)
    return Page(items=[ReceiptSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/receipts/{payment_id}", response_model=ReceiptOut)
async def get_receipt(payment_id: UUID, ctx: Ctx = Depends(read)) -> ReceiptOut:
    found = await load(ctx, payment_id)
    if found is None:
        raise not_found("Receipt")
    return found


# ------------------------------------------------------------------ ageing, collections, statement
BUCKETS = ("not_due", "d1_30", "d31_60", "d61_90", "over_90")


class AgeingRow(BaseModel):
    customer_id: UUID
    customer_code: str
    customer_name: str
    not_due: Decimal
    d1_30: Decimal
    d31_60: Decimal
    d61_90: Decimal
    over_90: Decimal
    total: Decimal
    on_account: Decimal  # receipts not yet applied
    net: Decimal  # total owed less money on account


class Ageing(BaseModel):
    unit_id: UUID
    as_of: date
    rows: list[AgeingRow]
    totals: dict[str, Decimal]


@router.get("/receivables/ageing", response_model=Ageing)
async def ageing(unit_id: UUID, as_of: date | None = None, ctx: Ctx = Depends(read)) -> Ageing:
    """What each customer owes, by how late it is on the date (default today)."""
    require_in_scope(ctx, unit_id)
    day = as_of or date.today()
    data = await rows(
        ctx,
        "SELECT v.customer_id, c.code AS customer_code, c.name AS customer_name, "
        "COALESCE(sum(o) FILTER (WHERE late <= 0), 0) AS not_due, COALESCE(sum(o) FILTER "
        "(WHERE late BETWEEN 1 AND 30), 0) AS d1_30, COALESCE(sum(o) FILTER (WHERE late BETWEEN "
        "31 AND 60), 0) AS d31_60, COALESCE(sum(o) FILTER (WHERE late BETWEEN 61 AND 90), 0) AS "
        "d61_90, COALESCE(sum(o) FILTER (WHERE late > 90), 0) AS over_90, sum(o) AS total FROM "
        "(SELECT customer_id, taxable_amount + tax_amount - paid_amount AS o, "
        "CAST(:d AS date) - COALESCE(due_date, issue_date) AS late FROM erp.customer_invoice "
        "WHERE unit_id = :u AND status = 'ISSUED' AND issue_date <= :d AND taxable_amount + "
        "tax_amount > paid_amount) v JOIN erp.customer c ON c.customer_id = v.customer_id GROUP BY "
        "v.customer_id, c.code, c.name ORDER BY c.code", {"u": unit_id, "d": day})
    unapplied = {r["customer_id"]: r["v"] for r in await rows(
        ctx, "SELECT p.customer_id, sum(p.amount - COALESCE((SELECT sum(a.allocated_amount) FROM "
        "erp.payment_allocation a WHERE a.payment_id = p.payment_id), 0)) AS v FROM erp.payment p "
        "WHERE p.unit_id = :u AND p.status = 'RECEIVED' AND p.customer_id IS NOT NULL AND "
        "p.payment_date <= :d GROUP BY p.customer_id", {"u": unit_id, "d": day})}
    out = [AgeingRow(**d, on_account=unapplied.get(d["customer_id"], Decimal(0)),
                     net=d["total"] - unapplied.get(d["customer_id"], Decimal(0))) for d in data]
    totals = {k: sum((getattr(r, k) for r in out), Decimal(0))
              for k in (*BUCKETS, "total", "on_account", "net")}
    return Ageing(unit_id=unit_id, as_of=day, rows=out, totals=totals)


class CollectionRow(BaseModel):
    invoice_id: UUID
    invoice_no: str
    customer_id: UUID
    customer_code: str
    customer_name: str
    issue_date: date
    due_date: date | None
    days_overdue: int
    outstanding: Decimal
    last_receipt_date: date | None


@router.get("/receivables/collections", response_model=list[CollectionRow])
async def collections(
    unit_id: UUID, as_of: date | None = None, customer_id: UUID | None = None,
    ctx: Ctx = Depends(read),
) -> list[CollectionRow]:
    """The overdue invoices to chase, the latest first, with the customer's last receipt."""
    require_in_scope(ctx, unit_id)
    day = as_of or date.today()
    data = await rows(
        ctx, "SELECT v.invoice_id, v.invoice_no, v.customer_id, c.code AS customer_code, c.name AS "
        "customer_name, v.issue_date, v.due_date, CAST(:d AS date) - v.due_date AS days_overdue, "
        "v.taxable_amount + v.tax_amount - v.paid_amount AS outstanding, (SELECT "
        "max(p.payment_date) "
        "FROM erp.payment p WHERE p.customer_id = v.customer_id AND p.status = 'RECEIVED') AS "
        "last_receipt_date FROM erp.customer_invoice v JOIN erp.customer c ON c.customer_id = "
        "v.customer_id WHERE v.unit_id = :u AND v.status = 'ISSUED' AND v.due_date < :d AND "
        "v.taxable_amount + v.tax_amount > v.paid_amount AND (CAST(:c AS uuid) IS NULL OR "
        "v.customer_id = :c) ORDER BY days_overdue DESC, v.invoice_no",
        {"u": unit_id, "d": day, "c": customer_id})
    return [CollectionRow(**d) for d in data]


class StatementLine(BaseModel):
    date: date
    kind: str  # INVOICE or RECEIPT
    reference: str
    debit: Decimal
    credit: Decimal
    balance: Decimal


class Statement(BaseModel):
    customer_id: UUID
    customer_code: str
    unit_id: UUID
    lines: list[StatementLine]
    balance: Decimal  # positive: the customer owes us


@router.get("/customers/{customer_id}/statement", response_model=Statement)
async def customer_statement(
    customer_id: UUID, unit_id: UUID, ctx: Ctx = Depends(read)
) -> Statement:
    """Invoices and receipts of the customer in date order, with the running balance."""
    require_in_scope(ctx, unit_id)
    customer = await one(ctx, "SELECT code FROM erp.customer WHERE customer_id = :id",
                         {"id": customer_id})
    if customer is None:
        raise not_found("Customer")
    data = await rows(
        ctx, "SELECT issue_date AS date, 'INVOICE' AS kind, invoice_no AS reference, "
        "taxable_amount "
        "+ tax_amount AS debit, 0 AS credit, created_key FROM (SELECT issue_date, invoice_no, "
        "taxable_amount, tax_amount, invoice_no AS created_key FROM erp.customer_invoice WHERE "
        "customer_id = :c AND unit_id = :u AND status = 'ISSUED') i UNION ALL SELECT "
        "payment_date, 'RECEIPT', COALESCE(bank_reference, payment_no), 0, amount, payment_no FROM "
        "erp.payment WHERE customer_id = :c AND unit_id = :u AND status = 'RECEIVED' ORDER BY "
        "date, kind, created_key", {"c": customer_id, "u": unit_id})
    balance = Decimal(0)
    lines: list[StatementLine] = []
    for d in data:
        balance += d["debit"] - d["credit"]
        lines.append(StatementLine(date=d["date"], kind=d["kind"], reference=d["reference"],
                                   debit=d["debit"], credit=d["credit"], balance=balance))
    return Statement(customer_id=customer_id, customer_code=customer["code"], unit_id=unit_id,
                     lines=lines, balance=balance)

