"""API tests for the infirmiere staff type and its separate lifecycle.

Infirmiere are scheduled independently from nurses: one schedule per
(year, month, staff_type), no sections, no kitchen, no head. The draft ->
approved -> locked chain, prev_tail and month-locking all operate within one
staff type only, so the two lifecycles never touch.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.solver import calendar as cal
from tests.api_helpers import add_user, auth, login, make_settings
from tests.test_api_schedules import ROSTER as NURSE_ROSTER

FAST = {"max_time_s": 20, "workers": 8, "seed": 7}

# Eight infirmiere: role nurse, no section, staff_type infirmiera.
AIDES = [f"inf.{i}" for i in range(1, 9)]


def _wd_x8(year: int, month: int) -> int:
    return cal.working_days(year, month, cal.default_holidays(year, month)) * 8


def _seed_aides(app) -> dict[str, int]:
    return {u: add_user(app, u, role="nurse", section=None, staff_type="infirmiera")
            for u in AIDES}


def _seed_nurses(app) -> dict[str, int]:
    return {u: add_user(app, u, section=s, kitchen=k, role=r)
            for u, s, k, r in NURSE_ROSTER}


def _aide_payload(ids, year=2026, month=7, target=184, requests=None, days_off=None):
    return {
        "year": year, "month": month, "staff_type": "infirmiera", "target_hours": target,
        "roster": [{"user_id": uid} for uid in ids.values()],
        "days_off": days_off or [], "requests": requests or [], "config": FAST,
    }


# --- fast tests (no solver) -------------------------------------------------

def test_create_infirmiera_account_via_api(client):
    """staff_type infirmiera => section forced null, kitchen forced off, role
    stays nurse; an asistenta still requires a section."""
    add_user(client.app, "anca.berbecar", role="manager_nurse", section="SMALL")
    h = auth(login(client, "anca.berbecar"))

    r = client.post("/api/users", headers=h, json={
        "username": "inf.new", "full_name": "Infirmiera Noua",
        "staff_type": "infirmiera", "default_kitchen_capable": True})
    assert r.status_code == 201, r.text
    user = r.json()["user"]
    assert user["staff_type"] == "infirmiera"
    assert user["section"] is None
    assert user["default_kitchen_capable"] is False  # forced off
    assert user["role"] == "nurse"

    # infirmiera + a section is rejected (422 from the schema validator)
    assert client.post("/api/users", headers=h, json={
        "username": "inf.bad", "full_name": "Rea", "staff_type": "infirmiera",
        "section": "BIG"}).status_code == 422
    # asistenta without a section is rejected
    assert client.post("/api/users", headers=h, json={
        "username": "asis.bad", "full_name": "Rea", "staff_type": "asistenta"}).status_code == 422


def test_update_user_keeps_staff_type_rules(client):
    """PATCH /users must hold the same staff-type rules as create: no section,
    manager role or kitchen flag for an infirmiera; an asistenta cannot have her
    section removed (a NULL section would silently break per-section coverage)."""
    add_user(client.app, "anca.berbecar", role="manager_nurse", section="SMALL")
    aide_id = add_user(client.app, "inf.1", section=None, staff_type="infirmiera")
    nurse_id = add_user(client.app, "lung.paula", section="BIG")
    h = auth(login(client, "anca.berbecar"))

    assert client.patch(f"/api/users/{aide_id}", headers=h,
                        json={"section": "BIG"}).status_code == 422
    assert client.patch(f"/api/users/{aide_id}", headers=h,
                        json={"role": "manager_nurse"}).status_code == 422
    assert client.patch(f"/api/users/{aide_id}", headers=h,
                        json={"default_kitchen_capable": True}).status_code == 422
    assert client.patch(f"/api/users/{nurse_id}", headers=h,
                        json={"section": None}).status_code == 422

    # benign updates still pass for both staff types (explicit null section on
    # an infirmiera is a no-op, which the Accounts edit form sends)
    r = client.patch(f"/api/users/{aide_id}", headers=h,
                     json={"full_name": "Infirmiera Redenumita", "section": None})
    assert r.status_code == 200, r.text
    assert r.json()["full_name"] == "Infirmiera Redenumita"
    assert client.patch(f"/api/users/{nurse_id}", headers=h,
                        json={"section": "SMALL"}).status_code == 200


def test_create_infirmiere_schedule_and_coexist_with_nurse_same_month(client):
    """The (year, month, staff_type) unique constraint lets an infirmiere and a
    nurse schedule share the same month."""
    n_ids = _seed_nurses(client.app)
    a_ids = _seed_aides(client.app)
    h = auth(login(client, "anca.berbecar"))

    nurse = client.post("/api/schedules", headers=h, json={
        "year": 2026, "month": 7, "target_hours": 184,
        "roster": [{"user_id": uid} for uid in n_ids.values()],
        "days_off": [], "requests": [], "config": FAST})
    assert nurse.status_code == 201, nurse.text
    assert nurse.json()["staff_type"] == "asistenta"

    aide = client.post("/api/schedules", headers=h, json=_aide_payload(a_ids))
    assert aide.status_code == 201, aide.text
    body = aide.json()
    assert body["staff_type"] == "infirmiera"
    # infirmiere roster has no section and no kitchen flag
    assert all(row["section"] is None for row in body["setup"]["roster"])
    assert all(row["kitchen_capable"] is False for row in body["setup"]["roster"])

    # a second infirmiere schedule for the same month is refused
    dup = client.post("/api/schedules", headers=h, json=_aide_payload(a_ids))
    assert dup.status_code == 409


def test_roster_staff_type_mismatch_is_422(client):
    """A nurse cannot be rostered onto an infirmiere schedule and vice versa."""
    n_ids = _seed_nurses(client.app)
    a_ids = _seed_aides(client.app)
    h = auth(login(client, "anca.berbecar"))

    payload = _aide_payload(a_ids)
    payload["roster"].append({"user_id": n_ids["lung.paula"]})  # a nurse on an aide schedule
    assert client.post("/api/schedules", headers=h, json=payload).status_code == 422

    nurse_payload = {
        "year": 2026, "month": 8, "target_hours": 184,
        "roster": [{"user_id": uid} for uid in n_ids.values()] + [{"user_id": a_ids["inf.1"]}],
        "days_off": [], "requests": [], "config": FAST}
    assert client.post("/api/schedules", headers=h, json=nurse_payload).status_code == 422


def test_kitchen_request_rejected_on_infirmiere(client):
    a_ids = _seed_aides(client.app)
    _seed_nurses(client.app)
    h = auth(login(client, "anca.berbecar"))
    payload = _aide_payload(a_ids, requests=[
        {"user_id": a_ids["inf.1"], "date": "2026-07-07", "kind": "K", "hard": False}])
    assert client.post("/api/schedules", headers=h, json=payload).status_code == 422


def test_eight_shift_edit_only_on_infirmiere(client):
    """The 8h shift is infirmiere-only; K/H are rejected on an infirmiere grid."""
    a_ids = _seed_aides(client.app)
    n_ids = _seed_nurses(client.app)
    h = auth(login(client, "anca.berbecar"))
    sid = client.post("/api/schedules", headers=h, json=_aide_payload(a_ids)).json()["id"]

    # "8" allowed on infirmiere
    ok = client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
        "edits": [{"user_id": a_ids["inf.1"], "date": "2026-07-07", "shift": "8"}]})
    assert ok.status_code == 200, ok.text
    # K and H rejected on infirmiere
    for bad in ("K", "H"):
        r = client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
            "edits": [{"user_id": a_ids["inf.2"], "date": "2026-07-08", "shift": bad}]})
        assert r.status_code == 422, (bad, r.text)

    # "8" rejected on a nurse schedule
    nsid = client.post("/api/schedules", headers=h, json={
        "year": 2026, "month": 8, "target_hours": 184,
        "roster": [{"user_id": uid} for uid in n_ids.values()],
        "days_off": [], "requests": [], "config": FAST}).json()["id"]
    r = client.patch(f"/api/schedules/{nsid}/assignments", headers=h, json={
        "edits": [{"user_id": n_ids["lung.paula"], "date": "2026-08-04", "shift": "8"}]})
    assert r.status_code == 422


def test_infirmiere_export_smoke(client):
    a_ids = _seed_aides(client.app)
    _seed_nurses(client.app)
    h = auth(login(client, "anca.berbecar"))
    sid = client.post("/api/schedules", headers=h, json=_aide_payload(a_ids)).json()["id"]
    client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
        "edits": [{"user_id": a_ids["inf.1"], "date": "2026-07-07", "shift": "8"}]})
    r = client.get(f"/api/schedules/{sid}/export.xlsx", headers=h)
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"] == \
        'attachment; filename="GRAFIC_Pediatrie_Infirmiere_2026-07.xlsx"'
    assert len(r.content) > 0


# --- separate lifecycle (module-scoped: heavy solves run once) --------------

@pytest.fixture(scope="module")
def lifecycle(tmp_path_factory):
    """Approved nurse-June, approved infirmiere-June and approved infirmiere-July
    in one app, so the locking / prev_tail separation can be asserted."""
    app = create_app(make_settings(tmp_path_factory.mktemp("inf")))
    client = TestClient(app)
    n_ids = _seed_nurses(app)
    a_ids = _seed_aides(app)
    h = auth(login(client, "anca.berbecar"))

    def approve(sid):
        assert client.post(f"/api/schedules/{sid}/generate", headers=h,
                           json={"respect_locks": False}).status_code == 200
        assert client.post(f"/api/schedules/{sid}/approve", headers=h).status_code == 200

    # nurse June (approved, lockable) — different staff type, must stay untouched
    nurse_june = client.post("/api/schedules", headers=h, json={
        "year": 2026, "month": 6, "target_hours": _wd_x8(2026, 6),
        "roster": [{"user_id": uid} for uid in n_ids.values()],
        "days_off": [], "requests": [], "config": FAST}).json()["id"]
    approve(nurse_june)

    # infirmiere June then July (approving July must lock infirmiere June only)
    inf_june = client.post("/api/schedules", headers=h,
                           json=_aide_payload(a_ids, month=6, target=_wd_x8(2026, 6))).json()["id"]
    approve(inf_june)
    inf_july = client.post("/api/schedules", headers=h,
                           json=_aide_payload(a_ids, month=7, target=184)).json()["id"]
    # generated (clean) but left as a draft so the approve test drives the
    # lock-separation step itself
    assert client.post(f"/api/schedules/{inf_july}/generate", headers=h,
                       json={"respect_locks": False}).status_code == 200

    return {"client": client, "h": h, "n_ids": n_ids, "a_ids": a_ids,
            "nurse_june": nurse_june, "inf_june": inf_june, "inf_july": inf_july}


def _status(client, h, sid):
    return client.get(f"/api/schedules/{sid}", headers=h).json()["status"]


def test_infirmiere_july_prev_tail_drawn_from_infirmiere_june_only(lifecycle):
    client, h, a_ids = lifecycle["client"], lifecycle["h"], lifecycle["a_ids"]
    setup = client.get(f"/api/schedules/{lifecycle['inf_july']}", headers=h).json()["setup"]
    tail = setup["prev_tail"]
    days = {d for day_map in tail.values() for d in day_map}
    assert "2026-06-30" in days  # June's last day carried across for the rest rules
    # every prev_tail user id belongs to the infirmiere pool, never a nurse
    assert set(int(k) for k in tail) <= set(a_ids.values())


def test_approving_infirmiere_july_locks_infirmiere_june_not_nurse_june(lifecycle):
    client, h = lifecycle["client"], lifecycle["h"]
    # before: nurse June and infirmiere June are both merely approved
    assert _status(client, h, lifecycle["nurse_june"]) == "approved"
    assert _status(client, h, lifecycle["inf_june"]) == "approved"

    assert client.post(f"/api/schedules/{lifecycle['inf_july']}/approve",
                       headers=h).status_code == 200

    # infirmiere June is now locked; the nurse June (other staff type) is untouched
    assert _status(client, h, lifecycle["inf_june"]) == "locked"
    assert _status(client, h, lifecycle["nurse_june"]) == "approved"


def test_read_filtering_by_staff_type(lifecycle):
    """A logged-in infirmiera sees only infirmiere schedules; a nurse sees only
    nurse ones. Both get the full grid but their own totals."""
    client, a_ids = lifecycle["client"], lifecycle["a_ids"]

    aide_h = auth(login(client, "inf.1"))
    listed = client.get("/api/schedules", headers=aide_h).json()
    staff_types = {s["staff_type"] for s in listed}
    assert staff_types == {"infirmiera"}
    assert lifecycle["inf_june"] in [s["id"] for s in listed]
    # the infirmiera cannot open a nurse schedule
    assert client.get(f"/api/schedules/{lifecycle['nurse_june']}", headers=aide_h).status_code == 404
    # she can open her own approved one, with only her totals
    detail = client.get(f"/api/schedules/{lifecycle['inf_june']}", headers=aide_h).json()
    assert [t["user_id"] for t in detail["totals"]] == [a_ids["inf.1"]]

    nurse_h = auth(login(client, "lung.paula"))
    nlisted = client.get("/api/schedules", headers=nurse_h).json()
    assert {s["staff_type"] for s in nlisted} == {"asistenta"}
    assert client.get(f"/api/schedules/{lifecycle['inf_june']}", headers=nurse_h).status_code == 404
