"""CP-SAT schedule generator (plan section 6).

Decision variables per non-head nurse and day: DAY / NIGHT / KITCHEN booleans
plus two top-up booleans (4h and 8h variants; at most one top-up per month).
The head nurse is not a decision variable - her weekday 8h shifts are fixed
constants added to the result.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from . import calendar as cal
from .domain import (BIG, SECTION_LABEL, SMALL, Assignment, Issue, Nurse,
                     ScheduleInput, Shift, ShiftRequest)
from .prechecks import (REQUEST_SHIFT, effective_requests,
                        per_nurse_night_minimum, run_prechecks, has_errors)
from .validator import NurseTotals, compute_totals

HOURS_UB = 40 * 31  # loose upper bound for hour IntVars


@dataclass
class SolveResult:
    status: str                       # OPTIMAL | FEASIBLE | INFEASIBLE | PRECHECK_FAILED | ...
    assignments: list[Assignment] = field(default_factory=list)
    totals: dict[int, NurseTotals] = field(default_factory=dict)
    relaxed_nights: dict[int, int] = field(default_factory=dict)  # nurse_id -> relaxed minimum
    requests: list[tuple[ShiftRequest, bool]] = field(default_factory=list)  # (request, granted)
    prechecks: list[Issue] = field(default_factory=list)
    diagnosis: list[str] = field(default_factory=list)          # filled on INFEASIBLE
    objective: int | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("OPTIMAL", "FEASIBLE")


def _head_assignments(inp: ScheduleInput, head: Nurse, holidays: set[dt.date]) -> list[Assignment]:
    """The head nurse's fixed schedule. She is auto-assigned an 8h H shift on
    every working day (skipping leave/holidays), but a locked head cell wins for
    its day: the locked assignment is emitted instead, and no auto-H that day.
    Locked head cells may be Z/N/K/T/H and may fall on a weekend."""
    locks_by_day: dict[dt.date, Assignment] = {
        lock.date: lock for lock in inp.locks if lock.nurse_id == head.id}
    out = []
    for day in cal.month_dates(inp.year, inp.month):
        if day in inp.leave_of(head.id):
            continue
        locked = locks_by_day.get(day)
        if locked is not None:
            out.append(locked)
            continue
        if cal.is_offday(day, holidays):
            continue
        out.append(Assignment(head.id, day, Shift.HEAD, 8, 8, 0))
    return out


def solve(inp: ScheduleInput, skip_prechecks: bool = False) -> SolveResult:
    cfg = inp.config
    holidays = inp.holidays if inp.holidays is not None else cal.default_holidays(inp.year, inp.month)
    dates = cal.month_dates(inp.year, inp.month)
    D = len(dates)

    prechecks = [] if skip_prechecks else run_prechecks(inp)
    if has_errors(prechecks):
        return SolveResult(status="PRECHECK_FAILED", prechecks=prechecks)

    heads = [n for n in inp.nurses if n.is_head]
    head = heads[0] if heads else None
    workers = [n for n in inp.nurses if not n.is_head]
    night_min = per_nurse_night_minimum(inp, holidays)

    # The head nurse is not a decision variable: fix her schedule up front so a
    # locked head Z/N can feed the per-section coverage sums as a constant.
    head_by_day: dict[dt.date, Assignment] = {}
    if head is not None:
        for a in _head_assignments(inp, head, holidays):
            head_by_day[a.date] = a

    m = cp_model.CpModel()
    x: dict[tuple[int, int, Shift], cp_model.IntVar] = {}
    t4: dict[tuple[int, int], cp_model.IntVar] = {}
    t8: dict[tuple[int, int], cp_model.IntVar] = {}

    for n in workers:
        for di, day in enumerate(dates):
            if day in inp.leave_of(n.id):
                continue
            x[n.id, di, Shift.DAY] = m.NewBoolVar(f"Z_{n.id}_{di}")
            x[n.id, di, Shift.NIGHT] = m.NewBoolVar(f"N_{n.id}_{di}")
            if n.kitchen_capable:
                x[n.id, di, Shift.KITCHEN] = m.NewBoolVar(f"K_{n.id}_{di}")
            if not cal.is_offday(day, holidays):
                t4[n.id, di] = m.NewBoolVar(f"T4_{n.id}_{di}")
                t8[n.id, di] = m.NewBoolVar(f"T8_{n.id}_{di}")

    def day_vars(nid: int, di: int) -> list[cp_model.IntVar]:
        out = [x[k] for s in (Shift.DAY, Shift.NIGHT, Shift.KITCHEN) if (k := (nid, di, s)) in x]
        for tv in (t4.get((nid, di)), t8.get((nid, di))):
            if tv is not None:
                out.append(tv)
        return out

    # --- locked cells (M3 editor): pin the matching variable when it exists ---
    # A lock whose variable is absent (nurse on leave, non-kitchen-capable K,
    # etc.) is skipped silently - the validator flags the mismatch. The head
    # nurse has no decision variables; her locks are applied in
    # _head_assignments (a locked head cell wins over the auto-H that day).
    lock_index = {day: di for di, day in enumerate(dates)}
    for lock in inp.locks:
        di = lock_index.get(lock.date)
        if di is None:
            continue
        if lock.shift in (Shift.DAY, Shift.NIGHT, Shift.KITCHEN):
            v = x.get((lock.nurse_id, di, lock.shift))
            if v is not None:
                m.Add(v == 1)
        elif lock.shift is Shift.TOPUP:
            tv = (t4 if lock.duration == 4 else t8).get((lock.nurse_id, di))
            if tv is not None:
                m.Add(tv == 1)

    # --- one shift per day; one top-up per month -----------------------------
    for n in workers:
        for di in range(D):
            vs = day_vars(n.id, di)
            if vs:
                m.AddAtMostOne(vs)
        tops = [v for (nid, _), v in t4.items() if nid == n.id]
        tops += [v for (nid, _), v in t8.items() if nid == n.id]
        if tops:
            m.AddAtMostOne(tops)

    # --- rest rules -----------------------------------------------------------
    for n in workers:
        for di in range(D):
            zd = x.get((n.id, di, Shift.DAY))
            nd = x.get((n.id, di, Shift.NIGHT))
            if zd is not None and di + 1 < D:
                # after a day shift, the only allowed next-day shift is a night
                for other in (x.get((n.id, di + 1, Shift.DAY)),
                              x.get((n.id, di + 1, Shift.KITCHEN)),
                              t4.get((n.id, di + 1)), t8.get((n.id, di + 1))):
                    if other is not None:
                        m.AddAtMostOne([zd, other])
            if nd is not None:
                # after a night shift, two full days off
                for k in (1, 2):
                    if di + k < D:
                        for other in day_vars(n.id, di + k):
                            m.AddAtMostOne([nd, other])

    # previous-month tail blocks (rest rules across the boundary)
    first = dates[0]
    for n in workers:
        tail = inp.prev_tail.get(n.id, {})
        last_prev = tail.get(first - dt.timedelta(days=1))
        second_prev = tail.get(first - dt.timedelta(days=2))
        if last_prev is Shift.NIGHT:
            for di in (0, 1):
                for v in day_vars(n.id, di):
                    m.Add(v == 0)
        if second_prev is Shift.NIGHT:
            for v in day_vars(n.id, 0):
                m.Add(v == 0)
        if last_prev is Shift.DAY:
            for other in (x.get((n.id, 0, Shift.DAY)), x.get((n.id, 0, Shift.KITCHEN)),
                          t4.get((n.id, 0)), t8.get((n.id, 0))):
                if other is not None:
                    m.Add(other == 0)

    # --- coverage: HARD per-section day/night, exactly one kitchen -----------
    # Each day must field at least one Z from each section and one N from each
    # section (that is how the 2Z and 2N are formed). A locked head Z/N counts
    # as a constant toward its section's sum. Staffing caps stay TOTAL-based.
    section_of = {n.id: n.section for n in inp.nurses}
    for di, day in enumerate(dates):
        zvars = [x[k] for n in workers if (k := (n.id, di, Shift.DAY)) in x]
        nvars = [x[k] for n in workers if (k := (n.id, di, Shift.NIGHT)) in x]
        kvars = [x[k] for n in workers if (k := (n.id, di, Shift.KITCHEN)) in x]
        head_a = head_by_day.get(day)
        head_shift = head_a.shift if head_a is not None else None
        head_section = section_of.get(head_a.nurse_id) if head_a is not None else None
        # total-based caps (head Z/N/K also occupies a slot)
        head_z = 1 if head_shift is Shift.DAY else 0
        head_n = 1 if head_shift is Shift.NIGHT else 0
        head_k = 1 if head_shift is Shift.KITCHEN else 0
        m.Add(sum(zvars) + head_z <= 2 + cfg.extra_day_cap)
        m.Add(sum(nvars) + head_n <= 2 + cfg.extra_night_cap)
        m.Add(sum(kvars) + head_k == 1)
        for slot, shift, head_here in (("day", Shift.DAY, head_z), ("night", Shift.NIGHT, head_n)):
            for section in (BIG, SMALL):
                pool = [x[k] for n in workers
                        if n.section == section and (k := (n.id, di, shift)) in x]
                const = head_here if head_section == section else 0
                m.Add(sum(pool) + const >= 1)

    # --- night minimums --------------------------------------------------------
    nights_of: dict[int, cp_model.IntVar] = {}
    for n in workers:
        nv = [x[k] for di in range(D) if (k := (n.id, di, Shift.NIGHT)) in x]
        cnt = m.NewIntVar(0, D, f"nights_{n.id}")
        m.Add(cnt == sum(nv))
        nights_of[n.id] = cnt
        m.Add(cnt >= night_min[n.id][0])

    # --- nurse shift requests (soft by default, hard = pinned) ----------------
    kept_requests, _ = effective_requests(inp)
    date_index = {day: di for di, day in enumerate(dates)}
    request_pen_terms = []
    for r in kept_requests:
        di = date_index[r.date]
        if r.kind == "FREE":
            vs = day_vars(r.nurse_id, di)
            if r.hard:
                for v in vs:
                    m.Add(v == 0)
            elif vs:
                request_pen_terms.append(sum(vs))
        else:
            v = x.get((r.nurse_id, di, REQUEST_SHIFT[r.kind]))
            if v is None:
                continue
            if r.hard:
                m.Add(v == 1)
            else:
                request_pen_terms.append(1 - v)

    # --- hours: buckets, weekend rule, target deviation ------------------------
    normal_of, weekend_of, absowed = {}, {}, {}
    for n in workers:
        n_terms, w_terms = [], []
        for di, day in enumerate(dates):
            for s in (Shift.DAY, Shift.NIGHT, Shift.KITCHEN):
                v = x.get((n.id, di, s))
                if v is None:
                    continue
                nh, wh = cal.shift_hours(day, s, cal.duration_of(s), holidays)
                if nh:
                    n_terms.append(nh * v)
                if wh:
                    w_terms.append(wh * v)
            for tv, dur in ((t4.get((n.id, di)), 4), (t8.get((n.id, di)), 8)):
                if tv is not None:
                    n_terms.append(dur * tv)  # top-ups exist only on working days -> normal bucket
        normal = m.NewIntVar(0, HOURS_UB, f"normal_{n.id}")
        weekend = m.NewIntVar(0, HOURS_UB, f"weekend_{n.id}")
        m.Add(normal == sum(n_terms))
        m.Add(weekend == sum(w_terms))
        m.Add(weekend <= normal)  # worked hours only: Co credit is NOT in these sums (decided)
        normal_of[n.id], weekend_of[n.id] = normal, weekend

        credit = 8 * len(inp.leave_of(n.id))
        owed = m.NewIntVar(-cfg.band, cfg.band, f"owed_{n.id}")
        m.Add(owed == inp.effective_target(n.id) - (normal + weekend + credit))
        ab = m.NewIntVar(0, cfg.band, f"absowed_{n.id}")
        m.AddAbsEquality(ab, owed)
        absowed[n.id] = ab

    # --- soft patterns -----------------------------------------------------------
    kitchen_run_exc, cluster_exc = [], []
    kitchen_counts = {}
    for n in workers:
        if n.kitchen_capable:
            kv_all = [x[k] for di in range(D) if (k := (n.id, di, Shift.KITCHEN)) in x]
            kc = m.NewIntVar(0, D, f"kcnt_{n.id}")
            m.Add(kc == sum(kv_all))
            kitchen_counts[n.id] = kc
            for di in range(D - 3):
                win = [x[k] for j in range(4) if (k := (n.id, di + j, Shift.KITCHEN)) in x]
                if len(win) == 4:
                    e = m.NewBoolVar(f"krun_{n.id}_{di}")
                    m.Add(sum(win) <= 3 + e)
                    kitchen_run_exc.append(e)
        for di in range(D - 6):
            win = []
            for j in range(7):
                win.extend(day_vars(n.id, di + j))
            if win:
                e = m.NewIntVar(0, 2, f"clus_{n.id}_{di}")
                m.Add(sum(win) <= 5 + e)
                cluster_exc.append(e)

    # --- fairness spreads ----------------------------------------------------------
    objective_terms = []
    spread_specs = []
    if len(workers) >= 2:
        spread_specs.append((cfg.w_wspread, [weekend_of[n.id] for n in workers], "wknd"))
        unrelaxed = [nights_of[n.id] for n in workers if not night_min[n.id][1]]
        if len(unrelaxed) >= 2:
            spread_specs.append((cfg.w_nspread, unrelaxed, "nights"))
    if len(kitchen_counts) >= 2:
        spread_specs.append((cfg.w_kspread, list(kitchen_counts.values()), "kitchen"))
    for weight, exprs, name in spread_specs:
        hi = m.NewIntVar(0, HOURS_UB, f"max_{name}")
        lo = m.NewIntVar(0, HOURS_UB, f"min_{name}")
        m.AddMaxEquality(hi, exprs)
        m.AddMinEquality(lo, exprs)
        objective_terms.append(weight * (hi - lo))

    # --- objective ------------------------------------------------------------------
    objective_terms.append(cfg.w_dev * sum(absowed.values()))
    if request_pen_terms:
        objective_terms.append(cfg.w_request * sum(request_pen_terms))
    all_tops = list(t4.values()) + list(t8.values())
    if all_tops:
        objective_terms.append(cfg.w_topup * sum(all_tops))
    if kitchen_run_exc:
        objective_terms.append(cfg.w_kitchen_run * sum(kitchen_run_exc))
    if cluster_exc:
        objective_terms.append(cfg.w_cluster * sum(cluster_exc))
    m.Minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = cfg.max_time_s
    solver.parameters.random_seed = cfg.seed
    solver.parameters.num_search_workers = cfg.workers
    status = solver.Solve(m)
    status_name = solver.StatusName(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result = SolveResult(status=status_name, prechecks=prechecks)
        if status == cp_model.INFEASIBLE:
            result.diagnosis = _diagnose(inp, holidays, night_min)
        return result

    assignments: list[Assignment] = []
    for n in workers:
        for di, day in enumerate(dates):
            for s in (Shift.DAY, Shift.NIGHT, Shift.KITCHEN):
                v = x.get((n.id, di, s))
                if v is not None and solver.Value(v):
                    dur = cal.duration_of(s)
                    nh, wh = cal.shift_hours(day, s, dur, holidays)
                    assignments.append(Assignment(n.id, day, s, dur, nh, wh))
            for tv, dur in ((t4.get((n.id, di)), 4), (t8.get((n.id, di)), 8)):
                if tv is not None and solver.Value(tv):
                    assignments.append(Assignment(n.id, day, Shift.TOPUP, dur, dur, 0))
    assignments.extend(head_by_day.values())
    assignments.sort(key=lambda a: (a.date, a.nurse_id))

    assigned_shift = {(a.nurse_id, a.date): a.shift for a in assignments}
    requests_report = []
    for r in kept_requests:
        if r.kind == "FREE":
            granted = (r.nurse_id, r.date) not in assigned_shift
        else:
            granted = assigned_shift.get((r.nurse_id, r.date)) is REQUEST_SHIFT[r.kind]
        requests_report.append((r, granted))

    return SolveResult(
        status=status_name,
        assignments=assignments,
        totals=compute_totals(inp, assignments),
        relaxed_nights={nid: mn for nid, (mn, rel) in night_min.items() if rel},
        requests=requests_report,
        prechecks=prechecks,
        objective=int(solver.ObjectiveValue()),
    )


def _diagnose(inp: ScheduleInput, holidays: set[dt.date],
              night_min: dict[int, tuple[int, bool]]) -> list[str]:
    """Slack-based infeasibility diagnosis (plan 6.6): re-solve with the
    per-section coverage minimums soft and report the day+section slots that
    needed slack."""
    cfg = inp.config
    dates = cal.month_dates(inp.year, inp.month)
    workers = [n for n in inp.nurses if not n.is_head]
    m = cp_model.CpModel()
    x = {}
    for n in workers:
        for di, day in enumerate(dates):
            if day in inp.leave_of(n.id):
                continue
            x[n.id, di, Shift.DAY] = m.NewBoolVar(f"Z_{n.id}_{di}")
            x[n.id, di, Shift.NIGHT] = m.NewBoolVar(f"N_{n.id}_{di}")
            if n.kitchen_capable:
                x[n.id, di, Shift.KITCHEN] = m.NewBoolVar(f"K_{n.id}_{di}")
    for n in workers:
        for di in range(D := len(dates)):
            vs = [x[k] for s in (Shift.DAY, Shift.NIGHT, Shift.KITCHEN) if (k := (n.id, di, s)) in x]
            if vs:
                m.AddAtMostOne(vs)
            nd = x.get((n.id, di, Shift.NIGHT))
            if nd is not None:
                for k_ in (1, 2):
                    if di + k_ < D:
                        for s in (Shift.DAY, Shift.NIGHT, Shift.KITCHEN):
                            other = x.get((n.id, di + k_, s))
                            if other is not None:
                                m.AddAtMostOne([nd, other])
    slacks = []
    for di, day in enumerate(dates):
        for label, s in (("day", Shift.DAY), ("night", Shift.NIGHT)):
            for section in (BIG, SMALL):
                pool = [x[k] for n in workers
                        if n.section == section and (k := (n.id, di, s)) in x]
                sl = m.NewIntVar(0, 1, f"slack_{label}_{section}_{di}")
                m.Add(sum(pool) + sl >= 1)
                slacks.append((day, f"{label} {SECTION_LABEL[section]}", sl, 1))
        kpool = [x[k] for n in workers if (k := (n.id, di, Shift.KITCHEN)) in x]
        ksl = m.NewIntVar(0, 1, f"slack_kitchen_{di}")
        m.Add(sum(kpool) + ksl >= 1)
        slacks.append((day, "kitchen", ksl, 1))
    m.Minimize(sum(sl for *_, sl, _n in slacks))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = min(10.0, cfg.max_time_s)
    solver.parameters.random_seed = cfg.seed
    if solver.Solve(m) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return ["The roster cannot satisfy basic daily coverage at all; add nurses."]
    out = []
    for day, label, sl, need in slacks:
        v = solver.Value(sl)
        if v:
            out.append(f"{day}: {label} coverage short by {v} (needs {need}) even ignoring hour targets.")
    if not out:
        out.append("Coverage alone is satisfiable; the conflict comes from hour targets, the weekend rule, "
                   "night minimums or rest rules interacting. Try widening the band or adjusting leave.")
    return out
