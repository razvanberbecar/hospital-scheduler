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


def test_bucket_for_simple_shifts():
    hs = cal.default_holidays(2026, 7)
    assert cal.shift_hours(d(2026, 7, 6), Shift.DAY, 12, hs) == (12, 0)     # Monday
    assert cal.shift_hours(d(2026, 7, 4), Shift.DAY, 12, hs) == (0, 12)     # Saturday
    assert cal.shift_hours(d(2026, 7, 4), Shift.KITCHEN, 8, hs) == (0, 8)   # kitchen runs weekends
    assert cal.shift_hours(d(2026, 7, 6), Shift.TOPUP, 4, hs) == (4, 0)
