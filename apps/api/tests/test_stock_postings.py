"""Stock issues, count differences and scrap sales reach the accounts."""

from decimal import Decimal

import pytest

from .test_challans_scrap_interunit import decide as decide_scrap
from .test_challans_scrap_interunit import draft_scrap
from .test_compound_batches import Fresh
from .test_finance_core import F
from .test_inventory_issue import receive
from .test_inventory_receipt import INV, world
from .test_inventory_transfer_count import approval_of, count_all, decide, start_count
from .test_sales_orders import S, setup_sales

pytestmark = pytest.mark.usefixtures("api_database")


def seed_chart(make_client, unit_id):
    done = Fresh(make_client, "ACCOUNTS").post(f"{F}/accounts/seed-default",
                                               json={"unit_id": unit_id})
    assert done.status_code == 200, done.text


def books_of(make_client, unit_id):
    tb = Fresh(make_client, "ACCOUNTS").get(f"{F}/trial-balance?unit_id={unit_id}").json()
    assert tb["balanced"]
    return {r["code"]: Decimal(r["closing"]) for r in tb["rows"]}


def test_a_stock_issue_charges_the_material_consumed_and_relieves_the_inventory(make_client):
    w = world(make_client, qty="100", price="10")
    seed_chart(make_client, w["unit"]["unit_id"])
    receive(make_client, w, "100")  # booked at the PO price: 1,000
    unit = w["unit"]["unit_id"]
    assert books_of(make_client, unit)["1200"] == 1000
    issued = Fresh(make_client, "STORES").post(f"{INV}/issues", json={
        "store_id": w["store"]["store_id"], "purpose": "Line trial",
        "lines": [{"item_id": w["item"]["item_id"], "quantity": "30"}]})
    assert issued.status_code == 201, issued.text
    books = books_of(make_client, unit)
    assert books["5100"] == 300 and books["1200"] == 700  # 30 at 10 a piece
    short = Fresh(make_client, "STORES").post(f"{INV}/issues", json={
        "store_id": w["store"]["store_id"], "purpose": "Too much",
        "lines": [{"item_id": w["item"]["item_id"], "quantity": "500"}]})
    assert short.status_code == 409
    assert books_of(make_client, unit)["5100"] == 300  # the refused issue booked nothing


def test_count_differences_are_booked_as_a_loss_and_a_gain_when_approved(make_client):
    w = world(make_client, qty="200", price="10")
    unit = w["unit"]["unit_id"]
    seed_chart(make_client, unit)
    first = receive(make_client, w, "100")
    second = receive(make_client, w, "100")
    c = start_count(make_client, w["store"]["store_id"])
    by_lot = {ln["lot_no"]: n for n, ln in enumerate(c["lines"])}
    quantities = ["0", "0"]
    quantities[by_lot[first["lot_no"]]] = "92"  # 8 missing: a loss of 80
    quantities[by_lot[second["lot_no"]]] = "103"  # 3 found, valued at the last cost: a gain of 30
    count_all(make_client, c, quantities)
    Fresh(make_client, "STORES").post(f"{INV}/counts/{c['count_id']}/submit")
    assert books_of(make_client, unit)["1200"] == 2000  # nothing moves before approval
    approval = approval_of(make_client, c["count_id"])
    decide(make_client, approval, "PLANT_HEAD")
    decide(make_client, approval, "ACCOUNTS")
    books = books_of(make_client, unit)
    assert books["1200"] == 2000 - 80 + 30
    assert books["5300"] == 80 - 30  # inventory adjustments: the net loss


def test_an_invoiced_scrap_sale_is_booked_as_income_owed_by_the_customer(make_client):
    w = setup_sales(make_client)
    unit = w["unit"]["unit_id"]
    seed_chart(make_client, unit)
    sale = draft_scrap(make_client, w, amount="25000")
    sales = Fresh(make_client, "SALES")
    approval = sales.post(f"{S}/scrap-sales/{sale['scrap_sale_id']}/submit").json()["approval_id"]
    decide_scrap(make_client, approval, "PLANT_HEAD")
    decide_scrap(make_client, approval, "ACCOUNTS")
    assert "4100" not in books_of(make_client, unit)  # booked when invoiced, not before
    done = sales.post(f"{S}/scrap-sales/{sale['scrap_sale_id']}/invoice")
    assert done.status_code == 200, done.text
    books = books_of(make_client, unit)
    assert books["1100"] == 25000 and books["4100"] == -25000
    owed = Fresh(make_client, "ACCOUNTS").get(
        f"{F}/customers/{w['customer']['customer_id']}/statement?unit_id={unit}").json()
    assert owed["lines"] == []  # a scrap sale is not a goods invoice: the statement is unchanged
