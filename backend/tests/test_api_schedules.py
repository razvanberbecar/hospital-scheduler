"""API tests for the schedule endpoints (milestone M3).

Uses a 14-user roster matching app.solver.cli.DEMO_NURSES (sections, kitchen
flags, anca as the manager_nurse head). Generation runs with a small time
budget so the suite stays fast; July 2026 with no leave is exactly hittable
(target 184h) so every nurse should land on owed==0 and the validator clean.
"""
from __future__ import annotations

import pytest

from tests.api_helpers import add_user, auth, login, make_settings

# (username, section, kitchen_capable, role) mirroring DEMO_NURSES row order.
ROSTER = [
    ("drulea.florica", "BIG", True, "nurse"),
    ("pop.delia", "BIG", True, "nurse"),
    ("prodan.gabriela", "BIG", False, "nurse"),
    ("codre.aurelia", "BIG", False, "nurse"),
    ("matei.sorina", "BIG", False, "nurse"),
    ("pop.marinela", "BIG", False, "nurse"),
    ("campian.aurelia", "BIG", False, "nurse"),
    ("rusu.ana", "SMALL", True, "nurse"),
    ("blaga.mirela", "SMALL", True, "nurse"),
    ("lung.paula", "SMALL", False, "nurse"),
    ("anca.berbecar", "SMALL", False, "manager_nurse"),
    ("boda.erika", "SMALL", False, "nurse"),
    ("ardelean.camelia", "SMALL", False, "nurse"),
    ("opris.elena", "SMALL", False, "nurse"),
]
# workers=8 + 15s so the exactly-hittable no-leave July reliably reaches
# owed==0; the spec's 10s/4-worker example flakes to an in-band FEASIBLE
# solution under load, and (measured) more workers search this model faster.
FAST_CONFIG = {"max_time_s": 15, "workers": 8, "seed": 7}
JULY_TARGET = 184


def _seed_roster(app) -> dict[str, int]:
    """Create the 14 accounts and return {username: id}. anca is the head."""
    ids = {}
    for username, section, kitchen, role in ROSTER:
        ids[username] = add_user(app, username, section=section, kitchen=kitchen, role=role)
    return ids


def _create_payload(ids: dict[str, int], year=2026, month=7, target=JULY_TARGET,
                    days_off=None, requests=None) -> dict:
    return {
        "year": year, "month": month, "target_hours": target,
        "roster": [{"user_id": uid} for uid in ids.values()],
        "days_off": days_off or [],
        "requests": requests or [],
        "config": FAST_CONFIG,
    }


def _manager(client) -> tuple[dict, dict[str, int]]:
    """Seed the 14-user roster and log the head nurse in; returns (headers, ids)."""
    ids = _seed_roster(client.app)
    return auth(login(client, "anca.berbecar")), ids


# --- fast tests (no solver) -------------------------------------------------

def test_create_list_and_detail(client):
    h, ids = _manager(client)
    r = client.post("/api/schedules", headers=h, json=_create_payload(ids))
    assert r.status_code == 201, r.text
    detail = r.json()
    assert detail["status"] == "draft"
    assert len(detail["setup"]["roster"]) == 14
    assert detail["approval_count"] == 0

    dup = client.post("/api/schedules", headers=h, json=_create_payload(ids))
    assert dup.status_code == 409

    listing = client.get("/api/schedules", headers=h, params={"year": 2026}).json()
    assert len(listing) == 1 and listing[0]["month"] == 7

    got = client.get(f"/api/schedules/{detail['id']}", headers=h)
    assert got.status_code == 200
    assert got.json()["id"] == detail["id"]


def test_setup_roster_follows_manager_position(client):
    """The detail's setup.roster echoes the manager's global order (users.position),
    not BIG-then-SMALL-alpha. No solve needed."""
    h, ids = _manager(client)
    all_ids = [u["id"] for u in client.get("/api/users", headers=h).json()]
    # A deliberately non-alphabetical, section-interleaved custom order.
    custom = list(reversed(all_ids))
    assert client.put("/api/users/order", headers=h,
                      json={"ordered_ids": custom}).status_code == 200

    r = client.post("/api/schedules", headers=h, json=_create_payload(ids))
    assert r.status_code == 201, r.text
    roster_ids = [row["user_id"] for row in r.json()["setup"]["roster"]]
    # Roster is exactly the rostered subset of `custom`, in that order.
    assert roster_ids == [uid for uid in custom if uid in set(ids.values())]


