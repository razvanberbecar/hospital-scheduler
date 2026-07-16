import datetime as dt

from app.solver import calendar as cal
from app.solver.domain import Shift


def d(y, m, day):
    return dt.date(y, m, day)


def test_orthodox_easter_known_years():
    assert cal.orthodox_easter(2025) == d(2025, 4, 20)
    assert cal.orthodox_easter(2026) == d(2026, 4, 12)


def test_romanian_holidays_2026():
    hs = cal.romanian_holidays(2026)
    assert d(2026, 4, 10) in hs      # Vinerea Mare
    assert d(2026, 4, 13) in hs      # a doua zi de Paste
    assert d(2026, 5, 31) in hs      # Rusalii
    assert d(2026, 6, 1) in hs       # a doua zi de Rusalii == Ziua Copilului
    assert d(2026, 1, 6) in hs and d(2026, 1, 7) in hs
    assert d(2026, 7, 15) not in hs  # July is clean


def test_working_days_july_august_2026():
    """The paper GRAFIC month: 184 ore = 23 wd x 8; August: 21 wd (Aug 15 falls on Saturday)."""
    assert cal.working_days(2026, 7, cal.default_holidays(2026, 7)) == 23
    assert cal.working_days(2026, 8, cal.default_holidays(2026, 8)) == 21


def test_night_splits():
    hs = cal.default_holidays(2026, 7)
    assert cal.night_split(d(2026, 7, 7), hs) == (12, 0)   # Tue -> Wed
    assert cal.night_split(d(2026, 7, 3), hs) == (5, 7)    # Fri -> Sat
    assert cal.night_split(d(2026, 7, 4), hs) == (0, 12)   # Sat -> Sun
    assert cal.night_split(d(2026, 7, 5), hs) == (7, 5)    # Sun -> Mon (normal 7 in the morning)


def test_night_split_holiday_adjacent():
    hs = cal.default_holidays(2026, 4)
    # Thursday Apr 9 -> Friday Apr 10 = Vinerea Mare (holiday): 5 normal + 7 holiday
    assert cal.night_split(d(2026, 4, 9), hs) == (5, 7)


def test_month_boundary_night_counts_fully_in_start_month():
    """Amended rule: July 31 (Fri) night = 5 normal + 7 weekend, ALL in July."""
    hs = cal.default_holidays(2026, 7)
    normal, weekend = cal.shift_hours(d(2026, 7, 31), Shift.NIGHT, 12, hs)
    assert (normal, weekend) == (5, 7)
    assert normal + weekend == 12  # nothing spills into August


def test_bridged_offdays_blocks_a_weekend_between_two_co_days():
    """Co on Friday and again on Monday: she is away across the weekend."""
    hs = cal.default_holidays(2026, 7)
    # July 2026: 3rd Fri, 4th-5th weekend, 6th Mon.
    assert cal.bridged_offdays(2026, 7, hs, {d(2026, 7, 3), d(2026, 7, 6)}) == {
        d(2026, 7, 4), d(2026, 7, 5)}


def test_bridged_offdays_needs_co_on_both_sides():
    hs = cal.default_holidays(2026, 7)
    assert cal.bridged_offdays(2026, 7, hs, {d(2026, 7, 3)}) == set()   # Friday only
    assert cal.bridged_offdays(2026, 7, hs, {d(2026, 7, 6)}) == set()   # Monday only
    assert cal.bridged_offdays(2026, 7, hs, set()) == set()


def test_bridged_offdays_spans_a_holiday_flanked_weekend():
    """The flanking days are the WORKING days either side, so a public holiday
    cannot break a bridge: Christmas 2026 falls on Friday, making Dec 25-27 one
    off-day run between Thursday the 24th and Monday the 28th."""
    hs = cal.default_holidays(2026, 12)
    assert cal.bridged_offdays(2026, 12, hs, {d(2026, 12, 24), d(2026, 12, 28)}) == {
        d(2026, 12, 25), d(2026, 12, 26), d(2026, 12, 27)}
    # Co on the holiday itself is not Co on a working day -> no bridge.
    assert cal.bridged_offdays(2026, 12, hs, {d(2026, 12, 25), d(2026, 12, 28)}) == set()


def test_bridged_offdays_ignores_runs_at_the_month_edge():
    """A run touching the edge has a flanking day in the neighbouring month,
    whose leave this schedule does not know - never bridged."""
    hs = cal.default_holidays(2026, 8)
    # August 2026 starts Sat 1st / Sun 2nd: no working day before the run.
    assert d(2026, 8, 1).weekday() == 5
    assert cal.bridged_offdays(2026, 8, hs, {d(2026, 8, 3), d(2026, 8, 31)}) == set()


def test_bridged_offdays_handles_several_bridges_in_one_month():
    hs = cal.default_holidays(2026, 7)
    leave = {d(2026, 7, 3), d(2026, 7, 6), d(2026, 7, 10), d(2026, 7, 13)}
    assert cal.bridged_offdays(2026, 7, hs, leave) == {
        d(2026, 7, 4), d(2026, 7, 5), d(2026, 7, 11), d(2026, 7, 12)}


def test_bucket_for_simple_shifts():
    hs = cal.default_holidays(2026, 7)
    assert cal.shift_hours(d(2026, 7, 6), Shift.DAY, 12, hs) == (12, 0)     # Monday
    assert cal.shift_hours(d(2026, 7, 4), Shift.DAY, 12, hs) == (0, 12)     # Saturday
    assert cal.shift_hours(d(2026, 7, 4), Shift.KITCHEN, 8, hs) == (0, 8)   # kitchen runs weekends
    assert cal.shift_hours(d(2026, 7, 6), Shift.TOPUP, 4, hs) == (4, 0)
