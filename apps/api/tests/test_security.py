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
from heyyerp_api.security import Principal, Profile, require_principal

TENANT = UUID("11111111-1111-1111-1111-111111111111")
ORG = "org-1"


def profile(role="STORES", permissions=("grn.post",), units=None):
    return Profile(
        app_user_id=UUID("22222222-2222-2222-2222-222222222222"), display_name="Test",
        role=role, permissions=frozenset(permissions), modules=frozenset({"home"}),
        unit_ids=units,
    )


def make_app(auth, membership=(ORG, ("MEMBER",)), prof="default"):
    """A tiny app with one protected route; auth, membership and profile replace real lookups."""
    app = FastAPI()
    app.include_router(me_router)

    @app.get("/protected")
    async def protected(request: Request, _: Principal = Depends(require_principal)):
        return {"tenant_id": str(request.state.tenant_id)}

    app.dependency_overrides[security.current_auth_session] = lambda: auth
    app.dependency_overrides[security.current_membership] = lambda: membership
    app.dependency_overrides[security.current_profile] = lambda: (
        profile() if prof == "default" else prof)
    return TestClient(app)


def session(verified=True, two_factor=False):
    return {"user": {"id": "u1", "email": "a@b.c", "emailVerified": verified,
                     "twoFactorEnabled": two_factor}}


@pytest.fixture(autouse=True)
def tenant_map(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # keep a developer's local .env out of the tests
    monkeypatch.setenv("ORG_TENANT_MAP", json.dumps({"v-rub-suspensions": str(TENANT)}))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_not_signed_in_is_401():
    assert make_app(None, None, None).get("/protected").status_code == 401


def test_unverified_email_is_403():
    assert make_app(session(verified=False)).get("/protected").status_code == 403


def test_non_member_is_403():
    assert make_app(session(), membership=None).get("/protected").status_code == 403


def test_member_without_an_erp_profile_is_403_with_a_clear_reason():
    response = make_app(session(), prof=None).get("/protected")
    assert response.status_code == 403 and "ERP profile" in response.json()["detail"]


def test_mfa_roles_need_two_factor():
    for role in ("ADMIN", "MD", "ACCOUNTS"):
        assert make_app(session(), prof=profile(role)).get("/protected").status_code == 403
        client = make_app(session(two_factor=True), prof=profile(role))
        assert client.get("/protected").status_code == 200


def test_shop_floor_role_does_not_need_two_factor():  # BRD 9.4
    assert make_app(session(), prof=profile("OPERATOR")).get("/protected").status_code == 200


def test_tenant_comes_from_server_mapping_not_client():
    client = make_app(session())
    other = "22222222-2222-2222-2222-222222222222"
    response = client.get("/protected", headers={"X-Tenant-Id": other}, params={"tenant_id": other})
    assert response.json() == {"tenant_id": str(TENANT)}


def test_unmapped_organization_is_403(monkeypatch):
    monkeypatch.setenv("ORG_TENANT_MAP", "{}")
    get_settings.cache_clear()
    assert make_app(session()).get("/protected").status_code == 403


def test_me_reports_pending_mfa_instead_of_failing():
    body = make_app(session(), prof=profile("MD")).get("/me").json()
    assert body["mfa_required"] is True and body["access_granted"] is False


def test_me_exposes_role_permissions_modules_and_scope():
    unit = UUID("33333333-3333-3333-3333-333333333333")
    body = make_app(session(), prof=profile("STORES", ("grn.post", "inv.issue"), {unit})).get(
        "/me").json()
    assert body["role"] == "STORES" and body["permissions"] == ["grn.post", "inv.issue"]
    assert body["modules"] == ["home"] and body["all_plants"] is False
    assert body["unit_ids"] == [str(unit)]
    assert make_app(session()).get("/me").json()["all_plants"] is True


def test_permissions_come_from_the_profile_only():
    principal = Principal("u", "e", True, ORG, True, TENANT, profile("STORES", ("grn.post",)))
    assert security.has_permission(principal, "grn.post")
    assert not security.has_permission(principal, "admin")


def test_unit_scope_helper():
    unit, other = UUID(int=1), UUID(int=2)
    scoped = Principal("u", "e", True, ORG, True, TENANT, profile(units=frozenset({unit})))
    everywhere = Principal("u", "e", True, ORG, True, TENANT, profile(units=None))
    assert scoped.may_see_unit(unit) and not scoped.may_see_unit(other)
    assert not scoped.may_see_unit(None)
    assert everywhere.may_see_unit(other) and everywhere.may_see_unit(None)


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


def test_a_throttled_auth_service_is_503_not_signed_out():
    with pytest.raises(HTTPException) as err:
        _call(lambda r: httpx.Response(429))
    assert err.value.status_code == 503


def test_cors_allows_only_the_web_origin_with_credentials():
    from heyyerp_api.main import app

    client = TestClient(app)
    ok = client.options("/api/v1/me", headers={"Origin": "http://localhost:5173",
                                              "Access-Control-Request-Method": "GET"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert ok.headers["access-control-allow-credentials"] == "true"
    bad = client.options("/api/v1/me", headers={"Origin": "http://evil.example",
                                               "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers
