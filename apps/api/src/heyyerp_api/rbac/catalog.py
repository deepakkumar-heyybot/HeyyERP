"""ERP roles, permissions and module access (BRD section 3, prototype v4 ROLES / ACTPERM).

Pure data, no database access. `rbac.seed` writes it into the tenant's role tables; the
database is the source of truth at runtime. Seeding only adds, so an admin's later edits survive.
"""

from typing import Any

# permission code -> description. Codes marked (P2) are the master-data permissions added in
# Phase 2; the rest follow the BRD permission table.
PERMISSIONS: dict[str, str] = {
    "org.manage": "Maintain plants, departments and stores (P2)",
    "refdata.manage": "Maintain payment terms, tax codes and item categories (P2)",
    "customer.manage": "Maintain customers (P2)",
    "vendor.manage": "Onboard and maintain vendors",
    "indent.raise": "Raise purchase indents",
    "po.create": "Create purchase orders",
    "cash.purchase": "Record cash purchases",
    "grn.post": "Post goods receipts",
    "inv.issue": "Issue stock to production",
    "inv.transfer": "Transfer stock between stores and plants",
    "inv.count": "Run and submit stock counts",
    "sales.order": "Create sales orders and import customer schedules",
    "sales.quote": "Create customer quotations",
    "sales.dispatch": "Dispatch and invoice",
    "challan": "Raise returnable and job-work challans",
    "scrap.sale": "Raise scrap and old-asset sales",
    "mfg.plan": "Plan production and run MRP",
    "mfg.entry": "Enter shift production",
    "route.move": "Move lots through production routes",
    "qa.approve": "Approve quality gates, GRN lots and compound batches",
    "ncr": "Raise and progress non-conformance reports",
    "mnt": "Run preventive maintenance and close breakdowns",
    "mnt.breakdown": "Report breakdowns",
    "asset.manage": "Maintain the fixed asset register and run depreciation",
    "fin.voucher": "Post vouchers and expenses",
    "fin.bill": "Book supplier bills",
    "fin.pay": "Run payments",
    "gst": "Prepare GST returns, e-invoices and e-way bills",
    "hr.manage": "Maintain employees and recruitment",
    "payroll": "Process payroll",
    "kaizen.submit": "Submit kaizen suggestions",
    "kaizen.manage": "Evaluate kaizen suggestions",
    "leave.apply": "Apply for leave",
    "ai": "Use the AI copilot and agents",
    "approval.view_all": "See every pending approval, not only your own",
    "approval.override": "Decide a step on behalf of its approver (logged as an override)",
    "admin": "Administer users, roles, approval rules and business settings",
}

ALL_MODULES = [
    "home", "approvals", "ai", "sales", "procurement", "inventory", "routes", "manufacturing",
    "quality", "maintenance", "finance", "gst", "hr", "kaizen", "admin", "integrations",
    "reports", "golive", "benchmark",
]

EVERYTHING = "*"

