"""Solver + validator tests for the infirmiere staff type (plan 2026-07-14).

Infirmiere are scheduled separately from nurses: no sections, no kitchen, no
head. Daily coverage is 1 Z + 1 N + at least 1 x 8h, with a penalized swap
exception to 2 Z + 1 N used only when leave/hour targets force it. Since
2026-11-18 an infirmiera works only in 8 -> Z -> N blocks on three consecutive
days, followed by 48h off; extra hours come from extra blocks (several 8h
shifts on a day).
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.solver import calendar as cal
from app.solver.domain import (STAFF_AIDE, Assignment, Config, Nurse,
                               ScheduleInput, Shift, ShiftRequest)
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
    """The unconstrained happy path fields at least 1 N, 1 Z and one 8h shift
    every day (extra hours come as extra 8-Z-N blocks within the caps, never a
    swap). No day drops below the 1Z+1N+1x8 minimum."""
    inp, res = july_aides
    report = validate(inp, res.assignments)
    for day, c in report.per_day.items():
        assert c["N"] >= 1, (day, c)
        assert c["Z"] >= 1, (day, c)
        assert 1 <= c["E8"] <= 1 + inp.config.extra_day_cap, (day, c)  # no swap
    # the hours above the daily minimum really come from extra blocks
    assert any(c["E8"] > 1 for c in report.per_day.values())


def test_infirmiere_work_only_in_8_z_n_blocks(july_aides):
    """Every shift of every aide sits in an 8 -> Z -> N run on consecutive days,
    and the two days after each night are free (48h). Top-ups (T) stand alone.
    The month has no previous schedule, so a block cut by the 1st is allowed."""
    inp, res = july_aides
    report = validate(inp, res.assignments)
    assert not {"aide_block", "rest_after_night_48h"} & _codes(report.violations)
    assert "aide_block_break" not in _codes(report.warnings)
    grid = {(a.nurse_id, a.date): a.shift for a in res.assignments}
    dates = cal.month_dates(2026, 7)
    nxt = {Shift.EIGHT: Shift.DAY, Shift.DAY: Shift.NIGHT}
    for n in inp.nurses:
        for i, day in enumerate(dates):
            s = grid.get((n.id, day))
            if s in nxt and i + 1 < len(dates):
                assert grid.get((n.id, dates[i + 1])) is nxt[s], (n.id, day, s)
            if s is Shift.NIGHT:
                for k in (1, 2):
                    if i + k < len(dates):
                        assert (n.id, dates[i + k]) not in grid, (n.id, day)
                if i > 0:
                    assert grid.get((n.id, dates[i - 1])) is Shift.DAY, (n.id, day)
            if s is Shift.DAY and i > 0:
                assert grid.get((n.id, dates[i - 1])) is Shift.EIGHT, (n.id, day)


def test_infirmiere_no_swap_days_when_unconstrained(july_aides):
    """Swap (2Z+1N) is penalized, so it is never used when 1Z+1N+1x8 is feasible."""
    inp, res = july_aides
    swap_days = [d for d, c in validate(inp, res.assignments).per_day.items() if c["E8"] == 0]
    assert swap_days == []
    # the 8h shift really exists on weekends/holidays too, not just working days
    eight = [a for a in res.assignments if a.shift is Shift.EIGHT]
    assert len({a.date for a in eight}) == 31


def test_infirmiere_eight_shifts_are_balanced_across_aides():
    """The every-day 8h slot is spread across aides (w_eight_fair), not piled onto
    one: over an October with mixed Co, the busiest and quietest aide's 8h counts
    differ by only a few - without the term one aide collected 10 while another had 1."""
    from collections import Counter
    holidays = cal.default_holidays(2026, 10)
    wd = [d for d in cal.month_dates(2026, 10) if not cal.is_offday(d, holidays)]
    leave = {1: set(wd[0:5]), 3: set(wd[5:13]), 6: set(wd[13:22]), 7: set(wd[9:16])}
    roster = aides(7)
    inp = ScheduleInput(2026, 10, 176, roster, staff_type=STAFF_AIDE, leave=leave,
                        holidays=holidays, config=Config(seed=7, max_time_s=60.0, workers=8))
    res = solve(inp)
    assert res.ok, (res.status, res.diagnosis, [i.message for i in res.prechecks])
    assert validate(inp, res.assignments).ok
    eights = Counter(a.nurse_id for a in res.assignments if a.shift is Shift.EIGHT)
    counts = [eights.get(n.id, 0) for n in roster]
    assert max(counts) - min(counts) <= 4, counts


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


def test_hard_eight_request_pins_the_8h_slot_for_an_infirmiera():
    """For infirmiere the 8h slot already exists daily; a must-have 8H request
    pins it to the requested nurse that day and the month still validates."""
    day = dt.date(2026, 7, 15)  # a Wednesday working day
    inp = aide_input(requests=[ShiftRequest(3, day, "8", hard=True)])
    res = solve(inp)
    assert res.ok, (res.status, res.diagnosis, [i.message for i in res.prechecks])
    assert any(a.nurse_id == 3 and a.date == day and a.shift is Shift.EIGHT
               for a in res.assignments)
    granted = {(r.nurse_id, r.date): g for r, g in res.requests}
    assert granted[3, day] is True
    report = validate(inp, res.assignments)
    assert report.ok, [v.message for v in report.violations]


def test_infirmiere_swap_used_only_under_pressure():
    """The 2Z+1N swap is still the escape hatch when no aide can take the day's
    8h shift. Seven aides; on July 15 three are on Co and two are pinned (locked)
    to a day shift. Of the other two, one must work that night and the other the
    night before (the Co aides cannot take the eve of their leave), so she rests
    on the 15th - nobody can open a block with an 8h. The solver swaps that day
    and the month still validates clean (any Z left without its 8 is only a
    tolerated block-break warning)."""
    day = dt.date(2026, 7, 15)
    locks = [Assignment(nid, day, Shift.DAY, 12,
                        *cal.shift_hours(day, Shift.DAY, 12, JULY_HOLIDAYS, STAFF_AIDE))
             for nid in (1, 2)]
    inp = aide_input(nurses=aides(7), leave={5: {day}, 6: {day}, 7: {day}}, locks=locks,
                     config=Config(seed=7, max_time_s=60.0))
    res = solve(inp)
    assert res.ok, (res.status, res.diagnosis, [i.message for i in res.prechecks])
    report = validate(inp, res.assignments)
    assert report.ok, [v.message for v in report.violations]
    assert report.per_day[day]["E8"] == 0 and report.per_day[day]["Z"] >= 2
    swap_days = [d for d, c in report.per_day.items() if c["E8"] == 0]
    assert swap_days == [day]   # only where forced
    for d, c in report.per_day.items():
        # each day is 1Z+1N+1x8 or the accepted 2Z+1N exception
        assert c["N"] >= 1 and c["E8"] + min(c["Z"], 2) >= 2, (d, c)


def test_infirmiere_determinism_same_seed():
    cfg = dict(year=2026, month=7, base_target=184, nurses=aides(8), staff_type=STAFF_AIDE)
    # A single worker does not prove optimality on the 8-Z-N block model within a
    # wall-clock limit, and where a wall-clock stop lands depends on CPU load; a
    # deterministic-time budget stops both runs at the same point.
    det = dict(seed=123, max_time_s=600.0, max_det_time=10.0, workers=1)
    a = solve(ScheduleInput(**cfg, config=Config(**det)))
    b = solve(ScheduleInput(**cfg, config=Config(**det)))
    assert a.ok and b.ok
    assert a.assignments == b.assignments


# --- validator acceptance / rejection --------------------------------------

def _day_assignments(day, *, z=0, n=0, e=0, base_id=1):
    """Build a day's worth of infirmiere assignments with z day, n night, e 8h.

    STAFF_AIDE matters only for the night, whose 18:00-06:00 splits 6h/6h rather
    than the asistente 5h/7h - invisible on a midweek day, wrong the moment the
    night straddles a weekend.
    """
    out = []
    nid = base_id
    for _ in range(z):
        out.append(Assignment(nid, day, Shift.DAY, 12,
                              *cal.shift_hours(day, Shift.DAY, 12, JULY_HOLIDAYS, STAFF_AIDE)))
        nid += 1
    for _ in range(n):
        out.append(Assignment(nid, day, Shift.NIGHT, 12,
                              *cal.shift_hours(day, Shift.NIGHT, 12, JULY_HOLIDAYS, STAFF_AIDE)))
        nid += 1
    for _ in range(e):
        out.append(Assignment(nid, day, Shift.EIGHT, 8,
                              *cal.shift_hours(day, Shift.EIGHT, 8, JULY_HOLIDAYS, STAFF_AIDE)))
        nid += 1
    return out


def _codes(issues):
    return {i.code for i in issues}


# --- the 06:00 clock (infirmiere work 06-18 / 18-06) -----------------------

def test_infirmiera_night_into_saturday_is_banked_six_six():
    """Their 18:00-06:00 night lays 6h on Friday and 6h on Saturday; an
    asistenta's 19:00-07:00 would be 5h/7h. Friday July 3 2026."""
    fri = dt.date(2026, 7, 3)
    assert cal.shift_hours(fri, Shift.NIGHT, 12, JULY_HOLIDAYS, STAFF_AIDE) == (6, 6)


