"""Pydantic request/response schemas."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["nurse", "manager_nurse"]
Section = Literal["BIG", "SMALL"]


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    must_change_password: bool


class RefreshIn(BaseModel):
    refresh_token: str


class AccessOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=100)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str
    role: Role
    section: Section
    default_kitchen_capable: bool
    active: bool
    must_change_password: bool
    position: int
    created_at: dt.datetime


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9._-]+$")
    full_name: str = Field(min_length=2, max_length=100)
    role: Role = "nurse"
    section: Section
    default_kitchen_capable: bool = False
    temporary_password: str | None = Field(default=None, min_length=8, max_length=100)


class UserCreatedOut(BaseModel):
    user: UserOut
    temporary_password: str


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=100)
    role: Role | None = None
    section: Section | None = None
    default_kitchen_capable: bool | None = None
    active: bool | None = None


class UserOrderIn(BaseModel):
    ordered_ids: list[int] = Field(min_length=1)


class ResetPasswordOut(BaseModel):
    temporary_password: str


class HolidayItem(BaseModel):
    date: dt.date
    name: str = Field(min_length=1, max_length=100)


class HolidaysPut(BaseModel):
    year: int = Field(ge=2000, le=2100)
    holidays: list[HolidayItem]


# --- schedules (M3) --------------------------------------------------------
RequestKind = Literal["Z", "N", "K", "FREE"]
EditShift = Literal["Z", "N", "K", "T", "H"]  # H only valid for the head nurse
ScheduleStatus = Literal["draft", "approved", "locked"]


class RosterItemIn(BaseModel):
    user_id: int
    kitchen_capable: bool | None = None   # None -> default from users.default_kitchen_capable


class DayOffIn(BaseModel):
    user_id: int
    date: dt.date


class RequestIn(BaseModel):
    user_id: int
    date: dt.date
    kind: RequestKind
    hard: bool = False


class ScheduleCreate(BaseModel):
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    target_hours: int = Field(ge=0)
    roster: list[RosterItemIn] = Field(default_factory=list)
    days_off: list[DayOffIn] = Field(default_factory=list)
    requests: list[RequestIn] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)


class ScheduleSetupPut(BaseModel):
    target_hours: int = Field(ge=0)
    roster: list[RosterItemIn] = Field(default_factory=list)
    days_off: list[DayOffIn] = Field(default_factory=list)
    requests: list[RequestIn] = Field(default_factory=list)
    prev_tail: dict[int, dict[dt.date, str]] = Field(default_factory=dict)
    config: dict = Field(default_factory=dict)


class GenerateIn(BaseModel):
    respect_locks: bool = False


class AssignmentEdit(BaseModel):
    user_id: int
    date: dt.date
    shift: EditShift | None            # None clears the cell
    duration_hours: int | None = None  # only top-ups use it (4|8); ignored for fixed shifts
    locked: bool = False


class AssignmentsPatch(BaseModel):
    edits: list[AssignmentEdit] = Field(default_factory=list)


class ScheduleListItem(BaseModel):
    id: int
    year: int
    month: int
    status: ScheduleStatus
    target_hours: int
    approval_count: int


class RosterEntryOut(BaseModel):
    user_id: int
    full_name: str
    section: Section
    role: Role
    kitchen_capable: bool


class RequestOut(BaseModel):
    user_id: int
    date: dt.date
    kind: RequestKind
    hard: bool
    granted: bool | None


class SetupOut(BaseModel):
    target_hours: int
    roster: list[RosterEntryOut]
    days_off: list[DayOffIn]
    requests: list[RequestOut]
    prev_tail: dict[int, dict[dt.date, str]]
    config: dict


class AssignmentOut(BaseModel):
    user_id: int
    date: dt.date
    shift: str
    duration_hours: int
    normal_hours: int
    weekend_hours: int
    locked: bool


class NurseTotalsOut(BaseModel):
    user_id: int
    worked_normal: int
    worked_weekend: int
    leave_days: int
    leave_credit: int
    worked: int
    total: int
    effective_target: int
    owed: int
    nights: int
    kitchens: int
    topup_hours: int


class IssueOut(BaseModel):
    level: str
    code: str
    message: str
    user_id: int | None = None
    date: dt.date | None = None


class DayCoverageOut(BaseModel):
    date: dt.date
    Z_big: int
    Z_small: int
    N_big: int
    N_small: int
    K: int


class ScheduleDetailOut(BaseModel):
    id: int
    year: int
    month: int
    status: ScheduleStatus
    setup: SetupOut
    assignments: list[AssignmentOut]
    totals: list[NurseTotalsOut]
    per_day: list[DayCoverageOut]
    violations: list[IssueOut]
    warnings: list[IssueOut]
    approval_count: int
    # solver echo, present only on the /generate response
    solver_status: str | None = None
    objective: int | None = None
    relaxed_nights: dict[int, int] | None = None


class ValidateOut(BaseModel):
    ok: bool
    violations: list[IssueOut]
    warnings: list[IssueOut]
    totals: list[NurseTotalsOut]
    per_day: list[DayCoverageOut]


class ApprovalOut(BaseModel):
    schedule_id: int
    version: int
    approved_by: int
    approved_at: dt.datetime


class ApprovalListItem(BaseModel):
    version: int
    approved_by: int
    approved_at: dt.datetime


class ApprovalSnapshotOut(BaseModel):
    version: int
    approved_by: int
    approved_at: dt.datetime
    snapshot: dict
