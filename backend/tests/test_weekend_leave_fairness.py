"""Co lowers the 100%-bonus (weekend/holiday) hours - the "Ore 100%" column -
but only a little.

Decided 2026-09-17: the 100% hours are shared in proportion to how much of the
month each nurse is actually available (Config.w_weekend_fair), so a nurse on Co
carries fewer than a full-month colleague, graded by how much leave she took, and
NOT collapsed to the 24h floor. A leave-free month is unaffected (all shares
equal, i.e. spread evenly).
"""
from __future__ import annotations

from app.solver import calendar as cal
from app.solver.domain import STAFF_AIDE, Config, Nurse, ScheduleInput
from app.solver.model import solve
from app.solver.validator import validate

H = cal.default_holidays(2026, 10)
WD = [d for d in cal.month_dates(2026, 10) if not cal.is_offday(d, H)]  # 22 weekdays

# ids 1,3,6,7 on Co (5, 8, 9, 7 days, echoing the real October sheet); 2,4,5 work
# the whole month.
LEAVE = {1: set(WD[0:5]), 3: set(WD[5:13]), 6: set(WD[13:22]), 7: set(WD[9:16])}
CO_IDS, NOCO_IDS = [1, 3, 6, 7], [2, 4, 5]


def aides(n: int) -> list[Nurse]:
    return [Nurse(i, f"Inf{i}", section="") for i in range(1, n + 1)]


def solve_oct(leave):
    cfg = Config(seed=7, max_time_s=45.0, workers=8)  # default w_weekend_fair
    inp = ScheduleInput(2026, 10, 176, aides(7), staff_type=STAFF_AIDE,
                        leave=leave, holidays=H, config=cfg)
    res = solve(inp)
    assert res.ok, (res.status, res.diagnosis, [i.message for i in res.prechecks])
    assert validate(inp, res.assignments).ok
    return {i: res.totals[i].worked_weekend for i in range(1, 8)}


def test_leave_lowers_weekend_hours_but_stays_moderate():
    w = solve_oct(LEAVE)
    co = [w[i] for i in CO_IDS]
    noco = [w[i] for i in NOCO_IDS]
    # Co nurses carry fewer 100% hours than full-month nurses...
    assert max(co) <= min(noco), (co, noco)
    # ...but are not collapsed to the 24h floor - the gap is moderate, not the old
    # bang-bang floor-vs-rest (which was 24 vs 66, a gap of 42).
    assert min(co) >= 28, co
    assert max(noco) - min(co) <= 32, (noco, co)


def test_more_leave_is_never_more_weekend_hours():
    w = solve_oct(LEAVE)
    # id 6 has the most Co (9 days), id 1 the least among leave-takers (5).
    assert w[6] <= w[1], (w[6], w[1])


def test_leave_free_month_shares_weekend_evenly():
    w = solve_oct({})
    vals = list(w.values())
    assert max(vals) - min(vals) <= 14, vals  # equal shares -> spread evenly
