"""Approval adapters for the document types that exist today. Importing registers them."""

from uuid import UUID

from heyyerp_api.masters.common import Ctx, one
from heyyerp_api.workflow.registry import DocInfo, DocumentType, register


async def load_vendor(ctx: Ctx, vendor_id: UUID) -> DocInfo | None:
    vendor = await one(
        ctx, "SELECT code, name, status FROM erp.vendor WHERE vendor_id = :id", {"id": vendor_id}
    )
    if vendor is None:
        return None
    return DocInfo(
        title=f"Vendor {vendor['code']} - {vendor['name']}",
        amount=None,
        department_id=None,
        submittable=vendor["status"] == "PENDING_APPROVAL",
        reason="Vendor is " + str(vendor["status"]).lower().replace("_", " ")
        + ", not awaiting approval",
    )


async def finalize_vendor(ctx: Ctx, vendor_id: UUID, approved: bool) -> None:
    await one(
        ctx,
        "UPDATE erp.vendor SET status = :status WHERE vendor_id = :id RETURNING vendor_id",
        {"status": "APPROVED" if approved else "REJECTED", "id": vendor_id},
    )


register(DocumentType("vendor", "vendor.manage", load_vendor, finalize_vendor))
