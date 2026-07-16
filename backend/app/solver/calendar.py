"""Day types, Romanian public holidays and hour-bucket contributions.

The single bucket rule (plan section 4.4): every worked hour counts toward the
bucket - normal or weekend/holiday - of the calendar day in which the hour
actually falls. A night shift contributes 5h by its start day's type and 7h by
the following day's type. Amended month-boundary rule: the whole shift is
accounted in the month it starts in, but the morning hours of a last-day night
are still *classified* by the type of the next month's day 1.
"""
from __future__ import annotations

import datetime as dt

from .domain import FIXED_DURATION, Shift

ONE_DAY = dt.timedelta(days=1)


def orthodox_easter(year: int) -> dt.date:
    """Orthodox Easter Sunday in the Gregorian calendar (valid 1900-2099)."""
    a = year % 4
    b = year % 7
    c = year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month = (d + e + 114) // 31
    day = (d + e + 114) % 31 + 1
    return dt.date(year, month, day) + dt.timedelta(days=13)  # Julian -> Gregorian


def romanian_holidays_named(year: int) -> dict[dt.date, str]:
    """Romanian legal public holidays with names (as of 2024 legislation)."""
    easter = orthodox_easter(year)
    return {
        dt.date(year, 1, 1): "Anul Nou",
        dt.date(year, 1, 2): "A doua zi de Anul Nou",
        dt.date(year, 1, 6): "Boboteaza",
        dt.date(year, 1, 7): "Sfantul Ioan Botezatorul",
        dt.date(year, 1, 24): "Unirea Principatelor Romane",
        dt.date(year, 5, 1): "Ziua Muncii",
        dt.date(year, 6, 1): "Ziua Copilului",
        dt.date(year, 8, 15): "Adormirea Maicii Domnului",
        dt.date(year, 11, 30): "Sfantul Andrei",
        dt.date(year, 12, 1): "Ziua Nationala a Romaniei",
        dt.date(year, 12, 25): "Craciunul",
        dt.date(year, 12, 26): "A doua zi de Craciun",
        easter - dt.timedelta(days=2): "Vinerea Mare",
        easter: "Pastele",
        easter + dt.timedelta(days=1): "A doua zi de Paste",
        easter + dt.timedelta(days=49): "Rusaliile",
        easter + dt.timedelta(days=50): "A doua zi de Rusalii",
    }


def romanian_holidays(year: int) -> set[dt.date]:
    """Romanian legal public holidays (as of 2024 legislation, incl. Jan 6/7)."""
    return set(romanian_holidays_named(year))


def default_holidays(year: int, month: int) -> set[dt.date]:
    """Holidays covering the month plus day 1 of the next month (night split)."""
    days = romanian_holidays(year)
    if month == 12:
        days |= romanian_holidays(year + 1)
    return days


def days_in_month(year: int, month: int) -> int:
    first = dt.date(year, month, 1)
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - first).days


def month_dates(year: int, month: int) -> list[dt.date]:
    return [dt.date(year, month, d) for d in range(1, days_in_month(year, month) + 1)]


def is_offday(day: dt.date, holidays: set[dt.date]) -> bool:
    """True for Saturday, Sunday or a public holiday (the weekend/holiday bucket)."""
    return day.weekday() >= 5 or day in holidays


def working_days(year: int, month: int, holidays: set[dt.date]) -> int:
    return sum(1 for d in month_dates(year, month) if not is_offday(d, holidays))


def bridged_offdays(year: int, month: int, holidays: set[dt.date],
                    leave: set[dt.date]) -> set[dt.date]:
    """Off days the nurse's own leave brackets on both sides, so she is away
    across them and cannot be worked.

    Co is a Monday-Friday thing, so a nurse on leave on the working day before a
    weekend and again on the working day after it is on vacation through the
    weekend. Those days are blocked but earn nothing: weekends are not working
    days, so they carry no 8h credit and do not count as leave days.

    Generalised over runs of consecutive off days rather than Saturday/Sunday, so
    a public holiday cannot break a bridge: with Friday a holiday the run is
    Fri-Sun and the flanking working days are Thursday and Monday.

    A run touching the month edge is never bridged - its flanking working day
    lies in the neighbouring month, whose leave this schedule does not know.
    """
    dates = month_dates(year, month)
    blocked: set[dt.date] = set()
    i = 0
    while i < len(dates):
        if not is_offday(dates[i], holidays):
            i += 1
            continue
        j = i
        while j < len(dates) and is_offday(dates[j], holidays):
            j += 1
        # dates[i:j] is a maximal off-day run, so dates[i-1] and dates[j] - when
        # they exist - are by construction the flanking working days.
        if i > 0 and j < len(dates) and dates[i - 1] in leave and dates[j] in leave:
            blocked |= set(dates[i:j])
        i = j
    return blocked


def night_split(start: dt.date, holidays: set[dt.date]) -> tuple[int, int]:
    """(normal, weekend/holiday) hours of the 12h night starting 19:00 on `start`."""
    nxt = start + ONE_DAY
    normal = weekend = 0
    if is_offday(start, holidays):
        weekend += 5
    else:
        normal += 5
    if is_offday(nxt, holidays):
        weekend += 7
    else:
        normal += 7
    return normal, weekend


def shift_hours(day: dt.date, shift: Shift, duration: int, holidays: set[dt.date]) -> tuple[int, int]:
    """(normal, weekend/holiday) contribution of one assignment. Whole shift is
    accounted in the month of `day`, per the amended boundary rule."""
    if shift is Shift.NIGHT:
        return night_split(day, holidays)
    if is_offday(day, holidays):
        return 0, duration
    return duration, 0


def duration_of(shift: Shift, topup_hours: int = 0) -> int:
    if shift is Shift.TOPUP:
        return topup_hours
    return FIXED_DURATION[shift]
