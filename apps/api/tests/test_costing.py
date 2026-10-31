"""Production costing, cost of sales, inter-unit postings and consolidation, all company-configurable."""

from decimal import Decimal

import psycopg
import pytest

from .test_bom import item as make_item
from .test_challans_scrap_interunit import inter_plant_transfer
from .test_compound_batches import Fresh, M, make_compound, ready_batch, stocked
from .test_dispatch_invoice import create, fg_lot, sales_world
from .test_finance_core import F
from .test_inventory_receipt import code
from .test_sales_orders import S
from .test_work_orders import approved_bom, run_route, start, work_order

pytestmark = pytest.mark.usefixtures("api_database")
RULE = "/api/v1/admin/settings"


class Rules:
    """Change company rules for a test and put them back afterwards."""

    def __init__(self, make_client, **values):
        self.make_client, self.values, self.old = make_client, values, {}

    def __enter__(self):
        for key, value in self.values.items():
            self.old[key] = Fresh(self.make_client, "ADMIN").get(f"/api/v1/settings/{key}").json()[
                "value"]
            done = Fresh(self.make_client, "ADMIN").put(f"{RULE}/{key}", json={"value": value})
            assert done.status_code == 200, done.text
        return self

    def __exit__(self, *exc):
        for key, value in self.old.items():
            Fresh(self.make_client, "ADMIN").put(f"{RULE}/{key}", json={"value": value})


def sql(api_database, query, *params):
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        cur = conn.execute(query, params)
        return cur.fetchone() if cur.description else None


def costed_shop(make_client, api_database, chart=False):
    """Like the shop of the work-order tests; the chart is set up first when asked for."""
    w = stocked(make_client)
    if chart:
        seeded = Fresh(make_client, "ACCOUNTS").post(f"{F}/accounts/seed-default",
                                                     json={"unit_id": w["unit"]["unit_id"]})
        assert seeded.status_code == 200, seeded.text
    admin = make_client("ADMIN")
    cmpd_item = make_item(admin, "WIP", "KG")
    compound = make_compound(make_client, w, output_item_id=cmpd_item["item_id"])
    batch = ready_batch(make_client, api_database, w, compound, "100")
    insert = make_item(Fresh(make_client, "ADMIN"), "WIP", "NOS", route_code="METAL")
    approved_bom(make_client, insert, [(w["items"][0], "1", "0")])
    metal_wo = work_order(make_client, w, insert, "200")
    metal_lot = start(make_client, w, metal_wo, "200").json()
    assert run_route(make_client, metal_lot["prod_lot_id"])["status"] == "READY"
    fg = make_item(Fresh(make_client, "ADMIN"), "FG", "NOS", route_code="MOULD")
    approved_bom(make_client, fg, [(cmpd_item, "0.1", "0"), (insert, "1", "0"),
                                   (w["items"][1], "0.05", "0")])
    w.update(batch=batch, insert=insert, fg=fg)
    w["batch_cost"] = sql(api_database, "select material_cost from erp.compound_batch where "
                          "batch_id = %s", batch)[0]
    return w


def pack_lot(make_client, w, qty="100", rejects="5"):
    wo = work_order(make_client, w, w["fg"], qty)
    lot = start(make_client, w, wo, qty).json()
    prod = Fresh(make_client, "PROD")
    prod.post(f"{M}/lots/{lot['prod_lot_id']}/move", json={})
    Fresh(make_client, "QA").post(f"{M}/lots/{lot['prod_lot_id']}/move", json={"rejects": rejects})
    store = make_client("ADMIN").post("/api/v1/org/stores", json={
        "unit_id": w["unit"]["unit_id"], "code": "FG" + code()[:4], "name": "Finished goods",
        "store_type": "FINISHED_GOODS"}).json()
    done = prod.post(f"{M}/lots/{lot['prod_lot_id']}/pack", json={"fg_store_id": store["store_id"]})
    assert done.status_code == 200, done.text
    return lot["prod_lot_id"]


def cost_of(make_client, lot_id):
    got = Fresh(make_client, "ACCOUNTS").get(f"{F}/costing/lots/{lot_id}")
    assert got.status_code == 200, got.text
    return got.json()


def expected_material(w):
    """5 units of raw material and 100 inserts at 50 each, plus a tenth of the batch."""
    return Decimal(5 * 50) + Decimal(100 * 50) + (w["batch_cost"] / 10)


