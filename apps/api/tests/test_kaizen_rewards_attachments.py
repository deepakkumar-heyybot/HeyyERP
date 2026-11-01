"""Kaizen rewards by the company's scheme, and files attached to records."""

import base64
from datetime import date
from decimal import Decimal

import pytest

from .test_compound_batches import Fresh
from .test_costing import Rules
from .test_hr import decide, user_of
from .test_kaizen import KZ, idea, kaizen_world, person

pytestmark = pytest.mark.usefixtures("api_database")
FILES = "/api/v1/attachments"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 40
PDF = b"%PDF-1.4 " + b"y" * 40


def b64(data):
    return base64.b64encode(data).decode()


def approved_kaizen(make_client, api_database, saving="50000"):
    w = kaizen_world(make_client, api_database)
    me = person(make_client, api_database, w)
    k = me.post(KZ, json=idea()).json()
    decide(make_client, k["approval_id"], "PROD")
    Fresh(make_client, "PLANT_HEAD").post(f"{KZ}/{k['kaizen_id']}/evaluate", json={
        "score": "8", "verified_saving": saving})
    decide(make_client, k["approval_id"], "PLANT_HEAD")
    return w, me, k


def test_a_kaizen_reward_follows_the_scheme_the_company_sets(make_client, api_database):
    w, me, k = approved_kaizen(make_client, api_database)
    assert me.get(f"{KZ}/{k['kaizen_id']}").json()["reward_amount"] is None  # no scheme yet
    plant_head = Fresh(make_client, "PLANT_HEAD")
    assert plant_head.post(f"{KZ}/{k['kaizen_id']}/reward-paid", json={
        "paid_on": date.today().isoformat()}).status_code == 409  # nothing to pay
    with Rules(make_client, kaizenRewardPct="2.00"):
        assert Decimal(me.get(f"{KZ}/{k['kaizen_id']}").json()["reward_amount"]) == 1000
        with Rules(make_client, kaizenRewardMax="750.00"):  # the company caps a reward
            assert Decimal(me.get(f"{KZ}/{k['kaizen_id']}").json()["reward_amount"]) == 750
            listed = me.get(f"{KZ}?mine=true").json()["items"][0]
            assert Decimal(listed["reward_amount"]) == 750
            assert me.post(f"{KZ}/{k['kaizen_id']}/reward-paid", json={
                "paid_on": date.today().isoformat()}).status_code == 403  # not an evaluator
            assert plant_head.post(f"{KZ}/{k['kaizen_id']}/reward-paid", json={
                "paid_on": "2100-01-01"}).status_code == 422
            paid = plant_head.post(f"{KZ}/{k['kaizen_id']}/reward-paid", json={
                "paid_on": date.today().isoformat()})
            assert paid.status_code == 200 and paid.json()["reward_paid_on"] == date.today().isoformat()
            assert plant_head.post(f"{KZ}/{k['kaizen_id']}/reward-paid", json={
                "paid_on": date.today().isoformat()}).status_code == 409  # once


