"""Employees, reporting lines, leave with approval, attendance and yearly appraisals."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from .test_compound_batches import Fresh
from .test_costing import Rules
from .test_inventory_receipt import code

pytestmark = pytest.mark.usefixtures("api_database")
HR = "/api/v1/hr"
A = "/api/v1/approvals"
NOBODY = "00000000-0000-4000-8000-000000000000"


def sql(api_database, query, *params):
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        cur = conn.execute(query, params)
        return cur.fetchone() if cur.description else None


def user_of(make_client, api_database, role):
    """A fresh sign-in user of the role; returns (client factory, user_id)."""
    subject = f"hr-{uuid4().hex[:10]}"
    Fresh(make_client, role, subject=subject).get("/api/v1/me")
    user_id = sql(api_database, "select user_id from erp.app_user where auth_subject = %s",
                  subject)[0]
    return Fresh(make_client, role, subject=subject), str(user_id)


def hr_world(make_client, api_database):
    admin = Fresh(make_client, "ADMIN")
    unit = admin.post("/api/v1/org/units", json={"code": code(), "name": "Plant"}).json()
    dept = admin.post("/api/v1/org/departments", json={
        "code": code(), "name": "Production", "unit_id": unit["unit_id"],
        "head_role_code": "PLANT_HEAD"}).json()
    hr = Fresh(make_client, "HR")
    assert hr.post(f"{HR}/leave-types/seed-default").status_code == 200
    return {"unit": unit, "dept": dept, "hr": hr}


def employee(w, **extra):
    body = {"unit_id": w["unit"]["unit_id"], "department_id": w["dept"]["department_id"],
            "employee_no": "E" + code()[:6], "full_name": "Test Person", **extra}
    made = w["hr"].post(f"{HR}/employees", json=body)
    assert made.status_code == 201, made.text
    return made.json()


def kinds(w):
    return {k["code"]: k for k in w["hr"].get(f"{HR}/leave-types").json()}


def workday(weeks_ahead=1, plus=0):
    """A Monday at least `weeks_ahead` weeks from now (plus some days), never a Sunday."""
    first = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)
    return first + timedelta(weeks=weeks_ahead - 1, days=plus)


def decide(make_client, approval_id, role, decision="APPROVE", subject=None):
    kw = {"subject": subject} if subject else {}
    return Fresh(make_client, role, **kw).post(f"{A}/{approval_id}/decide", json={
        "decision": decision, "note": "Not now" if decision == "REJECT" else None})


# ------------------------------------------------------------------ employees and reporting lines
def test_the_employee_master_keeps_a_reporting_line_that_cannot_loop(make_client, api_database):
    w = hr_world(make_client, api_database)
    boss = employee(w, full_name="Boss")
    mid = employee(w, full_name="Mid", manager_id=boss["employee_id"])
    low = employee(w, full_name="Low", manager_id=mid["employee_id"])
    assert low["manager_name"] == "Mid" and boss["direct_reports"] == 0
    got = w["hr"].get(f"{HR}/employees/{boss['employee_id']}").json()
    assert got["direct_reports"] == 1
    loop = w["hr"].patch(f"{HR}/employees/{boss['employee_id']}", json={
        "manager_id": low["employee_id"]})
    assert loop.status_code == 422 and "loop" in loop.json()["detail"]
    assert w["hr"].patch(f"{HR}/employees/{boss['employee_id']}", json={
        "manager_id": boss["employee_id"]}).status_code == 422
    dup = w["hr"].post(f"{HR}/employees", json={
        "unit_id": w["unit"]["unit_id"], "department_id": w["dept"]["department_id"],
        "employee_no": boss["employee_no"], "full_name": "Same number"})
    assert dup.status_code == 409
    other = Fresh(make_client, "ADMIN").post("/api/v1/org/units", json={
        "code": code(), "name": "Other plant"}).json()
    assert w["hr"].post(f"{HR}/employees", json={
        "unit_id": other["unit_id"], "department_id": w["dept"]["department_id"],
        "employee_no": "X" + code()[:5], "full_name": "Wrong plant"}).status_code == 422
    assert w["hr"].post(f"{HR}/employees", json={
        "unit_id": w["unit"]["unit_id"], "department_id": w["dept"]["department_id"],
        "employee_no": "Y" + code()[:5], "full_name": "No boss",
        "manager_id": NOBODY}).status_code == 422
    tree = w["hr"].get(f"{HR}/employees/{boss['employee_id']}/team").json()
    assert [(t["full_name"], t["depth"]) for t in tree] == [("Mid", 1), ("Low", 2)]
    blocked = w["hr"].post(f"{HR}/employees/{mid['employee_id']}/exit", json={
        "date_left": date.today().isoformat()})
    assert blocked.status_code == 409 and "report" in blocked.json()["detail"]
    gone = w["hr"].post(f"{HR}/employees/{low['employee_id']}/exit", json={
        "date_left": date.today().isoformat()})
    assert gone.status_code == 200 and gone.json()["employment_status"] == "EXITED"
    assert w["hr"].patch(f"{HR}/employees/{low['employee_id']}", json={
        "job_title": "Ghost"}).status_code == 409
    assert w["hr"].get(f"{HR}/employees/{mid['employee_id']}").json()["direct_reports"] == 0
    assert w["hr"].post(f"{HR}/employees/{low['employee_id']}/reinstate").status_code == 200
    found = w["hr"].get(f"{HR}/employees?unit_id={w['unit']['unit_id']}&q=mid").json()
    assert [e["full_name"] for e in found["items"]] == ["Mid"]
    assert w["hr"].get(f"{HR}/employees?unit_id={w['unit']['unit_id']}&status=active"
                       ).json()["total"] == 3


def test_a_user_links_to_one_employee_and_sees_their_own_record(make_client, api_database):
    w = hr_world(make_client, api_database)
    me, uid = user_of(make_client, api_database, "SALES")
    mine = employee(w, user_id=uid, full_name="Me Myself")
    assert me.get(f"{HR}/me").json()["employee_id"] == mine["employee_id"]
    again = w["hr"].post(f"{HR}/employees", json={
        "unit_id": w["unit"]["unit_id"], "department_id": w["dept"]["department_id"],
        "employee_no": "Z" + code()[:5], "full_name": "Twin", "user_id": uid})
    assert again.status_code == 409
    stranger, _ = user_of(make_client, api_database, "SALES")
    assert stranger.get(f"{HR}/me").status_code == 404  # no employee record for that user
    assert me.get(f"{HR}/employees").status_code == 403  # the master is for the hr module
    assert me.post(f"{HR}/employees", json={}).status_code == 403
    for role in ("SALES", "STORES", "PROD", "QA"):
        assert Fresh(make_client, role).post(f"{HR}/leave-types", json={
            "code": "ZZ", "name": "No", "annual_default_days": "1"}).status_code == 403, role


def test_leave_types_are_seeded_once_and_changed_by_hr(make_client, api_database):
    w = hr_world(make_client, api_database)
    got = kinds(w)
    assert {c: Decimal(k["annual_default_days"]) for c, k in got.items() if c in (
        "CL", "SL", "EL", "LOP")} == {"CL": 7, "SL": 7, "EL": 15, "LOP": 0}
    assert got["LOP"]["unpaid"] and not got["CL"]["unpaid"]
    assert w["hr"].post(f"{HR}/leave-types/seed-default").status_code == 200  # nothing duplicated
    assert len([k for k in kinds(w) if k == "CL"]) == 1
    done = w["hr"].patch(f"{HR}/leave-types/{got['CL']['leave_type_id']}", json={
        "annual_default_days": "10"})
    assert done.status_code == 200 and Decimal(done.json()["annual_default_days"]) == 10
    w["hr"].patch(f"{HR}/leave-types/{got['CL']['leave_type_id']}", json={
        "annual_default_days": "7"})
    assert w["hr"].post(f"{HR}/leave-types", json={
        "code": "cl", "name": "Duplicate", "annual_default_days": "1"}).status_code == 409


# ------------------------------------------------------------------ leave
def leave_body(w, kind, start, end=None, **extra):
    return {"leave_type_id": kinds(w)[kind]["leave_type_id"], "start_date": start.isoformat(),
            "end_date": (end or start).isoformat(), **extra}


def balance(client, kind, year, employee_id=None):
    q = f"?year={year}" + (f"&employee_id={employee_id}" if employee_id else "")
    return next(b for b in client.get(f"{HR}/leave-balances{q}").json()
                if b["leave_type_code"] == kind)


def test_leave_is_held_then_used_on_approval_and_given_back_when_rejected_or_cancelled(
        make_client, api_database):
    w = hr_world(make_client, api_database)
    me, uid = user_of(make_client, api_database, "SALES")
    mine = employee(w, user_id=uid)
    start = workday(2)
    year = start.year
    assert Decimal(balance(me, "CL", year)["available_days"]) == 7
    made = me.post(f"{HR}/leaves", json=leave_body(w, "CL", start, start + timedelta(days=1)))
    assert made.status_code == 201, made.text
    leave = made.json()
    assert leave["status"] == "PENDING_APPROVAL" and Decimal(leave["requested_days"]) == 2
    assert leave["approval_id"]
    held = balance(me, "CL", year)
    assert Decimal(held["pending_days"]) == 2 and Decimal(held["available_days"]) == 5
    assert me.post(f"{HR}/leaves", json=leave_body(w, "CL", start)).status_code == 409  # overlap
    big = me.post(f"{HR}/leaves", json=leave_body(w, "CL", workday(5), workday(5, 5)))
    assert big.status_code == 409 and "available" in big.json()["detail"]
    approved = decide(make_client, leave["approval_id"], "PLANT_HEAD")
    assert approved.status_code == 200, approved.text
    got = me.get(f"{HR}/leaves/{leave['leave_request_id']}").json()
    assert got["status"] == "APPROVED"
    used = balance(me, "CL", year)
    assert Decimal(used["used_days"]) == 2 and Decimal(used["available_days"]) == 5
    assert Decimal(used["pending_days"]) == 0
    # a rejected request frees its days
    second = me.post(f"{HR}/leaves", json=leave_body(w, "SL", workday(3), workday(3, 1))).json()
    assert Decimal(balance(me, "SL", workday(3).year)["available_days"]) == 5
    assert decide(make_client, second["approval_id"], "PLANT_HEAD", "REJECT").status_code == 200
    assert me.get(f"{HR}/leaves/{second['leave_request_id']}").json()["status"] == "REJECTED"
    assert Decimal(balance(me, "SL", workday(3).year)["available_days"]) == 7
    # a pending request can be withdrawn, an approved one cancelled before it starts
    third = me.post(f"{HR}/leaves", json=leave_body(w, "EL", workday(4))).json()
    out = me.post(f"{HR}/leaves/{third['leave_request_id']}/cancel")
    assert out.status_code == 200 and out.json()["status"] == "CANCELLED"
    assert decide(make_client, third["approval_id"], "PLANT_HEAD").status_code in (404, 409)
    back = me.post(f"{HR}/leaves/{leave['leave_request_id']}/cancel")
    assert back.status_code == 200 and back.json()["status"] == "CANCELLED"
    assert Decimal(balance(me, "CL", year)["used_days"]) == 0  # the days came back
    assert me.post(f"{HR}/leaves/{leave['leave_request_id']}/cancel").status_code == 409
    assert mine["employee_id"] == leave["employee_id"]


def test_leave_days_follow_the_company_rules_and_the_dates_are_checked(make_client, api_database):
    w = hr_world(make_client, api_database)
    me, uid = user_of(make_client, api_database, "SALES")
    employee(w, user_id=uid)
    monday = workday(6)
    sunday_only = monday + timedelta(days=6)
    week = me.post(f"{HR}/leaves", json=leave_body(w, "EL", monday, monday + timedelta(days=6)))
    assert week.status_code == 201 and Decimal(week.json()["requested_days"]) == 6  # no Sunday
    me.post(f"{HR}/leaves/{week.json()['leave_request_id']}/cancel")
    assert me.post(f"{HR}/leaves", json=leave_body(w, "CL", sunday_only)).status_code == 422
    with Rules(make_client, leaveCountSundays=True):
        counted = me.post(f"{HR}/leaves", json=leave_body(w, "EL", monday, monday + timedelta(
            days=6)))
        assert Decimal(counted.json()["requested_days"]) == 7
        me.post(f"{HR}/leaves/{counted.json()['leave_request_id']}/cancel")
    half = me.post(f"{HR}/leaves", json=leave_body(w, "CL", monday, half_day=True))
    assert half.status_code == 201 and Decimal(half.json()["requested_days"]) == Decimal("0.5")
    assert me.post(f"{HR}/leaves", json=leave_body(w, "CL", monday, monday + timedelta(days=1),
                                                   half_day=True)).status_code == 422
    assert me.post(f"{HR}/leaves", json=leave_body(w, "CL", monday + timedelta(days=3),
                                                   monday)).status_code == 422  # ends first
    across = me.post(f"{HR}/leaves", json=leave_body(w, "CL", date(2031, 12, 29), date(2032, 1, 2)))
    assert across.status_code == 422 and "two leave years" in across.json()["detail"]
    with Rules(make_client, leaveYearStartMonth=4):  # a leave year of April to March
        ok = me.post(f"{HR}/leaves", json=leave_body(w, "CL", date(2031, 12, 29), date(2032, 1, 2)))
        assert ok.status_code == 201  # no longer crosses a leave year
    lop = me.post(f"{HR}/leaves", json=leave_body(w, "LOP", date(2033, 3, 7), date(2033, 3, 25)))
    assert lop.status_code == 201  # unpaid leave needs no balance


def test_who_may_apply_see_and_adjust_leave(make_client, api_database):
    w = hr_world(make_client, api_database)
    boss_client, boss_uid = user_of(make_client, api_database, "PROD")
    boss = employee(w, user_id=boss_uid, full_name="Boss")
    me, uid = user_of(make_client, api_database, "SALES")
    mine = employee(w, user_id=uid, manager_id=boss["employee_id"])
    peer, peer_uid = user_of(make_client, api_database, "STORES")
    theirs = employee(w, user_id=peer_uid)
    start = workday(2)
    leave = me.post(f"{HR}/leaves", json=leave_body(w, "CL", start)).json()
    assert boss_client.get(f"{HR}/leaves/{leave['leave_request_id']}").status_code == 200  # manager
    assert w["hr"].get(f"{HR}/leaves/{leave['leave_request_id']}").status_code == 200  # HR
    assert peer.get(f"{HR}/leaves/{leave['leave_request_id']}").status_code == 404  # a stranger
    assert me.get(f"{HR}/leaves?status=pending_approval").json()["total"] == 1
    assert peer.get(f"{HR}/leaves").json()["total"] == 0
    assert boss_client.get(f"{HR}/leaves").json()["total"] == 1  # the team's leave
    assert me.post(f"{HR}/leaves", json={**leave_body(w, "CL", workday(3)),
                                         "employee_id": theirs["employee_id"]}).status_code == 403
    behalf = w["hr"].post(f"{HR}/leaves", json={**leave_body(w, "CL", workday(3)),
                                                "employee_id": mine["employee_id"]})
    assert behalf.status_code == 201  # HR applies for an employee
    assert me.get(f"{HR}/leave-balances?employee_id={theirs['employee_id']}").status_code == 404
    adjusted = w["hr"].post(f"{HR}/leave-balances/adjust", json={
        "employee_id": mine["employee_id"], "leave_type_id": kinds(w)["CL"]["leave_type_id"],
        "leave_year": start.year, "days": "3", "reason": "Carry forward"})
    assert adjusted.status_code == 200 and Decimal(adjusted.json()["adjusted_days"]) == 3
    assert w["hr"].post(f"{HR}/leave-balances/adjust", json={
        "employee_id": mine["employee_id"], "leave_type_id": kinds(w)["CL"]["leave_type_id"],
        "leave_year": start.year, "days": "-50", "reason": "Too much"}).status_code == 409
    assert me.post(f"{HR}/leave-balances/adjust", json={
        "employee_id": mine["employee_id"], "leave_type_id": kinds(w)["CL"]["leave_type_id"],
        "leave_year": start.year, "days": "1", "reason": "Self service"}).status_code == 403
    cal = w["hr"].get(f"{HR}/leave-calendar?date_from={start}&date_to={start + timedelta(days=30)}")
    assert cal.status_code == 200 and cal.json() == []  # nothing is approved yet


# ------------------------------------------------------------------ attendance
def test_attendance_is_imported_safely_and_summarised_for_the_month(make_client, api_database):
    w = hr_world(make_client, api_database)
    person = employee(w, employee_no="ATT" + code()[:4])
    month = (date.today().replace(day=1) - timedelta(days=1)).replace(day=1)  # a finished month
    day1 = month + timedelta(days=1)
    stamp = datetime(day1.year, day1.month, day1.day, 3, 30, tzinfo=UTC)  # 09:00 in India
    rows = [{"employee_no": person["employee_no"], "check_in": stamp.isoformat(),
             "check_out": (stamp + timedelta(hours=8)).isoformat()},
            {"employee_no": "NOSUCH1", "check_in": stamp.isoformat()},
            {"employee_no": person["employee_no"].lower(),
             "check_in": (stamp + timedelta(days=1)).isoformat(),
             "check_out": (stamp - timedelta(hours=1)).isoformat()}]
    first = w["hr"].post(f"{HR}/attendance/import", json={"records": rows})
    assert first.status_code == 200, first.text
    assert first.json()["imported"] == 1 and first.json()["unknown_employees"] == ["NOSUCH1"]
    assert len(first.json()["rejected"]) == 1
    again = w["hr"].post(f"{HR}/attendance/import", json={"records": rows})
    assert again.json()["imported"] == 0 and again.json()["updated"] == 1  # safe to repeat
    listed = w["hr"].get(f"{HR}/attendance?employee_id={person['employee_id']}").json()
    assert listed["total"] == 1 and listed["items"][0]["hours"] == "8.00"
    assert listed["items"][0]["work_date"] == day1.isoformat()
    late = datetime(day1.year, day1.month, day1.day, 19, 0, tzinfo=UTC)  # 00:30 next day in India
    manual = w["hr"].post(f"{HR}/attendance", json={
        "employee_id": person["employee_id"], "shift_code": "c", "check_in": late.isoformat(),
        "check_out": (late + timedelta(hours=7)).isoformat()})
    assert manual.status_code == 201
    assert manual.json()["work_date"] == (day1 + timedelta(days=1)).isoformat()
    assert w["hr"].post(f"{HR}/attendance", json={
        "employee_id": person["employee_id"], "check_in": late.isoformat(),
        "check_out": (late - timedelta(hours=1)).isoformat()}).status_code == 422
    assert w["hr"].post(f"{HR}/attendance", json={
        "employee_id": person["employee_id"], "check_in": (datetime.now(UTC) + timedelta(
            days=2)).isoformat()}).status_code == 422
    summary = w["hr"].get(f"{HR}/attendance/summary?unit_id={w['unit']['unit_id']}&month={month}")
    row = next(r for r in summary.json() if r["employee_id"] == person["employee_id"])
    assert row["present_days"] == 2 and Decimal(row["hours_worked"]) == 15
    assert Decimal(row["absent_days"]) >= 0
    for role in ("SALES", "STORES", "OPERATOR"):
        assert Fresh(make_client, role).post(f"{HR}/attendance/import", json={
            "records": rows}).status_code == 403, role
    assert Fresh(make_client, "SALES").get(f"{HR}/attendance").status_code == 403


# ------------------------------------------------------------------ appraisals
def test_an_appraisal_goes_from_self_to_manager_to_approval_with_hr_scoring(
        make_client, api_database):
    w = hr_world(make_client, api_database)
    boss_client, boss_uid = user_of(make_client, api_database, "PROD")
    boss = employee(w, user_id=boss_uid, full_name="Boss")
    me, uid = user_of(make_client, api_database, "SALES")
    mine = employee(w, user_id=uid, manager_id=boss["employee_id"])
    year = date.today().year
    opened = w["hr"].post(f"{HR}/appraisals/open", json={
        "fiscal_year": year, "unit_id": w["unit"]["unit_id"]})
    assert opened.status_code == 200 and opened.json()["opened"] == 2
    assert w["hr"].post(f"{HR}/appraisals/open", json={
        "fiscal_year": year, "unit_id": w["unit"]["unit_id"]}).json()["opened"] == 0
    mine_row = next(a for a in me.get(f"{HR}/appraisals").json()["items"]
                    if a["employee_id"] == mine["employee_id"])
    aid = mine_row["appraisal_id"]
    assert mine_row["status"] == "SELF_REVIEW"
    assert boss_client.post(f"{HR}/appraisals/{aid}/manager", json={"score": "4"}
                            ).status_code == 409  # the self review comes first
    assert boss_client.post(f"{HR}/appraisals/{aid}/self", json={"score": "4"}).status_code == 403
    assert me.post(f"{HR}/appraisals/{aid}/self", json={"score": "6"}).status_code == 422
    done = me.post(f"{HR}/appraisals/{aid}/self", json={"score": "4.5", "comment": "Good year"})
    assert done.status_code == 200 and done.json()["status"] == "MANAGER_REVIEW"
    assert me.post(f"{HR}/appraisals/{aid}/self", json={"score": "3"}).status_code == 409
    assert me.post(f"{HR}/appraisals/{aid}/manager", json={"score": "5"}).status_code == 403
    sent = boss_client.post(f"{HR}/appraisals/{aid}/manager", json={
        "score": "4", "comment": "Solid"})
    assert sent.status_code == 200 and sent.json()["status"] == "PENDING_APPROVAL"
    approval = sent.json()["approval_id"]
    assert approval
    scored = w["hr"].post(f"{HR}/appraisals/{aid}/hr-score", json={"score": "3.5"})
    assert scored.status_code == 200 and Decimal(scored.json()["hr_score"]) == Decimal("3.5")
    assert me.post(f"{HR}/appraisals/{aid}/hr-score", json={"score": "5"}).status_code == 403
    assert decide(make_client, approval, "PLANT_HEAD").status_code == 200  # the department head
    assert me.get(f"{HR}/appraisals/{aid}").json()["status"] == "PENDING_APPROVAL"
    assert decide(make_client, approval, "HR").status_code == 200  # then HR
    final = me.get(f"{HR}/appraisals/{aid}").json()
    assert final["status"] == "COMPLETED" and Decimal(final["final_score"]) == Decimal("3.5")
    stranger, _ = user_of(make_client, api_database, "STORES")
    assert stranger.get(f"{HR}/appraisals/{aid}").status_code == 404
    assert boss_client.get(f"{HR}/appraisals?status=completed").json()["total"] == 1


def test_a_rejected_appraisal_ends_rejected_and_a_manager_less_employee_is_reviewed_by_hr(
        make_client, api_database):
    w = hr_world(make_client, api_database)
    boss_client, boss_uid = user_of(make_client, api_database, "PROD")
    boss = employee(w, user_id=boss_uid)
    me, uid = user_of(make_client, api_database, "SALES")
    employee(w, user_id=uid, manager_id=boss["employee_id"])
    year = date.today().year
    w["hr"].post(f"{HR}/appraisals/open", json={"fiscal_year": year,
                                                "unit_id": w["unit"]["unit_id"]})
    mine = next(a for a in me.get(f"{HR}/appraisals").json()["items"])["appraisal_id"]
    me.post(f"{HR}/appraisals/{mine}/self", json={"score": "3"})
    sent = boss_client.post(f"{HR}/appraisals/{mine}/manager", json={"score": "2"}).json()
    assert decide(make_client, sent["approval_id"], "PLANT_HEAD", "REJECT").status_code == 200
    ended = me.get(f"{HR}/appraisals/{mine}").json()
    assert ended["status"] == "REJECTED" and ended["final_score"] is None
    top = boss_client.get(f"{HR}/appraisals").json()["items"]
    top_id = next(a["appraisal_id"] for a in top if a["employee_id"] == boss["employee_id"])
    own = boss_client.post(f"{HR}/appraisals/{top_id}/self", json={"score": "4"})
    assert own.status_code == 200
    assert boss_client.post(f"{HR}/appraisals/{top_id}/manager", json={"score": "4"}
                            ).status_code == 403  # nobody reviews their own
    by_hr = w["hr"].post(f"{HR}/appraisals/{top_id}/manager", json={"score": "4"})
    assert by_hr.status_code == 200 and by_hr.json()["status"] == "PENDING_APPROVAL"


def test_a_rejected_appraisal_is_reopened_by_hr_and_goes_round_again(make_client, api_database):
    w = hr_world(make_client, api_database)
    boss_client, boss_uid = user_of(make_client, api_database, "PROD")
    boss = employee(w, user_id=boss_uid)
    me, uid = user_of(make_client, api_database, "SALES")
    employee(w, user_id=uid, manager_id=boss["employee_id"])
    w["hr"].post(f"{HR}/appraisals/open", json={"fiscal_year": date.today().year,
                                                "unit_id": w["unit"]["unit_id"]})
    aid = next(a for a in me.get(f"{HR}/appraisals").json()["items"])["appraisal_id"]
    me.post(f"{HR}/appraisals/{aid}/self", json={"score": "3"})
    first = boss_client.post(f"{HR}/appraisals/{aid}/manager", json={"score": "2"}).json()
    assert decide(make_client, first["approval_id"], "PLANT_HEAD", "REJECT").status_code == 200
    assert w["hr"].post(f"{HR}/appraisals/{aid}/reopen").status_code == 200
    assert w["hr"].post(f"{HR}/appraisals/{aid}/reopen").status_code == 409  # no longer rejected
    assert me.post(f"{HR}/appraisals/{aid}/reopen").status_code == 403
    back = me.get(f"{HR}/appraisals/{aid}").json()
    assert back["status"] == "MANAGER_REVIEW" and back["manager_score"] is None
    second = boss_client.post(f"{HR}/appraisals/{aid}/manager", json={"score": "4"}).json()
    assert second["status"] == "PENDING_APPROVAL" and second["approval_id"] != first["approval_id"]
    assert decide(make_client, second["approval_id"], "PLANT_HEAD").status_code == 200
    assert decide(make_client, second["approval_id"], "HR").status_code == 200
    assert Decimal(me.get(f"{HR}/appraisals/{aid}").json()["final_score"]) == 4
