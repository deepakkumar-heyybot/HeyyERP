"""Encrypted bank account numbers, and the approval rule option that keeps a second approver."""

import base64
import os

import psycopg
import pytest
from fastapi import HTTPException

from heyyerp_api import crypto
from heyyerp_api.config import get_settings

from .conftest import TEST_ENCRYPTION_KEY
from .test_compound_batches import Fresh
from .test_payables import F, enter, payables_world
from .test_rfq import make_vendors

pytestmark = pytest.mark.usefixtures("api_database")
RULES = "/api/v1/admin/approval-rules"
A = "/api/v1/approvals"


# ------------------------------------------------------------------ the crypto helper
@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", TEST_ENCRYPTION_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_a_value_round_trips_and_every_encryption_looks_different(with_key):
    one = crypto.encrypt("123456789012", "vendor-1")
    two = crypto.encrypt("123456789012", "vendor-1")
    assert one.startswith("v1:") and one != two and "123456789012" not in one
    assert crypto.decrypt(one, "vendor-1") == crypto.decrypt(two, "vendor-1") == "123456789012"


def test_a_value_cannot_be_read_on_another_record_or_after_tampering(with_key):
    sealed = crypto.encrypt("9876543210", "vendor-1")
    with pytest.raises(HTTPException) as moved:
        crypto.decrypt(sealed, "vendor-2")  # copied onto another vendor's row
    assert moved.value.status_code == 500
    raw = bytearray(base64.b64decode(sealed[3:]))
    raw[-1] ^= 1
    with pytest.raises(HTTPException):
        crypto.decrypt("v1:" + base64.b64encode(bytes(raw)).decode(), "vendor-1")
    with pytest.raises(HTTPException):
        crypto.decrypt("v9:" + sealed[3:], "vendor-1")  # unknown key version


def test_without_a_valid_key_nothing_is_stored_in_the_clear(monkeypatch):
    for bad in ("", base64.b64encode(os.urandom(16)).decode(), "not base64!!"):
        monkeypatch.setenv("DATA_ENCRYPTION_KEY", bad)
        get_settings.cache_clear()
        with pytest.raises(HTTPException) as refused:
            crypto.encrypt("123456789012", "vendor-1")
        assert refused.value.status_code == 503
    get_settings.cache_clear()


# ------------------------------------------------------------------ bank accounts end to end
def test_bank_account_numbers_are_encrypted_in_the_database_and_masked_in_answers(
        make_client, api_database):
    w = payables_world(make_client)
    vendor = w["vendor"]["vendor_id"]
    acct = Fresh(make_client, "ACCOUNTS")
    put = acct.put(f"{F}/vendors/{vendor}/bank-account", json={
        "account_holder": "Vendor Co", "account_no": "123456789012", "ifsc": "HDFC0001234"})
    assert put.status_code == 200 and put.json()["account_no_masked"] == "********9012"
    assert "123456789012" not in put.text
    got = acct.get(f"{F}/vendors/{vendor}/bank-account")
    assert got.json()["account_no_masked"] == "********9012" and "123456789012" not in got.text
    with psycopg.connect(api_database["owner"]) as conn:
        enc, last4 = conn.execute(
            "select account_no_enc, account_last4 from erp.vendor_bank_account where vendor_id = %s",
            (vendor,)).fetchone()
        columns = [c[0] for c in conn.execute(
            "select column_name from information_schema.columns where table_schema = 'erp' and "
            "table_name = 'vendor_bank_account'")]
    assert enc.startswith("v1:") and "123456789012" not in enc and last4 == "9012"
    assert "account_no" not in columns  # no plaintext column is left to leak
    assert acct.put(f"{F}/vendors/{vendor}/bank-account", json={
        "account_holder": "Vendor Co", "account_no": "12345", "ifsc": "HDFC0001234"
    }).status_code == 422  # too short


