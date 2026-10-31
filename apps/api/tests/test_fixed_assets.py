"""Fixed asset register and depreciation runs (Companies Act book and Income-tax book)."""

from calendar import monthrange
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest

from .test_compound_batches import Fresh
from .test_costing import Rules
from .test_finance_core import TODAY, F
from .test_inventory_receipt import code

pytestmark = pytest.mark.usefixtures("api_database")
NOBODY = "00000000-0000-4000-8000-000000000000"


def first_of(months_back):
    """The first day of the month that many months before this one."""
    y, m = TODAY.year, TODAY.month - months_back
    while m < 1:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    return date(y, m, 1)


def plant(make_client, chart=True):
    admin = Fresh(make_client, "ADMIN")
    unit = admin.post("/api/v1/org/units", json={"code": code(), "name": "Plant"}).json()
    if chart:
        done = Fresh(make_client, "ACCOUNTS").post(f"{F}/accounts/seed-default",
                                                   json={"unit_id": unit["unit_id"]})
        assert done.status_code == 200, done.text
    return unit


def asset(make_client, unit, acquired, cost="120000", life=60, **extra):
    body = {"unit_id": unit["unit_id"], "asset_code": "A" + code()[:6], "description": "Press",
            "acquisition_date": acquired.isoformat(), "acquisition_cost": cost,
            "useful_life_months": life, **extra}
    made = Fresh(make_client, "ACCOUNTS").post(f"{F}/assets", json=body)
    assert made.status_code == 201, made.text
    return made.json()


def run(make_client, unit, months_back, books="COMPANIES_ACT"):
    return Fresh(make_client, "ACCOUNTS").post(f"{F}/depreciation-runs", json={
        "unit_id": unit["unit_id"], "month": first_of(months_back).isoformat(), "books": books})


def post(make_client, run_id, late=True):
    """Post a run; past months are locked, so the tests book late unless they test the lock."""
    if not late:
        return Fresh(make_client, "ACCOUNTS").post(f"{F}/depreciation-runs/{run_id}/post")
    with Rules(make_client, depreciationLateBooking="BOOK_IN_CURRENT_MONTH"):
        return Fresh(make_client, "ACCOUNTS").post(f"{F}/depreciation-runs/{run_id}/post")


def books_of(make_client, unit):
    tb = Fresh(make_client, "ACCOUNTS").get(f"{F}/trial-balance?unit_id={unit['unit_id']}").json()
    return {r["code"]: Decimal(r["closing"]) for r in tb["rows"]}


def amounts(result):
    return {ln["asset_code"]: Decimal(ln["depreciation_amount"]) for ln in result["lines"]}


# ------------------------------------------------------------------ the register
def test_registering_assets_checks_the_basis_and_keeps_codes_unique(make_client, api_database):
    u = plant(make_client)
    acct = Fresh(make_client, "ACCOUNTS")
    base = {"unit_id": u["unit_id"], "asset_code": "P-1", "description": "Press",
            "acquisition_date": first_of(3).isoformat(), "acquisition_cost": "100000",
            "useful_life_months": 60}
    good = acct.post(f"{F}/assets", json=base)
    assert good.status_code == 201 and good.json()["book_value"] == "100000.00"
    assert acct.post(f"{F}/assets", json=base).status_code == 409  # same code in the plant
    for change in ({"asset_code": "P-2", "salvage_value": "6000"},  # more than 5% of the cost
                   {"asset_code": "P-3", "method": "WDV"},  # no rate
                   {"asset_code": "P-4", "acquisition_date": date.today().replace(
                       year=date.today().year + 1).isoformat()},
                   {"asset_code": "P-5", "salvage_value": "200000"},
                   {"asset_code": "P-6", "useful_life_months": 0},
                   {"asset_code": "P-7", "machine_id": NOBODY}):
        assert acct.post(f"{F}/assets", json={**base, **change}).status_code == 422, change
    with Rules(make_client, salvageMaxPct=10):  # the company allows a larger residual value
        ok = acct.post(f"{F}/assets", json={**base, "asset_code": "P-8", "salvage_value": "9000"})
        assert ok.status_code == 201
    other = plant(make_client)
    foreign = Fresh(make_client, "PROD").post("/api/v1/manufacturing/machines", json={
        "unit_id": other["unit_id"], "code": "M" + code()[:4], "name": "Press"}).json()
    assert acct.post(f"{F}/assets", json={**base, "asset_code": "P-9",
                                          "machine_id": foreign["machine_id"]}).status_code == 422
    mine = Fresh(make_client, "PROD").post("/api/v1/manufacturing/machines", json={
        "unit_id": u["unit_id"], "code": "M" + code()[:4], "name": "Press"}).json()
    linked = acct.post(f"{F}/assets", json={**base, "asset_code": "P-10",
                                            "machine_id": mine["machine_id"]})
    assert linked.status_code == 201 and linked.json()["machine_id"] == mine["machine_id"]
    got = acct.get(f"{F}/assets?unit_id={u['unit_id']}&status=active").json()
    assert got["total"] == 3
    patched = acct.patch(f"{F}/assets/{good.json()['asset_id']}", json={"description": "Press 2"})
    assert patched.status_code == 200 and patched.json()["description"] == "Press 2"
    assert acct.get(f"{F}/assets/{NOBODY}").status_code == 404


