"""Pairwise rest-rule matrix against the shared validator (plan section 4.7)."""
import datetime as dt

import pytest

from app.solver import calendar as cal
from app.solver.domain import (BIG, SMALL, STAFF_AIDE, STAFF_NURSE, Assignment,
                               Nurse, ScheduleInput, Shift)

HOLIDAYS = cal.default_holidays(2026, 7)
NURSES = [
    Nurse(1, "A", BIG, kitchen_capable=True),
    Nurse(2, "B", SMALL, kitchen_capable=True),
]


def mk(nid, day, shift, duration=None):
    dur = duration if duration is not None else cal.duration_of(shift)
    nh, wh = cal.shift_hours(day, shift, dur, HOLIDAYS)
    return Assignment(nid, day, shift, dur, nh, wh)


def rest_codes(assignments, prev_tail=None, staff_type=STAFF_NURSE):
    from app.solver.validator import validate
    inp = ScheduleInput(2026, 7, 184, NURSES, staff_type=staff_type,
                        prev_tail=prev_tail or {})
    report = validate(inp, assignments)
    return {v.code for v in report.violations if v.code.startswith("rest_")}


def rest_warnings(assignments, prev_tail=None, staff_type=STAFF_NURSE):
    from app.solver.validator import validate
    inp = ScheduleInput(2026, 7, 184, NURSES, staff_type=staff_type,
                        prev_tail=prev_tail or {})
    report = validate(inp, assignments)
    return {w.code for w in report.warnings if w.code.startswith("rest")}


D6 = dt.date(2026, 7, 6)   # Monday
D7 = dt.date(2026, 7, 7)
D8 = dt.date(2026, 7, 8)
D9 = dt.date(2026, 7, 9)


@pytest.mark.parametrize("second,expected", [
    (Shift.NIGHT, set()),                       # Z then N next day: exactly 24h, allowed
    (Shift.DAY, {"rest_after_day"}),
    (Shift.KITCHEN, {"rest_after_day"}),
    (Shift.TOPUP, {"rest_after_day"}),
])
def test_after_day_shift(second, expected):
    duration = 4 if second is Shift.TOPUP else None
    codes = rest_codes([mk(1, D6, Shift.DAY), mk(1, D7, second, duration)])
    assert codes == expected


@pytest.mark.parametrize("gap,shift,expected", [
    # First day after a night (gap 1) is always a hard violation (24h floor).
    # The second day (gap 2) is now freely workable for both staff types (flat
    # 16h rule, decided 2026-10-18) -> no violation (and no warning either).
    (1, Shift.DAY, {"rest_after_night"}),
    (1, Shift.NIGHT, {"rest_after_night"}),
    (2, Shift.DAY, set()),
    (2, Shift.KITCHEN, set()),
    (3, Shift.DAY, set()),
    (3, Shift.NIGHT, set()),
])
def test_after_night_shift(gap, shift, expected):
    assignments = [mk(1, D6, Shift.NIGHT), mk(1, D6 + dt.timedelta(days=gap), shift)]
    assert rest_codes(assignments) == expected


@pytest.mark.parametrize("staff_type", [STAFF_NURSE, STAFF_AIDE])
@pytest.mark.parametrize("shift", [Shift.DAY, Shift.KITCHEN])
def test_second_day_after_night_is_free_for_both_staff(shift, staff_type):
    """Flat 16h rule (both staff types, decided 2026-10-18): with 12h shifts only
    the hard 24h floor remains, so the second day after a night is freely workable
    - no violation and no warning, whatever the staff type."""
    assignments = [mk(1, D6, Shift.NIGHT), mk(1, D8, shift)]  # D8 = two days later
    assert rest_codes(assignments, staff_type=staff_type) == set()
    assert rest_warnings(assignments, staff_type=staff_type) == set()


def test_after_eight_hour_shift_next_day_free():
    assert rest_codes([mk(1, D6, Shift.KITCHEN), mk(1, D7, Shift.DAY)]) == set()
    assert rest_codes([mk(1, D6, Shift.KITCHEN), mk(1, D7, Shift.KITCHEN)]) == set()


@pytest.mark.parametrize("staff_type", [STAFF_NURSE, STAFF_AIDE])
def test_prev_month_tail_night_blocks_first_day_only(staff_type):
    """A night on the last day of the previous month: day 1 is always a hard
    violation (24h floor). The second day (day 2) is freely workable for both
    staff types under the flat 16h rule - no violation and no warning."""
    tail = {1: {dt.date(2026, 6, 30): Shift.NIGHT}}
    d1, d2, d3 = dt.date(2026, 7, 1), dt.date(2026, 7, 2), dt.date(2026, 7, 3)
    assert rest_codes([mk(1, d1, Shift.DAY)], tail, staff_type) == {"rest_after_night"}
    assert rest_codes([mk(1, d2, Shift.DAY)], tail, staff_type) == set()
    assert rest_warnings([mk(1, d2, Shift.DAY)], tail, staff_type) == set()
    assert rest_codes([mk(1, d3, Shift.DAY)], tail, staff_type) == set()


def test_prev_month_tail_day_allows_only_night():
    tail = {1: {dt.date(2026, 6, 30): Shift.DAY}}
    d1 = dt.date(2026, 7, 1)
    assert rest_codes([mk(1, d1, Shift.DAY)], tail) == {"rest_after_day"}
    assert rest_codes([mk(1, d1, Shift.NIGHT)], tail) == set()
