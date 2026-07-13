from tests.api_helpers import TEST_PASSWORD, add_user, login


def test_manager_creates_nurse_with_temp_password(client, manager_h):
    r = client.post("/api/users", headers=manager_h, json={
        "username": "Elena.Opris", "full_name": "Opris Elena", "section": "SMALL",
        "default_kitchen_capable": True,
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user"]["username"] == "elena.opris"  # normalized
    assert body["user"]["must_change_password"] is True
    temp = body["temporary_password"]
    tokens = login(client, "elena.opris", temp)
    assert tokens["must_change_password"] is True


def test_duplicate_username_is_409(client, manager_h):
    payload = {"username": "maria.pop", "full_name": "Pop Maria", "section": "BIG"}
    assert client.post("/api/users", headers=manager_h, json=payload).status_code == 201
    assert client.post("/api/users", headers=manager_h, json=payload).status_code == 409


def test_only_one_manager_account(client, manager_h):
    r = client.post("/api/users", headers=manager_h, json={
        "username": "second.boss", "full_name": "Second Boss", "section": "BIG",
        "role": "manager_nurse",
    })
    assert r.status_code == 409
    uid = add_user(client.app, "maria.pop")
    r = client.patch(f"/api/users/{uid}", headers=manager_h, json={"role": "manager_nurse"})
    assert r.status_code == 409


def test_nurse_role_gets_403_on_accounts(client, nurse_h):
    assert client.get("/api/users", headers=nurse_h).status_code == 403
    r = client.post("/api/users", headers=nurse_h, json={
        "username": "x.y", "full_name": "X Y", "section": "BIG"})
    assert r.status_code == 403


def test_patch_updates_fields_and_deactivation_blocks_login(client, manager_h):
    uid = add_user(client.app, "maria.pop", section="BIG")
    r = client.patch(f"/api/users/{uid}", headers=manager_h,
                     json={"section": "SMALL", "default_kitchen_capable": True})
    assert r.status_code == 200
    assert r.json()["section"] == "SMALL" and r.json()["default_kitchen_capable"] is True

    assert client.patch(f"/api/users/{uid}", headers=manager_h,
                        json={"active": False}).status_code == 200
    assert client.post("/api/auth/login", json={
        "username": "maria.pop", "password": TEST_PASSWORD}).status_code == 401


def test_manager_cannot_deactivate_or_demote_herself(client, manager_h):
    me = client.get("/api/auth/me", headers=manager_h).json()
    assert client.patch(f"/api/users/{me['id']}", headers=manager_h,
                        json={"active": False}).status_code == 400
    assert client.patch(f"/api/users/{me['id']}", headers=manager_h,
                        json={"role": "nurse"}).status_code == 400


def test_reset_password_issues_new_temp(client, manager_h):
    uid = add_user(client.app, "maria.pop")
    r = client.post(f"/api/users/{uid}/reset-password", headers=manager_h)
    assert r.status_code == 200
    temp = r.json()["temporary_password"]
    assert client.post("/api/auth/login", json={
        "username": "maria.pop", "password": TEST_PASSWORD}).status_code == 401
    tokens = login(client, "maria.pop", temp)
    assert tokens["must_change_password"] is True


def test_delete_unreferenced_user(client, manager_h):
    """Change 5: a fresh, unreferenced account can be hard-deleted (204) and
    disappears from the listing."""
    uid = add_user(client.app, "maria.pop")
    assert client.delete(f"/api/users/{uid}", headers=manager_h).status_code == 204
    names = [u["username"] for u in client.get("/api/users", headers=manager_h).json()]
    assert "maria.pop" not in names


def test_delete_referenced_user_is_409(client, manager_h):
    """A user on any roster is part of schedule history -> 409, deactivate instead."""
    # enough nurses to pass the roster guards for a draft schedule
    roster = [("drulea.florica", "BIG", True), ("pop.delia", "BIG", True),
              ("prodan.gabriela", "BIG", False), ("rusu.ana", "SMALL", True),
              ("blaga.mirela", "SMALL", True), ("lung.paula", "SMALL", False)]
    ids = {u: add_user(client.app, u, section=s, kitchen=k) for u, s, k in roster}
    body = {"year": 2026, "month": 7, "target_hours": 184,
            "roster": [{"user_id": uid} for uid in ids.values()],
            "days_off": [], "requests": [], "config": {"max_time_s": 5}}
    assert client.post("/api/schedules", headers=manager_h, json=body).status_code == 201
    # rostered nurse is now referenced via schedule_nurses
    assert client.delete(f"/api/users/{ids['lung.paula']}",
                         headers=manager_h).status_code == 409


def test_delete_self_is_409(client, manager_h):
    me = client.get("/api/auth/me", headers=manager_h).json()
    assert client.delete(f"/api/users/{me['id']}", headers=manager_h).status_code == 409


def test_delete_unknown_user_is_404(client, manager_h):
    assert client.delete("/api/users/9999", headers=manager_h).status_code == 404


def test_delete_user_requires_manager(client, nurse_h):
    assert client.delete("/api/users/1", headers=nurse_h).status_code == 403


def test_list_users_ordered(client, manager_h):
    add_user(client.app, "maria.pop", section="BIG")
    add_user(client.app, "dana.rusu", section="SMALL")
    r = client.get("/api/users", headers=manager_h)
    assert r.status_code == 200
    names = [u["username"] for u in r.json()]
    assert set(names) == {"anca.berbecar", "maria.pop", "dana.rusu"}


def test_new_user_appends_to_end(client, manager_h):
    """A freshly created account lands at the end of the display order."""
    add_user(client.app, "maria.pop", section="BIG")
    r = client.post("/api/users", headers=manager_h, json={
        "username": "new.hire", "full_name": "New Hire", "section": "SMALL"})
    assert r.status_code == 201, r.text
    users = client.get("/api/users", headers=manager_h).json()
    assert users[-1]["username"] == "new.hire"
    # position is the max of all existing positions.
    assert users[-1]["position"] == max(u["position"] for u in users)


def test_reorder_persists_new_order(client, manager_h):
    add_user(client.app, "maria.pop", section="BIG")
    add_user(client.app, "dana.rusu", section="SMALL")
    ids = [u["id"] for u in client.get("/api/users", headers=manager_h).json()]
    new_order = list(reversed(ids))
    r = client.put("/api/users/order", headers=manager_h, json={"ordered_ids": new_order})
    assert r.status_code == 200, r.text
    got = [u["id"] for u in r.json()]
    assert got == new_order
    # Persisted: a fresh GET returns the same order and positions 1..n.
    users = client.get("/api/users", headers=manager_h).json()
    assert [u["id"] for u in users] == new_order
    assert [u["position"] for u in users] == [1, 2, 3]


def test_reorder_is_manager_only(client, nurse_h, manager_h):
    ids = [u["id"] for u in client.get("/api/users", headers=manager_h).json()]
    r = client.put("/api/users/order", headers=nurse_h, json={"ordered_ids": ids})
    assert r.status_code == 403


def test_reorder_rejects_missing_ids(client, manager_h):
    add_user(client.app, "maria.pop", section="BIG")
    ids = [u["id"] for u in client.get("/api/users", headers=manager_h).json()]
    r = client.put("/api/users/order", headers=manager_h, json={"ordered_ids": ids[:-1]})
    assert r.status_code == 422


def test_reorder_rejects_duplicate_ids(client, manager_h):
    add_user(client.app, "maria.pop", section="BIG")
    ids = [u["id"] for u in client.get("/api/users", headers=manager_h).json()]
    dup = ids[:-1] + [ids[0]]
    r = client.put("/api/users/order", headers=manager_h, json={"ordered_ids": dup})
    assert r.status_code == 422


def test_reorder_rejects_unknown_id(client, manager_h):
    ids = [u["id"] for u in client.get("/api/users", headers=manager_h).json()]
    r = client.put("/api/users/order", headers=manager_h,
                   json={"ordered_ids": ids + [9999]})
    assert r.status_code == 422


def test_reorder_includes_inactive_users(client, manager_h):
    """The order must cover inactive accounts too."""
    uid = add_user(client.app, "maria.pop", section="BIG", active=False)
    ids = [u["id"] for u in client.get("/api/users", headers=manager_h).json()]
    assert uid in ids  # inactive users are listed
    # An order that omits the inactive user is rejected.
    r = client.put("/api/users/order", headers=manager_h,
                   json={"ordered_ids": [i for i in ids if i != uid]})
    assert r.status_code == 422
