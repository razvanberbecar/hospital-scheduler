"""Holidays per year: preseeded with the Romanian public holidays, editable by
the manager (decided in Q&A round 2)."""
from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..auth.deps import get_db, require_password_changed
from ..models.schemas import HolidayItem, HolidaysPut
from ..models.tables import Holiday
from ..solver.calendar import romanian_holidays_named

router = APIRouter(prefix="/api/holidays", tags=["holidays"],
                   dependencies=[Depends(require_password_changed)])


def _year_rows(db: Session, year: int) -> list[Holiday]:
    q = (select(Holiday)
         .where(Holiday.date >= dt.date(year, 1, 1), Holiday.date <= dt.date(year, 12, 31))
         .order_by(Holiday.date))
    return list(db.scalars(q))


@router.get("", response_model=list[HolidayItem])
def get_holidays(
    db: Annotated[Session, Depends(get_db)],
    year: Annotated[int, Query(ge=2000, le=2100)],
) -> list[Holiday]:
    rows = _year_rows(db, year)
    if not rows:
        for date, name in sorted(romanian_holidays_named(year).items()):
            db.add(Holiday(date=date, name=name))
        db.commit()
        rows = _year_rows(db, year)
    return rows


@router.put("", response_model=list[HolidayItem])
def replace_holidays(
    body: HolidaysPut,
    db: Annotated[Session, Depends(get_db)],
) -> list[Holiday]:
    for item in body.holidays:
        if item.date.year != body.year:
            raise HTTPException(400, f"Sărbătoarea {item.date} nu este în anul {body.year}")
    db.execute(delete(Holiday).where(
        Holiday.date >= dt.date(body.year, 1, 1), Holiday.date <= dt.date(body.year, 12, 31)))
    for item in body.holidays:
        db.add(Holiday(date=item.date, name=item.name))
    db.commit()
    return _year_rows(db, body.year)
