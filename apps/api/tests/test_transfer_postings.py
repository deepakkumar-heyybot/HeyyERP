"""Inter-plant transfers in the accounts, by the scheme the company chooses."""

from decimal import Decimal

import pytest

from .test_challans_scrap_interunit import inter_plant_transfer
from .test_compound_batches import Fresh
from .test_costing import Rules
from .test_finance_core import F
from .test_inventory_receipt import INV
from .test_sales_orders import S

pytestmark = pytest.mark.usefixtures("api_database")


def charts(make_client, *units):
    for unit in units:
        done = Fresh(make_client, "ACCOUNTS").post(f"{F}/accounts/seed-default",
                                                   json={"unit_id": unit})
        assert done.status_code == 200, done.text


def books(make_client, unit):
    tb = Fresh(make_client, "ACCOUNTS").get(f"{F}/trial-balance?unit_id={unit}").json()
    assert tb["balanced"]
    return {r["code"]: Decimal(r["closing"]) for r in tb["rows"]}


def raise_invoice(make_client, transfer_id):
    made = Fresh(make_client, "ACCOUNTS").post(f"{S}/inter-unit-invoices", json={
        "transfer_id": transfer_id})
    assert made.status_code == 201, made.text
    return made.json()


def test_a_transfer_sold_between_plants_moves_the_inventory_and_nets_to_nothing(make_client):
    w, plant2, t = inter_plant_transfer(make_client, None)  # 30 at 10: worth 300
    sender, receiver = w["unit"]["unit_id"], plant2["unit_id"]
    charts(make_client, sender, receiver)
    invoice = raise_invoice(make_client, t["transfer_id"])
    out, into = books(make_client, sender), books(make_client, receiver)
    assert out["1500"] == 300 and out["4200"] == -300 and out["5600"] == 300  # sold at cost
    assert out["1200"] == -300  # the stock left the sending plant
    assert into["1200"] == 300 and into["2500"] == -300 and "5600" not in into
    both = Fresh(make_client, "ACCOUNTS").get(
        f"{F}/consolidation?unit_ids={sender}&unit_ids={receiver}").json()
    by = {r["code"]: Decimal(r["combined"]) for r in both["rows"]}
    assert by["1200"] == 0 and by["1500"] + by["2500"] == 0 and by["4200"] + by["5600"] == 0
    assert both["balanced"] and both["receivable_payable_difference"] in ("0", "0.00")
    gone = Fresh(make_client, "ACCOUNTS").post(
        f"{S}/inter-unit-invoices/{invoice['inter_unit_invoice_id']}/cancel")
    assert gone.status_code == 200
    assert all(v == 0 for v in books(make_client, sender).values())  # everything is reversed
    assert all(v == 0 for v in books(make_client, receiver).values())


def test_the_company_can_book_a_transfer_at_cost_without_a_sale(make_client):
    w, plant2, t = inter_plant_transfer(make_client, None)
    sender, receiver = w["unit"]["unit_id"], plant2["unit_id"]
    charts(make_client, sender, receiver)
    with Rules(make_client, transferScheme="AT_COST"):
        raise_invoice(make_client, t["transfer_id"])
    out, into = books(make_client, sender), books(make_client, receiver)
    assert out["1500"] == 300 and out["1200"] == -300
    assert "4200" not in out and "5600" not in out  # no sale and no purchase
    assert into["1200"] == 300 and into["2500"] == -300


def test_without_an_inter_unit_invoice_the_transfer_is_booked_when_it_is_dispatched(make_client):
    w, plant2, t = inter_plant_transfer(make_client, None, dispatch=False)
    sender, receiver = w["unit"]["unit_id"], plant2["unit_id"]
    charts(make_client, sender, receiver)
    assert "1500" not in books(make_client, sender)
    with Rules(make_client, interUnit=False):  # the company raises no invoice between its plants
        sent = Fresh(make_client, "STORES").post(f"{INV}/transfers/{t['transfer_id']}/dispatch")
        assert sent.status_code == 200, sent.text
    out, into = books(make_client, sender), books(make_client, receiver)
    assert out["1500"] == 300 and out["1200"] == -300 and "4200" not in out
    assert into["1200"] == 300 and into["2500"] == -300


def test_a_plant_without_a_chart_books_nothing_for_a_transfer(make_client):
    w, plant2, t = inter_plant_transfer(make_client, None)
    charts(make_client, plant2["unit_id"])  # only the receiving plant has a chart
    raise_invoice(make_client, t["transfer_id"])
    into = books(make_client, plant2["unit_id"])
    assert into["1200"] == 300 and into["2500"] == -300  # its own side is still booked
