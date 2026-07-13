def test_get_seeds_romanian_holidays(client, manager_h):
    r = client.get("/api/holidays", params={"year": 2026}, headers=manager_h)
    assert r.status_code == 200
    rows = r.json()
    dates = {h["date"] for h in rows}
    assert "2026-04-10" in dates          # Vinerea Mare (Orthodox Easter 2026-04-12)
    assert "2026-12-01" in dates
    assert all(d.startswith("2026-") for d in dates)
    assert len(rows) >= 15


def test_put_replaces_a_year(client, manager_h):
    client.get("/api/holidays", params={"year": 2027}, headers=manager_h)
    r = client.put("/api/holidays", headers=manager_h, json={
        "year": 2027,
        "holidays": [
            {"date": "2027-01-01", "name": "Anul Nou"},
            {"date": "2027-07-15", "name": "Zi speciala de sectie"},
        ],
    })
    assert r.status_code == 200
    r = client.get("/api/holidays", params={"year": 2027}, headers=manager_h)
    assert [h["date"] for h in r.json()] == ["2027-01-01", "2027-07-15"]


def test_put_rejects_dates_outside_year(client, manager_h):
    r = client.put("/api/holidays", headers=manager_h, json={
        "year": 2026, "holidays": [{"date": "2027-01-01", "name": "Wrong year"}]})
    assert r.status_code == 400


def test_replacing_one_year_leaves_others_alone(client, manager_h):
    before = client.get("/api/holidays", params={"year": 2026}, headers=manager_h).json()
    client.put("/api/holidays", headers=manager_h,
               json={"year": 2027, "holidays": []})
    after = client.get("/api/holidays", params={"year": 2026}, headers=manager_h).json()
    assert before == after


def test_nurse_gets_403(client, nurse_h):
    assert client.get("/api/holidays", params={"year": 2026}, headers=nurse_h).status_code == 403
