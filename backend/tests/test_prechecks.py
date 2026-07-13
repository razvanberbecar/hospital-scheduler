import datetime as dt

from app.solver import calendar as cal
from app.solver.cli import DEMO_NURSES
from app.solver.domain import BIG, SMALL, Nurse, ScheduleInput
from app.solver.prechecks import has_errors, per_nurse_night_minimum, run_prechecks

HOLIDAYS = cal.default_holidays(2026, 7)
WORKDAYS = [d for d in cal.month_dates(2026, 7) if not cal.is_offday(d, HOLIDAYS)]


def codes(issues, level=None):
    return {i.code for i in issues if level is None or i.level == level}


def test_demo_roster_is_clean():
    inp = ScheduleInput(2026, 7, 184, DEMO_NURSES)
    issues = run_prechecks(inp)
    assert not has_errors(issues), [i.message for i in issues if i.level == "error"]


def test_roster_too_small():
    nurses = [Nurse(1, "A", BIG, kitchen_capable=True), Nurse(2, "B", SMALL)]
    issues = run_prechecks(ScheduleInput(2026, 7, 184, nurses))
    assert "roster_size" in codes(issues, "error")


def test_no_kitchen_capable_nurse_is_an_error():
    nurses = [Nurse(i, f"N{i}", BIG if i % 2 else SMALL) for i in range(1, 11)]
    issues = run_prechecks(ScheduleInput(2026, 7, 184, nurses))
    assert "kitchen_pool" in codes(issues, "error")


def test_leave_on_weekend_rejected():
    inp = ScheduleInput(2026, 7, 184, DEMO_NURSES, leave={4: {dt.date(2026, 7, 4)}})  # Saturday
    assert "leave_on_offday" in codes(run_prechecks(inp), "error")


def test_target_not_multiple_of_4_rejected():
    issues = run_prechecks(ScheduleInput(2026, 7, 183, DEMO_NURSES))
    assert "target_granularity" in codes(issues, "error")


def test_heavy_leave_relaxes_night_minimum_with_warning():
    """With the +/-12h band, 3 nights (36h) fit while required worked >= 24h,
    i.e. up to 20 leave days at target 184. From 21 leave days on, the minimum
    relaxes and gets flagged (decided: flag, don't block)."""
    leave = {4: set(WORKDAYS[:22])}  # 22 leave days -> 8h required worked
    inp = ScheduleInput(2026, 7, 184, DEMO_NURSES, leave=leave)
    minimum, relaxed = per_nurse_night_minimum(inp, HOLIDAYS)[4]
    assert relaxed and minimum < 3
    assert "nights_relaxed" in codes(run_prechecks(inp), "warning")


def test_twenty_leave_days_still_allows_three_nights():
    leave = {4: set(WORKDAYS[:20])}  # 24h required; 3 nights = 36h = band edge
    minimum, relaxed = per_nurse_night_minimum(
        ScheduleInput(2026, 7, 184, DEMO_NURSES, leave=leave), HOLIDAYS)[4]
    assert (minimum, relaxed) == (3, False)


def test_moderate_leave_keeps_three_nights():
    leave = {4: set(WORKDAYS[:10])}  # 104h still to work -> 3 nights fit
    minimum, relaxed = per_nurse_night_minimum(
        ScheduleInput(2026, 7, 184, DEMO_NURSES, leave=leave), HOLIDAYS)[4]
    assert (minimum, relaxed) == (3, False)


def test_supply_bounds():
    few = [n for n in DEMO_NURSES if n.id <= 8]  # head + 7 workers: 7*184=1288 < 56*31=1736
    issues = run_prechecks(ScheduleInput(2026, 7, 184, few))
    assert "supply_low" in codes(issues, "error")


def test_whole_section_on_leave_one_day_is_an_error():
    """Change 1: if every nurse of a section is on leave on some working day,
    that day cannot field the section's required Z/N pair -> precheck error."""
    small = [n for n in DEMO_NURSES if n.section == SMALL and not n.is_head]
    day = WORKDAYS[10]
    leave = {n.id: {day} for n in small}
    inp = ScheduleInput(2026, 7, 184, DEMO_NURSES, leave=leave)
    issues = run_prechecks(inp)
    # The head belongs to SMALL, so the section is NOT fully blocked unless she
    # is on leave too; add her to make the whole section unavailable.
    assert "section_unavailable" not in codes(issues, "error")
    head = next(n for n in DEMO_NURSES if n.is_head)
    leave[head.id] = {day}
    inp2 = ScheduleInput(2026, 7, 184, DEMO_NURSES, leave=leave)
    assert "section_unavailable" in codes(run_prechecks(inp2), "error")
