"""Photos and documents attached to a record (kaizen before and after, a breakdown's damage).

A file is sent as base64 in a JSON body and kept in the database against the record. Only images
(PNG, JPEG, WebP) and PDFs are accepted, the content is checked against the file's own signature and
the size against the company rule `attachmentMaxKb`. A record carries at most 10 files. Whoever can
change the record may attach files to it (a kaizen's submitter or an evaluator; maintenance or the
reporter of a breakdown); whoever can read it can see and download them. A file is removed
by switching it off (by whoever uploaded it or a person with the record's manage permission),
never deleted. Downloads are sent as attachments and never sniffed as another type.
"""

import base64
import binascii
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from heyyerp_api.business_rules import service as rules
from heyyerp_api.masters.common import Ctx, Strict, audit, not_found, one, requires, rows
from heyyerp_api.security import has_permission

router = APIRouter(prefix="/attachments", tags=["attachments"])
EntityType = Literal["kaizen", "breakdown"]
SIGNATURES = {"image/png": (b"\x89PNG\r\n\x1a\n",), "image/jpeg": (b"\xff\xd8\xff",),
              "image/webp": (b"RIFF",), "application/pdf": (b"%PDF-",)}
MAX_FILES = 10
signed_in = requires("kaizen.submit")  # every role holds it; the record's own rules do the rest


class AttachmentIn(Strict):
    entity_type: EntityType
    entity_id: UUID
    file_name: str = Field(min_length=1, max_length=150, pattern=r"^[^/\\\x00]+$")
    content_type: Literal["image/png", "image/jpeg", "image/webp", "application/pdf"]
    content_base64: str = Field(min_length=4)


class AttachmentOut(BaseModel):
    attachment_id: UUID
    entity_type: str
    entity_id: UUID
    file_name: str
    content_type: str
    size_bytes: int
    uploaded_by_name: str | None
    uploaded_at: Any


META_SQL = (
    "SELECT a.attachment_id, a.entity_type, a.entity_id, a.file_name, a.content_type, "
    "a.size_bytes, u.display_name AS uploaded_by_name, a.uploaded_at FROM erp.attachment a "
    "LEFT JOIN erp.app_user u ON u.user_id = a.uploaded_by"
)


async def record_access(ctx: Ctx, kind: str, entity_id: UUID, *, change: bool) -> None:
    """Refuse (404 or 403) unless the user may see the record, or may change it when asked."""
    mine = ctx.principal.profile.app_user_id
    if kind == "kaizen":
        row = await one(ctx, "SELECT unit_id, submitter_id FROM erp.kaizen WHERE kaizen_id = :id",
                        {"id": entity_id})
        if row is None or not ctx.principal.may_see_unit(row["unit_id"]):
            raise not_found("Kaizen")
        if change and not (row["submitter_id"] == mine
                           or has_permission(ctx.principal, "kaizen.manage")):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Only the submitter or an evaluator attaches files to a kaizen")
    else:
        row = await one(ctx, "SELECT unit_id, reported_by FROM erp.breakdown WHERE breakdown_id = "
                        ":id", {"id": entity_id})
        if row is None or not ctx.principal.may_see_unit(row["unit_id"]):
            raise not_found("Breakdown")
        if change and not (row["reported_by"] == mine or has_permission(ctx.principal, "mnt")):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Only maintenance or the reporter attaches files to a breakdown")


@router.post("", response_model=AttachmentOut, status_code=status.HTTP_201_CREATED)
async def upload(body: AttachmentIn, ctx: Ctx = Depends(signed_in)) -> AttachmentOut:
    await record_access(ctx, body.entity_type, body.entity_id, change=True)
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, "The file is not valid base64") from exc
    limit = int(await rules.get_value(ctx, "attachmentMaxKb")) * 1024
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"The file is larger than the {limit // 1024} KB the company allows")
    if not data.startswith(SIGNATURES[body.content_type]) or (
            body.content_type == "image/webp" and data[8:12] != b"WEBP"):
        raise HTTPException(422, "The file does not look like the type it says it is")
    held = await one(ctx, "SELECT count(*) AS n FROM erp.attachment WHERE entity_type = :t AND "
                     "entity_id = :id AND active", {"t": body.entity_type, "id": body.entity_id})
    if held is not None and held["n"] >= MAX_FILES:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"A record carries at most {MAX_FILES} files")
    created = await one(
        ctx, "INSERT INTO erp.attachment(tenant_id, entity_type, entity_id, file_name, "
        "content_type, size_bytes, content, uploaded_by) VALUES (erp.current_tenant_id(), :t, :id, "
        ":n, :c, :s, :d, :me) RETURNING attachment_id",
        {"t": body.entity_type, "id": body.entity_id, "n": body.file_name,
         "c": body.content_type, "s": len(data), "d": data,
         "me": ctx.principal.profile.app_user_id})
    assert created is not None
    await audit(ctx, "attachment.add", body.entity_type, body.entity_id,
                {"attachment_id": created["attachment_id"], "file": body.file_name,
                 "bytes": len(data)})
    row = await one(ctx, f"{META_SQL} WHERE a.attachment_id = :id",
                    {"id": created["attachment_id"]})
    assert row is not None
    return AttachmentOut(**row)


@router.get("", response_model=list[AttachmentOut])
async def list_files(
    entity_type: EntityType = Query(...), entity_id: UUID = Query(...),
    ctx: Ctx = Depends(signed_in),
) -> list[AttachmentOut]:
    await record_access(ctx, entity_type, entity_id, change=False)
    data = await rows(ctx, f"{META_SQL} WHERE a.entity_type = :t AND a.entity_id = :id AND "
                      "a.active ORDER BY a.uploaded_at", {"t": entity_type, "id": entity_id})
    return [AttachmentOut(**d) for d in data]


@router.get("/{attachment_id}/content")
async def download(attachment_id: UUID, ctx: Ctx = Depends(signed_in)) -> Response:
    row = await one(ctx, "SELECT entity_type, entity_id, file_name, content_type, content FROM "
                    "erp.attachment WHERE attachment_id = :id AND active", {"id": attachment_id})
    if row is None:
        raise not_found("File")
    await record_access(ctx, row["entity_type"], row["entity_id"], change=False)
    return Response(
        content=bytes(row["content"]), media_type=row["content_type"],
        headers={"Content-Disposition": f'attachment; filename="{row["file_name"]}"',
                 "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.post("/{attachment_id}/remove", response_model=AttachmentOut)
async def remove(attachment_id: UUID, ctx: Ctx = Depends(signed_in)) -> AttachmentOut:
    row = await one(ctx, f"{META_SQL} WHERE a.attachment_id = :id AND a.active",
                    {"id": attachment_id})
    if row is None:
        raise not_found("File")
    await record_access(ctx, row["entity_type"], row["entity_id"], change=True)
    mine = await one(ctx, "SELECT uploaded_by FROM erp.attachment WHERE attachment_id = :id",
                     {"id": attachment_id})
    manager = has_permission(ctx.principal, "kaizen.manage" if row["entity_type"] == "kaizen"
                             else "mnt")
    assert mine is not None
    if mine["uploaded_by"] != ctx.principal.profile.app_user_id and not manager:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Only the uploader or the record's manager removes a file")
    await rows(ctx, "UPDATE erp.attachment SET active = false WHERE attachment_id = :id RETURNING "
               "attachment_id", {"id": attachment_id})
    await audit(ctx, "attachment.remove", row["entity_type"], row["entity_id"],
                {"attachment_id": attachment_id})
    return AttachmentOut(**row)
