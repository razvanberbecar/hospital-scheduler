"""The night-before-Co rule: a night runs 19:00-07:00, so a night the day before
a Co day would have the nurse working 7h of her own leave day.

Hard rule (blocks approval), and within the month only: a Co on the 1st says
nothing about the previous month's last day, which is usually locked and could
not be corrected anyway.
"""
import datetime as dt

from app.solver import calendar as cal
from app.solver.cli import DEMO_NURSES
from app.solver.domain import (BIG, SMALL, Assignment, Config, Nurse,
                               ScheduleInput, Shift, ShiftRequest)
from app.solver.model import solve
from app.solver.prechecks import effective_requests
from app.solver.validator import validate

HOLIDAYS = cal.default_holidays(2026, 7)
# July 2026: the 1st is a Wednesday, the 31st a Friday.
WED1, THU2, FRI3 = (dt.date(2026, 7, n) for n in (1, 2, 3))
MON6, TUE7, WED8 = (dt.date(2026, 7, n) for n in (6, 7, 8))
FRI31 = dt.date(2026, 7, 31)

NURSES = [
    Nurse(1, "A", BIG, kitchen_capable=True),
    Nurse(2, "B", SMALL, kitchen_capable=True),
]


def mk(nid, day, shift):
    dur = cal.duration_of(shift)
    nh, wh = cal.shift_hours(day, shift, dur, HOLIDAYS)
    return Assignment(nid, day, shift, dur, nh, wh)


def inp_with_leave(leave):
    return ScheduleInput(2026, 7, 184, NURSES, leave=leave, holidays=HOLIDAYS)


# --- the helper ------------------------------------------------------------

def test_night_blocked_is_the_eve_of_each_co_day():
    inp = inp_with_leave({1: {TUE7}})
    assert inp.night_blocked_of(1) == {MON6}
    assert inp.night_blocked_of(2) == set()


def test_night_blocked_ignores_the_month_boundary():
    """Co on the 1st would point at June 30 - out of scope (decided)."""
    inp = inp_with_leave({1: {WED1}})
    assert inp.night_blocked_of(1) == set()


def test_night_blocked_covers_a_run_of_co_days():
    inp = inp_with_leave({1: {TUE7, WED8}})
    assert inp.night_blocked_of(1) == {MON6, TUE7}


# --- validator -------------------------------------------------------------

def test_validator_flags_a_night_the_day_before_co():
    inp = inp_with_leave({1: {TUE7}})
    report = validate(inp, [mk(1, MON6, Shift.NIGHT)])
    assert "night_before_leave" in {v.code for v in report.violations}
    assert not report.ok  # hard rule


def test_a_day_shift_before_co_is_fine():
    """Only the night spills over; a Z ends at 19:00, well before the Co day."""
    inp = inp_with_leave({1: {TUE7}})
    report = validate(inp, [mk(1, MON6, Shift.DAY)])
    assert "night_before_leave" not in {v.code for v in report.violations}


def test_a_night_not_before_co_is_fine():
    inp = inp_with_leave({1: {FRI3}})
    report = validate(inp, [mk(1, MON6, Shift.NIGHT)])
    assert "night_before_leave" not in {v.code for v in report.violations}


def test_a_night_on_the_last_day_is_fine_whatever_next_month_holds():
    """The boundary is out of scope, so July 31 stays legal."""
    inp = inp_with_leave({1: set()})
    report = validate(inp, [mk(1, FRI31, Shift.NIGHT)])
    assert "night_before_leave" not in {v.code for v in report.violations}


def test_a_night_on_the_co_day_itself_reports_leave_conflict_only():
    """Co on both the 7th and 8th: a night on the 7th is already a leave
    conflict; it must not double-report as night_before_leave too."""
    inp = inp_with_leave({1: {TUE7, WED8}})
    codes = {v.code for v in validate(inp, [mk(1, TUE7, Shift.NIGHT)]).violations}
    assert "leave_conflict" in codes
    assert "night_before_leave" not in codes


# --- requests --------------------------------------------------------------

def test_a_night_request_before_co_is_dropped_not_silently_ignored():
    """Parity: the model has no NIGHT var on the eve of a Co day, so the request
    must be dropped for the solver and the validator alike."""
    inp = inp_with_leave({1: {TUE7}})
    inp.requests = [ShiftRequest(1, MON6, "N", hard=True)]
    kept, issues = effective_requests(inp)
    assert kept == []
    assert any(i.code == "request_night_before_leave" and i.level == "error" for i in issues)
    assert "hard_request_violated" not in {v.code for v in validate(inp, []).violations}


def test_a_day_request_before_co_is_kept():
    inp = inp_with_leave({1: {TUE7}})
    inp.requests = [ShiftRequest(1, MON6, "Z", hard=True)]
    kept, _ = effective_requests(inp)
    assert [r.kind for r in kept] == ["Z"]


# --- solver ----------------------------------------------------------------

def test_solver_never_places_a_night_the_day_before_co():
    """End to end on the real July roster: nurse 1 takes Co on the 7th and the
    14th, and never works the night before either."""
    leave = {1: {TUE7, dt.date(2026, 7, 14)}}
    inp = ScheduleInput(2026, 7, 184, DEMO_NURSES, leave=leave, holidays=HOLIDAYS,
                        config=Config(seed=7, max_time_s=30.0, workers=8))
    res = solve(inp)
    assert res.ok, (res.status, res.diagnosis, [i.message for i in res.prechecks])
    nights = {a.date for a in res.assignments
              if a.nurse_id == 1 and a.shift is Shift.NIGHT}
    assert MON6 not in nights
    assert dt.date(2026, 7, 13) not in nights
    report = validate(inp, res.assignments)
    assert report.ok, [v.message for v in report.violations]
