"""Notifications: in-app, e-mail and WhatsApp, the relay, the SLA reminders and the failures."""

import asyncio

import httpx
import psycopg
import pytest

from heyyerp_api import db
from heyyerp_api.notify import senders
from heyyerp_api.notify.jobs import LOCK_KEY, run_cycle

from .conftest import API_DB, API_PASSWORD, API_ROLE
from .dbutil import url_for
from .test_compound_batches import Fresh
from .test_costing import Rules
from .test_hr import decide
from .test_kaizen import KZ, idea, kaizen_world, person

pytestmark = pytest.mark.usefixtures("api_database")
N = "/api/v1/notifications"
ADMIN = "/api/v1/admin"


class Fake:
    """A sender that keeps what it was given, or fails on purpose."""

    def __init__(self, fail=False):
        self.fail, self.sent = fail, []

    async def send(self, to, subject, body):
        if self.fail:
            raise RuntimeError("smtp is down")
        self.sent.append((to, subject, body))


@pytest.fixture(autouse=True)
def quiet_queue(api_database):
    """Earlier test files leave messages waiting; these tests count what they send themselves."""
    sql(api_database, "update erp.notification set status = 'SENT' where status = 'QUEUED' "
        "and channel <> 'IN_APP'")
    sql(api_database, "update erp.notification set read_at = now() where read_at is null")
    sql(api_database, "update erp.outbox_event set published_at = now() where published_at is null")


@pytest.fixture
def mail(monkeypatch):
    fake = Fake()
    monkeypatch.setitem(senders.override, "EMAIL", fake)
    return fake


def sql(api_database, query, *params):
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else None


def submit_kaizen(make_client, api_database, title="Cut flash on press 7"):
    w = kaizen_world(make_client, api_database)
    me = person(make_client, api_database, w)
    k = me.post(KZ, json=idea(title=title)).json()
    return w, me, k


def run_jobs(make_client):
    done = Fresh(make_client, "ADMIN").post(f"{ADMIN}/jobs/run")
    assert done.status_code == 200, done.text
    return done.json()


def test_an_approval_reaches_the_approver_in_app_and_by_email_and_the_answer_comes_back(
        make_client, api_database, mail):
    w, me, k = submit_kaizen(make_client, api_database)
    assert Fresh(make_client, "PROD").get(f"{N}/count").json()["unread"] == 0  # nothing relayed yet
    stats = run_jobs(make_client)
    assert stats["relayed"] >= 1 and stats["sent"] >= 1
    inbox = Fresh(make_client, "PROD").get(N).json()
    hit = next(n for n in inbox if "Cut flash on press 7" in (n["subject"] or ""))
    assert hit["subject"].startswith("Approval needed:") and hit["read_at"] is None
    assert "/approvals/" in hit["body"] and hit["reference_type"] == "approval"
    assert any(subject.startswith("Approval needed:") and "Cut flash" in subject
               for _, subject, _ in mail.sent)
    again = run_jobs(make_client)
    assert again["sent"] == 0 and again["relayed"] == 0  # a second run changes nothing
    boss = Fresh(make_client, "PROD")
    assert boss.get(f"{N}/count").json()["unread"] >= 1
    assert boss.post(f"{N}/{hit['notification_id']}/read").json()["read_at"]
    assert boss.get(f"{N}?unread=true").json() != inbox  # the read one is gone from the unread list
    assert Fresh(make_client, "SALES").post(f"{N}/{hit['notification_id']}/read").status_code == 404
    assert boss.post(f"{N}/read-all").json() == {"unread": 0}
    decide(make_client, k["approval_id"], "PROD")
    decide(make_client, k["approval_id"], "PLANT_HEAD", "REJECT")
    run_jobs(make_client)
    mine = me.get(N).json()
    assert any(n["subject"].endswith("was rejected") for n in mine)  # the submitter is told


def test_a_message_that_cannot_be_sent_is_retried_then_failed_and_can_be_retried(
        make_client, api_database, monkeypatch):
    broken = Fake(fail=True)
    monkeypatch.setitem(senders.override, "EMAIL", broken)
    submit_kaizen(make_client, api_database, "Retry me")
    with Rules(make_client, notifyMaxAttempts=2):
        first = run_jobs(make_client)
        assert first["retried"] >= 1 and first["failed"] == 0
        sql(api_database, "update erp.notification set next_attempt_at = now() - interval '1 min' "
            "where status = 'QUEUED' and channel = 'EMAIL'")
        second = run_jobs(make_client)
        assert second["failed"] >= 1
    jobs = Fresh(make_client, "ADMIN").get(f"{ADMIN}/jobs").json()
    bad = [f for f in jobs["failures"] if f["status"] == "FAILED" and "smtp is down" in (
        f["last_error"] or "")]
    assert bad and bad[0]["channel"] == "EMAIL" and bad[0]["attempt_count"] == 2
    assert jobs["queue"].get("EMAIL FAILED", 0) >= 1 and jobs["queue"].get("IN_APP SENT", 0) >= 1
    fixed = Fake()
    monkeypatch.setitem(senders.override, "EMAIL", fixed)
    assert Fresh(make_client, "ADMIN").post(
        f"{ADMIN}/notifications/{bad[0]['notification_id']}/retry").status_code == 200
    run_jobs(make_client)
    assert fixed.sent  # delivered after the retry
    assert Fresh(make_client, "ADMIN").post(
        f"{ADMIN}/notifications/{bad[0]['notification_id']}/retry").status_code == 409  # sent now
    assert Fresh(make_client, "SALES").get(f"{ADMIN}/jobs").status_code == 403
    assert Fresh(make_client, "SALES").post(f"{ADMIN}/jobs/run").status_code == 403


