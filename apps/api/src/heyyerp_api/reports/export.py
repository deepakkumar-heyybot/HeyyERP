"""Turning report rows into CSV and Excel files.

Text that starts with a spreadsheet formula character (= + - @, a tab or a carriage return) is sent
with a leading apostrophe so a customer or item name can never run as a formula when the file is
opened. Numbers and dates keep their own types in the Excel file.
"""

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font

from heyyerp_api.reports.registry import Column

DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def safe(value: Any) -> Any:
    """The value as it is written to a file: formula-looking text is defused."""
    if isinstance(value, str) and value.startswith(DANGEROUS):
        return "'" + value
    return value


def plain(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(safe(value))


def to_csv(columns: tuple[Column, ...], data: list[dict[str, Any]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([safe(c.label) for c in columns])
    for row in data:
        writer.writerow([plain(row.get(c.key)) for c in columns])
    return ("﻿" + out.getvalue()).encode("utf-8")  # the mark makes Excel read it as UTF-8


def to_xlsx(title: str, columns: tuple[Column, ...], data: list[dict[str, Any]]) -> bytes:
    book = Workbook(write_only=True)
    sheet = book.create_sheet(title[:31])
    head = []
    for c in columns:
        cell = WriteOnlyCell(sheet, value=safe(c.label))
        cell.font = Font(bold=True)
        head.append(cell)
    sheet.append(head)
    for row in data:
        cells: list[Any] = []
        for c in columns:
            value = row.get(c.key)
            if isinstance(value, Decimal):
                value = float(value)
            elif isinstance(value, datetime) and value.tzinfo is not None:
                value = value.replace(tzinfo=None)
            elif isinstance(value, str):
                value = safe(value)
            cell = WriteOnlyCell(sheet, value=value)
            if c.kind == "money":
                cell.number_format = "#,##0.00"
            elif isinstance(value, date) and not isinstance(value, datetime):
                cell.number_format = "yyyy-mm-dd"
            cells.append(cell)
        sheet.append(cells)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