def test_validator_rejects_an_asistenta_split_on_an_infirmiere_night():
    """A night stored 5/7 on an infirmiere schedule is the asistente clock and
    must be caught - the same guard that keeps hand-edits honest."""
    fri = dt.date(2026, 7, 3)
    inp = aide_input()
    wrong = [Assignment(1, fri, Shift.NIGHT, 12, 5, 7)]   # asistenta split
    assert "bad_bucket_split" in _codes(validate(inp, wrong).violations)
    right = [Assignment(1, fri, Shift.NIGHT, 12, 6, 6)]   # infirmiera split
    assert "bad_bucket_split" not in _codes(validate(inp, right).violations)


def test_solver_banks_infirmiere_nights_on_the_six_six_clock():
    """End to end: every night the solver emits for infirmiere is bucketed on
    their own clock, and the validator - which re-derives it - agrees."""
    inp = aide_input(config=Config(seed=7, max_time_s=30.0, workers=8))
    res = solve(inp)
    assert res.ok, (res.status, res.diagnosis)
    nights = [a for a in res.assignments if a.shift is Shift.NIGHT]
    assert nights
    for a in nights:
        assert (a.normal_hours, a.weekend_hours) == \
            cal.shift_hours(a.date, Shift.NIGHT, 12, JULY_HOLIDAYS, STAFF_AIDE)
        assert {a.normal_hours, a.weekend_hours} != {5, 7}  # never the nurse clock
    assert validate(inp, res.assignments).ok


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


