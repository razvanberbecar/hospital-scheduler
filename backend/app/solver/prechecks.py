"""Fast feasibility pre-checks with human-readable messages (plan section 6.6).

Levels: "error" blocks generation, "warning" informs the manager but lets the
solver run (e.g. the 3-nights-vs-heavy-leave collision, which is relaxed and
flagged rather than blocked - decided in Q&A round 3).
"""
from __future__ import annotations

import datetime as dt
from collections import Counter

from . import calendar as cal
from .domain import (BIG, REQUEST_KINDS, SECTION_LABEL, SMALL, STAFF_AIDE,
                     Issue, Nurse, ScheduleInput, Shift, ShiftRequest)

REQUEST_SHIFT = {"Z": Shift.DAY, "N": Shift.NIGHT, "K": Shift.KITCHEN}


def _blocked_first_days(inp: ScheduleInput, nurse_id: int) -> set[dt.date]:
    """Days 1-2 blocked by the previous month's tail (rest rules)."""
    first = dt.date(inp.year, inp.month, 1)
    tail = inp.prev_tail.get(nurse_id, {})
    blocked: set[dt.date] = set()
    last_prev = first - dt.timedelta(days=1)
    second_prev = first - dt.timedelta(days=2)
    if tail.get(last_prev) is Shift.NIGHT:
        blocked |= {first, first + dt.timedelta(days=1)}
    if tail.get(second_prev) is Shift.NIGHT:
        blocked.add(first)
    return blocked


def max_nights_fitting(inp: ScheduleInput, nurse: Nurse, holidays: set[dt.date]) -> int:
    """Greedy maximum number of nights placeable for one nurse.

    A night on day d forbids any work on d+1 and d+2, so nights pack greedily
    (earliest-free placement is optimal for this interval structure). Only
    day-availability is considered here; the hours cap is applied separately.
    """
    dates = cal.month_dates(inp.year, inp.month)
    unavailable = (inp.unavailable_of(nurse.id, holidays)
                   | _blocked_first_days(inp, nurse.id))
    count = 0
    i = 0
    while i < len(dates):
        if dates[i] not in unavailable:
            count += 1
            i += 3  # the night + two mandatory free days
        else:
            i += 1
    return count


