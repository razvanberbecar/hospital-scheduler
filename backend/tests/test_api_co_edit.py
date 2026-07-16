"""API tests for editing Co (leave) days from the schedule grid.

Co is still entered up front in Setup, but it is no longer frozen once the month
exists: the grid edits it through the same PATCH endpoint as any shift. Co is
leave rather than a shift, so it upserts a days_off row - which is what the
solver's leave, the 8h/day credit and the bridged-weekend rule all read.
"""
from __future__ import annotations

import pytest

from tests.api_helpers import auth, login
from tests.test_api_schedules import _create_payload, _seed_roster

MONDAY = "2026-07-06"      # July 2026: 3rd Fri, 4th-5th weekend, 6th Mon
FRIDAY = "2026-07-03"
SATURDAY = "2026-07-04"
SUNDAY = "2026-07-05"


@pytest.fixture()
def draft(client):
    """A July 2026 draft with the full roster and no leave. Not generated: Co
    editing needs no grid, and skipping the solve keeps these tests fast."""
    ids = _seed_roster(client.app)
    h = auth(login(client, "anca.berbecar"))
    sid = client.post("/api/schedules", headers=h, json=_create_payload(ids)).json()["id"]
    return {"client": client, "h": h, "sid": sid, "ids": ids}


def _setup(d) -> dict:
    return d["client"].get(f"/api/schedules/{d['sid']}", headers=d["h"]).json()["setup"]


def _edit(d, user_id, date, shift, **extra) -> "object":
    return d["client"].patch(f"/api/schedules/{d['sid']}/assignments", headers=d["h"],
                             json={"edits": [{"user_id": user_id, "date": date,
                                              "shift": shift, **extra}]})


def _days_off(d) -> set[tuple[int, str]]:
    return {(x["user_id"], x["date"]) for x in _setup(d)["days_off"]}


# --- adding and removing ---------------------------------------------------

def test_co_can_be_added_from_the_grid(draft):
    uid = draft["ids"]["lung.paula"]
    assert (uid, MONDAY) not in _days_off(draft)

    r = _edit(draft, uid, MONDAY, "Co")
    assert r.status_code == 200, r.text
    assert (uid, MONDAY) in _days_off(draft)


def test_co_can_be_removed_from_the_grid(draft):
    """The bug this fixes: a Co entered before generation used to be permanent."""
    uid = draft["ids"]["lung.paula"]
    _edit(draft, uid, MONDAY, "Co")
    assert (uid, MONDAY) in _days_off(draft)

    r = _edit(draft, uid, MONDAY, None)
    assert r.status_code == 200, r.text
    assert (uid, MONDAY) not in _days_off(draft)


def test_co_moves_the_leave_credit(draft):
    """Co is real leave, not a label: adding one is worth 8h toward the target."""
    uid = draft["ids"]["lung.paula"]
    before = next(t for t in _edit(draft, uid, MONDAY, None).json()["totals"]
                  if t["user_id"] == uid)
    after = next(t for t in _edit(draft, uid, MONDAY, "Co").json()["totals"]
                 if t["user_id"] == uid)
    assert after["leave_days"] == before["leave_days"] + 1
    assert after["leave_credit"] == before["leave_credit"] + 8


# --- Co and shifts replace each other --------------------------------------

def test_co_replaces_an_existing_shift(draft):
    uid = draft["ids"]["lung.paula"]
    assert _edit(draft, uid, MONDAY, "Z").status_code == 200
    assert _edit(draft, uid, MONDAY, "Co").status_code == 200

    detail = draft["client"].get(f"/api/schedules/{draft['sid']}",
                                 headers=draft["h"]).json()
    cells = [a for a in detail["assignments"] if a["user_id"] == uid and a["date"] == MONDAY]
    assert cells == []                                  # the Z is gone
    assert (uid, MONDAY) in _days_off(draft)
    # ...so the cell cannot be both, and no leave_conflict is raised
    assert not any(v["code"] == "leave_conflict" for v in detail["violations"])


