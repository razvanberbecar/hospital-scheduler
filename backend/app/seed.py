"""Startup seeding of the real 14-nurse Pediatrie roster (plan section 5).

Runs from create_app on an EMPTY users table only, so an existing dev/prod
database is never touched. The accounts, sections, kitchen flags and display
positions match today's paper GRAFIC. anca.berbecar is the head nurse (manager)
and logs in with her own name and no forced change; everyone else gets the
shared temporary password and must change it at first login.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth.security import hash_password
from .config import Settings
from .models.tables import ROLE_MANAGER, ROLE_NURSE, STAFF_NURSE, User

ADMIN_USERNAME = "anca.berbecar"
DEFAULT_PASSWORD = "Parola123!"

# position, username, full name, section, kitchen-capable, role
_SEED_STAFF: list[tuple[int, str, str, str, bool, str]] = [
    (1, "drulea.florica", "Drulea Florica", "BIG", False, ROLE_NURSE),
    (2, "pop.delia", "Pop Delia", "BIG", False, ROLE_NURSE),
    (3, "prodan.gabriela", "Prodan Gabriela", "BIG", True, ROLE_NURSE),
    (4, "codre.aurelia", "Codre Aurelia", "BIG", False, ROLE_NURSE),
    (5, "matei.sorina", "Matei Sorina", "BIG", False, ROLE_NURSE),
    (6, "pop.marinela", "Pop Marinela", "BIG", True, ROLE_NURSE),
    (7, "campian.aurelia", "Campian Aurelia", "SMALL", True, ROLE_NURSE),
    (8, "rusu.ana", "Rusu Ana", "SMALL", False, ROLE_NURSE),
    (9, "blaga.mirela", "Blaga Mirela", "SMALL", True, ROLE_NURSE),
    (10, "lung.paula", "Lung Paula", "SMALL", True, ROLE_NURSE),
    (11, ADMIN_USERNAME, "Berbecar Anca", "BIG", False, ROLE_MANAGER),
    (12, "boda.erika", "Boda Erika", "SMALL", True, ROLE_NURSE),
    (13, "ardelean.camelia", "Ardelean Camelia", "SMALL", True, ROLE_NURSE),
    (14, "opris.elena", "Opris Elena", "SMALL", True, ROLE_NURSE),
]


def seed_default_staff(session: Session, settings: Settings) -> None:
    """Insert the 14 real accounts on a fresh database. No-op if any user exists."""
    if session.scalar(select(User).limit(1)) is not None:
        return
    for position, username, full_name, section, kitchen, role in _SEED_STAFF:
        is_admin = username == ADMIN_USERNAME
        password = username if is_admin else DEFAULT_PASSWORD
        session.add(User(
            username=username,
            password_hash=hash_password(password, settings.bcrypt_rounds),
            must_change_password=not is_admin,
            full_name=full_name,
            role=role,
            staff_type=STAFF_NURSE,
            section=section,
            default_kitchen_capable=kitchen,
            active=True,
            position=position,
        ))
    session.commit()
