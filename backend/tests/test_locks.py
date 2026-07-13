"""Solver-level tests for the M3 lock extension (ScheduleInput.locks).

A lock pins the matching decision variable to 1 so a hand-placed cell survives
regeneration. Absent variables (leave, missing kitchen flag, HEAD) are skipped.
"""
from __future__ import annotations

import datetime as dt

from app.solver import calendar as cal
from app.solver.cli import DEMO_NURSES
from app.solver.domain import Assignment, Config, ScheduleInput, Shift
from app.solver.model import solve


def _july_input(locks: list[Assignment]) -> ScheduleInput:
    holidays = cal.default_holidays(2026, 7)
    target = cal.working_days(2026, 7, holidays) * 8
    return ScheduleInput(
        year=2026, month=7, base_target=target, nurses=DEMO_NURSES,
        config=Config(seed=7, max_time_s=20.0, workers=4), locks=locks,
    )


def test_day_lock_is_honored():
    day = dt.date(2026, 7, 4)
    lock = Assignment(4, day, Shift.DAY, 12, 0, 12)  # July 4 is a Saturday (weekend hours)
    result = solve(_july_input([lock]))
    assert result.ok, result.status
    assert any(a.nurse_id == 4 and a.date == day and a.shift is Shift.NIGHT
               for a in result.assignments) is False
    assert any(a.nurse_id == 4 and a.date == day and a.shift is Shift.DAY
               for a in result.assignments)


def test_kitchen_lock_is_honored():
    day = dt.date(2026, 7, 15)
    lock = Assignment(1, day, Shift.KITCHEN, 8, 8, 0)  # nurse 1 is kitchen-capable
    result = solve(_july_input([lock]))
    assert result.ok, result.status
    assert any(a.nurse_id == 1 and a.date == day and a.shift is Shift.KITCHEN
               for a in result.assignments)


def test_absent_variable_lock_is_skipped():
    # Nurse 3 is not kitchen-capable, so no KITCHEN variable exists; the lock
    # must be ignored rather than crash, and the solve still succeeds.
    day = dt.date(2026, 7, 10)
    lock = Assignment(3, day, Shift.KITCHEN, 8, 0, 8)
    result = solve(_july_input([lock]))
    assert result.ok, result.status
    assert not any(a.nurse_id == 3 and a.date == day and a.shift is Shift.KITCHEN
                   for a in result.assignments)
