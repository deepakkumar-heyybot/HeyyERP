"""A report as a printable PDF: landscape A4, the title and filters on top, a table whose header
repeats on every page, numbers aligned right, and "Page x of y" at the foot.

Text is laid out with a shaping engine (HarfBuzz), so Hindi (Devanagari) and English print correctly
together: Noto Sans for Latin text and Noto Sans Devanagari for the rest, both bundled in
`reports/fonts` (SIL Open Font License, see OFL.txt there). A character that neither font has is
drawn as a replacement mark by the library, never as markup: nothing in the data is interpreted.
"""

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fpdf import FPDF
from fpdf.enums import TableCellFillMode
from fpdf.fonts import FontFace

from heyyerp_api.reports.registry import Column

COMPANY = "heyyERP · V-Rub Suspensions"
FONTS = Path(__file__).parent / "fonts"
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
GREEN = (31, 111, 92)


def clean(text: str) -> str:
    """The text without control characters, which have no place in a printed cell."""
    return CONTROL.sub("", text)


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
    return clean(str(value))


class ReportPdf(FPDF):
    """Landscape A4 with the company name and "Page x of y" in the footer."""

    def __init__(self) -> None:
        super().__init__(orientation="L", unit="mm", format="A4")
        self.set_margins(12, 12, 12)
        self.set_auto_page_break(auto=True, margin=14)
        for style, suffix in (("", "Regular"), ("B", "Bold")):
            self.add_font("NotoSans", style, str(FONTS / f"NotoSans-{suffix}.ttf"))
            self.add_font("NotoDeva", style, str(FONTS / f"NotoSansDevanagari-{suffix}.ttf"))
        self.set_fallback_fonts(["NotoDeva"])
        self.set_text_shaping(True)
        self.alias_nb_pages()

    def footer(self) -> None:
        self.set_y(-9)
        self.set_font("NotoSans", "", 7)
        self.set_text_color(128, 128, 128)
        self.cell(0, 4, COMPANY, align="L")
        self.set_x(-60)
        self.cell(48, 4, f"Page {self.page_no()} of {{nb}}", align="R")


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
    pdf = ReportPdf()
    pdf.set_title(clean(title))
    pdf.set_author(COMPANY)
    pdf.add_page()
    pdf.set_font("NotoSans", "B", 14)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 8, clean(title), new_x="LMARGIN", new_y="NEXT")
    shown = ", ".join(f"{k} = {clean(v)}" for k, v in filters.items()) or "none"
    pdf.set_font("NotoSans", "", 8)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(0, 5, f"Filters: {shown}  |  {len(data)} row(s)  |  "
                   f"Generated {generated:%Y-%m-%d %H:%M} UTC", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("NotoSans", "", 7)
    numeric = {c.key for c in columns if c.kind in ("money", "number")}
    with pdf.table(
            col_widths=tuple(widths(columns, data, pdf.epw)),
            text_align=tuple("RIGHT" if c.key in numeric else "LEFT" for c in columns),
            headings_style=FontFace(emphasis="BOLD", color=(255, 255, 255), fill_color=GREEN),
            borders_layout="HORIZONTAL_LINES", line_height=4.2,
            cell_fill_color=(242, 246, 245), cell_fill_mode=TableCellFillMode.ROWS) as table:
        head = table.row()
        for c in columns:
            head.cell(clean(c.label))
        for row in data:
            line = table.row()
            for c in columns:
                line.cell(cell_text(row.get(c.key), c.kind))
        if not data:
            table.row().cell("No rows")
    return bytes(pdf.output())
