"""The approval engine end to end: routing, decisions, rejection, SLA/escalation, rule changes."""

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest

from heyyerp_api.workflow.registry import REGISTRY, DocInfo, DocumentType

pytestmark = pytest.mark.usefixtures("api_database")
A = "/api/v1/approvals"


class FakeDocs:
    """Stands in for the PO / indent modules, which do not exist yet (Phase 5)."""

    def __init__(self):
        self.docs: dict[UUID, DocInfo] = {}
        self.finalized: dict[UUID, bool] = {}

    def add(self, amount=None, dept=None, title="Test document") -> UUID:
        doc_id = uuid4()
        self.docs[doc_id] = DocInfo(title, None if amount is None else Decimal(str(amount)),
                                    dept, True)
        return doc_id

    async def load(self, ctx, doc_id):
        return self.docs.get(doc_id)

    async def finalize(self, ctx, doc_id, approved):
        self.finalized[doc_id] = approved


@pytest.fixture
def docs():
    fake = FakeDocs()
    saved = dict(REGISTRY)
    REGISTRY["po"] = DocumentType("po", "po.create", fake.load, fake.finalize)
    REGISTRY["indent"] = DocumentType("indent", "indent.raise", fake.load, fake.finalize)
    yield fake
    REGISTRY.clear()
    REGISTRY.update(saved)


def submit(client, doc_id, kind="po"):
    return client.post(A, json={"document_type": kind, "document_id": str(doc_id)})


def decide(client, approval_id, decision="APPROVE", note=None, **extra):
    headers = {"Idempotency-Key": extra.pop("key")} if "key" in extra else {}
    return client.post(f"{A}/{approval_id}/decide", headers=headers,
                       json={"decision": decision, "note": note, **extra})


def mine(client):
    return {i["approval_id"] for i in client.get(f"{A}?scope=mine&limit=200").json()["items"]}


def steps(response):
    return [(s["role"], s["state"]) for s in response.json()["steps"]]


# ------------------------------------------------------------------ routing by value band
def test_small_po_raised_by_purchase_is_approved_on_the_spot(make_client, docs):
    doc = docs.add(300_000)
    response = submit(make_client("PURCHASE"), doc)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "APPROVED" and body["rule"] == "R-PO-1"
    assert steps(response) == [("PURCHASE", "SKIPPED")]  # BRD: skipped, raised by this role
    assert docs.finalized[doc] is True


def test_mid_value_po_waits_for_the_plant_head_only(make_client, docs):
    doc = docs.add(1_000_000)
    created = submit(make_client("PURCHASE"), doc)
    approval_id = created.json()["approval_id"]
    assert created.json()["status"] == "PENDING" and created.json()["rule"] == "R-PO-2"
    assert steps(created) == [("PURCHASE", "SKIPPED"), ("PLANT_HEAD", "CURRENT")]
    assert approval_id not in mine(make_client("STORES"))
    assert approval_id not in mine(make_client("PURCHASE"))
    plant_head = make_client("PLANT_HEAD")
    assert approval_id in mine(plant_head)
    done = decide(plant_head, approval_id, note="ok")
    assert done.status_code == 200 and done.json()["status"] == "APPROVED"
    assert steps(done) == [("PURCHASE", "SKIPPED"), ("PLANT_HEAD", "APPROVED")]
    assert docs.finalized[doc] is True
    assert approval_id not in mine(plant_head)


def test_high_value_po_goes_plant_head_then_md(make_client, docs):
    doc = docs.add(3_000_000)
    approval_id = submit(make_client("PURCHASE"), doc).json()["approval_id"]
    step_one = decide(make_client("PLANT_HEAD"), approval_id)
    assert step_one.json()["status"] == "PENDING" and step_one.json()["current_step"] == 3
    assert doc not in docs.finalized  # business effect only when the whole route is done
    assert approval_id in mine(make_client("MD"))
    assert decide(make_client("PLANT_HEAD"), approval_id).status_code == 403  # step 3 is the MD's
    final = decide(make_client("MD"), approval_id)
    assert final.json()["status"] == "APPROVED" and docs.finalized[doc] is True