def test_nurse_permissions(client):
    h, ids = _manager(client)
    sid = client.post("/api/schedules", headers=h, json=_create_payload(ids)).json()["id"]

    nurse_h = auth(login(client, "lung.paula"))
    # nurse cannot create / generate / approve
    assert client.post("/api/schedules", headers=nurse_h,
                       json=_create_payload(ids)).status_code == 403
    assert client.post(f"/api/schedules/{sid}/generate", headers=nurse_h,
                       json={"respect_locks": False}).status_code == 403
    assert client.post(f"/api/schedules/{sid}/approve", headers=nurse_h).status_code == 403
    # draft is invisible to the nurse -> 404
    assert client.get(f"/api/schedules/{sid}", headers=nurse_h).status_code == 404
    assert client.get("/api/schedules", headers=nurse_h, params={"year": 2026}).json() == []


def test_roster_validation_rejects_bad_users(client):
    h, ids = _manager(client)
    # two managers on the roster -> 422
    payload = _create_payload(ids)
    second_mgr = add_user(client.app, "second.head", section="BIG", role="manager_nurse")
    payload["roster"].append({"user_id": second_mgr})
    # add_user allows a second manager row directly; the endpoint must reject it.
    assert client.post("/api/schedules", headers=h, json=payload).status_code == 422

    # unknown user id -> 422
    payload2 = _create_payload(ids)
    payload2["roster"].append({"user_id": 9999})
    assert client.post("/api/schedules", headers=h, json=payload2).status_code == 422


# --- generation + downstream (module-scoped, one generate reused) -----------

@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    """A freshly generated, validator-clean July 2026 draft, reused across the
    heavier asserts. Module-scoped so the ~10s solve runs once."""
    from fastapi.testclient import TestClient

    from app.main import create_app
    tmp = tmp_path_factory.mktemp("gen")
    app = create_app(make_settings(tmp))
    client = TestClient(app)
    ids = _seed_roster(app)
    h = auth(login(client, "anca.berbecar"))
    sid = client.post("/api/schedules", headers=h, json=_create_payload(ids)).json()["id"]
    r = client.post(f"/api/schedules/{sid}/generate", headers=h, json={"respect_locks": False})
    assert r.status_code == 200, r.text
    return {"client": client, "h": h, "sid": sid, "ids": ids, "detail": r.json()}


def test_generate_is_feasible_and_on_target(generated):
    detail = generated["detail"]
    assert detail["solver_status"] in ("OPTIMAL", "FEASIBLE")
    assert detail["violations"] == []
    # No leave -> every nurse lands exactly on target, owed == 0.
    for t in detail["totals"]:
        assert t["owed"] == 0, t
        assert t["total"] == JULY_TARGET, t


def test_validate_endpoint_ok_after_generate(generated):
    client, h, sid = generated["client"], generated["h"], generated["sid"]
    r = client.post(f"/api/schedules/{sid}/validate", headers=h)
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_edit_creates_and_heals_violation(generated):
    client, h, sid = generated["client"], generated["h"], generated["sid"]
    # Clearing a kitchen cell deterministically breaks the exactly-one-kitchen
    # rule (unlike a day cell, which may sit on an over-covered day).
    cell = _first_kitchen_cell(client, h, sid)

    r = client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
        "edits": [{"user_id": cell["user_id"], "date": cell["date"], "shift": None}]})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert any(v["code"] == "coverage_kitchen" for v in r.json()["violations"])

    # put it back -> clean again
    r = client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
        "edits": [{"user_id": cell["user_id"], "date": cell["date"], "shift": cell["shift"],
                   "duration_hours": cell["duration_hours"]}]})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_head_nurse_h_cell_can_be_edited_to_a_shift(generated):
    """Change 2: the head's H cells are first-class. Switching one H to a day
    shift succeeds and re-validates cleanly (she now counts as a day nurse)."""
    client, h, sid = generated["client"], generated["h"], generated["sid"]
    ids = generated["ids"]
    head_id = ids["anca.berbecar"]
    grid = client.get(f"/api/schedules/{sid}", headers=h).json()["assignments"]
    head_cell = next(a for a in grid if a["shift"] == "H")
    r = client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
        "edits": [{"user_id": head_id, "date": head_cell["date"], "shift": "Z"}]})
    assert r.status_code == 200, r.text
    # no head_pattern violation (she is the head, a Z is fine for her now)
    assert not any(v["code"] == "head_pattern" for v in r.json()["violations"])
    got = client.get(f"/api/schedules/{sid}", headers=h).json()["assignments"]
    now = next(a for a in got if a["user_id"] == head_id and a["date"] == head_cell["date"])
    assert now["shift"] == "Z"
    # restore the H so the shared `generated` fixture stays clean for other tests
    client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
        "edits": [{"user_id": head_id, "date": head_cell["date"], "shift": "H"}]})


