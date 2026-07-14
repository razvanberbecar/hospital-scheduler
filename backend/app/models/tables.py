"""SQLAlchemy tables (plan section 7). The users table is the merged staff
registry + login table: accounts are deactivated, never deleted.

Schedules and their children (plan section 7, M3): one draft/approved/locked
schedule per (year, month); the roster, days off, requests and the generated
grid live in child tables, and each approval freezes an immutable snapshot row.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (JSON, Boolean, Date, DateTime, ForeignKey, Integer,
                        String, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base

ROLE_NURSE = "nurse"
ROLE_MANAGER = "manager_nurse"

# Staff types scheduled separately (see solver.domain). Kept as literals here to
# avoid importing the solver into the ORM layer.
STAFF_NURSE = "asistenta"
STAFF_AIDE = "infirmiera"

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_LOCKED = "locked"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    full_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), default=ROLE_NURSE)   # nurse | manager_nurse
    # asistenta -> section required BIG|SMALL; infirmiera -> section NULL, one pool.
    staff_type: Mapped[str] = mapped_column(String(12), default=STAFF_NURSE)
    section: Mapped[str | None] = mapped_column(String(10), nullable=True)  # BIG | SMALL | None
    default_kitchen_capable: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Manager-controlled display order for the schedule grid / GRAFIC export.
    # New users are appended (max+1) by the create endpoint; not a DB default.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def is_manager(self) -> bool:
        return self.role == ROLE_MANAGER


class Holiday(Base):
    __tablename__ = "holidays"

    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))


class Schedule(Base):
    """One month's schedule. prev_tail holds the last two days of the previous
    month (str(nurse_id) -> {"YYYY-MM-DD": shift code}) for the cross-boundary
    rest rules, config partial solver Config overrides (e.g. {"max_time_s": 10}).
    Months no longer carry hour balances between each other."""
    __tablename__ = "schedules"
    # One schedule per (year, month, staff_type): asistente and infirmiere have
    # fully separate draft->approved->locked lifecycles.
    __table_args__ = (UniqueConstraint("year", "month", "staff_type",
                                       name="uq_schedule_month_staff"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    staff_type: Mapped[str] = mapped_column(String(12), default=STAFF_NURSE)
    target_hours: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default=STATUS_DRAFT)
    prev_tail: Mapped[dict] = mapped_column(JSON, default=dict)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class ScheduleNurse(Base):
    """Per-schedule roster row; kitchen_capable defaults from the user's flag."""
    __tablename__ = "schedule_nurses"
    __table_args__ = (UniqueConstraint("schedule_id", "user_id", name="uq_schedule_nurse"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kitchen_capable: Mapped[bool] = mapped_column(Boolean, default=False)


class DayOff(Base):
    """A Co (leave) day for one rostered nurse."""
    __tablename__ = "days_off"
    __table_args__ = (UniqueConstraint("schedule_id", "user_id", "date", name="uq_dayoff"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    date: Mapped[dt.date] = mapped_column(Date)


class ShiftRequestRow(Base):
    """A nurse wish (kind Z|N|K|FREE, hard = must-have). granted is filled after
    generation (True/False/None-not-yet-generated)."""
    __tablename__ = "shift_requests"
    __table_args__ = (UniqueConstraint("schedule_id", "user_id", "date", name="uq_request"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    date: Mapped[dt.date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(4))
    hard: Mapped[bool] = mapped_column(Boolean, default=False)
    granted: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)


class AssignmentRow(Base):
    """One cell of the generated grid. locked cells survive regeneration."""
    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("schedule_id", "user_id", "date", name="uq_assignment"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    date: Mapped[dt.date] = mapped_column(Date)
    shift: Mapped[str] = mapped_column(String(1))            # Z | N | K | H | T | 8
    duration_hours: Mapped[int] = mapped_column(Integer)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    normal_hours: Mapped[int] = mapped_column(Integer)
    weekend_hours: Mapped[int] = mapped_column(Integer)


class Approval(Base):
    """Immutable approval snapshot: the frozen grid and per-nurse totals at
    approval time. version increments per re-approval of the same month."""
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    approved_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    snapshot: Mapped[dict] = mapped_column(JSON)