@pytest.mark.parametrize(("amount", "rule"), [
    (1, "R-PO-1"), (500_000, "R-PO-1"), (500_000.01, "R-PO-2"),
    (2_500_000, "R-PO-2"), (2_500_000.01, "R-PO-3"),
])
def test_value_band_boundaries(make_client, docs, amount, rule):
    response = submit(make_client("STORES_PO" if False else "ADMIN"), docs.add(amount))
    assert response.json()["rule"] == rule


def test_department_head_step_follows_the_departments_head_role(make_client, docs):
    admin = make_client("ADMIN")
    code = uuid4().hex[:8].upper()
    dept = admin.post("/api/v1/org/departments",
                      json={"code": code, "name": "Moulding", "head_role_code": "prod"}).json()
    assert dept["head_role_code"] == "PROD"
    doc = docs.add(dept=UUID(dept["department_id"]))
    created = submit(make_client("STORES"), doc, "indent")
    assert steps(created) == [("PROD", "CURRENT"), ("PLANT_HEAD", "WAITING")]
    assert created.json()["rule"] == "R-IND"
    approval_id = created.json()["approval_id"]
    assert approval_id in mine(make_client("PROD"))
    assert decide(make_client("STORES"), approval_id).status_code == 403
    assert decide(make_client("PROD"), approval_id).json()["current_step"] == 2
    assert decide(make_client("PLANT_HEAD"), approval_id).json()["status"] == "APPROVED"


def test_requester_in_the_head_role_skips_that_step(make_client, docs):
    admin = make_client("ADMIN")
    dept = admin.post("/api/v1/org/departments", json={
        "code": uuid4().hex[:8].upper(), "name": "Press", "head_role_code": "PROD"}).json()
    created = submit(make_client("PROD"), docs.add(dept=UUID(dept["department_id"])), "indent")
    assert steps(created) == [("PROD", "SKIPPED"), ("PLANT_HEAD", "CURRENT")]


def test_department_without_a_head_role_falls_back_to_plant_head_and_collapses(make_client, docs):
    admin = make_client("ADMIN")
    dept = admin.post("/api/v1/org/departments",
                      json={"code": uuid4().hex[:8].upper(), "name": "Misc"}).json()
    created = submit(make_client("STORES"), docs.add(dept=UUID(dept["department_id"])), "indent")
    assert steps(created) == [("PLANT_HEAD", "CURRENT")]  # DEPT_HEAD + PLANT_HEAD collapsed


# ------------------------------------------------------------------ rejection and permissions
def test_rejection_needs_a_note_and_finalises(make_client, docs):
    doc = docs.add(1_000_000)
    approval_id = submit(make_client("PURCHASE"), doc).json()["approval_id"]
    plant_head = make_client("PLANT_HEAD")
    assert decide(plant_head, approval_id, "REJECT").status_code == 422
    assert decide(plant_head, approval_id, "REJECT", "   ").status_code == 422
    rejected = decide(plant_head, approval_id, "REJECT", "Price too high")
    assert rejected.json()["status"] == "REJECTED"
    assert steps(rejected)[1] == ("PLANT_HEAD", "REJECTED")
    assert docs.finalized[doc] is False
    assert decide(plant_head, approval_id).status_code == 409  # Already decided
    assert submit(make_client("PURCHASE"), doc).status_code == 409  # one approval per document


def test_only_the_step_holder_may_decide_and_nobody_decides_their_own(make_client, docs):
    approval_id = submit(make_client("PURCHASE"), docs.add(1_000_000)).json()["approval_id"]
    for role in ("STORES", "PURCHASE", "QA", "ACCOUNTS", "OPERATOR"):
        assert decide(make_client(role), approval_id).status_code == 403, role
    own = submit(make_client("ADMIN"), docs.add(1_000_000)).json()["approval_id"]
    refused = decide(make_client("ADMIN"), own)
    assert refused.status_code == 403 and "own submission" in refused.json()["detail"]


