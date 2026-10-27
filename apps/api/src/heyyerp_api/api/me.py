from fastapi import APIRouter, Depends
from pydantic import BaseModel

from heyyerp_api.security import Principal, authenticate

router = APIRouter(tags=["session"])


class MeResponse(BaseModel):
    user_id: str
    email: str
    roles: list[str]
    mfa_required: bool
    two_factor_enabled: bool
    access_granted: bool


@router.get("/me", response_model=MeResponse)
async def me(principal: Principal = Depends(authenticate)) -> MeResponse:
    """Who the caller is and whether they still need to enrol in two-factor auth."""
    return MeResponse(
        user_id=principal.user_id,
        email=principal.email,
        roles=list(principal.roles),
        mfa_required=principal.mfa_required,
        two_factor_enabled=principal.two_factor_enabled,
        access_granted=not principal.mfa_required or principal.two_factor_enabled,
    )
