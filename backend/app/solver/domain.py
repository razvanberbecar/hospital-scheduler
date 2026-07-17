"""Core domain types for the scheduling solver.

Terminology follows the planning document (Hospital_Scheduling_Plan.docx v0.5):
- sections: Copii Mari (BIG) / Sugari (SMALL)
- shifts: Z (day), N (night), K (kitchen 8h), H (head-nurse 8h), T (top-up 4-8h),
  8 (infirmiere 8h day slot). Clock times differ by staff type - asistente work
  07-19/19-07, infirmiere 06-18/18-06 - see NIGHT_SPLIT.

Months no longer carry any hour debt: every month stands alone and each nurse
simply aims for the schedule's base target. The signed distance to that target
(owed = total - target) is still reported and, beyond Config.band, blocks
approval - but nothing rolls into the next month.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum

BIG = "BIG"      # Copii Mari
SMALL = "SMALL"  # Sugari
SECTION_LABEL = {BIG: "Copii Mari", SMALL: "Sugari"}

# Staff types scheduled completely separately (infirmiere plan 2026-07-14):
# asistente (nurses, everything historical) and infirmiere (nurse aides).
STAFF_NURSE = "asistenta"
STAFF_AIDE = "infirmiera"


class Shift(str, Enum):
    # Clock times below are the asistente ones; infirmiere run an hour earlier
    # (see NIGHT_SPLIT). Only the night's times reach the arithmetic.
    DAY = "Z"       # 12h: asistente 07:00-19:00, infirmiere 06:00-18:00
    NIGHT = "N"     # 12h across midnight: asistente 19:00-07:00 (5h/7h),
                    # infirmiere 18:00-06:00 (6h/6h)
    KITCHEN = "K"   # 8h kitchen slot, exactly one every day, kitchen-capable only
    HEAD = "H"      # 8h head-nurse shift, every working day, head nurse only
    TOPUP = "T"     # 4h or 8h gap-closing shift (infirmiere 4h = 06:00-10:00),
                    # max one per nurse per month
    EIGHT = "8"     # 8h daytime shift (infirmiere 06:00-14:00), every calendar day


FIXED_DURATION = {Shift.DAY: 12, Shift.NIGHT: 12, Shift.KITCHEN: 8,
                  Shift.HEAD: 8, Shift.EIGHT: 8}
TOPUP_DURATIONS = (4, 8)
REQUEST_KINDS = ("Z", "N", "K", "FREE")

# Clock times differ by staff type (decided 2026-07-16):
#   asistente   Z 07:00-19:00, N 19:00-07:00
#   infirmiere  Z 06:00-18:00, N 18:00-06:00, 8h slot 06:00-14:00, 4h top-up 06:00-10:00
# Only the night needs modelling. It is the one shift that crosses midnight, so
# its 12h fall in two different calendar days, and which side they land on picks
# their normal/weekend bucket. Every other shift sits inside a single day, so
# only its duration matters and its clock times never reach the arithmetic.
NIGHT_SPLIT = {
    STAFF_NURSE: (5, 7),   # 19:00-24:00 = 5h that day, 00:00-07:00 = 7h the next
    STAFF_AIDE: (6, 6),    # 18:00-24:00 = 6h that day, 00:00-06:00 = 6h the next
}
NIGHT_ENDS_AT = {STAFF_NURSE: "07:00", STAFF_AIDE: "06:00"}


@dataclass(frozen=True)
class ShiftRequest:
    """A nurse's wish for a specific day, entered by the manager together with
    the Co days before generation. Decided: soft by default (denied ones are
    reported), hard via the must-have flag; kinds Z / N / K / FREE; no monthly
    limit; the one-per-section rule and hour targets outrank requests."""
    nurse_id: int
    date: dt.date
    kind: str            # one of REQUEST_KINDS
    hard: bool = False


@dataclass(frozen=True)
class Nurse:
    id: int
    name: str
    section: str                  # BIG | SMALL
    role: str = "nurse"           # nurse | manager_nurse
    kitchen_capable: bool = False

    @property
    def is_head(self) -> bool:
        return self.role == "manager_nurse"


@dataclass
class Config:
    extra_day_cap: int = 2        # day-shift staffing allowed above the minimum of 2
    extra_night_cap: int = 2
    band: int = 12                # max |target deviation| the solver may leave per nurse
    min_nights: int = 3           # per non-head nurse, relaxed only when leave makes it impossible
    seed: int = 42
    max_time_s: float = 30.0
    workers: int = 8
    # objective weights, roughly an order of magnitude apart (priority list)
    w_dev: int = 1_000            # per hour of |target deviation|
    w_request: int = 500          # per denied request (below targets, above top-ups)
    w_topup: int = 300            # per top-up used
    w_wspread: int = 50           # weekend-hours spread across nurses
    w_nspread: int = 30           # night-count spread across nurses
    w_kitchen_run: int = 20       # >3 consecutive kitchen days
    w_cluster: int = 20           # >5 working days in any 7-day window
    w_kspread: int = 10           # kitchen-count spread among kitchen-capable
    # Cost per worked second-day-after-night (the 48h-rest exception, plan 1).
    # The first day after a night stays hard-blocked (24h floor); only the second
    # rest day is bendable. w_dev is 1000/hour, so an exception is taken only when
    # it saves >=2h of deviation or is forced by coverage/hard pins; a soft
    # request denial (w_request 500) stays cheaper, so wishes are dropped first.
    w_rest48: int = 2000
    # Infirmiere only: cost per day where the daily 8h slot is swapped for a
    # second Z (1Z+1N+1x8 -> 2Z+1N). w_dev is 1000/hour, so a swap (adds 4h of
    # capacity) is chosen only when it saves >=2h of total deviation or coverage
    # is otherwise infeasible from leave.
    w_eight_swap: int = 2000


@dataclass
class ScheduleInput:
    year: int
    month: int
    base_target: int
    nurses: list[Nurse]
    staff_type: str = STAFF_NURSE   # STAFF_NURSE | STAFF_AIDE (separate lifecycles)
    leave: dict[int, set[dt.date]] = field(default_factory=dict)          # Co dates per nurse id
    prev_tail: dict[int, dict[dt.date, Shift]] = field(default_factory=dict)  # last 2 days of prev month
    requests: list[ShiftRequest] = field(default_factory=list)
    holidays: set[dt.date] | None = None    # None -> Romanian public holidays computed automatically
    config: Config = field(default_factory=Config)
    locks: list[Assignment] = field(default_factory=list)  # cells pinned before solving (M3 editor)

    def effective_target(self, nurse_id: int) -> int:
        """The target every nurse aims for. Months no longer carry hour debt, so
        this is simply the schedule's base target for everyone."""
        return self.base_target

    def leave_of(self, nurse_id: int) -> set[dt.date]:
        return self.leave.get(nurse_id, set())

    def blocked_of(self, nurse_id: int, holidays: set[dt.date]) -> set[dt.date]:
        """Off days bracketed by this nurse's own Co on both sides (see
        calendar.bridged_offdays): she is away across them, so they are
        unworkable. Unlike leave_of they earn no credit and are derived, never
        stored - days_off stays the single source of truth."""
        from . import calendar as cal  # local: calendar imports domain
        return cal.bridged_offdays(self.year, self.month, holidays, self.leave_of(nurse_id))

    def unavailable_of(self, nurse_id: int, holidays: set[dt.date]) -> set[dt.date]:
        """Every day the nurse cannot be given a shift: her leave plus the off
        days her leave bridges."""
        return self.leave_of(nurse_id) | self.blocked_of(nurse_id, holidays)

    def night_blocked_of(self, nurse_id: int) -> set[dt.date]:
        """Days this nurse may not work a NIGHT: the day before each Co day.

        A night crosses midnight (asistente 19:00-07:00, infirmiere 18:00-06:00),
        so it spends its last hours on the following day - see NIGHT_SPLIT. A
        night the day before a Co day would have her working her own leave day.
        Only the night is forbidden: a Z, K, 8 or top-up on that day ends before
        midnight and is fine.

        Within the month only (decided): a Co on the 1st says nothing about the
        previous month's last day, which is usually locked and uncorrectable.
        """
        first = dt.date(self.year, self.month, 1)
        eve = {d - dt.timedelta(days=1) for d in self.leave_of(nurse_id)}
        return {d for d in eve if d >= first}


@dataclass(frozen=True)
class Assignment:
    nurse_id: int
    date: dt.date
    shift: Shift
    duration: int
    normal_hours: int
    weekend_hours: int


@dataclass
class Issue:
    level: str      # "error" | "violation" | "warning" | "info"
    code: str
    message: str
    nurse_id: int | None = None
    date: dt.date | None = None
