"""Payment runs, vendor payments and the bank file (BRD FR-FIN-03).

A payment run proposes what to pay: booked bills that fall due by a date and still have an
outstanding amount, grouped by vendor, MSME vendors first and then by due date. Bills already in an
open proposal are left out, so two runs never propose the same money twice. Each proposal is a
PROPOSED payment; paying it posts debit payable (vendor), credit bank and updates the bills, which
become PARTIALLY_PAID or PAID. The bank file lists the run's payments with each vendor's account
(payments of vendors without bank details are listed with a remark instead of being dropped). A
payment is paid or cancelled, never edited. Needs fin.pay; reading needs the finance module.
Account numbers are stored encrypted (AES-GCM, key DATA_ENCRYPTION_KEY) and shown masked;
only the bank file decrypts them.
"""

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from heyyerp_api.crypto import decrypt, encrypt
from heyyerp_api.finance.accounts import role_account
from heyyerp_api.finance.autopost import finance_enabled
from heyyerp_api.finance.journals import LineIn, post_entry
from heyyerp_api.inventory.ledger import money
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
pay = requires("fin.pay")
read = requires_module("finance")
PAYABLE_STATUSES = ("BOOKED", "PARTIALLY_PAID")
Money = Field(gt=0, le=Decimal(10_000_000_000), max_digits=18, decimal_places=2)


class AllocationIn(Strict):
    bill_id: UUID
    amount: Decimal = Money


class PaymentIn(Strict):
    unit_id: UUID
    vendor_id: UUID
    allocations: list[AllocationIn] = Field(min_length=1, max_length=200)


class RunIn(Strict):
    unit_id: UUID
    as_of: date | None = None  # bills due on or before this date; default today
    vendor_ids: list[UUID] | None = Field(default=None, max_length=500)


class PayIn(Strict):
    payment_date: date | None = None
    bank_reference: str | None = Field(default=None, max_length=60)


class BankIn(Strict):
    account_holder: str = Field(min_length=2, max_length=120)
    account_no: str = Field(pattern=r"^[0-9]{6,20}$")
    ifsc: str = Field(pattern=r"^[A-Z]{4}0[A-Z0-9]{6}$")
    bank_name: str | None = Field(default=None, max_length=100)


class BankOut(BaseModel):
    vendor_id: UUID
    account_holder: str
    account_no_masked: str
    ifsc: str
    bank_name: str | None


class ProposalLine(BaseModel):
    bill_id: UUID
    supplier_invoice_no: str
    due_date: date | None
    overdue_days: int
    outstanding: Decimal


class Proposal(BaseModel):
    vendor_id: UUID
    vendor_code: str
    vendor_name: str
    msme_flag: bool
    total: Decimal
    has_bank_details: bool
    bills: list[ProposalLine]


class AllocationOut(BaseModel):
    bill_id: UUID
    supplier_invoice_no: str
    allocated_amount: Decimal


class PaymentSummary(BaseModel):
    payment_id: UUID
    payment_no: str
    unit_id: UUID
    vendor_id: UUID | None
    vendor_code: str | None
    amount: Decimal
    status: str
    payment_date: date | None
    run_id: UUID | None
    bank_reference: str | None
    paid_at: datetime | None


class PaymentOut(PaymentSummary):
    allocations: list[AllocationOut]
    journal_id: UUID | None


PAYMENT_SQL = (
    "SELECT p.payment_id, p.payment_no, p.unit_id, p.vendor_id, v.code AS vendor_code, p.amount, "
    "p.status, p.payment_date, p.run_id, p.bank_reference, p.paid_at FROM erp.payment p LEFT JOIN "
    "erp.vendor v ON v.vendor_id = p.vendor_id"
)


async def load(ctx: Ctx, payment_id: UUID) -> PaymentOut | None:
    clause, params = scope_clause(ctx, "p.unit_id")
    head = await one(ctx, f"{PAYMENT_SQL} WHERE p.payment_id = :id"
                     f"{' AND ' + clause if clause else ''}", {**params, "id": payment_id})
    if head is None:
        return None
    allocations = await rows(
        ctx, "SELECT a.bill_id, b.supplier_invoice_no, a.allocated_amount FROM "
        "erp.payment_allocation a JOIN erp.supplier_bill b ON b.bill_id = a.bill_id WHERE "
        "a.payment_id = :id ORDER BY b.due_date, b.supplier_invoice_no", {"id": payment_id})
    journal = await one(ctx, "SELECT journal_id FROM erp.payment WHERE payment_id = :id",
                        {"id": payment_id})
    assert journal is not None
    return PaymentOut(**head, allocations=[AllocationOut(**a) for a in allocations],
                      journal_id=journal["journal_id"])