def test_finished_goods_are_valued_at_the_actual_material_cost(make_client, api_database):
    w = costed_shop(make_client, api_database)
    lot = pack_lot(make_client, w)  # 100 started, 5 rejected, 95 packed
    c = cost_of(make_client, lot)
    assert Decimal(c["material_cost"]) == expected_material(w)
    assert c["cost_basis"] == "ACTUAL" and Decimal(c["conversion_cost"]) == 0
    assert abs(Decimal(c["unit_cost"]) - expected_material(w) / 95) < Decimal("0.000001")
    layer = sql(api_database, "select unit_cost from erp.cost_layer where lot_id = (select "
                "fg_lot_id from erp.packing where prod_lot_id = %s)", lot)[0]
    assert layer == Decimal(c["unit_cost"])  # the FIFO layer carries the cost, not zero


def test_the_company_sets_overhead_and_a_standard_cost_through_rules(make_client, api_database):
    w = costed_shop(make_client, api_database)
    with Rules(make_client, overheadPct=10):
        lot = pack_lot(make_client, w)
        c = cost_of(make_client, lot)
    material = expected_material(w)
    assert Decimal(c["conversion_cost"]) == (material / 10).quantize(Decimal("0.01"))
    assert c["cost_basis"] == "ACTUAL"
    fg = w["fg"]["item_id"]
    acct = Fresh(make_client, "ACCOUNTS")
    assert acct.put(f"{F}/costing/standard/{fg}", json={"standard_cost": "120"}).status_code == 200
    assert Fresh(make_client, "STORES").put(f"{F}/costing/standard/{fg}", json={
        "standard_cost": "1"}).status_code == 403
    with Rules(make_client, costMethod="STANDARD"):
        again = pack_lot(make_client, w, qty="40", rejects="0")
        s = cost_of(make_client, again)
        assert s["cost_basis"] == "STANDARD" and Decimal(s["unit_cost"]) == 120
        assert Decimal(s["variance"]) == 120 * 40 - Decimal(s["total_cost"])
        acct.put(f"{F}/costing/standard/{fg}", json={"standard_cost": None})
        missing = cost_of(make_client, pack_lot(make_client, w, qty="10", rejects="0"))
        assert missing["cost_basis"] == "ACTUAL"  # no standard set: valued actual


def test_production_is_booked_raw_material_to_work_in_progress_to_finished_goods(
        make_client, api_database):
    w = costed_shop(make_client, api_database, chart=True)
    lot = pack_lot(make_client, w)
    c = cost_of(make_client, lot)
    tb = Fresh(make_client, "ACCOUNTS").get(
        f"{F}/trial-balance?unit_id={w['unit']['unit_id']}").json()
    books = {r["code"]: Decimal(r["closing"]) for r in tb["rows"]}
    value = (Decimal(c["unit_cost"]) * 95).quantize(Decimal("0.01"))
    assert books["1220"] == value
    # raw material out: 5 units + 200 inserts' worth + the whole batch weighed
    assert books["1200"] == -(250 + 10000 + w["batch_cost"])
    # what is left in work in progress is the unused 90% of the batch and the 100 spare inserts
    assert abs(books["1210"] - (w["batch_cost"] * Decimal("0.9") + 5000)) <= Decimal("0.02")
    assert tb["balanced"]
    with Rules(make_client, productionPosting=False):
        quiet = pack_lot(make_client, w, qty="20", rejects="0")
        assert cost_of(make_client, quiet)["unit_cost"] is not None  # costed, just not booked
        after = Fresh(make_client, "ACCOUNTS").get(
            f"{F}/trial-balance?unit_id={w['unit']['unit_id']}").json()
        assert {r["code"]: Decimal(r["closing"]) for r in after["rows"]}["1220"] == value


def test_returned_material_comes_back_at_its_issue_cost_and_out_of_the_lot(
        make_client, api_database):
    w = costed_shop(make_client, api_database, chart=True)
    wo = work_order(make_client, w, w["fg"], "100")
    lot = start(make_client, w, wo, "100").json()
    raw = next(s for s in lot["sources"] if s["consumption_type"] == "RAW_MATERIAL")
    before = sql(api_database, "select material_cost from erp.prod_lot where prod_lot_id = %s",
                 lot["prod_lot_id"])[0]
    back = Fresh(make_client, "PROD").post(f"{M}/lots/{lot['prod_lot_id']}/return-material", json={
        "source_lot_no": raw["source"], "quantity": "2"})
    assert back.status_code == 200, back.text
    after = sql(api_database, "select material_cost from erp.prod_lot where prod_lot_id = %s",
                lot["prod_lot_id"])[0]
    assert before - after == 100  # 2 units at 50
    assert sql(api_database, "select returned_cost from erp.lot_consumption where prod_lot_id = %s "
               "and consumption_type = 'RAW_MATERIAL'", lot["prod_lot_id"])[0] == 100


