"""The company's own holiday calendar, shift master and leave accrual rules."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from .test_compound_batches import Fresh
from .test_costing import Rules
from .test_hr import HR, balance, employee, hr_world, kinds, leave_body, user_of
from .test_inventory_receipt import code

pytestmark = pytest.mark.usefixtures("api_database")


def first_monday(year, month):
    d = date(year, month, 1)
    return d + timedelta(days=(7 - d.weekday()) % 7)


def test_holidays_are_kept_by_the_company_and_decide_what_counts_as_leave(
        make_client, api_database):
    w = hr_world(make_client, api_database)
    me, uid = user_of(make_client, api_database, "SALES")
    employee(w, user_id=uid)
    monday = first_monday(2032, 3)
    wednesday = monday + timedelta(days=2)
    added = w["hr"].post(f"{HR}/holidays", json={"holiday_date": wednesday.isoformat(),
                                                 "name": "Founders day"})
    assert added.status_code == 201, added.text
    assert w["hr"].post(f"{HR}/holidays", json={"holiday_date": wednesday.isoformat(),
                                                "name": "Again"}).status_code == 409
    other = Fresh(make_client, "ADMIN").post("/api/v1/org/units", json={
        "code": code(), "name": "Other plant"}).json()
    own = w["hr"].post(f"{HR}/holidays", json={
        "holiday_date": (monday + timedelta(days=3)).isoformat(), "name": "Other plant only",
        "unit_id": other["unit_id"]})
    assert own.status_code == 201  # a holiday of another plant does not touch this one
    bulk = w["hr"].post(f"{HR}/holidays/bulk", json={"holidays": [
        {"holiday_date": wednesday.isoformat(), "name": "Already there"},
        {"holiday_date": date(2032, 8, 15).isoformat(), "name": "Independence Day"},
        {"holiday_date": date(2032, 10, 2).isoformat(), "name": "Gandhi Jayanti"}]})
    assert bulk.status_code == 200 and len(bulk.json()) == 2  # the existing day is left alone
    listed = me.get(f"{HR}/holidays?year=2032&unit_id={w['unit']['unit_id']}").json()
    assert [h["name"] for h in listed] == ["Founders day", "Independence Day", "Gandhi Jayanti"]
    friday = monday + timedelta(days=4)
    week = me.post(f"{HR}/leaves", json=leave_body(w, "EL", monday, friday))
    assert week.status_code == 201 and Decimal(week.json()["requested_days"]) == 4  # no holiday
    me.post(f"{HR}/leaves/{week.json()['leave_request_id']}/cancel")
    with Rules(make_client, leaveHolidaysCount=True):  # the company counts holidays as leave
        counted = me.post(f"{HR}/leaves", json=leave_body(w, "EL", monday, friday))
        assert Decimal(counted.json()["requested_days"]) == 5
        me.post(f"{HR}/leaves/{counted.json()['leave_request_id']}/cancel")
    off = w["hr"].patch(f"{HR}/holidays/{added.json()['holiday_id']}", json={"active": False})
    assert off.status_code == 200
    again = me.post(f"{HR}/leaves", json=leave_body(w, "EL", monday, friday))
    assert Decimal(again.json()["requested_days"]) == 5  # switched off: a working day again
    assert me.post(f"{HR}/holidays", json={"holiday_date": "2032-01-26", "name": "No"}
                   ).status_code == 403  # only HR keeps the calendar
    scoped = Fresh(make_client, "HR", subject="hr-scoped", unit_ids=[w["unit"]["unit_id"]])
    assert scoped.post(f"{HR}/holidays", json={"holiday_date": "2032-01-26", "name": "All plants"}
                       ).status_code == 403  # an all-plant holiday needs all-plant access


def test_leave_can_be_earned_monthly_per_type_or_by_company_rule(make_client, api_database):
    w = hr_world(make_client, api_database)
    me, uid = user_of(make_client, api_database, "SALES")
    employee(w, user_id=uid)
    year = date.today().year
    months = date.today().month
    el = kinds(w)["EL"]
    assert Decimal(balance(me, "EL", year)["accrued_to_date"]) == 15  # yearly by default
    try:
        w["hr"].patch(f"{HR}/leave-types/{el['leave_type_id']}", json={"accrual": "MONTHLY"})
        got = balance(me, "EL", year)
        assert Decimal(got["accrued_to_date"]) == (Decimal(15) * months / 12).quantize(
            Decimal("0.01"))
        assert Decimal(balance(me, "CL", year)["accrued_to_date"]) == 7  # that type only
        january = first_monday(year, 1)
        short = me.post(f"{HR}/leaves", json=leave_body(w, "EL", january, january + timedelta(
            days=2)))
        assert short.status_code == 409 and "1.25" in short.json()["detail"]  # one month earned
        december = first_monday(year, 12)
        ok = me.post(f"{HR}/leaves", json=leave_body(w, "EL", december, december + timedelta(
            days=2)))
        assert ok.status_code == 201  # by December the whole year is earned
        with Rules(make_client, leaveAccrual="MONTHLY"):  # the company rule reaches CL too
            assert Decimal(balance(me, "CL", year)["accrued_to_date"]) == (
                Decimal(7) * months / 12).quantize(Decimal("0.01"))
    finally:
        w["hr"].patch(f"{HR}/leave-types/{el['leave_type_id']}", json={"accrual": None})
    assert Decimal(balance(me, "EL", year)["accrued_to_date"]) == 15


def test_shifts_are_defined_by_the_company_and_find_late_and_early(make_client, api_database):
    w = hr_world(make_client, api_database)
    person = employee(w, employee_no="SHF" + code()[:4])
    plant = w["unit"]["unit_id"]
    general = w["hr"].post(f"{HR}/shifts", json={
        "code": "a", "name": "General", "start_time": "08:00", "end_time": "16:00"})
    assert general.status_code == 201 and general.json()["code"] == "A"
    own = w["hr"].post(f"{HR}/shifts", json={
        "code": "A", "name": "Plant day", "start_time": "09:00", "end_time": "17:00",
        "grace_minutes": 10, "unit_id": plant})
    assert own.status_code == 201  # the plant's own shift replaces the general one
    night = w["hr"].post(f"{HR}/shifts", json={
        "code": "C", "name": "Night", "start_time": "22:00", "end_time": "06:00",
        "unit_id": plant})
    assert night.status_code == 201
    assert w["hr"].post(f"{HR}/shifts", json={
        "code": "A", "name": "Dup", "start_time": "10:00", "end_time": "18:00",
        "unit_id": plant}).status_code == 409
    assert w["hr"].post(f"{HR}/shifts", json={
        "code": "Z", "name": "Zero", "start_time": "10:00", "end_time": "10:00"}
    ).status_code == 422
    assert Fresh(make_client, "SALES").post(f"{HR}/shifts", json={
        "code": "Q", "name": "No", "start_time": "10:00", "end_time": "11:00"}).status_code == 403
    assert {s["code"] for s in Fresh(make_client, "SALES").get(
        f"{HR}/shifts?unit_id={plant}").json()} >= {"A", "C"}
    month = (date.today().replace(day=1) - timedelta(days=1)).replace(day=1)
    ist = timedelta(hours=5, minutes=30)

    def punch(day, shift, hh, mm, out_h, out_m=0, out_next=False):
        stamp = datetime(month.year, month.month, day, hh, mm) - ist
        out = datetime(month.year, month.month, day, out_h, out_m) - ist + timedelta(
            days=1 if out_next else 0)
        made = w["hr"].post(f"{HR}/attendance", json={
            "employee_id": person["employee_id"], "shift_code": shift,
            "check_in": stamp.replace(tzinfo=UTC).isoformat(),
            "check_out": out.replace(tzinfo=UTC).isoformat()})
        assert made.status_code == 201, made.text

    def mine():
        rows = w["hr"].get(f"{HR}/attendance/summary?unit_id={plant}&month={month}").json()
        return next(r for r in rows if r["employee_id"] == person["employee_id"])

    punch(3, "A", 9, 5, 17)  # within the ten minutes of grace
    punch(4, "A", 9, 20, 17)  # late
    punch(5, "A", 8, 55, 16, 30)  # early leaving
    punch(6, "C", 22, 0, 6, 10, out_next=True)  # night shift, on time
    punch(7, "C", 22, 30, 5, 30, out_next=True)  # night shift: late and early
    row = mine()
    assert row["present_days"] == 5 and row["late_days"] == 2 and row["early_leaving_days"] == 2
    assert w["hr"].patch(f"{HR}/shifts/{own.json()['shift_id']}", json={"active": False}
                         ).status_code == 200
    row = mine()  # now the general 08:00-16:00 shift decides day shifts
    assert row["late_days"] == 4 and row["early_leaving_days"] == 1
