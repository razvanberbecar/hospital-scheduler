"""Helpers shared by the API tests."""
from __future__ import annotations

from app.auth.security import hash_password
from app.config import Settings
from app.models.tables import User

TEST_PASSWORD = "Secret123!"


def make_settings(tmp_path, **overrides) -> Settings:
    defaults = dict(
        database_url=f"sqlite:///{tmp_path}/test.db",
        jwt_secret="test-secret",
        bcrypt_rounds=4,
        login_rate_per_minute=1000,
        seed_default_staff=False,  # keep the empty-DB assumption for API tests
    )
    defaults.update(overrides)
    return Settings(**defaults)


def add_user(app, username, *, password=TEST_PASSWORD, role="nurse", section="BIG",
             staff_type="asistenta", must_change=False, kitchen=False, active=True) -> int:
    session = app.state.sessionmaker()
    try:
        user = User(
            username=username,
            password_hash=hash_password(password, app.state.settings.bcrypt_rounds),
            must_change_password=must_change,
            full_name=username.replace(".", " ").title(),
            role=role,
            staff_type=staff_type,
            section=section,
            default_kitchen_capable=kitchen,
            active=active,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user.id
    finally:
        session.close()


def login(client, username, password=TEST_PASSWORD) -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def auth(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}
