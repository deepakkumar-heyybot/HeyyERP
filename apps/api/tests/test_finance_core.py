"""Chart of accounts, journals, reversals, trial balance, period lock and the database guards."""

import asyncio
from datetime import date, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text

from heyyerp_api import db
from heyyerp_api.finance import journals
from heyyerp_api.finance.journals import LineIn
from heyyerp_api.masters.common import Ctx
from heyyerp_api.rbac.seed import create_app_user
from heyyerp_api.security import Principal, Profile

from .conftest import API_DB, API_PASSWORD, API_ROLE, TENANT_A
from .dbutil import url_for
from .test_compound_batches import Fresh
from .test_inventory_issue import receive
from .test_inventory_receipt import INV, code, world

pytestmark = pytest.mark.usefixtures("api_database")
F = "/api/v1/finance"
TODAY = date.today()


def finance_world(make_client):
    admin = Fresh(make_client, "ADMIN")
    unit = admin.post("/api/v1/org/units", json={"code": code(), "name": "Plant"}).json()
    seeded = Fresh(make_client, "ACCOUNTS").post(f"{F}/accounts/seed-default",
                                                 json={"unit_id": unit["unit_id"]})
    assert seeded.status_code == 200, seeded.text
    return {"unit": unit, "acc": {a["code"]: a["account_id"] for a in seeded.json()}}


def voucher(w, amount="1000", debit="1010", credit="4000", day=None, **extra):
    return {"unit_id": w["unit"]["unit_id"], "journal_date": (day or TODAY).isoformat(),
            "narration": "Test voucher", **extra,
            "lines": [{"account_id": w["acc"][debit], "debit": amount},
                      {"account_id": w["acc"][credit], "credit": amount}]}


def draft(make_client, w, **kw):
    made = Fresh(make_client, "ACCOUNTS").post(f"{F}/journals", json=voucher(w, **kw))
    assert made.status_code == 201, made.text
    return made.json()


def posted(make_client, w, **kw):
    j = draft(make_client, w, **kw)
    done = Fresh(make_client, "ACCOUNTS").post(f"{F}/journals/{j['journal_id']}/post")
    assert done.status_code == 200, done.text
    return done.json()


# ------------------------------------------------------------------ chart of accounts
def test_default_chart_maps_the_posting_roles_and_can_be_repeated(make_client):
    w = finance_world(make_client)
    acct = Fresh(make_client, "ACCOUNTS")
    assert len(w["acc"]) == 26 and w["acc"]["1100"]
    roles = {r["role"]: r["account_code"] for r in acct.get(
        f"{F}/account-roles?unit_id={w['unit']['unit_id']}").json()}
    assert roles["BANK"] == "1010" and roles["RECEIVABLE"] == "1100" and roles["PAYABLE"] == "2000"
    assert roles["GRN_CLEARING"] == "2010" and roles["SALES"] == "4000"
    again = acct.post(f"{F}/accounts/seed-default", json={"unit_id": w["unit"]["unit_id"]})
    assert again.status_code == 200 and len(again.json()) == 26  # nothing duplicated
    other = acct.put(f"{F}/account-roles", json={
        "unit_id": w["unit"]["unit_id"], "role": "BANK", "account_id": w["acc"]["1000"]})
    assert other.status_code == 200 and other.json()["account_code"] == "1000"
    bad = acct.put(f"{F}/account-roles", json={
        "unit_id": w["unit"]["unit_id"], "role": "NOT_A_ROLE", "account_id": w["acc"]["1000"]})
    assert bad.status_code == 422


