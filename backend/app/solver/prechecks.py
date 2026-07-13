"""Fast feasibility pre-checks with human-readable messages (plan section 6.6).

Levels: "error" blocks generation, "warning" informs the manager but lets the
solver run (e.g. the 3-nights-vs-heavy-leave collision, which is relaxed and
flagged rather than blocked - decided in Q&A round 3).
"""
from __future__ import annotations

import datetime as dt
from collections import Counter

from . import calendar as cal
from .domain import (BIG, REQUEST_KINDS, SMALL, Issue, Nurse, ScheduleInput,
                     Shift, ShiftRequest)

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
    unavailable = set(inp.leave_of(nurse.id)) | _blocked_first_days(inp, nurse.id)
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
    holidays = inp.holidays if inp.holidays is not None else cal.default_holidays(inp.year, inp.month)
    dates = cal.month_dates(inp.year, inp.month)
    days = len(dates)
    wd = cal.working_days(inp.year, inp.month, holidays)

    heads = [n for n in inp.nurses if n.is_head]
    workers = [n for n in inp.nurses if not n.is_head]
    by_id = {n.id: n for n in inp.nurses}

    # --- roster structure -------------------------------------------------
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

    # --- per-section daily feasibility (each section must field a nurse) ------
    # Every day needs one Z and one N from each section (HARD). If all of a
    # section's workers are leave-blocked on some day, the model is infeasible;
    # flag it before burning solver time. A locked head Z/N can cover her own
    # section, so a section with only the head available is not necessarily
    # blocked - we only flag when NO nurse of the section is available at all.
    from .domain import SECTION_LABEL
    for section in (BIG, SMALL):
        pool = [n for n in inp.nurses if n.section == section]  # incl. head
        if not pool:
            continue
        for day in dates:
            available = [n for n in pool if day not in inp.leave_of(n.id)]
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

    # --- kitchen capacity ---------------------------------------------------
    kitchen_pool = [n for n in workers if n.kitchen_capable]
    if not kitchen_pool:
        issues.append(Issue("error", "kitchen_pool",
                            f"The kitchen shift runs every day ({days} shifts) but no rostered nurse is "
                            f"kitchen-capable."))
    else:
        capacity = 0
        for n in kitchen_pool:
            by_days = days - len(inp.leave_of(n.id))
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
    required_worked = sum(inp.effective_target(n.id) - 8 * len(inp.leave_of(n.id)) for n in workers)
    floor_hours = 56 * days
    ceiling_hours = ((2 + cfg.extra_day_cap) * 12 + (2 + cfg.extra_night_cap) * 12 + 8) * days + 8 * len(workers)
    if required_worked < floor_hours:
        issues.append(Issue("error", "supply_low",
                            f"The roster supplies {required_worked}h of work but minimum coverage costs "
                            f"{floor_hours}h ({days} days x 56h). Add nurses, reduce leave, or raise the target."))
    if required_worked > ceiling_hours:
        issues.append(Issue("error", "supply_high",
                            f"The roster must place {required_worked}h but staffing caps absorb at most "
                            f"{ceiling_hours}h. Remove nurses or lower the target."))

    # --- per-nurse reachability & the 3-nights collision --------------------
    night_min = per_nurse_night_minimum(inp, holidays)
    for n in workers:
        co = len(inp.leave_of(n.id))
        required = inp.effective_target(n.id) - 8 * co
        available_days = days - co - len(_blocked_first_days(inp, n.id))
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

    # --- shift requests -------------------------------------------------------
    issues.extend(effective_requests(inp)[1])
    return issues


def effective_requests(inp: ScheduleInput) -> tuple[list[ShiftRequest], list[Issue]]:
    """Filter raw requests down to the ones the model can act on.

    Ignorable problems (request on a Co day, kitchen request without the flag,
    head-nurse request, duplicates) are warnings for soft requests and errors
    for must-have ones. Contradictions between must-have requests (overbooked
    slots, rest-rule clashes) are always errors with named messages.
    """
    issues: list[Issue] = []
    by_id = {n.id: n for n in inp.nurses}
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