async def due_bills(
    ctx: Ctx, unit_id: UUID, as_of: date, vendor_ids: list[UUID] | None = None
) -> list[dict[str, Any]]:
    """Booked bills due by the date with money still to pay, minus money in open proposals."""
    return await rows(
        ctx,
        "SELECT b.bill_id, b.vendor_id, b.supplier_invoice_no, b.due_date, v.code AS vendor_code, "
        "v.name AS vendor_name, v.msme_flag, b.taxable_amount + b.tax_amount - b.paid_amount - "
        "COALESCE((SELECT sum(a.allocated_amount) FROM erp.payment_allocation a JOIN erp.payment "
        "p ON p.payment_id = a.payment_id WHERE a.bill_id = b.bill_id AND p.status = 'PROPOSED'), "
        "0) AS outstanding, EXISTS (SELECT 1 FROM erp.vendor_bank_account k WHERE k.vendor_id = "
        "b.vendor_id) AS has_bank FROM erp.supplier_bill b JOIN erp.vendor v ON v.vendor_id = "
        "b.vendor_id WHERE b.unit_id = :u AND b.status IN ('BOOKED', 'PARTIALLY_PAID') AND "
        "b.due_date <= :d AND (CAST(:vs AS uuid[]) IS NULL OR b.vendor_id = ANY(CAST(:vs AS "
        "uuid[]))) ORDER BY v.msme_flag DESC, b.due_date, b.supplier_invoice_no",
        {"u": unit_id, "d": as_of, "vs": vendor_ids})


def group(found: list[dict[str, Any]], as_of: date) -> list[Proposal]:
    by_vendor: dict[UUID, Proposal] = {}
    for b in found:
        if b["outstanding"] <= 0:
            continue
        p = by_vendor.setdefault(b["vendor_id"], Proposal(
            vendor_id=b["vendor_id"], vendor_code=b["vendor_code"], vendor_name=b["vendor_name"],
            msme_flag=b["msme_flag"], total=Decimal(0), has_bank_details=b["has_bank"], bills=[]))
        p.total += b["outstanding"]
        p.bills.append(ProposalLine(
            bill_id=b["bill_id"], supplier_invoice_no=b["supplier_invoice_no"],
            due_date=b["due_date"], overdue_days=max((as_of - b["due_date"]).days, 0),
            outstanding=b["outstanding"]))
    return sorted(by_vendor.values(), key=lambda p: (not p.msme_flag, p.bills[0].due_date or as_of))


@router.get("/payment-proposals", response_model=list[Proposal])
async def proposals(
    unit_id: UUID, as_of: date | None = None, ctx: Ctx = Depends(read)
) -> list[Proposal]:
    """What a payment run would pay today: MSME vendors first, then by due date."""
    require_in_scope(ctx, unit_id)
    day = as_of or date.today()
    return group(await due_bills(ctx, unit_id, day), day)