def test_account_rules_and_permissions(make_client):
    w = finance_world(make_client)
    unit = w["unit"]["unit_id"]
    acct = Fresh(make_client, "ACCOUNTS")
    new = acct.post(f"{F}/accounts", json={"unit_id": unit, "code": "6100", "name": "Rent",
                                           "account_type": "EXPENSE"})
    assert new.status_code == 201 and new.json()["roles"] == []
    assert acct.post(f"{F}/accounts", json={"unit_id": unit, "code": "6100", "name": "Dup",
                                            "account_type": "EXPENSE"}).status_code == 409
    assert acct.post(f"{F}/accounts", json={"unit_id": unit, "code": "6200", "name": "X Y",
                                            "account_type": "MISC"}).status_code == 422
    held = acct.patch(f"{F}/accounts/{w['acc']['1010']}", json={"active": False})
    assert held.status_code == 409 and "BANK" in held.json()["detail"]  # a role still uses it
    assert acct.patch(f"{F}/accounts/{new.json()['account_id']}", json={"active": False}
                      ).json()["active"] is False
    assert acct.patch(f"{F}/accounts/{new.json()['account_id']}", json={"name": None}
                      ).status_code == 422
    for role in ("SALES", "PURCHASE", "STORES"):
        assert Fresh(make_client, role).get(f"{F}/accounts").status_code == 403, role
        assert Fresh(make_client, role).post(f"{F}/accounts/seed-default", json={
            "unit_id": unit}).status_code == 403
    away_unit = Fresh(make_client, "ADMIN").post("/api/v1/org/units",
                                                  json={"code": code(), "name": "P2"}).json()
    away = Fresh(make_client, "ACCOUNTS", subject="u-fin-out", unit_ids=[away_unit["unit_id"]])
    assert away.get(f"{F}/accounts/{w['acc']['1010']}").status_code == 404
    assert away.get(f"{F}/accounts?unit_id={unit}").json()["total"] == 0
    assert away.post(f"{F}/accounts/seed-default", json={"unit_id": unit}).status_code == 403


# ------------------------------------------------------------------ journals
def test_a_journal_is_drafted_edited_posted_and_numbered(make_client):
    w = finance_world(make_client)
    j = draft(make_client, w, amount="2500")
    assert j["status"] == "DRAFT" and j["journal_no"].startswith("DRAFT-")
    assert float(j["total_debit"]) == float(j["total_credit"]) == 2500
    acct = Fresh(make_client, "ACCOUNTS")
    edited = acct.put(f"{F}/journals/{j['journal_id']}", json=voucher(w, amount="3000"))
    assert edited.status_code == 200 and float(edited.json()["total_debit"]) == 3000
    done = acct.post(f"{F}/journals/{j['journal_id']}/post")
    assert done.status_code == 200
    p = done.json()
    assert p["status"] == "POSTED" and p["journal_no"].startswith("JV-") and p["posted_by_name"]
    assert [ln["account_code"] for ln in p["lines"]] == ["1010", "4000"]
    assert acct.post(f"{F}/journals/{j['journal_id']}/post").status_code == 409
    assert acct.put(f"{F}/journals/{j['journal_id']}", json=voucher(w)).status_code == 409
    assert acct.delete(f"{F}/journals/{j['journal_id']}").status_code == 409
    gone = draft(make_client, w)
    assert acct.delete(f"{F}/journals/{gone['journal_id']}").status_code == 204
    assert acct.get(f"{F}/journals/{gone['journal_id']}").status_code == 404
    listed = acct.get(f"{F}/journals?unit_id={w['unit']['unit_id']}&status=posted").json()
    assert [x["journal_id"] for x in listed["items"]] == [j["journal_id"]]
    by_account = acct.get(f"{F}/journals?account_id={w['acc']['4000']}").json()
    assert j["journal_id"] in {x["journal_id"] for x in by_account["items"]}


