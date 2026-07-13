"""Shared API-test fixtures: fresh app + tmp SQLite per test."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.api_helpers import add_user, auth, login, make_settings


@pytest.fixture()
def client(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def manager_h(client):
    add_user(client.app, "anca.berbecar", role="manager_nurse", section="SMALL")
    return auth(login(client, "anca.berbecar"))


@pytest.fixture()
def nurse_h(client):
    add_user(client.app, "maria.pop", role="nurse", section="BIG")
    return auth(login(client, "maria.pop"))