def test_a_person_without_an_address_is_skipped_not_retried_forever(
        make_client, api_database, mail):
    sql(api_database, "update erp.app_user set email = null where auth_subject = 'u-PROD'")
    try:
        submit_kaizen(make_client, api_database, "No address")
        run_jobs(make_client)
        skipped = sql(api_database, "select last_error from erp.notification n join erp.app_user u "
                      "on u.user_id = n.recipient_user_id where u.auth_subject = 'u-PROD' and "
                      "n.channel = 'EMAIL' and n.status = 'SKIPPED' limit 1")
        assert skipped and "no usable e-mail" in skipped[0][0]
    finally:
        sql(api_database, "update erp.app_user set email = 't@vrub.example' where auth_subject = "
            "'u-PROD'")


def test_sla_reminders_use_the_channels_the_company_turns_on(make_client, api_database, mail,
                                                              monkeypatch):
    whatsapp = Fake()
    monkeypatch.setitem(senders.override, "WHATSAPP", whatsapp)
    sql(api_database, "update erp.app_user set phone = '98765 43210' where auth_subject = "
        "'u-PROD'")
    w, me, k = submit_kaizen(make_client, api_database, "Late one")
    sql(api_database, "update erp.approval_instance set submitted_at = now() - interval "
        "'160 hours' where approval_id = %s", k["approval_id"])  # 95% of the 168 hours of R-KZ
    run_jobs(make_client)
    assert whatsapp.sent == []  # WhatsApp is off until the company turns it on
    with Rules(make_client, notifyWhatsapp=True):
        run_jobs(make_client)
    assert any(to == "919876543210" and subject.startswith("Urgent reminder")
               for to, subject, _ in whatsapp.sent)
    assert not any("Overdue" in subject for _, subject, _ in whatsapp.sent)  # not overdue yet
    assert any(s.startswith("Reminder:") for _, s, _ in mail.sent)  # the first reminder by e-mail
    templates = {t for (t,) in sql(api_database, "select template_code from erp.notification "
                                   "where reference_id = %s", k["approval_id"])}
    assert {"APPROVAL_REMINDER_50:1", "APPROVAL_REMINDER_90:1"} <= templates
    with Rules(make_client, notifyEmail=False):  # in-app stays, e-mail stops
        sql(api_database, "delete from erp.notification where reference_id = %s and channel = "
            "'EMAIL'", k["approval_id"])
        run_jobs(make_client)
        assert sql(api_database, "select count(*) from erp.notification where reference_id = %s "
                   "and channel = 'EMAIL'", k["approval_id"])[0][0] == 0


def test_whatsapp_numbers_are_made_international_and_bad_ones_are_refused():
    assert senders.whatsapp_number("98765 43210") == "919876543210"
    assert senders.whatsapp_number("+91 (98765) 43210") == "919876543210"
    assert senders.whatsapp_number("447911123456") == "447911123456"
    for bad in (None, "", "abc", "12345", "1" * 20, "98765-43210x"):
        assert senders.whatsapp_number(bad) is None


class Cloud:
    """A stand-in for the HTTP client that records the request and answers as told."""

    calls = []
    status = 200

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        Cloud.calls.append((url, json, headers))
        return httpx.Response(Cloud.status, text="refused" if Cloud.status >= 300 else "ok")


def test_the_whatsapp_cloud_sender_posts_a_template_message_and_reports_a_refusal(
        monkeypatch):
    from heyyerp_api.config import get_settings  # noqa: PLC0415

    monkeypatch.setenv("WHATSAPP_PROVIDER", "cloud")
    monkeypatch.setenv("WHATSAPP_TOKEN", "secret-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "12345")
    get_settings.cache_clear()
    monkeypatch.setattr(senders.httpx, "AsyncClient", Cloud)
    Cloud.calls.clear()
    sender = senders.sender_for("WHATSAPP")
    assert isinstance(sender, senders.WhatsAppCloudSender)
    asyncio.run(sender.send("919876543210", "Urgent reminder: PO 1", "Please decide.\nnow"))
    url, body, headers = Cloud.calls[0]
    assert url.endswith("/12345/messages") and body["to"] == "919876543210"
    assert body["template"]["name"] == "heyyerp_alert"
    texts = [p["text"] for p in body["template"]["components"][0]["parameters"]]
    assert texts == ["Urgent reminder: PO 1", "Please decide. now"]
    assert headers["Authorization"] == "Bearer secret-token"
    Cloud.status = 400
    try:
        with pytest.raises(RuntimeError) as refused:
            asyncio.run(sender.send("919876543210", "x", "y"))
        assert "refused" in str(refused.value) and "secret-token" not in str(refused.value)
    finally:
        Cloud.status = 200
        get_settings.cache_clear()


def test_only_one_worker_runs_at_a_time_and_a_cycle_processes_the_company(
        make_client, api_database, mail):
    submit_kaizen(make_client, api_database, "By the worker")
    url = url_for(API_DB, API_ROLE, API_PASSWORD).replace("postgresql://", "postgresql+asyncpg://")
    db.configure_database(url)  # a pool for this test's own event loop
    async def both():
        with psycopg.connect(api_database["owner"], autocommit=True) as holder:
            holder.execute("select pg_advisory_lock(%s)", (LOCK_KEY,))
            try:
                blocked = await run_cycle()
            finally:
                holder.execute("select pg_advisory_unlock(%s)", (LOCK_KEY,))
        return blocked, await run_cycle()

    blocked, stats = asyncio.run(both())  # one event loop for both, as the server has
    assert blocked == {"skipped": "another worker is running"}
    assert list(stats.values())[0]["relayed"] >= 1  # the lock is free: the cycle ran
    assert any("By the worker" in subject for _, subject, _ in mail.sent)
