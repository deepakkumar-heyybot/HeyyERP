"""Tax deducted at source on supplier bills (BRD FR-FIN-07).

Deducting TDS on a booked bill records the section, the rate and the amount, books it (debit the
vendor's payable, credit TDS payable) and counts it as settled on the bill, so a payment run pays
the vendor only what is left. The entry stays OPEN until it is paid to the government; a deposit
records the challan and books debit TDS payable, credit bank. An OPEN entry can be cancelled, which
reverses the books and gives the amount back to the bill. The month's summary by section is the
data for the return. The section rates below are the common ones and can be overridden per
entry. Needs fin.bill; reading needs the finance module.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from heyyerp_api.finance.accounts import role_account
from heyyerp_api.finance.autopost import finance_enabled
from heyyerp_api.finance.gst import bounds, store
from heyyerp_api.finance.journals import LineIn, ReverseIn, post_entry, reverse_journal
from heyyerp_api.finance.period import get_period
from heyyerp_api.inventory.ledger import money
from heyyerp_api.masters.common import (
    Ctx,
    Strict,
    audit,
    not_found,
    one,
    require_in_scope,
    requires,
    requires_module,
    rows,
    scope_clause,
)

router = APIRouter(prefix="/finance/tds", tags=["finance"])
manage = requires("fin.bill")
read = requires_module("finance")
ZERO = Decimal(0)
SECTION_RATES = {"194C": Decimal("2"), "194H": Decimal("5"), "194I": Decimal("10"),
                 "194J": Decimal("10"), "194Q": Decimal("0.1")}


class DeductIn(Strict):
    bill_id: UUID
    section_code: str = Field(min_length=3, max_length=10)
    rate: Decimal | None = Field(None, ge=0, le=100, max_digits=7, decimal_places=4)
    taxable_base: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=2)


class DepositIn(Strict):
    unit_id: UUID
    entry_ids: list[UUID] = Field(min_length=1, max_length=500)
    challan_no: str = Field(min_length=3, max_length=40)
    deposited_on: date | None = None


class CancelIn(Strict):
    reason: str = Field(min_length=3, max_length=200)


class TdsOut(BaseModel):
    tds_entry_id: UUID
    unit_id: UUID
    bill_id: UUID | None
    vendor_id: UUID
    vendor_code: str
    supplier_invoice_no: str | None
    section_code: str
    rate: Decimal
    taxable_base: Decimal
    deducted_amount: Decimal
    status: str
    challan_no: str | None
    deposited_on: date | None


SELECT = ("SELECT t.tds_entry_id, t.unit_id, t.bill_id, t.vendor_id, v.code AS vendor_code, "
          "b.supplier_invoice_no, t.section_code, t.rate, t.taxable_base, t.deducted_amount, "
          "t.status, t.challan_no, t.deposited_on FROM erp.tds_entry t JOIN erp.vendor v ON "
          "v.vendor_id = t.vendor_id LEFT JOIN erp.supplier_bill b ON b.bill_id = t.bill_id")


async def load(ctx: Ctx, entry_id: UUID) -> TdsOut:
    found = await one(ctx, f"{SELECT} WHERE t.tds_entry_id = :id", {"id": entry_id})
    if found is None:
        raise not_found("TDS entry")
    return TdsOut(**found)


async def settle(ctx: Ctx, bill_id: UUID, delta: Decimal) -> None:
    """Move the bill's settled amount by `delta` and set its status from what is left."""
    await rows(
        ctx, "UPDATE erp.supplier_bill SET paid_amount = paid_amount + :d, status = CASE WHEN "
        "paid_amount + :d <= 0 THEN 'BOOKED' WHEN paid_amount + :d >= taxable_amount + "
        "tax_amount THEN 'PAID' ELSE 'PARTIALLY_PAID' END WHERE bill_id = :id RETURNING bill_id",
        {"d": delta, "id": bill_id})


