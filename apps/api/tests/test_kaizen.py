"""Kaizen suggestions: submission, two-step approval, evaluation and savings."""

from datetime import date
from decimal import Decimal

import pytest

from .test_compound_batches import Fresh
from .test_hr import decide, employee, user_of
from .test_inventory_receipt import code

pytestmark = pytest.mark.usefixtures("api_database")
KZ = "/api/v1/kaizen"
NOBODY = "00000000-0000-4000-8000-000000000000"


def kaizen_world(make_client, api_database):
    admin = Fresh(make_client, "ADMIN")
    unit = admin.post("/api/v1/org/units", json={"code": code(), "name": "Plant"}).json()
    dept = admin.post("/api/v1/org/departments", json={
        "code": code(), "name": "Moulding", "unit_id": unit["unit_id"],
        "head_role_code": "PROD"}).json()  # the department head approves as PROD
    return {"unit": unit, "dept": dept, "hr": Fresh(make_client, "HR")}


def person(make_client, api_database, w, role="SALES"):
    client, uid = user_of(make_client, api_database, role)
    employee(w, user_id=uid)
    return client


def idea(**extra):
    return {"title": "Cut flash on press 3", "category": "QUALITY", "baseline_value": "4.5",
            "problem_description": "Flash on the parting line", "proposed_change": "New vents",
            "estimated_saving": "60000", **extra}


def test_a_kaizen_goes_through_both_approvers_evaluation_implementation_and_savings(
        make_client, api_database):
    w = kaizen_world(make_client, api_database)
    me = person(make_client, api_database, w)
    made = me.post(KZ, json=idea())
    assert made.status_code == 201, made.text
    k = made.json()
    assert k["kaizen_no"].startswith("KZ-") and k["status"] == "SUBMITTED"
    assert k["department_name"] == "Moulding" and k["approval_id"]
    edited = me.patch(f"{KZ}/{k['kaizen_id']}", json={"title": "Cut flash on press 4"})
    assert edited.status_code == 200 and edited.json()["title"] == "Cut flash on press 4"
    assert Fresh(make_client, "PLANT_HEAD").patch(f"{KZ}/{k['kaizen_id']}", json={
        "title": "Not yours"}).status_code == 403
    step1 = decide(make_client, k["approval_id"], "PROD")  # the department head
    assert step1.status_code == 200, step1.text
    now = me.get(f"{KZ}/{k['kaizen_id']}").json()
    assert now["status"] == "UNDER_EVALUATION"  # derived: the second step is reached
    assert me.patch(f"{KZ}/{k['kaizen_id']}", json={"title": "Too late"}).status_code == 409
    listed = me.get(f"{KZ}?status=under_evaluation&mine=true").json()
    assert [x["kaizen_id"] for x in listed["items"]] == [k["kaizen_id"]]
    plant_head = Fresh(make_client, "PLANT_HEAD")
    assert me.post(f"{KZ}/{k['kaizen_id']}/evaluate", json={
        "score": "8", "verified_saving": "50000"}).status_code == 403  # not an evaluator
    assert plant_head.post(f"{KZ}/{k['kaizen_id']}/evaluate", json={
        "score": "11", "verified_saving": "1"}).status_code == 422  # above the company's scale
    graded = plant_head.post(f"{KZ}/{k['kaizen_id']}/evaluate", json={
        "score": "8", "verified_saving": "50000", "note": "Trial run confirmed it"})
    assert graded.status_code == 200 and Decimal(graded.json()["score"]) == 8
    assert decide(make_client, k["approval_id"], "PLANT_HEAD").status_code == 200
    approved = me.get(f"{KZ}/{k['kaizen_id']}").json()
    assert approved["status"] == "APPROVED" and approved["approved_at"]
    assert plant_head.post(f"{KZ}/{k['kaizen_id']}/evaluate", json={
        "score": "9", "verified_saving": "1"}).status_code == 409  # decided
    done = me.post(f"{KZ}/{k['kaizen_id']}/implemented", json={
        "implemented_on": date.today().isoformat(), "achieved_value": "1.2"})
    assert done.status_code == 200 and done.json()["status"] == "IMPLEMENTED"
    assert me.post(f"{KZ}/{k['kaizen_id']}/implemented", json={
        "implemented_on": date.today().isoformat()}).status_code == 409
    month = date.today().replace(day=1)
    first = plant_head.post(f"{KZ}/{k['kaizen_id']}/savings", json={
        "saving_month": month.isoformat(), "amount": "4000"})
    assert first.status_code == 200 and [Decimal(s["amount"]) for s in first.json()] == [4000]
    again = plant_head.post(f"{KZ}/{k['kaizen_id']}/savings", json={
        "saving_month": month.isoformat(), "amount": "4500", "note": "Corrected"})
    assert [Decimal(s["amount"]) for s in again.json()] == [4500]  # the month is replaced
    assert plant_head.post(f"{KZ}/{k['kaizen_id']}/savings", json={
        "saving_month": f"{month.year + 1}-01-01", "amount": "1"}).status_code == 422
    assert me.post(f"{KZ}/{k['kaizen_id']}/savings", json={
        "saving_month": month.isoformat(), "amount": "1"}).status_code == 403
    assert Decimal(me.get(f"{KZ}/{k['kaizen_id']}").json()["realised_saving"]) == 4500
    summary = me.get(f"{KZ}/summary/year?unit_id={w['unit']['unit_id']}").json()
    assert summary["by_status"] == {"IMPLEMENTED": 1}
    assert Decimal(summary["estimated_saving"]) == 60000 and Decimal(summary[
        "verified_saving"]) == 50000 and Decimal(summary["realised_saving"]) == 4500
    assert summary["top_contributors"][0]["approved"] == 1


