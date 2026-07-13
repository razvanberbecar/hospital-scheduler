"""FastAPI dependencies: DB session, current user, role and password guards."""
from __future__ import annotations

from typing import Annotated, Iterator

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..config import Settings
from ..models.tables import User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    session = request.app.state.sessionmaker()
    try:
        yield session
    finally:
        session.close()


def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if creds is None:
        raise HTTPException(401, "Not authenticated")
    user_id = decode_token(settings, creds.credentials, "access")
    if user_id is None:
        raise HTTPException(401, "Invalid or expired token")
    user = db.get(User, user_id)
    if user is None or not user.active:
        raise HTTPException(401, "Account is not active")
    return user


def require_manager(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_manager:
        raise HTTPException(403, "Manager access required")
    return user


def require_password_changed(user: Annotated[User, Depends(require_manager)]) -> User:
    """Business endpoints stay locked until the temporary password is replaced."""
    if user.must_change_password:
        raise HTTPException(403, "Change your temporary password first")
    return user
