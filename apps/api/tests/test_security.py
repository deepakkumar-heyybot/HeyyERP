import asyncio
import json
from uuid import UUID

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from heyyerp_api import security
from heyyerp_api.api.me import router as me_router
from heyyerp_api.config import get_settings
from heyyerp_api.security import Principal, require_principal

TENANT = UUID("11111111-1111-1111-1111-111111111111")
ORG = "org-1"


def make_app(auth, membership):
    """A tiny app with one protected route; auth/membership replace the auth service and DB."""
    app = FastAPI()
    app.include_router(me_router)

    @app.get("/protected")
    async def protected(request: Request, _: Principal = Depends(require_principal)):
        return {"tenant_id": str(request.state.tenant_id)}

    app.dependency_overrides[security.current_auth_session] = lambda: auth
    app.dependency_overrides[security.current_membership] = lambda: membership
    return TestClient(app)


def session(verified=True, two_factor=False):
    return {"user": {"id": "u1", "email": "a@b.c", "emailVerified": verified,
                     "twoFactorEnabled": two_factor}}


@pytest.fixture(autouse=True)
def tenant_map(monkeypatch):
    monkeypatch.setenv("ORG_TENANT_MAP", json.dumps({"v-rub-suspensions": str(TENANT)}))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_not_signed_in_is_401():
    assert make_app(None, None).get("/protected").status_code == 401


def test_unverified_email_is_403():
    client = make_app(session(verified=False), (ORG, ("STORES",)))
    assert client.get("/protected").status_code == 403


def test_non_member_is_403():
    assert make_app(session(), None).get("/protected").status_code == 403


def test_mfa_roles_need_two_factor():
    for role in ("ADMIN", "MD", "ACCOUNTS"):
        assert make_app(session(), (ORG, (role,))).get("/protected").status_code == 403
        client = make_app(session(two_factor=True), (ORG, (role,)))
        assert client.get("/protected").status_code == 200


def test_shop_floor_role_does_not_need_two_factor():  # BRD 9.4
    assert make_app(session(), (ORG, ("OPERATOR",))).get("/protected").status_code == 200


def test_tenant_comes_from_server_mapping_not_client():
    client = make_app(session(), (ORG, ("STORES",)))
    other = "22222222-2222-2222-2222-222222222222"
    response = client.get("/protected", headers={"X-Tenant-Id": other}, params={"tenant_id": other})
    assert response.json() == {"tenant_id": str(TENANT)}


def test_unmapped_organization_is_403(monkeypatch):
    monkeypatch.setenv("ORG_TENANT_MAP", "{}")
    get_settings.cache_clear()
    assert make_app(session(), (ORG, ("STORES",))).get("/protected").status_code == 403


def test_me_reports_pending_mfa_instead_of_failing():
    body = make_app(session(), (ORG, ("MD",))).get("/me").json()
    assert body["mfa_required"] is True and body["access_granted"] is False


def test_multiple_roles_any_mfa_role_requires_two_factor():
    assert make_app(session(), (ORG, ("STORES", "ACCOUNTS"))).get("/protected").status_code == 403


def _call(handler):
    return asyncio.run(security.fetch_auth_session("c=1", httpx.MockTransport(handler)))


def test_fetch_session_parses_user_and_forwards_cookie():
    seen = {}

    def handler(request):
        seen["cookie"] = request.headers["cookie"]
        return httpx.Response(200, json=session())

    assert _call(handler)["user"]["id"] == "u1"
    assert seen["cookie"] == "c=1"


def test_fetch_session_signed_out_and_outage():
    assert _call(lambda r: httpx.Response(200, json=None)) is None
    assert asyncio.run(security.fetch_auth_session(None)) is None
    with pytest.raises(HTTPException) as err:
        _call(lambda r: httpx.Response(502))
    assert err.value.status_code == 503
