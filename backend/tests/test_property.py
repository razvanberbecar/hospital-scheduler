"""Property test (plan section 10): whatever the leave pattern, a generated
schedule never violates a hard constraint."""

from hypothesis import given, settings, strategies as st

from app.solver import calendar as cal
from app.solver.cli import DEMO_NURSES
from app.solver.domain import Config, ScheduleInput
from app.solver.model import solve
from app.solver.prechecks import has_errors, run_prechecks
from app.solver.validator import validate

HOLIDAYS = cal.default_holidays(2026, 7)
WORKDAYS = [d for d in cal.month_dates(2026, 7) if not cal.is_offday(d, HOLIDAYS)]

leave_streak = st.tuples(st.integers(0, 15), st.integers(0, 8))  # (start, length)


@settings(max_examples=4, deadline=None)
@given(s1=leave_streak, s2=leave_streak)
def test_random_leave_never_breaks_hard_rules(s1, s2):
    leave = {
        4: set(WORKDAYS[s1[0]:s1[0] + s1[1]]),    # BIG, not kitchen-capable
        10: set(WORKDAYS[s2[0]:s2[0] + s2[1]]),   # SMALL, not kitchen-capable
    }
    inp = ScheduleInput(2026, 7, 184, DEMO_NURSES, leave=leave,
                        config=Config(seed=1, max_time_s=20.0))
    if has_errors(run_prechecks(inp)):
        return  # legitimately rejected before solving
    res = solve(inp)
    if not res.ok:
        return  # infeasible is allowed; wrong output is not
    report = validate(inp, res.assignments)
    assert report.ok, [v.message for v in report.violations]
