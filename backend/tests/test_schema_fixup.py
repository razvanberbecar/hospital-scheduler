"""Startup schema fixups (app.db.run_schema_fixups).

The project has no migration tool and create_all never alters existing tables,
so removed columns linger in older databases (e.g. a Docker Postgres volume
initialized before 2026-07-06 still had schedules.balances NOT NULL, which made
every INSERT fail). create_app must reconcile that drift on startup.
"""
from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.config import Settings
from app.main import create_app


def test_stale_balances_column_is_dropped_on_startup(tmp_path):
    db_file = tmp_path / "old.db"
    con = sqlite3.connect(db_file)
    con.execute("""
        CREATE TABLE schedules (
            id INTEGER PRIMARY KEY,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            target_hours INTEGER NOT NULL,
            status VARCHAR(10) NOT NULL,
            balances JSON NOT NULL,
            prev_tail JSON NOT NULL,
            config JSON NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_schedule_month UNIQUE (year, month))
    """)
    con.commit()
    con.close()

    settings = Settings(database_url=f"sqlite:///{db_file}",
                        jwt_secret="test-secret", bcrypt_rounds=4)
    app = create_app(settings)

    cols = {c["name"] for c in inspect(app.state.engine).get_columns("schedules")}
    assert "balances" not in cols
    assert {"year", "month", "prev_tail", "config"} <= cols
    # the app comes up healthy on the reconciled database
    assert TestClient(app).get("/api/health").status_code == 200


def test_missing_position_column_is_added_and_backfilled(tmp_path):
    """An older users table without `position`: create_app adds the column and
    backfills to the current display order (BIG group first, then SMALL, each
    alphabetical by full_name)."""
    db_file = tmp_path / "old.db"
    con = sqlite3.connect(db_file)
    con.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username VARCHAR(50) NOT NULL UNIQUE,
            password_hash VARCHAR(100) NOT NULL,
            must_change_password BOOLEAN NOT NULL,
            full_name VARCHAR(100) NOT NULL,
            role VARCHAR(20) NOT NULL,
            section VARCHAR(10) NOT NULL,
            default_kitchen_capable BOOLEAN NOT NULL,
            active BOOLEAN NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)
    """)
    # Inserted out of display order on purpose.
    rows = [
        ("z.small", "Zamfir Ana", "SMALL"),
        ("a.big", "Albu Ion", "BIG"),
        ("m.small", "Marin Ana", "SMALL"),
        ("b.big", "Barbu Vlad", "BIG"),
    ]
    for u, fn, sec in rows:
        con.execute(
            "INSERT INTO users (username, password_hash, must_change_password, "
            "full_name, role, section, default_kitchen_capable, active) "
            "VALUES (?, 'x', 0, ?, 'nurse', ?, 0, 1)", (u, fn, sec))
    con.commit()
    con.close()

    settings = Settings(database_url=f"sqlite:///{db_file}",
                        jwt_secret="test-secret", bcrypt_rounds=4)
    app = create_app(settings)

    cols = {c["name"] for c in inspect(app.state.engine).get_columns("users")}
    assert "position" in cols

    session = app.state.sessionmaker()
    try:
        from app.models.tables import User
        ordered = [u.full_name for u in session.query(User).order_by(User.position)]
    finally:
        session.close()
    # BIG group (alpha) then SMALL group (alpha).
    assert ordered == ["Albu Ion", "Barbu Vlad", "Marin Ana", "Zamfir Ana"]