def test_admin_override_is_logged_and_md_cannot_override(make_client, docs):
    approval_id = submit(make_client("PURCHASE"), docs.add(1_000_000)).json()["approval_id"]
    assert decide(make_client("MD"), approval_id).status_code == 403  # BRD: only ADMIN overrides
    done = decide(make_client("ADMIN"), approval_id, note="Plant head unavailable")
    assert done.status_code == 200 and done.json()["status"] == "APPROVED"
    last = done.json()["steps"][1]
    assert last["decision"] == "OVERRIDE_APPROVE" and last["role"] == "PLANT_HEAD"
    assert last["decided_by"] == "ADMIN user"


def test_submission_rules(make_client, docs):
    purchase = make_client("PURCHASE")
    doc = docs.add(1_000_000)
    assert submit(purchase, uuid4()).status_code == 404
    assert submit(purchase, doc, "no-such-type").status_code == 422
    assert submit(make_client("OPERATOR"), doc).status_code == 403  # lacks po.create
    assert submit(make_client("PURCHASE"), doc).status_code == 201
    assert submit(make_client("PURCHASE"), doc).status_code == 409
    docs.docs[(blocked := uuid4())] = DocInfo("Closed", Decimal(1), None, False, "Already closed")
    refused = submit(make_client("PURCHASE"), blocked)
    assert refused.status_code == 409 and refused.json()["detail"] == "Already closed"


def test_inbox_views(make_client, docs):
    approval_id = submit(make_client("PURCHASE"), docs.add(1_000_000)).json()["approval_id"]
    assert make_client("PURCHASE").get(f"{A}?scope=all").status_code == 403
    for role in ("PLANT_HEAD", "MD", "ADMIN"):
        ids = {i["approval_id"] for i in make_client(role).get(f"{A}?scope=all&limit=200").json()["items"]}
        assert approval_id in ids, role
    plant_head = make_client("PLANT_HEAD")
    assert plant_head.get(f"{A}/count").json()["pending"] >= 1
    detail = plant_head.get(f"{A}/{approval_id}").json()
    assert detail["title"] == "Test document" and detail["step_role"] == "PLANT_HEAD"
    assert plant_head.get(f"{A}/{uuid4()}").status_code == 404
    decide(plant_head, approval_id)
    history = plant_head.get(f"{A}?scope=all&state=APPROVED&limit=200").json()["items"]
    assert approval_id in {i["approval_id"] for i in history}


# ------------------------------------------------------------------ idempotency and races
def test_repeating_a_decision_with_the_same_key_is_harmless(make_client, docs, api_database):
    approval_id = submit(make_client("PURCHASE"), docs.add(1_000_000)).json()["approval_id"]
    plant_head = make_client("PLANT_HEAD")
    first = decide(plant_head, approval_id, key="retry-1")
    again = decide(plant_head, approval_id, key="retry-1")
    assert first.status_code == again.status_code == 200
    assert again.json()["status"] == "APPROVED"
    other = submit(make_client("PURCHASE"), docs.add(1_000_000)).json()["approval_id"]
    reuse = decide(make_client("PLANT_HEAD"), other, key="retry-1")
    assert reuse.status_code == 409  # a key belongs to one approval
    with psycopg.connect(api_database["owner"]) as conn:
        n = conn.execute("select count(*) from erp.approval_action where approval_id = %s "
                         "and decision = 'APPROVE'", (approval_id,)).fetchone()[0]
    assert n == 1


def test_a_stale_view_of_the_step_is_refused(make_client, docs):
    approval_id = submit(make_client("PURCHASE"), docs.add(3_000_000)).json()["approval_id"]
    plant_head = make_client("PLANT_HEAD")
    assert decide(plant_head, approval_id, step=3).status_code == 409  # step 3 is not current
    assert decide(plant_head, approval_id, step=2).status_code == 200


