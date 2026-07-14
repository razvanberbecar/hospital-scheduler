"""Excel export of the GRAFIC form (milestone M6).

`build_workbook` is a pure function: it takes already-assembled schedule data
(roster, assignments, leave, holidays, validator totals and per-day coverage)
and returns the .xlsx as bytes, mirroring the ward's paper GRAFIC closely
enough to print and post on the wall.

Layout (single sheet "GRAFIC"):
  title block -> two header rows (day number, weekday initial) -> nurse rows
  grouped by section -> per-nurse totals -> coverage rows -> footer.
Weekend columns are shaded light gray, holiday columns light rose (holiday
wins), and the whole grid is thin-bordered and centered.
"""
from __future__ import annotations

import datetime as dt
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.worksheet.worksheet import Worksheet

from ..solver import calendar as cal
from ..solver.domain import STAFF_AIDE, Nurse, Shift
from ..solver.validator import NurseTotals

# Romanian month names (index 1..12), uppercased for the title.
RO_MONTHS = [
    "", "IANUARIE", "FEBRUARIE", "MARTIE", "APRILIE", "MAI", "IUNIE",
    "IULIE", "AUGUST", "SEPTEMBRIE", "OCTOMBRIE", "NOIEMBRIE", "DECEMBRIE",
]
# Weekday initials Monday..Sunday, matching the UI's L/Ma/Mi/J/V/S/D.
RO_WEEKDAY_INITIALS = ["L", "Ma", "Mi", "J", "V", "S", "D"]
SECTION_LABELS = {"BIG": "Copii Mari", "SMALL": "Sugari"}

_WEEKEND_FILL = PatternFill("solid", fgColor="E8E8E8")   # light gray
_HOLIDAY_FILL = PatternFill("solid", fgColor="F6D9DE")   # light rose
_SECTION_FILL = PatternFill("solid", fgColor="DCE6F1")   # pale blue band
_HEADER_FILL = PatternFill("solid", fgColor="F2F2F2")
_THIN = Side(style="thin", color="B0B0B0")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_CENTER = Alignment(horizontal="center", vertical="center")
_LEFT = Alignment(horizontal="left", vertical="center")


def _cell_code(shift: Shift, duration: int) -> str:
    """Grid code identical to the UI: Z, N, K->'8', H->'8', 8->'8', T->duration."""
    if shift in (Shift.KITCHEN, Shift.HEAD, Shift.EIGHT):
        return "8"
    if shift is Shift.TOPUP:
        return str(duration)
    return shift.value  # Z or N