def test_h_shift_rejected_on_a_regular_nurse(generated):
    client, h, sid = generated["client"], generated["h"], generated["sid"]
    ids = generated["ids"]
    r = client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
        "edits": [{"user_id": ids["lung.paula"], "date": "2026-07-07", "shift": "H"}]})
    assert r.status_code == 422


def test_clearing_head_cell_is_a_warning_not_a_violation(generated):
    client, h, sid = generated["client"], generated["h"], generated["sid"]
    ids = generated["ids"]
    head_id = ids["anca.berbecar"]
    grid = client.get(f"/api/schedules/{sid}", headers=h).json()["assignments"]
    head_cell = next(a for a in grid if a["shift"] == "H")
    r = client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
        "edits": [{"user_id": head_id, "date": head_cell["date"], "shift": None}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert not any(v["code"] == "head_missing" for v in body["violations"])
    assert any(w["code"] == "head_missing" for w in body["warnings"])
    # restore
    client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
        "edits": [{"user_id": head_id, "date": head_cell["date"], "shift": "H"}]})


def test_locked_head_cell_survives_regenerate(client):
    """Change 2: a locked head Z where auto-H would have gone must survive a
    respect_locks regenerate, and no auto-H is emitted for her that day."""
    h, ids = _manager(client)
    sid = client.post("/api/schedules", headers=h, json=_create_payload(ids)).json()["id"]
    assert client.post(f"/api/schedules/{sid}/generate", headers=h,
                       json={"respect_locks": False}).status_code == 200
    head_id = ids["anca.berbecar"]
    grid = client.get(f"/api/schedules/{sid}", headers=h).json()["assignments"]
    head_cell = next(a for a in grid if a["user_id"] == head_id and a["shift"] == "H")
    day = head_cell["date"]
    # switch the H to a locked Z (she becomes a day nurse for her section)
    r = client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
        "edits": [{"user_id": head_id, "date": day, "shift": "Z", "locked": True}]})
    assert r.status_code == 200, r.text
    # regenerate respecting locks
    r = client.post(f"/api/schedules/{sid}/generate", headers=h, json={"respect_locks": True})
    assert r.status_code == 200, r.text
    after = r.json()["assignments"]
    head_that_day = [a for a in after if a["user_id"] == head_id and a["date"] == day]
    assert len(head_that_day) == 1
    assert head_that_day[0]["shift"] == "Z"
    assert head_that_day[0]["locked"] is True
    # no auto-H for her on the locked day
    assert not any(a["shift"] == "H" and a["user_id"] == head_id and a["date"] == day
                   for a in after)


def test_lock_survives_regenerate(generated):
    client, h, sid = generated["client"], generated["h"], generated["sid"]
    ids = generated["ids"]
    target_user = ids["codre.aurelia"]
    detail = client.get(f"/api/schedules/{sid}", headers=h).json()
    cell = next(a for a in detail["assignments"]
                if a["user_id"] == target_user and a["shift"] in ("Z", "N"))

    # lock the cell in place
    r = client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
        "edits": [{"user_id": target_user, "date": cell["date"], "shift": cell["shift"],
                   "duration_hours": cell["duration_hours"], "locked": True}]})
    assert r.status_code == 200

    # regenerate respecting locks; the locked cell must remain identical
    r = client.post(f"/api/schedules/{sid}/generate", headers=h, json={"respect_locks": True})
    assert r.status_code == 200, r.text
    after = r.json()
    survivor = next((a for a in after["assignments"]
                     if a["user_id"] == target_user and a["date"] == cell["date"]), None)
    assert survivor is not None
    assert survivor["shift"] == cell["shift"]
    assert survivor["locked"] is True


# --- approval + chain (own app so approvals don't disturb `generated`) ------

