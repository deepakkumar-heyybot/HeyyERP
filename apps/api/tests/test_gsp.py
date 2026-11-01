"""E-invoice (IRN) and e-way bill through the GST provider: the data it needs, retries, cancels."""

from uuid import uuid4

import psycopg
import pytest

from heyyerp_api.gsp import providers
from heyyerp_api.gsp.providers import GspError, MockGsp

from .test_compound_batches import Fresh
from .test_costing import Rules, dispatched
from .test_dispatch_invoice import fg_lot, sales_world
from .test_sales_orders import S

pytestmark = pytest.mark.usefixtures("api_database")
ADMIN = "/api/v1/admin"
MAHARASHTRA, KARNATAKA = "27", "29"


def gstin(state):
    return state + uuid4().hex[:10].upper() + "1Z5"


def sql(api_database, query, *params):
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else None


class Recorder(MockGsp):
    """The mock provider that keeps what it was asked and can be made to fail."""

    def __init__(self):
        self.irn_requests, self.ewb_requests, self.cancelled = [], [], []
        self.fail = None

    async def generate_irn(self, payload):
        if self.fail:
            raise self.fail
        self.irn_requests.append(payload)
        return await super().generate_irn(payload)

    async def generate_ewb(self, payload):
        self.ewb_requests.append(payload)
        return await super().generate_ewb(payload)

    async def cancel_irn(self, irn, reason, remark):
        self.cancelled.append(("irn", irn, reason))


@pytest.fixture
def gsp(monkeypatch):
    fake = Recorder()
    monkeypatch.setitem(providers.override, "gsp", fake)
    return fake


def invoice_of(make_client, api_database, buyer=MAHARASHTRA, address=True, qty="50"):
    """A dispatched, invoiced order whose plant and customer carry GSTINs (and addresses)."""
    w = sales_world(make_client, api_database)
    fg_lot(api_database, w, w["store"], "100")
    w["seller"] = gstin(MAHARASHTRA)
    sql(api_database, "update erp.unit set gstin = %s, state_code = '27' where unit_id = %s",
        w["seller"], w["unit"]["unit_id"])
    sql(api_database, "update erp.customer set gstin = %s where customer_id = %s",
        gstin(buyer), w["customer"]["customer_id"])
    sql(api_database, "update erp.item set hsn_code = '40169990' where item_id = %s",
        w["item"]["item_id"])
    if address:
        set_addresses(make_client, w)
    d = dispatched(make_client, api_database, w, qty)
    inv = Fresh(make_client, "SALES").post(f"{S}/dispatches/{d['dispatch_id']}/invoice").json()
    return w, inv


def set_addresses(make_client, w):
    body = {"address_line": "Plot 5, MIDC", "city": "Pune", "pincode": "411019"}
    assert Fresh(make_client, "ADMIN").put(
        f"/api/v1/org/units/{w['unit']['unit_id']}/address", json=body).status_code == 200
    assert Fresh(make_client, "SALES").put(
        f"/api/v1/parties/customers/{w['customer']['customer_id']}/address",
        json=body).status_code == 200


def status_of(make_client, inv):
    return Fresh(make_client, "SALES").get(f"{S}/invoices/{inv['invoice_id']}/einvoice").json()


def run_jobs(make_client):
    done = Fresh(make_client, "ADMIN").post(f"{ADMIN}/jobs/run")
    assert done.status_code == 200, done.text
    return done.json()


def test_an_irn_is_raised_with_the_right_tax_split_and_only_once(make_client, api_database, gsp):
    w, inv = invoice_of(make_client, api_database)
    sales = Fresh(make_client, "SALES")
    url = f"{S}/invoices/{inv['invoice_id']}/irn"
    assert sales.post(url).status_code == 409  # e-invoicing is off until the company turns it on
    with Rules(make_client, eInvoice="MANUAL", ewbThreshold="10000"):
        got = sales.post(url)
        assert got.status_code == 200, got.text
        e = got.json()
        assert e["irn_status"] == "GENERATED" and len(e["irn"]) == 64 and e["signed_qr"]
        assert e["required"] and e["ewb_needed"]  # 11,800 is over the 10,000 the company set
        sent = gsp.irn_requests[0]
        assert sent["SellerDtls"]["Gstin"] == w["seller"] and sent["BuyerDtls"]["Pin"] == 411019
        assert sent["ValDtls"]["IgstVal"] == 0 and sent["ValDtls"]["CgstVal"] == sent["ValDtls"][
            "SgstVal"] == 900.0  # same state: CGST + SGST
        assert sent["ItemList"][0]["HsnCd"] == "40169990"
        assert sales.post(url).status_code == 409  # one IRN per invoice
        assert Fresh(make_client, "PROD").post(url).status_code == 403
        listed = sales.get(f"{S}/invoices/{inv['invoice_id']}").json()
        assert listed["irn"] == e["irn"]