def build_workbook(
    *,
    year: int,
    month: int,
    nurses: list[Nurse],          # pre-sorted in the manager's display order;
                                  # a section band is emitted whenever the
                                  # section changes (interleaving -> repeats)
    assignments: list,            # list[Assignment]
    leave: dict[int, set[dt.date]],
    holidays: set[dt.date],
    totals: dict[int, NurseTotals],
    per_day: dict[dt.date, dict[str, int]],
    staff_type: str = "asistenta",
) -> bytes:
    is_aide = staff_type == STAFF_AIDE
    dates = cal.month_dates(year, month)
    n_days = len(dates)

    wb = Workbook()
    ws = wb.active
    ws.title = "GRAFIC"

    # Column layout: A = Nr. crt., B = name, then one per day, then 4 totals.
    first_day_col = 3
    last_day_col = first_day_col + n_days - 1
    total_cols = ["Total ore", "Ore 100%", "Nopți", "Co"]
    first_tot_col = last_day_col + 1
    last_col = first_tot_col + len(total_cols) - 1

    day_col = {d: first_day_col + i for i, d in enumerate(dates)}
    weekend_cols = {day_col[d] for d in dates if d.weekday() >= 5}
    holiday_cols = {day_col[d] for d in dates if d in holidays}

    def col_fill(col: int) -> PatternFill | None:
        if col in holiday_cols:   # holiday wins over weekend
            return _HOLIDAY_FILL
        if col in weekend_cols:
            return _WEEKEND_FILL
        return None

    # --- 1. title block ----------------------------------------------------
    subtitle = "Secția Pediatrie — INFIRMIERE" if is_aide else "Secția Pediatrie"
    title_lines = [
        ("SPITALUL JUDEȚEAN DE URGENȚĂ ZALĂU", 12),
        (subtitle, 11),
        (f"GRAFIC DE LUCRU — {RO_MONTHS[month]} {year}", 11),
    ]
    row = 1
    for text, size in title_lines:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_col)
        c = ws.cell(row=row, column=1, value=text)
        c.font = Font(bold=True, size=size)
        c.alignment = _CENTER
        row += 1
    row += 1  # blank spacer

    header_num_row = row
    header_wd_row = row + 1
    body_start_row = row + 2

    # --- 2. header rows ----------------------------------------------------
    hdr_crt = ws.cell(row=header_num_row, column=1, value="Nr. crt.")
    hdr_name = ws.cell(row=header_num_row, column=2, value="Nume și prenume")
    ws.merge_cells(start_row=header_num_row, start_column=1, end_row=header_wd_row, end_column=1)
    ws.merge_cells(start_row=header_num_row, start_column=2, end_row=header_wd_row, end_column=2)
    for d in dates:
        col = day_col[d]
        ws.cell(row=header_num_row, column=col, value=d.day)
        ws.cell(row=header_wd_row, column=col, value=RO_WEEKDAY_INITIALS[d.weekday()])
    for i, label in enumerate(total_cols):
        col = first_tot_col + i
        ws.cell(row=header_num_row, column=col, value=label)
        ws.merge_cells(start_row=header_num_row, start_column=col, end_row=header_wd_row, end_column=col)

    for c in (hdr_crt, hdr_name):
        c.font = Font(bold=True)
        c.alignment = _CENTER
    for col in range(1, last_col + 1):
        for r in (header_num_row, header_wd_row):
            cell = ws.cell(row=r, column=col)
            cell.font = Font(bold=True)
            cell.alignment = _CENTER
            cell.border = _BORDER
            fill = col_fill(col) or _HEADER_FILL
            cell.fill = fill

    # --- 3-6. nurse rows grouped by section --------------------------------
    code_by_cell: dict[tuple[int, dt.date], str] = {}
    for a in assignments:
        code_by_cell[a.nurse_id, a.date] = _cell_code(a.shift, a.duration)
    for nid, days in leave.items():
        for d in days:
            code_by_cell.setdefault((nid, d), "Co")

    r = body_start_row
    crt = 0
    # Infirmiere have no sections: one ward-wide band titled "Infirmiere".
    last_section: str | None = None
    for nurse in nurses:
        band_key = "" if is_aide else nurse.section
        if band_key != last_section:
            # section label row spanning the whole grid
            band_label = "Infirmiere" if is_aide else SECTION_LABELS.get(nurse.section, nurse.section)
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=last_col)
            label = ws.cell(row=r, column=1, value=band_label)
            label.font = Font(bold=True, italic=True)
            label.alignment = _LEFT
            label.fill = _SECTION_FILL
            for col in range(1, last_col + 1):
                ws.cell(row=r, column=col).border = _BORDER
                if col != 1:
                    ws.cell(row=r, column=col).fill = _SECTION_FILL
            r += 1
            last_section = band_key

        crt += 1
        ws.cell(row=r, column=1, value=crt).alignment = _CENTER
        ws.cell(row=r, column=2, value=nurse.name).alignment = _LEFT
        for d in dates:
            col = day_col[d]
            cell = ws.cell(row=r, column=col, value=code_by_cell.get((nurse.id, d), ""))
            cell.alignment = _CENTER
        t = totals.get(nurse.id)
        tot_values = (
            [t.total, t.worked_weekend, t.nights, t.leave_days] if t is not None else [0, 0, 0, 0]
        )
        for i, val in enumerate(tot_values):
            ws.cell(row=r, column=first_tot_col + i, value=val).alignment = _CENTER

        for col in range(1, last_col + 1):
            cell = ws.cell(row=r, column=col)
            cell.border = _BORDER
            fill = col_fill(col)
            if fill is not None:
                cell.fill = fill
        r += 1

    # --- 7. coverage rows --------------------------------------------------
    if is_aide:
        coverage_rows = [
            ("Zi (Z)", "Z"),
            ("Noapte (N)", "N"),
            ("Tură 8h", "E8"),
        ]
    else:
        coverage_rows = [
            ("Zi (Copii Mari)", "Z_big"),
            ("Zi (Sugari)", "Z_small"),
            ("Noapte (Copii Mari)", "N_big"),
            ("Noapte (Sugari)", "N_small"),
            ("Bucătărie", "K"),
        ]
    for label, key in coverage_rows:
        lc = ws.cell(row=r, column=1, value=label)
        lc.font = Font(italic=True)
        lc.alignment = _LEFT
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
        for d in dates:
            col = day_col[d]
            count = per_day.get(d, {}).get(key, 0)
            ws.cell(row=r, column=col, value=count).alignment = _CENTER
        for col in range(1, last_col + 1):
            cell = ws.cell(row=r, column=col)
            cell.border = _BORDER
            fill = col_fill(col)
            if fill is not None and col >= first_day_col:
                cell.fill = fill
        r += 1

    # --- 8. footer ---------------------------------------------------------
    r += 1
    left = ws.cell(row=r, column=1, value="Întocmit: asistentă șefă ________________")
    left.alignment = _LEFT
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=max(2, last_day_col // 2))
    right = ws.cell(row=r, column=first_tot_col, value="Data: ____________")
    right.alignment = _LEFT
    ws.merge_cells(start_row=r, start_column=first_tot_col, end_row=r, end_column=last_col)

    # --- 9. print niceties -------------------------------------------------
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 22
    for d in dates:
        ws.column_dimensions[get_column_letter(day_col[d])].width = 3.5
    for i in range(len(total_cols)):
        ws.column_dimensions[get_column_letter(first_tot_col + i)].width = 9

    # Freeze name column + header rows so both stay visible while scrolling.
    ws.freeze_panes = f"{get_column_letter(first_day_col)}{body_start_row}"

    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.print_options.horizontalCentered = True

    _set_print_area(ws, last_col, r)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _set_print_area(ws: Worksheet, last_col: int, last_row: int) -> None:
    ws.print_area = f"A1:{get_column_letter(last_col)}{last_row}"
