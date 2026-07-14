"""Solver + validator tests for the infirmiere staff type (plan 2026-07-14).

Infirmiere are scheduled separately from nurses: no sections, no kitchen, no
head. Daily coverage is 1 Z + 1 N + 1 x 8h, with a penalized swap exception to
2 Z + 1 N used only when leave/hour targets force it.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.solver import calendar as cal
from app.solver.domain import (STAFF_AIDE, Assignment, Config, Nurse,
                               ScheduleInput, Shift)
from app.solver.model import solve
from app.solver.validator import validate

JULY_HOLIDAYS = cal.default_holidays(2026, 7)


def aides(n: int) -> list[Nurse]:
    # Infirmiere carry no section (one ward-wide pool) and are never kitchen.
    return [Nurse(i, f"Infirmiera {i}", section="") for i in range(1, n + 1)]


def aide_input(**kw) -> ScheduleInput:
    cfg = Config(seed=7, max_time_s=60.0)
    defaults = dict(year=2026, month=7, base_target=184, nurses=aides(8),
                    staff_type=STAFF_AIDE, config=cfg)
    defaults.update(kw)
    return ScheduleInput(**defaults)


@pytest.fixture(scope="module")
def july_aides():
    inp = aide_input()
    return inp, solve(inp)


def test_infirmiere_solves_and_is_clean(july_aides):
    inp, res = july_aides
    assert res.ok, (res.status, res.diagnosis, [i.message for i in res.prechecks])
    report = validate(inp, res.assignments)
    assert report.ok, [v.message for v in report.violations]


def test_infirmiere_daily_coverage_one_z_one_n_one_eight(july_aides):
    """The unconstrained happy path fields 1 N, at least 1 Z and exactly one 8h
    slot every day (extra hours spill into extra Z/N within the caps, never a
    swap). No day drops below the 1Z+1N+1x8 minimum."""
    inp, res = july_aides
    report = validate(inp, res.assignments)
    for day, c in report.per_day.items():
        assert c["N"] >= 1, (day, c)
        assert c["Z"] >= 1, (day, c)
        assert c["E8"] == 1, (day, c)   # 8h slot filled, no swap


def test_infirmiere_no_swap_days_when_unconstrained(july_aides):
    """Swap (2Z+1N) is penalized, so it is never used when 1Z+1N+1x8 is feasible."""
    inp, res = july_aides
    swap_days = [d for d, c in validate(inp, res.assignments).per_day.items() if c["E8"] == 0]
    assert swap_days == []
    # the 8h shift really exists on weekends/holidays too, not just working days
    eight = [a for a in res.assignments if a.shift is Shift.EIGHT]
    assert len({a.date for a in eight}) == 31


def test_infirmiere_nights_min_and_band(july_aides):
    inp, res = july_aides
    for nid, t in res.totals.items():
        assert t.nights >= 3, (nid, t)
        assert abs(t.owed) <= inp.config.band, (nid, t)
        assert t.kitchens == 0  # infirmiere never touch the kitchen


def test_infirmiere_no_kitchen_or_head_shifts(july_aides):
    _, res = july_aides
    kinds = {a.shift for a in res.assignments}
    assert Shift.KITCHEN not in kinds
    assert Shift.HEAD not in kinds
    assert Shift.EIGHT in kinds


def test_infirmiere_swap_used_only_under_pressure():
    """Under pressure the solver reaches for the 2Z+1N swap. With the day/night
    caps clamped to the minimum (extra_day_cap = extra_night_cap = 0) a day can
    hold at most 1Z+1N+1x8 = 32h, so 6 infirmiere at 184h each (1104h) cannot be
    placed without converting some 8h slots to a second Z. The result stays valid
    and every day still satisfies the swap-coverage rule e + min(z,2) >= 2."""
    inp = aide_input(nurses=aides(6), base_target=184,
                     config=Config(seed=7, max_time_s=90.0,
                                   extra_day_cap=0, extra_night_cap=0))
    res = solve(inp)
    assert res.ok, (res.status, res.diagnosis, [i.message for i in res.prechecks])
    report = validate(inp, res.assignments)
    assert report.ok, [v.message for v in report.violations]
    swap_days = [d for d, c in report.per_day.items() if c["E8"] == 0]
    assert swap_days, "expected the solver to use the 2Z+1N swap under pressure"
    for day, c in report.per_day.items():
        # each day is 1Z+1N+1x8 or the accepted 2Z+1N exception
        assert c["N"] >= 1 and c["E8"] + min(c["Z"], 2) >= 2, (day, c)


def test_infirmiere_determinism_same_seed():
    cfg = dict(year=2026, month=7, base_target=184, nurses=aides(8), staff_type=STAFF_AIDE)
    a = solve(ScheduleInput(**cfg, config=Config(seed=123, max_time_s=60.0, workers=1)))
    b = solve(ScheduleInput(**cfg, config=Config(seed=123, max_time_s=60.0, workers=1)))
    assert a.ok and b.ok
    assert a.assignments == b.assignments


# --- validator acceptance / rejection --------------------------------------

def _day_assignments(day, *, z=0, n=0, e=0, base_id=1):
    """Build a day's worth of infirmiere assignments with z day, n night, e 8h."""
    out = []
    nid = base_id
    for _ in range(z):
        out.append(Assignment(nid, day, Shift.DAY, 12, *cal.shift_hours(day, Shift.DAY, 12, JULY_HOLIDAYS)))
        nid += 1
    for _ in range(n):
        out.append(Assignment(nid, day, Shift.NIGHT, 12, *cal.shift_hours(day, Shift.NIGHT, 12, JULY_HOLIDAYS)))
        nid += 1
    for _ in range(e):
        out.append(Assignment(nid, day, Shift.EIGHT, 8, *cal.shift_hours(day, Shift.EIGHT, 8, JULY_HOLIDAYS)))
        nid += 1
    return out