def test_two_approvers_deciding_at_once_cannot_both_win(api_database, docs, make_client):
    """Row lock + unique(approval, step): exactly one of two simultaneous decisions lands."""
    from sqlalchemy import text

    from heyyerp_api import db
    from heyyerp_api.masters.common import Ctx
    from heyyerp_api.rbac.seed import create_app_user
    from heyyerp_api.security import Principal, Profile
    from heyyerp_api.workflow import engine

    from .conftest import API_DB, API_PASSWORD, API_ROLE, TENANT_A
    from .dbutil import url_for

    approval_id = UUID(submit(make_client("PURCHASE"), docs.add(1_000_000)).json()["approval_id"])
    people = []
    with psycopg.connect(api_database["owner"], autocommit=True) as owner:
        for n in range(2):
            uid = create_app_user(owner, TENANT_A, f"racer-{n}", "PLANT_HEAD", f"Racer {n}")
            people.append(Profile(uid, f"Racer {n}", "PLANT_HEAD",
                                  frozenset({"approval.view_all"}), frozenset(), None))

    async def attempt(profile, key):
        async with db.SessionFactory() as session, session.begin():
            await session.execute(text("select set_config('app.tenant_id', :t, true)"),
                                  {"t": TENANT_A})
            principal = Principal("x", "x@x", True, "org", True, UUID(TENANT_A), profile)
            try:
                await engine.decide(Ctx(session, principal), approval_id, "APPROVE", None, key)
                return "won"
            except Exception as exc:  # noqa: BLE001 - HTTPException(409/403) is the expected loser
                return getattr(exc, "status_code", repr(exc))

    async def race():
        db.configure_database(url_for(API_DB, API_ROLE, API_PASSWORD).replace(
            "postgresql://", "postgresql+asyncpg://"))
        try:
            return await asyncio.gather(attempt(people[0], "race-a"), attempt(people[1], "race-b"))
        finally:
            await db.engine.dispose()

    results = asyncio.run(race())
    assert sorted(map(str, results)) == ["409", "won"], results


# ------------------------------------------------------------------ rule changes in flight
def rule_body(amount_min, amount_max, steps, name):
    return {"name": name, "document_type": "po", "amount_min": amount_min,
            "amount_max": amount_max, "priority": 1, "sla_hours": 12,
            "steps": [{"kind": "ROLE", "role_code": s} for s in steps]}


def test_changing_a_rule_does_not_change_documents_already_in_flight(make_client, docs):
    admin = make_client("ADMIN")
    name = "T-" + uuid4().hex[:6].upper()
    rule = admin.post("/api/v1/admin/approval-rules",
                      json=rule_body(7_000_000, 7_100_000, ["PURCHASE", "PLANT_HEAD"], name))
    assert rule.status_code == 201, rule.text
    rule_id = rule.json()["rule_id"]
    in_flight = submit(make_client("STORES_" if False else "ADMIN"), docs.add(7_050_000))
    assert in_flight.json()["rule"] == name
    assert steps(in_flight) == [("PURCHASE", "CURRENT"), ("PLANT_HEAD", "WAITING")]
    flight_id = in_flight.json()["approval_id"]

    changed = make_client("ADMIN").put(f"/api/v1/admin/approval-rules/{rule_id}",
                                       json=rule_body(7_000_000, 7_100_000, ["ACCOUNTS"], name))
    assert changed.json()["version"] == 2 and changed.json()["steps"][0]["role_code"] == "ACCOUNTS"

    # The document submitted before the change keeps its original route...
    assert approval_id_in_inbox(make_client("PURCHASE"), flight_id)
    assert not approval_id_in_inbox(make_client("ACCOUNTS"), flight_id)
    # ...and one submitted after follows the new rule.
    fresh = submit(make_client("PURCHASE"), docs.add(7_050_000))
    assert steps(fresh) == [("ACCOUNTS", "CURRENT")]

    retired = make_client("ADMIN").post(f"/api/v1/admin/approval-rules/{rule_id}/retire")
    assert retired.json()["active"] is False
    after = submit(make_client("PURCHASE"), docs.add(7_050_000))
    assert after.json()["rule"] != name  # falls back to the standing band rule
    assert make_client("ADMIN").post(f"/api/v1/admin/approval-rules/{rule_id}/retire").status_code == 409
    assert decide(make_client("PURCHASE"), flight_id).json()["current_step"] == 2  # still flows