async def create_payment(
    ctx: Ctx, unit_id: UUID, vendor_id: UUID, allocations: list[AllocationIn],
    run_id: UUID | None = None,
) -> UUID:
    ids = [a.bill_id for a in allocations]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Each bill may appear only once")
    bills = {r["bill_id"]: r for r in await rows(
        ctx, "SELECT b.bill_id, b.vendor_id, b.unit_id, b.status, b.supplier_invoice_no, "
        "b.taxable_amount + b.tax_amount - b.paid_amount - COALESCE((SELECT "
        "sum(a.allocated_amount) FROM erp.payment_allocation a JOIN erp.payment p ON p.payment_id "
        "= a.payment_id WHERE a.bill_id = b.bill_id AND p.status = 'PROPOSED'), 0) AS outstanding "
        "FROM erp.supplier_bill b WHERE b.bill_id = ANY(CAST(:ids AS uuid[])) ORDER BY b.bill_id "
        "FOR UPDATE OF b", {"ids": ids})}
    for a in allocations:
        bill = bills.get(a.bill_id)
        if bill is None or bill["vendor_id"] != vendor_id or bill["unit_id"] != unit_id:
            raise HTTPException(422, "Every bill must belong to this vendor and plant")
        if bill["status"] not in PAYABLE_STATUSES:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Bill {bill['supplier_invoice_no']} is {str(bill['status']).lower()} and "
                "cannot be paid")
        if a.amount > bill["outstanding"]:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Bill {bill['supplier_invoice_no']}: {a.amount} asked but only "
                f"{bill['outstanding']} is left to pay")
    total = sum((a.amount for a in allocations), Decimal(0))
    created = await one(
        ctx, "INSERT INTO erp.payment(tenant_id, unit_id, payment_no, vendor_id, amount, "
        "created_by, run_id) VALUES (erp.current_tenant_id(), :u, :n, :v, :a, :me, :r) "
        "RETURNING payment_id",
        {"u": unit_id, "n": await next_number(ctx, "payment"), "v": vendor_id, "a": total,
         "me": ctx.principal.profile.app_user_id, "r": run_id})
    assert created is not None
    for a in allocations:
        await rows(
            ctx, "INSERT INTO erp.payment_allocation(tenant_id, payment_id, bill_id, "
            "allocated_amount) VALUES (erp.current_tenant_id(), :p, :b, :a) "
            "RETURNING allocation_id", {"p": created["payment_id"], "b": a.bill_id, "a": a.amount})
    return created["payment_id"]  # type: ignore[no-any-return]


@router.post("/payment-runs", response_model=list[PaymentOut], status_code=status.HTTP_201_CREATED)
async def create_run(body: RunIn, ctx: Ctx = Depends(pay)) -> list[PaymentOut]:
    """Turn the proposals into PROPOSED payments, one per vendor, all in one run."""
    require_in_scope(ctx, body.unit_id)
    day = body.as_of or date.today()
    found = await due_bills(ctx, body.unit_id, day, body.vendor_ids)
    plan = group(found, day)
    if not plan:
        raise HTTPException(status.HTTP_409_CONFLICT, "No bills are due and unpaid")
    run_id = uuid4()
    made: list[PaymentOut] = []
    for p in plan:
        payment_id = await create_payment(
            ctx, body.unit_id, p.vendor_id,
            [AllocationIn(bill_id=b.bill_id, amount=b.outstanding) for b in p.bills], run_id)
        result = await load(ctx, payment_id)
        assert result is not None
        made.append(result)
    await audit(ctx, "payment_run.create", "payment_run", run_id,
                {"unit_id": body.unit_id, "as_of": day, "payments": len(made),
                 "total": sum((m.amount for m in made), Decimal(0))})
    return made


