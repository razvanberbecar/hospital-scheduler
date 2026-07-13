"""CLI harness for the solver (milestone M2): run a month, print the GRAFIC.

Usage:
    python -m app.solver.cli --year 2026 --month 7 [--target 184] [--demo-leave]
"""
from __future__ import annotations

import argparse
import datetime as dt

from . import calendar as cal
from .domain import BIG, SMALL, Config, Nurse, ScheduleInput, Shift, ShiftRequest
from .model import SolveResult, solve
from .prechecks import run_prechecks
from .validator import validate

# The real Pediatrie roster, ids = row numbers on the paper GRAFIC (July 2026).
# Berbecar Anca is the head nurse (confirmed 2026-07-05). Matei Sorina's
# 8h-heavy July on the paper was a one-off duty exception, not a role.
# PLACEHOLDERS until the manager provides them: the BIG/SMALL section split
# and the kitchen-capable flags below are guesses to be corrected in the UI.
DEMO_NURSES = [
    Nurse(1, "Drulea Florica", BIG, kitchen_capable=True),
    Nurse(2, "Pop Delia", BIG, kitchen_capable=True),
    Nurse(3, "Prodan Gabriela", BIG),
    Nurse(4, "Codre Aurelia", BIG),
    Nurse(5, "Matei Sorina", BIG),
    Nurse(6, "Pop Marinela", BIG),
    Nurse(7, "Campian Aurelia", BIG),
    Nurse(8, "Rusu Ana", SMALL, kitchen_capable=True),
    Nurse(9, "Blaga Mirela", SMALL, kitchen_capable=True),
    Nurse(10, "Lung Paula", SMALL),
    Nurse(11, "Berbecar Anca", SMALL, role="manager_nurse"),
    Nurse(12, "Boda Erika", SMALL),
    Nurse(13, "Ardelean Camelia", SMALL),
    Nurse(14, "Opris Elena", SMALL),
]


def demo_leave(year: int, month: int, holidays: set[dt.date]) -> dict[int, set[dt.date]]:
    """Two Co streaks similar to the paper GRAFIC: 10 and 5 working days
    (Matei Sorina and Lung Paula, echoing the real sheet's long Co runs)."""
    workdays = [d for d in cal.month_dates(year, month) if not cal.is_offday(d, holidays)]
    return {5: set(workdays[8:18]), 10: set(workdays[3:8])}


def paper_leave_july_2026() -> dict[int, set[dt.date]]:
    """The exact Co days from the real July 2026 paper GRAFIC, decoded from the
    photo and cross-checked against the sheet's own Co= hour annotations
    (e.g. Boda Erika: Total ore 96 = 184 - 88 = 11 Co days)."""
    def days(*ds: int) -> set[dt.date]:
        return {dt.date(2026, 7, d) for d in ds}
    return {
        3: days(24, 27, 28, 29, 30, 31),                       # Prodan Gabriela, 6
        4: days(27, 28, 29, 30, 31),                           # Codre Aurelia, 5
        5: days(13, 14, 15, 16, 17, 20, 21, 22, 23),           # Matei Sorina, 9
        6: days(27, 28, 29, 30, 31),                           # Pop Marinela, 5
        7: days(9, 10, 13, 14),                                # Campian Aurelia, 4
        8: days(6, 7, 8, 9, 10, 13, 14, 15, 16, 17),           # Rusu Ana, 10
        9: days(16, 17, 20),                                   # Blaga Mirela, 3
        10: days(1, 2, 3, 6, 7, 8),                            # Lung Paula, 6
        11: days(1, 2, 3),                                     # Berbecar Anca, 3
        12: days(13, 14, 15, 16, 17, 20, 21, 22, 23, 24, 27),  # Boda Erika, 11
        14: days(1, 2, 3, 6),                                  # Opris Elena, 4
    }


def demo_requests() -> list[ShiftRequest]:
    """Sample requests exercising every kind, including a competition:
    three nurses all want a day shift on Monday July 20."""
    def d(day: int) -> dt.date:
        return dt.date(2026, 7, day)
    return [
        ShiftRequest(4, d(4), "Z", hard=True),   # Codre MUST have Saturday 4 day shift
        ShiftRequest(13, d(10), "Z"),            # Ardelean wants a day shift on the 10th
        ShiftRequest(14, d(24), "N"),            # Opris wants a night on the 24th
        ShiftRequest(10, d(31), "FREE"),         # Lung wants the 31st free
        ShiftRequest(9, d(6), "K"),              # Blaga wants the kitchen on the 6th
        ShiftRequest(1, d(20), "Z"),             # competition for Monday the 20th
        ShiftRequest(5, d(20), "Z"),
        ShiftRequest(6, d(20), "Z"),
    ]


