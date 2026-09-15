"""Co (concediu) reduces overtime, proportionally, for both staff types.

Decided 2026-09-15: a nurse who takes leave should not carry the same overtime
as one who worked the whole month. The overtime portion of the target - the part
of base_target above the month's working-days norm (working_days x 8) - is scaled
down in proportion to a nurse's Co days in ScheduleInput.effective_target, so
each Co day sheds a slice of it and a full month of Co removes it. A nurse with
no Co keeps the full base_target. When the target is at or below the norm there is
no overtime to scale and everyone keeps base_target - which is why the whole
existing golden-month suite (July 2026, base_target 184 == norm) is unaffected.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.solver import calendar as cal
from app.solver.cli import DEMO_NURSES
from app.solver.domain import STAFF_AIDE, Config, Nurse, ScheduleInput
from app.solver.model import solve
from app.solver.validator import validate

# August 2026: 21 working days -> norm 168h. A base target of 184h therefore
# carries 16h of overtime, the slice Co is meant to scale down.
AUG_HOLIDAYS = cal.default_holidays(2026, 8)
AUG_WORKDAYS = [d for d in cal.month_dates(2026, 8) if not cal.is_offday(d, AUG_HOLIDAYS)]
AUG_NORM = len(AUG_WORKDAYS) * 8  # 168


def co(n: int) -> set[dt.date]:
    """The first n August working days as Co - always a valid Co placement."""
    return set(AUG_WORKDAYS[:n])


def aug_input(leave, base_target=184, **kw):
    return ScheduleInput(2026, 8, base_target, DEMO_NURSES, leave=leave,
                         holidays=AUG_HOLIDAYS, **kw)


# --- the arithmetic (fast, no solver) -------------------------------------

def test_no_leave_keeps_full_base_target():
    inp = aug_input(leave={1: co(10)})
    assert inp.effective_target(2) == 184  # id 2 has no Co


def test_overtime_scaled_down_by_leave():
    # 16h of overtime, spread over 21 working days: each Co day sheds 16/21 h.
    inp = aug_input(leave={1: co(5), 2: co(10)})
    assert inp.effective_target(1) == 184 - round(16 * 5 / 21)   # 180
    assert inp.effective_target(2) == 184 - round(16 * 10 / 21)  # 176


def test_full_month_of_leave_removes_all_overtime():
    inp = aug_input(leave={1: co(len(AUG_WORKDAYS))})
    assert inp.effective_target(1) == AUG_NORM  # 168, no overtime left


def test_more_leave_never_means_more_overtime():
    inp = aug_input(leave={1: co(3), 2: co(7), 3: co(14)})
    a, b, c = (inp.effective_target(i) for i in (1, 2, 3))
    assert 184 >= a >= b >= c  # monotone: more Co -> lower (or equal) target


def test_leave_nurse_target_never_exceeds_a_worked_nurse():
    inp = aug_input(leave={1: co(8)})
    assert inp.effective_target(1) < inp.effective_target(2)


def test_no_overtime_target_is_left_alone():
    # base_target == norm: nothing above the norm to scale, so Co changes nothing.
    inp = aug_input(leave={1: co(10)}, base_target=AUG_NORM)
    assert inp.effective_target(1) == AUG_NORM


def test_target_below_norm_is_left_alone():
    # A deficit target (below the norm) is not overtime; Co must not inflate it.
    inp = aug_input(leave={1: co(10)}, base_target=AUG_NORM - 8)
    assert inp.effective_target(1) == AUG_NORM - 8


def test_infirmiere_use_the_same_rule():
    # Scope is all staff (decided 2026-09-15): the aide pool sees it identically.
    aides = [Nurse(i, f"Infirmiera {i}", section="") for i in range(1, 9)]
    inp = ScheduleInput(2026, 8, 184, aides, staff_type=STAFF_AIDE,
                        leave={1: co(10)}, holidays=AUG_HOLIDAYS)
    assert inp.effective_target(1) == 184 - round(16 * 10 / 21)  # 176
    assert inp.effective_target(2) == 184                        # no Co


# --- end to end: a valid schedule honours the reduced target ---------------

# The head nurse works only 8h shifts, so she can never reach a target above the
# month's norm - an orthogonal, pre-existing constraint (it is why the golden
# tests use July, where target == norm). Exclude her so this test isolates the
# Co-overtime mechanic on the worker pool.
_ASISTENTE = [n for n in DEMO_NURSES if not n.is_head]


@pytest.mark.parametrize("staff_type,nurses", [
    ("asistenta", _ASISTENTE),
    (STAFF_AIDE, [Nurse(i, f"Infirmiera {i}", section="") for i in range(1, 9)]),
])
def test_solver_produces_valid_schedule_with_reduced_targets(staff_type, nurses):
    # workers=1 for a reproducible solve (seed alone is not enough - see the
    # solver-nondeterminism note). base_target 184 in August carries real
    # overtime, so the leave nurse's target is genuinely reduced.
    cfg = Config(seed=7, max_time_s=60.0, workers=1)
    leave = {1: co(5)}
    inp = ScheduleInput(2026, 8, 184, nurses, staff_type=staff_type,
                        leave=leave, holidays=AUG_HOLIDAYS, config=cfg)
    res = solve(inp)
    assert res.ok, (res.status, res.diagnosis, [i.message for i in res.prechecks])
    report = validate(inp, res.assignments)
    assert report.ok, [v.message for v in report.violations]

    co_t = res.totals[1]        # 5 Co days -> reduced target
    worked_t = res.totals[2]    # no Co -> full base target
    assert co_t.effective_target == 180
    assert worked_t.effective_target == 184
    assert co_t.effective_target < worked_t.effective_target
    # both land inside the band, so the leave nurse really does less overtime
    # (her total sits near 180, the worked nurse's near 184).
    assert abs(co_t.owed) <= cfg.band and abs(worked_t.owed) <= cfg.band
