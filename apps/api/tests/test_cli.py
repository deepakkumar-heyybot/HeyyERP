"""The operator command that creates the first administrator."""

from uuid import uuid4

import psycopg
import pytest

from heyyerp_api.cli import make_admin

from .conftest import TENANT_A

pytestmark = pytest.mark.usefixtures("api_database")


def signed_up(owner, verified=False):
    uid, email = f"cli-{uuid4().hex[:10]}", f"{uuid4().hex[:8]}@vrub.example"
    owner.execute('insert into auth."user"(id, name, email, "emailVerified") values (%s,%s,%s,%s)',
                  (uid, "First Admin", email, verified))
    return uid, email


def test_first_admin_is_bootstrapped_with_both_options(api_database):
    with psycopg.connect(api_database["owner"], autocommit=True) as owner:
        uid, email = signed_up(owner)
        done = make_admin(owner, TENANT_A, email.upper(), join_org=True, verify_email=True)
        assert "email marked as verified" in done and "given the ADMIN role" in done
        assert owner.execute('select "emailVerified" from auth."user" where id = %s',
                             (uid,)).fetchone() == (True,)
        assert owner.execute('select role from auth.member where "userId" = %s',
                             (uid,)).fetchone() == ("owner",)
        assert owner.execute(
            "select r.code, u.status from erp.app_user u join erp.role r on r.role_id = u.role_id "
            "where u.auth_subject = %s", (uid,)).fetchone() == ("ADMIN", "ACTIVE")


def test_without_the_options_it_refuses_non_members_and_leaves_email_alone(api_database):
    with psycopg.connect(api_database["owner"], autocommit=True) as owner:
        uid, email = signed_up(owner)
        with pytest.raises(SystemExit, match="not a member"):
            make_admin(owner, TENANT_A, email)
        assert owner.execute('select "emailVerified" from auth."user" where id = %s',
                             (uid,)).fetchone() == (False,)
        assert owner.execute("select count(*) from erp.app_user where auth_subject = %s",
                             (uid,)).fetchone() == (0,)


def test_existing_members_need_no_options_and_it_is_repeatable(api_database):
    with psycopg.connect(api_database["owner"], autocommit=True) as owner:
        uid, email = signed_up(owner, verified=True)
        owner.execute('insert into auth.member(id, "organizationId", "userId", role) '
                      "values (%s, 'org-1', %s, 'member')", (f"m-{uid}", uid))
        assert make_admin(owner, TENANT_A, email) == ["given the ADMIN role"]
        assert make_admin(owner, TENANT_A, email) == ["given the ADMIN role"]  # safe to repeat
        assert owner.execute("select count(*) from erp.app_user where auth_subject = %s",
                             (uid,)).fetchone() == (1,)


def test_unknown_email_is_refused(api_database):
    with psycopg.connect(api_database["owner"], autocommit=True) as owner, \
            pytest.raises(SystemExit, match="must sign up first"):
        make_admin(owner, TENANT_A, "nobody@vrub.example", join_org=True)
