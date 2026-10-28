"""List every API endpoint with the permission or module it needs, as JSON or Markdown.

    python scripts/endpoint_inventory.py            # Markdown table per module
    python scripts/endpoint_inventory.py --json
"""

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from heyyerp_api.api.router import api_router  # noqa: E402
from heyyerp_api.main import app  # noqa: E402

PREFIX = "/api/v1"


def closure_vars(fn: Any) -> dict[str, Any]:
    names = getattr(getattr(fn, "__code__", None), "co_freevars", ())
    cells = getattr(fn, "__closure__", None) or ()
    return {n: c.cell_contents for n, c in zip(names, cells, strict=False)}


def needs(dependant: Any, found: dict[str, str]) -> None:
    found.update({k: v for k, v in closure_vars(dependant.call).items()
                  if k in ("permission", "module") and isinstance(v, str)})
    for dep in dependant.dependencies:
        needs(dep, found)


def api_routes(router: Any) -> Iterator[APIRoute]:
    for route in router.routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from api_routes(route.original_router)


def inventory() -> list[dict[str, Any]]:
    summaries = {(m.upper(), p): op.get("summary", "") for p, ops in app.openapi()["paths"].items()
                 for m, op in ops.items() if m in ("get", "post", "put", "patch", "delete")}
    rows: list[dict[str, Any]] = []
    for route in api_routes(api_router):
        found: dict[str, str] = {}
        needs(route.dependant, found)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            path = PREFIX + route.path
            rows.append({"method": method, "path": path, "tag": (route.tags or [""])[0],
                         "permission": found.get("permission"), "module": found.get("module"),
                         "summary": summaries.get((method, path), "")})
    return sorted(rows, key=lambda r: (r["tag"], r["path"], r["method"]))


if __name__ == "__main__":
    data = inventory()
    if "--json" in sys.argv:
        print(json.dumps(data, indent=1))
    else:
        for tag in dict.fromkeys(r["tag"] for r in data):
            print(f"\n### {tag}\n\n| Method | Path | Needs |\n|---|---|---|")
            for r in (x for x in data if x["tag"] == tag):
                need = f"`{r['permission']}`" if r["permission"] else (
                    f"module `{r['module']}`" if r["module"] else "signed in")
                print(f"| {r['method']} | `{r['path']}` | {need} |")
