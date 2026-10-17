"""Golden-month and chain tests: the solver against the real July 2026 calendar."""
import datetime as dt

import pytest

from app.solver import calendar as cal
from app.solver.cli import DEMO_NURSES, demo_leave
from app.solver.domain import Config, ScheduleInput, Shift
from app.solver.model import solve
from app.solver.validator import validate

JULY_HOLIDAYS = cal.default_holidays(2026, 7)


def july_input(**kw):
    cfg = Config(seed=7, max_time_s=60.0)
    defaults = dict(year=2026, month=7, base_target=184, nurses=DEMO_NURSES, config=cfg)
    defaults.update(kw)
    return ScheduleInput(**defaults)


@pytest.fixture(scope="module")
def july_result():
    inp = july_input()
    return inp, solve(inp)


def test_july_solves(july_result):
    _, res = july_result
    assert res.ok, (res.status, res.diagnosis, [i.message for i in res.prechecks])


def test_validator_parity(july_result):
    """A freshly generated schedule always passes the shared validator."""
    inp, res = july_result
    report = validate(inp, res.assignments)
    assert report.ok, [v.message for v in report.violations]


def test_everyone_hits_184_exactly(july_result):
    """No leave, top-ups available: every nurse lands exactly on the target."""
    _, res = july_result
    for nid, t in res.totals.items():
        assert t.owed == 0, (nid, t)
        assert t.total == 184


def test_paper_arithmetic_15_shifts_plus_4_hours(july_result):
    """The sheet's own note: '184 ore = 15 ture + 4 ore'. Non-kitchen nurses can
    only build hours from 12h blocks + one top-up, so each must have exactly
    15 twelve-hour shifts and one 4h top-up."""
    inp, res = july_result
    for n in inp.nurses:
        if n.is_head or n.kitchen_capable:
            continue
        mine = [a for a in res.assignments if a.nurse_id == n.id]
        twelves = [a for a in mine if a.duration == 12]
        tops = [a for a in mine if a.shift is Shift.TOPUP]
        assert len(twelves) == 15, n.name
        assert len(tops) == 1 and tops[0].duration == 4, n.name


def test_minimum_three_nights_each(july_result):
    inp, res = july_result
    for n in inp.nurses:
        if not n.is_head:
            assert res.totals[n.id].nights >= 3, n.name


def test_weekend_never_exceeds_normal(july_result):
    _, res = july_result
    for t in res.totals.values():
        assert t.worked_weekend <= t.worked_normal


def test_kitchen_every_day_and_head_every_working_day(july_result):
    inp, res = july_result
    kitchens = [a for a in res.assignments if a.shift is Shift.KITCHEN]
    assert len(kitchens) == 31
    assert len({a.date for a in kitchens}) == 31
    heads = [a for a in res.assignments if a.shift is Shift.HEAD]
    assert len(heads) == 23  # working days of July 2026
    assert all(not cal.is_offday(a.date, JULY_HOLIDAYS) for a in heads)
    head = next(n for n in inp.nurses if n.is_head)
    assert head.name == "Berbecar Anca"
    assert {a.nurse_id for a in heads} == {head.id}
    head_total = res.totals[head.id]
    assert head_total.total == 184 and head_total.owed == 0


def _codes(issues):
    return {i.code for i in issues}


def test_on_target_has_no_target_deviation_issue(july_result):
    """Base-target semantics: hitting the target exactly leaves no deviation
    warning or violation (months stand alone, nothing carries)."""
    inp, res = july_result
    report = validate(inp, res.assignments)
    assert "target_missed" not in _codes(report.warnings)
    assert "target_band" not in _codes(report.violations)


def test_deviation_within_band_is_a_warning(july_result):
    """Total is 184h for every non-leave nurse. Re-validating against a target
    6h away (inside the +/-12h band) flags a target_missed warning, not a
    violation, and does not block approval."""
    _, res = july_result
    inp = july_input(base_target=190)  # owed = +6 for everyone, within band
    report = validate(inp, res.assignments)
    assert "target_missed" in _codes(report.warnings)
    assert "target_band" not in _codes(report.violations)
    assert not any(v.code == "target_band" for v in report.violations)


def test_deviation_beyond_band_is_a_violation(july_result):
    """A target 16h away (outside the +/-12h band) becomes a target_band
    violation, which blocks approval."""
    _, res = july_result
    inp = july_input(base_target=200)  # owed = +16 for everyone, beyond band
    report = validate(inp, res.assignments)
    assert "target_band" in _codes(report.violations)
    assert not report.ok


def test_with_demo_leave_still_valid():
    holidays = JULY_HOLIDAYS
    inp = july_input(leave=demo_leave(2026, 7, holidays))
    res = solve(inp)
    assert res.ok, (res.status, res.diagnosis)
    report = validate(inp, res.assignments)
    assert report.ok, [v.message for v in report.violations]
    for nid, t in res.totals.items():
        assert t.owed == 0, (nid, t)


