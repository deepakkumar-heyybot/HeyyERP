"""Things the company decides itself: GST on scrap, machine and labour cost, kaizen rewards,
attachments."""

from datetime import date
from decimal import Decimal

import psycopg
import pytest

from .test_challans_scrap_interunit import decide as decide_scrap
from .test_challans_scrap_interunit import draft_scrap
from .test_compound_batches import Fresh
from .test_costing import Rules
from .test_finance_core import F
from .test_sales_orders import S, setup_sales

pytestmark = pytest.mark.usefixtures("api_database")
G = "/api/v1/gst"


def sql(api_database, query, *params):
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute(query, params)


def invoiced_scrap(make_client, w, amount):
    sale = draft_scrap(make_client, w, amount=amount)
    sales = Fresh(make_client, "SALES")
    approval = sales.post(f"{S}/scrap-sales/{sale['scrap_sale_id']}/submit").json()["approval_id"]
    decide_scrap(make_client, approval, "PLANT_HEAD")
    decide_scrap(make_client, approval, "ACCOUNTS")
    return sales.post(f"{S}/scrap-sales/{sale['scrap_sale_id']}/invoice")


def books(make_client, unit):
    tb = Fresh(make_client, "ACCOUNTS").get(f"{F}/trial-balance?unit_id={unit}").json()
    assert tb["balanced"]
    return {r["code"]: Decimal(r["closing"]) for r in tb["rows"]}


def scrap_world(make_client, api_database):
    w = setup_sales(make_client)
    unit = w["unit"]["unit_id"]
    Fresh(make_client, "ACCOUNTS").post(f"{F}/accounts/seed-default", json={"unit_id": unit})
    sql(api_database, "update erp.unit set state_code = '29' where unit_id = %s", unit)
    return w, unit


def test_gst_on_scrap_follows_the_company_rates_and_reaches_the_books_and_gstr1(
        make_client, api_database):
    w, unit = scrap_world(make_client, api_database)
    with Rules(make_client, scrapGstRate="18.00"):  # GST is added on top of the amount
        done = invoiced_scrap(make_client, w, "10000")
    assert done.status_code == 200, done.text
    assert Decimal(done.json()["taxable_amount"]) == 10000 and Decimal(
        done.json()["tax_amount"]) == 1800
    got = books(make_client, unit)
    assert got["1100"] == 11800 and got["4100"] == -10000 and got["2100"] == -1800
    with Rules(make_client, scrapGstRate="18.00", scrapAmountIncludesGst=True):  # already in it
        inclusive = invoiced_scrap(make_client, w, "11800")
    assert Decimal(inclusive.json()["taxable_amount"]) == 10000 and Decimal(
        inclusive.json()["tax_amount"]) == 1800
    plain = invoiced_scrap(make_client, w, "5000")  # the default: no GST charged
    assert Decimal(plain.json()["tax_amount"]) == 0 and Decimal(plain.json()["taxable_amount"]) == 5000
    after = books(make_client, unit)
    assert after["2100"] == -3600 and after["4100"] == -(10000 + 10000 + 5000)
    first = Fresh(make_client, "ACCOUNTS").post(f"{G}/returns", json={
        "unit_id": unit, "month": date.today().isoformat(), "return_type": "GSTR1"})
    assert first.status_code == 200, first.text
    totals = first.json()["payload"]["totals"]
    assert totals["invoices"] == 3 and totals["taxable_amount"] == "25000.00"  # scrap is outward supply
    assert Decimal(totals["cgst"]) + Decimal(totals["sgst"]) == 3600


# ------------------------------------------------------------------ machine and labour cost
def test_machine_time_and_labour_enter_the_cost_of_finished_goods_as_the_company_sets(
        make_client, api_database):
    from .test_compound_batches import M  # noqa: PLC0415
    from .test_costing import cost_of, costed_shop  # noqa: PLC0415
    from .test_inventory_receipt import code  # noqa: PLC0415
    from .test_shop_floor import entry, machine  # noqa: PLC0415
    from .test_work_orders import start, work_order  # noqa: PLC0415

    w = costed_shop(make_client, api_database)
    mc = machine(make_client, w)
    rated = Fresh(make_client, "PROD").patch(f"{M}/machines/{mc['machine_id']}", json={
        "hourly_rate": "300", "labour_per_hour": "100"})
    assert rated.status_code == 200 and Decimal(rated.json()["hourly_rate"]) == 300
    assert Fresh(make_client, "STORES").patch(f"{M}/machines/{mc['machine_id']}", json={
        "hourly_rate": "1"}).status_code == 403
    wo = work_order(make_client, w, w["fg"], "100")
    lot = start(make_client, w, wo, "100").json()
    assert entry(make_client, w, wo, mc, good="100",
                 downtime=[{"reason": "Mould change", "minutes": 60}]).status_code == 201
    prod = Fresh(make_client, "PROD")
    prod.post(f"{M}/lots/{lot['prod_lot_id']}/move", json={})
    Fresh(make_client, "QA").post(f"{M}/lots/{lot['prod_lot_id']}/move", json={"rejects": "5"})
    store = Fresh(make_client, "ADMIN").post("/api/v1/org/stores", json={
        "unit_id": w["unit"]["unit_id"], "code": "FG" + code()[:4], "name": "Finished goods",
        "store_type": "FINISHED_GOODS"}).json()
    done = prod.post(f"{M}/lots/{lot['prod_lot_id']}/pack", json={"fg_store_id": store["store_id"]})
    assert done.status_code == 200, done.text
    c = cost_of(make_client, lot["prod_lot_id"])
    # 7 running hours at 300 + 100 = 2,800 over 100 good pieces = 28 a piece, 95 packed
    assert Decimal(c["conversion_cost"]) == Decimal("2660.00")
    assert abs(Decimal(c["unit_cost"]) - (Decimal(c["material_cost"]) + 2660) / 95) < Decimal(
        "0.000001")
    # switched off: the same lot costed without machine time and labour
    assert Decimal(Fresh(make_client, "ACCOUNTS").get(
        f"{F}/costing/lots/{lot['prod_lot_id']}").json()["conversion_cost"]) == Decimal("2660.00")
    with Rules(make_client, costMachineLabour=False):
        second = start(make_client, w, work_order(make_client, w, w["fg"], "50"), "50").json()
        prod.post(f"{M}/lots/{second['prod_lot_id']}/move", json={})
        Fresh(make_client, "QA").post(f"{M}/lots/{second['prod_lot_id']}/move", json={})
        packed = prod.post(f"{M}/lots/{second['prod_lot_id']}/pack",
                           json={"fg_store_id": store["store_id"]})
        assert packed.status_code == 200
        assert Decimal(cost_of(make_client, second["prod_lot_id"])["conversion_cost"]) == 0
