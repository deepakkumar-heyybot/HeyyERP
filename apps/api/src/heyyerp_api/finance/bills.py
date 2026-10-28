"""Supplier bills with 3-way match, exceptions through approval, and booking (BRD FR-FIN-02).

A bill line that names the purchase-order line and the goods-receipt line it bills is matched:
the quantity may not exceed what was accepted and not yet billed, and the rate may not differ from
the PO price by more than the rule `priceTolPct` (default 0%). Any line that fails, a line that
matches nothing, or a bill that looks like a duplicate (same vendor and amount within a week under
another number) makes the whole bill an EXCEPTION that Accounts must approve (`bill` approval
type). A clean bill is MATCHED. A MATCHED or APPROVED bill is booked: debit goods-received-not-
billed (at PO price), price/quantity differences and GST input, credit the vendor's payable. The
due date is the vendor's payment term, shortened to `msmePayDays` (45) for MSME vendors. The same
vendor invoice number is never accepted twice. Needs fin.bill; reading needs the finance module.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Self
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from heyyerp_api.business_rules import service as rules
from heyyerp_api.finance.accounts import role_account
from heyyerp_api.finance.autopost import finance_enabled
from heyyerp_api.finance.journals import LineIn, ReverseIn, post_entry, reverse_journal
from heyyerp_api.inventory.ledger import money
from heyyerp_api.masters.common import (
    Ctx,
    Page,
    Paging,
    Strict,
    audit,
    count,
    db_errors,
    not_found,
    one,
    require_in_scope,
    requires,
    requires_module,
    rows,
    scope_clause,
)
from heyyerp_api.workflow import engine
from heyyerp_api.workflow.registry import DocInfo, DocumentType, register

router = APIRouter(prefix="/finance", tags=["finance"])
manage = requires("fin.bill")
read = requires_module("finance")
Qty = Field(gt=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=3)
Rate = Field(ge=0, le=Decimal(1_000_000_000), max_digits=18, decimal_places=2)


class BillLineIn(Strict):
    description: str | None = Field(default=None, min_length=2, max_length=200)
    po_line_id: UUID | None = None
    grn_line_id: UUID | None = None
    item_id: UUID | None = None
    quantity: Decimal = Qty
    unit_rate: Decimal = Rate
    tax_code_id: UUID | None = None
    expense_account_id: UUID | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.grn_line_id is not None and self.po_line_id is None:
            raise ValueError("A receipt line needs the PO line it belongs to")
        matched = self.po_line_id is not None and self.grn_line_id is not None
        if not matched and self.expense_account_id is None:
            raise ValueError("A line that matches no receipt needs an expense account")
        if not matched and self.description is None:
            raise ValueError("A line that matches no receipt needs a description")
        return self


class BillIn(Strict):
    unit_id: UUID
    vendor_id: UUID
    supplier_invoice_no: str = Field(min_length=1, max_length=60)
    invoice_date: date
    lines: list[BillLineIn] = Field(min_length=1, max_length=100)


class BillLineOut(BaseModel):
    bill_line_id: UUID
    line_no: int
    description: str
    po_line_id: UUID | None
    grn_line_id: UUID | None
    quantity: Decimal
    unit_rate: Decimal
    taxable_amount: Decimal
    tax_amount: Decimal
    match_status: str
    qty_variance: Decimal | None
    rate_variance: Decimal | None


class BillSummary(BaseModel):
    bill_id: UUID
    unit_id: UUID
    vendor_id: UUID
    vendor_code: str
    msme_flag: bool
    supplier_invoice_no: str
    invoice_date: date
    due_date: date | None
    taxable_amount: Decimal
    tax_amount: Decimal
    gross_amount: Decimal
    paid_amount: Decimal
    outstanding: Decimal
    status: str
    exception_note: str | None
    journal_id: UUID | None


class BillOut(BillSummary):
    lines: list[BillLineOut]
    approval_id: UUID | None


BILL_SQL = (
    "SELECT b.bill_id, b.unit_id, b.vendor_id, v.code AS vendor_code, v.msme_flag, "
    "b.supplier_invoice_no, b.invoice_date, b.due_date, b.taxable_amount, b.tax_amount, "
    "b.taxable_amount + b.tax_amount AS gross_amount, b.paid_amount, b.taxable_amount + "
    "b.tax_amount - b.paid_amount AS outstanding, b.status, b.exception_note, b.journal_id FROM "
    "erp.supplier_bill b JOIN erp.vendor v ON v.vendor_id = b.vendor_id"
)


async def load(ctx: Ctx, bill_id: UUID) -> BillOut | None:
    clause, params = scope_clause(ctx, "b.unit_id")
    head = await one(ctx, f"{BILL_SQL} WHERE b.bill_id = :id{' AND ' + clause if clause else ''}",
                     {**params, "id": bill_id})
    if head is None:
        return None
    lines = await rows(
        ctx, "SELECT l.bill_line_id, l.line_no, l.description, l.po_line_id, l.grn_line_id, "
        "l.quantity, l.unit_rate, l.taxable_amount, l.tax_amount, l.match_status, m.qty_variance, "
        "m.rate_variance FROM erp.supplier_bill_line l LEFT JOIN erp.bill_match m ON "
        "m.bill_line_id = l.bill_line_id WHERE l.bill_id = :id ORDER BY l.line_no",
        {"id": bill_id})
    approval = await one(
        ctx, "SELECT approval_id FROM erp.approval_instance WHERE document_type = 'bill' AND "
        "document_id = :id", {"id": bill_id})
    return BillOut(**head, lines=[BillLineOut(**ln) for ln in lines],
                   approval_id=approval["approval_id"] if approval else None)


async def due_date_for(ctx: Ctx, vendor: dict[str, Any], invoice_date: date) -> date:
    term = await one(ctx, "SELECT due_days FROM erp.payment_term WHERE payment_term_id = :id",
                     {"id": vendor["payment_term_id"]}) if vendor["payment_term_id"] else None
    days = term["due_days"] if term else 0
    if vendor["msme_flag"]:
        days = min(days or 10_000, int(await rules.get_value(ctx, "msmePayDays")))
    return invoice_date + timedelta(days=days)


async def match_line(
    ctx: Ctx, unit_id: UUID, vendor_id: UUID, line: BillLineIn, tolerance: int
) -> dict[str, Any]:
    """Match one bill line to its receipt: returns the status, variances and the PO price."""
    found = await one(
        ctx, "SELECT gl.grn_line_id, gl.po_line_id, gl.accepted_qty, g.unit_id, po.vendor_id, "
        "pl.unit_price, pl.item_id FROM erp.grn_line gl JOIN erp.grn g ON g.grn_id = gl.grn_id "
        "JOIN erp.po_line pl ON pl.po_line_id = gl.po_line_id JOIN erp.purchase_order po ON "
        "po.po_id = pl.po_id WHERE gl.grn_line_id = :g FOR UPDATE OF gl",
        {"g": line.grn_line_id})
    if (found is None or found["po_line_id"] != line.po_line_id or found["unit_id"] != unit_id
            or found["vendor_id"] != vendor_id):
        raise HTTPException(
            422, "The receipt line must belong to this vendor, plant and PO line")
    billed = await one(
        ctx, "SELECT COALESCE(sum(m.billed_qty), 0) AS q FROM erp.bill_match m JOIN "
        "erp.supplier_bill_line l ON l.bill_line_id = m.bill_line_id JOIN erp.supplier_bill b ON "
        "b.bill_id = l.bill_id WHERE m.grn_line_id = :g AND b.status NOT IN ('CANCELLED', "
        "'REJECTED')", {"g": line.grn_line_id})
    assert billed is not None
    billable = found["accepted_qty"] - billed["q"]
    qty_variance = line.quantity - billable  # above zero: more billed than was received
    rate_variance = line.unit_rate - found["unit_price"]
    over_qty = qty_variance > 0
    over_rate = found["unit_price"] == 0 and rate_variance != 0 or (
        found["unit_price"] > 0 and abs(rate_variance) * 100 > tolerance * found["unit_price"])
    kind = "QTY_RATE" if over_qty and over_rate else "QTY" if over_qty else "RATE" if over_rate \
        else "NONE"
    return {"status": kind, "qty_variance": max(qty_variance, Decimal(0)),
            "rate_variance": rate_variance, "po_price": found["unit_price"],
            "item_id": found["item_id"], "billable": billable}


@router.post("/bills", response_model=BillOut, status_code=status.HTTP_201_CREATED)
async def enter_bill(body: BillIn, ctx: Ctx = Depends(manage)) -> BillOut:
    require_in_scope(ctx, body.unit_id)
    if body.invoice_date > date.today():
        raise HTTPException(422, "The invoice date is in the future")
    unit = await one(ctx, "SELECT active FROM erp.unit WHERE unit_id = :id", {"id": body.unit_id})
    if unit is None or not unit["active"]:
        raise HTTPException(422, "The plant does not exist or is inactive")
    vendor = await one(ctx, "SELECT vendor_id, status, msme_flag, payment_term_id FROM erp.vendor "
                       "WHERE vendor_id = :id", {"id": body.vendor_id})
    if vendor is None or vendor["status"] != "APPROVED":
        raise HTTPException(422, "The vendor does not exist or is not approved")
    if await one(ctx, "SELECT 1 AS x FROM erp.supplier_bill WHERE vendor_id = :v AND "
                 "supplier_invoice_no = :n", {"v": body.vendor_id, "n": body.supplier_invoice_no}):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Invoice {body.supplier_invoice_no} of this vendor is already entered")
    tolerance = int(await rules.get_value(ctx, "priceTolPct"))
    planned: list[dict[str, Any]] = []
    notes: list[str] = []
    taxable_total = tax_total = Decimal(0)
    await rows(  # lock the receipt lines in a fixed order so two bills cannot deadlock
        ctx, "SELECT grn_line_id FROM erp.grn_line WHERE grn_line_id = ANY(CAST(:ids AS uuid[])) "
        "ORDER BY grn_line_id FOR UPDATE",
        {"ids": [ln.grn_line_id for ln in body.lines if ln.grn_line_id is not None]})
    for number, line in enumerate(body.lines, start=1):
        rate = None
        if line.tax_code_id is not None:
            tax = await one(ctx, "SELECT gst_rate FROM erp.tax_code WHERE tax_code_id = :id",
                            {"id": line.tax_code_id})
            if tax is None:
                raise HTTPException(422, "A tax code does not exist")
            rate = tax["gst_rate"]
        net = money(line.quantity * line.unit_rate)
        gst = money(net * (rate or Decimal(0)) / Decimal(100))
        entry: dict[str, Any] = {"line": line, "net": net, "gst": gst, "status": "UNMATCHED",
                                 "qty_variance": None, "rate_variance": None, "po_price": None}
        description = line.description or "Goods"
        if line.po_line_id is not None and line.grn_line_id is not None:
            outcome = await match_line(ctx, body.unit_id, body.vendor_id, line, tolerance)
            entry.update(outcome)
            if outcome["status"] != "NONE":
                notes.append(f"line {number}: {outcome['status'].replace('_', ' + ').lower()} "
                             f"(qty over by {outcome['qty_variance']}, rate off by "
                             f"{outcome['rate_variance']})")
        else:
            if line.expense_account_id is not None and await one(
                    ctx, "SELECT 1 AS x FROM erp.account WHERE account_id = :id AND unit_id = :u "
                    "AND active", {"id": line.expense_account_id, "u": body.unit_id}) is None:
                raise HTTPException(
                    422, "The expense account must be an active account of the plant")
            notes.append(f"line {number}: matches no goods receipt")
        entry["description"] = description
        planned.append(entry)
        taxable_total += net
        tax_total += gst
    gross = taxable_total + tax_total
    twin = await one(
        ctx, "SELECT supplier_invoice_no FROM erp.supplier_bill WHERE vendor_id = :v AND status "
        "NOT IN ('CANCELLED', 'REJECTED') AND taxable_amount + tax_amount = :g AND "
        "abs(invoice_date - CAST(:d AS date)) <= 7 LIMIT 1",
        {"v": body.vendor_id, "g": gross, "d": body.invoice_date})
    if twin is not None:
        notes.append(f"possible duplicate of invoice {twin['supplier_invoice_no']} "
                     "(same vendor and amount within a week)")
    with db_errors("This vendor invoice number is already entered"):
        created = await one(
            ctx, "INSERT INTO erp.supplier_bill(tenant_id, vendor_id, unit_id, "
            "supplier_invoice_no, invoice_date, taxable_amount, tax_amount, status, due_date, "
            "created_by, exception_note) "
            "VALUES (erp.current_tenant_id(), :v, :u, :n, :d, :net, :tax, :st, :due, :me, :note) "
            "RETURNING bill_id",
            {"v": body.vendor_id, "u": body.unit_id, "n": body.supplier_invoice_no,
             "d": body.invoice_date, "net": taxable_total, "tax": tax_total,
             "st": "EXCEPTION" if notes else "MATCHED",
             "due": await due_date_for(ctx, vendor, body.invoice_date),
             "me": ctx.principal.profile.app_user_id, "note": "; ".join(notes) or None})
    assert created is not None
    for number, entry in enumerate(planned, start=1):
        line = entry["line"]
        made = await one(
            ctx, "INSERT INTO erp.supplier_bill_line(tenant_id, bill_id, line_no, description, "
            "item_id, quantity, unit_rate, tax_code_id, taxable_amount, tax_amount, po_line_id, "
            "grn_line_id, expense_account_id, match_status) VALUES (erp.current_tenant_id(), :b, "
            ":n, :d, :i, :q, :r, :t, :net, :gst, :pl, :gl, :ex, :ms) RETURNING bill_line_id",
            {"b": created["bill_id"], "n": number, "d": entry["description"],
             "i": line.item_id or entry.get("item_id"), "q": line.quantity, "r": line.unit_rate,
             "t": line.tax_code_id, "net": entry["net"], "gst": entry["gst"],
             "pl": line.po_line_id, "gl": line.grn_line_id, "ex": line.expense_account_id,
             "ms": entry["status"]})
        assert made is not None
        if entry["status"] != "UNMATCHED":
            await rows(
                ctx, "INSERT INTO erp.bill_match(tenant_id, bill_line_id, po_line_id, grn_line_id, "
                "billed_qty, billed_rate, qty_variance, rate_variance, exception_status) VALUES "
                "(erp.current_tenant_id(), :bl, :pl, :gl, :q, :r, :qv, :rv, :st) "
                "RETURNING bill_match_id",
                {"bl": made["bill_line_id"], "pl": line.po_line_id, "gl": line.grn_line_id,
                 "q": line.quantity, "r": line.unit_rate, "qv": entry["qty_variance"],
                 "rv": entry["rate_variance"], "st": entry["status"]})
    await audit(ctx, "bill.enter", "supplier_bill", created["bill_id"],
                {"invoice_no": body.supplier_invoice_no, "gross": gross,
                 "exceptions": len(notes)})
    result = await load(ctx, created["bill_id"])
    assert result is not None
    return result


@router.get("/bills", response_model=Page[BillSummary])
async def list_bills(
    vendor_id: UUID | None = None, bill_status: str | None = Query(None, alias="status"),
    unit_id: UUID | None = None, due_by: date | None = None, page: Paging = Depends(),
    ctx: Ctx = Depends(read),
) -> Page[BillSummary]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    clause, scope_params = scope_clause(ctx, "b.unit_id")
    if clause:
        conditions.append(clause)
        params.update(scope_params)
    for text_, key, value in (
            ("b.vendor_id = :v", "v", vendor_id), ("b.unit_id = :u", "u", unit_id),
            ("b.status = :st", "st", bill_status.upper() if bill_status else None),
            ("b.due_date <= :due", "due", due_by)):
        if value is not None:
            conditions.append(text_)
            params[key] = value
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    items = await rows(ctx, f"{BILL_SQL}{where} ORDER BY b.invoice_date DESC, "
                       "b.supplier_invoice_no LIMIT :limit OFFSET :offset",
                       {**params, "limit": page.limit, "offset": page.offset})
    total = await count(ctx, f"SELECT count(*) FROM erp.supplier_bill b{where}", params)
    return Page(items=[BillSummary(**i) for i in items], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/bills/{bill_id}", response_model=BillOut)
async def get_bill(bill_id: UUID, ctx: Ctx = Depends(read)) -> BillOut:
    found = await load(ctx, bill_id)
    if found is None:
        raise not_found("Bill")
    return found


async def require_bill(ctx: Ctx, bill_id: UUID, *allowed: str) -> dict[str, Any]:
    clause, params = scope_clause(ctx, "unit_id")
    head = await one(
        ctx, "SELECT bill_id, unit_id, vendor_id, supplier_invoice_no, status, paid_amount, "
        "taxable_amount, tax_amount, invoice_date, journal_id FROM erp.supplier_bill WHERE "
        f"bill_id = :id{' AND ' + clause if clause else ''} FOR UPDATE", {**params, "id": bill_id})
    if head is None:
        raise not_found("Bill")
    if head["status"] not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The bill is {str(head['status']).lower().replace('_', ' ')}; this needs it to be "
            f"{' or '.join(a.lower().replace('_', ' ') for a in allowed)}")
    return head


@router.post("/bills/{bill_id}/submit", response_model=BillOut)
async def submit_bill(bill_id: UUID, ctx: Ctx = Depends(manage)) -> BillOut:
    """Send a bill with match exceptions to Accounts for approval."""
    head = await require_bill(ctx, bill_id, "EXCEPTION")
    await rows(ctx, "UPDATE erp.supplier_bill SET status = 'PENDING_APPROVAL' WHERE bill_id = :id "
               "RETURNING bill_id", {"id": bill_id})
    approval_id = await engine.submit(ctx, "bill", bill_id)
    await audit(ctx, "bill.submit", "supplier_bill", bill_id,
                {"invoice_no": head["supplier_invoice_no"], "approval_id": approval_id})
    result = await load(ctx, bill_id)
    assert result is not None
    return result


@router.post("/bills/{bill_id}/book", response_model=BillOut)
async def book_bill(bill_id: UUID, ctx: Ctx = Depends(manage)) -> BillOut:
    """Post the bill: clear goods-received-not-billed, take the differences, credit the vendor."""
    head = await require_bill(ctx, bill_id, "MATCHED", "APPROVED")
    unit_id = head["unit_id"]
    if not await finance_enabled(ctx, unit_id):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Set up the plant's chart of accounts before booking bills")
    lines = await rows(
        ctx, "SELECT l.taxable_amount, l.tax_amount, l.expense_account_id, l.match_status, "
        "l.po_line_id, l.grn_line_id, m.billed_qty, pl.unit_price FROM erp.supplier_bill_line l "
        "LEFT JOIN erp.bill_match m ON m.bill_line_id = l.bill_line_id LEFT JOIN erp.po_line pl "
        "ON pl.po_line_id = l.po_line_id WHERE l.bill_id = :id", {"id": bill_id})
    debit: dict[UUID, Decimal] = {}
    variance = Decimal(0)
    clearing = Decimal(0)
    gst = Decimal(0)
    for ln in lines:
        gst += ln["tax_amount"]
        if ln["match_status"] == "UNMATCHED":
            account = ln["expense_account_id"]
            debit[account] = debit.get(account, Decimal(0)) + ln["taxable_amount"]
        else:
            cleared = money(ln["billed_qty"] * ln["unit_price"])
            clearing += cleared
            variance += ln["taxable_amount"] - cleared
    postings: list[LineIn] = []
    if clearing:
        postings.append(LineIn(account_id=await role_account(ctx, unit_id, "GRN_CLEARING"),
                               debit=clearing))
    for account, amount in debit.items():
        postings.append(LineIn(account_id=account, debit=amount))
    if variance > 0:
        postings.append(LineIn(account_id=await role_account(ctx, unit_id, "PURCHASE_VARIANCE"),
                               debit=variance))
    elif variance < 0:
        postings.append(LineIn(account_id=await role_account(ctx, unit_id, "PURCHASE_VARIANCE"),
                               credit=-variance))
    if gst:
        postings.append(LineIn(account_id=await role_account(ctx, unit_id, "GST_INPUT"), debit=gst))
    gross = head["taxable_amount"] + head["tax_amount"]
    postings.append(LineIn(account_id=await role_account(ctx, unit_id, "PAYABLE"), credit=gross,
                           description=f"Invoice {head['supplier_invoice_no']}",
                           party_type="VENDOR", party_id=head["vendor_id"]))
    journal_id = await post_entry(
        ctx, unit_id=unit_id, journal_date=date.today(), source_type="SUPPLIER_BILL",
        source_id=bill_id, narration=f"Supplier bill {head['supplier_invoice_no']}", lines=postings)
    await rows(ctx, "UPDATE erp.supplier_bill SET status = 'BOOKED', booked_at = now(), "
               "journal_id = :j WHERE bill_id = :id RETURNING bill_id",
               {"j": journal_id, "id": bill_id})
    await audit(ctx, "bill.book", "supplier_bill", bill_id,
                {"invoice_no": head["supplier_invoice_no"], "gross": gross,
                 "journal_id": journal_id})
    result = await load(ctx, bill_id)
    assert result is not None
    return result


@router.post("/bills/{bill_id}/cancel", response_model=BillOut)
async def cancel_bill(bill_id: UUID, ctx: Ctx = Depends(manage)) -> BillOut:
    """Cancel a bill that was not paid; a booked bill is reversed in the books."""
    head = await require_bill(ctx, bill_id, "MATCHED", "EXCEPTION", "REJECTED", "APPROVED",
                              "BOOKED")
    reversal = None
    if head["status"] == "BOOKED":
        if head["paid_amount"] > 0:
            raise HTTPException(status.HTTP_409_CONFLICT, "Part of this bill is already paid")
        reversed_ = await reverse_journal(
            head["journal_id"], ReverseIn(reason=f"Bill {head['supplier_invoice_no']} cancelled"),
            ctx)
        reversal = reversed_.journal_id
    await rows(ctx, "UPDATE erp.supplier_bill SET status = 'CANCELLED', reversal_journal_id = :r "
               "WHERE bill_id = :id RETURNING bill_id", {"r": reversal, "id": bill_id})
    await audit(ctx, "bill.cancel", "supplier_bill", bill_id,
                {"invoice_no": head["supplier_invoice_no"], "reversal_id": reversal})
    result = await load(ctx, bill_id)
    assert result is not None
    return result


# ------------------------------------------------------------------ approval adapter
async def load_for_approval(ctx: Ctx, bill_id: UUID) -> DocInfo | None:
    head = await one(
        ctx, "SELECT supplier_invoice_no, status, taxable_amount + tax_amount AS gross FROM "
        "erp.supplier_bill WHERE bill_id = :id", {"id": bill_id})
    if head is None:
        return None
    return DocInfo(
        title=f"Supplier bill {head['supplier_invoice_no']}", amount=head["gross"],
        department_id=None, submittable=head["status"] == "PENDING_APPROVAL",
        reason=f"The bill is {str(head['status']).lower().replace('_', ' ')}")


async def finalize_bill(ctx: Ctx, bill_id: UUID, approved: bool) -> None:
    await rows(ctx, "UPDATE erp.supplier_bill SET status = :s WHERE bill_id = :id RETURNING "
               "bill_id", {"s": "APPROVED" if approved else "REJECTED", "id": bill_id})


register(DocumentType("bill", "fin.bill", load_for_approval, finalize_bill))