def test_the_bank_file_decrypts_and_a_missing_key_stops_the_feature(
        make_client, api_database, monkeypatch):
    w = payables_world(make_client)
    vendor = w["vendor"]["vendor_id"]
    acct = Fresh(make_client, "ACCOUNTS")
    acct.put(f"{F}/vendors/{vendor}/bank-account", json={
        "account_holder": "Vendor Co", "account_no": "555566667777", "ifsc": "SBIN0004321"})
    bill = enter(make_client, w, qty="100", invoice="ENC-1").json()
    acct.post(f"{F}/bills/{bill['bill_id']}/book")
    run = acct.post(f"{F}/payment-runs", json={"unit_id": w["unit"]["unit_id"]}).json()
    sheet = acct.get(f"{F}/payment-runs/{run[0]['run_id']}/bank-file")
    assert sheet.status_code == 200 and "555566667777" in sheet.text and "SBIN0004321" in sheet.text
    other = make_vendors(make_client, 1)[0]
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "")  # the key is lost or not configured
    get_settings.cache_clear()
    refused = Fresh(make_client, "ACCOUNTS").put(
        f"{F}/vendors/{other['vendor_id']}/bank-account", json={
            "account_holder": "Other Co", "account_no": "111122223333", "ifsc": "HDFC0001234"})
    assert refused.status_code == 503 and "DATA_ENCRYPTION_KEY" in refused.json()["detail"]
    with psycopg.connect(api_database["owner"]) as conn:
        assert conn.execute("select count(*) from erp.vendor_bank_account where vendor_id = %s",
                            (other["vendor_id"],)).fetchone()[0] == 0  # nothing stored


# ------------------------------------------------------------------ the approval rule option
def bill_rule(make_client):
    rules = Fresh(make_client, "ADMIN").get(f"{RULES}?document_type=bill").json()
    return next(r for r in rules if r["name"] == "R-BILL")


def put_rule(make_client, rule, skip):
    return Fresh(make_client, "ADMIN").put(f"{RULES}/{rule['rule_id']}", json={
        "name": rule["name"], "document_type": "bill", "priority": rule["priority"],
        "sla_hours": rule["sla_hours"], "skip_requester_role": skip,
        "steps": [{"kind": s["kind"], "role_code": s["role_code"], "user_id": s["user_id"]}
                  for s in rule["steps"]]})


def exception_bill(make_client, w, invoice):
    bill = enter(make_client, w, qty="150", invoice=invoice).json()
    assert bill["status"] == "EXCEPTION"
    return bill


def test_by_default_a_bill_exception_needs_a_second_accounts_person(make_client):
    w = payables_world(make_client)
    assert bill_rule(make_client)["skip_requester_role"] is False
    bill = exception_bill(make_client, w, "SECOND-1")
    sent = Fresh(make_client, "ACCOUNTS").post(f"{F}/bills/{bill['bill_id']}/submit")
    assert sent.status_code == 200 and sent.json()["status"] == "PENDING_APPROVAL"
    approval = sent.json()["approval_id"]
    own = Fresh(make_client, "ACCOUNTS").post(f"{A}/{approval}/decide", json={
        "decision": "APPROVE", "note": None})
    assert own.status_code == 403 and "own submission" in own.json()["detail"]
    colleague = Fresh(make_client, "ACCOUNTS", subject="u-accounts-two")
    done = colleague.post(f"{A}/{approval}/decide", json={"decision": "APPROVE", "note": None})
    assert done.status_code == 200
    assert Fresh(make_client, "ACCOUNTS").get(f"{F}/bills/{bill['bill_id']}").json()[
        "status"] == "APPROVED"


def test_the_company_admin_can_switch_the_option_per_rule(make_client):
    w = payables_world(make_client)
    rule = bill_rule(make_client)
    try:
        assert put_rule(make_client, rule, True).json()["skip_requester_role"] is True
        bill = exception_bill(make_client, w, "SKIP-1")
        auto = Fresh(make_client, "ACCOUNTS").post(f"{F}/bills/{bill['bill_id']}/submit").json()
        assert auto["status"] == "APPROVED"  # the old behaviour: the requester's step is skipped
        assert put_rule(make_client, rule, False).json()["skip_requester_role"] is False
        again = exception_bill(make_client, w, "SKIP-2")
        held = Fresh(make_client, "ACCOUNTS").post(f"{F}/bills/{again['bill_id']}/submit").json()
        assert held["status"] == "PENDING_APPROVAL"
    finally:
        put_rule(make_client, rule, False)
    assert Fresh(make_client, "ACCOUNTS").get(RULES).status_code == 403  # only an admin edits rules