def test_validator_allows_eight_shift_on_nurse_schedule():
    """The 8h shift is now a legal asistente shift too (the solver only ever
    places it to honor an explicit 8h request): the validator no longer flags it
    as not-allowed."""
    from app.solver.cli import DEMO_NURSES
    day = dt.date(2026, 7, 6)
    inp = ScheduleInput(2026, 7, 184, DEMO_NURSES)  # default staff_type = nurse
    report = validate(inp, [Assignment(1, day, Shift.EIGHT, 8, 8, 0)])
    assert "shift_not_allowed" not in _codes(report.violations)


# --- 8-Z-N blocks + 48h (decided 2026-11-18) -------------------------------

def _a(nid, day, shift):
    dur = cal.duration_of(shift)
    return Assignment(nid, day, shift, dur,
                      *cal.shift_hours(day, shift, dur, JULY_HOLIDAYS, STAFF_AIDE))


MON = dt.date(2026, 7, 6)


def _days(k):
    return MON + dt.timedelta(days=k)


def _block_issues(assignments, **kw):
    report = validate(aide_input(**kw), assignments)
    return ({(v.nurse_id, v.date) for v in report.violations if v.code == "aide_block"},
            _codes(report.warnings))


def test_validator_accepts_a_full_block():
    blk = [_a(1, _days(0), Shift.EIGHT), _a(1, _days(1), Shift.DAY), _a(1, _days(2), Shift.NIGHT)]
    assert _block_issues(blk)[0] == set()


