"""Install-time CLI (plan section 3): seed accounts without an open
registration endpoint.

Usage:
    python -m app.manage create-manager --username anca.berbecar --name "Berbecar Anca" --section SMALL
    python -m app.manage create-user --username maria.pop --name "Pop Maria" --section BIG [--kitchen] [--role nurse]

Prints the generated temporary password; the account must change it at first login.
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from .auth.security import generate_temp_password, hash_password
from .config import Settings
from .db import Base, make_engine, make_sessionmaker
from .models.tables import ROLE_MANAGER, ROLE_NURSE, User


def _create(role: str, args: argparse.Namespace) -> int:
    settings = Settings()
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session = make_sessionmaker(engine)()
    try:
        username = args.username.strip().lower()
        if session.scalar(select(User).where(User.username == username)):
            print(f"ERROR: username '{username}' already exists")
            return 1
        if role == ROLE_MANAGER and session.scalar(
                select(User).where(User.role == ROLE_MANAGER, User.active)):
            print("ERROR: there is already an active manager account")
            return 1
        temp = args.password or generate_temp_password()
        user = User(
            username=username,
            password_hash=hash_password(temp, settings.bcrypt_rounds),
            must_change_password=True,
            full_name=args.name,
            role=role,
            section=args.section,
            default_kitchen_capable=getattr(args, "kitchen", False),
        )
        session.add(user)
        session.commit()
        print(f"Created {role} '{username}' (id {user.id})")
        print(f"Temporary password: {temp}")
        return 0
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hospital Scheduling account seeding")
    sub = parser.add_subparsers(dest="command", required=True)

    manager = sub.add_parser("create-manager", help="create the single manager account")
    manager.add_argument("--username", required=True)
    manager.add_argument("--name", required=True)
    manager.add_argument("--section", choices=["BIG", "SMALL"], required=True)
    manager.add_argument("--password", default=None, help="default: generated")

    nurse = sub.add_parser("create-user", help="create a nurse account")
    nurse.add_argument("--username", required=True)
    nurse.add_argument("--name", required=True)
    nurse.add_argument("--section", choices=["BIG", "SMALL"], required=True)
    nurse.add_argument("--role", choices=[ROLE_NURSE], default=ROLE_NURSE)
    nurse.add_argument("--kitchen", action="store_true")
    nurse.add_argument("--password", default=None)

    args = parser.parse_args(argv)
    role = ROLE_MANAGER if args.command == "create-manager" else ROLE_NURSE
    return _create(role, args)


if __name__ == "__main__":
    raise SystemExit(main())
