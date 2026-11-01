"""Automatic postings from business documents into the books (BRD FR-FIN-01).

A plant takes part only once it has a chart of accounts (an account mapped to GRN_CLEARING):
until then its documents are not posted and nothing else is blocked. Each posting is made once per
source document (the journal remembers its source) and uses the plant's mapped accounts.
Goods received are valued at the purchase-order price: debit inventory, credit "goods received not
billed", which the supplier bill later clears.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from heyyerp_api.business_rules import service as rules
from heyyerp_api.finance.accounts import role_account
from heyyerp_api.finance.journals import LineIn, post_entry
from heyyerp_api.inventory.ledger import money
from heyyerp_api.masters.common import Ctx, one, rows


async def finance_enabled(ctx: Ctx, unit_id: UUID) -> bool:
    found = await one(ctx, "SELECT 1 AS x FROM erp.account_role WHERE unit_id = :u AND role = "
                      "'GRN_CLEARING'", {"u": unit_id})
    return found is not None


async def book(
    ctx: Ctx, *, unit_id: UUID, source_type: str, source_id: UUID, narration: str,
    amounts: list[tuple[str, Decimal, Decimal]], day: date | None = None, production: bool = False,
) -> UUID | None:
    """Post (role, debit, credit) lines for a plant once per source; None when the plant has no
    chart, the production-posting rule is off (for production postings) or there is no value."""
    if not any(debit or credit for _, debit, credit in amounts):
        return None
    if not await finance_enabled(ctx, unit_id):
        return None
    if production and not await rules.get_value(ctx, "productionPosting"):
        return None
    lines = [LineIn(account_id=await role_account(ctx, unit_id, role), debit=debit, credit=credit)
             for role, debit, credit in amounts if debit or credit]
    return await post_entry(ctx, unit_id=unit_id, journal_date=day or date.today(),
                            source_type=source_type, source_id=source_id, narration=narration,
                            lines=lines)


INVENTORY_ROLE = {"RM": "INVENTORY_RM", "WIP": "INVENTORY_WIP", "FG": "INVENTORY_FG"}


async def book_stock(
    ctx: Ctx, *, unit_id: UUID, source_type: str, source_id: UUID, narration: str,
    values: dict[UUID, Decimal], expense_role: str, outflow: bool,
) -> UUID | None:
    """Book stock leaving (debit the expense, credit the inventory of each item type) or arriving
    (the reverse) at the given values. Raw material, work in progress and finished goods have their
    own inventory accounts; spares, consumables and the rest sit with the raw material store."""
    if not values or not any(values.values()) or not await finance_enabled(ctx, unit_id):
        return None
    kinds = {r["item_id"]: r["item_type"] for r in await rows(
        ctx, "SELECT item_id, item_type FROM erp.item WHERE item_id = ANY(CAST(:ids AS uuid[]))",
        {"ids": list(values)})}
    by_role: dict[str, Decimal] = {}
    for item_id, value in values.items():
        role = INVENTORY_ROLE.get(kinds.get(item_id, "RM"), "INVENTORY_RM")
        by_role[role] = by_role.get(role, Decimal(0)) + value
    total = sum(by_role.values(), Decimal(0))
    zero = Decimal(0)
    if outflow:
        amounts = [(expense_role, total, zero)] + [(r, zero, v) for r, v in by_role.items()]
    else:
        amounts = [(r, v, zero) for r, v in by_role.items()] + [(expense_role, zero, total)]
    return await book(ctx, unit_id=unit_id, source_type=source_type, source_id=source_id,
                      narration=narration, amounts=amounts)


async def inventory_by_role(ctx: Ctx, values: dict[UUID, Decimal]) -> dict[str, Decimal]:
    """The values grouped by the inventory account of each item's type."""
    kinds = {r["item_id"]: r["item_type"] for r in await rows(
        ctx, "SELECT item_id, item_type FROM erp.item WHERE item_id = ANY(CAST(:ids AS uuid[]))",
        {"ids": list(values)})}
    by_role: dict[str, Decimal] = {}
    for item_id, value in values.items():
        role = INVENTORY_ROLE.get(kinds.get(item_id, "RM"), "INVENTORY_RM")
        by_role[role] = by_role.get(role, Decimal(0)) + value
    return by_role


async def book_transfer(
    ctx: Ctx, transfer_id: UUID, scheme: str, label: str
) -> tuple[UUID | None, UUID | None]:
    """Book a transfer between two plants at the cost that left, once, in both plants.

    AT_COST: the sender debits inter-unit receivable and credits its inventory; the receiver debits
    its inventory and credits inter-unit payable. SALE_AND_PURCHASE: the sender also books the sale
    (credit inter-unit sales) and its cost (debit inter-unit purchases), so its profit is nil at
    cost and the consolidation eliminates the pair. Returns the sender's and the receiver's
    journal."""
    head = await one(
        ctx, "SELECT t.transfer_no, f.unit_id AS from_unit, d.unit_id AS to_unit FROM "
        "erp.stock_transfer t JOIN erp.store f ON f.store_id = t.from_store_id JOIN erp.store d "
        "ON d.store_id = t.to_store_id WHERE t.transfer_id = :id", {"id": transfer_id})
    if head is None or head["from_unit"] == head["to_unit"]:
        return None, None
    values = {r["item_id"]: money(r["v"]) for r in await rows(
        ctx, "SELECT item_id, sum(quantity * unit_cost) AS v FROM erp.stock_transfer_line WHERE "
        "transfer_id = :id GROUP BY item_id", {"id": transfer_id})}
    by_role = await inventory_by_role(ctx, values)
    total = sum(by_role.values(), Decimal(0))
    zero = Decimal(0)
    narration = f"Inter-unit transfer {head['transfer_no']} ({label})"
    out: list[tuple[str, Decimal, Decimal]] = [("INTER_UNIT_RECEIVABLE", total, zero)]
    if scheme == "SALE_AND_PURCHASE":
        out += [("INTER_UNIT_SALES", zero, total), ("INTER_UNIT_PURCHASES", total, zero)]
    out += [(role, zero, v) for role, v in by_role.items()]
    sent = await book(ctx, unit_id=head["from_unit"], source_type="TRANSFER_FROM",
                      source_id=transfer_id, narration=narration, amounts=out)
    got = await book(
        ctx, unit_id=head["to_unit"], source_type="TRANSFER_TO", source_id=transfer_id,
        narration=narration, amounts=[(role, v, zero) for role, v in by_role.items()]
        + [("INTER_UNIT_PAYABLE", zero, total)])
    return sent, got


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
