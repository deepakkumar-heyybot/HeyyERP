"""What the regression guards compare against: the API's operations and the catalogs.

The snapshots in tests/snapshots/*.json are the agreed contract. When a change is meant to add,
remove or rename an endpoint, permission, role, setting, document type or numbering prefix,
regenerate them on purpose and review the diff in the pull request:

    python -m tests.snapshots
"""

import json
from pathlib import Path
from typing import Any

from heyyerp_api.business_rules.registry import SETTINGS
from heyyerp_api.main import app
from heyyerp_api.procurement.numbering import PREFIXES
from heyyerp_api.rbac.catalog import ALL_MODULES, PERMISSIONS, ROLES, role_modules, role_permissions
from heyyerp_api.workflow.registry import KNOWN_TYPES, REGISTRY

DIR = Path(__file__).parent / "snapshots"
METHODS = ("get", "post", "put", "patch", "delete")


def routes() -> list[str]:
    spec = app.openapi()
    return sorted(f"{m.upper()} {p}" for p, ops in spec["paths"].items() for m in ops
                  if m in METHODS)


def catalog() -> dict[str, Any]:
    return {
        "permissions": sorted(PERMISSIONS),
        "modules": sorted(ALL_MODULES),
        "roles": {code: {"permissions": sorted(role_permissions(code)),
                         "modules": sorted(role_modules(code))} for code in sorted(ROLES)},
        "settings": {s.key: {"kind": s.kind, "default": s.default, "rule": s.rule}
                     for s in SETTINGS},
        "numbering_prefixes": dict(sorted(PREFIXES.items())),
        "approval_document_types": sorted(KNOWN_TYPES),
        "registered_document_types": sorted(REGISTRY),
    }


def load(name: str) -> Any:
    return json.loads((DIR / f"{name}.json").read_text(encoding="utf-8"))


def write() -> None:
    DIR.mkdir(exist_ok=True)
    (DIR / "routes.json").write_text(json.dumps(routes(), indent=1) + "\n", encoding="utf-8")
    (DIR / "catalog.json").write_text(
        json.dumps(catalog(), indent=1, default=str) + "\n", encoding="utf-8")
    print(f"{len(routes())} operations and the catalog written to {DIR}")


if __name__ == "__main__":
    write()
