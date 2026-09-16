"""Two hard staffing floors (decided 2026-09-16):

- every rostered non-head nurse must reach Config.weekend_floor (24h) of 100%
  (weekend/holiday) hours, both asistente and infirmiere;
- an infirmiera may work at most Config.max_consec_aide (4) days in a row, so she
  runs short 8-Z-N stretches, not 8-8-8-Z-N.

The solver keeps both; the validator blocks approval when either is broken; a
precheck rejects a roster whose weekends cannot supply 24h up front.
"""
from __future__ import annotations

import datetime as dt

from app.solver import calendar as cal
from app.solver.domain import (STAFF_AIDE, STAFF_NURSE, Assignment, Config,
                               Nurse, ScheduleInput, Shift)
from app.solver.model import solve
from app.solver.prechecks import run_prechecks
from app.solver.validator import validate

H = cal.default_holidays(2026, 10)
DATES = cal.month_dates(2026, 10)
WD = [d for d in DATES if not cal.is_offday(d, H)]
OFF = [d for d in DATES if cal.is_offday(d, H)]  # Sat/Sun/holidays, 12h each if Z


def aides(n: int) -> list[Nurse]:
    return [Nurse(i, f"Inf{i}", section="") for i in range(1, n + 1)]


def A(nid: int, day: dt.date, shift: Shift, staff=STAFF_AIDE) -> Assignment:
    dur = cal.duration_of(shift)
    nh, wh = cal.shift_hours(day, shift, dur, H, staff)
    return Assignment(nid, day, shift, dur, nh, wh)


def codes_for(report, nid):
    return {v.code for v in report.violations if v.nurse_id == nid}


# --- validator: 24h 100% floor -------------------------------------------------

def test_validator_flags_nurse_under_24h_of_100_percent():
    inp = ScheduleInput(2026, 10, 176, aides(3), staff_type=STAFF_AIDE, holidays=H)
    # nurse 1: two weekend day shifts = 24h of 100%; nurse 2: one = 12h (short).
    assignments = [
        A(1, OFF[0], Shift.DAY), A(1, OFF[1], Shift.DAY),
        A(2, OFF[0], Shift.DAY),
    ]
    report = validate(inp, assignments)
    assert "weekend_floor" in codes_for(report, 2)      # 12h < 24h
    assert "weekend_floor" not in codes_for(report, 1)  # 24h is enough


def test_head_is_exempt_from_the_24h_floor():
    # An asistenta head works only 8h weekday shifts -> 0 weekend hours, and must
    # not be flagged for the floor.
    head = Nurse(1, "Sefa", section="BIG", role="manager_nurse")
    worker = Nurse(2, "Ana", section="BIG")
    inp = ScheduleInput(2026, 10, 176, [head, worker], staff_type=STAFF_NURSE, holidays=H)
    assignments = [A(1, WD[0], Shift.HEAD, staff=STAFF_NURSE)]  # head, weekday, 0 weekend
    report = validate(inp, assignments)
    assert "weekend_floor" not in codes_for(report, 1)


# --- validator: infirmiere run cap --------------------------------------------

def test_validator_flags_five_consecutive_worked_days():
    inp = ScheduleInput(2026, 10, 176, aides(2), staff_type=STAFF_AIDE, holidays=H)
    five = [A(1, DATES[d], Shift.EIGHT) for d in range(4, 9)]  # 5 in a row
    assert "aide_run" in codes_for(validate(inp, five), 1)


def test_validator_allows_four_consecutive_worked_days():
    inp = ScheduleInput(2026, 10, 176, aides(2), staff_type=STAFF_AIDE, holidays=H)
    four = [A(1, DATES[d], Shift.EIGHT) for d in range(4, 8)]  # 4 in a row
    assert "aide_run" not in codes_for(validate(inp, four), 1)


def test_run_cap_counts_the_previous_month_tail():
    # Two worked tail days + three worked month days = a 5-run across the boundary.
    prev = {1: {DATES[0] - dt.timedelta(days=2): Shift.EIGHT,
                DATES[0] - dt.timedelta(days=1): Shift.EIGHT}}
    inp = ScheduleInput(2026, 10, 176, aides(2), staff_type=STAFF_AIDE,
                        holidays=H, prev_tail=prev)
    start = [A(1, DATES[d], Shift.EIGHT) for d in range(0, 3)]
    assert "aide_run" in codes_for(validate(inp, start), 1)


# --- precheck: unreachable floor ----------------------------------------------

def test_precheck_rejects_a_roster_that_cannot_supply_24h():
    # One aide on Co every working day that flanks the weekends -> her weekends are
    # bridged away, so she cannot reach 24h of 100% hours; flagged up front.
    allpool = aides(6)
    leave = {1: set(WD)}  # id 1 on Co every weekday -> weekends bridged off
    inp = ScheduleInput(2026, 10, 176, allpool, staff_type=STAFF_AIDE,
                        leave=leave, holidays=H)
    codes = {i.code for i in run_prechecks(inp) if i.nurse_id == 1}
    assert "weekend_floor_unreachable" in codes


# --- end to end ---------------------------------------------------------------

def test_solver_output_honours_both_floors():
    # October, several aides on Co: the solve must reach 24h for everyone and keep
    # every run at 4 days or fewer.
    leave = {1: set(WD[0:5]), 3: set(WD[5:13]), 6: set(WD[13:22])}
    cfg = Config(seed=7, max_time_s=60.0, workers=8)
    inp = ScheduleInput(2026, 10, 176, aides(7), staff_type=STAFF_AIDE,
                        leave=leave, holidays=H, config=cfg)
    res = solve(inp)
    assert res.ok, (res.status, res.diagnosis, [i.message for i in res.prechecks])
    report = validate(inp, res.assignments)
    assert report.ok, [v.message for v in report.violations]
    for n in inp.nurses:
        assert res.totals[n.id].worked_weekend >= 24, (n.name, res.totals[n.id].worked_weekend)
