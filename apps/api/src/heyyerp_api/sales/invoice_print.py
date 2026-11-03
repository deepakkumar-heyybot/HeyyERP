"""The tax invoice as a printable PDF, with the e-invoice QR code and the company's own wording.

What a plant prints (legal name, bank details, terms, signatory, footer) is written by the company
in its print profile; the number of copies is the company rule `invoiceCopies` (ONE, or THREE with
the usual Original, Duplicate and Triplicate labels). The QR code is the signed QR the GST provider
returned, so it can be scanned and checked on the portal; an invoice with no IRN prints without it.
Hindi and English print together (the report fonts).
"""

import io
from decimal import Decimal
from typing import Any
from uuid import UUID

import segno
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fpdf import FPDF
from pydantic import BaseModel, Field

from heyyerp_api.business_rules import service as rules
from heyyerp_api.gsp.payload import invoice_data, split
from heyyerp_api.masters.common import (
    Ctx,
    Strict,
    audit,
    not_found,
    one,
    requires,
    requires_module,
    scope_clause,
)
from heyyerp_api.reports.pdf import FONTS, clean

router = APIRouter(tags=["invoice printing"])
read = requires_module("sales")
manage_org = requires("org.manage")
COPIES: dict[str, list[str | None]] = {
    "ONE": [None],
    "THREE": ["Original for recipient", "Duplicate for transporter", "Triplicate for supplier"]}
ZERO = Decimal(0)


class ProfileIn(Strict):
    legal_name: str | None = Field(default=None, max_length=150)
    bank_details: str | None = Field(default=None, max_length=500)
    terms: str | None = Field(default=None, max_length=1500)
    signatory: str | None = Field(default=None, max_length=100)
    footer: str | None = Field(default=None, max_length=300)


class ProfileOut(ProfileIn, BaseModel):
    unit_id: UUID


@router.get("/org/units/{unit_id}/print-profile", response_model=ProfileOut)
async def get_profile(unit_id: UUID, ctx: Ctx = Depends(manage_org)) -> ProfileOut:
    row = await one(ctx, "SELECT u.unit_id, p.legal_name, p.bank_details, p.terms, p.signatory, "
                    "p.footer FROM erp.unit u LEFT JOIN erp.print_profile p ON p.unit_id = "
                    "u.unit_id WHERE u.unit_id = :id", {"id": unit_id})
    if row is None:
        raise not_found("Unit")
    return ProfileOut(**row)


@router.put("/org/units/{unit_id}/print-profile", response_model=ProfileOut)
async def set_profile(unit_id: UUID, body: ProfileIn, ctx: Ctx = Depends(manage_org)
                      ) -> ProfileOut:
    if await one(ctx, "SELECT 1 AS x FROM erp.unit WHERE unit_id = :id", {"id": unit_id}) is None:
        raise not_found("Unit")
    await one(
        ctx, "INSERT INTO erp.print_profile(tenant_id, unit_id, legal_name, bank_details, terms, "
        "signatory, footer) VALUES (erp.current_tenant_id(), :u, :n, :b, :t, :s, :f) ON CONFLICT "
        "(tenant_id, unit_id) DO UPDATE SET legal_name = :n, bank_details = :b, terms = :t, "
        "signatory = :s, footer = :f, updated_at = now() RETURNING print_profile_id",
        {"u": unit_id, "n": body.legal_name, "b": body.bank_details, "t": body.terms,
         "s": body.signatory, "f": body.footer})
    await audit(ctx, "unit.print_profile", "unit", unit_id, {})
    return await get_profile(unit_id, ctx)


def money(value: Decimal) -> str:
    return f"{value:,.2f}"


def qr_png(text: str) -> io.BytesIO:
    out = io.BytesIO()
    segno.make(text, error="l").save(out, kind="png", scale=3, border=1)
    out.seek(0)
    return out


class InvoicePdf(FPDF):
    def __init__(self) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_margins(12, 12, 12)
        self.set_auto_page_break(auto=True, margin=14)
        for style, suffix in (("", "Regular"), ("B", "Bold")):
            self.add_font("NotoSans", style, str(FONTS / f"NotoSans-{suffix}.ttf"))
            self.add_font("NotoDeva", style, str(FONTS / f"NotoSansDevanagari-{suffix}.ttf"))
        self.set_fallback_fonts(["NotoDeva"])
        self.set_text_shaping(True)

    def line_text(self, text: str, size: float = 9, bold: bool = False, align: str = "L") -> None:
        self.set_font("NotoSans", "B" if bold else "", size)
        self.multi_cell(0, 4.6, clean(text), align=align, new_x="LMARGIN", new_y="NEXT")