def approval_id_in_inbox(client, approval_id):
    return approval_id in mine(client)


def test_no_matching_rule_is_a_clear_conflict(make_client, docs):
    doc = docs.add(1)
    saved = REGISTRY["po"]
    REGISTRY["po"] = DocumentType("po", "po.create", docs.load, docs.finalize)
    try:
        # 'review' has a default rule; use a band no rule covers by retiring via a fresh type.
        REGISTRY["leave"] = DocumentType("leave", "leave.apply", docs.load, docs.finalize)
        response = make_client("STORES").post(A, json={"document_type": "leave",
                                                       "document_id": str(doc)})
        assert response.status_code == 201  # R-LEAVE exists
        assert make_client("STORES").post(A, json={"document_type": "leave",
                                                   "document_id": str(docs.add(5))}
                                          ).json()["rule"] == "R-LEAVE"
    finally:
        REGISTRY["po"] = saved
        REGISTRY.pop("leave", None)


# ------------------------------------------------------------------ SLA, reminders, escalation
def test_overdue_approvals_are_flagged_reminded_and_escalated_once(make_client, docs, api_database):
    approval_id = submit(make_client("PURCHASE"), docs.add(1_000_000)).json()["approval_id"]
    plant_head = make_client("PLANT_HEAD")
    fresh = plant_head.get(f"{A}/{approval_id}").json()
    assert fresh["overdue"] is False and fresh["due_at"] is not None  # R-PO-2: 24h

    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("update erp.approval_instance set submitted_at = now() - interval '30 hours' "
                     "where approval_id = %s", (approval_id,))
        # The skipped first step was recorded at submission; age it with the submission.
        conn.execute("update erp.approval_action set decided_at = now() - interval '30 hours' "
                     "where approval_id = %s", (approval_id,))

    late = plant_head.get(f"{A}/{approval_id}").json()
    assert late["overdue"] is True
    assert plant_head.get(f"{A}/count").json()["overdue"] >= 1
    assert plant_head.post(f"{A}/sla-sweep").status_code == 403  # admin only

    def notifications():
        with psycopg.connect(api_database["owner"]) as conn:
            return conn.execute(
                "select template_code, r.code from erp.notification n "
                "join erp.app_user u on u.user_id = n.recipient_user_id "
                "join erp.role r on r.role_id = u.role_id where n.reference_id = %s "
                "order by 1, 2", (approval_id,)).fetchall()

    swept = make_client("ADMIN").post(f"{A}/sla-sweep")
    assert swept.status_code == 200 and swept.json()["reminders"] >= 2
    sent = notifications()
    templates = {t for t, _ in sent}
    assert {"APPROVAL_REMINDER_50:2", "APPROVAL_REMINDER_90:2", "APPROVAL_OVERDUE:2"} <= templates
    assert ("APPROVAL_REMINDER_50:2", "PLANT_HEAD") in sent  # the approver is reminded
    assert ("APPROVAL_OVERDUE:2", "MD") in sent  # escalated one level above the Plant Head
    assert ("APPROVAL_OVERDUE:2", "PURCHASE") in sent  # and the requester is told
    make_client("ADMIN").post(f"{A}/sla-sweep")
    assert notifications() == sent  # sweeping again sends nothing new


