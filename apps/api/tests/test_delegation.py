"""Delegation of approvals: a stand-in sees, is told, decides as themselves and is recorded."""

from datetime import UTC, datetime, timedelta

import pytest

from heyyerp_api.notify import senders

from .test_compound_batches import Fresh
from .test_costing import Rules
from .test_hr import user_of
from .test_notifications import (
    Fake,
    quiet_queue,  # noqa: F401
    run_jobs,
    sql,
    submit_kaizen,
)

pytestmark = pytest.mark.usefixtures("api_database")
A = "/api/v1/approvals"
D = f"{A}/delegations"


def window(days=3, start=-1):
    now = datetime.now(UTC)
    return {"valid_from": (now + timedelta(days=start)).isoformat(),
            "valid_to": (now + timedelta(days=start + days)).isoformat()}


def two_heads(make_client, api_database):
    """The department head (role PROD) and a colleague of another role who may stand in."""
    boss, boss_id = user_of(make_client, api_database, "PROD")
    stand_in, stand_id = user_of(make_client, api_database, "QA")
    return boss, boss_id, stand_in, stand_id


def test_a_stand_in_sees_and_decides_the_approval_as_themselves_and_it_is_recorded(
        make_client, api_database):
    w, me, k = submit_kaizen(make_client, api_database, "While away")
    boss, boss_id, stand_in, stand_id = two_heads(make_client, api_database)
    assert stand_in.post(f"{A}/{k['approval_id']}/decide", json={
        "decision": "APPROVE"}).status_code == 403  # not yet
    assert [i["approval_id"] for i in stand_in.get(f"{A}?scope=mine").json()["items"]
            if i["approval_id"] == k["approval_id"]] == []
    made = boss.post(D, json={"to_user_id": stand_id, **window()})
    assert made.status_code == 201, made.text
    assert made.json()["in_force"] and made.json()["role"] == "PROD"
    seen = stand_in.get(f"{A}?scope=mine").json()["items"]
    assert any(i["approval_id"] == k["approval_id"] for i in seen)
    assert stand_in.get(f"{A}/{k['approval_id']}").status_code == 200
    done = stand_in.post(f"{A}/{k['approval_id']}/decide", json={"decision": "APPROVE"})
    assert done.status_code == 200, done.text
    row = sql(api_database, "select decision, acting_as_role, actor_user_id::text, "
              "on_behalf_of::text from erp.approval_action where approval_id = %s",
              k["approval_id"])[0]
    assert row == ("APPROVE", "PROD", stand_id, boss_id)  # not an override: a real delegation
    audited = sql(api_database, "select details->>'on_behalf_of' from erp.audit_log where "
                  "action = 'approval.decide' and entity_id = %s", k["approval_id"])
    assert audited == [(boss_id,)]


def test_a_delegation_ends_on_revoke_on_expiry_and_cannot_cover_your_own_submission(
        make_client, api_database):
    boss, boss_id, stand_in, stand_id = two_heads(make_client, api_database)
    w, me, k = submit_kaizen(make_client, api_database, "Revoked")
    made = boss.post(D, json={"to_user_id": stand_id, **window()}).json()
    assert Fresh(make_client, "SALES").post(
        f"{D}/{made['delegation_id']}/revoke").status_code == 404  # not theirs
    assert boss.post(f"{D}/{made['delegation_id']}/revoke").json()["active"] is False
    assert boss.post(f"{D}/{made['delegation_id']}/revoke").status_code == 409
    assert stand_in.post(f"{A}/{k['approval_id']}/decide", json={
        "decision": "APPROVE"}).status_code == 403
    past = boss.post(D, json={"to_user_id": stand_id, **window(days=1, start=-5)}).json()
    assert past["in_force"] is False  # the period is over
    assert stand_in.post(f"{A}/{k['approval_id']}/decide", json={
        "decision": "APPROVE"}).status_code == 403
    future = boss.post(D, json={"to_user_id": stand_id, **window(days=1, start=2)}).json()
    assert future["in_force"] is False  # not started yet


def test_the_delegate_cannot_decide_what_the_delegator_submitted(make_client, api_database):
    boss, boss_id, stand_in, stand_id = two_heads(make_client, api_database)
    w, me, k = submit_kaizen(make_client, api_database, "Boss's own")
    sql(api_database, "update erp.approval_instance set submitted_by = %s where approval_id = %s",
        boss_id, k["approval_id"])
    boss.post(D, json={"to_user_id": stand_id, **window()})
    refused = stand_in.post(f"{A}/{k['approval_id']}/decide", json={"decision": "APPROVE"})
    assert refused.status_code == 403 and "stand in for" in refused.text


def test_delegation_rules_and_the_company_limit(make_client, api_database):
    boss, boss_id, stand_in, stand_id = two_heads(make_client, api_database)
    assert boss.post(D, json={"to_user_id": boss_id, **window()}).status_code == 422
    backwards = {"valid_from": window()["valid_to"], "valid_to": window()["valid_from"]}
    assert boss.post(D, json={"to_user_id": stand_id, **backwards}).status_code == 422
    assert boss.post(D, json={"to_user_id": stand_id, **window(days=40)}).status_code == 422
    with Rules(make_client, delegationMaxDays=60):
        assert boss.post(D, json={"to_user_id": stand_id, **window(days=40)}).status_code == 201
    assert boss.post(D, json={"to_user_id": stand_id, **window(days=2)}).status_code == 409
    assert boss.post(D, json={"to_user_id": "00000000-0000-0000-0000-000000000009",
                              **window(days=2, start=100)}).status_code == 404
    mine, theirs = boss.get(D).json(), stand_in.get(D).json()
    assert mine and theirs and all(d["from_user_id"] == boss_id for d in mine)
    assert boss.get(f"{D}?all_=true").status_code == 403
    assert Fresh(make_client, "ADMIN").get(f"{D}?all_=true").status_code in (200, 403)


def test_the_stand_in_is_told_when_an_approval_arrives(make_client, api_database, monkeypatch):
    mail = Fake()
    monkeypatch.setitem(senders.override, "EMAIL", mail)
    boss, boss_id, stand_in, stand_id = two_heads(make_client, api_database)
    sql(api_database, "update erp.app_user set email = 'standin@vrub.example' where user_id = %s",
        stand_id)
    boss.post(D, json={"to_user_id": stand_id, **window()})
    submit_kaizen(make_client, api_database, "Tell the stand-in")
    run_jobs(make_client)
    assert any(to == "standin@vrub.example" and "Tell the stand-in" in subject
               for to, subject, _ in mail.sent)
    inbox = stand_in.get("/api/v1/notifications").json()
    assert any("Tell the stand-in" in (n["subject"] or "") for n in inbox)