def test_only_the_asset_role_changes_the_register_and_finance_reads_it(make_client, api_database):
    u = plant(make_client)
    body = {"unit_id": u["unit_id"], "asset_code": "X-1", "description": "Press",
            "acquisition_date": first_of(2).isoformat(), "acquisition_cost": "1000",
            "useful_life_months": 12}
    for role in ("PROD", "STORES", "SALES", "QA", "OPERATOR"):
        assert Fresh(make_client, role).post(f"{F}/assets", json=body).status_code == 403, role
        assert Fresh(make_client, role).post(f"{F}/depreciation-runs", json={
            "unit_id": u["unit_id"], "month": first_of(2).isoformat()}).status_code == 403
    for role in ("STORES", "QA", "OPERATOR"):
        assert Fresh(make_client, role).get(f"{F}/assets").status_code == 403, role
        assert Fresh(make_client, role).get(
            f"{F}/depreciation-runs?unit_id={u['unit_id']}").status_code == 403


# ------------------------------------------------------------------ straight line and written down
def test_runs_are_in_month_order_posted_to_the_books_and_cancelled_latest_first(
        make_client, api_database):
    u = plant(make_client)
    slm = asset(make_client, u, first_of(3))  # 120,000 over 60 months: 2,000 a month
    wdv = asset(make_client, u, first_of(3), cost="100000", method="WDV", wdv_rate="15")
    first = run(make_client, u, 3)
    assert first.status_code == 201, first.text
    assert amounts(first.json()) == {slm["asset_code"]: Decimal("2000.00"),
                                     wdv["asset_code"]: Decimal("1250.00")}  # 100,000 x 15% / 12
    assert first.json()["status"] == "DRAFT" and first.json()["total"] == "3250.00"
    assert books_of(make_client, u).get("5700") is None  # nothing is booked until posted
    assert run(make_client, u, 2).status_code == 409  # the draft must be posted or cancelled first
    posted = post(make_client, first.json()["depreciation_run_id"])
    assert posted.status_code == 200 and posted.json()["journal_id"]
    books = books_of(make_client, u)
    assert books["5700"] == 3250 and books["1690"] == -3250
    assert post(make_client, first.json()["depreciation_run_id"]).status_code == 409
    assert run(make_client, u, 3).status_code == 409  # that month is done
    assert run(make_client, u, -1).status_code == 422  # a future month has not finished
    second = run(make_client, u, 2).json()
    assert amounts(second) == {slm["asset_code"]: Decimal("2000.00"),
                               wdv["asset_code"]: Decimal("1234.38")}  # (100,000 - 1,250) x 1.25%
    post(make_client, second["depreciation_run_id"])
    acct = Fresh(make_client, "ACCOUNTS")
    assert acct.post(f"{F}/depreciation-runs/{first.json()['depreciation_run_id']}/cancel"
                     ).status_code == 409  # not the latest run
    cancelled = acct.post(f"{F}/depreciation-runs/{second['depreciation_run_id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    assert books_of(make_client, u)["5700"] == 3250  # the second month is reversed
    again = run(make_client, u, 2)
    assert again.status_code == 201  # and can be run again
    got = acct.get(f"{F}/assets/{wdv['asset_id']}").json()
    assert got["accumulated_depreciation"] == "1250.00" and got["book_value"] == "98750.00"
    assert acct.get(f"{F}/depreciation-runs?unit_id={u['unit_id']}").json()[0]["month"] == (
        first_of(2).isoformat())
    assert acct.get(f"{F}/depreciation-runs/{NOBODY}").status_code == 404


def test_a_skipped_month_is_caught_up_by_the_next_run(make_client, api_database):
    u = plant(make_client)
    slm = asset(make_client, u, first_of(3))
    wdv = asset(make_client, u, first_of(3), cost="100000", method="WDV", wdv_rate="15")
    post(make_client, run(make_client, u, 3).json()["depreciation_run_id"])
    later = run(make_client, u, 1).json()  # the middle month was never run
    assert amounts(later)[slm["asset_code"]] == Decimal("4000.00")  # two months at once
    # written down: 1,250.00 + 1,234.38 + 1,218.95 = 3,703.33 carried by the end of month three
    line = next(ln for ln in later["lines"] if ln["asset_code"] == wdv["asset_code"])
    assert Decimal(line["accumulated_amount"]) == Decimal("3703.33")
    assert Decimal(line["depreciation_amount"]) == Decimal("2453.33")


def test_depreciation_stops_at_the_residual_value(make_client, api_database):
    u = plant(make_client)
    a = asset(make_client, u, first_of(3), cost="10000", life=3, salvage_value="500")
    for back in (3, 2, 1):
        made = run(make_client, u, back)
        assert made.status_code == 201, made.text
        post(make_client, made.json()["depreciation_run_id"])
    got = Fresh(make_client, "ACCOUNTS").get(f"{F}/assets/{a['asset_id']}").json()
    assert got["accumulated_depreciation"] == "9500.00" and got["book_value"] == "500.00"
    assert run(make_client, u, -1).status_code == 422  # and a future month is not due


def test_a_locked_month_refuses_depreciation_unless_the_company_books_it_late(
        make_client, api_database):
    u = plant(make_client)
    asset(make_client, u, first_of(3))
    made = run(make_client, u, 3).json()
    refused = post(make_client, made["depreciation_run_id"], late=False)
    assert refused.status_code == 409 and "locked" in refused.json()["detail"]
    assert books_of(make_client, u).get("5700") is None
    late = post(make_client, made["depreciation_run_id"])
    assert late.status_code == 200 and late.json()["status"] == "POSTED"
    assert books_of(make_client, u)["5700"] == 2000  # booked in the current month
    journal = Fresh(make_client, "ACCOUNTS").get(f"{F}/journals/{late.json()['journal_id']}").json()
    assert journal["journal_date"] == TODAY.isoformat() and "booked late" in journal["narration"]


def test_the_company_chooses_from_when_an_asset_is_depreciated(make_client, api_database):
    days = monthrange(first_of(2).year, first_of(2).month)[1]
    started = first_of(2).replace(day=11)
    pro_rata = (Decimal(2000) * (days - 10) / days).quantize(Decimal("0.01"), ROUND_HALF_UP)
    for rule, expected in (("PRO_RATA_DAYS", pro_rata), ("FULL_MONTH", Decimal("2000.00")),
                           ("NEXT_MONTH", None)):
        u = plant(make_client)
        a = asset(make_client, u, started)
        with Rules(make_client, depreciationConvention=rule):
            made = run(make_client, u, 2)
        if expected is None:
            assert made.status_code == 409, rule  # it starts next month: nothing this month
        else:
            assert amounts(made.json())[a["asset_code"]] == expected, rule


# ------------------------------------------------------------------ the tax book
def test_the_income_tax_book_is_a_separate_schedule_that_is_not_posted(make_client, api_database):
    u = plant(make_client)
    both = asset(make_client, u, first_of(2), cost="100000", it_rate="30")
    asset(make_client, u, first_of(2), cost="50000")  # no tax rate: not in the tax book
    tax = run(make_client, u, 2, "INCOME_TAX")
    assert tax.status_code == 201, tax.text
    assert amounts(tax.json()) == {both["asset_code"]: Decimal("2500.00")}  # 100,000 x 30% / 12
    posted = post(make_client, tax.json()["depreciation_run_id"])
    assert posted.status_code == 200 and posted.json()["journal_id"] is None
    assert books_of(make_client, u).get("5700") is None  # the accounts are untouched
    book = run(make_client, u, 2)  # the Companies Act run for the same month is separate
    assert book.status_code == 201 and len(book.json()["lines"]) == 2
    got = Fresh(make_client, "ACCOUNTS").get(f"{F}/assets/{both['asset_id']}").json()
    assert got["tax_accumulated"] == "2500.00" and got["tax_value"] == "97500.00"
    assert Decimal(got["accumulated_depreciation"]) == 0  # the Companies Act run is only a draft


# ------------------------------------------------------------------ disposal and the rest
def test_disposal_stops_depreciation_and_respects_the_runs_already_made(make_client, api_database):
    u = plant(make_client)
    a = asset(make_client, u, first_of(3))
    acct = Fresh(make_client, "ACCOUNTS")
    day = first_of(2).replace(day=15)
    assert acct.post(f"{F}/assets/{a['asset_id']}/dispose", json={
        "disposed_on": first_of(4).isoformat()}).status_code == 422  # before it was in use
    gone = acct.post(f"{F}/assets/{a['asset_id']}/dispose", json={
        "disposed_on": day.isoformat(), "disposal_value": "90000"})
    assert gone.status_code == 200 and gone.json()["status"] == "DISPOSED"
    assert acct.post(f"{F}/assets/{a['asset_id']}/dispose", json={
        "disposed_on": day.isoformat()}).status_code == 409
    days = monthrange(day.year, day.month)[1]
    part = (Decimal(2000) * 15 / days).quantize(Decimal("0.01"), ROUND_HALF_UP)
    made = run(make_client, u, 2).json()
    assert amounts(made)[a["asset_code"]] == Decimal("2000.00") + part  # a full month, then 15 days
    assert Decimal(made["lines"][0]["accumulated_amount"]) == Decimal("2000.00") + part
    post(make_client, made["depreciation_run_id"])
    assert run(make_client, u, 1).status_code == 409  # nothing more to depreciate
    other = plant(make_client)
    b = asset(make_client, other, first_of(3))
    post(make_client, run(make_client, other, 3).json()["depreciation_run_id"])
    early = acct.post(f"{F}/assets/{b['asset_id']}/dispose", json={
        "disposed_on": first_of(4).replace(day=1).isoformat()})
    assert early.status_code == 422
    sold_before_run = acct.post(f"{F}/assets/{b['asset_id']}/dispose", json={
        "disposed_on": first_of(3).replace(day=2).isoformat()})
    assert sold_before_run.status_code == 409  # a posted run already covers that month


def test_the_basis_is_frozen_once_depreciation_has_run_and_posting_needs_a_chart(
        make_client, api_database):
    u = plant(make_client)
    a = asset(make_client, u, first_of(2))
    acct = Fresh(make_client, "ACCOUNTS")
    assert acct.patch(f"{F}/assets/{a['asset_id']}", json={"useful_life_months": 48}
                      ).status_code == 200  # nothing has run yet
    made = run(make_client, u, 2)
    assert made.status_code == 201
    assert acct.patch(f"{F}/assets/{a['asset_id']}", json={"useful_life_months": 36}
                      ).status_code == 409
    assert acct.patch(f"{F}/assets/{a['asset_id']}", json={"category": "Plant"}
                      ).status_code == 200  # labels stay editable
    bare = plant(make_client, chart=False)
    asset(make_client, bare, first_of(2))
    draft = run(make_client, bare, 2)
    assert draft.status_code == 201
    refused = post(make_client, draft.json()["depreciation_run_id"])
    assert refused.status_code == 409 and "chart of accounts" in refused.json()["detail"]
