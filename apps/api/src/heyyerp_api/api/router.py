from fastapi import APIRouter

from heyyerp_api.api.health import router as health_router
from heyyerp_api.api.me import router as me_router
from heyyerp_api.masters.org import router as org_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(me_router)
api_router.include_router(org_router)
