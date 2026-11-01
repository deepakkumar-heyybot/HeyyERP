"""A report as a printable PDF: landscape A4, the title and filters on top, a table whose header
repeats on every page, numbers aligned right, and "Page x of y" at the foot.

The standard PDF fonts only draw Western letters, so any other character is shown as "?" rather
than as a broken box. Text is escaped before it is laid out, so a name can never inject markup.
"""

import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from heyyerp_api.reports.registry import Column

COMPANY = "heyyERP · V-Rub Suspensions"
PAGE = landscape(A4)
MARGIN = 12 * mm


def latin(text: str) -> str:
    """The text with anything the standard fonts cannot draw replaced by a question mark."""
    return text.encode("cp1252", errors="replace").decode("cp1252")


def cell_text(value: Any, kind: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, Decimal) or isinstance(value, int | float) and kind in ("money", "number"):
        number = Decimal(str(value))
        return f"{number:,.2f}" if kind == "money" else f"{number.normalize():,f}"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


class Numbered(canvas.Canvas):  # type: ignore[misc]
    """A canvas that knows the page count, so every page can say "Page x of y"."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pages: list[dict[str, Any]] = []

    def showPage(self) -> None:  # noqa: N802 (reportlab's name)
        self.pages.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self.pages)
        for state in self.pages:
            self.__dict__.update(state)
            self.setFont("Helvetica", 7)
            self.setFillColor(colors.grey)
            self.drawString(MARGIN, 7 * mm, latin(COMPANY))
            self.drawRightString(PAGE[0] - MARGIN, 7 * mm, f"Page {self._pageNumber} of {total}")
            super().showPage()
        super().save()


def widths(columns: tuple[Column, ...], data: list[dict[str, Any]], room: float) -> list[float]:
    """Column widths in proportion to how much each column needs, within sensible limits."""
    need = []
    for c in columns:
        longest = max([len(c.label)] + [len(cell_text(r.get(c.key), c.kind)) for r in data[:200]])
        need.append(min(max(longest, 5), 30))
    scale = room / sum(need)
    return [n * scale for n in need]


def to_pdf(title: str, filters: dict[str, str], columns: tuple[Column, ...],
           data: list[dict[str, Any]], generated: datetime) -> bytes:
    out = io.BytesIO()
    doc = SimpleDocTemplate(
        out, pagesize=PAGE, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN,
        bottomMargin=14 * mm, title=latin(title), author=latin(COMPANY))
    heading = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=14, leading=17)
    small = ParagraphStyle("s", fontName="Helvetica", fontSize=8, leading=10, textColor=colors.grey)
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=7, leading=8.5)
    right = ParagraphStyle("r", parent=body, alignment=2)
    head = ParagraphStyle("t", parent=body, fontName="Helvetica-Bold", textColor=colors.white)
    head_right = ParagraphStyle("tr", parent=head, alignment=2)
    shown = ", ".join(f"{k} = {v}" for k, v in filters.items()) or "none"
    story: list[Any] = [
        Paragraph(escape(latin(title)), heading),
        Paragraph(escape(latin(f"Filters: {shown}  |  {len(data)} row(s)  |  "
                               f"Generated {generated:%Y-%m-%d %H:%M} UTC")), small),
        Spacer(1, 4 * mm)]
    numeric = {c.key for c in columns if c.kind in ("money", "number")}
    table_data: list[list[Any]] = [[
        Paragraph(escape(latin(c.label)), head_right if c.key in numeric else head)
        for c in columns]]
    for row in data:
        table_data.append([
            Paragraph(escape(latin(cell_text(row.get(c.key), c.kind))),
                      right if c.key in numeric else body) for c in columns])
    if not data:
        table_data.append([Paragraph("No rows", body)] + [""] * (len(columns) - 1))
    table = Table(table_data, colWidths=widths(columns, data, PAGE[0] - 2 * MARGIN),
                  repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f6f5c")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f6f5")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#d5dedb")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story.append(table)
    doc.build(story, canvasmaker=Numbered)
    return out.getvalue()
