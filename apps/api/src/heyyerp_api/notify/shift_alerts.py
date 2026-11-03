"""The alert "shift ended without a production entry" (BRD section 5).

When a shift has ended (its end, its grace minutes and the company's wait `shiftAlertAfterMinutes`),
the machines of a plant that ran in the previous 7 days but have no entry for that shift are listed
to the production planners of the plant, in-app and on WhatsApp. Shifts come from the shift list
the company maintains (a plant's own shift replaces the all-plants shift of the same code); a day
the company marked as a holiday is skipped. Times are Indian time. Each plant, shift and day is
told once, however often the job runs.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_OID, uuid5

from heyyerp_api.business_rules import service as rules
from heyyerp_api.masters.common import Ctx, rows
from heyyerp_api.notify.queue import queue

IST = timezone(timedelta(hours=5, minutes=30))


async def machines_without_entry(ctx: Ctx, unit_id: Any, day: date, shift: str) -> list[str]:
    found = await rows(
        ctx, "SELECT m.code FROM erp.machine m WHERE m.active AND m.unit_id = :u AND EXISTS "
        "(SELECT 1 FROM erp.shift_production s WHERE s.machine_id = m.machine_id AND "
        "s.work_date >= CAST(:d AS date) - 7 AND s.work_date < :d) AND NOT EXISTS (SELECT 1 FROM "
        "erp.shift_production s WHERE s.machine_id = m.machine_id AND s.work_date = :d AND "
        "s.shift_code = :s) ORDER BY m.code", {"u": unit_id, "d": day, "s": shift})
    return [m["code"] for m in found]


async def planners(ctx: Ctx, unit_id: Any) -> list[Any]:
    """Active people who plan production and can see the plant."""
    return [r["user_id"] for r in await rows(
        ctx, "SELECT u.user_id FROM erp.app_user u JOIN erp.role_permission rp ON rp.role_id = "
        "u.role_id JOIN erp.permission p ON p.permission_id = rp.permission_id WHERE u.status = "
        "'ACTIVE' AND p.code = 'mfg.plan' AND (NOT EXISTS (SELECT 1 FROM erp.user_unit_scope s "
        "WHERE s.user_id = u.user_id) OR EXISTS (SELECT 1 FROM erp.user_unit_scope s WHERE "
        "s.user_id = u.user_id AND s.unit_id = :unit))", {"unit": unit_id})]


async def run(ctx: Ctx) -> dict[str, int]:
    wait = int(await rules.get_value(ctx, "shiftAlertAfterMinutes"))
    if wait <= 0:
        return {"shift_alerts": 0}
    now = datetime.now(UTC).astimezone(IST)
    units = [u["unit_id"] for u in await rows(ctx, "SELECT unit_id FROM erp.unit WHERE active")]
    chosen: dict[tuple[Any, str], dict[str, Any]] = {}
    for sh in await rows(ctx, "SELECT code, unit_id, start_time, end_time, grace_minutes FROM "
                         "erp.shift WHERE active AND code IN ('A', 'B', 'C') ORDER BY unit_id "
                         "NULLS FIRST"):  # a plant's own shift overwrites the general one
        for unit in units if sh["unit_id"] is None else [sh["unit_id"]]:
            chosen[(unit, sh["code"])] = sh
    told = 0
    for (unit, code), sh in chosen.items():
        for day in (now.date(), now.date() - timedelta(days=1)):
            start = datetime.combine(day, sh["start_time"], IST)
            end = datetime.combine(day, sh["end_time"], IST)
            if end <= start:
                end += timedelta(days=1)  # a night shift ends the next morning
            if now < end + timedelta(minutes=sh["grace_minutes"] + wait):
                continue
            if await rows(ctx, "SELECT 1 FROM erp.holiday WHERE active AND holiday_date = :d AND "
                          "(unit_id IS NULL OR unit_id = :u)", {"d": day, "u": unit}):
                continue
            machines = await machines_without_entry(ctx, unit, day, code)
            if not machines:
                continue
            people = await planners(ctx, unit)
            if not people:
                continue
            told += await queue(
                ctx, people, f"SHIFT_NO_ENTRY:{code}:{day.isoformat()}", "shift",
                uuid5(NAMESPACE_OID, f"{unit}|{day}|{code}"),
                {"shift": code, "day": day.isoformat(), "machines": machines})
    return {"shift_alerts": told}
