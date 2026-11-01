"""Building the e-invoice and e-way bill requests from an invoice (the standard NIC JSON shapes).

Everything the portal needs must be on file, so a missing piece (a GSTIN, an address, a pincode, an
HSN code) stops the request with one message that lists all of them, rather than failing at the
provider. The tax is split into CGST and SGST for a customer in the plant's state and IGST for
another state, from the first two digits of the two GSTINs. E-invoices are for customers with a
GSTIN; a customer without one is a consumer sale and has none.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status

from heyyerp_api.masters.common import Ctx, one, rows

UQC = {"NOS": "NOS", "PCS": "PCS", "KG": "KGS", "KGS": "KGS", "MTR": "MTR", "LTR": "LTR",
       "SET": "SET", "BOX": "BOX", "PKT": "PAC"}
ZERO = Decimal(0)


def money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def day(value: date) -> str:
    return f"{value:%d/%m/%Y}"


async def invoice_data(ctx: Ctx, invoice_id: UUID) -> dict[str, Any]:
    head = await one(
        ctx, "SELECT v.invoice_no, v.issue_date, v.taxable_amount, v.tax_amount, v.status, "
        "v.unit_id, u.gstin AS s_gstin, u.name AS s_name, u.address_line AS s_addr, u.city AS "
        "s_city, u.pincode AS s_pin, u.state_code AS s_state, c.gstin AS b_gstin, c.name AS "
        "b_name, c.address_line AS b_addr, c.city AS b_city, c.pincode AS b_pin FROM "
        "erp.customer_invoice v JOIN erp.unit u ON u.unit_id = v.unit_id JOIN erp.customer c ON "
        "c.customer_id = v.customer_id WHERE v.invoice_id = :id", {"id": invoice_id})
    if head is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    if head["status"] != "ISSUED":
        raise HTTPException(status.HTTP_409_CONFLICT, f"The invoice is {head['status'].lower()}")
    lines = await rows(
        ctx, "SELECT l.quantity, l.unit_price, l.taxable_amount, l.tax_amount, i.code, i.name, "
        "i.hsn_code, m.code AS uom FROM erp.invoice_line l JOIN erp.item i ON i.item_id = "
        "l.item_id JOIN erp.uom m ON m.uom_id = i.uom_id WHERE l.invoice_id = :id "
        "ORDER BY l.invoice_line_id", {"id": invoice_id})
    return {**head, "lines": lines}


def missing(data: dict[str, Any]) -> list[str]:
    """What is not on file that the portal needs."""
    gaps = []
    for who, prefix in (("the plant", "s"), ("the customer", "b")):
        if not data[f"{prefix}_gstin"]:
            gaps.append(f"{who}'s GSTIN")
        if not data[f"{prefix}_addr"]:
            gaps.append(f"{who}'s address")
        if not data[f"{prefix}_pin"]:
            gaps.append(f"{who}'s pincode")
    for line in data["lines"]:
        if not line["hsn_code"]:
            gaps.append(f"the HSN code of item {line['code']}")
    return gaps


def split(data: dict[str, Any], tax: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """(CGST, SGST, IGST) of an amount of tax, by the states of the two GSTINs."""
    if data["s_gstin"][:2] == data["b_gstin"][:2]:
        half = (tax / 2).quantize(Decimal("0.01"))
        return half, tax - half, ZERO
    return ZERO, ZERO, tax


async def irn_payload(ctx: Ctx, invoice_id: UUID) -> dict[str, Any]:
    data = await invoice_data(ctx, invoice_id)
    if not data["b_gstin"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "An e-invoice is for a customer with a GSTIN; this is a consumer sale")
    gaps = missing(data)
    if gaps:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Cannot raise the e-invoice; missing: " + ", ".join(gaps))
    items: list[dict[str, Any]] = []
    for n, line in enumerate(data["lines"], start=1):
        cgst, sgst, igst = split(data, line["tax_amount"])
        rate = (line["tax_amount"] / line["taxable_amount"] * 100 if line["taxable_amount"] else
                ZERO)
        items.append({
            "SlNo": str(n), "PrdDesc": line["name"], "IsServc": "N", "HsnCd": line["hsn_code"],
            "Qty": float(line["quantity"]), "Unit": UQC.get(line["uom"], "OTH"),
            "UnitPrice": money(line["unit_price"]), "TotAmt": money(line["taxable_amount"]),
            "AssAmt": money(line["taxable_amount"]), "GstRt": float(round(rate, 2)),
            "CgstAmt": money(cgst), "SgstAmt": money(sgst), "IgstAmt": money(igst),
            "TotItemVal": money(line["taxable_amount"] + line["tax_amount"])})
    cgst, sgst, igst = split(data, data["tax_amount"])
    return {
        "Version": "1.1",
        "TranDtls": {"TaxSch": "GST", "SupTyp": "B2B", "RegRev": "N", "IgstOnIntra": "N"},
        "DocDtls": {"Typ": "INV", "No": data["invoice_no"], "Dt": day(data["issue_date"])},
        "SellerDtls": {"Gstin": data["s_gstin"], "LglNm": data["s_name"], "Addr1": data["s_addr"],
                       "Loc": data["s_city"] or data["s_addr"][:50], "Pin": int(data["s_pin"]),
                       "Stcd": data["s_gstin"][:2]},
        "BuyerDtls": {"Gstin": data["b_gstin"], "LglNm": data["b_name"],
                      "Pos": data["b_gstin"][:2], "Addr1": data["b_addr"],
                      "Loc": data["b_city"] or data["b_addr"][:50], "Pin": int(data["b_pin"]),
                      "Stcd": data["b_gstin"][:2]},
        "ItemList": items,
        "ValDtls": {"AssVal": money(data["taxable_amount"]), "CgstVal": money(cgst),
                    "SgstVal": money(sgst), "IgstVal": money(igst),
                    "TotInvVal": money(data["taxable_amount"] + data["tax_amount"])}}


async def ewb_payload(
    ctx: Ctx, invoice_id: UUID, transport: dict[str, Any]
) -> dict[str, Any]:
    data = await invoice_data(ctx, invoice_id)
    gaps = missing(data)
    if gaps:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Cannot raise the e-way bill; missing: " + ", ".join(gaps))
    cgst, sgst, igst = split(data, data["tax_amount"])
    return {
        "supplyType": "O", "subSupplyType": "1", "docType": "INV", "docNo": data["invoice_no"],
        "docDate": day(data["issue_date"]), "fromGstin": data["s_gstin"],
        "fromTrdName": data["s_name"], "fromAddr1": data["s_addr"],
        "fromPlace": data["s_city"] or data["s_addr"][:50], "fromPincode": int(data["s_pin"]),
        "fromStateCode": int(data["s_gstin"][:2]), "toGstin": data["b_gstin"] or "URP",
        "toTrdName": data["b_name"], "toAddr1": data["b_addr"],
        "toPlace": data["b_city"] or data["b_addr"][:50], "toPincode": int(data["b_pin"]),
        "toStateCode": int((data["b_gstin"] or data["s_gstin"])[:2]),
        "totalValue": money(data["taxable_amount"]), "cgstValue": money(cgst),
        "sgstValue": money(sgst), "igstValue": money(igst),
        "totInvValue": money(data["taxable_amount"] + data["tax_amount"]),
        "transMode": {"ROAD": 1, "RAIL": 2, "AIR": 3, "SHIP": 4}[transport["transport_mode"]],
        "transDistance": transport["distance_km"], "vehicleNo": transport["vehicle_no"],
        "vehicleType": "R",
        "itemList": [{"productName": line["name"], "hsnCode": line["hsn_code"],
                      "quantity": float(line["quantity"]), "qtyUnit": UQC.get(line["uom"], "OTH"),
                      "taxableAmount": money(line["taxable_amount"])} for line in data["lines"]]}
