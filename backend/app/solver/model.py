"""CP-SAT schedule generator (plan section 6).

Decision variables per non-head nurse and day: DAY / NIGHT / KITCHEN booleans
plus two top-up booleans (4h and 8h variants; at most one top-up per month).
The head nurse is not a decision variable - her weekday 8h shifts are fixed
constants added to the result.
"""
from __future__ import annotations

import copy
import datetime as dt
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from . import calendar as cal
from .domain import (BIG, SECTION_LABEL, SMALL, STAFF_AIDE, Assignment, Issue,
                     Nurse, ScheduleInput, Shift, ShiftRequest)
from .prechecks import (REQUEST_SHIFT, effective_requests,
                        per_nurse_night_minimum, run_prechecks, has_errors)
from .validator import NurseTotals, compute_totals

HOURS_UB = 40 * 31  # loose upper bound for hour IntVars
# Penalty for breaking a hard staffing floor (the 24h 100%-hours minimum, the
# infirmiere run cap): far above every soft weight, so the solver only ever leaves
# one unmet when it is genuinely unavoidable - and the validator then blocks
# approval instead of the run failing opaquely as INFEASIBLE.
W_HARD = 100_000


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

    # Infirmiere (aides) are scheduled separately: no head, no kitchen; the third
    # daily shift is an 8h daytime slot (Shift.EIGHT) instead of the kitchen (K).
    is_aide = inp.staff_type == STAFF_AIDE
    # Every shift a single worker can personally hold on a day (guarded by
    # presence in x): kitchen exists only for kitchen-capable asistente; the 8h
    # slot is every-day for infirmiere and, for asistente, only on cells with an
    # explicit 8h request (see eight_request_cells).
    PERSONAL_SHIFTS = (Shift.DAY, Shift.NIGHT, Shift.KITCHEN, Shift.EIGHT)
    # Asistente get no automatic 8h shifts: a Shift.EIGHT variable is created only
    # where an 8h shift was requested, so the solver can honor the request but
    # never fills 8h shifts on its own. Infirmiere have their own 8h slot daily.
    eight_request_cells = (set() if is_aide
                           else {(r.nurse_id, r.date) for r in inp.requests if r.kind == "8"})

    heads = [] if is_aide else [n for n in inp.nurses if n.is_head]
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
        # A nurse on leave either side of a weekend is away across it, so those
        # off days get no variables either - same treatment as Co itself.
        unavailable = inp.unavailable_of(n.id, holidays)
        # The eve of a Co day takes no NIGHT var (a night would run 7h into her
        # leave); the day still takes the others, which end before midnight.
        no_night = inp.night_blocked_of(n.id)
        for di, day in enumerate(dates):
            if day in unavailable:
                continue
            x[n.id, di, Shift.DAY] = m.NewBoolVar(f"Z_{n.id}_{di}")
            if day not in no_night:
                x[n.id, di, Shift.NIGHT] = m.NewBoolVar(f"N_{n.id}_{di}")
            if is_aide:
                # the 8h slot exists on every calendar day (weekends/holidays too)
                x[n.id, di, Shift.EIGHT] = m.NewBoolVar(f"E_{n.id}_{di}")
            else:
                if n.kitchen_capable:
                    x[n.id, di, Shift.KITCHEN] = m.NewBoolVar(f"K_{n.id}_{di}")
                # 8h shift for an asistenta: only where explicitly requested.
                if (n.id, day) in eight_request_cells:
                    x[n.id, di, Shift.EIGHT] = m.NewBoolVar(f"E8_{n.id}_{di}")
            if not cal.is_offday(day, holidays):
                t4[n.id, di] = m.NewBoolVar(f"T4_{n.id}_{di}")
                t8[n.id, di] = m.NewBoolVar(f"T8_{n.id}_{di}")

    def day_vars(nid: int, di: int) -> list[cp_model.IntVar]:
        out = [x[k] for s in PERSONAL_SHIFTS if (k := (nid, di, s)) in x]
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
        if lock.shift in (Shift.DAY, Shift.NIGHT, Shift.KITCHEN, Shift.EIGHT):
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
    # Flat "16h free between shifts" for BOTH staff types (decided 2026-10-18;
    # extended from asistente to infirmiere the same day). Every shift is 12h and
    # starts only ~07:00 (Z/K/8) or 19:00/18:00 (N), so 16h of rest always clears
    # the whole next day, and the second day after a night (>=24h later) is freely
    # workable. In day-granular terms that leaves exactly two hard rules: after a
    # day shift only a night may follow, and the first full day after a night is
    # never worked (the hard 24h floor). The old penalized 48h exception is gone.
    for n in workers:
        for di in range(D):
            zd = x.get((n.id, di, Shift.DAY))
            nd = x.get((n.id, di, Shift.NIGHT))
            if zd is not None and di + 1 < D:
                # after a day shift, the only allowed next-day shift is a night
                # (a next-morning Z/K/8/top-up would be only 12h rest, below 16h)
                for other in (x.get((n.id, di + 1, Shift.DAY)),
                              x.get((n.id, di + 1, Shift.KITCHEN)),
                              x.get((n.id, di + 1, Shift.EIGHT)),
                              t4.get((n.id, di + 1)), t8.get((n.id, di + 1))):
                    if other is not None:
                        m.AddAtMostOne([zd, other])
            if nd is not None and di + 1 < D:
                # hard 24h floor: the day after a night is never worked
                for other in day_vars(n.id, di + 1):
                    m.AddAtMostOne([nd, other])

    # previous-month tail blocks (rest rules across the boundary)
    first = dates[0]
    for n in workers:
        tail = inp.prev_tail.get(n.id, {})
        last_prev = tail.get(first - dt.timedelta(days=1))
        if last_prev is Shift.NIGHT:
            # day 0 is the hard 24h floor; the second day (day 1) is freely
            # workable under the flat 16h rule, so nothing else is pinned.
            for v in day_vars(n.id, 0):
                m.Add(v == 0)
        if last_prev is Shift.DAY:
            for other in (x.get((n.id, 0, Shift.DAY)), x.get((n.id, 0, Shift.KITCHEN)),
                          x.get((n.id, 0, Shift.EIGHT)),
                          t4.get((n.id, 0)), t8.get((n.id, 0))):
                if other is not None:
                    m.Add(other == 0)

    # --- coverage -------------------------------------------------------------
    swap_terms: list[cp_model.IntVar] = []
    night_section_exc: list[cp_model.IntVar] = []  # asistente 2+0/0+2 nights
    if is_aide:
        # Infirmiere daily coverage (HARD): 1 Z + 1 N + 1 x 8h. The swap exception
        # (penalized) turns the 8h slot into a second Z -> 2 Z + 1 N, used only when
        # leave/hour targets force it.
        for di, day in enumerate(dates):
            zvars = [x[k] for n in workers if (k := (n.id, di, Shift.DAY)) in x]
            nvars = [x[k] for n in workers if (k := (n.id, di, Shift.NIGHT)) in x]
            evars = [x[k] for n in workers if (k := (n.id, di, Shift.EIGHT)) in x]
            swap = m.NewBoolVar(f"swap_{di}")
            m.Add(sum(evars) == 1 - swap)
            m.Add(sum(zvars) >= 1 + swap)
            m.Add(sum(nvars) >= 1)
            m.Add(sum(zvars) <= 1 + swap + cfg.extra_day_cap)
            m.Add(sum(nvars) <= 1 + cfg.extra_night_cap)
            swap_terms.append(swap)
    else:
        # Day (Z): one per section is the rule (2/day), but everyone reaching the
        # monthly target forces 3-4 Z on most days (nights are fixed at 2, so days
        # carry the surplus), so the cap is a plain hard 4. Night (N): exactly
        # 2/day total, one per section preferred but the 2+0 / 0+2 split allowed
        # under penalty (night_section_exc). Exactly one kitchen. A locked head
        # Z/N/K counts as a constant toward the relevant sum.
        section_of = {n.id: n.section for n in inp.nurses}
        for di, day in enumerate(dates):
            zvars = [x[k] for n in workers if (k := (n.id, di, Shift.DAY)) in x]
            nvars = [x[k] for n in workers if (k := (n.id, di, Shift.NIGHT)) in x]
            kvars = [x[k] for n in workers if (k := (n.id, di, Shift.KITCHEN)) in x]
            head_a = head_by_day.get(day)
            head_shift = head_a.shift if head_a is not None else None
            head_section = section_of.get(head_a.nurse_id) if head_a is not None else None
            # total-based counts (head Z/N/K also occupies a slot)
            head_z = 1 if head_shift is Shift.DAY else 0
            head_n = 1 if head_shift is Shift.NIGHT else 0
            head_k = 1 if head_shift is Shift.KITCHEN else 0
            # exactly two nights and exactly one kitchen every day
            m.Add(sum(nvars) + head_n == 2)
            m.Add(sum(kvars) + head_k == 1)
            # day shifts: hard cap 4 (they carry the hours the nights no longer do)
            m.Add(sum(zvars) + head_z <= 4)
            # HARD: one Z per section (the 3rd/4th Z land wherever hours need them)
            for section in (BIG, SMALL):
                pool = [x[k] for n in workers
                        if n.section == section and (k := (n.id, di, Shift.DAY)) in x]
                const = head_z if head_section == section else 0
                m.Add(sum(pool) + const >= 1)
            # SOFT: one N per section preferred; pay w_night_section for a 2+0/0+2
            for section in (BIG, SMALL):
                pool = [x[k] for n in workers
                        if n.section == section and (k := (n.id, di, Shift.NIGHT)) in x]
                const = head_n if head_section == section else 0
                uncov = m.NewBoolVar(f"nuncov_{section}_{di}")
                m.Add(sum(pool) + const + uncov >= 1)
                night_section_exc.append(uncov)

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
    weekend_floor_slack = []
    for n in workers:
        n_terms, w_terms = [], []
        for di, day in enumerate(dates):
            for s in PERSONAL_SHIFTS:
                v = x.get((n.id, di, s))
                if v is None:
                    continue
                nh, wh = cal.shift_hours(day, s, cal.duration_of(s), holidays, inp.staff_type)
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
        if cfg.weekend_floor:
            # hard floor of 100% (weekend/holiday) hours; the head is not a worker,
            # so she is exempt. Slack is W_HARD-penalised, never used when reachable.
            fl = m.NewIntVar(0, cfg.weekend_floor, f"wfloor_{n.id}")
            m.Add(weekend + fl >= cfg.weekend_floor)
            weekend_floor_slack.append(fl)

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

    # --- infirmiere run cap: no more than max_consec_aide worked days in a row ---
    # Keep an aide on short 8-Z-N stretches, not 8-8-Z-N (hard). Each window of
    # cap+1 consecutive days holds at most cap worked days; windows at the start
    # count the previous month's worked tail so a run cannot cross the boundary.
    # W_HARD-penalised slack, so an impossible month fails via the validator, not
    # opaquely.
    aide_run_slack = []
    if is_aide and cfg.max_consec_aide:
        cap = cfg.max_consec_aide
        for n in workers:
            tail = inp.prev_tail.get(n.id, {})
            tail_worked = {-1: 1 if tail.get(first - dt.timedelta(days=1)) else 0,
                           -2: 1 if tail.get(first - dt.timedelta(days=2)) else 0}
            for start in range(-2, D - cap):
                terms, const = [], 0
                for j in range(cap + 1):
                    di = start + j
                    if di < 0:
                        const += tail_worked.get(di, 0)
                    else:
                        terms.extend(day_vars(n.id, di))
                if not terms:
                    continue
                sl = m.NewIntVar(0, cap + 1, f"aiderun_{n.id}_{start}")
                m.Add(sum(terms) + const <= cap + sl)
                aide_run_slack.append(sl)

    # --- fairness spreads ----------------------------------------------------------
    objective_terms = []
    spread_specs = []
    if len(workers) >= 2:
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

    # --- weekend/holiday ("Ore 100%") fairness, scaled to presence ---------------
    # Each nurse's share of the 100%-bonus hours tracks how much of the month she
    # is available, so a nurse on Co carries a little fewer than a full-month
    # colleague - graded, not the old all-equal-or-floor-vs-rest. Shares are scaled
    # to a fixed base of 100 so the weight is roster-independent; the deviation
    # from the fair share is penalised (L1), which - unlike a max-min spread -
    # holds each nurse near her own target instead of forcing the extremes.
    if len(workers) >= 2 and cfg.w_weekend_fair:
        avail = {n.id: D - len(inp.unavailable_of(n.id, holidays)) for n in workers}
        tot_avail = sum(avail.values()) or 1
        share = {nid: max(1, round(100 * a / tot_avail)) for nid, a in avail.items()}
        S = sum(share.values())
        wtot = m.NewIntVar(0, HOURS_UB, "wknd_total")
        m.Add(wtot == sum(weekend_of[n.id] for n in workers))
        for n in workers:
            # fair share = wtot * share/S; penalise |S*weekend - share*wtot|
            g = m.NewIntVar(-S * HOURS_UB, S * HOURS_UB, f"wfair_{n.id}")
            m.Add(g == S * weekend_of[n.id] - share[n.id] * wtot)
            ag = m.NewIntVar(0, S * HOURS_UB, f"wfairabs_{n.id}")
            m.AddAbsEquality(ag, g)
            objective_terms.append(cfg.w_weekend_fair * ag)

    # --- infirmiere: balance the daily 8h slot across aides, scaled to presence --
    # Same presence-proportional fair share, on the COUNT of 8h shifts, so the
    # every-day 8h slot is not piled onto one aide (10 eights) while another gets 1.
    # The hour target still dominates, so a heavy-Co aide stays on 12h shifts rather
    # than being handed eights she cannot afford within her hours.
    if is_aide and len(workers) >= 2 and cfg.w_eight_fair:
        avail = {n.id: D - len(inp.unavailable_of(n.id, holidays)) for n in workers}
        tot_avail = sum(avail.values()) or 1
        share = {nid: max(1, round(100 * a / tot_avail)) for nid, a in avail.items()}
        S = sum(share.values())
        eight_of = {}
        for n in workers:
            ev = [x[k] for di in range(D) if (k := (n.id, di, Shift.EIGHT)) in x]
            ec = m.NewIntVar(0, D, f"ecnt_{n.id}")
            m.Add(ec == sum(ev))
            eight_of[n.id] = ec
        etot = m.NewIntVar(0, D, "eight_total")
        m.Add(etot == sum(eight_of[n.id] for n in workers))
        for n in workers:
            # fair share = etot * share/S; penalise |S*eights - share*etot|
            g = m.NewIntVar(-S * D, S * D, f"efair_{n.id}")
            m.Add(g == S * eight_of[n.id] - share[n.id] * etot)
            ag = m.NewIntVar(0, S * D, f"efairabs_{n.id}")
            m.AddAbsEquality(ag, g)
            objective_terms.append(cfg.w_eight_fair * ag)

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
    if swap_terms:
        objective_terms.append(cfg.w_eight_swap * sum(swap_terms))
    if night_section_exc:
        objective_terms.append(cfg.w_night_section * sum(night_section_exc))
    if weekend_floor_slack:
        objective_terms.append(W_HARD * sum(weekend_floor_slack))
    if aide_run_slack:
        objective_terms.append(W_HARD * sum(aide_run_slack))
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
            # Plan 2 (A): if must-have requests were in play, first try to explain
            # which ones cannot hold; fall back to the coverage diagnosis otherwise.
            hard_reqs = [r for r in kept_requests if r.hard]
            explained = _explain_hard_requests(inp, hard_reqs) if hard_reqs else None
            result.diagnosis = explained if explained is not None \
                else _diagnose(inp, holidays, night_min)
        return result

    assignments: list[Assignment] = []
    for n in workers:
        for di, day in enumerate(dates):
            for s in PERSONAL_SHIFTS:
                v = x.get((n.id, di, s))
                if v is not None and solver.Value(v):
                    dur = cal.duration_of(s)
                    nh, wh = cal.shift_hours(day, s, dur, holidays, inp.staff_type)
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