# ------------------------------------------------------------------ cost of sales
def books_of(make_client, w):
    tb = Fresh(make_client, "ACCOUNTS").get(
        f"{F}/trial-balance?unit_id={w['unit']['unit_id']}").json()
    return {r["code"]: Decimal(r["closing"]) for r in tb["rows"]}


def dispatched(make_client, api_database, w, qty="50"):
    d = create(make_client, w, qty).json()
    assert Fresh(make_client, "SALES").post(
        f"{S}/dispatches/{d['dispatch_id']}/dispatch").status_code == 200
    return d


def sales_with_chart(make_client, api_database):
    w = sales_world(make_client, api_database)
    Fresh(make_client, "ACCOUNTS").post(f"{F}/accounts/seed-default",
                                        json={"unit_id": w["unit"]["unit_id"]})
    fg_lot(api_database, w, w["store"], "100")  # the stock layer costs 40 a piece
    return w


def test_cost_of_sales_is_booked_when_the_goods_are_dispatched(make_client, api_database):
    w = sales_with_chart(make_client, api_database)
    d = dispatched(make_client, api_database, w, "50")
    books = books_of(make_client, w)
    assert books["5000"] == 2000 and books["1220"] == -2000
    assert sql(api_database, "select cost_amount from erp.dispatch where dispatch_id = %s",
               d["dispatch_id"])[0] == 2000


def test_a_company_can_book_cost_of_sales_with_the_invoice_instead(make_client, api_database):
    w = sales_with_chart(make_client, api_database)
    with Rules(make_client, cogsTiming="ON_INVOICE"):
        d = dispatched(make_client, api_database, w, "50")
        assert "5000" not in books_of(make_client, w)  # nothing booked at dispatch
        sales = Fresh(make_client, "SALES")
        inv = sales.post(f"{S}/dispatches/{d['dispatch_id']}/invoice").json()
        assert books_of(make_client, w)["5000"] == 2000
        assert sales.post(f"{S}/invoices/{inv['invoice_id']}/cancel").status_code == 200
        assert books_of(make_client, w).get("5000", 0) == 0  # cost goes back with the sale


# ------------------------------------------------------------------ inter-unit and consolidation
def test_consolidation_eliminates_inter_unit_balances_and_reports_a_mismatch(
        make_client, api_database):
    w, plant2, t = inter_plant_transfer(make_client, api_database)
    units = [w["unit"]["unit_id"], plant2["unit_id"]]
    acct = Fresh(make_client, "ACCOUNTS")
    for unit in units:
        assert acct.post(f"{F}/accounts/seed-default", json={"unit_id": unit}).status_code == 200
    inv = acct.post(f"{S}/inter-unit-invoices", json={"transfer_id": t["transfer_id"]})
    assert inv.status_code == 201, inv.text
    value = Decimal(inv.json()["taxable_amount"])
    both = "?" + "&".join(f"unit_ids={u}" for u in units)
    got = acct.get(f"{F}/consolidation{both}").json()
    by = {r["code"]: r for r in got["rows"]}
    assert Decimal(by["1500"]["combined"]) == value and Decimal(by["1500"]["consolidated"]) == 0
    assert Decimal(by["2500"]["combined"]) == -value and by["4200"]["inter_unit"]
    assert Decimal(got["receivable_payable_difference"]) == 0
    assert Decimal(got["sales_purchases_difference"]) == 0 and got["balanced"]
    only = acct.get(f"{F}/consolidation?unit_ids={units[0]}").json()  # one side only: a mismatch
    assert Decimal(only["receivable_payable_difference"]) == value
    gone = acct.post(f"{S}/inter-unit-invoices/{inv.json()['inter_unit_invoice_id']}/cancel")
    assert gone.status_code == 200
    after = {r["code"]: r for r in acct.get(f"{F}/consolidation{both}").json()["rows"]}
    assert Decimal(after["1500"]["combined"]) == 0  # both sides reversed
    assert acct.get(f"{F}/consolidation?date_from=2030-01-01&date_to=2029-01-01"
                    ).status_code == 422
    assert Fresh(make_client, "STORES").get(f"{F}/consolidation").status_code == 403
