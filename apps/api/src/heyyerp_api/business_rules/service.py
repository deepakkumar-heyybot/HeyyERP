"""Reading and changing business rules. Other modules call get_value(); only ADMIN changes them."""

import json
from typing import Any

from fastapi import HTTPException, status

from heyyerp_api.business_rules.registry import BY_KEY, SETTINGS, SettingDef, validate
from heyyerp_api.masters.common import Ctx, audit, db_errors, one, rows

CURRENT = "effective_from <= now() AND (effective_to IS NULL OR effective_to > now())"
CURRENT_S = "s.effective_from <= now() AND (s.effective_to IS NULL OR s.effective_to > now())"


def definition(key: str) -> SettingDef:
    found = BY_KEY.get(key)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No business rule named {key!r}")
    return found


async def get_value(ctx: Ctx, key: str) -> Any:
    """The rule's value right now: the stored answer, or V-Rub's default."""
    spec = definition(key)
    row = await one(
        ctx,
        f"SELECT setting_value FROM erp.business_setting WHERE setting_key = :k AND {CURRENT} "
        "ORDER BY effective_from DESC LIMIT 1", {"k": key},
    )
    return row["setting_value"] if row is not None else spec.default


async def all_current(ctx: Ctx) -> list[dict[str, Any]]:
    stored = {
        r["setting_key"]: r
        for r in await rows(
            ctx,
            "SELECT DISTINCT ON (s.setting_key) s.setting_key, s.setting_value, s.effective_from, "
            "u.display_name AS changed_by FROM erp.business_setting s "
            "LEFT JOIN erp.app_user u ON u.user_id = s.changed_by "
            f"WHERE {CURRENT_S} "
            "ORDER BY s.setting_key, s.effective_from DESC",
        )
    }
    result = []
    for spec in SETTINGS:
        row = stored.get(spec.key)
        value = row["setting_value"] if row else spec.default
        result.append(describe(spec, value, row))
    return result


def describe(spec: SettingDef, value: Any, row: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "key": spec.key, "rule": spec.rule, "description": spec.description, "kind": spec.kind,
        "options": list(spec.options), "nullable": spec.nullable, "value": value,
        "default": spec.default, "is_default": row is None,
        "status": "AWAITING_DECISION" if spec.awaiting and value is None else "APPLIED",
        "changed_at": row["effective_from"] if row else None,
        "changed_by": row["changed_by"] if row else None,
    }


async def set_value(ctx: Ctx, key: str, raw: Any) -> dict[str, Any]:
    spec = definition(key)
    try:
        value = validate(spec, raw)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    # Serialise changes to one rule so two administrators cannot interleave versions.
    await one(ctx, "SELECT pg_advisory_xact_lock(hashtext(:k)) AS locked",
              {"k": f"{ctx.principal.tenant_id}:{key}"})
    before = await get_value(ctx, key)
    if before == value:
        return (await _described(ctx, key))
    await rows(
        ctx,
        "UPDATE erp.business_setting SET effective_to = now() "
        f"WHERE setting_key = :k AND {CURRENT} RETURNING setting_id", {"k": key},
    )
    with db_errors("Business rule change"):
        await rows(
            ctx,
            "INSERT INTO erp.business_setting(tenant_id, setting_key, setting_value, changed_by) "
            "VALUES (erp.current_tenant_id(), :k, CAST(:v AS jsonb), :me) RETURNING setting_id",
            {"k": key, "v": json.dumps(value), "me": ctx.principal.profile.app_user_id},
        )
    await audit(ctx, "business_rule.update", "business_rule", key,
                {"rule": spec.rule, "from": before, "to": value})
    return await _described(ctx, key)


async def _described(ctx: Ctx, key: str) -> dict[str, Any]:
    return next(item for item in await all_current(ctx) if item["key"] == key)


async def history(ctx: Ctx, key: str) -> list[dict[str, Any]]:
    definition(key)
    return await rows(
        ctx,
        "SELECT s.setting_value AS value, s.effective_from, s.effective_to, "
        "u.display_name AS changed_by FROM erp.business_setting s "
        "LEFT JOIN erp.app_user u ON u.user_id = s.changed_by "
        "WHERE s.setting_key = :k ORDER BY s.effective_from DESC", {"k": key},
    )
