"""Schedule endpoints (plan sections 5-8, milestone M3): the REST surface that
wires the CP-SAT solver and the shared validator.

Lifecycle: draft -> approved -> locked. Setup/generate/edit happen only while a
month is a draft. Approving month M permanently locks the approved month M-1;
a month may be re-approved - or reopened back to draft for corrections - only
while the following month is still a draft or absent (i.e. while it is merely
"approved", not "locked"). Every mutation of a locked month is rejected with 409. Months stand
alone: only the previous month's last two days (prev_tail) cross the boundary,
for the rest rules - no hour balance carries.

All endpoints are manager-only except the two read endpoints (list, detail),
which nurses may call for approved/locked months with their own totals only.
"""
from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..auth.deps import get_current_user, get_db, require_password_changed
from ..models.schemas import (ApprovalListItem, ApprovalOut,
                              ApprovalSnapshotOut, AssignmentOut,
                              AssignmentsPatch, DayCoverageOut, DayOffIn,
                              GenerateIn, IssueOut, NurseTotalsOut, RequestOut,
                              RosterEntryOut, ScheduleCreate,
                              ScheduleDetailOut, ScheduleListItem,
                              ScheduleSetupPut, SetupOut, ValidateOut)
from ..models.tables import (STAFF_AIDE, STATUS_APPROVED, STATUS_DRAFT,
                             STATUS_LOCKED, Approval, AssignmentRow, DayOff,
                             Schedule, ScheduleNurse, ShiftRequestRow, User)
from ..solver import calendar as cal
from ..solver.domain import (TOPUP_DURATIONS, Assignment, Config, Nurse,
                             ScheduleInput, Shift, ShiftRequest)
from ..solver.model import solve
from ..solver.prechecks import has_errors, run_prechecks
from ..solver.validator import NurseTotals, Report, validate

router = APIRouter(prefix="/api/schedules", tags=["schedules"])

Manager = Annotated[User, Depends(require_password_changed)]
AnyUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[Session, Depends(get_db)]


# --- domain <-> DB glue ----------------------------------------------------

def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _holidays(db: Session, year: int, month: int) -> set[dt.date]:
    """DB holiday rows for the month (plus day 1 of next month when month==12);
    fall back to the computed Romanian holidays when the year has no rows."""
    from ..models.tables import Holiday
    years = [year] + ([year + 1] if month == 12 else [])
    days: set[dt.date] = set()
    for y in years:
        rows = list(db.scalars(
            select(Holiday).where(Holiday.date >= dt.date(y, 1, 1),
                                  Holiday.date <= dt.date(y, 12, 31))))
        days |= {r.date for r in rows} if rows else cal.romanian_holidays(y)
    return days


def _solver_config(overrides: dict) -> Config:
    """A solver Config with only the known fields overridden (unknown keys are
    ignored so a stale UI payload cannot break generation)."""
    cfg = Config()
    for key, value in (overrides or {}).items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
    return cfg


def _roster_nurses(db: Session, schedule: Schedule) -> list[Nurse]:
    rows = list(db.scalars(select(ScheduleNurse).where(ScheduleNurse.schedule_id == schedule.id)))
    users = {u.id: u for u in db.scalars(
        select(User).where(User.id.in_([r.user_id for r in rows])))} if rows else {}
    nurses = []
    for r in rows:
        u = users.get(r.user_id)
        if u is None:
            continue
        # section is None for infirmiere (one pool); the solver only reads it on
        # nurse schedules, where it is always set.
        nurses.append(Nurse(id=u.id, name=u.full_name, section=u.section,
                            role=u.role, kitchen_capable=r.kitchen_capable))
    return nurses


def _sorted_roster(db: Session, schedule: Schedule) -> list[Nurse]:
    """Roster in the manager-controlled global order (users.position), the same
    order GET /api/users and the paper GRAFIC use. Feeds the grid roster, the
    setup echo, the History snapshot grid and the xlsx export. Interleaved
    sections are allowed and produce multiple section bands - intended, the
    owner controls the order."""
    positions = dict(db.execute(select(User.id, User.position)).all())
    return sorted(_roster_nurses(db, schedule),
                  key=lambda n: (positions.get(n.id, 0), n.name))