def _explain_hard_requests(inp: ScheduleInput,
                           hard_reqs: list[ShiftRequest]) -> list[str] | None:
    """Plan 2 (A): the run was INFEASIBLE with must-have requests. Re-solve once
    with every request soft and w_request enormous, so denials happen only when
    unavoidable and the denied set is the minimal explanation. Returns the
    diagnosis lines, or None if even the all-soft retry is infeasible (the caller
    then falls back to the coverage diagnosis). The retry has no hard requests, so
    it cannot recurse into this path."""
    retry = copy.deepcopy(inp)
    retry.requests = [ShiftRequest(r.nurse_id, r.date, r.kind, hard=False)
                      for r in inp.requests]
    retry.config.w_request = 1_000_000
    res = solve(retry, skip_prechecks=True)
    if not res.ok:
        return None
    denied = {(r.nurse_id, r.date, r.kind) for r, granted in res.requests if not granted}
    by_id = {n.id: n for n in inp.nurses}
    lines = ["Cererile obligatorii nu pot fi toate respectate; un grafic valid "
             "există fără acestea:"]
    for r in hard_reqs:
        if (r.nurse_id, r.date, r.kind) in denied:
            lines.append(f"{by_id[r.nurse_id].name}: {r.kind} pe {r.date}")
    return lines


