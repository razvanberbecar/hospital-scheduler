"""Password hashing (bcrypt) and JWT issue/verify (PyJWT).

Decided in the plan (section 3): username login, access token 60 min, refresh
7 days; role and active status are re-checked when a refresh token is used.
"""
from __future__ import annotations

import datetime as dt
import secrets

import bcrypt
import jwt

from ..config import Settings

ALGORITHM = "HS256"


def hash_password(password: str, rounds: int = 12) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=rounds)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def generate_temp_password() -> str:
    return secrets.token_urlsafe(9)


def _create_token(settings: Settings, user_id: int, kind: str, lifetime: dt.timedelta) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {"sub": str(user_id), "type": kind, "iat": now, "exp": now + lifetime}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def create_access_token(settings: Settings, user_id: int) -> str:
    return _create_token(settings, user_id, "access", dt.timedelta(minutes=settings.access_ttl_min))


def create_refresh_token(settings: Settings, user_id: int) -> str:
    return _create_token(settings, user_id, "refresh", dt.timedelta(days=settings.refresh_ttl_days))


def decode_token(settings: Settings, token: str, expected_type: str) -> int | None:
    """Returns the user id, or None for anything invalid or of the wrong type."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != expected_type:
        return None
    try:
        return int(payload["sub"])
    except (KeyError, ValueError):
        return None
