from fastapi import APIRouter

from heyyerp_api.admin.audit import router as admin_audit_router
from heyyerp_api.admin.users import router as admin_users_router
from heyyerp_api.api.health import router as health_router
from heyyerp_api.api.me import router as me_router
from heyyerp_api.business_rules.api import router as business_rules_router
from heyyerp_api.masters.org import router as org_router
from heyyerp_api.masters.parties import router as parties_router
from heyyerp_api.masters.reference import router as reference_router
from heyyerp_api.product.bom import router as bom_router
from heyyerp_api.product.items import router as items_router
from heyyerp_api.product.routes import router as routes_router
from heyyerp_api.workflow.api import router as approvals_router
from heyyerp_api.workflow.rules import router as approval_rules_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(me_router)
api_router.include_router(org_router)
api_router.include_router(reference_router)
api_router.include_router(parties_router)
api_router.include_router(admin_users_router)
api_router.include_router(admin_audit_router)
api_router.include_router(approvals_router)
api_router.include_router(approval_rules_router)
api_router.include_router(business_rules_router)
api_router.include_router(items_router)
api_router.include_router(routes_router)
api_router.include_router(bom_router)