@pytest.mark.parametrize("pattern", [
    [Shift.EIGHT, None, Shift.DAY],             # 8, free, Z ...  (the pattern to forbid)
    [Shift.EIGHT, Shift.NIGHT],                 # 8 then N
    [Shift.DAY, None],                          # Z not followed by N
    [None, Shift.NIGHT],                        # N not after a Z
    [None, Shift.DAY, Shift.NIGHT],             # Z without the block's 8 (no swap day)
])
def test_validator_rejects_a_broken_block(pattern):
    shifts = [_a(1, _days(k), s) for k, s in enumerate(pattern) if s is not None]
    assert _block_issues(shifts)[0], pattern


def test_validator_tolerates_block_cut_by_leave_and_month_end():
    # 8 then Co: the block is cut by leave - no aide_block
    cut = [_a(1, _days(0), Shift.EIGHT)]
    assert _block_issues(cut, leave={1: {_days(1)}})[0] == set()
    # back from Co straight onto Z, N
    back = [_a(1, _days(1), Shift.DAY), _a(1, _days(2), Shift.NIGHT)]
    assert _block_issues(back, leave={1: {_days(0)}})[0] == set()
    # 8 on the 30th, Z on the 31st: the night follows next month
    end = [_a(1, dt.date(2026, 7, 30), Shift.EIGHT), _a(1, dt.date(2026, 7, 31), Shift.DAY)]
    assert _block_issues(end)[0] == set()


def test_validator_checks_the_block_across_the_month_start():
    tail = {1: {dt.date(2026, 6, 30): Shift.EIGHT}, 2: {dt.date(2026, 6, 30): Shift.DAY}}
    d1, d2 = dt.date(2026, 7, 1), dt.date(2026, 7, 2)
    good = [_a(1, d1, Shift.DAY), _a(1, d2, Shift.NIGHT), _a(2, d1, Shift.NIGHT)]
    assert _block_issues(good, prev_tail=tail)[0] == set()
    bad = [_a(2, d1, Shift.DAY)]   # tail Z must continue with N, aide 1 must do Z
    assert {(1, d1), (2, d1)} <= _block_issues(bad, prev_tail=tail)[0]


def test_validator_reports_a_swap_block_break_as_a_warning():
    """On a 2Z+1N day the second Z has no 8 before it: a tolerated break."""
    day = _days(3)
    assignments = [
        _a(1, _days(2), Shift.EIGHT), _a(1, day, Shift.DAY), _a(1, _days(4), Shift.NIGHT),
        _a(2, day, Shift.DAY), _a(2, _days(4), Shift.NIGHT),
        _a(3, _days(1), Shift.EIGHT), _a(3, _days(2), Shift.DAY), _a(3, day, Shift.NIGHT),
    ]
    blocks, warnings = _block_issues(assignments)
    assert (2, day) not in blocks
    assert "aide_block_break" in warnings


def test_validator_allows_several_eights_up_to_the_cap():
    inp = aide_input()
    cap = 1 + inp.config.extra_day_cap
    def eight_issues(e):
        report = validate(inp, _day_assignments(MON, z=1, n=1, e=e))
        return {v.code for v in report.violations if v.date == MON}
    assert "coverage_eight" not in eight_issues(cap)
    assert "coverage_eight" in eight_issues(cap + 1)


def test_solver_continues_a_block_from_the_previous_month():
    """A block opened on June 30 (8h) carries into July: Z on the 1st, N on the
    2nd; an aide whose June ended on a night stays off July 1-2 (48h)."""
    tail = {1: {dt.date(2026, 6, 30): Shift.EIGHT},
            2: {dt.date(2026, 6, 29): Shift.EIGHT, dt.date(2026, 6, 30): Shift.DAY},
            3: {dt.date(2026, 6, 29): Shift.DAY, dt.date(2026, 6, 30): Shift.NIGHT}}
    inp = aide_input(prev_tail=tail, config=Config(seed=7, max_time_s=30.0))
    res = solve(inp)
    assert res.ok, (res.status, res.diagnosis, [i.message for i in res.prechecks])
    grid = {(a.nurse_id, a.date): a.shift for a in res.assignments}
    d1, d2 = dt.date(2026, 7, 1), dt.date(2026, 7, 2)
    assert grid.get((1, d1)) is Shift.DAY and grid.get((1, d2)) is Shift.NIGHT
    assert grid.get((2, d1)) is Shift.NIGHT
    assert (3, d1) not in grid and (3, d2) not in grid
    report = validate(inp, res.assignments)
    assert report.ok, [v.message for v in report.violations]
