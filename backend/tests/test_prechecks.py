import datetime as dt

from app.solver import calendar as cal
from app.solver.cli import DEMO_NURSES
from app.solver.domain import (BIG, SMALL, STAFF_AIDE, Assignment, Nurse,
                                ScheduleInput, Shift)
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


def test_three_nights_survive_the_hours_band_edge():
    """The hours edge: at 24h required worked, 3 nights (36h) still sit inside
    the +/-12h band, so the minimum must not relax.

    Reached with 10 Co days against a 104h target rather than 20 against 184h:
    20 of July's 23 working days cannot avoid bridging a weekend (four weekends
    need four distinct free flanking days and only three days are left over), so
    that shape measures day availability instead of the hours it means to guard.
    """
    leave = {4: set(WORKDAYS[:10])}  # 104 - 80 = 24h required
    inp = ScheduleInput(2026, 7, 104, DEMO_NURSES, leave=leave, holidays=HOLIDAYS)
    assert inp.effective_target(4) - 8 * 10 == 24
    minimum, relaxed = per_nurse_night_minimum(inp, HOLIDAYS)[4]
    assert (minimum, relaxed) == (3, False)


def test_consecutive_leave_relaxes_nights_via_the_bridged_weekends():
    """Co on every working day of July 1-28 is a month-long holiday: she is also
    away across the four weekends inside it, leaving only the 29th-31st. Three
    nights no longer fit on days, so the minimum relaxes and is flagged rather
    than blocking (decided: flag, don't block)."""
    leave = {4: set(WORKDAYS[:20])}
    inp = ScheduleInput(2026, 7, 184, DEMO_NURSES, leave=leave, holidays=HOLIDAYS)
    assert len(inp.blocked_of(4, HOLIDAYS)) == 8  # four bridged weekends
    minimum, relaxed = per_nurse_night_minimum(inp, HOLIDAYS)[4]
    assert relaxed and minimum < 3
    assert "nights_relaxed" in codes(run_prechecks(inp), "warning")


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


# --- leave-crunch prechecks (plan 4): a section's Z cannot follow a Z ----------
# July 8/9/10 2026 are consecutive Wed/Thu/Fri working days. BIG workers are
# ids 1..7, SMALL workers 8/9/10/12/13/14, and the head (id 11) is SMALL.
D8, D9, D10 = dt.date(2026, 7, 8), dt.date(2026, 7, 9), dt.date(2026, 7, 10)


def test_single_lone_available_day_is_not_a_crunch():
    """One available nurse on a single day still fields the section's Z; the two
    nights may both come from the other section (2+0), so it is not a crunch."""
    leave = {nid: {D8} for nid in (1, 2, 3, 4, 5, 6)}  # 6 of 7 BIG on Co, one day
    inp = ScheduleInput(2026, 7, 184, DEMO_NURSES, leave=leave)
    assert "section_leave_crunch" not in codes(run_prechecks(inp), "error")


def test_two_available_over_two_days_is_not_a_crunch():
    """Two nurses cover two consecutive day shifts (they alternate, since a Z
    cannot follow a Z); nights are pool-wide now, so this no longer blocks."""
    leave = {nid: {D8, D9} for nid in (1, 2, 3, 4, 5)}  # 5 of 7 BIG -> 2 available
    issues = run_prechecks(ScheduleInput(2026, 7, 184, DEMO_NURSES, leave=leave))
    assert "section_leave_crunch" not in codes(issues, "error")


def test_lone_nurse_over_two_consecutive_days_is_a_crunch_error():
    """One available nurse cannot cover two consecutive day shifts - a Z cannot
    follow a Z, so the section's Z on the second day is unfillable."""
    leave = {nid: {D8, D9} for nid in (1, 2, 3, 4, 5, 6)}  # 6 of 7 BIG -> 1 available both days
    issues = run_prechecks(ScheduleInput(2026, 7, 184, DEMO_NURSES, leave=leave))
    assert "section_leave_crunch" in codes(issues, "error")


def test_locked_head_day_rescues_a_consecutive_day_crunch():
    """A locked head Z is +1 available for her section's day shift, so it breaks
    an otherwise lone-nurse consecutive-day crunch (lock-aware)."""
    leave = {nid: {D8, D9} for nid in (8, 9, 10, 12, 13)}  # 5 of 6 SMALL workers -> 1 available both days
    inp = ScheduleInput(2026, 7, 184, DEMO_NURSES, leave=leave)
    assert "section_leave_crunch" in codes(run_prechecks(inp), "error")
    locked = ScheduleInput(2026, 7, 184, DEMO_NURSES, leave=leave,
                           locks=[Assignment(11, D8, Shift.DAY, 12, 12, 0)])  # head Z on D8
    assert "section_leave_crunch" not in codes(run_prechecks(locked), "error")


def test_infirmiere_leave_crunch_is_an_error():
    """Infirmiere need 3 distinct people every day (1 Z + 1 N + 1 x 8h)."""
    aides = [Nurse(i, f"Inf{i}", BIG) for i in range(1, 7)]  # section ignored for aides
    leave = {i: {D8} for i in (1, 2, 3, 4)}  # 4 of 6 on Co -> 2 available
    inp = ScheduleInput(2026, 7, 184, aides, staff_type=STAFF_AIDE, leave=leave)
    assert "infirmiere_leave_crunch" in codes(run_prechecks(inp), "error")