def test_approve_flow_and_chain(client):
    h, ids = _manager(client)
    sid = client.post("/api/schedules", headers=h, json=_create_payload(ids)).json()["id"]
    # a draft cannot be reopened (it is already editable)
    assert client.post(f"/api/schedules/{sid}/reopen", headers=h).status_code == 409
    assert client.post(f"/api/schedules/{sid}/generate", headers=h,
                       json={"respect_locks": False}).status_code == 200

    # approve fails while a hole exists (clearing a kitchen cell always violates)
    cell = _first_kitchen_cell(client, h, sid)
    client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
        "edits": [{"user_id": cell["user_id"], "date": cell["date"], "shift": None}]})
    assert client.post(f"/api/schedules/{sid}/approve", headers=h).status_code == 409

    # heal it, then approve -> version 1 (no hour balances carry any more)
    client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
        "edits": [{"user_id": cell["user_id"], "date": cell["date"], "shift": cell["shift"],
                   "duration_hours": cell["duration_hours"]}]})
    r = client.post(f"/api/schedules/{sid}/approve", headers=h)
    assert r.status_code == 200, r.text
    approval = r.json()
    assert approval["version"] == 1
    assert "closing_balances" not in approval  # the ore-restante carry-over is gone

    # status is now approved; re-approve allowed (next month absent) -> version 2
    assert client.get(f"/api/schedules/{sid}", headers=h).json()["status"] == "approved"
    r2 = client.post(f"/api/schedules/{sid}/approve", headers=h)
    assert r2.status_code == 200
    assert r2.json()["version"] == 2

    # --- August draft auto-fills prev_tail from July, then locks July --------
    aug = _create_payload(ids, month=8, target=cal_working_days_x8(2026, 8))
    r = client.post("/api/schedules", headers=h, json=aug)
    assert r.status_code == 201, r.text
    aug_detail = r.json()
    aug_sid = aug_detail["id"]
    # no balances concept anywhere in the setup payload
    assert "balances" not in aug_detail["setup"]
    # prev_tail still auto-filled: July 30 and 31 shift codes for at least one nurse
    tail_days = {d for day_map in aug_detail["setup"]["prev_tail"].values() for d in day_map}
    assert "2026-07-31" in tail_days
    assert "2026-07-30" in tail_days

    # --- reopen July (approved, August still a draft) for a correction -------
    assert client.post(f"/api/schedules/{sid}/reopen", headers=h).status_code == 200
    july = client.get(f"/api/schedules/{sid}", headers=h).json()
    assert july["status"] == "draft"
    # blank out the August draft's prev_tail via setup to prove that
    # re-approving July rebuilds it from July's (possibly corrected) grid
    aug_setup = client.get(f"/api/schedules/{aug_sid}", headers=h).json()["setup"]
    put = {
        "target_hours": aug_setup["target_hours"],
        "roster": [{"user_id": r["user_id"], "kitchen_capable": r["kitchen_capable"]}
                   for r in aug_setup["roster"]],
        "days_off": aug_setup["days_off"], "requests": [],
        "prev_tail": {}, "config": aug_setup["config"],
    }
    assert client.put(f"/api/schedules/{aug_sid}/setup", headers=h, json=put).status_code == 200
    assert client.get(f"/api/schedules/{aug_sid}",
                      headers=h).json()["setup"]["prev_tail"] == {}
    # re-approve July -> version 3; the August draft's prev_tail is refreshed
    r = client.post(f"/api/schedules/{sid}/approve", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 3
    aug_tail = client.get(f"/api/schedules/{aug_sid}", headers=h).json()["setup"]["prev_tail"]
    refreshed_days = {d for day_map in aug_tail.values() for d in day_map}
    assert "2026-07-31" in refreshed_days and "2026-07-30" in refreshed_days

    assert client.post(f"/api/schedules/{aug_sid}/generate", headers=h,
                       json={"respect_locks": False}).status_code == 200
    assert client.post(f"/api/schedules/{aug_sid}/approve", headers=h).status_code == 200

    # July is now locked -> further edits 409
    assert client.get(f"/api/schedules/{sid}", headers=h).json()["status"] == "locked"
    r = client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
        "edits": [{"user_id": cell["user_id"], "date": cell["date"], "shift": None}]})
    assert r.status_code == 409
    assert client.put(f"/api/schedules/{sid}/setup", headers=h,
                      json=_setup_payload(ids)).status_code == 409

    # approval history (plan section 8): all July versions, each snapshot readable
    hist = client.get(f"/api/schedules/{sid}/approvals", headers=h)
    assert hist.status_code == 200
    assert [a["version"] for a in hist.json()] == [1, 2, 3]
    snap = client.get(f"/api/schedules/{sid}/approvals/2", headers=h)
    assert snap.status_code == 200
    snap_body = snap.json()["snapshot"]
    assert "closing_balances" not in snap_body  # no balances frozen any more
    assert set(int(k) for k in snap_body["totals"]) == set(ids.values())
    assert client.get(f"/api/schedules/{sid}/approvals/9", headers=h).status_code == 404
    nurse_h = auth(login(client, "boda.erika"))
    assert client.get(f"/api/schedules/{sid}/approvals", headers=nurse_h).status_code == 403

    # --- reopen: approved -> draft, locked -> never --------------------------
    assert client.post(f"/api/schedules/{sid}/reopen", headers=h).status_code == 409  # locked
    assert client.post(f"/api/schedules/{aug_sid}/reopen", headers=nurse_h).status_code == 403
    assert client.post(f"/api/schedules/{aug_sid}/reopen", headers=h).status_code == 200
    assert client.get(f"/api/schedules/{aug_sid}", headers=h).json()["status"] == "draft"
    # a reopened draft is hidden from nurses again
    assert client.get(f"/api/schedules/{aug_sid}", headers=nurse_h).status_code == 404
    # re-approve -> August version 2; July stays locked
    assert client.post(f"/api/schedules/{aug_sid}/approve", headers=h).json()["version"] == 2
    assert client.get(f"/api/schedules/{sid}", headers=h).json()["status"] == "locked"


