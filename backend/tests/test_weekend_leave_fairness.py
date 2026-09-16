"""Co reduces the 100%-bonus (weekend/holiday) hours - the "Ore 100%" column.

Decided 2026-09-16: staff who were on leave should carry fewer weekend/holiday
hours than colleagues who worked the whole month. Config.w_weekend_leave charges
each nurse's weekend hours in proportion to her Co days, so the solver moves those
bonus hours onto full-month staff. With the weight at 0 the solver just equalises
them (the old behaviour), which is why a leave-free month is unaffected.
"""
from __future__ import annotations

from app.solver import calendar as cal
from app.solver.domain import STAFF_AIDE, Config, Nurse, ScheduleInput
from app.solver.model import solve
from app.solver.validator import validate

H = cal.default_holidays(2026, 10)
WD = [d for d in cal.month_dates(2026, 10) if not cal.is_offday(d, H)]  # 22 weekdays


def aides(n: int) -> list[Nurse]:
    return [Nurse(i, f"Inf{i}", section="") for i in range(1, n + 1)]


def solve_oct(w_weekend_leave: int):
    # id 1 takes a long Co block; ids 2..7 work the whole month. October norm is
    # 176 (22 x 8), so the target carries no overtime - this isolates the weekend
    # split. workers=1 keeps the solve reproducible (seed alone is not enough).
    cfg = Config(seed=7, max_time_s=45.0, workers=1, w_weekend_leave=w_weekend_leave)
    inp = ScheduleInput(2026, 10, 176, aides(7), staff_type=STAFF_AIDE,
                        leave={1: set(WD[:12])}, holidays=H, config=cfg)
    res = solve(inp)
    assert res.ok, (res.status, res.diagnosis, [i.message for i in res.prechecks])
    assert validate(inp, res.assignments).ok
    return res


def test_leave_nurse_carries_the_fewest_weekend_hours():
    res = solve_oct(20)  # rule on
    lead = res.totals[1].worked_weekend            # 12 Co days
    others = [res.totals[i].worked_weekend for i in range(2, 8)]  # full month
    assert lead < min(others), (lead, others)
    # the rule only redistributes weekend hours; every target is still met.
    for i in range(1, 8):
        assert abs(res.totals[i].owed) <= 20  # cfg.band default is 12; generous here


def test_off_switch_restores_even_weekend_hours():
    # With the weight at 0 the leave nurse is no longer singled out: the solver
    # equalises weekend hours, so she is no longer strictly the lowest.
    off = solve_oct(0)
    on = solve_oct(20)
    assert on.totals[1].worked_weekend < off.totals[1].worked_weekend
