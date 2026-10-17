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

REQUEST_SHIFT = {"Z": Shift.DAY, "N": Shift.NIGHT, "K": Shift.KITCHEN, "8": Shift.EIGHT}


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

    The eve of a Co day counts as unavailable too: this function places nights
    only, and a night there would run into her leave.
    """
    dates = cal.month_dates(inp.year, inp.month)
    unavailable = (inp.unavailable_of(nurse.id, holidays)
                   | _blocked_first_days(inp, nurse.id)
                   | inp.night_blocked_of(nurse.id))
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
                                f"Acoperirea infirmierelor necesită cel puțin 3 persoane pe zi (1 zi + 1 noapte + 1 × 8h); "
                                f"pe grafic sunt doar {len(workers)}."))
    else:
        if len(heads) > 1:
            issues.append(Issue("error", "head_count",
                                f"Graficul are {len(heads)} conturi de asistentă șefă; este permis cel mult unul."))
        if not heads:
            issues.append(Issue("warning", "head_count",
                                "Nicio asistentă șefă pe grafic: tura zilnică de 8h a șefei nu va fi programată."))
        if len(workers) < 5:
            issues.append(Issue("error", "roster_size",
                                f"Acoperirea necesită cel puțin 5 asistente distincte pe zi (2 zi + 2 noapte + 1 bucătărie); "
                                f"pe grafic sunt doar {len(workers)} în afară de asistenta șefă."))

        for section in (BIG, SMALL):
            pool = [n for n in workers if n.section == section]
            if not pool:
                issues.append(Issue("warning", "section_pool",
                                    f"Secția {SECTION_LABEL[section]} nu are nicio asistentă: fiecare pereche de zi și noapte "
                                    f"va fi o excepție în aceeași secție."))
            elif len(pool) < 3:
                issues.append(Issue("warning", "section_pool",
                                    f"Secția {SECTION_LABEL[section]} are doar {len(pool)} asistentă(e) pentru ~{days} ture de "
                                    f"noapte și {days} de zi: încărcare mare."))

        # --- per-section daily feasibility (each section must field a Z) ---
        # Every day needs one Z from each section (HARD); the two nights are
        # pool-wide (either section - the 2+0 / 0+2 split). If all of a section's
        # workers are leave-blocked on some day, its Z is unfillable and the model
        # is infeasible; flag it before burning solver time. A locked head Z can
        # cover her own section, so a section with only the head available is not
        # necessarily blocked - we only flag when NO nurse is available at all.
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
                                        f"{SECTION_LABEL[section]}: toate asistentele din această secție sunt în concediu "
                                        f"pe {day}, dar fiecare zi are nevoie de o asistentă de zi din secție.", date=day))

    # --- target sanity ----------------------------------------------------
    if inp.base_target % 4 != 0:
        issues.append(Issue("error", "target_granularity",
                            f"Ținta de {inp.base_target}h nu este multiplu de 4: turele au blocuri de 12/8/4h, "
                            f"așa că nicio asistentă nu o poate atinge exact."))
    hint = wd * 8
    if inp.base_target != hint:
        issues.append(Issue("info", "target_hint",
                            f"Ținta de {inp.base_target}h diferă de norma obișnuită zile_lucrătoare × 8 = {hint}h "
                            f"pentru {inp.month:02d}/{inp.year}."))

    # --- leave validation (Co only on Mon-Fri working days) ----------------
    for nid, co_days in inp.leave.items():
        if nid not in by_id:
            issues.append(Issue("error", "leave_unknown_nurse", f"Concediu atribuit unei asistente necunoscute (id {nid})."))
            continue
        for day in sorted(co_days):
            if day.year != inp.year or day.month != inp.month:
                issues.append(Issue("error", "leave_outside_month",
                                    f"{by_id[nid].name}: ziua de concediu {day} este în afara lunii {inp.month:02d}/{inp.year}.",
                                    nurse_id=nid, date=day))
            elif cal.is_offday(day, holidays):
                issues.append(Issue("error", "leave_on_offday",
                                    f"{by_id[nid].name}: concediul pe {day} este invalid - Co se poate pune doar în "
                                    f"zilele lucrătoare, luni-vineri.", nurse_id=nid, date=day))

    # --- kitchen capacity (asistente only) ----------------------------------
    if not is_aide:
        kitchen_pool = [n for n in workers if n.kitchen_capable]
        if not kitchen_pool:
            issues.append(Issue("error", "kitchen_pool",
                                f"Tura de bucătărie se ține în fiecare zi ({days} ture), dar nicio asistentă de pe "
                                f"grafic nu poate lucra la bucătărie."))
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
                                    f"Sunt necesare {days} ture de bucătărie, dar cele {len(kitchen_pool)} asistente care "
                                    f"pot lucra la bucătărie acoperă cel mult {capacity} în limita orelor lor - adaugă "
                                    f"asistente cu bucătărie pe grafic."))
            elif len(kitchen_pool) == 1:
                issues.append(Issue("error", "kitchen_pool",
                                    f"O singură asistentă poate lucra la bucătărie pentru {days} ture - imposibil în "
                                    f"limita orelor lunare ale unei singure asistente."))
            elif len(kitchen_pool) == 2:
                issues.append(Issue("warning", "kitchen_pool",
                                    "Doar două asistente pot lucra la bucătărie: rotația de bucătărie va fi strânsă."))

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
        # asistente day shifts cap at 4 (they carry the surplus), nights at exactly
        # 2/day, plus the single 8h kitchen; top-ups add <=8h per nurse.
        ceiling_hours = ((2 + cfg.extra_day_cap) * 12 + 2 * 12 + 8) * days + 8 * len(workers)
    if required_worked < floor_hours:
        issues.append(Issue("error", "supply_low",
                            f"Graficul oferă {required_worked}h de muncă, dar acoperirea minimă costă "
                            f"{floor_hours}h. Adaugă personal, redu concediile sau crește ținta."))
    if required_worked > ceiling_hours:
        issues.append(Issue("error", "supply_high",
                            f"Graficul trebuie să plaseze {required_worked}h, dar plafoanele de personal absorb cel mult "
                            f"{ceiling_hours}h. Scoate personal sau scade ținta."))

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
                                f"{n.name}: are nevoie de {required}h lucrate, dar doar {available_days} zile disponibile "
                                f"(maxim {available_days * 12}h). Ținta nu poate fi atinsă nici cu marja de ±{cfg.band}h.",
                                nurse_id=n.id))
        minimum, relaxed = night_min[n.id]
        if relaxed:
            issues.append(Issue("warning", "nights_relaxed",
                                f"{n.name}: concediul face imposibil minimul de 3 nopți; relaxat la {minimum} "
                                f"noapte/nopți și semnalat.", nurse_id=n.id))

    # --- 100%-hours floor reachability (hard weekend_floor) -----------------
    # Every worker must reach cfg.weekend_floor hours of 100% (weekend/holiday)
    # work; a weekend/holiday day supplies at most 12h, so she needs at least
    # ceil(floor/12) of them available. Flag it up front instead of failing opaque.
    if cfg.weekend_floor:
        offdays = [d for d in dates if cal.is_offday(d, holidays)]
        for n in workers:
            avail = sum(1 for d in offdays if d not in inp.unavailable_of(n.id, holidays))
            if avail * 12 < cfg.weekend_floor:
                issues.append(Issue("error", "weekend_floor_unreachable",
                                    f"{n.name}: are nevoie de minim {cfg.weekend_floor}h la 100% "
                                    f"(weekend/sărbători), dar are doar {avail} zi(le) de "
                                    f"weekend/sărbătoare disponibile (maxim {avail * 12}h). Redu "
                                    f"concediul sau scoate-o de pe grafic.", nurse_id=n.id))

    # --- head nurse arithmetic ----------------------------------------------
    for h in heads:
        total = 8 * wd  # worked on working days + 8h credit per Co day == 8 x wd always
        owed = inp.effective_target(h.id) - total
        if owed != 0:
            issues.append(Issue("warning", "head_owed",
                                f"{h.name} (asistentă șefă) lucrează doar ture de 8h în zilele lucrătoare: totalul fix "
                                f"de {total}h față de ținta lunară de {inp.effective_target(h.id)}h diferă cu {owed:+d}h.",
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

    A section's one hard daily duty is now a Z (its nights are pool-wide - the
    2+0 / 0+2 split covers them from either section). A Z cannot follow a Z, so
    two consecutive days need two distinct available people; a consecutive pair
    whose combined availability is under two leaves a day shift unfillable.
    Availability is the section's non-head workers who are neither on Co that day
    nor away across a Co-bridged weekend; a locked head Z counts as one more
    available person that day (lock-aware, so the head-cover workaround is not
    blocked). Infirmiere are pool-wide: three distinct people every day.
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
                                f"Infirmiere: mai puțin de 3 disponibile pe {span} - o zi are nevoie de "
                                f"1 zi + 1 noapte + 1 × 8h (3 persoane distincte); prea multe sunt în Co.",
                                date=dates[run[0]]))
        return issues

    head = next((n for n in inp.nurses if n.is_head), None)
    # Only a locked head DAY shift helps a section field its Z; a locked head N
    # covers a night, which is now pool-wide (either section), so it does not.
    head_z_lock_days = {lock.date for lock in inp.locks
                        if head is not None and lock.nurse_id == head.id
                        and lock.shift is Shift.DAY}
    by_id = {n.id: n for n in inp.nurses}

    for section in (BIG, SMALL):
        sec_workers = [w for w in workers if w.section == section]
        if not sec_workers:
            continue  # the empty-section warning is emitted elsewhere
        head_here = head is not None and head.section == section
        avail: list[set[int]] = []
        for day in dates:
            people = {w.id for w in sec_workers if day not in off[w.id]}
            if head_here and day in head_z_lock_days:
                people.add(head.id)
            avail.append(people)
        label = SECTION_LABEL[section]

        # A section's one hard daily duty is now a Z (its night may come from the
        # other section - the 2+0 / 0+2 fallback). A Z cannot follow a Z, so two
        # consecutive days need two distinct available people: a consecutive pair
        # whose combined availability is under two leaves a day shift unfillable.
        # (A single all-on-leave day is reported as section_unavailable above.)
        two = [i for i in range(len(dates) - 1)
               if len(avail[i] | avail[i + 1]) < 2]
        for run in _runs(sorted({i for w in two for i in (w, w + 1)})):
            names = _names(by_id, set().union(*(avail[i] for i in run)))
            issues.append(Issue("error", "section_leave_crunch",
                                f"{label}: doar {names} disponibilă(e) pe {_span(dates, run)} - fiecare zi "
                                f"are nevoie de o tură de zi din secție, iar o tură de zi nu poate urma "
                                f"alteia, deci două zile consecutive nu pot fi acoperite de o singură "
                                f"persoană.", date=dates[run[0]]))
    return issues


def _span(dates: list[dt.date], run: list[int]) -> str:
    return str(dates[run[0]]) if len(run) == 1 else f"{dates[run[0]]}..{dates[run[-1]]}"


def _names(by_id: dict[int, Nurse], ids: set[int]) -> str:
    return ", ".join(sorted(by_id[i].name for i in ids)) or "nimeni"


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
    no_night = {n.id: inp.night_blocked_of(n.id) for n in inp.nurses}
    kept: list[ShiftRequest] = []
    seen: dict[tuple[int, dt.date], str] = {}
    for r in inp.requests:
        nurse = by_id.get(r.nurse_id)
        if nurse is None:
            issues.append(Issue("error", "request_unknown_nurse",
                                f"Cerere pentru o asistentă necunoscută (id {r.nurse_id})."))
            continue
        label = f"cererea {r.kind} pe {r.date}"
        must = "imposibil (obligatorie)" if r.hard else "ignorată"
        level = "error" if r.hard else "warning"
        if r.kind not in REQUEST_KINDS:
            issues.append(Issue("error", "request_kind",
                                f"{nurse.name}: tip de cerere necunoscut {r.kind!r}.", r.nurse_id, r.date))
            continue
        if r.date.year != inp.year or r.date.month != inp.month:
            issues.append(Issue("error", "request_outside_month",
                                f"{nurse.name}: {label} este în afara lunii.", r.nurse_id, r.date))
            continue
        if nurse.is_head:
            issues.append(Issue("warning", "request_head",
                                f"{nurse.name} este asistenta șefă și programul ei este fix - {label} ignorată.",
                                r.nurse_id, r.date))
            continue
        if r.date in inp.leave_of(r.nurse_id):
            issues.append(Issue(level, "request_on_leave",
                                f"{nurse.name}: {label} cade în ziua ei de Co - {must}.", r.nurse_id, r.date))
            continue
        if r.date in blocked[r.nurse_id]:
            # Dropped like a request on a Co day: she is away across this
            # weekend, so the model has no variable to grant it with. Dropping it
            # here keeps the solver and the validator agreeing - otherwise a hard
            # request would be silently ignored by one and flagged by the other.
            issues.append(Issue(level, "request_on_leave_bridge",
                                f"{nurse.name}: {label} cade într-un weekend în care este plecată "
                                f"(Co de ambele părți) - {must}.", r.nurse_id, r.date))
            continue
        if r.kind == "N" and r.date in no_night[r.nurse_id]:
            # Same parity reason as request_on_leave_bridge: the model has no
            # NIGHT variable here, so leaving the request in would have the
            # solver ignore it while the validator flags it.
            issues.append(Issue(level, "request_night_before_leave",
                                f"{nurse.name}: {label} este cu o zi înainte de ziua ei de Co și ar "
                                f"intra în ea - {must}.", r.nurse_id, r.date))
            continue
        if r.kind == "K" and not nurse.kitchen_capable:
            issues.append(Issue(level, "request_kitchen",
                                f"{nurse.name}: {label} la bucătărie, dar nu poate lucra la bucătărie - {must}.",
                                r.nurse_id, r.date))
            continue
        key = (r.nurse_id, r.date)
        if key in seen:
            if seen[key] != r.kind:
                issues.append(Issue("error", "request_conflict",
                                    f"{nurse.name}: cereri în conflict pe {r.date} "
                                    f"({seen[key]} și {r.kind}).", r.nurse_id, r.date))
            continue
        seen[key] = r.kind
        kept.append(r)

    # contradictions between must-have requests
    cfg = inp.config
    hards = [r for r in kept if r.hard]
    # Daily capacity per requestable shift. The 8h slot is a single daily slot for
    # infirmiere (at most one), but for asistente it is request-only and per-nurse
    # independent, so any number can be requested on the same day.
    cap = {"Z": 2 + cfg.extra_day_cap, "N": 2 + cfg.extra_night_cap, "K": 1,
           "8": 1 if inp.staff_type == STAFF_AIDE else len(inp.nurses)}
    if inp.staff_type != STAFF_AIDE:
        # asistente: day capped at 4 (days carry the surplus), night exactly 2/day.
        cap["N"] = 2
    for (day, kind), count in Counter((r.date, r.kind) for r in hards if r.kind != "FREE").items():
        if count > cap[kind]:
            issues.append(Issue("error", "request_overbooked",
                                f"{count} cereri obligatorii {kind} pe {day}, dar plafonul zilnic este {cap[kind]}."))
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
                                    f"{name}: o noapte obligatorie pe {day} necesită 48h de odihnă - cererea "
                                    f"obligatorie din ziua/zilele următoare nu poate fi respectată.", nid, day))
            elif kind == "Z" and nxt1 not in (None, "FREE", "N"):
                issues.append(Issue("error", "request_rest",
                                    f"{name}: după o tură de zi obligatorie pe {day}, în ziua următoare poate urma "
                                    f"doar o noapte, nu {nxt1}.", nid, day))
    return kept, issues


def has_errors(issues: list[Issue]) -> bool:
    return any(i.level == "error" for i in issues)
