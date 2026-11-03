"""Approving or rejecting from an e-mail link: once, for one person and one step, on the company's say."""

import re

import pytest

from heyyerp_api.notify import senders

from .test_compound_batches import Fresh
from .test_costing import Rules
from .test_hr import decide
from .test_notifications import (
    Fake,
    quiet_queue,  # noqa: F401
    run_jobs,
    sql,
)
from .test_notifications import submit_kaizen as submit

pytestmark = pytest.mark.usefixtures("api_database")
P = "/api/v1/public/approval-links"


@pytest.fixture
def mail(monkeypatch):
    fake = Fake()
    monkeypatch.setitem(senders.override, "EMAIL", fake)
    return fake


def token_in(mail, containing):
    """The secret of the link in the e-mail whose subject has the words."""
    for _, subject, body in mail.sent:
        found = re.search(r"/approve/(\S+)", body)
        if containing in subject and found:
            return found.group(1)
    return None


def submit_kaizen(make_client, api_database, title):
    """A kaizen waiting for the department head (whose account must exist to be messaged)."""
    made = submit(make_client, api_database, title)
    Fresh(make_client, "PROD").get("/api/v1/notifications/count")
    return made


def anyone(make_client):
    return Fresh(make_client, "SALES")  # the public pages need no sign-in; any client will do


def test_no_link_is_sent_until_the_company_turns_it_on(make_client, api_database, mail):
    submit_kaizen(make_client, api_database, "Quiet one")
    run_jobs(make_client)
    assert mail.sent and not any("/approve/" in body for _, _, body in mail.sent)


def test_an_approver_decides_from_the_link_once_and_it_is_recorded(
        make_client, api_database, mail):
    with Rules(make_client, approvalByLink=True, approvalLinkMaxAmount="100000"):
        w, me, k = submit_kaizen(make_client, api_database, "Link me")
        run_jobs(make_client)
        token = token_in(mail, "Link me")
        assert token and len(token) >= 40
        inbox = Fresh(make_client, "PROD").get("/api/v1/notifications").json()
        assert not any("/approve/" in (n["body"] or "") for n in inbox)  # in-app needs no link
        shown = anyone(make_client).get(f"{P}/{token}")
        assert shown.status_code == 200 and shown.json()["title"].startswith("Kaizen")
        assert anyone(make_client).get(f"{P}/{token}").status_code == 200  # looking is harmless
        assert anyone(make_client).get(f"{P}/not-a-link").status_code == 404
        done = anyone(make_client).post(f"{P}/{token}/decide", json={"decision": "APPROVE"})
        assert done.status_code == 200 and done.json() == {"status": "PENDING"}  # next step
        via = sql(api_database, "select via, decision, acting_as_role from erp.approval_action "
                  "where approval_id = %s", k["approval_id"])
        assert via == [("LINK", "APPROVE", "PROD")]
        again = anyone(make_client).post(f"{P}/{token}/decide", json={"decision": "APPROVE"})
        assert again.status_code == 410 and "already been used" in again.text
        assert anyone(make_client).get(f"{P}/{token}").status_code == 410


def test_a_link_for_a_step_decided_elsewhere_or_expired_does_not_work(
        make_client, api_database, mail):
    with Rules(make_client, approvalByLink=True, approvalLinkMaxAmount="100000"):
        w, me, k = submit_kaizen(make_client, api_database, "Decided elsewhere")
        run_jobs(make_client)
        token = token_in(mail, "Decided elsewhere")
        assert decide(make_client, k["approval_id"], "PROD").status_code == 200
        gone = anyone(make_client).post(f"{P}/{token}/decide", json={"decision": "APPROVE"})
        assert gone.status_code == 410 and "already been decided" in gone.text
        w2, me2, k2 = submit_kaizen(make_client, api_database, "Too late")
        run_jobs(make_client)
        late = token_in(mail, "Too late")
        sql(api_database, "update erp.approval_link set expires_at = now() - interval '1 min'")
        expired = anyone(make_client).post(f"{P}/{late}/decide", json={"decision": "REJECT"})
        assert expired.status_code == 410 and "expired" in expired.text


def test_a_reject_by_link_carries_the_note_and_the_requester_is_told(
        make_client, api_database, mail):
    with Rules(make_client, approvalByLink=True, approvalLinkMaxAmount="100000"):
        w, me, k = submit_kaizen(make_client, api_database, "Reject me")
        run_jobs(make_client)
        token = token_in(mail, "Reject me")
        done = anyone(make_client).post(f"{P}/{token}/decide", json={
            "decision": "REJECT", "note": "Not enough data"})
        assert done.json() == {"status": "REJECTED"}
        assert sql(api_database, "select note from erp.approval_action where approval_id = %s",
                   k["approval_id"]) == [("Not enough data",)]
        run_jobs(make_client)
        mine = me.get("/api/v1/notifications").json()
        assert any(n["subject"].endswith("was rejected") for n in mine)


def test_the_lifetime_is_the_companys_and_a_switch_off_stops_new_links(
        make_client, api_database, mail):
    with Rules(make_client, approvalByLink=True, approvalLinkHours=1,
               approvalLinkMaxAmount="100000"):
        submit_kaizen(make_client, api_database, "One hour")
        run_jobs(make_client)
        hours = sql(api_database, "select extract(epoch from expires_at - created_at) / 3600 "
                    "from erp.approval_link order by created_at desc limit 1")[0][0]
        assert round(float(hours)) == 1
    mail.sent.clear()
    submit_kaizen(make_client, api_database, "Switched off")
    run_jobs(make_client)
    assert token_in(mail, "Switched off") is None


def test_a_large_amount_needs_a_sign_in_not_a_link(make_client, api_database, mail):
    with Rules(make_client, approvalByLink=True):  # the default cap is 50,000; this kaizen is 60,000
        submit_kaizen(make_client, api_database, "Big saving")
        run_jobs(make_client)
        assert any("Big saving" in subject for _, subject, _ in mail.sent)
        assert token_in(mail, "Big saving") is None