def test_co_replaces_a_locked_shift(draft):
    """Clearing a locked cell is already allowed, so Co may replace one too."""
    uid = draft["ids"]["lung.paula"]
    assert _edit(draft, uid, MONDAY, "Z", locked=True).status_code == 200
    assert _edit(draft, uid, MONDAY, "Co").status_code == 200
    assert (uid, MONDAY) in _days_off(draft)


def test_a_shift_replaces_an_existing_co(draft):
    uid = draft["ids"]["lung.paula"]
    assert _edit(draft, uid, MONDAY, "Co").status_code == 200

    r = _edit(draft, uid, MONDAY, "Z")
    assert r.status_code == 200, r.text
    assert (uid, MONDAY) not in _days_off(draft)
    assert not any(v["code"] == "leave_conflict" for v in r.json()["violations"])


# --- the Monday-Friday rule ------------------------------------------------

@pytest.mark.parametrize("day", [SATURDAY, SUNDAY])
def test_co_is_refused_on_a_weekend(draft, day):
    r = _edit(draft, draft["ids"]["lung.paula"], day, "Co")
    assert r.status_code == 422
    assert "Monday-Friday" in r.json()["detail"]


def test_co_is_refused_on_a_public_holiday(draft):
    """Aug 15 2026 (Adormirea Maicii Domnului) - a holiday is not a working day."""
    ids = draft["ids"]
    h, client = draft["h"], draft["client"]
    sid = client.post("/api/schedules", headers=h,
                      json=_create_payload(ids, month=8, target=168)).json()["id"]
    r = client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={
        "edits": [{"user_id": ids["lung.paula"], "date": "2026-08-15", "shift": "Co"}]})
    assert r.status_code == 422


def test_co_is_refused_for_someone_off_the_roster(draft):
    from tests.api_helpers import add_user
    outsider = add_user(draft["client"].app, "not.rostered", section="BIG")
    r = _edit(draft, outsider, MONDAY, "Co")
    assert r.status_code == 422


def test_co_edits_are_refused_once_the_month_is_locked(draft):
    """Co is editable like any cell - and, like any cell, only while draft."""
    client, sid = draft["client"], draft["sid"]
    session = client.app.state.sessionmaker()
    try:
        from app.models.tables import STATUS_LOCKED, Schedule
        session.get(Schedule, sid).status = STATUS_LOCKED
        session.commit()
    finally:
        session.close()
    r = _edit(draft, draft["ids"]["lung.paula"], MONDAY, "Co")
    assert r.status_code == 409


# --- the bridged weekend surfaces in the payload ---------------------------

def test_blocked_days_appear_once_a_weekend_is_bridged(draft):
    uid = draft["ids"]["lung.paula"]
    assert _setup(draft)["blocked_days"] == []

    _edit(draft, uid, FRIDAY, "Co")
    assert _setup(draft)["blocked_days"] == []      # one side only - not bridged yet

    _edit(draft, uid, MONDAY, "Co")
    blocked = {(x["user_id"], x["date"]) for x in _setup(draft)["blocked_days"]}
    assert blocked == {(uid, SATURDAY), (uid, SUNDAY)}

    # ...and removing one side un-bridges it again
    _edit(draft, uid, MONDAY, None)
    assert _setup(draft)["blocked_days"] == []


def test_a_hand_placed_shift_on_a_bridged_weekend_is_a_violation(draft):
    uid = draft["ids"]["lung.paula"]
    _edit(draft, uid, FRIDAY, "Co")
    _edit(draft, uid, MONDAY, "Co")

    r = _edit(draft, uid, SATURDAY, "Z")
    assert r.status_code == 200, r.text          # allowed, but flagged
    assert any(v["code"] == "leave_bridge" for v in r.json()["violations"])
    assert r.json()["ok"] is False               # blocks approval