def _leave(db: Session, schedule: Schedule) -> dict[int, set[dt.date]]:
    out: dict[int, set[dt.date]] = {}
    for row in db.scalars(select(DayOff).where(DayOff.schedule_id == schedule.id)):
        out.setdefault(row.user_id, set()).add(row.date)
    return out


def _requests(db: Session, schedule: Schedule) -> list[ShiftRequestRow]:
    return list(db.scalars(
        select(ShiftRequestRow).where(ShiftRequestRow.schedule_id == schedule.id)
        .order_by(ShiftRequestRow.date, ShiftRequestRow.user_id)))


def _prev_tail(schedule: Schedule) -> dict[int, dict[dt.date, Shift]]:
    out: dict[int, dict[dt.date, Shift]] = {}
    for nid, day_map in (schedule.prev_tail or {}).items():
        out[int(nid)] = {dt.date.fromisoformat(d): Shift(code) for d, code in day_map.items()}
    return out


def _shift_requests(db: Session, schedule: Schedule) -> list[ShiftRequest]:
    return [ShiftRequest(r.user_id, r.date, r.kind, r.hard) for r in _requests(db, schedule)]


def _locks(db: Session, schedule: Schedule) -> list[Assignment]:
    rows = db.scalars(select(AssignmentRow).where(
        AssignmentRow.schedule_id == schedule.id, AssignmentRow.locked))
    return [_row_to_assignment(r) for r in rows]


def _row_to_assignment(r: AssignmentRow) -> Assignment:
    return Assignment(r.user_id, r.date, Shift(r.shift), r.duration_hours,
                      r.normal_hours, r.weekend_hours)


def _current_assignments(db: Session, schedule: Schedule) -> list[Assignment]:
    rows = db.scalars(select(AssignmentRow).where(AssignmentRow.schedule_id == schedule.id)
                      .order_by(AssignmentRow.date, AssignmentRow.user_id))
    return [_row_to_assignment(r) for r in rows]


def _build_input(db: Session, schedule: Schedule, *, with_locks: bool = False) -> ScheduleInput:
    """Assemble a ScheduleInput from the schedule's DB rows. Holidays come from
    the DB, prev_tail from the schedule's stored JSON."""
    return ScheduleInput(
        year=schedule.year, month=schedule.month, base_target=schedule.target_hours,
        nurses=_roster_nurses(db, schedule),
        staff_type=schedule.staff_type,
        leave=_leave(db, schedule),
        prev_tail=_prev_tail(schedule),
        requests=_shift_requests(db, schedule),
        holidays=_holidays(db, schedule.year, schedule.month),
        config=_solver_config(schedule.config),
        locks=_locks(db, schedule) if with_locks else [],
    )


# --- month chain -----------------------------------------------------------

def _latest_approval(db: Session, schedule_id: int) -> Approval | None:
    return db.scalars(select(Approval).where(Approval.schedule_id == schedule_id)
                      .order_by(Approval.version.desc())).first()


def _prev_tail_from_previous(db: Session, year: int, month: int, staff_type: str) -> dict:
    """prev_tail for a new month M drawn from M-1's stored assignments (the last
    two days' shift codes), used only for the cross-boundary rest rules. Empty
    when there is no previous schedule. Scoped to one staff type so the two
    lifecycles never draw tails from each other."""
    py, pm = _prev_month(year, month)
    prev = db.scalar(select(Schedule).where(
        Schedule.year == py, Schedule.month == pm, Schedule.staff_type == staff_type))
    if prev is None:
        return {}
    last_two = sorted(cal.month_dates(py, pm))[-2:]
    prev_tail: dict[str, dict[str, str]] = {}
    for row in db.scalars(select(AssignmentRow).where(
            AssignmentRow.schedule_id == prev.id, AssignmentRow.date.in_(last_two))):
        prev_tail.setdefault(str(row.user_id), {})[row.date.isoformat()] = row.shift
    return prev_tail


# --- fetch + guards --------------------------------------------------------

def _get_schedule(db: Session, schedule_id: int, user: User) -> Schedule:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise HTTPException(404, "No such schedule")
    if not user.is_manager and schedule.status == STATUS_DRAFT:
        raise HTTPException(404, "No such schedule")  # nurses never see drafts
    if not user.is_manager and schedule.staff_type != user.staff_type:
        raise HTTPException(404, "No such schedule")  # readers see only their own staff type
    return schedule


