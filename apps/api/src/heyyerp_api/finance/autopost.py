"""Automatic postings from business documents into the books (BRD FR-FIN-01).

A plant takes part only once it has a chart of accounts (an account mapped to GRN_CLEARING):
until then its documents are not posted and nothing else is blocked. Each posting is made once per
source document (the journal remembers its source) and uses the plant's mapped accounts.
Goods received are valued at the purchase-order price: debit inventory, credit "goods received not
billed", which the supplier bill later clears.
"""

from datetime import date
from typing import Any
from uuid import UUID

from heyyerp_api.finance.accounts import role_account
from heyyerp_api.finance.journals import LineIn, post_entry
from heyyerp_api.inventory.ledger import money
from heyyerp_api.masters.common import Ctx, one


async def finance_enabled(ctx: Ctx, unit_id: UUID) -> bool:
    found = await one(ctx, "SELECT 1 AS x FROM erp.account_role WHERE unit_id = :u AND role = "
                      "'GRN_CLEARING'", {"u": unit_id})
    return found is not None


async def post_sales_invoice(ctx: Ctx, invoice_id: UUID) -> UUID | None:
    """Book a customer invoice: debit the customer, credit sales and GST output (once)."""
    inv: dict[str, Any] | None = await one(
        ctx, "SELECT unit_id, customer_id, invoice_no, issue_date, taxable_amount, tax_amount FROM "
        "erp.customer_invoice WHERE invoice_id = :id", {"id": invoice_id})
    if inv is None or not await finance_enabled(ctx, inv["unit_id"]):
        return None
    unit = inv["unit_id"]
    lines = [LineIn(account_id=await role_account(ctx, unit, "RECEIVABLE"),
                    debit=inv["taxable_amount"] + inv["tax_amount"], party_type="CUSTOMER",
                    party_id=inv["customer_id"], description=f"Invoice {inv['invoice_no']}"),
             LineIn(account_id=await role_account(ctx, unit, "SALES"),
                    credit=inv["taxable_amount"])]
    if inv["tax_amount"] > 0:
        lines.append(LineIn(account_id=await role_account(ctx, unit, "GST_OUTPUT"),
                            credit=inv["tax_amount"]))
    if inv["taxable_amount"] == 0:
        lines = lines[:1] + lines[2:]
    return await post_entry(
        ctx, unit_id=unit, journal_date=inv["issue_date"], source_type="CUSTOMER_INVOICE",
        source_id=invoice_id, narration=f"Customer invoice {inv['invoice_no']}", lines=lines)


async def post_grn_receipt(ctx: Ctx, grn_line_id: UUID) -> UUID | None:
    """Book the accepted quantity of a goods-receipt line at the PO price (once)."""
    line: dict[str, Any] | None = await one(
        ctx, "SELECT g.unit_id, g.grn_no, gl.accepted_qty, pl.unit_price FROM erp.grn_line gl "
        "JOIN erp.grn g ON g.grn_id = gl.grn_id JOIN erp.po_line pl ON pl.po_line_id = "
        "gl.po_line_id WHERE gl.grn_line_id = :id", {"id": grn_line_id})
    if line is None or line["accepted_qty"] <= 0 or not await finance_enabled(ctx, line["unit_id"]):
        return None
    amount = money(line["accepted_qty"] * line["unit_price"])
    if amount == 0:
        return None
    return await post_entry(
        ctx, unit_id=line["unit_id"], journal_date=date.today(), source_type="GRN_LINE",
        source_id=grn_line_id, narration=f"Goods received {line['grn_no']}",
        lines=[LineIn(account_id=await role_account(ctx, line["unit_id"], "INVENTORY_RM"),
                      debit=amount),
               LineIn(account_id=await role_account(ctx, line["unit_id"], "GRN_CLEARING"),
                      credit=amount)])
