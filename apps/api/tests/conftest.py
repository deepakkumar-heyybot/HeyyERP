"""Fixtures for API tests against a real, migrated scratch database with two tenants."""

import json
from collections.abc import Callable, Iterator
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from heyyerp_api import db, security
from heyyerp_api.config import get_settings
from heyyerp_api.main import app

from .dbutil import ADMIN_URL, create_migrated_database, drop_database, url_for

API_DB, API_ROLE, API_PASSWORD = "heyyerp_api_test", "heyyerp_api_test_app", "api-test-password"
TENANT_A, TENANT_B = str(uuid4()), str(uuid4())


@pytest.fixture(scope="session")
def api_database() -> Iterator[dict[str, str]]:
    """A migrated scratch database with tenants A and B and a non-owner API login."""
    if not ADMIN_URL:
        pytest.skip("TEST_ADMIN_DATABASE_URL is not set")
    create_migrated_database(API_DB)
    with psycopg.connect(url_for(API_DB), autocommit=True) as owner:
        owner.execute(
            f"CREATE ROLE {API_ROLE} LOGIN PASSWORD '{API_PASSWORD}' "
            "NOSUPERUSER NOBYPASSRLS IN ROLE heyyerp_app_rw"
        )
        for tenant_id, code in ((TENANT_A, "tenant-a"), (TENANT_B, "tenant-b")):
            owner.execute(
                "insert into erp.tenant(tenant_id, code, name) values (%s, %s, %s)",
                (tenant_id, code, code),
            )
    yield {"owner": url_for(API_DB), "tenant_a": TENANT_A, "tenant_b": TENANT_B}
    drop_database(API_DB, API_ROLE)


@pytest.fixture
def make_client(
    api_database: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[..., TestClient]]:
    """Build a client signed in with the given ERP roles; auth and DB are otherwise real."""
    monkeypatch.setenv("ORG_TENANT_MAP", json.dumps({"v-rub-suspensions": TENANT_A}))
    get_settings.cache_clear()
    api_url = url_for(API_DB, API_ROLE, API_PASSWORD).replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    clients: list[TestClient] = []

    def close_all() -> None:
        while clients:
            clients.pop().__exit__(None, None, None)  # also disposes the connection pool

    def build(*roles: str, two_factor: bool = True) -> TestClient:
        # Each TestClient runs its own event loop, so it needs its own connection pool.
        close_all()
        db.configure_database(api_url)
        user = {"id": "u-" + "-".join(roles), "email": "t@vrub.example",
                "emailVerified": True, "twoFactorEnabled": two_factor}
        app.dependency_overrides[security.current_auth_session] = lambda: {"user": user}
        app.dependency_overrides[security.current_membership] = lambda: ("org-1", tuple(roles))
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield build
    close_all()
    app.dependency_overrides.clear()
    get_settings.cache_clear()
