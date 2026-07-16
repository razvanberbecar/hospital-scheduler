"""The Co-bridged weekend rule: a nurse on leave on the working day either side
of a weekend is away across it and cannot be worked.

The days in between are blocked but earn nothing - weekends are not working
days, so unlike Co itself they carry no 8h credit and do not count as leave.
"""
import datetime as dt

import pytest

from app.solver import calendar as cal
from app.solver.cli import DEMO_NURSES
from app.solver.domain import BIG, SMALL, Assignment, Config, Nurse, ScheduleInput, Shift
from app.solver.model import solve
from app.solver.prechecks import run_prechecks
from app.solver.validator import validate

HOLIDAYS = cal.default_holidays(2026, 7)
FRI, SAT, SUN, MON = (dt.date(2026, 7, n) for n in (3, 4, 5, 6))

NURSES = [
    Nurse(1, "A", BIG, kitchen_capable=True),
    Nurse(2, "B", SMALL, kitchen_capable=True),
]


def mk(nid, day, shift):
    dur = cal.duration_of(shift)
    nh, wh = cal.shift_hours(day, shift, dur, HOLIDAYS)
    return Assignment(nid, day, shift, dur, nh, wh)


def inp_with_leave(leave, nurses=None):
    return ScheduleInput(2026, 7, 184, nurses or NURSES, leave=leave, holidays=HOLIDAYS)


# --- the domain helpers ----------------------------------------------------

def test_blocked_of_reports_the_bridged_weekend():
    inp = inp_with_leave({1: {FRI, MON}})
    assert inp.blocked_of(1, HOLIDAYS) == {SAT, SUN}
    assert inp.unavailable_of(1, HOLIDAYS) == {FRI, SAT, SUN, MON}
    assert inp.blocked_of(2, HOLIDAYS) == set()  # nurse B has no leave at all


def test_bridged_days_are_not_leave_and_earn_no_credit():
    """The whole point of blocked-not-Co: her leave count and 8h/day credit are
    unchanged by the bridge, so the weekend is worth 0h, not 16h."""
    inp = inp_with_leave({1: {FRI, MON}})
    report = validate(inp, [])
    totals = report.totals[1]
    assert totals.leave_days == 2          # Friday and Monday only
    assert totals.leave_credit == 2 * 8    # not 4 * 8
    assert SAT not in inp.leave_of(1)


# --- validator -------------------------------------------------------------

def test_validator_flags_a_shift_on_a_bridged_weekend():
    inp = inp_with_leave({1: {FRI, MON}})
    report = validate(inp, [mk(1, SAT, Shift.DAY)])
    assert "leave_bridge" in {v.code for v in report.violations}
    assert not report.ok  # hard rule: blocks approval


def test_validator_allows_the_weekend_when_only_one_side_is_co():
    inp = inp_with_leave({1: {FRI}})
    report = validate(inp, [mk(1, SAT, Shift.DAY)])
    assert "leave_bridge" not in {v.code for v in report.violations}


def test_validator_keeps_leave_conflict_distinct_from_the_bridge():
    """A shift on the Co day itself is still leave_conflict, not leave_bridge."""
    inp = inp_with_leave({1: {FRI, MON}})
    report = validate(inp, [mk(1, FRI, Shift.DAY)])
    codes = {v.code for v in report.violations}
    assert "leave_conflict" in codes and "leave_bridge" not in codes


# --- prechecks -------------------------------------------------------------

def test_prechecks_count_a_bridged_weekend_as_unavailable():
    """Both BIG nurses away across the same weekend leaves the section with
    nobody to field Saturday's Z and N."""
    nurses = [Nurse(1, "A", BIG), Nurse(2, "B", BIG), Nurse(3, "C", SMALL)]
    inp = ScheduleInput(2026, 7, 184, nurses, holidays=HOLIDAYS,
                        leave={1: {FRI, MON}, 2: {FRI, MON}})
    issues = run_prechecks(inp)
    assert any(i.code == "section_unavailable" and i.date == SAT for i in issues), \
        [i.message for i in issues]


# --- requests --------------------------------------------------------------

def test_a_request_on_a_bridged_day_is_dropped_not_silently_ignored():
    """Solver/validator parity: the model has no variable for a bridged day, so
    a request there must be dropped by effective_requests for BOTH of them -
    otherwise the solver quietly ignores a must-have and the validator flags it,
    and generation yields a schedule that fails its own validation."""
    from app.solver.prechecks import effective_requests
    from app.solver.domain import ShiftRequest

    inp = inp_with_leave({1: {FRI, MON}})
    inp.requests = [ShiftRequest(1, SAT, "Z", hard=True)]
    kept, issues = effective_requests(inp)
    assert kept == []
    assert any(i.code == "request_on_leave_bridge" and i.level == "error" for i in issues)

    # ...so the validator raises no hard_request_violated for it either
    report = validate(inp, [])
    assert "hard_request_violated" not in {v.code for v in report.violations}


# --- solver ----------------------------------------------------------------

@pytest.mark.parametrize("staff_type", ["asistenta"])
def test_solver_never_schedules_a_bridged_weekend(staff_type):
    """End to end on the real July roster: the nurse with Co either side of the
    first weekend gets no shift on it, and the result validates clean."""
    leave = {1: {FRI, MON}}
    inp = ScheduleInput(2026, 7, 184, DEMO_NURSES, leave=leave, holidays=HOLIDAYS,
                        config=Config(seed=7, max_time_s=30.0, workers=8))
    res = solve(inp)
    assert res.ok, (res.status, res.diagnosis, [i.message for i in res.prechecks])
    hers = {a.date for a in res.assignments if a.nurse_id == 1}
    assert SAT not in hers and SUN not in hers
    report = validate(inp, res.assignments)
    assert report.ok, [v.message for v in report.violations]
    assert "leave_bridge" not in {v.code for v in report.violations}
