from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from heyyerp_api.security import Principal, authenticate

router = APIRouter(tags=["session"])


class MeResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: str
    roles: list[str]  # kept for older clients; always the single ERP role
    permissions: list[str]
    modules: list[str]
    all_plants: bool
    unit_ids: list[UUID]
    mfa_required: bool
    two_factor_enabled: bool
    access_granted: bool


@router.get("/me", response_model=MeResponse)
async def me(principal: Principal = Depends(authenticate)) -> MeResponse:
    """Who the caller is, what they may do, and whether they still need to enrol in 2FA."""
    profile = principal.profile
    return MeResponse(
        user_id=principal.user_id,
        email=principal.email,
        display_name=profile.display_name,
        role=profile.role,
        roles=[profile.role],
        permissions=sorted(profile.permissions),
        modules=sorted(profile.modules),
        all_plants=profile.unit_ids is None,
        unit_ids=sorted(profile.unit_ids or []),
        mfa_required=principal.mfa_required,
        two_factor_enabled=principal.two_factor_enabled,
        access_granted=not principal.mfa_required or principal.two_factor_enabled,
    )