# role code -> (display name, modules or "*", permissions or "*", "*" minus "admin" for MD)
ROLES: dict[str, dict[str, Any]] = {
    "ADMIN": {"name": "System admin", "modules": EVERYTHING, "permissions": EVERYTHING},
    "MD": {"name": "Managing Director", "modules": EVERYTHING,
           "permissions": EVERYTHING,
           # BRD: the MD cannot change admin settings, and only ADMIN overrides an approval step
           "except": {"admin", "approval.override"}},
    "PLANT_HEAD": {
        "name": "Plant Head",
        "modules": ["home", "approvals", "ai", "routes", "sales", "procurement", "inventory",
                    "manufacturing", "quality", "maintenance", "finance", "gst", "hr", "kaizen",
                    "admin", "reports"],
        "permissions": ["indent.raise", "po.create", "mfg.plan", "mfg.entry", "route.move",
                        "inv.count", "inv.issue", "kaizen.submit", "kaizen.manage", "leave.apply",
                        "scrap.sale", "mnt", "mnt.breakdown", "ai", "approval.view_all",
                        "refdata.manage", "customer.manage"],
    },
    "PURCHASE": {
        "name": "Purchase Manager",
        "modules": ["home", "approvals", "ai", "procurement", "inventory", "routes", "reports",
                    "kaizen"],
        "permissions": ["indent.raise", "po.create", "vendor.manage", "cash.purchase",
                        "kaizen.submit", "leave.apply", "ai"],
    },
    "STORES": {
        "name": "Stores In-charge",
        "modules": ["home", "approvals", "procurement", "inventory", "routes", "reports",
                    "kaizen"],
        "permissions": ["indent.raise", "grn.post", "inv.issue", "inv.transfer", "inv.count",
                        "challan", "cash.purchase", "kaizen.submit", "leave.apply"],
    },
    "QA": {
        "name": "Quality Head",
        "modules": ["home", "approvals", "quality", "routes", "inventory", "manufacturing",
                    "procurement", "reports", "kaizen"],
        "permissions": ["qa.approve", "ncr", "indent.raise", "kaizen.submit", "leave.apply"],
    },
    "PROD": {
        "name": "Production Manager",
        "modules": ["home", "approvals", "ai", "routes", "manufacturing", "inventory", "quality",
                    "maintenance", "kaizen", "reports"],
        "permissions": ["mfg.plan", "mfg.entry", "route.move", "indent.raise", "inv.issue",
                        "kaizen.submit", "leave.apply", "mnt.breakdown", "ai"],
    },
    "SALES": {
        "name": "Sales & Dispatch",
        "modules": ["home", "approvals", "ai", "sales", "inventory", "reports", "kaizen"],
        "permissions": ["sales.order", "sales.quote", "sales.dispatch", "challan", "scrap.sale",
                        "kaizen.submit", "leave.apply", "ai", "customer.manage"],
    },
    "ACCOUNTS": {
        "name": "Finance Controller",
        "modules": ["home", "approvals", "ai", "finance", "gst", "procurement", "sales",
                    "inventory", "maintenance", "reports", "kaizen"],
        "permissions": ["fin.voucher", "fin.bill", "fin.pay", "gst", "asset.manage",
                        "kaizen.submit", "leave.apply", "ai", "refdata.manage"],
    },
    "HR": {
        "name": "HR Manager",
        "modules": ["home", "approvals", "hr", "kaizen", "reports"],
        "permissions": ["hr.manage", "payroll", "kaizen.submit", "kaizen.manage", "leave.apply"],
    },
    "MAINT": {
        "name": "Maintenance Engineer",
        "modules": ["home", "approvals", "maintenance", "inventory", "routes", "kaizen"],
        "permissions": ["mnt", "mnt.breakdown", "indent.raise", "kaizen.submit", "leave.apply"],
    },
    "OPERATOR": {
        "name": "Shop-floor Operator",
        "modules": ["routes", "manufacturing", "kaizen"],
        "permissions": ["mfg.entry", "route.move", "kaizen.submit", "mnt.breakdown",
                        "leave.apply"],
    },
}


def role_permissions(role_code: str) -> set[str]:
    spec = ROLES[role_code]
    granted = set(PERMISSIONS) if spec["permissions"] == EVERYTHING else set(spec["permissions"])
    return granted - set(spec.get("except", set()))


def role_modules(role_code: str) -> set[str]:
    spec = ROLES[role_code]
    return set(ALL_MODULES) if spec["modules"] == EVERYTHING else set(spec["modules"])


# Default approval matrix (BRD section 5). Proposal only: V-Rub replaces it with workflow
# sheet 3. Steps: a role code, or "DEPT_HEAD" for the requesting department's head role.
# Bands: first rule by priority whose [amount_min, amount_max] holds the amount wins, so the
# shared boundary value falls in the lower band.
LAKH = 100_000
DEFAULT_RULES: list[dict[str, Any]] = [
    {"name": "R-IND", "document_type": "indent", "priority": 10, "sla_hours": 24,
     "steps": ["DEPT_HEAD", "PLANT_HEAD"]},
    {"name": "R-PO-1", "document_type": "po", "priority": 10, "amount_max": 5 * LAKH,
     "sla_hours": 24, "steps": ["PURCHASE"]},
    {"name": "R-PO-2", "document_type": "po", "priority": 20, "amount_max": 25 * LAKH,
     "sla_hours": 24, "steps": ["PURCHASE", "PLANT_HEAD"]},
    {"name": "R-PO-3", "document_type": "po", "priority": 30, "sla_hours": 24,
     "steps": ["PURCHASE", "PLANT_HEAD", "MD"]},
    {"name": "R-BILL", "document_type": "bill", "priority": 10, "sla_hours": 48,
     "steps": ["ACCOUNTS"]},
    {"name": "R-SCRAP", "document_type": "scrap", "priority": 10, "sla_hours": 48,
     "steps": ["PLANT_HEAD", "ACCOUNTS"]},
    {"name": "R-COUNT", "document_type": "count", "priority": 10, "sla_hours": 48,
     "steps": ["PLANT_HEAD", "ACCOUNTS"]},
    {"name": "R-LEAVE", "document_type": "leave", "priority": 10, "sla_hours": 24,
     "steps": ["DEPT_HEAD"]},
    {"name": "R-KZ", "document_type": "kaizen", "priority": 10, "sla_hours": 168,
     "steps": ["DEPT_HEAD", "PLANT_HEAD"]},
    {"name": "R-REV", "document_type": "review", "priority": 10, "sla_hours": 120,
     "steps": ["DEPT_HEAD", "HR"]},
    # Not in the BRD matrix: vendor onboarding (FR-PRC-04) needs an approver too.
    {"name": "R-VENDOR", "document_type": "vendor", "priority": 10, "sla_hours": 48,
     "steps": ["PLANT_HEAD"]},
]