def test_two_month_chain_july_to_august():
    """July -> August: rest blocks cross the boundary, hours do not (amended rule)."""
    inp7 = july_input()
    res7 = solve(inp7)
    assert res7.ok

    # July 31 nights: full 12h accounted in July, split 5 normal + 7 weekend
    last = dt.date(2026, 7, 31)
    july31_nights = [a for a in res7.assignments if a.date == last and a.shift is Shift.NIGHT]
    assert len(july31_nights) >= 2
    for a in july31_nights:
        assert (a.normal_hours, a.weekend_hours) == (5, 7)

    # build August input from July's tail only (the rest rules cross the
    # boundary; hours do not - months stand alone, no balance carries)
    tail_days = {last, dt.date(2026, 7, 30)}
    prev_tail: dict[int, dict[dt.date, Shift]] = {}
    for a in res7.assignments:
        if a.date in tail_days:
            prev_tail.setdefault(a.nurse_id, {})[a.date] = a.shift

    inp8 = ScheduleInput(year=2026, month=8, base_target=168, nurses=DEMO_NURSES,
                         prev_tail=prev_tail,
                         config=Config(seed=7, max_time_s=60.0))
    res8 = solve(inp8)
    assert res8.ok, (res8.status, res8.diagnosis, [i.message for i in res8.prechecks])
    report = validate(inp8, res8.assignments)
    assert report.ok, [v.message for v in report.violations]

    # nurses who worked the July 31 night must be free on Aug 1-2
    blocked = {a.nurse_id for a in july31_nights}
    for a in res8.assignments:
        if a.nurse_id in blocked:
            assert a.date >= dt.date(2026, 8, 3), (a.nurse_id, a.date)

    # no hours from July's boundary night appear in August; targets hit exactly
    for nid, t in res8.totals.items():
        assert t.owed == 0, (nid, t)
        assert t.total == 168


def test_determinism_same_seed_same_schedule():
    cfg = dict(year=2026, month=7, base_target=184, nurses=DEMO_NURSES)
    a = solve(ScheduleInput(**cfg, config=Config(seed=123, max_time_s=60.0, workers=1)))
    b = solve(ScheduleInput(**cfg, config=Config(seed=123, max_time_s=60.0, workers=1)))
    assert a.ok and b.ok
    assert a.assignments == b.assignments


def test_unconstrained_month_uses_no_rest48_exceptions(july_result):
    """Plan 1: with no leave pressure the solver never bends the 48h rest, so
    the validator reports no rest48_exception warnings."""
    inp, res = july_result
    report = validate(inp, res.assignments)
    assert "rest48_exception" not in {w.code for w in report.warnings}


def test_section_crunch_solves_via_a_penalized_exception():
    """Plan 1: a section squeezed to 3 available nurses for three consecutive days
    is INFEASIBLE under the old hard 48h rule but now SOLVES by bending a soft rule
    - a warning, never a violation. With nights no longer tied to the section, the
    solver relieves it either by splitting the two nights 2+0 into the other
    section (coverage_night_section) or by bending the 48h rest (rest48_exception);
    both are cheaper than failing, so at least one appears."""
    holidays = JULY_HOLIDAYS
    workdays = [d for d in cal.month_dates(2026, 7) if not cal.is_offday(d, holidays)]
    # three consecutive CALENDAR days (Mon-Wed July 6-8): a weekend gap inside
    # the window would hand out free rest days and no exception would be needed
    crunch = workdays[3:6]
    assert [d.day for d in crunch] == [6, 7, 8]
    # BIG workers are ids 1..7; put four of them on Co across the crunch so only
    # three BIG nurses remain to field the section's Z each of the three days.
    leave = {nid: set(crunch) for nid in (1, 2, 3, 4)}
    inp = july_input(leave=leave)
    res = solve(inp)
    assert res.ok, (res.status, res.diagnosis, [i.message for i in res.prechecks])
    report = validate(inp, res.assignments)
    assert report.ok, [v.message for v in report.violations]
    codes = {w.code for w in report.warnings}
    assert codes & {"rest48_exception", "coverage_night_section"}, codes


def test_validator_flags_missing_section_day_coverage():
    """Change 1: a day with two BIG day nurses and no SMALL day nurse breaks the
    HARD per-section rule -> coverage_day_section violation, and per_day carries
    the split counts."""
    from app.solver.domain import Assignment
    day = dt.date(2026, 7, 6)  # a Monday working day
    inp = july_input()
    # two day nurses, both BIG (ids 1 and 3 are BIG in DEMO_NURSES)
    assignments = [
        Assignment(1, day, Shift.DAY, 12, 12, 0),
        Assignment(3, day, Shift.DAY, 12, 12, 0),
    ]
    report = validate(inp, assignments)
    codes = {v.code for v in report.violations}
    assert "coverage_day_section" in codes
    pd = report.per_day[day]
    assert pd["Z_big"] == 2 and pd["Z_small"] == 0