def test_another_state_gets_igst_and_missing_data_is_listed_then_fixed(
        make_client, api_database, gsp):
    w, inv = invoice_of(make_client, api_database, buyer=KARNATAKA, address=False)
    sales = Fresh(make_client, "SALES")
    url = f"{S}/invoices/{inv['invoice_id']}/irn"
    with Rules(make_client, eInvoice="MANUAL"):
        failed = sales.post(url).json()
        assert failed["irn_status"] == "FAILED"
        assert "address" in failed["gsp_error"] and "pincode" in failed["gsp_error"]
        bad = Fresh(make_client, "ADMIN").put(
            f"/api/v1/org/units/{w['unit']['unit_id']}/address",
            json={"address_line": "Plot 5", "city": "Pune", "pincode": "012345"})
        assert bad.status_code == 422  # a pincode does not start with 0
        set_addresses(make_client, w)
        jobs = Fresh(make_client, "ADMIN").get(f"{ADMIN}/gsp-jobs").json()
        job = next(j for j in jobs if j["invoice_id"] == inv["invoice_id"])
        assert job["status"] == "FAILED" and "address" in job["last_error"]
        assert Fresh(make_client, "SALES").get(f"{ADMIN}/gsp-jobs").status_code == 403
        again = Fresh(make_client, "ADMIN").post(f"{ADMIN}/gsp-jobs/{job['job_id']}/retry")
        assert again.json() == {"outcome": "DONE"}
        assert status_of(make_client, inv)["irn_status"] == "GENERATED"
        sent = gsp.irn_requests[0]["ValDtls"]
        assert sent["IgstVal"] == 1800.0 and sent["CgstVal"] == 0
        assert Fresh(make_client, "ADMIN").post(
            f"{ADMIN}/gsp-jobs/{job['job_id']}/retry").status_code == 409  # it is done now


def test_a_consumer_sale_has_no_e_invoice(make_client, api_database, gsp):
    w, inv = invoice_of(make_client, api_database)
    sql(api_database, "update erp.customer set gstin = null where customer_id = %s",
        w["customer"]["customer_id"])
    with Rules(make_client, eInvoice="MANUAL"):
        refused = Fresh(make_client, "SALES").post(f"{S}/invoices/{inv['invoice_id']}/irn")
        assert refused.status_code == 422 and "GSTIN" in refused.text
        assert status_of(make_client, inv)["required"] is False


def test_a_provider_that_is_down_is_retried_by_the_worker_then_given_up(
        make_client, api_database, gsp):
    w, inv = invoice_of(make_client, api_database)
    sales = Fresh(make_client, "SALES")
    gsp.fail = GspError("timeout")
    with Rules(make_client, eInvoice="MANUAL", gspMaxAttempts=3):
        queued = sales.post(f"{S}/invoices/{inv['invoice_id']}/irn").json()
        assert queued["irn_status"] == "QUEUED" and queued["pending_jobs"] == ["IRN_GENERATE"]
        assert queued["gsp_error"] == "timeout"
        assert run_jobs(make_client)["gsp_done"] == 0  # not due yet: the delay has not passed
        sql(api_database, "update erp.integration_job set next_attempt_at = now() - interval "
            "'1 min' where status = 'RETRY'")
        assert run_jobs(make_client)["gsp_retry"] == 1  # the second try failed too
        gsp.fail = None  # the provider is back
        sql(api_database, "update erp.integration_job set next_attempt_at = now() - interval "
            "'1 min' where status = 'RETRY'")
        assert run_jobs(make_client)["gsp_done"] == 1
        done = status_of(make_client, inv)
        assert done["irn_status"] == "GENERATED" and done["pending_jobs"] == []
    w2, inv2 = invoice_of(make_client, api_database)
    gsp.fail = GspError("rejected", retryable=False)  # waiting would not help
    with Rules(make_client, eInvoice="MANUAL"):
        sales.post(f"{S}/invoices/{inv2['invoice_id']}/irn")
        assert status_of(make_client, inv2)["irn_status"] == "FAILED"


