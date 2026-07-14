"""Accounts CRUD (manager only). Decided: username login, temporary password
changed at first login, exactly one manager account; accounts with schedule
history are deactivated, never deleted (unused ones may be hard-deleted);
manager-controlled global display order (users.position)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth.deps import get_db, get_settings, require_password_changed
from ..auth.security import generate_temp_password, hash_password
from ..config import Settings
from ..models.schemas import (ResetPasswordOut, UserCreate, UserCreatedOut,
                              UserOrderIn, UserOut, UserUpdate)
from ..models.tables import (ROLE_MANAGER, STAFF_AIDE, Approval, AssignmentRow,
                             DayOff, ScheduleNurse, ShiftRequestRow, User)

router = APIRouter(prefix="/api/users", tags=["users"],
                   dependencies=[Depends(require_password_changed)])


def _manager_conflict(db: Session, exclude_id: int | None = None) -> bool:
    q = select(User).where(User.role == ROLE_MANAGER, User.active)
    managers = [u for u in db.scalars(q) if u.id != exclude_id]
    return len(managers) >= 1


@router.get("", response_model=list[UserOut])
def list_users(db: Annotated[Session, Depends(get_db)]) -> list[User]:
    return list(db.scalars(select(User).order_by(User.position, User.full_name)))


@router.put("/order", response_model=list[UserOut])
def reorder_users(
    body: UserOrderIn,
    db: Annotated[Session, Depends(get_db)],
) -> list[User]:
    """Set the global staff display order (manager-only, like the paper GRAFIC's
    row numbers). The body must list every existing user id - active or not -
    exactly once; positions are then assigned 1..n atomically."""
    existing_ids = set(db.scalars(select(User.id)))
    ordered = body.ordered_ids
    if len(ordered) != len(set(ordered)):
        raise HTTPException(422, "ordered_ids contains duplicate ids")
    if set(ordered) != existing_ids:
        raise HTTPException(
            422, "ordered_ids must list every existing user exactly once "
                 "(including inactive accounts)")
    users = {u.id: u for u in db.scalars(select(User))}
    for pos, uid in enumerate(ordered, start=1):
        users[uid].position = pos
    db.commit()
    return list(db.scalars(select(User).order_by(User.position, User.full_name)))


@router.post("", response_model=UserCreatedOut, status_code=201)
def create_user(
    body: UserCreate,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserCreatedOut:
    username = body.username.strip().lower()
    if db.scalar(select(User).where(User.username == username)) is not None:
        raise HTTPException(409, f"Username '{username}' is already taken")
    if body.role == ROLE_MANAGER and _manager_conflict(db):
        raise HTTPException(409, "There is already an active manager account")
    temp = body.temporary_password or generate_temp_password()
    max_pos = db.scalar(select(func.max(User.position)))
    user = User(
        username=username,
        password_hash=hash_password(temp, settings.bcrypt_rounds),
        must_change_password=True,
        full_name=body.full_name.strip(),
        role=body.role,
        staff_type=body.staff_type,
        section=body.section,
        default_kitchen_capable=body.default_kitchen_capable,
        position=(max_pos or 0) + 1,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserCreatedOut(user=UserOut.model_validate(user), temporary_password=temp)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Annotated[Session, Depends(get_db)],
    manager: Annotated[User, Depends(require_password_changed)],
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "No such account")
    changes = body.model_dump(exclude_unset=True)
    if user.id == manager.id and changes.get("active") is False:
        raise HTTPException(400, "You cannot deactivate your own account")
    if user.id == manager.id and changes.get("role") == "nurse":
        raise HTTPException(400, "You cannot demote your own account")
    # staff-type consistency (staff_type itself is immutable and absent from
    # UserUpdate): the same rules UserCreate enforces must hold on update.
    if user.staff_type == STAFF_AIDE:
        if changes.get("role") == ROLE_MANAGER:
            raise HTTPException(422, "An infirmiera account must keep the plain nurse role")
        if changes.get("section") is not None:
            raise HTTPException(422, "Infirmiere have no section (one ward-wide pool)")
        if changes.get("default_kitchen_capable"):
            raise HTTPException(422, "Infirmiere cannot be kitchen-capable")
    elif "section" in changes and changes["section"] is None:
        raise HTTPException(422, "A section (BIG or SMALL) is required for asistente")
    if changes.get("role") == ROLE_MANAGER and _manager_conflict(db, exclude_id=user.id):
        raise HTTPException(409, "There is already an active manager account")
    for field, value in changes.items():
        setattr(user, field, value)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    manager: Annotated[User, Depends(require_password_changed)],
) -> Response:
    """Hard-delete an account (amends the old deactivate-never-delete policy).
    Refused (409) when the user is referenced anywhere in schedule history -
    the manager is told to deactivate instead - or when deleting oneself."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "No such account")
    if user.id == manager.id:
        raise HTTPException(409, "You cannot delete your own account")
    referenced = (
        db.scalar(select(ScheduleNurse.id).where(ScheduleNurse.user_id == user_id))
        or db.scalar(select(DayOff.id).where(DayOff.user_id == user_id))
        or db.scalar(select(ShiftRequestRow.id).where(ShiftRequestRow.user_id == user_id))
        or db.scalar(select(AssignmentRow.id).where(AssignmentRow.user_id == user_id))
        or db.scalar(select(Approval.id).where(Approval.approved_by == user_id))
    )
    if referenced is not None:
        raise HTTPException(409, "This account is part of schedule history; deactivate it instead of deleting")
    db.delete(user)
    db.commit()
    return Response(status_code=204)


@router.post("/{user_id}/reset-password", response_model=ResetPasswordOut)
def reset_password(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ResetPasswordOut:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "No such account")
    temp = generate_temp_password()
    user.password_hash = hash_password(temp, settings.bcrypt_rounds)
    user.must_change_password = True
    db.add(user)
    db.commit()
    return ResetPasswordOut(temporary_password=temp)