@router.post("/payments", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
async def create_single_payment(body: PaymentIn, ctx: Ctx = Depends(pay)) -> PaymentOut:
    """A payment to one vendor outside a run, for named bills and amounts."""
    require_in_scope(ctx, body.unit_id)
    payment_id = await create_payment(ctx, body.unit_id, body.vendor_id, body.allocations)
    await audit(ctx, "payment.create", "payment", payment_id,
                {"vendor_id": body.vendor_id, "bills": len(body.allocations)})
    result = await load(ctx, payment_id)
    assert result is not None
    return result


async def require_payment(ctx: Ctx, payment_id: UUID) -> dict[str, Any]:
    clause, params = scope_clause(ctx, "unit_id")
    head = await one(
        ctx, "SELECT payment_id, payment_no, unit_id, vendor_id, amount, status FROM erp.payment "
        f"WHERE payment_id = :id{' AND ' + clause if clause else ''} FOR UPDATE",
        {**params, "id": payment_id})
    if head is None:
        raise not_found("Payment")
    if head["status"] != "PROPOSED":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The payment is {str(head['status']).lower()}")
    return head


@router.post("/payments/{payment_id}/pay", response_model=PaymentOut)
async def pay_payment(payment_id: UUID, body: PayIn, ctx: Ctx = Depends(pay)) -> PaymentOut:
    """The money left the bank: post debit payable, credit bank, and settle the bills."""
    head = await require_payment(ctx, payment_id)
    unit_id = head["unit_id"]
    if not await finance_enabled(ctx, unit_id):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Set up the plant's chart of accounts before paying")
    day = body.payment_date or date.today()
    if day > date.today():
        raise HTTPException(422, "The payment date is in the future")
    allocations = await rows(
        ctx, "SELECT a.bill_id, a.allocated_amount, b.status, b.taxable_amount + b.tax_amount AS "
        "gross, b.paid_amount FROM erp.payment_allocation a JOIN erp.supplier_bill b ON b.bill_id "
        "= a.bill_id WHERE a.payment_id = :id ORDER BY a.bill_id FOR UPDATE OF b",
        {"id": payment_id})
    for a in allocations:
        if a["status"] not in PAYABLE_STATUSES or a["paid_amount"] + a["allocated_amount"] > a[
                "gross"]:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "A bill of this payment changed; cancel it and propose again")
    journal_id = await post_entry(
        ctx, unit_id=unit_id, journal_date=day, source_type="PAYMENT", source_id=payment_id,
        narration=f"Payment {head['payment_no']}",
        lines=[LineIn(account_id=await role_account(ctx, unit_id, "PAYABLE"),
                      debit=money(head["amount"]), party_type="VENDOR", party_id=head["vendor_id"],
                      description=f"Payment {head['payment_no']}"),
               LineIn(account_id=await role_account(ctx, unit_id, "BANK"),
                      credit=money(head["amount"]))])
    for a in allocations:
        settled = a["paid_amount"] + a["allocated_amount"]
        await rows(ctx, "UPDATE erp.supplier_bill SET paid_amount = :p, status = :s WHERE bill_id "
                   "= :id RETURNING bill_id",
                   {"p": settled, "s": "PAID" if settled == a["gross"] else "PARTIALLY_PAID",
                    "id": a["bill_id"]})
    await rows(
        ctx, "UPDATE erp.payment SET status = 'PAID', payment_date = :d, paid_by = :me, paid_at = "
        "now(), journal_id = :j, bank_reference = :ref WHERE payment_id = :id RETURNING payment_id",
        {"d": day, "me": ctx.principal.profile.app_user_id, "j": journal_id,
         "ref": body.bank_reference, "id": payment_id})
    await audit(ctx, "payment.pay", "payment", payment_id,
                {"payment_no": head["payment_no"], "amount": head["amount"]})
    result = await load(ctx, payment_id)
    assert result is not None
    return result


@router.post("/payments/{payment_id}/cancel", response_model=PaymentOut)
async def cancel_payment(payment_id: UUID, ctx: Ctx = Depends(pay)) -> PaymentOut:
    """Drop a proposal; its bills become available for the next run."""
    head = await require_payment(ctx, payment_id)
    await rows(ctx, "UPDATE erp.payment SET status = 'CANCELLED' WHERE payment_id = :id "
               "RETURNING payment_id", {"id": payment_id})
    await audit(ctx, "payment.cancel", "payment", payment_id, {"payment_no": head["payment_no"]})
    result = await load(ctx, payment_id)
    assert result is not None
    return result


