"""Reference data through the real API: payment terms, tax codes, categories, units of measure."""

from uuid import uuid4

import psycopg
import pytest

from .conftest import API_DB, API_PASSWORD, API_ROLE
from .dbutil import url_for

pytestmark = pytest.mark.usefixtures("api_database")


def code() -> str:
    return uuid4().hex[:10].upper()


def test_payment_terms_create_update_and_validate(make_client):
    client = make_client("ACCOUNTS")
    c = code()
    created = client.post("/api/v1/ref/payment-terms",
                          json={"code": c, "name": "Net 45", "due_days": 45})
    assert created.status_code == 201 and created.json()["due_days"] == 45
    term_id = created.json()["payment_term_id"]
    assert client.post("/api/v1/ref/payment-terms",
                       json={"code": c, "name": "Dup", "due_days": 1}).status_code == 409
    for bad in (-1, 366):
        assert client.post("/api/v1/ref/payment-terms",
                           json={"code": code(), "name": "X", "due_days": bad}).status_code == 422
    patched = client.patch(f"/api/v1/ref/payment-terms/{term_id}", json={"due_days": 30})
    assert patched.status_code == 200 and patched.json()["due_days"] == 30
    assert client.patch(f"/api/v1/ref/payment-terms/{term_id}",
                        json={"due_days": None}).status_code == 422
    assert client.patch(f"/api/v1/ref/payment-terms/{uuid4()}",
                        json={"name": "x"}).status_code == 404


def test_tax_codes_rates_and_deactivation(make_client):
    client = make_client("ACCOUNTS")
    created = client.post("/api/v1/ref/tax-codes",
                          json={"code": code(), "description": "GST 18%", "gst_rate": "18"})
    assert created.status_code == 201 and float(created.json()["gst_rate"]) == 18.0
    for bad in ("-1", "100.01", "abc"):
        assert client.post("/api/v1/ref/tax-codes", json={
            "code": code(), "description": "x", "gst_rate": bad}).status_code == 422
    tax_id = created.json()["tax_code_id"]
    assert client.patch(f"/api/v1/ref/tax-codes/{tax_id}", json={"active": False}).status_code == 200
    inactive = client.get("/api/v1/ref/tax-codes?active=false&limit=200").json()
    assert tax_id in [t["tax_code_id"] for t in inactive["items"]]
    active = client.get("/api/v1/ref/tax-codes?active=true&limit=200").json()
    assert tax_id not in [t["tax_code_id"] for t in active["items"]]


def test_item_category_tree_rules(make_client):
    client = make_client("PLANT_HEAD")
    make = lambda **b: client.post("/api/v1/ref/item-categories",  # noqa: E731
                                   json={"code": code(), "name": "Cat", **b})
    root = make().json()
    child = make(parent_category_id=root["category_id"]).json()
    assert child["parent_category_id"] == root["category_id"]
    base = "/api/v1/ref/item-categories/"
    assert client.patch(base + root["category_id"],
                        json={"parent_category_id": child["category_id"]}).status_code == 422
    assert client.patch(base + root["category_id"],
                        json={"parent_category_id": root["category_id"]}).status_code == 422
    assert make(parent_category_id=str(uuid4())).status_code == 422
    assert client.patch(base + child["category_id"],
                        json={"parent_category_id": None}).json()["parent_category_id"] is None


def test_only_reference_managers_can_write(make_client):
    for role in ("STORES", "SALES", "PURCHASE", "OPERATOR", "QA"):
        client = make_client(role)
        assert client.get("/api/v1/ref/payment-terms").status_code == 200
        write = client.post("/api/v1/ref/tax-codes",
                            json={"code": code(), "description": "x", "gst_rate": "5"})
        assert write.status_code == 403, role


def test_reference_data_is_tenant_isolated_and_rejects_tenant_ids(make_client, api_database):
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        conn.execute("insert into erp.payment_term(tenant_id, code, name, due_days) "
                     "values (%s, 'THEIRS', 'Tenant B term', 7)", (api_database["tenant_b"],))
    client = make_client("ADMIN")
    codes = [t["code"] for t in client.get("/api/v1/ref/payment-terms?limit=200").json()["items"]]
    assert "THEIRS" not in codes
    assert client.post("/api/v1/ref/payment-terms", json={
        "code": code(), "name": "x", "due_days": 1,
        "tenant_id": api_database["tenant_b"]}).status_code == 422
    # The same code can exist in another tenant, so "THEIRS" is free for this one.
    assert client.post("/api/v1/ref/payment-terms",
                       json={"code": "THEIRS", "name": "Mine", "due_days": 1}).status_code == 201


def test_units_of_measure_are_seeded_and_read_only(make_client):
    client = make_client("OPERATOR")
    uoms = {u["code"]: u for u in client.get("/api/v1/ref/uom").json()}
    assert uoms["KG"]["decimal_places"] == 3 and uoms["NOS"]["decimal_places"] == 0
    assert client.post("/api/v1/ref/uom", json={"code": "X", "name": "X"}).status_code == 405
    with psycopg.connect(url_for(API_DB, API_ROLE, API_PASSWORD)) as app:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app.execute("insert into erp.uom(code, name) values ('HACK', 'x')")


def test_reference_changes_are_audited(make_client, api_database):
    client = make_client("ACCOUNTS")
    term = client.post("/api/v1/ref/payment-terms",
                       json={"code": code(), "name": "A", "due_days": 5}).json()
    client.patch(f"/api/v1/ref/payment-terms/{term['payment_term_id']}", json={"name": "B"})
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        actions = conn.execute(
            "select action from erp.audit_log where entity_id = %s order by audit_id",
            (term["payment_term_id"],)).fetchall()
    assert actions == [("payment_term.create",), ("payment_term.update",)]