def per_nurse_night_minimum(inp: ScheduleInput, holidays: set[dt.date]) -> dict[int, tuple[int, bool]]:
    """{nurse_id: (minimum nights enforced, relaxed?)} for non-head nurses.

    The minimum is cfg.min_nights (3), lowered only when leave makes it
    impossible either by days (greedy packing) or by hours
    (required worked hours + band < 3 nights x 12h).
    """
    cfg = inp.config
    result: dict[int, tuple[int, bool]] = {}
    for nurse in inp.nurses:
        if nurse.is_head:
            continue
        required_worked = inp.effective_target(nurse.id) - 8 * len(inp.leave_of(nurse.id))
        by_hours = max(0, (required_worked + cfg.band) // 12)
        by_days = max_nights_fitting(inp, nurse, holidays)
        minimum = min(cfg.min_nights, by_hours, by_days)
        minimum = max(0, minimum)
        result[nurse.id] = (minimum, minimum < cfg.min_nights)
    return result


def run_prechecks(inp: ScheduleInput) -> list[Issue]:
    issues: list[Issue] = []
    cfg = inp.config
    is_aide = inp.staff_type == STAFF_AIDE
    holidays = inp.holidays if inp.holidays is not None else cal.default_holidays(inp.year, inp.month)
    dates = cal.month_dates(inp.year, inp.month)
    days = len(dates)
    wd = cal.working_days(inp.year, inp.month, holidays)

    heads = [] if is_aide else [n for n in inp.nurses if n.is_head]
    workers = [n for n in inp.nurses if not n.is_head]
    by_id = {n.id: n for n in inp.nurses}

    # --- roster structure -------------------------------------------------
    if is_aide:
        # Infirmiere: no head, no sections, no kitchen. Daily coverage needs
        # 1 Z + 1 N + 1 x 8h, and the night rest rule keeps a night worker off
        # the next two days, so a workable pool is a few people.
        if len(workers) < 3:
            issues.append(Issue("error", "roster_size",
                                f"Infirmiere coverage needs at least 3 per day (1 day + 1 night + 1 x 8h); "
                                f"the roster has only {len(workers)}."))
    else:
        if len(heads) > 1:
            issues.append(Issue("error", "head_count",
                                f"The roster has {len(heads)} manager_nurse accounts; at most one is supported."))
        if not heads:
            issues.append(Issue("warning", "head_count",
                                "No head nurse on the roster: the daily head-nurse 8h shift will not be scheduled."))
        if len(workers) < 5:
            issues.append(Issue("error", "roster_size",
                                f"Coverage needs at least 5 distinct nurses per day (2 day + 2 night + 1 kitchen); "
                                f"the roster has only {len(workers)} besides the head nurse."))

        for section in (BIG, SMALL):
            pool = [n for n in workers if n.section == section]
            if not pool:
                issues.append(Issue("warning", "section_pool",
                                    f"Section {section} has no nurses: every day and night pair will be a "
                                    f"same-section exception."))
            elif len(pool) < 3:
                issues.append(Issue("warning", "section_pool",
                                    f"Section {section} has only {len(pool)} nurse(s) for ~{days} night and {days} day "
                                    f"slots: expect heavy load."))

        # --- per-section daily feasibility (each section must field a nurse) ---
        # Every day needs one Z and one N from each section (HARD). If all of a
        # section's workers are leave-blocked on some day, the model is infeasible;
        # flag it before burning solver time. A locked head Z/N can cover her own
        # section, so a section with only the head available is not necessarily
        # blocked - we only flag when NO nurse of the section is available at all.
        for section in (BIG, SMALL):
            pool = [n for n in inp.nurses if n.section == section]  # incl. head
            if not pool:
                continue
            # Hoisted: unavailable_of re-derives the bridges from scratch, and
            # these checks are meant to be instant.
            off = {n.id: inp.unavailable_of(n.id, holidays) for n in pool}
            for day in dates:
                available = [n for n in pool if day not in off[n.id]]
                if not available:
                    issues.append(Issue("error", "section_unavailable",
                                        f"{SECTION_LABEL[section]}: every nurse in this section is on leave "
                                        f"on {day}, but each day needs a day and a night nurse from the "
                                        f"section.", date=day))

    # --- target sanity ----------------------------------------------------
    if inp.base_target % 4 != 0:
        issues.append(Issue("error", "target_granularity",
                            f"Target {inp.base_target}h is not a multiple of 4: shift blocks are 12/8/4h, "
                            f"so no nurse can ever land on it exactly."))
    hint = wd * 8
    if inp.base_target != hint:
        issues.append(Issue("info", "target_hint",
                            f"Target {inp.base_target}h differs from the usual norm working_days x 8 = {hint}h "
                            f"for {inp.month:02d}/{inp.year}."))

    # --- leave validation (Co only on Mon-Fri working days) ----------------
    for nid, co_days in inp.leave.items():
        if nid not in by_id:
            issues.append(Issue("error", "leave_unknown_nurse", f"Leave given for unknown nurse id {nid}."))
            continue
        for day in sorted(co_days):
            if day.year != inp.year or day.month != inp.month:
                issues.append(Issue("error", "leave_outside_month",
                                    f"{by_id[nid].name}: leave day {day} is outside {inp.month:02d}/{inp.year}.",
                                    nurse_id=nid, date=day))
            elif cal.is_offday(day, holidays):
                issues.append(Issue("error", "leave_on_offday",
                                    f"{by_id[nid].name}: leave on {day} is invalid - Co is allowed only on "
                                    f"Monday-Friday working days (decided).", nurse_id=nid, date=day))

    # --- kitchen capacity (asistente only) ----------------------------------
    if not is_aide:
        kitchen_pool = [n for n in workers if n.kitchen_capable]
        if not kitchen_pool:
            issues.append(Issue("error", "kitchen_pool",
                                f"The kitchen shift runs every day ({days} shifts) but no rostered nurse is "
                                f"kitchen-capable."))
        else:
            capacity = 0
            for n in kitchen_pool:
                # The kitchen runs weekends too, so a Co-bridged weekend costs
                # her those days as well - hours, though, only Co credits.
                by_days = days - len(inp.unavailable_of(n.id, holidays))
                by_hours = (inp.effective_target(n.id) - 8 * len(inp.leave_of(n.id)) + cfg.band) // 8
                capacity += max(0, min(by_days, by_hours))
            if capacity < days:
                issues.append(Issue("error", "kitchen_capacity",
                                    f"{days} kitchen shifts needed but the {len(kitchen_pool)} kitchen-capable "
                                    f"nurse(s) can cover at most {capacity} within their hours - add "
                                    f"kitchen-capable nurses to the roster."))
            elif len(kitchen_pool) == 1:
                issues.append(Issue("error", "kitchen_pool",
                                    f"Only one kitchen-capable nurse for {days} kitchen shifts - impossible "
                                    f"within one nurse's monthly hours."))
            elif len(kitchen_pool) == 2:
                issues.append(Issue("warning", "kitchen_pool",
                                    "Only two kitchen-capable nurses: the kitchen rotation will be tight."))

    # --- hour supply vs demand ---------------------------------------------
    # Minimum daily coverage: asistente 2Z + 2N + 1K = 56h; infirmiere
    # 1Z + 1N + 1x8 = 32h. Ceilings mirror the total staffing caps.
    required_worked = sum(inp.effective_target(n.id) - 8 * len(inp.leave_of(n.id)) for n in workers)
    if is_aide:
        floor_hours = 32 * days
        # A day absorbs the most hours fully swapped (Z up to 2+cap, no 8h slot:
        # the 8h and the swap-maxed Z never coexist); top-ups add <=8h per aide.
        ceiling_hours = ((2 + cfg.extra_day_cap) * 12 + (1 + cfg.extra_night_cap) * 12) * days \
            + 8 * len(workers)
    else:
        floor_hours = 56 * days
        ceiling_hours = ((2 + cfg.extra_day_cap) * 12 + (2 + cfg.extra_night_cap) * 12 + 8) * days + 8 * len(workers)
    if required_worked < floor_hours:
        issues.append(Issue("error", "supply_low",
                            f"The roster supplies {required_worked}h of work but minimum coverage costs "
                            f"{floor_hours}h. Add staff, reduce leave, or raise the target."))
    if required_worked > ceiling_hours:
        issues.append(Issue("error", "supply_high",
                            f"The roster must place {required_worked}h but staffing caps absorb at most "
                            f"{ceiling_hours}h. Remove staff or lower the target."))

    # --- per-nurse reachability & the 3-nights collision --------------------
    night_min = per_nurse_night_minimum(inp, holidays)
    for n in workers:
        co = len(inp.leave_of(n.id))
        required = inp.effective_target(n.id) - 8 * co
        # Set difference, not arithmetic: leave, bridged off days and the
        # prev-tail block can overlap and must not be subtracted twice.
        off_limits = inp.unavailable_of(n.id, holidays) | _blocked_first_days(inp, n.id)
        available_days = sum(1 for d in dates if d not in off_limits)
        if available_days * 12 + cfg.band < required:
            issues.append(Issue("error", "nurse_unreachable",
                                f"{n.name}: needs {required}h worked but only {available_days} available days "
                                f"(max {available_days * 12}h). Target unreachable even with the +/-{cfg.band}h band.",
                                nurse_id=n.id))
        minimum, relaxed = night_min[n.id]
        if relaxed:
            issues.append(Issue("warning", "nights_relaxed",
                                f"{n.name}: leave makes the 3-night minimum impossible; relaxed to {minimum} "
                                f"night(s) and flagged (decided: flag, don't block).", nurse_id=n.id))

    # --- head nurse arithmetic ----------------------------------------------
    for h in heads:
        total = 8 * wd  # worked on working days + 8h credit per Co day == 8 x wd always
        owed = inp.effective_target(h.id) - total
        if owed != 0:
            issues.append(Issue("warning", "head_owed",
                                f"{h.name} (head nurse) works only weekday 8h shifts: fixed total {total}h vs "
                                f"monthly target {inp.effective_target(h.id)}h is off by {owed:+d}h.",
                                nurse_id=h.id))

    # --- leave crunches (plan 4: relaxed-rule thresholds) ---------------------
    issues.extend(_leave_crunch_issues(inp, holidays))

    # --- shift requests -------------------------------------------------------
    issues.extend(effective_requests(inp)[1])
    return issues


def _runs(indices: list[int]) -> list[list[int]]:
    """Group sorted day-indices into maximal contiguous runs (one issue per run,
    so a long crunch is not spammed one message per day)."""
    runs: list[list[int]] = []
    for i in sorted(indices):
        if runs and i == runs[-1][-1] + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def _leave_crunch_issues(inp: ScheduleInput, holidays: set[dt.date]) -> list[Issue]:
    """Instant leave-crunch prechecks with plain-language messages (plan 4).

    The thresholds are derived for the RELAXED 48h rule (the 24h floor is still
    hard): each day needs two distinct people to field Z+N, and because a night
    blocks the next day, two people cannot cover two consecutive days. Availability
    is the section's non-head workers who are neither on Co that day nor away
    across a Co-bridged weekend; a locked head Z/N counts as one more available
    person that day (lock-aware, so the head-cover workaround is not blocked).
    Infirmiere are pool-wide: three distinct people every day.
    """
    dates = cal.month_dates(inp.year, inp.month)
    workers = [n for n in inp.nurses if not n.is_head]
    off = {w.id: inp.unavailable_of(w.id, holidays) for w in workers}
    issues: list[Issue] = []

    if inp.staff_type == STAFF_AIDE:
        short = [i for i, day in enumerate(dates)
                 if sum(1 for w in workers if day not in off[w.id]) < 3]
        for run in _runs(short):
            span = _span(dates, run)
            issues.append(Issue("error", "infirmiere_leave_crunch",
                                f"Infirmiere: fewer than 3 available on {span} - a day needs "
                                f"1 day + 1 night + 1 x 8h (3 distinct people); too many are on Co.",
                                date=dates[run[0]]))
        return issues

    head = next((n for n in inp.nurses if n.is_head), None)
    head_lock_days = {lock.date for lock in inp.locks
                      if head is not None and lock.nurse_id == head.id
                      and lock.shift in (Shift.DAY, Shift.NIGHT)}
    by_id = {n.id: n for n in inp.nurses}

    for section in (BIG, SMALL):
        sec_workers = [w for w in workers if w.section == section]
        if not sec_workers:
            continue  # the empty-section warning is emitted elsewhere
        head_here = head is not None and head.section == section
        avail: list[set[int]] = []
        for day in dates:
            people = {w.id for w in sec_workers if day not in off[w.id]}
            if head_here and day in head_lock_days:
                people.add(head.id)
            avail.append(people)
        label = SECTION_LABEL[section]

        # Each day: <2 available cannot field Z+N. The all-on-leave (0) case is
        # already reported as section_unavailable, so only flag the lone-nurse case.
        for run in _runs([i for i, s in enumerate(avail) if len(s) == 1]):
            names = _names(by_id, set().union(*(avail[i] for i in run)))
            issues.append(Issue("error", "section_leave_crunch",
                                f"{label}: only {names} available on {_span(dates, run)} - each "
                                f"day needs a day and a night nurse from the section (2 people).",
                                date=dates[run[0]]))

        # Each 2-day window: a union of <3 distinct people cannot cover Z+N on
        # both days (a night blocks the next day). Only the case where neither day
        # is already a lone-nurse day (both >=2) is a new finding.
        two = [i for i in range(len(dates) - 1)
               if len(avail[i]) >= 2 and len(avail[i + 1]) >= 2
               and len(avail[i] | avail[i + 1]) < 3]
        for run in _runs(sorted({i for w in two for i in (w, w + 1)})):
            names = _names(by_id, set().union(*(avail[i] for i in run)))
            issues.append(Issue("error", "section_leave_crunch",
                                f"{label}: only {names} available across {_span(dates, run)} - two "
                                f"people cannot fill Z+N on consecutive days (a night blocks the "
                                f"next day).", date=dates[run[0]]))

        # Each 3-day window: a union of <4 with everyone otherwise available is a
        # soft warning - the stretch will lean on 48h-rest exceptions.
        three = [i for i in range(len(dates) - 2)
                 if all(len(avail[i + k]) >= 2 for k in range(3))
                 and len(avail[i] | avail[i + 1]) >= 3
                 and len(avail[i + 1] | avail[i + 2]) >= 3
                 and len(avail[i] | avail[i + 1] | avail[i + 2]) < 4]
        for run in _runs(sorted({i for w in three for i in (w, w + 1, w + 2)})):
            union = set().union(*(avail[i] for i in run))
            names = _names(by_id, union)
            on_leave = _names(by_id, {w.id for w in sec_workers if w.id not in union})
            issues.append(Issue("warning", "section_leave_warning",
                                f"{label}: only {names} are available on {_span(dates, run)} "
                                f"({on_leave} on Co) - expect 48h-rest exceptions.",
                                date=dates[run[0]]))
    return issues


def _span(dates: list[dt.date], run: list[int]) -> str:
    return str(dates[run[0]]) if len(run) == 1 else f"{dates[run[0]]}..{dates[run[-1]]}"


def _names(by_id: dict[int, Nurse], ids: set[int]) -> str:
    return ", ".join(sorted(by_id[i].name for i in ids)) or "no one"


def effective_requests(inp: ScheduleInput) -> tuple[list[ShiftRequest], list[Issue]]:
    """Filter raw requests down to the ones the model can act on.

    Ignorable problems (request on a Co day, kitchen request without the flag,
    head-nurse request, duplicates) are warnings for soft requests and errors
    for must-have ones. Contradictions between must-have requests (overbooked
    slots, rest-rule clashes) are always errors with named messages.
    """
    issues: list[Issue] = []
    holidays = inp.holidays if inp.holidays is not None \
        else cal.default_holidays(inp.year, inp.month)
    by_id = {n.id: n for n in inp.nurses}
    blocked = {n.id: inp.blocked_of(n.id, holidays) for n in inp.nurses}
    kept: list[ShiftRequest] = []
    seen: dict[tuple[int, dt.date], str] = {}
    for r in inp.requests:
        nurse = by_id.get(r.nurse_id)
        if nurse is None:
            issues.append(Issue("error", "request_unknown_nurse",
                                f"Request for unknown nurse id {r.nurse_id}."))
            continue
        label = f"request {r.kind} on {r.date}"
        must = "impossible (must-have)" if r.hard else "ignored"
        level = "error" if r.hard else "warning"
        if r.kind not in REQUEST_KINDS:
            issues.append(Issue("error", "request_kind",
                                f"{nurse.name}: unknown request kind {r.kind!r}.", r.nurse_id, r.date))
            continue
        if r.date.year != inp.year or r.date.month != inp.month:
            issues.append(Issue("error", "request_outside_month",
                                f"{nurse.name}: {label} is outside the month.", r.nurse_id, r.date))
            continue
        if nurse.is_head:
            issues.append(Issue("warning", "request_head",
                                f"{nurse.name} is the head nurse and her pattern is fixed - {label} ignored.",
                                r.nurse_id, r.date))
            continue
        if r.date in inp.leave_of(r.nurse_id):
            issues.append(Issue(level, "request_on_leave",
                                f"{nurse.name}: {label} falls on her Co day - {must}.", r.nurse_id, r.date))
            continue
        if r.date in blocked[r.nurse_id]:
            # Dropped like a request on a Co day: she is away across this
            # weekend, so the model has no variable to grant it with. Dropping it
            # here keeps the solver and the validator agreeing - otherwise a hard
            # request would be silently ignored by one and flagged by the other.
            issues.append(Issue(level, "request_on_leave_bridge",
                                f"{nurse.name}: {label} falls on a weekend she is away across "
                                f"(Co either side) - {must}.", r.nurse_id, r.date))
            continue
        if r.kind == "K" and not nurse.kitchen_capable:
            issues.append(Issue(level, "request_kitchen",
                                f"{nurse.name}: kitchen {label} but she is not kitchen-capable - {must}.",
                                r.nurse_id, r.date))
            continue
        key = (r.nurse_id, r.date)
        if key in seen:
            if seen[key] != r.kind:
                issues.append(Issue("error", "request_conflict",
                                    f"{nurse.name}: conflicting requests on {r.date} "
                                    f"({seen[key]} and {r.kind}).", r.nurse_id, r.date))
            continue
        seen[key] = r.kind
        kept.append(r)

    # contradictions between must-have requests
    cfg = inp.config
    hards = [r for r in kept if r.hard]
    cap = {"Z": 2 + cfg.extra_day_cap, "N": 2 + cfg.extra_night_cap, "K": 1}
    for (day, kind), count in Counter((r.date, r.kind) for r in hards if r.kind != "FREE").items():
        if count > cap[kind]:
            issues.append(Issue("error", "request_overbooked",
                                f"{count} must-have {kind} requests on {day} but the daily cap is {cap[kind]}."))
    by_nurse: dict[int, dict[dt.date, str]] = {}
    for r in hards:
        by_nurse.setdefault(r.nurse_id, {})[r.date] = r.kind
    for nid, day_map in by_nurse.items():
        name = by_id[nid].name
        for day, kind in day_map.items():
            nxt1 = day_map.get(day + dt.timedelta(days=1))
            nxt2 = day_map.get(day + dt.timedelta(days=2))
            if kind == "N" and (nxt1 not in (None, "FREE") or nxt2 not in (None, "FREE")):
                issues.append(Issue("error", "request_rest",
                                    f"{name}: a must-have night on {day} requires 48h of rest - the must-have "
                                    f"request on the following day(s) cannot hold.", nid, day))
            elif kind == "Z" and nxt1 not in (None, "FREE", "N"):
                issues.append(Issue("error", "request_rest",
                                    f"{name}: after a must-have day shift on {day} only a night may follow "
                                    f"the next day, not {nxt1}.", nid, day))
    return kept, issues


def has_errors(issues: list[Issue]) -> bool:
    return any(i.level == "error" for i in issues)
