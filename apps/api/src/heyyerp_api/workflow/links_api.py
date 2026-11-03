"""The public pages behind an approval link: look at the document, then approve or reject it.

No sign-in: the secret in the link is the credential. Looking (GET) changes nothing; deciding is a
POST. Everything else about the decision is the same as in the application.
"""

from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from heyyerp_api import db
from heyyerp_api.masters.common import Ctx, Strict
from heyyerp_api.security import mapped_tenant
from heyyerp_api.workflow import links

router = APIRouter(prefix="/public/approval-links", tags=["approval links"])


async def link_ctx() -> AsyncIterator[Ctx]:
    async with db.SessionFactory() as session, session.begin():
        await session.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                              {"t": str(mapped_tenant())})
        yield Ctx(session, None)  # type: ignore[arg-type]  # nobody is signed in


class LinkOut(BaseModel):
    title: str | None
    document_type: str
    amount: Decimal | None
    step_label: str | None
    requested_by: str | None
    expires_at: datetime


class DecisionIn(Strict):
    decision: Literal["APPROVE", "REJECT"]
    note: str | None = Field(default=None, max_length=500)


class DecisionOut(BaseModel):
    status: str


@router.get("/{token}", response_model=LinkOut)
async def look(token: str, ctx: Ctx = Depends(link_ctx)) -> LinkOut:
    return LinkOut(**await links.find(ctx, token))


@router.post("/{token}/decide", response_model=DecisionOut)
async def decide(token: str, body: DecisionIn, ctx: Ctx = Depends(link_ctx)) -> DecisionOut:
    return DecisionOut(status=await links.decide(ctx, token, body.decision, body.note))