def _codes(issues):
    return {i.code for i in issues}


def test_validator_accepts_two_z_one_n_swap():
    """A 2Z+1N day (e==0, z>=2) is a VALID accepted exception, no violation."""
    day = dt.date(2026, 7, 6)  # Monday
    inp = aide_input()
    report = validate(inp, _day_assignments(day, z=2, n=1, e=0))
    codes = {v.code for v in report.violations if v.date == day}
    assert "coverage_eight" not in codes
    assert "coverage_day" not in codes and "coverage_night" not in codes
    assert report.per_day[day] == {"Z": 2, "N": 1, "E8": 0}


def test_validator_rejects_one_z_one_n_without_eight():
    """1 Z + 1 N with no 8h and no second Z leaves the daytime slot unfilled."""
    day = dt.date(2026, 7, 6)
    inp = aide_input()
    report = validate(inp, _day_assignments(day, z=1, n=1, e=0))
    assert "coverage_eight" in {v.code for v in report.violations if v.date == day}


def test_validator_rejects_kitchen_and_head_on_infirmiere():
    day = dt.date(2026, 7, 6)
    inp = aide_input()
    bad = [
        Assignment(1, day, Shift.KITCHEN, 8, 8, 0),
        Assignment(2, day, Shift.HEAD, 8, 8, 0),
    ]
    report = validate(inp, bad)
    assert "shift_not_allowed" in _codes(report.violations)
    # both the K and the H are flagged
    assert sum(1 for v in report.violations if v.code == "shift_not_allowed") >= 2


def test_validator_rejects_eight_shift_on_nurse_schedule():
    """The 8h shift is infirmiere-only: it is a violation on a nurse schedule."""
    from app.solver.cli import DEMO_NURSES
    day = dt.date(2026, 7, 6)
    inp = ScheduleInput(2026, 7, 184, DEMO_NURSES)  # default staff_type = nurse
    report = validate(inp, [Assignment(1, day, Shift.EIGHT, 8, 8, 0)])
    assert "shift_not_allowed" in _codes(report.violations)