def test_journal_validation(make_client):
    w = finance_world(make_client)
    acct = Fresh(make_client, "ACCOUNTS")
    base = voucher(w)
    assert acct.post(f"{F}/journals", json={**base, "lines": [
        {"account_id": w["acc"]["1010"], "debit": "100"},
        {"account_id": w["acc"]["4000"], "credit": "90"}]}).status_code == 422  # unbalanced
    assert acct.post(f"{F}/journals", json={**base, "lines": base["lines"][:1]}
                     ).status_code == 422  # one line
    assert acct.post(f"{F}/journals", json={**base, "lines": [
        {"account_id": w["acc"]["1010"], "debit": "100", "credit": "100"},
        {"account_id": w["acc"]["4000"], "credit": "0"}]}).status_code == 422  # both or neither
    assert acct.post(f"{F}/journals", json={**base, "lines": [
        {"account_id": str(uuid4()), "debit": "100"},
        {"account_id": w["acc"]["4000"], "credit": "100"}]}).status_code == 422
    other = finance_world(make_client)
    assert acct.post(f"{F}/journals", json={**base, "lines": [
        {"account_id": other["acc"]["1010"], "debit": "100"},
        {"account_id": w["acc"]["4000"], "credit": "100"}]}).status_code == 422  # other plant
    assert acct.post(f"{F}/journals", json={**base, "lines": [
        {"account_id": w["acc"]["1010"], "debit": "100", "party_type": "VENDOR"},
        {"account_id": w["acc"]["4000"], "credit": "100"}]}).status_code == 422  # party alone
    acct.post(f"{F}/accounts", json={"unit_id": w["unit"]["unit_id"], "code": "9999",
                                     "name": "Closed one", "account_type": "EXPENSE"})
    listing = acct.get(f"{F}/accounts?unit_id={w['unit']['unit_id']}&limit=200").json()["items"]
    spare = next(a for a in listing if a["code"] == "9999")
    acct.patch(f"{F}/accounts/{spare['account_id']}", json={"active": False})
    assert acct.post(f"{F}/journals", json={**base, "lines": [
        {"account_id": spare["account_id"], "debit": "100"},
        {"account_id": w["acc"]["4000"], "credit": "100"}]}).status_code == 422  # inactive
    for role in ("SALES", "STORES", "QA"):
        assert Fresh(make_client, role).post(f"{F}/journals", json=base).status_code == 403, role


def test_a_posted_journal_is_reversed_never_edited(make_client):
    w = finance_world(make_client)
    original = posted(make_client, w, amount="750")
    acct = Fresh(make_client, "ACCOUNTS")
    rev = acct.post(f"{F}/journals/{original['journal_id']}/reverse", json={"reason": "Wrong account"})
    assert rev.status_code == 201, rev.text
    r = rev.json()
    assert r["source_type"] == "REVERSAL" and r["status"] == "POSTED"
    assert r["reverses_journal_id"] == original["journal_id"]
    assert [(ln["account_code"], float(ln["debit"]), float(ln["credit"])) for ln in r["lines"]] == [
        ("1010", 0.0, 750.0), ("4000", 750.0, 0.0)]  # debit and credit swapped
    assert acct.get(f"{F}/journals/{original['journal_id']}").json()["status"] == "REVERSED"
    assert acct.post(f"{F}/journals/{original['journal_id']}/reverse", json={
        "reason": "Again"}).status_code == 409
    assert acct.post(f"{F}/journals/{r['journal_id']}/reverse", json={
        "reason": "Undo the undo"}).status_code == 409
    d = draft(make_client, w)
    assert acct.post(f"{F}/journals/{d['journal_id']}/reverse", json={"reason": "Draft"}
                     ).status_code == 409
    tb = acct.get(f"{F}/trial-balance?unit_id={w['unit']['unit_id']}").json()
    assert all(float(row["closing"]) == 0 for row in tb["rows"]) and tb["balanced"]


def test_the_database_itself_refuses_to_change_a_posted_journal(make_client, api_database):
    w = finance_world(make_client)
    j = posted(make_client, w, amount="400")
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        for sql in ("update erp.journal set narration = 'edited' where journal_id = %s",
                    "delete from erp.journal where journal_id = %s",
                    "update erp.journal_line set debit = debit + 1 where journal_id = %s and "
                    "debit > 0", "delete from erp.journal_line where journal_id = %s",
                    "update erp.journal set status = 'DRAFT' where journal_id = %s"):
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(sql, (j["journal_id"],))
        with pytest.raises(psycopg.errors.RaiseException):  # a line cannot be added to it either
            conn.execute(
                "insert into erp.journal_line(tenant_id, journal_id, account_id, line_no, debit) "
                "values (%s, %s, %s, 9, 1)", (TENANT_A, j["journal_id"], w["acc"]["1010"]))


