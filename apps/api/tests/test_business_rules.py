"""Business rules: the registry against the BRD, validation, and the API with versioned history."""

from decimal import Decimal

import psycopg
import pytest

from heyyerp_api.business_rules.registry import BY_KEY, SETTINGS, validate

S = "/api/v1/settings"
ADMIN_S = "/api/v1/admin/settings"


# ------------------------------------------------------------------ registry (no database)
def test_every_brd_rule_has_a_setting_with_a_valid_default():
    rules = {s.rule for s in SETTINGS}
    assert {f"RULE-{n:02d}" for n in range(1, 18)} <= rules  # BRD 6.1 lists RULE-01..RULE-17
    assert len(BY_KEY) == len(SETTINGS)  # no duplicate keys
    for spec in SETTINGS:
        assert validate(spec, spec.default) == spec.default or spec.default is None, spec.key


def test_defaults_are_the_answers_v_rub_gave():
    answers = {"grnTolPct": 10, "priceTolPct": 0, "requireIndent": False, "valuation": "FIFO",
               "negativeStock": False, "issueRule": "FEFO", "countFreq": "MONTHLY",
               "interUnit": True, "prodLockExtraDays": 1, "consumption": "ACTUAL_ISSUE",
               "prodReporting": "PER_SHIFT", "appraisal": "YEARLY", "language": "EN"}
    assert {k: BY_KEY[k].default for k in answers} == answers
    for key in ("cashLimit", "minQuotes", "marginMD"):  # RULE-04, 05, 10: still undecided
        assert BY_KEY[key].default is None and BY_KEY[key].awaiting


def test_validation_is_strict():
    ok = lambda key, raw: validate(BY_KEY[key], raw)  # noqa: E731
    assert ok("grnTolPct", 15) == 15 and ok("requireIndent", True) is True
    assert ok("cashLimit", "5000") == "5000" and ok("cashLimit", 5000.5) == "5000.5"
    assert ok("cashLimit", None) is None and ok("issueRule", "FIFO") == "FIFO"
    for key, bad in [("grnTolPct", 101), ("grnTolPct", -1), ("grnTolPct", "10"),
                     ("grnTolPct", True), ("grnTolPct", 1.5), ("grnTolPct", None),
                     ("requireIndent", "true"), ("requireIndent", 1), ("issueRule", "LIFO"),
                     ("valuation", "WEIGHTED"), ("cashLimit", -5), ("cashLimit", "abc"),
                     ("cashLimit", "1.234"), ("cashLimit", True), ("cashLimit", float("inf")),
                     ("lockDay", 29), ("minQuotes", 11), ("language", "HI")]:
        with pytest.raises(ValueError):
            validate(BY_KEY[key], bad)
    assert Decimal(ok("marginMD", "12.50")) == Decimal("12.5")


