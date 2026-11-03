"""The alert "shift ended without a production entry": told once, only for machines that ran."""

from datetime import timedelta

import pytest

from .test_compound_batches import Fresh
from .test_costing import Rules
from .test_notifications import run_jobs, sql
from .test_shop_floor import TODAY, entry, machine, shop, work_order

pytestmark = pytest.mark.usefixtures("api_database")
N = "/api/v1/notifications"


def three_shifts(api_database, unit_id):
    """Three tiny shifts that ended minutes after midnight, so they are over whenever this runs."""
    for code, start, end in (("A", "00:00", "00:01"), ("B", "00:01", "00:02"),
                             ("C", "00:02", "00:03")):
        sql(api_database, "insert into erp.shift(tenant_id, unit_id, code, name, start_time, "
            "end_time, grace_minutes) select tenant_id, unit_id, %s, %s, %s, %s, 0 from erp.unit "
            "where unit_id = %s", code, "Shift " + code, start, end, unit_id)


@pytest.fixture(autouse=True)
def retire_machines(api_database):
    """Other tests list the machines that should report; these tests' machines must not."""
    yield
    sql(api_database, "update erp.machine set active = false")


def alerts_for(make_client, machine_code):
    inbox = Fresh(make_client, "PROD").get(N).json()
    return [n for n in inbox if (n["subject"] or "").startswith("Shift ")
            and machine_code in (n["body"] or "")]


def test_planners_are_told_once_per_shift_about_machines_that_ran_but_have_no_entry(
        make_client, api_database):
    w = shop(make_client, api_database, compound_kg=None, inserts=None)
    wo = work_order(make_client, w, w["fg"], "500")
    mc = machine(make_client, w)
    machine(make_client, w, "IDLE")  # never ran: not expected to report
    three_shifts(api_database, w["unit"]["unit_id"])
    assert entry(make_client, w, wo, mc, day=TODAY - timedelta(days=1), shift="A").status_code == 201
    Fresh(make_client, "PROD").get(f"{N}/count")  # the planner's account exists
    with Rules(make_client, shiftAlertAfterMinutes=1):
        stats = run_jobs(make_client)
        assert stats["shift_alerts"] >= 3
        told = alerts_for(make_client, mc["code"])
        assert sorted(n["subject"][6] for n in told) == ["A", "B", "C"]
        assert "IDLE" not in " ".join(n["body"] for n in told)
        assert run_jobs(make_client)["shift_alerts"] == 0  # told once, however often it runs
    assert len(alerts_for(make_client, mc["code"])) == 3


def test_a_holiday_or_switching_the_alert_off_silences_it(make_client, api_database):
    w = shop(make_client, api_database, compound_kg=None, inserts=None)
    wo = work_order(make_client, w, w["fg"], "500")
    mc = machine(make_client, w)
    three_shifts(api_database, w["unit"]["unit_id"])
    assert entry(make_client, w, wo, mc, day=TODAY - timedelta(days=1), shift="A").status_code == 201
    Fresh(make_client, "PROD").get(f"{N}/count")
    with Rules(make_client, shiftAlertAfterMinutes=0):  # off
        assert run_jobs(make_client)["shift_alerts"] == 0
    sql(api_database, "insert into erp.holiday(tenant_id, unit_id, holiday_date, name) select "
        "tenant_id, %s, current_date, 'Test holiday' from erp.unit where unit_id = %s",
        w["unit"]["unit_id"], w["unit"]["unit_id"])
    sql(api_database, "insert into erp.holiday(tenant_id, unit_id, holiday_date, name) select "
        "tenant_id, %s, current_date - 1, 'Test holiday 2' from erp.unit where unit_id = %s",
        w["unit"]["unit_id"], w["unit"]["unit_id"])
    with Rules(make_client, shiftAlertAfterMinutes=1):
        run_jobs(make_client)
    assert alerts_for(make_client, mc["code"]) == []
