"""Startup seeding of the real 14-nurse roster (plan section 5)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.models.tables import ROLE_MANAGER, User
from tests.api_helpers import add_user, make_settings

# username -> (position, section, kitchen-capable) straight from the plan table.
EXPECTED = {
    "drulea.florica": (1, "BIG", False),
    "pop.delia": (2, "BIG", False),
    "prodan.gabriela": (3, "BIG", True),
    "codre.aurelia": (4, "BIG", False),
    "matei.sorina": (5, "BIG", False),
    "pop.marinela": (6, "BIG", True),
    "campian.aurelia": (7, "SMALL", True),
    "rusu.ana": (8, "SMALL", False),
    "blaga.mirela": (9, "SMALL", True),
    "lung.paula": (10, "SMALL", True),
    "anca.berbecar": (11, "BIG", False),
    "boda.erika": (12, "SMALL", True),
    "ardelean.camelia": (13, "SMALL", True),
    "opris.elena": (14, "SMALL", True),
}


def _seeded_app(tmp_path):
    return create_app(make_settings(tmp_path, seed_default_staff=True))


def test_empty_db_seeds_fourteen_accounts(tmp_path):
    app = _seeded_app(tmp_path)
    session = app.state.sessionmaker()
    try:
        users = {u.username: u for u in session.query(User)}
        assert len(users) == 14
        for username, (position, section, kitchen) in EXPECTED.items():
            u = users[username]
            assert (u.position, u.section, u.default_kitchen_capable) == (position, section, kitchen)
            assert u.staff_type == "asistenta" and u.active
        # exactly one manager, and it is anca.berbecar
        managers = [u for u in users.values() if u.role == ROLE_MANAGER]
        assert [m.username for m in managers] == ["anca.berbecar"]
        # anca does not have to change her password; everyone else does
        assert users["anca.berbecar"].must_change_password is False
        assert all(u.must_change_password for un, u in users.items() if un != "anca.berbecar")
    finally:
        session.close()


def test_admin_logs_in_with_her_own_name_without_forced_change(tmp_path):
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    r = client.post("/api/auth/login",
                    json={"username": "anca.berbecar", "password": "anca.berbecar"})
    assert r.status_code == 200, r.text
    assert r.json()["must_change_password"] is False


def test_default_password_and_forced_change_for_others(tmp_path):
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    r = client.post("/api/auth/login",
                    json={"username": "drulea.florica", "password": "Parola123!"})
    assert r.status_code == 200, r.text
    assert r.json()["must_change_password"] is True


def test_existing_database_is_left_untouched(tmp_path):
    """Any pre-existing user disables seeding entirely (plan 5)."""
    app = _seeded_app(tmp_path)  # first boot seeds
    add_user(app, "someone.else")  # now a 15th, non-seed user exists
    # re-open the same database with seeding on: it must be a no-op now
    reopened = create_app(make_settings(tmp_path, seed_default_staff=True))
    session = reopened.state.sessionmaker()
    try:
        assert session.query(User).count() == 15  # unchanged, nothing re-seeded
    finally:
        session.close()
