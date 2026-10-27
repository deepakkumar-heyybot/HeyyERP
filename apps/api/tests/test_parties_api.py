"""Customers and vendors through the real API (needs a database)."""

from uuid import uuid4

import psycopg
import pytest

from heyyerp_api.masters.validation import _gstin_check_char

pytestmark = pytest.mark.usefixtures("api_database")


def code() -> str:
    return uuid4().hex[:10].upper()


def identifiers() -> tuple[str, str]:
    """A fresh (gstin, pan) pair that is mutually consistent and passes the check digit."""
    letters = "".join(chr(65 + int(c, 16) % 26) for c in uuid4().hex[:5])
    pan = f"{letters}{int(uuid4().int % 9000) + 1000}A"
    body = f"27{pan}1Z"
    return body + _gstin_check_char(body), pan


def term(client) -> str:
    response = client.post("/api/v1/ref/payment-terms",
                           json={"code": code(), "name": "Net 30", "due_days": 30})
    assert response.status_code == 201, response.text
    return response.json()["payment_term_id"]


# ------------------------------------------------------------------ customers
def test_sales_creates_customers_with_terms_and_credit_limit(make_client):
    admin = make_client("ADMIN")
    term_id = term(admin)
    sales = make_client("SALES")
    gstin, _ = identifiers()
    created = sales.post("/api/v1/parties/customers", json={
        "code": code(), "name": "Maruti Suzuki", "gstin": gstin,
        "payment_term_id": term_id, "credit_limit": "2500000.50"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["gstin"] == gstin and body["payment_term_id"] == term_id
    assert float(body["credit_limit"]) == 2500000.50 and body["active"] is True


def test_customer_validation(make_client):
    client = make_client("SALES")
    post = lambda **b: client.post("/api/v1/parties/customers",  # noqa: E731
                                   json={"code": code(), "name": "C", **b})
    assert post(gstin="27AAPFU0939F1ZW").status_code == 422
    assert post(credit_limit="-1").status_code == 422
    assert post(credit_limit="1.234").status_code == 422
    assert post(payment_term_id=str(uuid4())).status_code == 422
    assert post(gstin="").json()["gstin"] is None  # blank means none
    c = code()
    assert client.post("/api/v1/parties/customers", json={"code": c, "name": "A"}).status_code == 201
    assert client.post("/api/v1/parties/customers", json={"code": c, "name": "B"}).status_code == 409


def test_a_customer_may_share_a_gstin_across_billing_codes(make_client):
    client = make_client("SALES")
    gstin, _ = identifiers()
    for _ in range(2):
        assert client.post("/api/v1/parties/customers", json={
            "code": code(), "name": "Same legal entity", "gstin": gstin}).status_code == 201


def test_customer_update_deactivate_search_and_missing(make_client):
    client = make_client("PLANT_HEAD")
    c = code()
    cid = client.post("/api/v1/parties/customers",
                      json={"code": c, "name": "Hero Moto Corp"}).json()["customer_id"]
    url = f"/api/v1/parties/customers/{cid}"
    assert client.patch(url, json={"credit_limit": "100", "active": False}).json()["active"] is False
    assert client.patch(url, json={"credit_limit": None}).json()["credit_limit"] is None
    assert client.patch(url, json={"name": None}).status_code == 422
    found = client.get("/api/v1/parties/customers?q=hero%20moto&active=false").json()
    assert cid in [x["customer_id"] for x in found["items"]]
    assert client.get("/api/v1/parties/customers?q=%25").json()["total"] == 0  # wildcard is literal
    assert client.get(f"/api/v1/parties/customers/{uuid4()}").status_code == 404


# ------------------------------------------------------------------ vendors
def test_purchase_creates_a_vendor_that_starts_pending(make_client):
    client = make_client("PURCHASE")
    gstin, pan = identifiers()
    created = client.post("/api/v1/parties/vendors", json={
        "code": code(), "name": "Reliance Polymers", "gstin": gstin, "pan": pan, "msme_flag": True})
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "PENDING_APPROVAL" and created.json()["msme_flag"] is True


def test_client_cannot_set_vendor_status(make_client):
    client = make_client("ADMIN")
    response = client.post("/api/v1/parties/vendors", json={
        "code": code(), "name": "Pushy", "status": "APPROVED"})
    assert response.status_code == 422
    vendor_id = client.post("/api/v1/parties/vendors",
                            json={"code": code(), "name": "V"}).json()["vendor_id"]
    assert client.patch(f"/api/v1/parties/vendors/{vendor_id}",
                        json={"status": "APPROVED"}).status_code == 422


def test_vendor_identifier_checks(make_client):
    client = make_client("PURCHASE")
    gstin, pan = identifiers()
    post = lambda **b: client.post("/api/v1/parties/vendors",  # noqa: E731
                                   json={"code": code(), "name": "V", **b})
    assert post(gstin="27AAPFU0939F1ZW").status_code == 422
    assert post(pan="BADPAN").status_code == 422
    other_pan = identifiers()[1]
    assert post(gstin=gstin, pan=other_pan).status_code == 422  # PAN is inside the GSTIN
    assert post(gstin=gstin, pan=pan).status_code == 201
    assert post(gstin=gstin).status_code == 409  # one vendor per GSTIN
    assert post(pan=pan).status_code == 201  # PAN alone may repeat (many GSTINs per PAN)


def test_vendor_update_and_deactivate(make_client):
    admin = make_client("ADMIN")
    term_id = term(admin)
    client = make_client("PURCHASE")
    vendor_id = client.post("/api/v1/parties/vendors",
                            json={"code": code(), "name": "V"}).json()["vendor_id"]
    url = f"/api/v1/parties/vendors/{vendor_id}"
    patched = client.patch(url, json={"payment_term_id": term_id, "msme_flag": True})
    assert patched.status_code == 200 and patched.json()["payment_term_id"] == term_id
    assert client.patch(url, json={"payment_term_id": str(uuid4())}).status_code == 422
    gstin, pan = identifiers()
    assert client.patch(url, json={"gstin": gstin, "pan": identifiers()[1]}).status_code == 422
    assert client.patch(url, json={"gstin": gstin, "pan": pan}).status_code == 200
    assert client.post(url + "/deactivate").json()["status"] == "INACTIVE"
    assert client.post(url + "/deactivate").status_code == 409
    listed = client.get("/api/v1/parties/vendors?vendor_status=INACTIVE&limit=200").json()
    assert vendor_id in [v["vendor_id"] for v in listed["items"]]


# ------------------------------------------------------------------ permissions & tenancy
def test_each_party_type_has_its_own_owner_role(make_client):
    body = lambda: {"code": code(), "name": "X"}  # noqa: E731
    sales, purchase = make_client("SALES"), make_client("PURCHASE")
    assert purchase.post("/api/v1/parties/customers", json=body()).status_code == 403
    assert purchase.post("/api/v1/parties/vendors", json=body()).status_code == 201
    sales = make_client("SALES")
    assert sales.post("/api/v1/parties/vendors", json=body()).status_code == 403
    assert sales.post("/api/v1/parties/customers", json=body()).status_code == 201
    for role in ("STORES", "QA", "OPERATOR", "ACCOUNTS"):
        other = make_client(role)
        assert other.get("/api/v1/parties/customers").status_code == 200
        assert other.get("/api/v1/parties/vendors").status_code == 200
        assert other.post("/api/v1/parties/customers", json=body()).status_code == 403
        assert other.post("/api/v1/parties/vendors", json=body()).status_code == 403


def test_other_tenants_partners_are_invisible(make_client, api_database):
    customer_b, vendor_b = uuid4(), uuid4()
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("insert into erp.customer(customer_id, tenant_id, code, name) "
                     "values (%s, %s, 'THEIR-C', 'Tenant B customer')",
                     (customer_b, api_database["tenant_b"]))
        conn.execute("insert into erp.vendor(vendor_id, tenant_id, code, name) "
                     "values (%s, %s, 'THEIR-V', 'Tenant B vendor')",
                     (vendor_b, api_database["tenant_b"]))
    client = make_client("ADMIN")
    assert client.get(f"/api/v1/parties/customers/{customer_b}").status_code == 404
    assert client.get(f"/api/v1/parties/vendors/{vendor_b}").status_code == 404
    assert client.patch(f"/api/v1/parties/vendors/{vendor_b}", json={"name": "x"}).status_code == 404
    assert client.post(f"/api/v1/parties/vendors/{vendor_b}/deactivate").status_code == 404
    assert client.post("/api/v1/parties/customers", json={
        "code": "THEIR-C", "name": "Mine now"}).status_code == 201  # codes are per tenant


def test_party_changes_are_audited(make_client, api_database):
    client = make_client("PURCHASE")
    vendor_id = client.post("/api/v1/parties/vendors",
                            json={"code": code(), "name": "V"}).json()["vendor_id"]
    client.patch(f"/api/v1/parties/vendors/{vendor_id}", json={"name": "W"})
    client.post(f"/api/v1/parties/vendors/{vendor_id}/deactivate")
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        actions = conn.execute("select action from erp.audit_log where entity_id = %s "
                               "order by audit_id", (vendor_id,)).fetchall()
    assert actions == [("vendor.create",), ("vendor.update",), ("vendor.deactivate",)]