def _require_draft(schedule: Schedule) -> None:
    if schedule.status == STATUS_LOCKED:
        raise HTTPException(409, "This month is locked and can no longer be edited")
    if schedule.status != STATUS_DRAFT:
        raise HTTPException(409, "Only draft schedules can be edited; re-open is not this action")


# --- setup writers ---------------------------------------------------------

def _write_roster(db: Session, schedule: Schedule, roster, users: dict[int, User]) -> None:
    db.execute(delete(ScheduleNurse).where(ScheduleNurse.schedule_id == schedule.id))
    is_aide = schedule.staff_type == STAFF_AIDE
    for item in roster:
        u = users[item.user_id]
        # Infirmiere never have a kitchen slot, so the kitchen flag is forced off.
        if is_aide:
            kitchen = False
        else:
            kitchen = u.default_kitchen_capable if item.kitchen_capable is None else item.kitchen_capable
        db.add(ScheduleNurse(schedule_id=schedule.id, user_id=item.user_id, kitchen_capable=kitchen))


def _write_days_off(db: Session, schedule: Schedule, days_off) -> None:
    db.execute(delete(DayOff).where(DayOff.schedule_id == schedule.id))
    seen: set[tuple[int, dt.date]] = set()
    for item in days_off:
        if (item.user_id, item.date) in seen:
            continue
        seen.add((item.user_id, item.date))
        db.add(DayOff(schedule_id=schedule.id, user_id=item.user_id, date=item.date))


def _write_requests(db: Session, schedule: Schedule, requests) -> None:
    db.execute(delete(ShiftRequestRow).where(ShiftRequestRow.schedule_id == schedule.id))
    seen: set[tuple[int, dt.date]] = set()
    for item in requests:
        if (item.user_id, item.date) in seen:
            continue
        seen.add((item.user_id, item.date))
        db.add(ShiftRequestRow(schedule_id=schedule.id, user_id=item.user_id,
                               date=item.date, kind=item.kind, hard=item.hard))


def _validate_roster(db: Session, roster, staff_type: str) -> dict[int, User]:
    """Rostered users must exist, be active and share the schedule's staff type;
    the at-most-one-head rule applies to nurse schedules only (infirmiere have no
    head)."""
    ids = [item.user_id for item in roster]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "A user appears more than once on the roster")
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}
    for uid in ids:
        u = users.get(uid)
        if u is None:
            raise HTTPException(422, f"User id {uid} does not exist")
        if not u.active:
            raise HTTPException(422, f"User '{u.username}' is deactivated and cannot be rostered")
        if u.staff_type != staff_type:
            raise HTTPException(422,
                f"User '{u.username}' is a {u.staff_type}; this schedule is for {staff_type} only")
    if staff_type != STAFF_AIDE:
        heads = [uid for uid in ids if users[uid].is_manager]
        if len(heads) > 1:
            raise HTTPException(422, "At most one manager_nurse (head nurse) may be on the roster")
    return users


def _reject_bad_requests(requests, staff_type: str) -> None:
    """Kitchen (K) requests make no sense on an infirmiere schedule (no kitchen)."""
    if staff_type == STAFF_AIDE and any(r.kind == "K" for r in requests):
        raise HTTPException(422, "Kitchen (K) requests are not allowed on an infirmiere schedule")


# --- report / payload assembly ---------------------------------------------

def _validate_current(db: Session, schedule: Schedule) -> Report:
    inp = _build_input(db, schedule)
    return validate(inp, _current_assignments(db, schedule))


def _issue_out(i) -> IssueOut:
    return IssueOut(level=i.level, code=i.code, message=i.message,
                    user_id=i.nurse_id, date=i.date)


def _totals_out(totals: dict[int, NurseTotals]) -> list[NurseTotalsOut]:
    return [NurseTotalsOut(
        user_id=nid, worked_normal=t.worked_normal, worked_weekend=t.worked_weekend,
        leave_days=t.leave_days, leave_credit=t.leave_credit, worked=t.worked,
        total=t.total, effective_target=t.effective_target, owed=t.owed,
        nights=t.nights, kitchens=t.kitchens, topup_hours=t.topup_hours)
        for nid, t in sorted(totals.items())]