def draw(pdf: InvoicePdf, d: dict[str, Any], profile: dict[str, Any], tax: tuple[Decimal, ...],
         label: str | None, einv: dict[str, Any]) -> None:
    pdf.add_page()
    pdf.line_text(profile["legal_name"] or d["s_name"], 14, True)
    pdf.line_text(f"{d['s_addr'] or ''}, {d['s_city'] or ''} {d['s_pin'] or ''}\n"
                  f"GSTIN: {d['s_gstin'] or '-'}")
    pdf.ln(1)
    pdf.line_text("TAX INVOICE", 12, True, "C")
    if label:
        pdf.line_text(label, 9, False, "C")
    pdf.ln(1)
    top = pdf.get_y()
    pdf.line_text(f"Invoice no: {d['invoice_no']}\nDate: {d['issue_date']:%d-%m-%Y}\n"
                  f"Due: {d['due_date']:%d-%m-%Y}")
    if einv["irn"]:
        pdf.set_font("NotoSans", "", 7)
        pdf.multi_cell(150, 3.8, clean(f"IRN: {einv['irn']}\nAck no: {einv['irn_ack_no'] or ''}"
                       f"   Ack date: {einv['irn_ack_date']:%d-%m-%Y}" if einv["irn_ack_date"]
                       else f"IRN: {einv['irn']}"), new_x="LMARGIN", new_y="NEXT")
        if einv["signed_qr"]:
            pdf.image(qr_png(einv["signed_qr"]), x=160, y=top, w=36)
    if einv["ewb_no"]:
        pdf.line_text(f"E-way bill: {einv['ewb_no']}   Vehicle: {einv['ewb_vehicle_no'] or '-'}", 8)
    pdf.set_y(max(pdf.get_y(), top + 38))
    pdf.line_text("Bill to", 9, True)
    pdf.line_text(f"{d['b_name']}\n{d['b_addr'] or ''}, {d['b_city'] or ''} {d['b_pin'] or ''}\n"
                  f"GSTIN: {d['b_gstin'] or 'Unregistered'}")
    pdf.ln(2)
    widths = (8, 62, 20, 16, 24, 28, 28)
    heads = ("#", "Item", "HSN", "Qty", "Rate", "Taxable", "Total")
    pdf.set_font("NotoSans", "B", 8)
    for w, h in zip(widths, heads, strict=True):
        pdf.cell(w, 6, h, border=1, align="C")
    pdf.ln()
    pdf.set_font("NotoSans", "", 8)
    for n, ln in enumerate(d["lines"], start=1):
        row = (str(n), clean(ln["name"])[:38], ln["hsn_code"] or "", f"{ln['quantity']:g}",
               money(ln["unit_price"]), money(ln["taxable_amount"]),
               money(ln["taxable_amount"] + ln["tax_amount"]))
        for w, v, align in zip(widths, row, "CLLRRRR", strict=True):
            pdf.cell(w, 6, v, border=1, align=align)
        pdf.ln()
    cgst, sgst, igst = tax
    pdf.ln(2)
    for name, value in (("Taxable value", d["taxable_amount"]), ("CGST", cgst), ("SGST", sgst),
                        ("IGST", igst), ("Invoice total", d["taxable_amount"] + d["tax_amount"])):
        if value or name in ("Taxable value", "Invoice total"):
            pdf.set_font("NotoSans", "B" if name == "Invoice total" else "", 9)
            pdf.cell(130, 5.5, "")
            pdf.cell(32, 5.5, name)
            pdf.cell(32, 5.5, money(value), align="R", new_x="LMARGIN", new_y="NEXT")
    for title, text in (("Bank details", profile["bank_details"]), ("Terms", profile["terms"])):
        if text:
            pdf.ln(2)
            pdf.line_text(title, 8, True)
            pdf.line_text(text, 8)
    if profile["signatory"]:
        pdf.ln(8)
        pdf.line_text(f"For {profile['legal_name'] or d['s_name']}\n\n{profile['signatory']}", 9,
                      False, "R")
    if profile["footer"]:
        pdf.ln(3)
        pdf.line_text(profile["footer"], 7, False, "C")


@router.get("/sales/invoices/{invoice_id}/pdf")
async def invoice_pdf(invoice_id: UUID, ctx: Ctx = Depends(read)) -> Response:
    clause, params = scope_clause(ctx, "unit_id")
    einv = await one(
        ctx, "SELECT unit_id, irn, irn_ack_no, irn_ack_date, signed_qr, ewb_no, ewb_vehicle_no, "
        "due_date FROM erp.customer_invoice WHERE invoice_id = :id"
        f"{' AND ' + clause if clause else ''}", {**params, "id": invoice_id})
    if einv is None:
        raise not_found("Invoice")
    try:
        d = await invoice_data(ctx, invoice_id)
    except HTTPException as exc:  # a cancelled invoice is not printed as a tax invoice
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc.detail)) from exc
    d["due_date"] = einv["due_date"]
    profile = await one(ctx, "SELECT legal_name, bank_details, terms, signatory, footer FROM "
                        "erp.print_profile WHERE unit_id = :u", {"u": einv["unit_id"]}) or {
        "legal_name": None, "bank_details": None, "terms": None, "signatory": None,
        "footer": None}
    tax = (split(d, d["tax_amount"]) if d["s_gstin"] and d["b_gstin"]
           else (ZERO, ZERO, d["tax_amount"]))
    pdf = InvoicePdf()
    copies = COPIES[str(await rules.get_value(ctx, "invoiceCopies"))]
    for label in copies:
        draw(pdf, d, profile, tax, label, einv)
    await audit(ctx, "invoice.print", "invoice", invoice_id, {"invoice_no": d["invoice_no"]})
    return Response(bytes(pdf.output()), media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{d["invoice_no"]}.pdf"'})