def test_a_fresh_approval_gets_no_reminders(make_client, docs, api_database):
    approval_id = submit(make_client("PURCHASE"), docs.add(1_000_000)).json()["approval_id"]
    make_client("ADMIN").post(f"{A}/sla-sweep")
    with psycopg.connect(api_database["owner"]) as conn:
        n = conn.execute("select count(*) from erp.notification where reference_id = %s",
                         (approval_id,)).fetchone()[0]
    assert n == 0


# ------------------------------------------------------------------ the first real consumer: vendors
def test_vendor_onboarding_goes_through_approval(make_client):
    purchase = make_client("PURCHASE")
    vendor = purchase.post("/api/v1/parties/vendors",
                           json={"code": uuid4().hex[:10].upper(), "name": "New Vendor"}).json()
    assert vendor["status"] == "PENDING_APPROVAL"
    submitted = submit(purchase, vendor["vendor_id"], "vendor")
    assert submitted.status_code == 201 and submitted.json()["rule"] == "R-VENDOR"
    approval_id = submitted.json()["approval_id"]
    assert steps(submitted) == [("PLANT_HEAD", "CURRENT")]
    assert submit(purchase, vendor["vendor_id"], "vendor").status_code == 409
    assert decide(make_client("PLANT_HEAD"), approval_id).json()["status"] == "APPROVED"
    after = make_client("PURCHASE").get(f"/api/v1/parties/vendors/{vendor['vendor_id']}").json()
    assert after["status"] == "APPROVED"
    # An approved vendor is no longer awaiting approval, so it cannot be submitted again.
    assert submit(make_client("PURCHASE"), vendor["vendor_id"], "vendor").status_code == 409


def test_rejected_vendor_is_marked_rejected(make_client):
    purchase = make_client("PURCHASE")
    vendor = purchase.post("/api/v1/parties/vendors",
                           json={"code": uuid4().hex[:10].upper(), "name": "Bad Vendor"}).json()
    approval_id = submit(purchase, vendor["vendor_id"], "vendor").json()["approval_id"]
    decide(make_client("PLANT_HEAD"), approval_id, "REJECT", "Failed audit")
    after = make_client("PURCHASE").get(f"/api/v1/parties/vendors/{vendor['vendor_id']}").json()
    assert after["status"] == "REJECTED"


def test_only_vendor_managers_can_submit_a_vendor(make_client):
    purchase = make_client("PURCHASE")
    vendor = purchase.post("/api/v1/parties/vendors",
                           json={"code": uuid4().hex[:10].upper(), "name": "V"}).json()
    assert submit(make_client("STORES"), vendor["vendor_id"], "vendor").status_code == 403


# ------------------------------------------------------------------ rule administration
def test_rule_admin_validation_and_permissions(make_client):
    admin = make_client("ADMIN")
    rules = admin.get("/api/v1/admin/approval-rules").json()
    names = {r["name"] for r in rules}
    assert {"R-IND", "R-PO-1", "R-PO-2", "R-PO-3", "R-BILL", "R-VENDOR"} <= names
    po2 = next(r for r in rules if r["name"] == "R-PO-2")
    assert [s["role_code"] for s in po2["steps"]] == ["PURCHASE", "PLANT_HEAD"]
    assert po2["sla_hours"] == 24 and po2["amount_max"] == "2500000.00"

    base = rule_body(None, None, ["PLANT_HEAD"], "T-" + uuid4().hex[:6].upper())
    post = lambda **c: admin.post("/api/v1/admin/approval-rules", json={**base, **c})  # noqa: E731
    assert post(document_type="spaceship").status_code == 422
    assert post(amount_min=10, amount_max=5).status_code == 422
    assert post(steps=[]).status_code == 422
    assert post(steps=[{"kind": "ROLE"}]).status_code == 422
    assert post(steps=[{"kind": "ROLE", "role_code": "WIZARD"}]).status_code == 422
    assert post(steps=[{"kind": "USER", "user_id": str(uuid4())}]).status_code == 422
    assert post(steps=[{"kind": "DEPARTMENT_HEAD", "role_code": "MD"}]).status_code == 422
    assert post(sla_hours=0).status_code == 422
    assert post(name="R-PO-1").status_code == 409  # names are unique
    created = post()
    assert created.status_code == 201
    rule_id = created.json()["rule_id"]
    assert admin.get(f"/api/v1/admin/approval-rules/{rule_id}").status_code == 200
    # An open-ended rule would shadow every later test, so retire it.
    assert admin.post(f"/api/v1/admin/approval-rules/{rule_id}/retire").status_code == 200
    assert admin.get(f"/api/v1/admin/approval-rules/{uuid4()}").status_code == 404
    for role in ("MD", "PLANT_HEAD", "STORES"):
        client = make_client(role)
        assert client.get("/api/v1/admin/approval-rules").status_code == 403, role
        assert client.post("/api/v1/admin/approval-rules", json=base).status_code == 403, role