def test_auto_mode_queues_the_irn_for_a_customer_with_a_gstin_only(
        make_client, api_database, gsp):
    with Rules(make_client, eInvoice="AUTO"):
        w, inv = invoice_of(make_client, api_database)
        assert status_of(make_client, inv)["irn_status"] == "QUEUED"
        assert run_jobs(make_client)["gsp_done"] >= 1
        assert status_of(make_client, inv)["irn_status"] == "GENERATED"
        w2 = sales_world(make_client, api_database)  # a customer with no GSTIN
        fg_lot(api_database, w2, w2["store"], "100")
        d = dispatched(make_client, api_database, w2, "5")
        inv2 = Fresh(make_client, "SALES").post(
            f"{S}/dispatches/{d['dispatch_id']}/invoice").json()
        assert status_of(make_client, inv2)["irn_status"] == "NONE"


def test_the_e_way_bill_and_the_cancels_unblock_cancelling_the_invoice(
        make_client, api_database, gsp):
    w, inv = invoice_of(make_client, api_database)
    sales = Fresh(make_client, "SALES")
    base = f"{S}/invoices/{inv['invoice_id']}"
    with Rules(make_client, eInvoice="MANUAL"):
        assert sales.post(f"{base}/ewb", json={
            "vehicle_no": "bad", "distance_km": 100}).status_code == 422
        assert sales.post(f"{base}/irn").status_code == 200
        ewb = sales.post(f"{base}/ewb", json={"vehicle_no": "mh 12-ab 1234", "distance_km": 450})
        assert ewb.status_code == 200, ewb.text
        e = ewb.json()
        assert e["ewb_no"] and e["ewb_vehicle_no"] == "MH12AB1234" and not e["ewb_needed"]
        assert gsp.ewb_requests[0]["transDistance"] == 450 and gsp.ewb_requests[0]["transMode"] == 1
        assert sales.post(f"{base}/ewb", json={
            "vehicle_no": "MH12AB1234", "distance_km": 10}).status_code == 409
        assert sales.post(f"{base}/cancel").status_code == 409  # numbers still live
        reason = {"reason": "2", "remark": "wrong qty"}
        assert sales.post(f"{base}/irn/cancel", json=reason).status_code == 409  # EWB first
        assert sales.post(f"{base}/ewb/cancel", json=reason).json()["ewb_no"] is None
        cancelled = sales.post(f"{base}/irn/cancel", json=reason).json()
        assert cancelled["irn_status"] == "CANCELLED" and cancelled["irn_cancelled_at"]
        assert gsp.cancelled[0][0] == "irn" and gsp.cancelled[0][2] == "2"
        assert sales.post(f"{base}/irn").status_code == 409  # a cancelled IRN is not re-issued
        assert sales.post(f"{base}/cancel").status_code == 200  # now the invoice can be cancelled


def test_an_irn_older_than_a_day_cannot_be_cancelled(make_client, api_database, gsp):
    w, inv = invoice_of(make_client, api_database)
    sales = Fresh(make_client, "SALES")
    with Rules(make_client, eInvoice="MANUAL"):
        sales.post(f"{S}/invoices/{inv['invoice_id']}/irn")
        sql(api_database, "update erp.customer_invoice set irn_ack_date = now() - interval "
            "'25 hours' where invoice_id = %s", inv["invoice_id"])
        late = sales.post(f"{S}/invoices/{inv['invoice_id']}/irn/cancel", json={
            "reason": "1", "remark": "duplicate"})
        assert late.status_code == 409 and "24 hours" in late.text


def test_the_mock_provider_is_refused_in_production(monkeypatch):
    from heyyerp_api.config import get_settings  # noqa: PLC0415

    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(GspError):
            providers.provider()
    finally:
        get_settings.cache_clear()