def _per_day_out(per_day: dict[dt.date, dict[str, int]], staff_type: str) -> list[DayCoverageOut]:
    if staff_type == STAFF_AIDE:
        return [DayCoverageOut(date=day, Z=c.get("Z", 0), N=c.get("N", 0), E8=c.get("E8", 0))
                for day, c in sorted(per_day.items())]
    return [DayCoverageOut(
        date=day, Z_big=c.get("Z_big", 0), Z_small=c.get("Z_small", 0),
        N_big=c.get("N_big", 0), N_small=c.get("N_small", 0), K=c.get("K", 0))
        for day, c in sorted(per_day.items())]


def _setup_out(db: Session, schedule: Schedule) -> SetupOut:
    roster = []
    for n in _sorted_roster(db, schedule):
        roster.append(RosterEntryOut(user_id=n.id, full_name=n.name, section=n.section,
                                     role=n.role, kitchen_capable=n.kitchen_capable))
    days_off = [DayOffIn(user_id=r.user_id, date=r.date)
                for r in db.scalars(select(DayOff).where(DayOff.schedule_id == schedule.id)
                                    .order_by(DayOff.user_id, DayOff.date))]
    # Derived from the same days_off rows the solver reads, so the grid greys the
    # exact days the model refuses to fill.
    holidays = _holidays(db, schedule.year, schedule.month)
    leave = _leave(db, schedule)
    blocked_days = [
        DayOffIn(user_id=uid, date=day)
        for uid in sorted(leave)
        for day in sorted(cal.bridged_offdays(schedule.year, schedule.month,
                                              holidays, leave[uid]))]
    requests = [RequestOut(user_id=r.user_id, date=r.date, kind=r.kind, hard=r.hard,
                           granted=r.granted) for r in _requests(db, schedule)]
    return SetupOut(
        target_hours=schedule.target_hours, roster=roster, days_off=days_off,
        blocked_days=blocked_days, requests=requests,
        prev_tail={nid: {d.isoformat(): s.value for d, s in day_map.items()}
                   for nid, day_map in _prev_tail(schedule).items()},
        config=schedule.config or {})


def _assignments_out(assignments: list[Assignment], locked: set[tuple[int, dt.date]]) -> list[AssignmentOut]:
    return [AssignmentOut(
        user_id=a.nurse_id, date=a.date, shift=a.shift.value, duration_hours=a.duration,
        normal_hours=a.normal_hours, weekend_hours=a.weekend_hours,
        locked=(a.nurse_id, a.date) in locked) for a in assignments]


def _detail(db: Session, schedule: Schedule, viewer: User, **solver_echo) -> ScheduleDetailOut:
    """Full detail payload (GET and the /generate response share this). Nurses
    see the whole grid but only their own totals."""
    assignments = _current_assignments(db, schedule)
    locked = {(r.user_id, r.date) for r in db.scalars(select(AssignmentRow).where(
        AssignmentRow.schedule_id == schedule.id, AssignmentRow.locked))}
    report = _validate_current(db, schedule)
    totals = report.totals
    if not viewer.is_manager:
        totals = {nid: t for nid, t in totals.items() if nid == viewer.id}
    approval_count = len(list(db.scalars(
        select(Approval.id).where(Approval.schedule_id == schedule.id))))
    return ScheduleDetailOut(
        id=schedule.id, year=schedule.year, month=schedule.month,
        staff_type=schedule.staff_type, status=schedule.status,
        setup=_setup_out(db, schedule),
        assignments=_assignments_out(assignments, locked),
        totals=_totals_out(totals),
        per_day=_per_day_out(report.per_day, schedule.staff_type),
        violations=[_issue_out(i) for i in report.violations],
        warnings=[_issue_out(i) for i in report.warnings],
        approval_count=approval_count,
        **solver_echo)


# --- endpoints -------------------------------------------------------------

@router.post("", response_model=ScheduleDetailOut, status_code=201)
def create_schedule(body: ScheduleCreate, db: Db, manager: Manager) -> ScheduleDetailOut:
    """Create a draft month (plan section 5). Auto-fills prev_tail (the previous
    month's last two days) from the stored grid for the cross-boundary rest
    rules; no hour balance carries between months."""
    if db.scalar(select(Schedule).where(Schedule.year == body.year, Schedule.month == body.month,
                                        Schedule.staff_type == body.staff_type)):
        raise HTTPException(409,
            f"A {body.staff_type} schedule for {body.month:02d}/{body.year} already exists")
    users = _validate_roster(db, body.roster, body.staff_type)
    _reject_bad_requests(body.requests, body.staff_type)
    prev_tail = _prev_tail_from_previous(db, body.year, body.month, body.staff_type)
    schedule = Schedule(
        year=body.year, month=body.month, staff_type=body.staff_type,
        target_hours=body.target_hours,
        status=STATUS_DRAFT, prev_tail=prev_tail, config=body.config or {})
    db.add(schedule)
    db.flush()
    _write_roster(db, schedule, body.roster, users)
    _write_days_off(db, schedule, body.days_off)
    _write_requests(db, schedule, body.requests)
    db.commit()
    db.refresh(schedule)
    return _detail(db, schedule, manager)