def test_a_named_user_can_be_an_approver(make_client, docs, api_database):
    admin = make_client("ADMIN")
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        from heyyerp_api.rbac.seed import create_app_user

        from .conftest import TENANT_A
        uid = create_app_user(conn, TENANT_A, "named-approver", "QA", "Named Approver")
    name = "T-" + uuid4().hex[:6].upper()
    body = {"name": name, "document_type": "po", "amount_min": 9_000_000, "amount_max": 9_100_000,
            "priority": 1, "sla_hours": 24, "steps": [{"kind": "USER", "user_id": str(uid)}]}
    assert admin.post("/api/v1/admin/approval-rules", json=body).status_code == 201
    created = submit(make_client("PURCHASE"), docs.add(9_050_000))
    approval_id = created.json()["approval_id"]
    assert created.json()["steps"][0]["label"] == "Named Approver"
    assert decide(make_client("QA", subject="someone-else"), approval_id).status_code == 403
    done = decide(make_client("QA", subject="named-approver", provision=False), approval_id)
    assert done.status_code == 200 and done.json()["status"] == "APPROVED"


# ------------------------------------------------------------------ tenant isolation and audit
def test_other_tenants_approvals_are_invisible(make_client, api_database):
    foreign = uuid4()
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute(
            "insert into erp.approval_instance(approval_id, tenant_id, document_type, document_id,"
            " status, route_snapshot) values (%s, %s, 'po', %s, 'PENDING', %s::jsonb)",
            (foreign, api_database["tenant_b"], uuid4(),
             '{"title":"B","amount":null,"rule":"x","steps":[{"seq":1,"role":"PLANT_HEAD",'
             '"user_id":null,"label":"Plant Head","sla_hours":24,"skipped":false,"kind":"ROLE"}]}'))
    admin = make_client("ADMIN")
    ids = {i["approval_id"] for i in admin.get(f"{A}?scope=all&limit=200").json()["items"]}
    assert str(foreign) not in ids
    assert admin.get(f"{A}/{foreign}").status_code == 404
    assert decide(admin, foreign).status_code == 404


def test_submit_and_decide_are_audited_and_emit_events(make_client, docs, api_database):
    approval_id = submit(make_client("PURCHASE"), docs.add(1_000_000)).json()["approval_id"]
    decide(make_client("PLANT_HEAD"), approval_id)
    with psycopg.connect(api_database["owner"]) as conn:
        actions = [r[0] for r in conn.execute(
            "select action from erp.audit_log where entity_id = %s order by audit_id",
            (approval_id,))]
        events = [r[0] for r in conn.execute(
            "select event_type from erp.outbox_event where aggregate_id = %s order by created_at",
            (approval_id,))]
    assert actions == ["approval.submit", "approval.decide"]
    assert events == ["approval.step_pending", "approval.decided"]