def test_force_approve_overrides_violations(client):
    """A month with open violations is refused normally but approves with
    ?force=true, so an imperfect month can still be approved (it records the
    prev_tail the next month's generator needs). The overridden violations are
    kept on the snapshot for traceability; a nurse still cannot force."""
    h, ids = _manager(client)
    sid = client.post("/api/schedules", headers=h, json=_create_payload(ids)).json()["id"]
    assert client.post(f"/api/schedules/{sid}/generate", headers=h,
                       json={"respect_locks": False}).status_code == 200

    # clearing a kitchen cell deterministically breaks coverage (a violation)
    cell = _first_kitchen_cell(client, h, sid)
    client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
        "edits": [{"user_id": cell["user_id"], "date": cell["date"], "shift": None}]})

    # a nurse may never force; the normal approve is refused for the manager
    nurse_h = auth(login(client, "lung.paula"))
    assert client.post(f"/api/schedules/{sid}/approve?force=true",
                       headers=nurse_h).status_code == 403
    assert client.post(f"/api/schedules/{sid}/approve", headers=h).status_code == 409

    # force=true approves despite the open violation
    r = client.post(f"/api/schedules/{sid}/approve?force=true", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1
    assert client.get(f"/api/schedules/{sid}", headers=h).json()["status"] == "approved"

    # the overridden violation is recorded on the snapshot
    snap = client.get(f"/api/schedules/{sid}/approvals/1", headers=h).json()["snapshot"]
    assert any(v["code"] == "coverage_kitchen" for v in snap["overridden_violations"])


def test_force_approve_on_a_clean_month_records_no_overrides(client):
    """force=true on a month with no violations still approves and records an
    empty override list - forcing never invents a problem."""
    h, ids = _manager(client)
    sid = client.post("/api/schedules", headers=h, json=_create_payload(ids)).json()["id"]
    client.post(f"/api/schedules/{sid}/generate", headers=h, json={"respect_locks": False})
    r = client.post(f"/api/schedules/{sid}/approve?force=true", headers=h)
    assert r.status_code == 200, r.text
    snap = client.get(f"/api/schedules/{sid}/approvals/1", headers=h).json()["snapshot"]
    assert snap["overridden_violations"] == []


def test_nurse_sees_approved_with_own_totals_only(client):
    h, ids = _manager(client)
    sid = client.post("/api/schedules", headers=h, json=_create_payload(ids)).json()["id"]
    client.post(f"/api/schedules/{sid}/generate", headers=h, json={"respect_locks": False})
    assert client.post(f"/api/schedules/{sid}/approve", headers=h).status_code == 200

    nurse_h = auth(login(client, "lung.paula"))
    detail = client.get(f"/api/schedules/{sid}", headers=nurse_h)
    assert detail.status_code == 200
    body = detail.json()
    # full grid visible, but totals only her own user id
    assert len(body["assignments"]) > 14
    assert [t["user_id"] for t in body["totals"]] == [ids["lung.paula"]]
    # she now sees it in the listing too
    listed = client.get("/api/schedules", headers=nurse_h, params={"year": 2026}).json()
    assert [s["id"] for s in listed] == [sid]


# --- delete draft schedules (Change 4) --------------------------------------

def test_delete_draft_removes_schedule_and_children(client):
    h, ids = _manager(client)
    sid = client.post("/api/schedules", headers=h, json=_create_payload(
        ids, days_off=[{"user_id": ids["lung.paula"], "date": "2026-07-06"}],
        requests=[{"user_id": ids["boda.erika"], "date": "2026-07-07",
                   "kind": "N", "hard": False}])).json()["id"]
    # a couple of hand-placed cells so assignment children exist
    client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
        "edits": [{"user_id": ids["rusu.ana"], "date": "2026-07-07", "shift": "K"}]})

    r = client.delete(f"/api/schedules/{sid}", headers=h)
    assert r.status_code == 204, r.text
    # gone
    assert client.get(f"/api/schedules/{sid}", headers=h).status_code == 404
    # children gone: re-create the same month succeeds (unique-month rule frees up)
    r2 = client.post("/api/schedules", headers=h, json=_create_payload(ids))
    assert r2.status_code == 201