@router.post("", response_model=TdsOut, status_code=status.HTTP_201_CREATED)
async def deduct(body: DeductIn, ctx: Ctx = Depends(manage)) -> TdsOut:
    bill = await one(
        ctx, "SELECT bill_id, unit_id, vendor_id, supplier_invoice_no, status, invoice_date, "
        "taxable_amount, tax_amount, paid_amount FROM erp.supplier_bill WHERE bill_id = :id "
        "FOR UPDATE", {"id": body.bill_id})
    if bill is None:
        raise not_found("Bill")
    require_in_scope(ctx, bill["unit_id"])
    if bill["status"] not in ("BOOKED", "PARTIALLY_PAID"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "TDS is deducted on a booked bill that is not fully settled")
    if not await finance_enabled(ctx, bill["unit_id"]):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Set up the plant's chart of accounts before deducting TDS")
    if await one(ctx, "SELECT 1 AS x FROM erp.tds_entry WHERE bill_id = :id AND status <> "
                 "'CANCELLED'", {"id": body.bill_id}) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "TDS is already deducted on this bill")
    section = body.section_code.strip().upper()
    rate = body.rate if body.rate is not None else SECTION_RATES.get(section)
    if rate is None:
        raise HTTPException(422, f"No standard rate for section {section}; give the rate")
    base = body.taxable_base if body.taxable_base is not None else bill["taxable_amount"]
    if base > bill["taxable_amount"]:
        raise HTTPException(422, "The base is more than the bill's taxable value")
    amount = money(base * rate / 100)
    if amount <= 0:
        raise HTTPException(422, "Nothing to deduct at this rate and base")
    owed = bill["taxable_amount"] + bill["tax_amount"] - bill["paid_amount"]
    if amount > owed:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Only {owed} of this bill is left to settle, {amount} to deduct")
    period = await get_period(ctx, bill["unit_id"], date.today())
    created = await one(
        ctx, "INSERT INTO erp.tds_entry(tenant_id, unit_id, period_id, vendor_id, section_code, "
        "taxable_base, deducted_amount, rate, bill_id) VALUES (erp.current_tenant_id(), :u, :p, "
        ":v, :s, :b, :a, :r, :bill) RETURNING tds_entry_id",
        {"u": bill["unit_id"], "p": period["period_id"], "v": bill["vendor_id"], "s": section,
         "b": base, "a": amount, "r": rate, "bill": body.bill_id})
    assert created is not None
    entry_id: UUID = created["tds_entry_id"]
    journal_id = await post_entry(
        ctx, unit_id=bill["unit_id"], journal_date=date.today(), source_type="TDS_DEDUCTION",
        source_id=entry_id, narration=f"TDS {section} on {bill['supplier_invoice_no']}",
        lines=[LineIn(account_id=await role_account(ctx, bill["unit_id"], "PAYABLE"),
                      debit=amount, party_type="VENDOR", party_id=bill["vendor_id"],
                      description=f"TDS {section} on {bill['supplier_invoice_no']}"),
               LineIn(account_id=await role_account(ctx, bill["unit_id"], "TDS_PAYABLE"),
                      credit=amount)])
    await rows(ctx, "UPDATE erp.tds_entry SET journal_id = :j WHERE tds_entry_id = :id "
               "RETURNING tds_entry_id", {"j": journal_id, "id": entry_id})
    await settle(ctx, body.bill_id, amount)
    await audit(ctx, "tds.deduct", "tds_entry", entry_id,
                {"bill_id": body.bill_id, "section": section, "amount": amount})
    return await load(ctx, entry_id)


@router.post("/{entry_id}/cancel", response_model=TdsOut)
async def cancel(entry_id: UUID, body: CancelIn, ctx: Ctx = Depends(manage)) -> TdsOut:
    entry = await one(
        ctx, "SELECT tds_entry_id, unit_id, bill_id, status, journal_id, deducted_amount FROM "
        "erp.tds_entry WHERE tds_entry_id = :id FOR UPDATE", {"id": entry_id})
    if entry is None:
        raise not_found("TDS entry")
    require_in_scope(ctx, entry["unit_id"])
    if entry["status"] != "OPEN":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The entry is {str(entry['status']).lower()}; only open ones cancel")
    if entry["journal_id"] is not None:
        await reverse_journal(entry["journal_id"], ReverseIn(
            reason=f"TDS entry cancelled: {body.reason.strip()}"), ctx)
    if entry["bill_id"] is not None:
        await settle(ctx, entry["bill_id"], -entry["deducted_amount"])
    await rows(ctx, "UPDATE erp.tds_entry SET status = 'CANCELLED' WHERE tds_entry_id = :id "
               "RETURNING tds_entry_id", {"id": entry_id})
    await audit(ctx, "tds.cancel", "tds_entry", entry_id, {"reason": body.reason})
    return await load(ctx, entry_id)