@router.get("/payments", response_model=Page[PaymentSummary])
async def list_payments(
    unit_id: UUID | None = None, vendor_id: UUID | None = None, run_id: UUID | None = None,
    payment_status: str | None = Query(None, alias="status"), page: Paging = Depends(),
    ctx: Ctx = Depends(read),
) -> Page[PaymentSummary]:
    conditions: list[str] = ["p.vendor_id IS NOT NULL"]  # customer receipts have their own list
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "p.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for text_, key, value in (
            ("p.unit_id = :u", "u", unit_id), ("p.vendor_id = :v", "v", vendor_id),
            ("p.run_id = :r", "r", run_id),
            ("p.status = :st", "st", payment_status.upper() if payment_status else None)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{PAYMENT_SQL}{where} ORDER BY p.created_at DESC, p.payment_no DESC "
                       "LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.payment p{where}", params)
    return Page(items=[PaymentSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/payments/{payment_id}", response_model=PaymentOut)
async def get_payment(payment_id: UUID, ctx: Ctx = Depends(read)) -> PaymentOut:
    found = await load(ctx, payment_id)
    if found is None:
        raise not_found("Payment")
    return found


# ------------------------------------------------------------------ bank file and bank details
@router.get("/payment-runs/{run_id}/bank-file", response_class=PlainTextResponse)
async def bank_file(run_id: UUID, ctx: Ctx = Depends(pay)) -> PlainTextResponse:
    """A CSV for the bank portal: one row per payment of the run (cancelled ones left out)."""
    clause, params = scope_clause(ctx, "p.unit_id")
    found = await rows(
        ctx, "SELECT p.payment_no, v.code AS vendor_code, v.name AS vendor_name, p.amount, "
        "p.vendor_id, k.account_holder, k.account_no_enc, k.ifsc, k.bank_name FROM erp.payment p "
        "JOIN erp.vendor "
        "v ON v.vendor_id = p.vendor_id LEFT JOIN erp.vendor_bank_account k ON k.vendor_id = "
        f"p.vendor_id WHERE p.run_id = :r AND p.status <> 'CANCELLED'"
        f"{' AND ' + clause if clause else ''} ORDER BY p.payment_no", {**params, "r": run_id})
    if not found:
        raise not_found("Payment run")
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["payment_no", "vendor_code", "vendor_name", "beneficiary", "account_no",
                     "ifsc", "bank", "amount", "remark"])
    for r in found:
        missing = r["account_no_enc"] is None
        account_no = "" if missing else decrypt(r["account_no_enc"], str(r["vendor_id"]))
        writer.writerow([r["payment_no"], r["vendor_code"], r["vendor_name"],
                         r["account_holder"] or "", account_no, r["ifsc"] or "",
                         r["bank_name"] or "", f"{r['amount']:.2f}",
                         "MISSING_BANK_DETAILS" if missing else ""])
    await audit(ctx, "payment_run.bank_file", "payment_run", run_id, {"rows": len(found)})
    return PlainTextResponse(out.getvalue(), media_type="text/csv")


def mask(account_no: str) -> str:
    return "*" * max(len(account_no) - 4, 0) + account_no[-4:]


@router.put("/vendors/{vendor_id}/bank-account", response_model=BankOut)
async def set_bank_account(vendor_id: UUID, body: BankIn, ctx: Ctx = Depends(pay)) -> BankOut:
    if await one(ctx, "SELECT 1 AS x FROM erp.vendor WHERE vendor_id = :id", {"id": vendor_id}
                 ) is None:
        raise not_found("Vendor")
    await rows(
        ctx, "INSERT INTO erp.vendor_bank_account(tenant_id, vendor_id, account_holder, "
        "account_no_enc, account_last4, ifsc, bank_name, updated_by) VALUES "
        "(erp.current_tenant_id(), :v, :h, :n, :l, :i, :b, :me) ON CONFLICT (vendor_id) DO "
        "UPDATE SET account_holder = EXCLUDED.account_holder, account_no_enc = "
        "EXCLUDED.account_no_enc, account_last4 = EXCLUDED.account_last4, ifsc = "
        "EXCLUDED.ifsc, bank_name = EXCLUDED.bank_name, updated_by = EXCLUDED.updated_by, "
        "updated_at = now() RETURNING vendor_id",
        {"v": vendor_id, "h": body.account_holder,
         "n": encrypt(body.account_no, str(vendor_id)), "l": body.account_no[-4:],
         "i": body.ifsc, "b": body.bank_name, "me": ctx.principal.profile.app_user_id})
    await audit(ctx, "vendor.bank_account", "vendor", vendor_id, {"ifsc": body.ifsc})
    return BankOut(vendor_id=vendor_id, account_holder=body.account_holder,
                   account_no_masked=mask(body.account_no), ifsc=body.ifsc,
                   bank_name=body.bank_name)


@router.get("/vendors/{vendor_id}/bank-account", response_model=BankOut)
async def get_bank_account(vendor_id: UUID, ctx: Ctx = Depends(pay)) -> BankOut:
    found = await one(ctx, "SELECT vendor_id, account_holder, account_last4, ifsc, bank_name FROM "
                      "erp.vendor_bank_account WHERE vendor_id = :id", {"id": vendor_id})
    if found is None:
        raise not_found("Bank account")
    return BankOut(vendor_id=found["vendor_id"], account_holder=found["account_holder"],
                   account_no_masked="*" * 8 + found["account_last4"], ifsc=found["ifsc"],
                   bank_name=found["bank_name"])
