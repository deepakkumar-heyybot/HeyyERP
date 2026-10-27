from fastapi import APIRouter

from heyyerp_api.admin.users import router as admin_users_router
from heyyerp_api.api.health import router as health_router
from heyyerp_api.api.me import router as me_router
from heyyerp_api.masters.org import router as org_router
from heyyerp_api.masters.parties import router as parties_router
from heyyerp_api.masters.reference import router as reference_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(me_router)
api_router.include_router(org_router)
api_router.include_router(reference_router)
api_router.include_router(parties_router)
api_router.include_router(admin_users_router)