def build_input(year: int, month: int, target: int | None, leave: dict[int, set[dt.date]],
                seed: int, time_s: float,
                requests: list[ShiftRequest] | None = None) -> ScheduleInput:
    holidays = cal.default_holidays(year, month)
    if target is None:
        target = cal.working_days(year, month, holidays) * 8
    cfg = Config(seed=seed, max_time_s=time_s)
    return ScheduleInput(year=year, month=month, base_target=target,
                         nurses=DEMO_NURSES, leave=leave, requests=requests or [],
                         config=cfg)


def print_grid(inp: ScheduleInput, result: SolveResult) -> None:
    holidays = inp.holidays if inp.holidays is not None else cal.default_holidays(inp.year, inp.month)
    dates = cal.month_dates(inp.year, inp.month)
    cell: dict[tuple[int, dt.date], str] = {}
    for a in result.assignments:
        code = "8" if a.shift in (Shift.KITCHEN, Shift.HEAD) else a.shift.value
        if a.shift is Shift.TOPUP:
            code = f"T{a.duration}"
        cell[a.nurse_id, a.date] = code
    for nid, days in inp.leave.items():
        for d in days:
            cell[nid, d] = "Co"

    def daylabel(d: dt.date) -> str:
        return f"{d.day:>3}" + ("*" if cal.is_offday(d, holidays) else " ")

    name_w = max(len(n.name) for n in inp.nurses) + 2
    header = " " * name_w + "".join(daylabel(d) for d in dates)
    print(header)
    for n in inp.nurses:
        row = "".join(f"{cell.get((n.id, d), '-'):>3} " for d in dates)
        print(f"{n.name:<{name_w}}{row}")

    print("\n--- Totals (Total ore | Ore 100% | normal | Co zile | abatere | nopti | bucatarie) ---")
    for n in inp.nurses:
        t = result.totals[n.id]
        print(f"{n.name:<{name_w}} total={t.total:>3}  ore100={t.worked_weekend:>3}  "
              f"normal={t.worked_normal:>3}  Co={t.leave_days:>2}  abatere={t.owed:+3d}  "
              f"nopti={t.nights}  buc={t.kitchens}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Nurse schedule generator - M2 harness")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--month", type=int, default=7)
    p.add_argument("--target", type=int, default=None, help="default: working_days x 8")
    p.add_argument("--demo-leave", action="store_true")
    p.add_argument("--paper-leave", action="store_true",
                   help="the real Co days from the July 2026 paper GRAFIC")
    p.add_argument("--demo-requests", action="store_true",
                   help="sample nurse shift requests (July 2026)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--time", type=float, default=30.0)
    args = p.parse_args(argv)

    leave: dict[int, set[dt.date]] = {}
    if args.demo_leave:
        leave = demo_leave(args.year, args.month, cal.default_holidays(args.year, args.month))
    if args.paper_leave:
        if (args.year, args.month) != (2026, 7):
            p.error("--paper-leave is the July 2026 sheet; use --year 2026 --month 7")
        leave = paper_leave_july_2026()
    requests: list[ShiftRequest] = []
    if args.demo_requests:
        if (args.year, args.month) != (2026, 7):
            p.error("--demo-requests uses July 2026 dates; use --year 2026 --month 7")
        requests = demo_requests()

    inp = build_input(args.year, args.month, args.target, leave, args.seed, args.time, requests)
    print(f"Month {args.month:02d}/{args.year}: target {inp.base_target}h "
          f"({cal.working_days(args.year, args.month, cal.default_holidays(args.year, args.month))} working days)")

    issues = run_prechecks(inp)
    for i in issues:
        print(f"  [{i.level.upper():7}] {i.message}")
    result = solve(inp)
    print(f"\nSolver status: {result.status}"
          + (f", objective {result.objective}" if result.objective is not None else ""))
    if not result.ok:
        for line in result.diagnosis:
            print(f"  DIAGNOSIS: {line}")
        return 1

    print_grid(inp, result)

    if result.requests:
        names = {n.id: n.name for n in inp.nurses}
        print("\n--- Requests ---")
        for r, granted in result.requests:
            mark = "GRANTED" if granted else "DENIED "
            print(f"  [{mark}] {names[r.nurse_id]}: {r.kind} on {r.date}"
                  + (" (must-have)" if r.hard else ""))

    report = validate(inp, result.assignments)
    print(f"\nValidator: {'OK - no hard violations' if report.ok else 'VIOLATIONS FOUND'}")
    for v in report.violations:
        print(f"  [VIOLATION] {v.message}")
    shown = 0
    for w in report.warnings:
        print(f"  [warning] {w.message}")
        shown += 1
        if shown >= 15:
            print(f"  ... and {len(report.warnings) - shown} more warnings")
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
