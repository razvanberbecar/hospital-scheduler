from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models.schemas import (AccessOut, ChangePasswordIn, LoginIn, RefreshIn,
                              TokenOut, UserOut)
from ..models.tables import User
from .deps import get_current_user, get_db, get_settings
from .security import (create_access_token, create_refresh_token, decode_token,
                       hash_password, verify_password)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _rate_limit(request: Request, settings: Settings) -> None:
    """Fixed-window in-memory limiter for the login endpoint (plan section 3)."""
    attempts: dict[str, list[float]] = request.app.state.login_attempts
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window = [t for t in attempts.get(key, []) if now - t < 60]
    if len(window) >= settings.login_rate_per_minute:
        raise HTTPException(429, "Too many login attempts; try again in a minute")
    window.append(now)
    attempts[key] = window


@router.post("/login", response_model=TokenOut)
def login(
    body: LoginIn,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> TokenOut:
    _rate_limit(request, settings)
    user = db.scalar(select(User).where(User.username == body.username.strip().lower()))
    if user is None or not verify_password(body.password, user.password_hash) or not user.active:
        raise HTTPException(401, "Wrong username or password")
    return TokenOut(
        access_token=create_access_token(settings, user.id),
        refresh_token=create_refresh_token(settings, user.id),
        must_change_password=user.must_change_password,
    )


@router.post("/refresh", response_model=AccessOut)
def refresh(
    body: RefreshIn,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> AccessOut:
    user_id = decode_token(settings, body.refresh_token, "refresh")
    if user_id is None:
        raise HTTPException(401, "Invalid or expired refresh token")
    user = db.get(User, user_id)
    if user is None or not user.active:
        raise HTTPException(401, "Account is not active")
    return AccessOut(access_token=create_access_token(settings, user.id))


@router.get("/me", response_model=UserOut)
def me(user: Annotated[User, Depends(get_current_user)]) -> User:
    return user


@router.post("/change-password", response_model=UserOut)
def change_password(
    body: ChangePasswordIn,
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is wrong")
    user.password_hash = hash_password(body.new_password, settings.bcrypt_rounds)
    user.must_change_password = False
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