def test_the_database_refuses_to_post_a_journal_that_does_not_balance(make_client, api_database):
    w = finance_world(make_client)
    d = draft(make_client, w, amount="100")
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("update erp.journal_line set debit = 90 where journal_id = %s and debit > 0",
                     (d["journal_id"],))  # a draft may be unbalanced while it is being edited
        with pytest.raises(psycopg.errors.RaiseException, match="balance"):
            conn.execute("update erp.journal set status = 'POSTED' where journal_id = %s",
                         (d["journal_id"],))


# ------------------------------------------------------------------ trial balance and ledger
def test_trial_balance_and_account_ledger_follow_the_dates(make_client):
    w = finance_world(make_client)
    posted(make_client, w, amount="1000", day=TODAY)
    posted(make_client, w, amount="250", debit="5500", credit="1010", day=TODAY)
    acct = Fresh(make_client, "ACCOUNTS")
    tb = acct.get(f"{F}/trial-balance?unit_id={w['unit']['unit_id']}").json()
    by = {r["code"]: r for r in tb["rows"]}
    assert float(by["1010"]["debit"]) == 1000 and float(by["1010"]["credit"]) == 250
    assert float(by["1010"]["closing"]) == 750 and float(by["4000"]["closing"]) == -1000
    assert float(by["5500"]["closing"]) == 250
    assert tb["balanced"] and float(tb["total_debit"]) == float(tb["total_credit"]) == 1250
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    later = acct.get(f"{F}/trial-balance?unit_id={w['unit']['unit_id']}&date_from={tomorrow}"
                     f"&date_to={tomorrow}").json()
    bank = next(r for r in later["rows"] if r["code"] == "1010")
    assert float(bank["opening"]) == 750 and float(bank["debit"]) == 0  # opening carries forward
    assert acct.get(f"{F}/trial-balance?unit_id={w['unit']['unit_id']}&date_from={tomorrow}"
                    f"&date_to={TODAY.isoformat()}").status_code == 422
    stmt = acct.get(f"{F}/accounts/{w['acc']['1010']}/ledger").json()
    assert [(float(x["debit"]), float(x["credit"]), float(x["balance"])) for x in stmt["lines"]] == [
        (1000.0, 0.0, 1000.0), (0.0, 250.0, 750.0)]
    assert float(stmt["opening"]) == 0 and float(stmt["closing"]) == 750
    drafts = draft(make_client, w, amount="9999")  # drafts never reach the books
    assert drafts["status"] == "DRAFT"
    again = acct.get(f"{F}/trial-balance?unit_id={w['unit']['unit_id']}").json()
    assert float(again["total_debit"]) == 1250


# ------------------------------------------------------------------ period lock
def test_a_month_past_its_lock_date_cannot_be_posted_into(make_client):
    w = finance_world(make_client)
    last_month_day = TODAY.replace(day=1) - timedelta(days=3)
    d = draft(make_client, w, day=last_month_day)  # drafting is allowed, posting is not
    refused = Fresh(make_client, "ACCOUNTS").post(f"{F}/journals/{d['journal_id']}/post")
    assert refused.status_code == 409 and "nobody may post into a closed month" in (
        refused.json()["detail"])
    assert posted(make_client, w, day=TODAY)["status"] == "POSTED"


