"""Bills of material: integrity, revisions, effective dates, engineering changes, approval."""

from datetime import date, timedelta
from uuid import uuid4

import psycopg
import pytest

P = "/api/v1/product"
pytestmark = pytest.mark.usefixtures("api_database")
TODAY = date.today()


def code() -> str:
    return "B" + uuid4().hex[:9].upper()


def item(client, item_type="RM", uom="KG", **extra):
    response = client.post(f"{P}/items", json={"code": code(), "name": f"{item_type} item",
                                                "item_type": item_type, "uom_code": uom, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def line(component, qty="1.5", scrap="0"):
    return {"component_item_id": component["item_id"], "quantity_per": qty, "scrap_factor": scrap}


def parts(admin, n=2):
    return item(admin, "FG", "NOS"), [item(admin) for _ in range(n)]


def make_bom(client, fg, components):
    response = client.post(f"{P}/boms", json={"item_id": fg["item_id"],
                                               "lines": [line(c) for c in components]})
    assert response.status_code == 201, response.text
    return response.json()


def decide(client, approval_id, decision="APPROVE", note=None):
    return client.post(f"/api/v1/approvals/{approval_id}/decide",
                       json={"decision": decision, "note": note})


def release(make_client, bom, effective=TODAY, reason="Initial release of the part"):
    """Submit a draft as PROD, then approve as QA and the Plant Head. Returns the final BOM."""
    submitted = make_client("PROD").post(f"{P}/boms/{bom['bom_id']}/submit", json={
        "reason": reason, "effective_from": effective.isoformat()})
    assert submitted.status_code == 200, submitted.text
    approval_id = submitted.json()["approval_id"]
    assert decide(make_client("QA"), approval_id).status_code == 200
    assert decide(make_client("PLANT_HEAD"), approval_id).status_code == 200
    return make_client("PROD").get(f"{P}/boms/{bom['bom_id']}").json()


# ------------------------------------------------------------------ integrity
def test_create_edit_and_validate_a_draft(make_client):
    prod = make_client("PROD")
    fg, (rm1, rm2) = parts(prod)
    bom = make_bom(prod, fg, [rm1])
    assert bom["status"] == "DRAFT" and bom["version"] == 1 and bom["item_code"] == fg["code"]
    assert [(ln["line_no"], ln["component_code"], ln["uom_code"]) for ln in bom["lines"]] == [
        (1, rm1["code"], "KG")]
    url = f"{P}/boms/{bom['bom_id']}/lines"
    two = prod.put(url, json={"lines": [line(rm1, "2"), line(rm2, "0.25", "0.05")]})
    assert two.status_code == 200 and [ln["line_no"] for ln in two.json()["lines"]] == [1, 2]
    assert float(two.json()["lines"][1]["scrap_factor"]) == 0.05
    put = lambda *lines: prod.put(url, json={"lines": list(lines)}).status_code  # noqa: E731
    assert put(line(rm1), line(rm1)) == 422  # duplicate component
    assert put(line(fg)) == 422  # an item inside itself
    assert put(line(rm1, "0")) == 422 and put(line(rm1, "-1")) == 422
    assert put(line(rm1, "1", "1")) == 422 and put(line(rm1, "1", "-0.1")) == 422
    assert put({"component_item_id": str(uuid4()), "quantity_per": "1"}) == 422
    capital = item(prod, "CAPITAL", "NOS")
    spare = item(prod, "SPARE", "NOS")
    assert put(line(capital)) == 422 and put(line(spare)) == 422  # not BOM material
    unused = item(prod)
    prod.patch(f"{P}/items/{unused['item_id']}", json={"active": False})
    assert put(line(unused)) == 422  # inactive component
    assert prod.patch(f"{P}/items/{rm2['item_id']}", json={"active": False}).status_code == 409
    assert prod.get(f"{P}/boms/{bom['bom_id']}").json()["lines"][1]["component_code"] == rm2["code"]


def test_create_rules(make_client):
    prod = make_client("PROD")
    fg, (rm,) = parts(prod, 1)
    assert prod.post(f"{P}/boms", json={"item_id": rm["item_id"]}).status_code == 422  # RM parent
    assert prod.post(f"{P}/boms", json={"item_id": str(uuid4())}).status_code == 422
    make_bom(prod, fg, [rm])
    assert prod.post(f"{P}/boms", json={"item_id": fg["item_id"]}).status_code == 409
    inactive = item(prod, "FG", "NOS")
    prod.patch(f"{P}/items/{inactive['item_id']}", json={"active": False})
    assert prod.post(f"{P}/boms", json={"item_id": inactive["item_id"]}).status_code == 422
    assert prod.post(f"{P}/boms", json={"item_id": fg["item_id"], "tenant_id": "x"}).status_code == 422


def test_a_bom_can_never_become_circular(make_client):
    prod = make_client("PROD")
    a, b, c = item(prod, "FG", "NOS"), item(prod, "WIP", "NOS"), item(prod, "WIP", "NOS")
    rm = item(prod)
    make_bom(prod, a, [b])  # A uses B
    bom_b = make_bom(prod, b, [c])  # B uses C
    bom_c = make_bom(prod, c, [rm])
    url = lambda bom: f"{P}/boms/{bom['bom_id']}/lines"  # noqa: E731
    closing = prod.put(url(bom_c), json={"lines": [line(a)]})  # C uses A -> A > B > C > A
    assert closing.status_code == 422 and "circular" in closing.json()["detail"]
    assert prod.put(url(bom_b), json={"lines": [line(a)]}).status_code == 422  # B uses A
    assert prod.put(url(bom_c), json={"lines": [line(rm)]}).status_code == 200  # still fine


# ------------------------------------------------------------------ submit, approve, effective dates
def test_submit_freezes_the_draft_and_records_the_engineering_change(make_client):
    prod = make_client("PROD")
    fg, (rm,) = parts(prod, 1)
    empty = prod.post(f"{P}/boms", json={"item_id": fg["item_id"]}).json()
    submit = lambda bom, **b: prod.post(f"{P}/boms/{bom['bom_id']}/submit", json={  # noqa: E731
        "reason": "Initial release", "effective_from": TODAY.isoformat(), **b})
    assert submit(empty).status_code == 422  # no components
    prod.put(f"{P}/boms/{empty['bom_id']}/lines", json={"lines": [line(rm)]})
    assert submit(empty, reason="no").status_code == 422
    assert submit(empty, effective_from=(TODAY - timedelta(days=1)).isoformat()).status_code == 422
    sent = submit(empty)
    assert sent.status_code == 200 and sent.json()["status"] == "PENDING_APPROVAL"
    assert sent.json()["approval_id"] is not None
    assert prod.put(f"{P}/boms/{empty['bom_id']}/lines", json={"lines": []}).status_code == 409
    assert submit(empty).status_code == 409  # already submitted
    approval = prod.get(f"/api/v1/approvals/{sent.json()['approval_id']}").json()
    assert approval["rule"] == "R-BOM" and approval["title"].startswith("BOM ")
    assert [(s["role"], s["state"]) for s in approval["steps"]] == [
        ("QA", "CURRENT"), ("PLANT_HEAD", "WAITING")]
    changes = prod.get(f"{P}/items/{fg['item_id']}/engineering-changes").json()
    assert changes[0]["reason"] == "Initial release" and changes[0]["from_version"] is None
    assert changes[0]["requested_by"] == "PROD user" and changes[0]["approval_status"] == "PENDING"


def test_approval_makes_a_version_effective_on_its_date(make_client):
    prod = make_client("PROD")
    fg, (rm,) = parts(prod, 1)
    bom = make_bom(prod, fg, [rm])
    start = TODAY + timedelta(days=3)
    released = release(make_client, bom, effective=start)
    assert released["status"] == "APPROVED" and released["valid_from"] == start.isoformat()
    view = lambda on=None: make_client("OPERATOR").get(  # noqa: E731
        f"{P}/items/{fg['item_id']}/effective-bom", params={"on": on} if on else {})
    assert view().status_code == 404  # approved, but not in force yet
    assert view((start - timedelta(days=1)).isoformat()).status_code == 404
    assert view(start.isoformat()).json()["bom_id"] == bom["bom_id"]
    assert view((start + timedelta(days=400)).isoformat()).json()["version"] == 1


def test_a_revision_takes_over_on_its_date_and_the_old_version_is_kept(make_client):
    prod = make_client("PROD")
    fg, (rm1, rm2) = parts(prod)
    v1 = release(make_client, make_bom(prod, fg, [rm1]), effective=TODAY)
    prod = make_client("PROD")
    v2 = prod.post(f"{P}/boms/{v1['bom_id']}/revise")
    assert v2.status_code == 201 and v2.json()["version"] == 2 and v2.json()["status"] == "DRAFT"
    assert [ln["component_code"] for ln in v2.json()["lines"]] == [rm1["code"]]  # copied
    assert prod.post(f"{P}/boms/{v1['bom_id']}/revise").status_code == 409  # one open revision
    prod.put(f"{P}/boms/{v2.json()['bom_id']}/lines", json={"lines": [line(rm1), line(rm2)]})
    switch = TODAY + timedelta(days=5)
    early = prod.post(f"{P}/boms/{v2.json()['bom_id']}/submit", json={
        "reason": "Add the second compound", "effective_from": TODAY.isoformat()})
    assert early.status_code == 422  # must be after version 1 took effect
    done = release(make_client, v2.json(), effective=switch, reason="Add the second compound")
    assert done["version"] == 2 and done["status"] == "APPROVED"
    old = make_client("OPERATOR").get(f"{P}/boms/{v1['bom_id']}").json()
    assert old["status"] == "SUPERSEDED" and old["valid_to"] == (switch - timedelta(days=1)).isoformat()
    assert len(old["lines"]) == 1  # history untouched
    effective = lambda day: make_client("OPERATOR").get(  # noqa: E731
        f"{P}/items/{fg['item_id']}/effective-bom", params={"on": day.isoformat()}).json()["version"]
    assert effective(TODAY) == 1 and effective(switch - timedelta(days=1)) == 1
    assert effective(switch) == 2 and effective(switch + timedelta(days=30)) == 2
    history = make_client("OPERATOR").get(f"{P}/items/{fg['item_id']}/engineering-changes").json()
    assert [(h["version"], h["from_version"], h["approval_status"]) for h in history] == [
        (2, 1, "APPROVED"), (1, None, "APPROVED")]
    assert history[0]["reason"] == "Add the second compound"
    assert history[0]["effective_from"] == switch.isoformat()


def test_a_late_approval_takes_effect_today_not_in_the_past(make_client, api_database):
    prod = make_client("PROD")
    fg, (rm,) = parts(prod, 1)
    bom = make_bom(prod, fg, [rm])
    submitted = prod.post(f"{P}/boms/{bom['bom_id']}/submit", json={
        "reason": "Late approval case", "effective_from": TODAY.isoformat()}).json()
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:  # the date passes
        conn.execute("update erp.bom set valid_from = current_date - 4 where bom_id = %s",
                     (bom["bom_id"],))
    decide(make_client("QA"), submitted["approval_id"])
    decide(make_client("PLANT_HEAD"), submitted["approval_id"])
    final = make_client("PROD").get(f"{P}/boms/{bom['bom_id']}").json()
    assert final["status"] == "APPROVED" and final["valid_from"] == TODAY.isoformat()


def test_rejection_keeps_the_old_version_and_allows_another_try(make_client):
    prod = make_client("PROD")
    fg, (rm1, rm2) = parts(prod)
    v1 = release(make_client, make_bom(prod, fg, [rm1]))
    prod = make_client("PROD")
    v2 = prod.post(f"{P}/boms/{v1['bom_id']}/revise").json()
    prod.put(f"{P}/boms/{v2['bom_id']}/lines", json={"lines": [line(rm2)]})
    sent = prod.post(f"{P}/boms/{v2['bom_id']}/submit", json={
        "reason": "Swap the compound", "effective_from": (TODAY + timedelta(days=2)).isoformat()})
    rejected = decide(make_client("QA"), sent.json()["approval_id"], "REJECT", "Fails hardness spec")
    assert rejected.json()["status"] == "REJECTED"
    prod = make_client("PROD")
    assert prod.get(f"{P}/boms/{v2['bom_id']}").json()["status"] == "REJECTED"
    assert prod.get(f"{P}/items/{fg['item_id']}/effective-bom").json()["bom_id"] == v1["bom_id"]
    assert prod.put(f"{P}/boms/{v2['bom_id']}/lines", json={"lines": []}).status_code == 409
    assert prod.post(f"{P}/boms/{v2['bom_id']}/revise").status_code == 409  # revise an approved one
    v3 = prod.post(f"{P}/boms/{v1['bom_id']}/revise")  # nothing open any more
    assert v3.status_code == 201 and v3.json()["version"] == 3
    history = prod.get(f"{P}/items/{fg['item_id']}/engineering-changes").json()
    assert history[0]["version"] == 2 and history[0]["approval_status"] == "REJECTED"


def test_qa_raising_the_change_skips_its_own_step(make_client):
    qa = make_client("QA")
    fg, (rm,) = parts(qa, 1)
    bom = make_bom(qa, fg, [rm])
    sent = qa.post(f"{P}/boms/{bom['bom_id']}/submit", json={
        "reason": "Raised by quality", "effective_from": TODAY.isoformat()}).json()
    approval = qa.get(f"/api/v1/approvals/{sent['approval_id']}").json()
    assert [(s["role"], s["state"]) for s in approval["steps"]] == [
        ("QA", "SKIPPED"), ("PLANT_HEAD", "CURRENT")]
    decide(make_client("PLANT_HEAD"), sent["approval_id"])
    assert make_client("QA").get(f"{P}/boms/{bom['bom_id']}").json()["status"] == "APPROVED"


# ------------------------------------------------------------------ where used, permissions, isolation
def test_where_used_and_deactivation_guard(make_client):
    prod = make_client("PROD")
    fg, (rm,) = parts(prod, 1)
    bom = make_bom(prod, fg, [rm])
    used = prod.get(f"{P}/items/{rm['item_id']}/where-used").json()
    assert [u["bom_id"] for u in used] == [bom["bom_id"]]
    refused = prod.patch(f"{P}/items/{rm['item_id']}", json={"active": False})
    assert refused.status_code == 409 and "bill of material" in refused.json()["detail"]
    prod.put(f"{P}/boms/{bom['bom_id']}/lines", json={"lines": []})
    assert prod.get(f"{P}/items/{rm['item_id']}/where-used").json() == []
    assert prod.patch(f"{P}/items/{rm['item_id']}", json={"active": False}).status_code == 200


def test_only_bom_managers_write_but_everyone_reads(make_client):
    prod = make_client("PROD")
    fg, (rm,) = parts(prod, 1)
    bom = make_bom(prod, fg, [rm])
    for role in ("STORES", "SALES", "OPERATOR", "ACCOUNTS", "PURCHASE", "HR", "MAINT"):
        client = make_client(role)
        assert client.get(f"{P}/boms/{bom['bom_id']}").status_code == 200, role
        assert client.get(f"{P}/boms?item_id={fg['item_id']}").json()["total"] == 1
        assert client.post(f"{P}/boms", json={"item_id": fg["item_id"]}).status_code == 403
        assert client.put(f"{P}/boms/{bom['bom_id']}/lines", json={"lines": []}).status_code == 403
        assert client.post(f"{P}/boms/{bom['bom_id']}/submit", json={
            "reason": "not allowed", "effective_from": TODAY.isoformat()}).status_code == 403
    for role in ("QA", "PLANT_HEAD", "ADMIN", "MD"):
        other = item(make_client(role), "FG", "NOS")
        assert make_client(role).post(f"{P}/boms", json={"item_id": other["item_id"]}
                                      ).status_code == 201, role


def test_other_tenants_boms_are_invisible(make_client, api_database):
    with psycopg.connect(api_database["owner"], autocommit=True) as conn:
        uom = conn.execute("select uom_id from erp.uom where code = 'NOS'").fetchone()[0]
        theirs, bom = uuid4(), uuid4()
        conn.execute("insert into erp.item(item_id, tenant_id, uom_id, code, name, item_type) "
                     "values (%s, %s, %s, 'B-THEIRS', 'Tenant B', 'FG')",
                     (theirs, api_database["tenant_b"], uom))
        conn.execute("insert into erp.bom(bom_id, tenant_id, item_id, version, valid_from, status) "
                     "values (%s, %s, %s, 1, current_date, 'APPROVED')",
                     (bom, api_database["tenant_b"], theirs))
    admin = make_client("ADMIN")
    assert admin.get(f"{P}/boms/{bom}").status_code == 404
    assert admin.get(f"{P}/items/{theirs}/effective-bom").status_code == 404
    assert admin.put(f"{P}/boms/{bom}/lines", json={"lines": []}).status_code == 404
    assert admin.post(f"{P}/boms/{bom}/revise").status_code == 404
    assert str(bom) not in {b["bom_id"] for b in admin.get(f"{P}/boms?limit=200").json()["items"]}


def test_bom_changes_are_audited(make_client, api_database):
    prod = make_client("PROD")
    fg, (rm,) = parts(prod, 1)
    bom = make_bom(prod, fg, [rm])
    prod.put(f"{P}/boms/{bom['bom_id']}/lines", json={"lines": [line(rm, "3")]})
    prod.post(f"{P}/boms/{bom['bom_id']}/submit", json={
        "reason": "Audit trail check", "effective_from": TODAY.isoformat()})
    with psycopg.connect(api_database["owner"]) as conn:
        actions = [r[0] for r in conn.execute(
            "select action from erp.audit_log where entity_id = %s order by audit_id",
            (bom["bom_id"],))]
    assert actions == ["bom.create", "bom.lines", "bom.submit"]
