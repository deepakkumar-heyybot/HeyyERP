"""Request ids, one log line per request without the query string, and the security headers."""

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from heyyerp_api import observability
from heyyerp_api.main import app


def test_every_response_has_a_request_id_and_the_security_headers():
    client = TestClient(app)
    got = client.get("/api/v1/health/live")
    assert got.status_code == 200 and len(got.headers["x-request-id"]) >= 8
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["x-frame-options"] == "DENY"
    assert got.headers["cache-control"] == "no-store"
    kept = client.get("/api/v1/health/live", headers={"X-Request-ID": "abc-12345678"})
    assert kept.headers["x-request-id"] == "abc-12345678"
    odd = client.get("/api/v1/health/live", headers={"X-Request-ID": "bad id\twith space"})
    assert odd.headers["x-request-id"] != "bad id\twith space"


def test_the_log_line_has_no_query_string_and_an_unhandled_error_is_a_plain_500(caplog):
    small = FastAPI()  # its own app, so the real API's routes stay as the snapshot says
    observability.install(small)

    @small.get("/__boom")
    async def boom() -> None:
        raise RuntimeError("secret detail")

    @small.get("/api/v1/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(small, raise_server_exceptions=False)
    with caplog.at_level(logging.INFO, logger="heyyerp.request"):
        client.get("/api/v1/health/live?token=SECRET")
        failed = client.get("/__boom")
    ours = [r for r in caplog.records if r.name == "heyyerp.request"]  # not the test client's own
    text = "\n".join(r.getMessage() for r in ours)
    assert "SECRET" not in text and "/api/v1/health/live" in text
    assert failed.status_code == 500 and "secret detail" not in failed.text
    assert failed.json()["request_id"] == failed.headers["x-request-id"]
