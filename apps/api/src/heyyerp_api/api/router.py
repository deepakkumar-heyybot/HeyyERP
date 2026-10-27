from fastapi import APIRouter

from heyyerp_api.admin.audit import router as admin_audit_router
from heyyerp_api.admin.users import router as admin_users_router
from heyyerp_api.api.health import router as health_router
from heyyerp_api.api.me import router as me_router
from heyyerp_api.business_rules.api import router as business_rules_router
from heyyerp_api.inventory.counts import router as counts_router
from heyyerp_api.inventory.grn import router as grn_router
from heyyerp_api.inventory.issues import router as issues_router
from heyyerp_api.inventory.stock import router as stock_router
from heyyerp_api.inventory.transfers import router as transfers_router
from heyyerp_api.manufacturing.batches import router as batches_router
from heyyerp_api.manufacturing.compounds import router as compounds_router
from heyyerp_api.manufacturing.equipment import router as equipment_router
from heyyerp_api.manufacturing.orders import router as work_orders_router
from heyyerp_api.masters.org import router as org_router
from heyyerp_api.masters.parties import router as parties_router
from heyyerp_api.masters.reference import router as reference_router
from heyyerp_api.procurement.indents import router as indents_router
from heyyerp_api.procurement.orders import router as orders_router
from heyyerp_api.procurement.rfq import router as rfq_router
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
api_router.include_router(indents_router)
api_router.include_router(rfq_router)
api_router.include_router(orders_router)
api_router.include_router(grn_router)
api_router.include_router(stock_router)
api_router.include_router(issues_router)
api_router.include_router(transfers_router)
api_router.include_router(counts_router)
api_router.include_router(compounds_router)
api_router.include_router(batches_router)
api_router.include_router(equipment_router)
api_router.include_router(work_orders_router)