@router.post("/deposit", response_model=list[TdsOut])
async def deposit(body: DepositIn, ctx: Ctx = Depends(manage)) -> list[TdsOut]:
    """Pay open entries to the government: records the challan and books TDS payable to bank."""
    require_in_scope(ctx, body.unit_id)
    day = body.deposited_on or date.today()
    if day > date.today():
        raise HTTPException(422, "The deposit date is in the future")
    ids = list(dict.fromkeys(body.entry_ids))
    found = await rows(
        ctx, "SELECT tds_entry_id, status, deducted_amount FROM erp.tds_entry WHERE unit_id = :u "
        "AND tds_entry_id = ANY(CAST(:ids AS uuid[])) ORDER BY tds_entry_id FOR UPDATE",
        {"u": body.unit_id, "ids": ids})
    if len(found) != len(ids):
        raise HTTPException(422, "Every entry must belong to this plant")
    if any(f["status"] != "OPEN" for f in found):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only open entries can be deposited")
    total = sum((f["deducted_amount"] for f in found), ZERO)
    journal_id = await post_entry(
        ctx, unit_id=body.unit_id, journal_date=day, source_type="TDS_DEPOSIT",
        source_id=uuid4(), narration=f"TDS deposited, challan {body.challan_no}",
        lines=[LineIn(account_id=await role_account(ctx, body.unit_id, "TDS_PAYABLE"), debit=total),
               LineIn(account_id=await role_account(ctx, body.unit_id, "BANK"), credit=total)])
    await rows(ctx, "UPDATE erp.tds_entry SET status = 'DEPOSITED', challan_no = :c, "
               "deposited_on = :d, deposit_journal_id = :j WHERE tds_entry_id = "
               "ANY(CAST(:ids AS uuid[])) RETURNING tds_entry_id",
               {"c": body.challan_no, "d": day, "j": journal_id, "ids": ids})
    await audit(ctx, "tds.deposit", "tds_entry", ids[0],
                {"entries": len(ids), "amount": total, "challan_no": body.challan_no})
    return [await load(ctx, i) for i in ids]


@router.get("", response_model=list[TdsOut])
async def list_entries(
    unit_id: UUID | None = None, entry_status: str | None = Query(None, alias="status"),
    vendor_id: UUID | None = None, ctx: Ctx = Depends(read),
) -> list[TdsOut]:
    conditions = ["TRUE"]
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "t.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    wanted = entry_status.upper() if entry_status else None
    for text_, key, value in (("t.unit_id = :u", "u", unit_id),
                              ("t.vendor_id = :v", "v", vendor_id), ("t.status = :s", "s", wanted)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    data = await rows(ctx, f"{SELECT} WHERE {' AND '.join(conditions)} ORDER BY t.created_at DESC",
                      params)
    return [TdsOut(**d) for d in data]


class SectionRow(BaseModel):
    section_code: str
    entries: int
    taxable_base: Decimal
    deducted_amount: Decimal
    deposited_amount: Decimal


@router.get("/summary", response_model=list[SectionRow])
async def summary(unit_id: UUID, month: date, ctx: Ctx = Depends(read)) -> list[SectionRow]:
    """The month's deductions by section (what the return needs), and how much is deposited."""
    require_in_scope(ctx, unit_id)
    first, after = bounds(month)
    data = await rows(
        ctx, "SELECT section_code, count(*) AS entries, sum(taxable_base) AS taxable_base, "
        "sum(deducted_amount) AS deducted_amount, COALESCE(sum(deducted_amount) FILTER (WHERE "
        "status = 'DEPOSITED'), 0.00) AS deposited_amount FROM erp.tds_entry WHERE unit_id = :u "
        "AND "
        "status <> 'CANCELLED' AND created_at >= :a AND created_at < :b GROUP BY section_code "
        "ORDER BY section_code", {"u": unit_id, "a": first, "b": after})
    return [SectionRow(**d) for d in data]


@router.post("/summary/prepare", response_model=list[SectionRow])
async def prepare_return(
    unit_id: UUID, month: date, ctx: Ctx = Depends(manage)
) -> list[SectionRow]:
    """Keep the month's summary as the TDS return data (rebuilt until it is marked filed)."""
    result = await summary(unit_id, month, ctx)
    await store(ctx, unit_id, bounds(month)[0], "TDS",
                {"sections": [r.model_dump(mode="json") for r in result]})
    return result
