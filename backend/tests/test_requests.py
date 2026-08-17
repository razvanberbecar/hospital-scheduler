"""Nurse shift requests: soft by default with report, hard = pinned (Q&A round 5)."""
import datetime as dt

from app.solver.cli import DEMO_NURSES
from app.solver.domain import Config, ScheduleInput, Shift, ShiftRequest
from app.solver.model import solve
from app.solver.prechecks import effective_requests, has_errors, run_prechecks
from app.solver.validator import validate

import pytest


def d(day: int) -> dt.date:
    return dt.date(2026, 7, day)


def july(requests, leave=None):
    return ScheduleInput(2026, 7, 184, DEMO_NURSES, leave=leave or {},
                         requests=requests, config=Config(seed=9, max_time_s=45.0))


@pytest.fixture(scope="module")
def bundle_result():
    requests = [
        ShiftRequest(4, d(4), "Z", hard=True),     # must-have Saturday day shift
        ShiftRequest(13, d(10), "Z"),
        ShiftRequest(14, d(24), "N"),
        ShiftRequest(10, d(31), "FREE"),
        ShiftRequest(9, d(6), "K"),
        # competition: six nurses want a day shift on Monday the 20th (cap is 4)
        ShiftRequest(1, d(20), "Z"),
        ShiftRequest(3, d(20), "Z"),
        ShiftRequest(5, d(20), "Z"),
        ShiftRequest(6, d(20), "Z"),
        ShiftRequest(12, d(20), "Z"),
        ShiftRequest(2, d(20), "Z"),
    ]
    inp = july(requests)
    return inp, solve(inp)


def test_bundle_solves_and_validates(bundle_result):
    inp, res = bundle_result
    assert res.ok, (res.status, res.diagnosis)
    report = validate(inp, res.assignments)
    assert report.ok, [v.message for v in report.violations]


def test_hard_request_is_always_honored(bundle_result):
    inp, res = bundle_result
    assert any(a.nurse_id == 4 and a.date == d(4) and a.shift is Shift.DAY
               for a in res.assignments)
    granted = {(r.nurse_id, r.date): g for r, g in res.requests}
    assert granted[4, d(4)] is True


def test_competition_respects_the_cap(bundle_result):
    _, res = bundle_result
    monday = [g for r, g in res.requests if r.date == d(20) and r.kind == "Z"]
    assert len(monday) == 6
    assert sum(monday) <= 4  # the staffing cap wins over wishes


def test_denied_requests_become_validator_warnings(bundle_result):
    inp, res = bundle_result
    denied = [(r.nurse_id, r.date) for r, g in res.requests if not g]
    report = validate(inp, res.assignments)
    warned = [(w.nurse_id, w.date) for w in report.warnings if w.code == "request_denied"]
    assert sorted(denied) == sorted(warned)


def test_request_on_leave_day_is_ignored_with_warning():
    leave = {5: {d(13)}}
    inp = july([ShiftRequest(5, d(13), "Z")], leave=leave)
    kept, issues = effective_requests(inp)
    assert kept == []
    assert any(i.code == "request_on_leave" and i.level == "warning" for i in issues)


def test_kitchen_request_needs_the_flag():
    inp = july([ShiftRequest(4, d(6), "K")])  # Codre is not kitchen-capable
    kept, issues = effective_requests(inp)
    assert kept == []
    assert any(i.code == "request_kitchen" and i.level == "warning" for i in issues)


def test_hard_contradictions_are_precheck_errors():
    overbooked = [ShiftRequest(i, d(20), "Z", hard=True) for i in (1, 2, 3, 4, 5)]
    assert has_errors(run_prechecks(july(overbooked)))

    rest_clash = [ShiftRequest(4, d(6), "N", hard=True),
                  ShiftRequest(4, d(7), "Z", hard=True)]
    issues = run_prechecks(july(rest_clash))
    assert any(i.code == "request_rest" and i.level == "error" for i in issues)


def test_infeasible_hard_requests_are_explained_in_the_diagnosis():
    """Plan 2 (A): when must-have requests make the month INFEASIBLE, the result's
    diagnosis names the originally-hard requests that a valid schedule must drop.
    Six BIG nurses (of seven) hard-FREE the same working day, so BIG cannot field
    its required Z+N pair - the prechecks pass, but the model is infeasible."""
    day = d(8)  # a Wednesday working day
    reqs = [ShiftRequest(nid, day, "FREE", hard=True) for nid in (1, 2, 3, 4, 5, 6)]
    inp = july(reqs)
    assert not has_errors(run_prechecks(inp))  # nothing catches it before solving
    res = solve(inp)
    assert res.status == "INFEASIBLE", res.status
    assert res.diagnosis, "an infeasible run with hard requests must be explained"
    assert res.diagnosis[0].startswith("Cererile obligatorii nu pot fi toate respectate")
    assert any(f"FREE pe {day}" in line for line in res.diagnosis[1:])


def test_removing_a_hard_request_assignment_is_a_violation(bundle_result):
    inp, res = bundle_result
    tampered = [a for a in res.assignments
                if not (a.nurse_id == 4 and a.date == d(4))]
    report = validate(inp, tampered)
    assert any(v.code == "hard_request_violated" for v in report.violations)
