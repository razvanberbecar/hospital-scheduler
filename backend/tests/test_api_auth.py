from fastapi.testclient import TestClient

from app.main import create_app

from tests.api_helpers import TEST_PASSWORD, add_user, auth, login, make_settings


def test_login_returns_token_pair(client):
    add_user(client.app, "maria.pop")
    tokens = login(client, "maria.pop")
    assert tokens["token_type"] == "bearer"
    assert tokens["must_change_password"] is False
    assert tokens["access_token"] and tokens["refresh_token"]


def test_login_rejects_wrong_password_unknown_user_and_inactive(client):
    add_user(client.app, "maria.pop")
    add_user(client.app, "gone.nurse", active=False)
    for payload in (
        {"username": "maria.pop", "password": "wrong-password"},
        {"username": "nobody", "password": TEST_PASSWORD},
        {"username": "gone.nurse", "password": TEST_PASSWORD},
    ):
        assert client.post("/api/auth/login", json=payload).status_code == 401


def test_me_returns_profile(client, nurse_h):
    r = client.get("/api/auth/me", headers=nurse_h)
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "maria.pop"
    assert body["role"] == "nurse"
    assert "password_hash" not in body


def test_refresh_issues_working_access_token(client):
    add_user(client.app, "maria.pop")
    tokens = login(client, "maria.pop")
    r = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    access = r.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200


def test_refresh_rechecks_active_status(client, manager_h):
    uid = add_user(client.app, "maria.pop")
    tokens = login(client, "maria.pop")
    r = client.patch(f"/api/users/{uid}", json={"active": False}, headers=manager_h)
    assert r.status_code == 200
    r = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


def test_access_token_is_not_a_refresh_token(client):
    add_user(client.app, "maria.pop")
    tokens = login(client, "maria.pop")
    r = client.post("/api/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert r.status_code == 401


def test_change_password_flow_clears_must_change(client):
    add_user(client.app, "noua.asistenta", must_change=True)
    tokens = login(client, "noua.asistenta")
    assert tokens["must_change_password"] is True

    bad = client.post("/api/auth/change-password", headers=auth(tokens),
                      json={"current_password": "wrong", "new_password": "BrandNew123"})
    assert bad.status_code == 400

    ok = client.post("/api/auth/change-password", headers=auth(tokens),
                     json={"current_password": TEST_PASSWORD, "new_password": "BrandNew123"})
    assert ok.status_code == 200
    assert ok.json()["must_change_password"] is False
    assert client.post("/api/auth/login",
                       json={"username": "noua.asistenta", "password": TEST_PASSWORD}).status_code == 401
    assert login(client, "noua.asistenta", "BrandNew123")["must_change_password"] is False


def test_manager_with_temp_password_is_locked_out_of_business_endpoints(client):
    add_user(client.app, "anca.berbecar", role="manager_nurse", section="SMALL", must_change=True)
    tokens = login(client, "anca.berbecar")
    assert client.get("/api/users", headers=auth(tokens)).status_code == 403
    client.post("/api/auth/change-password", headers=auth(tokens),
                json={"current_password": TEST_PASSWORD, "new_password": "BrandNew123"})
    assert client.get("/api/users", headers=auth(tokens)).status_code == 200


def test_login_rate_limit(tmp_path):
    app = create_app(make_settings(tmp_path, login_rate_per_minute=3))
    with TestClient(app) as client:
        add_user(app, "maria.pop")
        for _ in range(3):
            client.post("/api/auth/login", json={"username": "maria.pop", "password": "nope"})
        r = client.post("/api/auth/login",
                        json={"username": "maria.pop", "password": TEST_PASSWORD})
        assert r.status_code == 429


def test_garbage_token_is_401(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401
    assert client.get("/api/auth/me").status_code == 401