def test_a_kaizen_can_be_withdrawn_or_rejected_and_savings_need_approval(
        make_client, api_database):
    w = kaizen_world(make_client, api_database)
    me = person(make_client, api_database, w)
    pulled = me.post(KZ, json=idea()).json()
    gone = me.post(f"{KZ}/{pulled['kaizen_id']}/cancel")
    assert gone.status_code == 200 and gone.json()["status"] == "CANCELLED"
    assert decide(make_client, pulled["approval_id"], "PROD").status_code in (404, 409)
    assert me.post(f"{KZ}/{pulled['kaizen_id']}/cancel").status_code == 409
    refused = me.post(KZ, json=idea(title="A weaker idea")).json()
    assert decide(make_client, refused["approval_id"], "PROD", "REJECT").status_code == 200
    ended = me.get(f"{KZ}/{refused['kaizen_id']}").json()
    assert ended["status"] == "REJECTED"
    plant_head = Fresh(make_client, "PLANT_HEAD")
    assert plant_head.post(f"{KZ}/{refused['kaizen_id']}/savings", json={
        "saving_month": date.today().isoformat(), "amount": "10"}).status_code == 409
    assert me.post(f"{KZ}/{refused['kaizen_id']}/implemented", json={
        "implemented_on": date.today().isoformat()}).status_code == 409
    third = me.post(KZ, json=idea(title="Waiting for review")).json()
    other = person(make_client, api_database, w, "STORES")
    assert other.post(f"{KZ}/{third['kaizen_id']}/cancel").status_code == 403
    assert plant_head.post(f"{KZ}/{third['kaizen_id']}/cancel").status_code == 200  # an evaluator


def test_the_department_head_own_kaizen_skips_to_evaluation(make_client, api_database):
    w = kaizen_world(make_client, api_database)
    head = person(make_client, api_database, w, "PROD")  # the department's head role
    k = head.post(KZ, json=idea()).json()
    assert k["status"] == "UNDER_EVALUATION"  # the first step is theirs, so it is skipped
    assert decide(make_client, k["approval_id"], "PLANT_HEAD").status_code == 200
    assert head.get(f"{KZ}/{k['kaizen_id']}").json()["status"] == "APPROVED"


def test_submission_needs_a_department_and_plant_and_respects_scope(make_client, api_database):
    w = kaizen_world(make_client, api_database)
    loose, _ = user_of(make_client, api_database, "SALES")  # no employee record
    assert loose.post(KZ, json=idea()).status_code == 422
    other = Fresh(make_client, "ADMIN").post("/api/v1/org/units", json={
        "code": code(), "name": "Other plant"}).json()
    assert loose.post(KZ, json=idea(unit_id=other["unit_id"], department_id=w["dept"][
        "department_id"])).status_code == 422  # the department is in another plant
    given = loose.post(KZ, json=idea(unit_id=w["unit"]["unit_id"], department_id=w["dept"][
        "department_id"]))
    assert given.status_code == 201  # unit and department given by hand
    mine = given.json()
    scoped = Fresh(make_client, "SALES", subject="kz-scoped", unit_ids=[other["unit_id"]])
    assert scoped.get(f"{KZ}/{mine['kaizen_id']}").status_code == 404
    assert scoped.get(f"{KZ}?unit_id={w['unit']['unit_id']}").json()["total"] == 0
    assert scoped.post(KZ, json=idea(unit_id=w["unit"]["unit_id"], department_id=w["dept"][
        "department_id"])).status_code == 403
    assert Fresh(make_client, "SALES").get(f"{KZ}/{NOBODY}").status_code == 404
    assert Fresh(make_client, "PLANT_HEAD").post(f"{KZ}/{mine['kaizen_id']}/evaluate", json={
        "score": "-1", "verified_saving": "1"}).status_code == 422


def test_a_rejected_kaizen_is_corrected_and_sent_again_with_the_first_round_kept(
        make_client, api_database):
    w = kaizen_world(make_client, api_database)
    me = person(make_client, api_database, w)
    first = me.post(KZ, json=idea()).json()
    assert decide(make_client, first["approval_id"], "PROD", "REJECT").status_code == 200
    assert me.get(f"{KZ}/{first['kaizen_id']}").json()["status"] == "REJECTED"
    assert Fresh(make_client, "PLANT_HEAD").post(f"{KZ}/{first['kaizen_id']}/resubmit",
                                                 json={}).status_code == 403
    again = me.post(f"{KZ}/{first['kaizen_id']}/resubmit", json={
        "proposed_change": "New vents and a harder compound", "estimated_saving": "90000"})
    assert again.status_code == 200, again.text
    second = again.json()
    assert second["status"] == "SUBMITTED" and second["approval_id"] != first["approval_id"]
    assert Decimal(second["estimated_saving"]) == 90000
    assert me.post(f"{KZ}/{first['kaizen_id']}/resubmit", json={}).status_code == 409  # not rejected
    assert decide(make_client, second["approval_id"], "PROD").status_code == 200
    assert decide(make_client, second["approval_id"], "PLANT_HEAD").status_code == 200
    assert me.get(f"{KZ}/{first['kaizen_id']}").json()["status"] == "APPROVED"
    admin = Fresh(make_client, "ADMIN")
    kept = [admin.get(f"/api/v1/approvals/{a}").json()["status"]
            for a in (first["approval_id"], second["approval_id"])]
    assert kept == ["REJECTED", "APPROVED"]  # both rounds are kept