def test_delete_approved_or_locked_is_409(client):
    h, ids = _manager(client)
    sid = client.post("/api/schedules", headers=h, json=_create_payload(ids)).json()["id"]
    client.post(f"/api/schedules/{sid}/generate", headers=h, json={"respect_locks": False})
    assert client.post(f"/api/schedules/{sid}/approve", headers=h).status_code == 200
    assert client.delete(f"/api/schedules/{sid}", headers=h).status_code == 409


def test_delete_schedule_requires_manager(client):
    h, ids = _manager(client)
    sid = client.post("/api/schedules", headers=h, json=_create_payload(ids)).json()["id"]
    nurse_h = auth(login(client, "lung.paula"))
    assert client.delete(f"/api/schedules/{sid}", headers=nurse_h).status_code == 403


def test_reopened_then_deleted_removes_approvals(client):
    h, ids = _manager(client)
    sid = client.post("/api/schedules", headers=h, json=_create_payload(ids)).json()["id"]
    client.post(f"/api/schedules/{sid}/generate", headers=h, json={"respect_locks": False})
    assert client.post(f"/api/schedules/{sid}/approve", headers=h).status_code == 200
    # reopen -> draft (approval history still on disk), then delete
    assert client.post(f"/api/schedules/{sid}/reopen", headers=h).status_code == 200
    assert client.delete(f"/api/schedules/{sid}", headers=h).status_code == 204
    # re-create + approve the same month -> its first approval version is 1 again,
    # proving the old approvals were purged (versions did not continue at 2).
    sid2 = client.post("/api/schedules", headers=h, json=_create_payload(ids)).json()["id"]
    client.post(f"/api/schedules/{sid2}/generate", headers=h, json={"respect_locks": False})
    r = client.post(f"/api/schedules/{sid2}/approve", headers=h)
    assert r.status_code == 200
    assert r.json()["version"] == 1


# --- small local helpers ----------------------------------------------------

def _first_kitchen_cell(client, h, sid) -> dict:
    """The earliest kitchen assignment in the grid. Clearing it deterministically
    breaks the exactly-one-kitchen-per-day rule; healing it restores 8h."""
    grid = client.get(f"/api/schedules/{sid}", headers=h).json()["assignments"]
    kitchen = sorted((a for a in grid if a["shift"] == "K"), key=lambda a: a["date"])
    assert kitchen, "generated grid has no kitchen shifts"
    return kitchen[0]


def _setup_payload(ids: dict[str, int]) -> dict:
    return {
        "target_hours": JULY_TARGET,
        "roster": [{"user_id": uid} for uid in ids.values()],
        "days_off": [], "requests": [], "prev_tail": {}, "config": FAST_CONFIG,
    }


def cal_working_days_x8(year: int, month: int) -> int:
    from app.solver import calendar as cal
    return cal.working_days(year, month, cal.default_holidays(year, month)) * 8
