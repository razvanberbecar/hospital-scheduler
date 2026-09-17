"""The shared validator (plan sections 6.7 / 8): one module, four callers.

Powers the post-generate report, per-edit validation, the on-demand Check
button and the approval gate. It re-derives every rule from the assignment
list independently of the CP-SAT model, which is what makes the
validator-parity test meaningful.

"violation" = hard rule broken (blocks approval); "warning" = allowed but
reported (a target missed within tolerance, relaxed nights, clustering,
kitchen runs, a head-nurse working day without a shift).
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from . import calendar as cal
from .domain import (BIG, NIGHT_ENDS_AT, SECTION_LABEL, SMALL, STAFF_AIDE,
                     TOPUP_DURATIONS, Assignment, Issue, Nurse, ScheduleInput,
                     Shift)
from .prechecks import REQUEST_SHIFT, effective_requests, per_nurse_night_minimum


@dataclass
class NurseTotals:
    worked_normal: int = 0
    worked_weekend: int = 0
    leave_days: int = 0
    nights: int = 0
    kitchens: int = 0
    topup_hours: int = 0
    effective_target: int = 0

    @property
    def worked(self) -> int:
        return self.worked_normal + self.worked_weekend

    @property
    def leave_credit(self) -> int:
        return 8 * self.leave_days

    @property
    def total(self) -> int:
        return self.worked + self.leave_credit

    @property
    def owed(self) -> int:
        """Signed distance to the target: + means the nurse fell short of it,
        - means she went over. Reported for the month only; nothing carries."""
        return self.effective_target - self.total


@dataclass
class Report:
    violations: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    totals: dict[int, NurseTotals] = field(default_factory=dict)
    per_day: dict[dt.date, dict[str, int]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.violations


def compute_totals(inp: ScheduleInput, assignments: list[Assignment]) -> dict[int, NurseTotals]:
    totals = {n.id: NurseTotals(leave_days=len(inp.leave_of(n.id)),
                                effective_target=inp.effective_target(n.id))
              for n in inp.nurses}
    for a in assignments:
        t = totals[a.nurse_id]
        t.worked_normal += a.normal_hours
        t.worked_weekend += a.weekend_hours
        if a.shift is Shift.NIGHT:
            t.nights += 1
        elif a.shift is Shift.KITCHEN:
            t.kitchens += 1
        elif a.shift is Shift.TOPUP:
            t.topup_hours += a.duration
    return totals


def validate(inp: ScheduleInput, assignments: list[Assignment]) -> Report:
    cfg = inp.config
    is_aide = inp.staff_type == STAFF_AIDE
    holidays = inp.holidays if inp.holidays is not None else cal.default_holidays(inp.year, inp.month)
    dates = cal.month_dates(inp.year, inp.month)
    by_id: dict[int, Nurse] = {n.id: n for n in inp.nurses}
    report = Report()
    V = report.violations.append
    W = report.warnings.append

    # Off days each nurse's own Co brackets on both sides: unworkable, and unlike
    # Co they earn no credit, so they never touch the totals below.
    blocked_by_nurse = {n.id: inp.blocked_of(n.id, holidays) for n in inp.nurses}
    # Days whose NIGHT would run 7h into a Co day (the eve of each leave day).
    no_night_by_nurse = {n.id: inp.night_blocked_of(n.id) for n in inp.nurses}

    by_nurse_day: dict[tuple[int, dt.date], list[Assignment]] = defaultdict(list)
    by_day_shift: dict[tuple[dt.date, Shift], list[Assignment]] = defaultdict(list)
    for a in assignments:
        by_nurse_day[a.nurse_id, a.date].append(a)
        by_day_shift[a.date, a.shift].append(a)

    # --- structural checks --------------------------------------------------
    for a in assignments:
        nurse = by_id.get(a.nurse_id)
        if nurse is None:
            V(Issue("violation", "unknown_nurse", f"Repartizare pentru o asistentă necunoscută (id {a.nurse_id}).",
                    nurse_id=a.nurse_id, date=a.date))
            continue
        if a.date.year != inp.year or a.date.month != inp.month:
            V(Issue("violation", "outside_month", f"{nurse.name}: repartizarea pe {a.date} este în afara lunii.",
                    nurse_id=a.nurse_id, date=a.date))
            continue
        if a.date in inp.leave_of(a.nurse_id):
            V(Issue("violation", "leave_conflict",
                    f"{nurse.name}: repartizată {a.shift.value} pe {a.date}, dar aceea este o zi de Co (concediu).",
                    nurse_id=a.nurse_id, date=a.date))
        elif a.date in blocked_by_nurse[a.nurse_id]:
            V(Issue("violation", "leave_bridge",
                    f"{nurse.name}: repartizată {a.shift.value} pe {a.date}, dar are Co în ziua "
                    f"lucrătoare de ambele părți - este plecată peste acest weekend și nu poate "
                    f"fi programată.", nurse_id=a.nurse_id, date=a.date))
        elif a.shift is Shift.NIGHT and a.date in no_night_by_nurse[a.nurse_id]:
            V(Issue("violation", "night_before_leave",
                    f"{nurse.name}: tura de noapte pe {a.date} se termină la "
                    f"{NIGHT_ENDS_AT[inp.staff_type]} pe {a.date + dt.timedelta(days=1)}, care "
                    f"este ziua ei de Co - ziua dinaintea unei zile de concediu nu poate fi noapte.",
                    nurse_id=a.nurse_id, date=a.date))
        expected = cal.duration_of(a.shift, a.duration if a.shift is Shift.TOPUP else 0)
        if a.shift is Shift.TOPUP:
            if a.duration not in TOPUP_DURATIONS:
                V(Issue("violation", "topup_rules",
                        f"{nurse.name}: completare de {a.duration}h pe {a.date}; sunt permise doar 4h sau 8h.",
                        nurse_id=a.nurse_id, date=a.date))
            if cal.is_offday(a.date, holidays):
                V(Issue("violation", "topup_rules",
                        f"{nurse.name}: completarea pe {a.date} este invalidă - completările se pun doar în zilele lucrătoare.",
                        nurse_id=a.nurse_id, date=a.date))
        elif a.duration != expected:
            V(Issue("violation", "bad_duration",
                    f"{nurse.name}: {a.shift.value} pe {a.date} înregistrată cu {a.duration}h "
                    f"(așteptat {expected}h).", nurse_id=a.nurse_id, date=a.date))
        nh, wh = cal.shift_hours(a.date, a.shift, a.duration, holidays, inp.staff_type)
        if (a.normal_hours, a.weekend_hours) != (nh, wh):
            V(Issue("violation", "bad_bucket_split",
                    f"{nurse.name}: {a.shift.value} pe {a.date} stocată ca {a.normal_hours}/{a.weekend_hours} "
                    f"(normale/weekend), dar calendarul indică {nh}/{wh}.", nurse_id=a.nurse_id, date=a.date))
        # shift legality per staff type: K/H never appear on an infirmiere
        # schedule; the 8h shift never appears on a nurse schedule.
        if is_aide:
            if a.shift in (Shift.KITCHEN, Shift.HEAD):
                V(Issue("violation", "shift_not_allowed",
                        f"{nurse.name}: {a.shift.value} pe {a.date} nu este permisă pe un grafic de "
                        f"infirmiere (fără bucătărie, fără șefă).", nurse_id=a.nurse_id, date=a.date))
        else:
            # The 8h shift (Shift.EIGHT) is a legal asistente shift: the solver
            # only ever places it to honor an explicit 8h request, but as an
            # assignment it breaks no rule, so it is not flagged here.
            if a.shift is Shift.HEAD and not nurse.is_head:
                V(Issue("violation", "head_pattern",
                        f"{nurse.name}: tură de asistentă șefă repartizată unei asistente care nu este șefă pe {a.date}.",
                        nurse_id=a.nurse_id, date=a.date))
            if a.shift is Shift.KITCHEN and not nurse.kitchen_capable:
                V(Issue("violation", "kitchen_capability",
                        f"{nurse.name}: tură de bucătărie pe {a.date}, dar nu poate lucra la bucătărie.",
                        nurse_id=a.nurse_id, date=a.date))

    # one shift per nurse per day; at most one top-up per month
    for (nid, day), lst in by_nurse_day.items():
        if len(lst) > 1:
            V(Issue("violation", "double_shift",
                    f"{by_id[nid].name}: {len(lst)} ture pe {day}.", nurse_id=nid, date=day))
    for n in inp.nurses:
        tops = [a for a in assignments if a.nurse_id == n.id and a.shift is Shift.TOPUP]
        if len(tops) > 1:
            V(Issue("violation", "topup_rules",
                    f"{n.name}: {len(tops)} ture de completare în lună; este permisă cel mult una.",
                    nurse_id=n.id))

    # --- rest rules (incl. previous-month tail) ------------------------------
    def shift_on(nid: int, day: dt.date) -> Shift | None:
        if day.month == inp.month and day.year == inp.year:
            lst = by_nurse_day.get((nid, day))
            return lst[0].shift if lst else None
        return inp.prev_tail.get(nid, {}).get(day)

    for n in inp.nurses:
        for day in dates:
            today = shift_on(n.id, day)
            if today is None:
                continue
            prev1 = shift_on(n.id, day - dt.timedelta(days=1))
            prev2 = shift_on(n.id, day - dt.timedelta(days=2))
            if prev1 is Shift.NIGHT:
                # the 24h floor is absolute: the first day after a night is never
                # worked (plan 1), so this stays a violation blocking approval.
                V(Issue("violation", "rest_after_night",
                        f"{n.name}: lucrează {today.value} pe {day}, dar a avut tură de noapte în "
                        f"ziua precedentă - prima zi după o tură de noapte nu poate fi lucrată "
                        f"niciodată (odihnă de 24h).", nurse_id=n.id, date=day))
            elif prev2 is Shift.NIGHT:
                # the second rest day is now a penalized exception, not a hard
                # rule (plan 1): report it as a warning, do not block approval.
                two_before = day - dt.timedelta(days=2)
                W(Issue("warning", "rest48_exception",
                        f"{n.name}: lucrează {today.value} pe {day}, la doar o zi întreagă după "
                        f"noaptea din {two_before} - excepție de la odihna de 48h (permisă, dar rară).",
                        nurse_id=n.id, date=day))
            elif prev1 is Shift.DAY and today is not Shift.NIGHT:
                V(Issue("violation", "rest_after_day",
                        f"{n.name}: {today.value} pe {day} începe la mai puțin de 24h după tura de zi precedentă - "
                        f"după o tură de zi poate urma doar o tură de noapte în ziua următoare.", nurse_id=n.id, date=day))

    # --- coverage -------------------------------------------------------------
    if is_aide:
        # Infirmiere: each day must be 1Z + 1N + 1x8, or the accepted swap
        # exception 2Z + 1N (e == 0, z >= 2). A day that is neither is a
        # violation. Caps mirror the model (Z <= 2 + extra_day_cap once swapped,
        # N <= 1 + extra_night_cap).
        for day in dates:
            z = by_day_shift.get((day, Shift.DAY), [])
            nn = by_day_shift.get((day, Shift.NIGHT), [])
            e = by_day_shift.get((day, Shift.EIGHT), [])
            zc, ec = len(z), len(e)
            report.per_day[day] = {"Z": zc, "N": len(nn), "E8": ec}
            if len(nn) < 1:
                V(Issue("violation", "coverage_night",
                        f"{day}: nicio infirmieră de noapte; o tură de noapte este obligatorie în fiecare zi.", date=day))
            if zc < 1:
                V(Issue("violation", "coverage_day",
                        f"{day}: nicio infirmieră de zi (Z); cel puțin una este obligatorie în fiecare zi.", date=day))
            if ec > 1:
                V(Issue("violation", "coverage_eight",
                        f"{day}: {ec} ture de opt ore; este permisă cel mult una.", date=day))
            # neither 1Z+1N+1x8 nor 2Z+1N: the day slot is unfilled
            if ec + min(zc, 2) < 2:
                V(Issue("violation", "coverage_eight",
                        f"{day}: slotul de zi este neacoperit - o zi are nevoie de 1 Z + 1 N + 1 × 8h, sau "
                        f"excepția 2 Z + 1 N.", date=day))
            if len(nn) > 1 + cfg.extra_night_cap:
                V(Issue("violation", "coverage_night",
                        f"{day}: {len(nn)} infirmiere de noapte depășesc plafonul de {1 + cfg.extra_night_cap}.",
                        date=day))
            if zc > 2 + cfg.extra_day_cap:
                V(Issue("violation", "coverage_day",
                        f"{day}: {zc} infirmiere de zi depășesc plafonul de {2 + cfg.extra_day_cap}.", date=day))
    else:
        # HARD per-section day/night, exactly one kitchen. Every day needs one Z
        # and one N from EACH section (that is how the 2Z, 2N are formed). The
        # head nurse's own Z/N counts toward her section. Caps stay total-based.
        def section_counts(lst: list[Assignment]) -> dict[str, int]:
            counts = {BIG: 0, SMALL: 0}
            for a in lst:
                n = by_id.get(a.nurse_id)
                if n is not None and n.section in counts:
                    counts[n.section] += 1
            return counts

        for day in dates:
            z = by_day_shift.get((day, Shift.DAY), [])
            nn = by_day_shift.get((day, Shift.NIGHT), [])
            k = by_day_shift.get((day, Shift.KITCHEN), [])
            zc, nc = section_counts(z), section_counts(nn)
            report.per_day[day] = {
                "Z_big": zc[BIG], "Z_small": zc[SMALL],
                "N_big": nc[BIG], "N_small": nc[SMALL], "K": len(k)}
            if len(z) > 2 + cfg.extra_day_cap:
                V(Issue("violation", "coverage_day",
                        f"{day}: {len(z)} asistente de zi depășesc plafonul de {2 + cfg.extra_day_cap}.", date=day))
            if len(nn) > 2 + cfg.extra_night_cap:
                V(Issue("violation", "coverage_night",
                        f"{day}: {len(nn)} asistente de noapte depășesc plafonul de {2 + cfg.extra_night_cap}.", date=day))
            if len(k) != 1:
                V(Issue("violation", "coverage_kitchen",
                        f"{day}: {len(k)} asistentă(e) la bucătărie; exact 1 este obligatorie în fiecare zi.", date=day))
            for label, code, counts in (("de zi", "coverage_day_section", zc),
                                        ("de noapte", "coverage_night_section", nc)):
                for section in (BIG, SMALL):
                    if counts[section] < 1:
                        V(Issue("violation", code,
                                f"{day}: nicio asistentă {label} din {SECTION_LABEL[section]} - fiecare secție "
                                f"are nevoie de o tură {label} în fiecare zi.", date=day))

        # head nurse present every working day (unless on leave) - a warning now:
        # her cells are hand-editable and may deliberately be cleared.
        heads = [n for n in inp.nurses if n.is_head]
        for h in heads:
            for day in dates:
                if cal.is_offday(day, holidays) or day in inp.leave_of(h.id):
                    continue
                lst = by_nurse_day.get((h.id, day), [])
                if not lst:
                    W(Issue("warning", "head_missing",
                            f"{h.name} (asistentă șefă) nu are nicio tură în ziua lucrătoare {day}.",
                            nurse_id=h.id, date=day))

    # --- per-nurse totals and hour rules --------------------------------------
    report.totals = compute_totals(inp, assignments)
    night_min = per_nurse_night_minimum(inp, holidays)
    for n in inp.nurses:
        t = report.totals[n.id]
        if t.worked_weekend > t.worked_normal:
            V(Issue("violation", "weekend_gt_normal",
                    f"{n.name}: orele lucrate în weekend/sărbători ({t.worked_weekend}) depășesc orele normale lucrate "
                    f"({t.worked_normal}) - niciodată permis (creditul de Co nu se numără aici).", nurse_id=n.id))
        if abs(t.owed) > cfg.band:
            V(Issue("violation", "target_band",
                    f"{n.name}: totalul de {t.total}h față de ținta lunară de {t.effective_target}h diferă cu "
                    f"{t.owed:+d}h - în afara toleranței de ±{cfg.band}h.", nurse_id=n.id))
        elif t.owed != 0:
            W(Issue("warning", "target_missed",
                    f"{n.name}: nu a atins ținta lunară cu {t.owed:+d}h "
                    f"(total {t.total}h față de ținta de {t.effective_target}h).", nurse_id=n.id))
        if not n.is_head:
            minimum, relaxed = night_min[n.id]
            if t.nights < minimum:
                V(Issue("violation", "nights_min",
                        f"{n.name}: {t.nights} tură/ture de noapte; minimul lunii acesteia este {minimum}.",
                        nurse_id=n.id))
            elif relaxed:
                W(Issue("warning", "nights_relaxed",
                        f"{n.name}: minimul de nopți relaxat la {minimum} din cauza concediului; are {t.nights}.",
                        nurse_id=n.id))
            if cfg.weekend_floor and t.worked_weekend < cfg.weekend_floor:
                V(Issue("violation", "weekend_floor",
                        f"{n.name}: doar {t.worked_weekend}h la 100% (weekend/sărbători); minimul "
                        f"lunar este {cfg.weekend_floor}h pentru fiecare asistentă/infirmieră.",
                        nurse_id=n.id))

    # --- infirmiere run cap: no run of worked days longer than max_consec_aide ---
    # Walks each aide's worked days from two days before the month (prev tail) to
    # its last day; a run of more than the cap is flagged once, where it first
    # exceeds it. Co and off days reset the run.
    if is_aide and cfg.max_consec_aide:
        cap = cfg.max_consec_aide
        for n in inp.nurses:
            if n.is_head:
                continue
            run = 0
            day = dates[0] - dt.timedelta(days=2)
            while day <= dates[-1]:
                if shift_on(n.id, day) is not None:
                    run += 1
                    if run == cap + 1:
                        V(Issue("violation", "aide_run",
                                f"{n.name}: lucrează peste {cap} zile consecutiv (până pe {day}); "
                                f"infirmierele se țin pe ture scurte 8-Z-N, nu 8-8-Z-N.",
                                nurse_id=n.id, date=day))
                else:
                    run = 0
                day += dt.timedelta(days=1)

    # --- shift requests ----------------------------------------------------------
    kept_requests, _ = effective_requests(inp)
    assigned_shift = {(a.nurse_id, a.date): a.shift for a in assignments}
    for r in kept_requests:
        if r.kind == "FREE":
            granted = (r.nurse_id, r.date) not in assigned_shift
        else:
            granted = assigned_shift.get((r.nurse_id, r.date)) is REQUEST_SHIFT[r.kind]
        if granted:
            continue
        nurse = by_id[r.nurse_id]
        if r.hard:
            V(Issue("violation", "hard_request_violated",
                    f"{nurse.name}: cererea obligatorie {r.kind} pe {r.date} nu este respectată.",
                    r.nurse_id, r.date))
        else:
            W(Issue("warning", "request_denied",
                    f"{nurse.name}: cererea {r.kind} pe {r.date} nu a fost respectată.",
                    r.nurse_id, r.date))

    # --- soft-pattern warnings -------------------------------------------------
    for n in inp.nurses:
        if n.is_head:
            continue
        worked_days = sorted(d for (nid, d) in by_nurse_day if nid == n.id)
        worked_set = set(worked_days)
        for day in worked_days:
            window = sum(1 for k in range(7) if day + dt.timedelta(days=k) in worked_set)
            if window > 5:
                W(Issue("warning", "cluster",
                        f"{n.name}: lucrează {window} din cele 7 zile începând cu {day}.", nurse_id=n.id, date=day))
                break
        kitchen_days = sorted(a.date for a in assignments if a.nurse_id == n.id and a.shift is Shift.KITCHEN)
        run = 1
        for prev, cur in zip(kitchen_days, kitchen_days[1:]):
            run = run + 1 if (cur - prev).days == 1 else 1
            if run == 4:
                W(Issue("warning", "kitchen_run",
                        f"{n.name}: 4 sau mai multe zile consecutive de bucătărie care se termină pe {cur}.", nurse_id=n.id, date=cur))
                break
    return report