def test_files_are_attached_checked_listed_downloaded_and_removed(make_client, api_database):
    w = kaizen_world(make_client, api_database)
    me = person(make_client, api_database, w)
    k = me.post(KZ, json=idea()).json()
    ref = {"entity_type": "kaizen", "entity_id": k["kaizen_id"]}
    up = me.post(FILES, json={**ref, "file_name": "before.png", "content_type": "image/png",
                              "content_base64": b64(PNG)})
    assert up.status_code == 201, up.text
    first = up.json()
    assert first["size_bytes"] == len(PNG) and first["uploaded_by_name"]
    pdf = me.post(FILES, json={**ref, "file_name": "plan.pdf", "content_type": "application/pdf",
                               "content_base64": b64(PDF)})
    assert pdf.status_code == 201
    assert [f["file_name"] for f in me.get(f"{FILES}?entity_type=kaizen&entity_id="
                                           f"{k['kaizen_id']}").json()] == ["before.png", "plan.pdf"]
    got = me.get(f"{FILES}/{first['attachment_id']}/content")
    assert got.status_code == 200 and got.content == PNG
    assert got.headers["content-type"] == "image/png"
    assert got.headers["x-content-type-options"] == "nosniff" and "attachment" in got.headers[
        "content-disposition"]
    fake = me.post(FILES, json={**ref, "file_name": "pic.png", "content_type": "image/png",
                                "content_base64": b64(PDF)})
    assert fake.status_code == 422  # a PDF pretending to be a picture
    assert me.post(FILES, json={**ref, "file_name": "x.png", "content_type": "image/png",
                                "content_base64": "###"}).status_code == 422
    assert me.post(FILES, json={**ref, "file_name": "../evil.png", "content_type": "image/png",
                                "content_base64": b64(PNG)}).status_code == 422
    assert me.post(FILES, json={**ref, "file_name": "run.exe", "content_type":
                                "application/x-msdownload", "content_base64": b64(PNG)}
                   ).status_code == 422
    with Rules(make_client, attachmentMaxKb=50):  # the company's size limit
        big = me.post(FILES, json={**ref, "file_name": "big.png", "content_type": "image/png",
                                   "content_base64": b64(PNG + b"z" * 60_000)})
        assert big.status_code == 413
    stranger, _ = user_of(make_client, api_database, "STORES")
    assert stranger.post(FILES, json={**ref, "file_name": "no.png", "content_type": "image/png",
                                      "content_base64": b64(PNG)}).status_code == 403
    assert stranger.get(f"{FILES}/{first['attachment_id']}/content").status_code == 200  # may read
    assert stranger.post(f"{FILES}/{first['attachment_id']}/remove").status_code == 403
    gone = me.post(f"{FILES}/{first['attachment_id']}/remove")
    assert gone.status_code == 200
    assert me.get(f"{FILES}/{first['attachment_id']}/content").status_code == 404
    assert len(me.get(f"{FILES}?entity_type=kaizen&entity_id={k['kaizen_id']}").json()) == 1
    assert Fresh(make_client, "PLANT_HEAD").post(FILES, json={
        **ref, "file_name": "eval.pdf", "content_type": "application/pdf",
        "content_base64": b64(PDF)}).status_code == 201  # an evaluator may attach too


def test_a_breakdown_takes_photos_from_maintenance_and_the_reporter_and_files_are_limited(
        make_client, api_database):
    from .test_maintenance import MT, make_machine, plant  # noqa: PLC0415

    w = plant(make_client)
    mc = make_machine(make_client, w)
    b = Fresh(make_client, "OPERATOR").post(f"{MT}/breakdowns", json={
        "machine_id": mc["machine_id"], "description": "Hydraulic leak"}).json()
    ref = {"entity_type": "breakdown", "entity_id": b["breakdown_id"]}
    body = {**ref, "file_name": "leak.png", "content_type": "image/png", "content_base64": b64(PNG)}
    assert Fresh(make_client, "OPERATOR").post(FILES, json=body).status_code == 201  # the reporter
    assert Fresh(make_client, "MAINT").post(FILES, json=body).status_code == 201  # maintenance
    assert Fresh(make_client, "PROD").post(FILES, json=body).status_code == 403  # someone else
    assert Fresh(make_client, "SALES").get(
        f"{FILES}?entity_type=breakdown&entity_id={b['breakdown_id']}").status_code == 200
    for n in range(8):
        assert Fresh(make_client, "MAINT").post(FILES, json={
            **body, "file_name": f"p{n}.png"}).status_code == 201
    assert Fresh(make_client, "MAINT").post(FILES, json=body).status_code == 409  # at most 10
    nobody = "00000000-0000-4000-8000-000000000000"
    assert Fresh(make_client, "MAINT").post(FILES, json={
        **body, "entity_id": nobody}).status_code == 404