def _diagnose(inp: ScheduleInput, holidays: set[dt.date],
              night_min: dict[int, tuple[int, bool]]) -> list[str]:
    """Slack-based infeasibility diagnosis (plan 6.6): re-solve with the daily
    coverage minimums soft and report the day+slot that needed slack. For
    infirmiere the slots are Z/N/8 (the swap exception folds into the Z slack)."""
    cfg = inp.config
    is_aide = inp.staff_type == STAFF_AIDE
    THIRD = Shift.EIGHT if is_aide else Shift.KITCHEN
    dates = cal.month_dates(inp.year, inp.month)
    workers = [n for n in inp.nurses if not n.is_head]
    m = cp_model.CpModel()
    x = {}
    for n in workers:
        unavailable = inp.unavailable_of(n.id, holidays)
        no_night = inp.night_blocked_of(n.id)
        for di, day in enumerate(dates):
            if day in unavailable:
                continue
            x[n.id, di, Shift.DAY] = m.NewBoolVar(f"Z_{n.id}_{di}")
            if day not in no_night:
                x[n.id, di, Shift.NIGHT] = m.NewBoolVar(f"N_{n.id}_{di}")
            if is_aide:
                x[n.id, di, Shift.EIGHT] = m.NewBoolVar(f"E_{n.id}_{di}")
            elif n.kitchen_capable:
                x[n.id, di, Shift.KITCHEN] = m.NewBoolVar(f"K_{n.id}_{di}")

    # Plan 3 (B): respect locks so a manager-covered day is not reported short.
    # The head has no variables; her locked Z/N/K fold into the coverage sums as
    # constants (below), while worker locks pin the matching variable when present.
    heads = [] if is_aide else [n for n in inp.nurses if n.is_head]
    head = heads[0] if heads else None
    head_by_day: dict[dt.date, Assignment] = {}
    if head is not None:
        for a in _head_assignments(inp, head, holidays):
            head_by_day[a.date] = a
    lock_index = {day: di for di, day in enumerate(dates)}
    for lock in inp.locks:
        di = lock_index.get(lock.date)
        if di is None:
            continue
        if lock.shift in (Shift.DAY, Shift.NIGHT, Shift.KITCHEN, Shift.EIGHT):
            v = x.get((lock.nurse_id, di, lock.shift))
            if v is not None:
                m.Add(v == 1)

    for n in workers:
        for di in range(D := len(dates)):
            vs = [x[k] for s in (Shift.DAY, Shift.NIGHT, THIRD) if (k := (n.id, di, s)) in x]
            if vs:
                m.AddAtMostOne(vs)
            nd = x.get((n.id, di, Shift.NIGHT))
            if nd is not None and di + 1 < D:
                # only the hard 24h floor after a night (flat 16h rule); the second
                # day after a night carries no constraint for either staff type.
                for s in (Shift.DAY, Shift.NIGHT, THIRD):
                    other = x.get((n.id, di + 1, s))
                    if other is not None:
                        m.AddAtMostOne([nd, other])
    slacks = []
    if is_aide:
        for di, day in enumerate(dates):
            for label, s in (("zi (Z)", Shift.DAY), ("noapte (N)", Shift.NIGHT),
                             ("8h", Shift.EIGHT)):
                pool = [x[k] for n in workers if (k := (n.id, di, s)) in x]
                sl = m.NewIntVar(0, 1, f"slack_{s.value}_{di}")
                m.Add(sum(pool) + sl >= 1)
                slacks.append((day, label, sl, 1))
    else:
        # A locked head Z counts toward its section's day sum, a head N toward the
        # pool-wide night sum, a head K toward the kitchen sum (plan 3), so a
        # head-covered day is not flagged. Day coverage is per-section (one Z
        # each); night coverage is pool-wide (two, from either section).
        section_of = {n.id: n.section for n in inp.nurses}
        for di, day in enumerate(dates):
            head_a = head_by_day.get(day)
            head_shift = head_a.shift if head_a is not None else None
            head_section = section_of.get(head_a.nurse_id) if head_a is not None else None
            head_z = 1 if head_shift is Shift.DAY else 0
            head_n = 1 if head_shift is Shift.NIGHT else 0
            head_k = 1 if head_shift is Shift.KITCHEN else 0
            for section in (BIG, SMALL):
                pool = [x[k] for n in workers
                        if n.section == section and (k := (n.id, di, Shift.DAY)) in x]
                const = head_z if head_section == section else 0
                sl = m.NewIntVar(0, 1, f"slack_zi_{section}_{di}")
                m.Add(sum(pool) + const + sl >= 1)
                slacks.append((day, f"zi {SECTION_LABEL[section]}", sl, 1))
            npool = [x[k] for n in workers if (k := (n.id, di, Shift.NIGHT)) in x]
            nsl = m.NewIntVar(0, 2, f"slack_noapte_{di}")
            m.Add(sum(npool) + head_n + nsl >= 2)
            slacks.append((day, "noapte", nsl, 2))
            kpool = [x[k] for n in workers if (k := (n.id, di, Shift.KITCHEN)) in x]
            ksl = m.NewIntVar(0, 1, f"slack_kitchen_{di}")
            m.Add(sum(kpool) + head_k + ksl >= 1)
            slacks.append((day, "bucătărie", ksl, 1))
    m.Minimize(sum(sl for *_, sl, _n in slacks))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = min(10.0, cfg.max_time_s)
    solver.parameters.random_seed = cfg.seed
    if solver.Solve(m) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return ["Graficul nu poate acoperi deloc necesarul zilnic de bază; adaugă asistente."]
    out = []
    for day, label, sl, need in slacks:
        v = solver.Value(sl)
        if v:
            out.append(f"{day}: acoperire {label} insuficientă cu {v} (necesar {need}), chiar ignorând țintele de ore.")
    if not out:
        out.append("Acoperirea în sine este realizabilă; conflictul vine din interacțiunea dintre țintele de ore, "
                   "regula de weekend, minimul de nopți sau regulile de odihnă. Încearcă să lărgești marja sau "
                   "să ajustezi concediile.")
    return out
