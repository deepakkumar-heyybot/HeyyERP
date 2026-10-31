"""Fixtures for API tests against a real, migrated scratch database with two tenants.

Better Auth owns the `auth` schema in production; the scratch database gets minimal tables with
the same column names so the API's real queries run. Sign-in itself is faked (a session and
organization membership), but the ERP profile, roles, permissions and tenant isolation are real.
"""

import json
from collections.abc import Callable, Iterator
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from heyyerp_api import db, security
from heyyerp_api.config import get_settings
from heyyerp_api.main import app
from heyyerp_api.rbac.seed import apply_auth_grants, create_app_user, seed_tenant

from .dbutil import ADMIN_URL, create_migrated_database, drop_database, url_for

API_DB, API_ROLE, API_PASSWORD = "heyyerp_api_test", "heyyerp_api_test_app", "api-test-password"
TENANT_A, TENANT_B = str(uuid4()), str(uuid4())
# A throwaway key for tests only (32 bytes, base64); real keys are never kept in the repository.
TEST_ENCRYPTION_KEY = "dGVzdC1vbmx5LWtleS1ub3QtZm9yLXJlYWwtZGF0YSE="

AUTH_TABLES = """
CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE auth."user" (id text PRIMARY KEY, name text NOT NULL, email text NOT NULL UNIQUE,
  "emailVerified" boolean NOT NULL DEFAULT false);
CREATE TABLE auth.organization (id text PRIMARY KEY, name text NOT NULL, slug text NOT NULL UNIQUE,
  "createdAt" timestamptz NOT NULL DEFAULT now());
CREATE TABLE auth.member (id text PRIMARY KEY,
  "organizationId" text NOT NULL REFERENCES auth.organization (id),
  "userId" text NOT NULL REFERENCES auth."user" (id), role text NOT NULL,
  "createdAt" timestamptz NOT NULL DEFAULT now());
INSERT INTO auth.organization (id, name, slug) VALUES ('org-1', 'V-Rub', 'v-rub-suspensions');
"""


@pytest.fixture(scope="session")
def api_database() -> Iterator[dict[str, str]]:
    """A migrated scratch database: tenants A and B seeded with roles, and a non-owner login."""
    if not ADMIN_URL:
        pytest.skip("TEST_ADMIN_DATABASE_URL is not set")
    create_migrated_database(API_DB)
    with psycopg.connect(url_for(API_DB), autocommit=True) as owner:
        owner.execute(AUTH_TABLES)
        owner.execute(
            f"CREATE ROLE {API_ROLE} LOGIN PASSWORD '{API_PASSWORD}' "
            "NOSUPERUSER NOBYPASSRLS IN ROLE heyyerp_app_rw"
        )
        for tenant_id, code in ((TENANT_A, "tenant-a"), (TENANT_B, "tenant-b")):
            owner.execute(
                "insert into erp.tenant(tenant_id, code, name) values (%s, %s, %s)",
                (tenant_id, code, code),
            )
            seed_tenant(owner, tenant_id)
        apply_auth_grants(owner)
    yield {"owner": url_for(API_DB), "tenant_a": TENANT_A, "tenant_b": TENANT_B}
    drop_database(API_DB, API_ROLE)


@pytest.fixture
def make_client(
    api_database: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[..., TestClient]]:
    """Build a client signed in as a user with the given ERP role (tenant A)."""
    monkeypatch.setenv("ORG_TENANT_MAP", json.dumps({"v-rub-suspensions": TENANT_A}))
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", TEST_ENCRYPTION_KEY)
    get_settings.cache_clear()
    api_url = url_for(API_DB, API_ROLE, API_PASSWORD).replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    clients: list[TestClient] = []

    def close_all() -> None:
        while clients:
            clients.pop().__exit__(None, None, None)  # also disposes the connection pool

    def build(
        role: str,
        two_factor: bool = True,
        subject: str | None = None,
        unit_ids: list[UUID | str] | None = None,
        provision: bool = True,
    ) -> TestClient:
        """provision=False signs in as an account whose ERP profile already exists."""
        # Each TestClient runs its own event loop, so it needs its own connection pool.
        close_all()
        db.configure_database(api_url)
        subject = subject or f"u-{role}"
        if provision:
            with psycopg.connect(api_database["owner"], autocommit=True) as owner:
                user_id = create_app_user(
                    owner, TENANT_A, subject, role, f"{role} user", "t@vrub.example")
                owner.execute("delete from erp.user_unit_scope where user_id = %s", (user_id,))
                for unit_id in unit_ids or []:
                    owner.execute(
                        "insert into erp.user_unit_scope(tenant_id, user_id, unit_id) "
                        "values (%s,%s,%s)", (TENANT_A, user_id, unit_id))
        user = {"id": subject, "email": "t@vrub.example", "emailVerified": True,
                "twoFactorEnabled": two_factor}
        app.dependency_overrides[security.current_auth_session] = lambda: {"user": user}
        app.dependency_overrides[security.current_membership] = lambda: ("org-1", ("MEMBER",))
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield build
    close_all()
    app.dependency_overrides.clear()
    get_settings.cache_clear()
