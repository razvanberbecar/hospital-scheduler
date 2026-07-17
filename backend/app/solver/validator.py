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
            V(Issue("violation", "unknown_nurse", f"Assignment for unknown nurse id {a.nurse_id}.",
                    nurse_id=a.nurse_id, date=a.date))
            continue
        if a.date.year != inp.year or a.date.month != inp.month:
            V(Issue("violation", "outside_month", f"{nurse.name}: assignment on {a.date} is outside the month.",
                    nurse_id=a.nurse_id, date=a.date))
            continue
        if a.date in inp.leave_of(a.nurse_id):
            V(Issue("violation", "leave_conflict",
                    f"{nurse.name}: assigned {a.shift.value} on {a.date} but that is a Co (leave) day.",
                    nurse_id=a.nurse_id, date=a.date))
        elif a.date in blocked_by_nurse[a.nurse_id]:
            V(Issue("violation", "leave_bridge",
                    f"{nurse.name}: assigned {a.shift.value} on {a.date} but she is on Co on the "
                    f"working day either side of it - she is away across this weekend and cannot "
                    f"be scheduled.", nurse_id=a.nurse_id, date=a.date))
        elif a.shift is Shift.NIGHT and a.date in no_night_by_nurse[a.nurse_id]:
            V(Issue("violation", "night_before_leave",
                    f"{nurse.name}: night shift on {a.date} runs until "
                    f"{NIGHT_ENDS_AT[inp.staff_type]} on {a.date + dt.timedelta(days=1)}, which "
                    f"is her Co day - the day before a leave day cannot be a night.",
                    nurse_id=a.nurse_id, date=a.date))
        expected = cal.duration_of(a.shift, a.duration if a.shift is Shift.TOPUP else 0)
        if a.shift is Shift.TOPUP:
            if a.duration not in TOPUP_DURATIONS:
                V(Issue("violation", "topup_rules",
                        f"{nurse.name}: top-up of {a.duration}h on {a.date}; only 4h or 8h are allowed.",
                        nurse_id=a.nurse_id, date=a.date))
            if cal.is_offday(a.date, holidays):
                V(Issue("violation", "topup_rules",
                        f"{nurse.name}: top-up on {a.date} is invalid - top-ups only on working days.",
                        nurse_id=a.nurse_id, date=a.date))
        elif a.duration != expected:
            V(Issue("violation", "bad_duration",
                    f"{nurse.name}: {a.shift.value} on {a.date} recorded with {a.duration}h "
                    f"(expected {expected}h).", nurse_id=a.nurse_id, date=a.date))
        nh, wh = cal.shift_hours(a.date, a.shift, a.duration, holidays, inp.staff_type)
        if (a.normal_hours, a.weekend_hours) != (nh, wh):
            V(Issue("violation", "bad_bucket_split",
                    f"{nurse.name}: {a.shift.value} on {a.date} stored as {a.normal_hours}/{a.weekend_hours} "
                    f"(normal/weekend) but the calendar says {nh}/{wh}.", nurse_id=a.nurse_id, date=a.date))
        # shift legality per staff type: K/H never appear on an infirmiere
        # schedule; the 8h shift never appears on a nurse schedule.
        if is_aide:
            if a.shift in (Shift.KITCHEN, Shift.HEAD):
                V(Issue("violation", "shift_not_allowed",
                        f"{nurse.name}: {a.shift.value} on {a.date} is not allowed on an infirmiere "
                        f"schedule (no kitchen, no head).", nurse_id=a.nurse_id, date=a.date))
        else:
            if a.shift is Shift.EIGHT:
                V(Issue("violation", "shift_not_allowed",
                        f"{nurse.name}: the 8h shift on {a.date} is only for infirmiere schedules.",
                        nurse_id=a.nurse_id, date=a.date))
            if a.shift is Shift.HEAD and not nurse.is_head:
                V(Issue("violation", "head_pattern",
                        f"{nurse.name}: head-nurse shift assigned to a non-head nurse on {a.date}.",
                        nurse_id=a.nurse_id, date=a.date))
            if a.shift is Shift.KITCHEN and not nurse.kitchen_capable:
                V(Issue("violation", "kitchen_capability",
                        f"{nurse.name}: kitchen shift on {a.date} but she is not kitchen-capable.",
                        nurse_id=a.nurse_id, date=a.date))

    # one shift per nurse per day; at most one top-up per month
    for (nid, day), lst in by_nurse_day.items():
        if len(lst) > 1:
            V(Issue("violation", "double_shift",
                    f"{by_id[nid].name}: {len(lst)} shifts on {day}.", nurse_id=nid, date=day))
    for n in inp.nurses:
        tops = [a for a in assignments if a.nurse_id == n.id and a.shift is Shift.TOPUP]
        if len(tops) > 1:
            V(Issue("violation", "topup_rules",
                    f"{n.name}: {len(tops)} top-up shifts in the month; at most one is allowed.",
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
                        f"{n.name}: works {today.value} on {day} but had a night shift the "
                        f"previous day - the first day after a night shift can never be "
                        f"worked (24h rest).", nurse_id=n.id, date=day))
            elif prev2 is Shift.NIGHT:
                # the second rest day is now a penalized exception, not a hard
                # rule (plan 1): report it as a warning, do not block approval.
                two_before = day - dt.timedelta(days=2)
                W(Issue("warning", "rest48_exception",
                        f"{n.name}: works {today.value} on {day}, only one full day after the "
                        f"night of {two_before} - 48h-rest exception (allowed, keep rare).",
                        nurse_id=n.id, date=day))
            elif prev1 is Shift.DAY and today is not Shift.NIGHT:
                V(Issue("violation", "rest_after_day",
                        f"{n.name}: {today.value} on {day} starts less than 24h after the previous day shift - "
                        f"only a night shift may follow a day shift on the next day.", nurse_id=n.id, date=day))

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
                        f"{day}: no night infirmiera; one night shift is required every day.", date=day))
            if zc < 1:
                V(Issue("violation", "coverage_day",
                        f"{day}: no day (Z) infirmiera; at least one is required every day.", date=day))
            if ec > 1:
                V(Issue("violation", "coverage_eight",
                        f"{day}: {ec} eight-hour shifts; at most one is allowed.", date=day))
            # neither 1Z+1N+1x8 nor 2Z+1N: the day slot is unfilled
            if ec + min(zc, 2) < 2:
                V(Issue("violation", "coverage_eight",
                        f"{day}: the daytime slot is unfilled - a day needs 1 Z + 1 N + 1 x 8h, or "
                        f"the 2 Z + 1 N exception.", date=day))
            if len(nn) > 1 + cfg.extra_night_cap:
                V(Issue("violation", "coverage_night",
                        f"{day}: {len(nn)} night infirmiere exceeds the cap of {1 + cfg.extra_night_cap}.",
                        date=day))
            if zc > 2 + cfg.extra_day_cap:
                V(Issue("violation", "coverage_day",
                        f"{day}: {zc} day infirmiere exceeds the cap of {2 + cfg.extra_day_cap}.", date=day))
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
                        f"{day}: {len(z)} day nurses exceeds the cap of {2 + cfg.extra_day_cap}.", date=day))
            if len(nn) > 2 + cfg.extra_night_cap:
                V(Issue("violation", "coverage_night",
                        f"{day}: {len(nn)} night nurses exceeds the cap of {2 + cfg.extra_night_cap}.", date=day))
            if len(k) != 1:
                V(Issue("violation", "coverage_kitchen",
                        f"{day}: {len(k)} kitchen nurse(s); exactly 1 is required every day.", date=day))
            for label, code, counts in (("day", "coverage_day_section", zc),
                                        ("night", "coverage_night_section", nc)):
                for section in (BIG, SMALL):
                    if counts[section] < 1:
                        V(Issue("violation", code,
                                f"{day}: no {label} nurse from {SECTION_LABEL[section]} - each section "
                                f"needs one {label} shift every day.", date=day))

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
                            f"{h.name} (head nurse) has no shift on working day {day}.",
                            nurse_id=h.id, date=day))

    # --- per-nurse totals and hour rules --------------------------------------
    report.totals = compute_totals(inp, assignments)
    night_min = per_nurse_night_minimum(inp, holidays)
    for n in inp.nurses:
        t = report.totals[n.id]
        if t.worked_weekend > t.worked_normal:
            V(Issue("violation", "weekend_gt_normal",
                    f"{n.name}: worked weekend/holiday hours ({t.worked_weekend}) exceed worked normal hours "
                    f"({t.worked_normal}) - never allowed (Co credit does not count here).", nurse_id=n.id))
        if abs(t.owed) > cfg.band:
            V(Issue("violation", "target_band",
                    f"{n.name}: total {t.total}h vs monthly target {t.effective_target}h is off by "
                    f"{t.owed:+d}h - outside the +/-{cfg.band}h tolerance.", nurse_id=n.id))
        elif t.owed != 0:
            W(Issue("warning", "target_missed",
                    f"{n.name}: did not hit the monthly target by {t.owed:+d}h "
                    f"(total {t.total}h vs target {t.effective_target}h).", nurse_id=n.id))
        if not n.is_head:
            minimum, relaxed = night_min[n.id]
            if t.nights < minimum:
                V(Issue("violation", "nights_min",
                        f"{n.name}: {t.nights} night shift(s); the minimum this month is {minimum}.",
                        nurse_id=n.id))
            elif relaxed:
                W(Issue("warning", "nights_relaxed",
                        f"{n.name}: night minimum relaxed to {minimum} because of leave; she has {t.nights}.",
                        nurse_id=n.id))

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
                    f"{nurse.name}: must-have request {r.kind} on {r.date} is not honored.",
                    r.nurse_id, r.date))
        else:
            W(Issue("warning", "request_denied",
                    f"{nurse.name}: request {r.kind} on {r.date} was not honored.",
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
                        f"{n.name}: works {window} of the 7 days starting {day}.", nurse_id=n.id, date=day))
                break
        kitchen_days = sorted(a.date for a in assignments if a.nurse_id == n.id and a.shift is Shift.KITCHEN)
        run = 1
        for prev, cur in zip(kitchen_days, kitchen_days[1:]):
            run = run + 1 if (cur - prev).days == 1 else 1
            if run == 4:
                W(Issue("warning", "kitchen_run",
                        f"{n.name}: 4 or more consecutive kitchen days ending {cur}.", nurse_id=n.id, date=cur))
                break
    return report