@router.get("", response_model=list[ScheduleListItem])
def list_schedules(db: Db, user: AnyUser,
                   year: Annotated[int | None, Query(ge=2000, le=2100)] = None) -> list[ScheduleListItem]:
    q = select(Schedule)
    if year is not None:
        q = q.where(Schedule.year == year)
    if not user.is_manager:
        q = q.where(Schedule.status.in_([STATUS_APPROVED, STATUS_LOCKED]),
                    Schedule.staff_type == user.staff_type)
    schedules = list(db.scalars(q.order_by(Schedule.year, Schedule.month)))
    out = []
    for s in schedules:
        count = len(list(db.scalars(select(Approval.id).where(Approval.schedule_id == s.id))))
        out.append(ScheduleListItem(id=s.id, year=s.year, month=s.month, staff_type=s.staff_type,
                                    status=s.status, target_hours=s.target_hours,
                                    approval_count=count))
    return out


@router.get("/{schedule_id}", response_model=ScheduleDetailOut)
def get_schedule(schedule_id: int, db: Db, user: AnyUser) -> ScheduleDetailOut:
    schedule = _get_schedule(db, schedule_id, user)
    return _detail(db, schedule, user)


@router.delete("/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, db: Db, manager: Manager) -> Response:
    """Delete a draft month and all of its children (start over). Allowed only
    while the month is a draft; an approved or locked month is refused (409).
    A reopened draft loses its approval history - deliberate (the UI warns)."""
    schedule = _get_schedule(db, schedule_id, manager)
    if schedule.status != STATUS_DRAFT:
        raise HTTPException(409, "Only draft schedules can be deleted; approved or locked months are kept")
    for table in (AssignmentRow, DayOff, ShiftRequestRow, ScheduleNurse, Approval):
        db.execute(delete(table).where(table.schedule_id == schedule.id))
    db.delete(schedule)
    db.commit()
    return Response(status_code=204)


XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/{schedule_id}/export.xlsx")
def export_xlsx(schedule_id: int, db: Db, manager: Manager) -> Response:
    """Download the month's GRAFIC as an .xlsx (manager-only, any status). The
    workbook mirrors the ward's paper sheet: nurse rows grouped by section,
    weekend/holiday shading, per-nurse totals and coverage rows."""
    from ..export.xlsx import build_workbook

    schedule = _get_schedule(db, schedule_id, manager)
    report = _validate_current(db, schedule)
    content = build_workbook(
        year=schedule.year, month=schedule.month,
        staff_type=schedule.staff_type,
        nurses=_sorted_roster(db, schedule),
        assignments=_current_assignments(db, schedule),
        leave=_leave(db, schedule),
        holidays=_holidays(db, schedule.year, schedule.month),
        totals=report.totals,
        per_day=report.per_day,
    )
    stem = "Pediatrie_Infirmiere" if schedule.staff_type == STAFF_AIDE else "Pediatrie"
    filename = f"GRAFIC_{stem}_{schedule.year}-{schedule.month:02d}.xlsx"
    return Response(content=content, media_type=XLSX_MEDIA_TYPE,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.put("/{schedule_id}/setup", response_model=ScheduleDetailOut)
def update_setup(schedule_id: int, body: ScheduleSetupPut, db: Db, manager: Manager) -> ScheduleDetailOut:
    """Replace roster/days off/requests/target/prev_tail/config while draft. Any
    setup change may invalidate the grid, so - deliberately, for simplicity -
    all existing assignments are deleted here (documented)."""
    schedule = _get_schedule(db, schedule_id, manager)
    _require_draft(schedule)
    users = _validate_roster(db, body.roster, schedule.staff_type)
    _reject_bad_requests(body.requests, schedule.staff_type)
    schedule.target_hours = body.target_hours
    schedule.prev_tail = {str(nid): {d.isoformat(): code for d, code in day_map.items()}
                          for nid, day_map in body.prev_tail.items()}
    schedule.config = body.config or {}
    _write_roster(db, schedule, body.roster, users)
    _write_days_off(db, schedule, body.days_off)
    _write_requests(db, schedule, body.requests)
    db.execute(delete(AssignmentRow).where(AssignmentRow.schedule_id == schedule.id))
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return _detail(db, schedule, manager)


@router.post("/{schedule_id}/generate", response_model=ScheduleDetailOut)
def generate(schedule_id: int, body: GenerateIn, db: Db, manager: Manager) -> ScheduleDetailOut:
    """Run pre-checks then the CP-SAT solver (plan section 6). respect_locks
    rebuilds locks from the currently-locked cells so they survive."""
    schedule = _get_schedule(db, schedule_id, manager)
    _require_draft(schedule)
    inp = _build_input(db, schedule, with_locks=body.respect_locks)

    prechecks = run_prechecks(inp)
    if has_errors(prechecks):
        raise HTTPException(409, {"prechecks": [_issue_out(i).model_dump(mode="json")
                                                for i in prechecks if i.level == "error"]})
    result = solve(inp, skip_prechecks=True)
    if not result.ok:
        raise HTTPException(409, {"solver_status": result.status, "diagnosis": result.diagnosis})

    # Replace the grid, keeping the locked cells the solver was told to pin.
    kept = {(a.nurse_id, a.date) for a in inp.locks} if body.respect_locks else set()
    clear = delete(AssignmentRow).where(AssignmentRow.schedule_id == schedule.id)
    if body.respect_locks:
        clear = clear.where(AssignmentRow.locked.is_(False))
    db.execute(clear)
    for a in result.assignments:
        if (a.nurse_id, a.date) in kept:
            continue  # the locked row already exists and matches
        db.add(AssignmentRow(
            schedule_id=schedule.id, user_id=a.nurse_id, date=a.date, shift=a.shift.value,
            duration_hours=a.duration, normal_hours=a.normal_hours,
            weekend_hours=a.weekend_hours, locked=False))

    granted = {(r.nurse_id, r.date): ok for r, ok in result.requests}
    for row in _requests(db, schedule):
        row.granted = granted.get((row.user_id, row.date))
        db.add(row)
    db.commit()
    db.refresh(schedule)
    return _detail(db, schedule, manager,
                   solver_status=result.status, objective=result.objective,
                   relaxed_nights=result.relaxed_nights)


@router.post("/{schedule_id}/validate", response_model=ValidateOut)
def validate_schedule(schedule_id: int, db: Db, manager: Manager) -> ValidateOut:
    """The Check button (plan 6.7): validate the current DB state, draft or
    approved."""
    schedule = _get_schedule(db, schedule_id, manager)
    report = _validate_current(db, schedule)
    return ValidateOut(
        ok=report.ok,
        violations=[_issue_out(i) for i in report.violations],
        warnings=[_issue_out(i) for i in report.warnings],
        totals=_totals_out(report.totals),
        per_day=_per_day_out(report.per_day, schedule.staff_type))


@router.patch("/{schedule_id}/assignments", response_model=ValidateOut)
def edit_assignments(schedule_id: int, body: AssignmentsPatch, db: Db, manager: Manager) -> ValidateOut:
    """Hand-edit cells while draft (plan 6.7). shift null clears a cell; a shift
    upserts it with calendar-correct hour buckets. Head cells are first-class:
    they may be edited or cleared like any other, and an H shift may be placed
    only on the rostered head nurse.

    "Co" is editable here like any other cell, but it is leave rather than a
    shift: it upserts a days_off row, which is what the solver's leave, the 8h
    credit and the bridged-weekend rule all read. A cell holds either a shift or
    a Co, never both, so each replaces the other and null clears whichever is
    there. Co days entered up front in Setup and Co days set here are the same
    rows - this screen is just a second way in."""
    schedule = _get_schedule(db, schedule_id, manager)
    _require_draft(schedule)
    is_aide = schedule.staff_type == STAFF_AIDE
    holidays = _holidays(db, schedule.year, schedule.month)
    roster = _roster_nurses(db, schedule)
    head_ids = {n.id for n in roster if n.is_head}
    roster_ids = {n.id for n in roster}
    for edit in body.edits:
        if edit.date.year != schedule.year or edit.date.month != schedule.month:
            raise HTTPException(422, f"{edit.date} is outside {schedule.month:02d}/{schedule.year}")
        existing = db.scalar(select(AssignmentRow).where(
            AssignmentRow.schedule_id == schedule.id,
            AssignmentRow.user_id == edit.user_id, AssignmentRow.date == edit.date))
        day_off = db.scalar(select(DayOff).where(
            DayOff.schedule_id == schedule.id,
            DayOff.user_id == edit.user_id, DayOff.date == edit.date))
        if edit.shift is None:
            if existing is not None:
                db.delete(existing)
            if day_off is not None:
                db.delete(day_off)
            db.flush()
            continue
        if edit.shift == "Co":
            # Co is leave, not an assignment: no Shift enum member, no hour
            # buckets, no lock (the solver always honours leave, so pinning it
            # would mean nothing).
            if edit.user_id not in roster_ids:
                raise HTTPException(422, "Co can only be given to a nurse on this schedule's roster")
            if cal.is_offday(edit.date, holidays):
                raise HTTPException(422, f"Co on {edit.date} is invalid - leave is allowed only on "
                                         f"Monday-Friday working days")
            if existing is not None:
                db.delete(existing)   # Co replaces whatever shift was there
            if day_off is None:
                db.add(DayOff(schedule_id=schedule.id, user_id=edit.user_id, date=edit.date))
            db.flush()
            continue
        shift = Shift(edit.shift)
        # Shift legality per staff type: the 8h shift is infirmiere-only; the
        # kitchen (K) and head (H) shifts are nurse-only.
        if is_aide and shift in (Shift.KITCHEN, Shift.HEAD):
            raise HTTPException(422, "Kitchen (K) and head (H) shifts are not allowed on an infirmiere schedule")
        if not is_aide and shift is Shift.EIGHT:
            raise HTTPException(422, "The 8h shift is only for infirmiere schedules")
        if shift is Shift.HEAD and edit.user_id not in head_ids:
            raise HTTPException(422, "The 8h head-nurse shift (H) can be placed only on the head nurse")
        if shift is Shift.TOPUP:
            duration = edit.duration_hours if edit.duration_hours is not None else 4
            if duration not in TOPUP_DURATIONS:
                raise HTTPException(422, f"Top-up duration must be 4 or 8, not {duration}")
        else:
            duration = cal.duration_of(shift)
        nh, wh = cal.shift_hours(edit.date, shift, duration, holidays)
        if day_off is not None:
            db.delete(day_off)        # ...and a shift replaces the Co
        if existing is None:
            existing = AssignmentRow(schedule_id=schedule.id, user_id=edit.user_id, date=edit.date)
            db.add(existing)
        existing.shift = shift.value
        existing.duration_hours = duration
        existing.normal_hours = nh
        existing.weekend_hours = wh
        existing.locked = edit.locked
        db.flush()
    db.commit()
    db.refresh(schedule)
    report = _validate_current(db, schedule)
    return ValidateOut(
        ok=report.ok,
        violations=[_issue_out(i) for i in report.violations],
        warnings=[_issue_out(i) for i in report.warnings],
        totals=_totals_out(report.totals),
        per_day=_per_day_out(report.per_day, schedule.staff_type))


@router.post("/{schedule_id}/approve", response_model=ApprovalOut)
def approve(schedule_id: int, db: Db, manager: Manager) -> ApprovalOut:
    """Approve a clean month (plan section 8): freeze an immutable snapshot,
    flip to approved, and permanently lock the previous approved month.

    Re-approval of an already-approved month is allowed only while the next
    month is still a draft or absent."""
    schedule = _get_schedule(db, schedule_id, manager)
    if schedule.status == STATUS_LOCKED:
        raise HTTPException(409, "This month is locked and can no longer be approved")
    if schedule.status == STATUS_APPROVED:
        ny, nm = _next_month(schedule.year, schedule.month)
        nxt = db.scalar(select(Schedule).where(
            Schedule.year == ny, Schedule.month == nm,
            Schedule.staff_type == schedule.staff_type))
        if nxt is not None and nxt.status != STATUS_DRAFT:
            raise HTTPException(409, "Cannot re-approve: the following month is no longer a draft")

    report = _validate_current(db, schedule)
    if not report.ok:
        raise HTTPException(409, {"message": "Schedule has violations and cannot be approved",
                                  "violations": [_issue_out(i).model_dump(mode="json")
                                                 for i in report.violations]})

    latest = _latest_approval(db, schedule.id)
    version = (latest.version + 1) if latest is not None else 1
    snapshot = {
        "assignments": [
            {"user_id": a.nurse_id, "date": a.date.isoformat(), "shift": a.shift.value,
             "duration_hours": a.duration, "normal_hours": a.normal_hours,
             "weekend_hours": a.weekend_hours}
            for a in _current_assignments(db, schedule)],
        "totals": {str(nid): {
            "worked_normal": t.worked_normal, "worked_weekend": t.worked_weekend,
            "leave_days": t.leave_days, "total": t.total,
            "effective_target": t.effective_target, "owed": t.owed,
            "nights": t.nights, "kitchens": t.kitchens} for nid, t in report.totals.items()},
    }
    approval = Approval(schedule_id=schedule.id, version=version,
                        approved_by=manager.id, snapshot=snapshot)
    db.add(approval)
    schedule.status = STATUS_APPROVED
    db.add(schedule)

    # Locking / prev_tail refresh operate within this staff type only, so
    # approving infirmiere M never touches the nurse month M-1 (and vice versa).
    py, pm = _prev_month(schedule.year, schedule.month)
    prev = db.scalar(select(Schedule).where(
        Schedule.year == py, Schedule.month == pm,
        Schedule.staff_type == schedule.staff_type))
    if prev is not None and prev.status == STATUS_APPROVED:
        prev.status = STATUS_LOCKED
        db.add(prev)

    # A following draft month was created from this month's older grid: refresh
    # its prev_tail so the cross-boundary rest rules see the approved reality
    # (matters after a reopen -> edit -> re-approve cycle).
    ny, nm = _next_month(schedule.year, schedule.month)
    nxt = db.scalar(select(Schedule).where(
        Schedule.year == ny, Schedule.month == nm,
        Schedule.staff_type == schedule.staff_type))
    if nxt is not None and nxt.status == STATUS_DRAFT:
        nxt.prev_tail = _prev_tail_from_previous(db, ny, nm, schedule.staff_type)
        db.add(nxt)
    db.commit()
    db.refresh(approval)
    return ApprovalOut(schedule_id=schedule.id, version=version, approved_by=manager.id,
                       approved_at=approval.approved_at)


@router.post("/{schedule_id}/reopen", response_model=ScheduleDetailOut)
def reopen(schedule_id: int, db: Db, manager: Manager) -> ScheduleDetailOut:
    """Send an approved month back to draft for corrections. A locked month is
    final (the following month was approved on top of it). The approval history
    is kept - a later approval simply adds the next version."""
    schedule = _get_schedule(db, schedule_id, manager)
    if schedule.status == STATUS_LOCKED:
        raise HTTPException(409, "This month is locked and can no longer be reopened")
    if schedule.status != STATUS_APPROVED:
        raise HTTPException(409, "Only an approved month can be reopened")
    schedule.status = STATUS_DRAFT
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return _detail(db, schedule, manager)


@router.get("/{schedule_id}/approvals", response_model=list[ApprovalListItem])
def list_approvals(schedule_id: int, db: Db, manager: Manager) -> list[ApprovalListItem]:
    """The month's approval history (plan section 8): one row per version."""
    schedule = _get_schedule(db, schedule_id, manager)
    rows = db.scalars(select(Approval).where(Approval.schedule_id == schedule.id)
                      .order_by(Approval.version))
    return [ApprovalListItem(version=a.version, approved_by=a.approved_by,
                             approved_at=a.approved_at) for a in rows]


@router.get("/{schedule_id}/approvals/{version}", response_model=ApprovalSnapshotOut)
def get_approval_snapshot(schedule_id: int, version: int, db: Db, manager: Manager) -> ApprovalSnapshotOut:
    """One immutable snapshot exactly as approved (grid and per-nurse totals)."""
    schedule = _get_schedule(db, schedule_id, manager)
    row = db.scalar(select(Approval).where(Approval.schedule_id == schedule.id,
                                           Approval.version == version))
    if row is None:
        raise HTTPException(404, f"No approval version {version} for this schedule")
    return ApprovalSnapshotOut(version=row.version, approved_by=row.approved_by,
                               approved_at=row.approved_at, snapshot=row.snapshot)
