"""Document numbers such as IND-26-0001 or PO-26-0861 (BRD section 6.1: numbering style).

The number is the financial year (April to March) in two digits and a running count that starts
again each year. It is taken inside the caller's transaction, so concurrent requests get
different numbers and a rolled-back request leaves no gap.
"""

from datetime import date

from heyyerp_api.masters.common import Ctx, one

PREFIXES = {"indent": "IND", "rfq": "RFQ", "po": "PO", "grn": "GRN", "issue": "ISS",
            "transfer": "TRF", "count": "CNT", "return": "RET"}


def fy_label(day: date) -> str:
    """Two-digit label of the financial year a date falls in (April 2026 to March 2027 is 26)."""
    return f"{(day.year if day.month >= 4 else day.year - 1) % 100:02d}"


async def next_number(ctx: Ctx, doc_type: str, on: date | None = None) -> str:
    label = fy_label(on or date.today())
    row = await one(
        ctx,
        "INSERT INTO erp.document_sequence(tenant_id, doc_type, fy_label, last_no) "
        "VALUES (erp.current_tenant_id(), :type, :fy, 1) "
        "ON CONFLICT (tenant_id, doc_type, fy_label) DO UPDATE "
        "SET last_no = erp.document_sequence.last_no + 1 RETURNING last_no",
        {"type": doc_type, "fy": label},
    )
    assert row is not None
    return f"{PREFIXES[doc_type]}-{label}-{row['last_no']:04d}"