# ------------------------------------------------------------------ API (needs a database)
@pytest.mark.usefixtures("api_database")
class TestApi:
    def test_everyone_can_read_and_defaults_are_shown(self, make_client):
        for role in ("OPERATOR", "STORES", "ADMIN"):
            rules = {r["key"]: r for r in make_client(role).get(S).json()}
            assert len(rules) == len(SETTINGS)
        grn = rules["grnTolPct"]
        assert grn["value"] == 10 and grn["is_default"] is True and grn["rule"] == "RULE-01"
        assert grn["status"] == "APPLIED" and grn["changed_by"] is None
        assert rules["cashLimit"]["status"] == "AWAITING_DECISION"
        assert make_client("STORES").get(f"{S}/issueRule").json()["value"] == "FEFO"
        assert make_client("STORES").get(f"{S}/nope").status_code == 404

    def test_only_admin_changes_rules_not_even_the_md(self, make_client):  # BRD: MD cannot
        for role in ("MD", "PLANT_HEAD", "ACCOUNTS", "STORES", "OPERATOR"):
            client = make_client(role)
            assert client.put(f"{ADMIN_S}/grnTolPct", json={"value": 5}).status_code == 403, role
            assert client.get(f"{ADMIN_S}/grnTolPct/history").status_code == 403, role

    def test_a_change_is_versioned_audited_and_reversible(self, make_client, api_database):
        admin = make_client("ADMIN")
        changed = admin.put(f"{ADMIN_S}/priceTolPct", json={"value": 2})
        assert changed.status_code == 200, changed.text
        body = changed.json()
        assert body["value"] == 2 and body["is_default"] is False and body["changed_by"] == "ADMIN user"
        assert make_client("OPERATOR").get(f"{S}/priceTolPct").json()["value"] == 2  # seen by all
        admin = make_client("ADMIN")
        admin.put(f"{ADMIN_S}/priceTolPct", json={"value": 3})
        history = admin.get(f"{ADMIN_S}/priceTolPct/history").json()
        assert [v["value"] for v in history] == [3, 2]
        assert history[0]["effective_to"] is None and history[1]["effective_to"] is not None
        with psycopg.connect(api_database["owner"]) as conn:
            audit = conn.execute(
                "select details->>'from', details->>'to' from erp.audit_log "
                "where action = 'business_rule.update' and entity_id = 'priceTolPct' "
                "order by audit_id").fetchall()
        assert audit == [("0", "2"), ("2", "3")]
        admin.put(f"{ADMIN_S}/priceTolPct", json={"value": 0})  # back to V-Rub's answer
        assert admin.get(f"{S}/priceTolPct").json()["value"] == 0

    def test_setting_the_same_value_changes_nothing(self, make_client):
        admin = make_client("ADMIN")
        admin.put(f"{ADMIN_S}/minQuotes", json={"value": 3})
        before = admin.get(f"{ADMIN_S}/minQuotes/history").json()
        again = admin.put(f"{ADMIN_S}/minQuotes", json={"value": 3})
        assert again.status_code == 200
        assert admin.get(f"{ADMIN_S}/minQuotes/history").json() == before
        admin.put(f"{ADMIN_S}/minQuotes", json={"value": None})

    def test_awaiting_rules_can_be_decided_and_undecided(self, make_client):
        admin = make_client("ADMIN")
        assert admin.get(f"{S}/cashLimit").json()["status"] == "AWAITING_DECISION"
        decided = admin.put(f"{ADMIN_S}/cashLimit", json={"value": "25000"}).json()
        assert decided["value"] == "25000" and decided["status"] == "APPLIED"
        undone = admin.put(f"{ADMIN_S}/cashLimit", json={"value": None}).json()
        assert undone["value"] is None and undone["status"] == "AWAITING_DECISION"

    def test_invalid_values_are_refused_and_nothing_is_stored(self, make_client):
        admin = make_client("ADMIN")
        for key, bad in [("grnTolPct", 500), ("grnTolPct", "ten"), ("requireIndent", "yes"),
                         ("issueRule", "LIFO"), ("grnTolPct", None), ("lockDay", 40)]:
            assert admin.put(f"{ADMIN_S}/{key}", json={"value": bad}).status_code == 422, key
        assert admin.put(f"{ADMIN_S}/nope", json={"value": 1}).status_code == 404
        assert admin.put(f"{ADMIN_S}/grnTolPct", json={}).status_code == 422
        assert admin.put(f"{ADMIN_S}/grnTolPct", json={"value": 5, "tenant_id": "x"}).status_code == 422
        assert admin.get(f"{S}/grnTolPct").json()["is_default"] is True

    def test_other_tenants_rules_are_not_ours(self, make_client, api_database):
        with psycopg.connect(api_database["owner"], autocommit=True) as conn:
            conn.execute("insert into erp.business_setting(tenant_id, setting_key, setting_value) "
                         "values (%s, 'appraisal', '\"HALF_YEARLY\"')", (api_database["tenant_b"],))
        assert make_client("ADMIN").get(f"{S}/appraisal").json()["value"] == "YEARLY"

    def test_other_modules_read_the_value_through_get_value(self, make_client, api_database):
        import asyncio

        from sqlalchemy import text

        from heyyerp_api import db
        from heyyerp_api.business_rules import service
        from heyyerp_api.masters.common import Ctx
        from heyyerp_api.security import Principal, Profile

        from .conftest import API_DB, API_PASSWORD, API_ROLE, TENANT_A
        from .dbutil import url_for

        make_client("ADMIN").put(f"{ADMIN_S}/grnTolPct", json={"value": 7})
        profile = Profile(__import__("uuid").uuid4(), "x", "ADMIN", frozenset(), frozenset(), None)

        async def read():
            db.configure_database(url_for(API_DB, API_ROLE, API_PASSWORD).replace(
                "postgresql://", "postgresql+asyncpg://"))
            try:
                async with db.SessionFactory() as session, session.begin():
                    await session.execute(text("select set_config('app.tenant_id', :t, true)"),
                                          {"t": TENANT_A})
                    ctx = Ctx(session, Principal("u", "e", True, "o", True,
                                                 __import__("uuid").UUID(TENANT_A), profile))
                    return await service.get_value(ctx, "grnTolPct"), \
                        await service.get_value(ctx, "negativeStock")
            finally:
                await db.engine.dispose()

        assert asyncio.run(read()) == (7, False)
        make_client("ADMIN").put(f"{ADMIN_S}/grnTolPct", json={"value": 10})
