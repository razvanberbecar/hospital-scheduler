from __future__ import annotations

import pathlib

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def run_schema_fixups(engine) -> None:
    """Reconcile known schema drift in pre-existing databases. The project has
    no migration tool yet and create_all never alters existing tables, so each
    removed/renamed column needs a one-off, idempotent fixup here."""
    inspector = inspect(engine)
    is_postgres = engine.dialect.name == "postgresql"
    if "schedules" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("schedules")}
        if "balances" in cols:  # ore-restante carry-over removed 2026-07-06
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE schedules DROP COLUMN balances"))

        # schedules.staff_type + (year, month, staff_type) unique constraint
        # (infirmiere as a second staff type, 2026-07-14). ADD COLUMN ... DEFAULT
        # works on both SQLite and Postgres; the constraint swap is Postgres-only
        # (SQLite bakes constraints into the table, so fresh create_all is enough).
        if "staff_type" not in cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE schedules ADD COLUMN staff_type VARCHAR(12) "
                    "NOT NULL DEFAULT 'asistenta'"))
        if is_postgres:
            uniques = {u["name"] for u in inspector.get_unique_constraints("schedules")}
            if "uq_schedule_month_staff" not in uniques:
                with engine.begin() as conn:
                    conn.execute(text(
                        "ALTER TABLE schedules DROP CONSTRAINT IF EXISTS uq_schedule_month"))
                    conn.execute(text(
                        "ALTER TABLE schedules ADD CONSTRAINT uq_schedule_month_staff "
                        "UNIQUE (year, month, staff_type)"))

    # users.position added 2026-07-07 (manager-controlled staff ordering, M8).
    # Older SQLite dev DBs and the owner's Docker Postgres volume lack it; add it
    # and backfill to today's display order (BIG group first, then SMALL, each
    # alphabetical by full_name) so the upgrade changes nothing visually.
    if "users" in inspector.get_table_names():
        user_cols = {c["name"] for c in inspector.get_columns("users")}
        if "position" not in user_cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN position INTEGER NOT NULL DEFAULT 0"))
                rows = conn.execute(text("SELECT id, section, full_name FROM users")).all()
                ordered = sorted(rows, key=lambda r: (r[1] != "BIG", r[2]))
                for pos, r in enumerate(ordered, start=1):
                    conn.execute(text("UPDATE users SET position = :p WHERE id = :i"),
                                 {"p": pos, "i": r[0]})

        # users.staff_type (infirmiere, 2026-07-14). users.section becomes
        # nullable for infirmiere; Postgres can drop the NOT NULL in place, while
        # older SQLite dev DBs keep it (only asistente exist there, section set).
        if "staff_type" not in user_cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN staff_type VARCHAR(12) "
                    "NOT NULL DEFAULT 'asistenta'"))
        if is_postgres:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ALTER COLUMN section DROP NOT NULL"))


def make_engine(database_url: str):
    if database_url.startswith("sqlite"):
        path = database_url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(database_url, connect_args={"check_same_thread": False})
    # Postgres (or anything server-based): recycle dead connections gracefully.
    return create_engine(database_url, pool_pre_ping=True)


def make_sessionmaker(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