def test_closing_a_month_blocks_postings_and_stock_until_an_admin_reopens_it(
        make_client, api_database):
    w = world(make_client, qty="200")
    receive(make_client, w, "100")
    unit = w["unit"]["unit_id"]
    fw = {"unit": w["unit"], "acc": {a["code"]: a["account_id"] for a in Fresh(
        make_client, "ACCOUNTS").post(f"{F}/accounts/seed-default", json={"unit_id": unit}).json()}}
    acct = Fresh(make_client, "ACCOUNTS")
    month = f"{TODAY:%Y-%m}"
    d = draft(make_client, fw)
    blocked = acct.post(f"{F}/periods/close", json={"unit_id": unit, "month": month})
    assert blocked.status_code == 409 and "draft journal" in blocked.json()["detail"]
    acct.delete(f"{F}/journals/{d['journal_id']}")
    closed = acct.post(f"{F}/periods/close", json={"unit_id": unit, "month": month})
    assert closed.status_code == 200 and closed.json()["status"] == "CLOSED"
    assert closed.json()["closed_by_name"] and acct.post(
        f"{F}/periods/close", json={"unit_id": unit, "month": month}).status_code == 409
    again = draft(make_client, fw)
    refused = acct.post(f"{F}/journals/{again['journal_id']}/post")
    assert refused.status_code == 409 and "is closed" in refused.json()["detail"]
    issue = Fresh(make_client, "STORES").post(f"{INV}/issues", json={
        "store_id": w["store"]["store_id"], "purpose": "Production batch",
        "lines": [{"item_id": w["item"]["item_id"], "quantity": "5"}]})
    assert issue.status_code == 409 and "closed" in issue.json()["detail"]
    assert acct.post(f"{F}/periods/reopen", json={"unit_id": unit, "month": month}
                     ).status_code == 403  # only an administrator reopens
    reopened = Fresh(make_client, "ADMIN").post(f"{F}/periods/reopen",
                                                json={"unit_id": unit, "month": month})
    assert reopened.status_code == 200 and reopened.json()["status"] == "OPEN"
    assert acct.post(f"{F}/journals/{again['journal_id']}/post").status_code == 200
    assert Fresh(make_client, "STORES").post(f"{INV}/issues", json={
        "store_id": w["store"]["store_id"], "purpose": "Production batch",
        "lines": [{"item_id": w["item"]["item_id"], "quantity": "5"}]}).status_code == 201
    assert any(p["unit_id"] == unit for p in acct.get(f"{F}/periods?unit_id={unit}").json())
    future = f"{(TODAY.replace(day=1) + timedelta(days=40)):%Y-%m}"
    assert acct.post(f"{F}/periods/close", json={"unit_id": unit, "month": future}
                     ).status_code == 422  # a future month cannot be closed
    assert acct.post(f"{F}/periods/close", json={"unit_id": unit, "month": "2026-13"}
                     ).status_code == 422


# ------------------------------------------------------------------ posting for other modules
def test_posting_for_a_source_document_happens_once(make_client, api_database):
    w = finance_world(make_client)
    source = uuid4()
    with psycopg.connect(url_for(API_DB), autocommit=True) as owner:
        uid = create_app_user(owner, TENANT_A, f"poster-{uuid4().hex[:6]}", "ACCOUNTS", "Poster")
    profile = Profile(uid, "Poster", "ACCOUNTS", frozenset(), frozenset(), None)

    async def post_twice() -> tuple[UUID, UUID]:
        db.configure_database(url_for(API_DB, API_ROLE, API_PASSWORD).replace(
            "postgresql://", "postgresql+asyncpg://"))
        try:
            ids = []
            for _ in range(2):
                async with db.SessionFactory() as session, session.begin():
                    await session.execute(text("select set_config('app.tenant_id', :t, true)"),
                                          {"t": TENANT_A})
                    ctx = Ctx(session, Principal("u", "e", True, "o", True, UUID(TENANT_A), profile))
                    ids.append(await journals.post_entry(
                        ctx, unit_id=UUID(w["unit"]["unit_id"]), journal_date=TODAY,
                        source_type="TEST_SOURCE", source_id=source, narration="Auto posting",
                        lines=[LineIn(account_id=UUID(w["acc"]["1010"]), debit="50"),
                               LineIn(account_id=UUID(w["acc"]["4000"]), credit="50")]))
            return ids[0], ids[1]
        finally:
            await db.engine.dispose()

    first, second = asyncio.run(post_twice())
    assert first == second  # the same document is never posted twice
    count = Fresh(make_client, "ACCOUNTS").get(f"{F}/journals?source_type=test_source").json()
    assert count["total"] == 1 and count["items"][0]["status"] == "POSTED"
