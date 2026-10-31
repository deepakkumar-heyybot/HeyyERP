"""GST return data and the GSTR-2B check (BRD FR-FIN-07).

This prepares the numbers for a plant and month from the books; it does not file anything. GSTR-1
lists the month's outward invoices (business customers by GSTIN, the rest summed), the cancelled
ones, and the tax split into CGST and SGST (same state) or IGST (another state). GSTR-3B sets the
output tax against the input credit on the month's booked supplier bills. The GSTR-2B check takes
what suppliers reported and compares it with the booked bills: matched, differing, missing in the
books, or booked but not reported (credit at risk). A prepared return is rebuilt each time it is
asked for; once marked filed it is kept as it was. The state of a party is the first two digits of
its GSTIN; a customer without a GSTIN is taken to be in the plant's own state. Needs the gst
permission; reading needs the gst module.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from heyyerp_api.finance.period import get_period, month_start
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
)

router = APIRouter(prefix="/gst", tags=["gst"])
manage = requires("gst")
read = requires_module("gst")
ZERO = Decimal(0)
ReturnType = Literal["GSTR1", "GSTR3B", "GSTR2B"]
BILL_STATES = ("BOOKED", "PARTIALLY_PAID", "PAID")


class ReturnOut(BaseModel):
    return_data_id: UUID
    unit_id: UUID
    month: date
    return_type: str
    status: str
    generated_at: datetime
    filed_reference: str | None
    payload: dict[str, Any]


class PrepareIn(Strict):
    unit_id: UUID
    month: date  # any day of the month
    return_type: ReturnType = "GSTR1"


class FileIn(Strict):
    reference: str = Field(min_length=3, max_length=60)  # acknowledgement number of the filing


class Reported(Strict):
    supplier_gstin: str = Field(min_length=15, max_length=15)
    invoice_no: str = Field(min_length=1, max_length=40)
    invoice_date: date | None = None
    taxable_amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    tax_amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)


class ReconcileIn(Strict):
    unit_id: UUID
    month: date
    lines: list[Reported] = Field(max_length=5000)


def split(taxable: Decimal, tax: Decimal, same_state: bool) -> dict[str, str]:
    """Tax by head: half each to CGST and SGST inside the state, all IGST otherwise."""
    if same_state:
        half = money(tax / 2)
        return {"cgst": str(half), "sgst": str(tax - half), "igst": "0.00"}
    return {"cgst": "0.00", "sgst": "0.00", "igst": str(tax)}


async def unit_state(ctx: Ctx, unit_id: UUID) -> str:
    unit = await one(ctx, "SELECT state_code, gstin FROM erp.unit WHERE unit_id = :u",
                     {"u": unit_id})
    state = (unit or {}).get("state_code") or ((unit or {}).get("gstin") or "")[:2]
    if not state:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Set the plant's state code or GSTIN before preparing GST returns")
    return str(state)


def bounds(month: date) -> tuple[date, date]:
    first = month_start(month)
    return first, date(first.year + (first.month == 12), first.month % 12 + 1, 1)


def heads(items: list[dict[str, Any]]) -> dict[str, str]:
    return {h: str(sum((Decimal(i[h]) for i in items), ZERO)) for h in ("cgst", "sgst", "igst")}


async def gstr1(ctx: Ctx, unit_id: UUID, first: date, after: date) -> dict[str, Any]:
    state = await unit_state(ctx, unit_id)
    data = await rows(
        ctx, "SELECT v.invoice_no, v.issue_date, v.status, v.taxable_amount, v.tax_amount, "
        "c.code AS customer_code, c.name AS customer_name, c.gstin FROM erp.customer_invoice v "
        "JOIN erp.customer c ON c.customer_id = v.customer_id WHERE v.unit_id = :u AND "
        "v.issue_date >= :a AND v.issue_date < :b ORDER BY v.invoice_no",
        {"u": unit_id, "a": first, "b": after})
    b2b: list[dict[str, Any]] = []
    b2c: list[dict[str, Any]] = []
    cancelled: list[str] = []
    for d in data:
        if d["status"] == "CANCELLED":
            cancelled.append(d["invoice_no"])
            continue
        gstin = d["gstin"]
        entry = {"invoice_no": d["invoice_no"], "invoice_date": d["issue_date"].isoformat(),
                 "customer_gstin": gstin, "customer": d["customer_name"],
                 "taxable_amount": str(d["taxable_amount"]),
                 **split(d["taxable_amount"], d["tax_amount"],
                         gstin is None or gstin[:2] == state)}
        entry["total"] = str(d["taxable_amount"] + d["tax_amount"])
        (b2b if gstin else b2c).append(entry)
    everything = b2b + b2c
    return {"b2b": b2b, "b2c": {"count": len(b2c), "taxable_amount": str(sum(
        (Decimal(i["taxable_amount"]) for i in b2c), ZERO)), **heads(b2c)},
        "cancelled_invoices": cancelled,
        "totals": {"invoices": len(everything), "taxable_amount": str(sum(
            (Decimal(i["taxable_amount"]) for i in everything), ZERO)), **heads(everything)}}


async def bills_in(ctx: Ctx, unit_id: UUID, first: date, after: date) -> list[dict[str, Any]]:
    return await rows(
        ctx, "SELECT b.bill_id, b.supplier_invoice_no, b.invoice_date, b.taxable_amount, "
        "b.tax_amount, v.code AS vendor_code, v.name AS vendor_name, v.gstin FROM "
        "erp.supplier_bill b JOIN erp.vendor v ON v.vendor_id = b.vendor_id WHERE b.unit_id = :u "
        "AND b.status = ANY(CAST(:st AS text[])) AND b.invoice_date >= :a AND b.invoice_date < :b "
        "ORDER BY b.supplier_invoice_no", {"u": unit_id, "a": first, "b": after,
                                           "st": list(BILL_STATES)})


async def gstr3b(ctx: Ctx, unit_id: UUID, first: date, after: date) -> dict[str, Any]:
    state = await unit_state(ctx, unit_id)
    outward = (await gstr1(ctx, unit_id, first, after))["totals"]
    inward = [{**split(b["taxable_amount"], b["tax_amount"],
                       b["gstin"] is None or b["gstin"][:2] == state),
               "taxable_amount": str(b["taxable_amount"])}
              for b in await bills_in(ctx, unit_id, first, after)]
    credit: dict[str, Any] = {"bills": len(inward), "taxable_amount": str(sum(
        (Decimal(i["taxable_amount"]) for i in inward), ZERO)), **heads(inward)}
    net = {h: str(Decimal(outward[h]) - Decimal(credit[h])) for h in ("cgst", "sgst", "igst")}
    return {"outward": outward, "input_credit": credit,
            # negative is credit left over; set-off between heads is for the filer
            "net_payable": net,
            "total_payable": str(max(sum((Decimal(v) for v in net.values()), ZERO), ZERO))}


def key(gstin: str | None, invoice_no: str) -> tuple[str, str]:
    return (gstin or "").strip().upper(), invoice_no.strip().upper()


async def gstr2b(ctx: Ctx, body: ReconcileIn) -> dict[str, Any]:
    first, after = bounds(body.month)
    books = {key(b["gstin"], b["supplier_invoice_no"]): b
             for b in await bills_in(ctx, body.unit_id, first, after)}
    seen: set[tuple[str, str]] = set()
    result: dict[str, list[dict[str, Any]]] = {
        "matched": [], "differing": [], "missing_in_books": [], "missing_in_2b": []}
    for line in body.lines:
        k = key(line.supplier_gstin, line.invoice_no)
        seen.add(k)
        base = {"supplier_gstin": k[0], "invoice_no": line.invoice_no,
                "reported_taxable": str(line.taxable_amount), "reported_tax": str(line.tax_amount)}
        bill = books.get(k)
        if bill is None:
            result["missing_in_books"].append(base)
        elif (abs(bill["taxable_amount"] - line.taxable_amount) <= 1
              and abs(bill["tax_amount"] - line.tax_amount) <= 1):
            result["matched"].append({**base, "bill_id": str(bill["bill_id"])})
        else:
            result["differing"].append({
                **base, "bill_id": str(bill["bill_id"]), "booked_taxable": str(
                    bill["taxable_amount"]), "booked_tax": str(bill["tax_amount"])})
    for k, bill in books.items():
        if k not in seen and bill["gstin"]:  # unregistered suppliers never appear in 2B
            result["missing_in_2b"].append({
                "supplier_gstin": k[0], "invoice_no": bill["supplier_invoice_no"],
                "bill_id": str(bill["bill_id"]), "booked_tax": str(bill["tax_amount"])})
    return {**result, "summary": {
        **{name: len(items) for name, items in result.items()},
        "credit_at_risk": str(sum((Decimal(i["booked_tax"]) for i in result["missing_in_2b"]),
                                  ZERO))}}


async def store(ctx: Ctx, unit_id: UUID, month: date, return_type: str,
                payload: dict[str, Any]) -> UUID:
    period = await get_period(ctx, unit_id, month)
    current = await one(
        ctx, "SELECT return_data_id, status FROM erp.gst_return_data WHERE unit_id = :u AND "
        "period_id = :p AND return_type = :t FOR UPDATE",
        {"u": unit_id, "p": period["period_id"], "t": return_type})
    if current is not None and current["status"] == "FILED":
        raise HTTPException(status.HTTP_409_CONFLICT, "This return is filed and kept as filed")
    args = {"u": unit_id, "p": period["period_id"], "t": return_type, "d": json.dumps(payload)}
    saved = await one(
        ctx, "INSERT INTO erp.gst_return_data(tenant_id, unit_id, period_id, return_type, "
        "payload) VALUES (erp.current_tenant_id(), :u, :p, :t, CAST(:d AS jsonb)) ON CONFLICT "
        "(unit_id, period_id, return_type) DO UPDATE SET payload = EXCLUDED.payload, "
        "generated_at = now() RETURNING return_data_id", args)
    assert saved is not None
    return saved["return_data_id"]  # type: ignore[no-any-return]


SELECT = ("SELECT r.return_data_id, r.unit_id, p.year_month AS month, r.return_type, r.status, "
          "r.generated_at, r.filed_reference, r.payload FROM erp.gst_return_data r JOIN "
          "erp.fiscal_period p ON p.period_id = r.period_id")


async def load(ctx: Ctx, return_id: UUID) -> ReturnOut:
    found = await one(ctx, f"{SELECT} WHERE r.return_data_id = :id", {"id": return_id})
    if found is None:
        raise not_found("Return")
    return ReturnOut(**found)


@router.post("/returns", response_model=ReturnOut)
async def prepare(body: PrepareIn, ctx: Ctx = Depends(manage)) -> ReturnOut:
    """Build (or rebuild) the data of a return from the books."""
    require_in_scope(ctx, body.unit_id)
    first, after = bounds(body.month)
    if body.return_type == "GSTR2B":
        raise HTTPException(422, "GSTR-2B is prepared by reconciling what suppliers reported; "
                                 "use /gst/gstr2b/reconcile")
    build = gstr1 if body.return_type == "GSTR1" else gstr3b
    payload = await build(ctx, body.unit_id, first, after)
    return_id = await store(ctx, body.unit_id, first, body.return_type, payload)
    await audit(ctx, "gst.prepare", "gst_return_data", return_id,
                {"return_type": body.return_type, "month": first.isoformat()})
    return await load(ctx, return_id)


@router.post("/gstr2b/reconcile", response_model=ReturnOut)
async def reconcile(body: ReconcileIn, ctx: Ctx = Depends(manage)) -> ReturnOut:
    """Compare what suppliers reported (GSTR-2B) with the booked bills of the month."""
    require_in_scope(ctx, body.unit_id)
    payload = await gstr2b(ctx, body)
    return_id = await store(ctx, body.unit_id, bounds(body.month)[0], "GSTR2B", payload)
    await audit(ctx, "gst.reconcile", "gst_return_data", return_id, payload["summary"])
    return await load(ctx, return_id)


@router.post("/returns/{return_id}/file", response_model=ReturnOut)
async def mark_filed(return_id: UUID, body: FileIn, ctx: Ctx = Depends(manage)) -> ReturnOut:
    """Record that the return was filed on the portal; it is then kept as it was."""
    current = await load(ctx, return_id)
    require_in_scope(ctx, current.unit_id)
    if current.status == "FILED":
        raise HTTPException(status.HTTP_409_CONFLICT, "The return is already filed")
    await rows(ctx, "UPDATE erp.gst_return_data SET status = 'FILED', filed_at = now(), "
               "filed_reference = :r WHERE return_data_id = :id RETURNING return_data_id",
               {"r": body.reference, "id": return_id})
    await audit(ctx, "gst.file", "gst_return_data", return_id, {"reference": body.reference})
    return await load(ctx, return_id)


@router.get("/returns", response_model=list[ReturnOut])
async def list_returns(
    unit_id: UUID, return_type: str | None = Query(None), ctx: Ctx = Depends(read)
) -> list[ReturnOut]:
    require_in_scope(ctx, unit_id)
    data = await rows(
        ctx, f"{SELECT} WHERE r.unit_id = :u AND (CAST(:t AS text) IS NULL OR r.return_type = :t) "
        "ORDER BY p.year_month DESC, r.return_type",
        {"u": unit_id, "t": return_type.upper() if return_type else None})
    return [ReturnOut(**d) for d in data]


@router.get("/returns/{return_id}", response_model=ReturnOut)
async def get_return(return_id: UUID, ctx: Ctx = Depends(read)) -> ReturnOut:
    found = await load(ctx, return_id)
    require_in_scope(ctx, found.unit_id)
    return found
