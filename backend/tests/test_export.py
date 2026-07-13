"""Excel export tests (milestone M6).

Fast on purpose: no full 14-nurse solve. We create a draft, hand-PATCH a couple
of cells, then export and re-parse the workbook. The export endpoint is
manager-only and works for any status (draft included).
"""
from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook

from tests.api_helpers import add_user, auth, login

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# A small roster that still passes the roster guards: one head + a few nurses,
# at least two kitchen-capable. Sections mixed so both groups appear.
SMALL_ROSTER = [
    ("drulea.florica", "BIG", True, "nurse"),
    ("prodan.gabriela", "BIG", False, "nurse"),
    ("rusu.ana", "SMALL", True, "nurse"),
    ("lung.paula", "SMALL", False, "nurse"),
    ("anca.berbecar", "SMALL", False, "manager_nurse"),
]


def _seed(app) -> dict[str, int]:
    return {u: add_user(app, u, section=s, kitchen=k, role=r) for u, s, k, r in SMALL_ROSTER}


def _create_draft(client, h, ids, days_off=None) -> int:
    body = {
        "year": 2026, "month": 7, "target_hours": 184,
        "roster": [{"user_id": uid} for uid in ids.values()],
        "days_off": days_off or [], "requests": [], "config": {"max_time_s": 5},
    }
    r = client.post("/api/schedules", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _patch(client, h, sid, user_id, date, shift, duration=None):
    edit = {"user_id": user_id, "date": date, "shift": shift}
    if duration is not None:
        edit["duration_hours"] = duration
    r = client.patch(f"/api/schedules/{sid}/assignments", headers=h, json={"edits": [edit]})
    assert r.status_code == 200, r.text
    return r.json()


def _manager(client):
    ids = _seed(client.app)
    return auth(login(client, "anca.berbecar")), ids


def _open(content: bytes):
    return load_workbook(BytesIO(content))["GRAFIC"]


def _header_row(ws) -> int:
    for r in range(1, ws.max_row + 1):
        if ws.cell(row=r, column=1).value == "Nr. crt.":
            return r
    raise AssertionError("header row 'Nr. crt.' not found")


def test_export_content_type_and_filename(client):
    h, ids = _manager(client)
    sid = _create_draft(client, h, ids)
    r = client.get(f"/api/schedules/{sid}/export.xlsx", headers=h)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == XLSX_MEDIA_TYPE
    assert r.headers["content-disposition"] == \
        'attachment; filename="GRAFIC_Pediatrie_2026-07.xlsx"'


def test_export_workbook_content(client):
    h, ids = _manager(client)
    # Prodan has a Co day on Mon Jul 6 - leave days must render in the export.
    sid = _create_draft(client, h, ids, days_off=[
        {"user_id": ids["prodan.gabriela"], "date": "2026-07-06"}])
    # A kitchen cell on a weekday (Jul 7, Tuesday) for a kitchen-capable nurse,
    # and a night on Jul 8 for a different nurse.
    _patch(client, h, sid, ids["drulea.florica"], "2026-07-07", "K")
    v = _patch(client, h, sid, ids["rusu.ana"], "2026-07-08", "N")

    r = client.get(f"/api/schedules/{sid}/export.xlsx", headers=h)
    assert r.status_code == 200, r.text
    ws = _open(r.content)

    # Title block + section labels.
    assert ws["A1"].value == "SPITALUL JUDEȚEAN DE URGENȚĂ ZALĂU"
    col1 = [ws.cell(row=rr, column=1).value for rr in range(1, ws.max_row + 1)]
    assert "GRAFIC DE LUCRU — IULIE 2026" in col1
    assert "Copii Mari" in col1 and "Sugari" in col1

    hdr = _header_row(ws)
    assert ws.cell(row=hdr, column=3).value == 1  # first day number

    # A known nurse name is present; find her row.
    def row_of(name: str) -> int:
        for rr in range(hdr + 2, ws.max_row + 1):
            if ws.cell(row=rr, column=2).value == name:
                return rr
        raise AssertionError(f"nurse {name} not in sheet")

    drulea_row = row_of("Drulea Florica")
    # Jul 7 -> column 3 + 7 - 1 = 9; kitchen renders as "8".
    assert ws.cell(row=drulea_row, column=9).value == "8"
    rusu_row = row_of("Rusu Ana")
    assert ws.cell(row=rusu_row, column=10).value == "N"  # Jul 8 -> col 10
    # Leave renders as "Co" (Jul 6 -> col 8) and counts in the Co totals column.
    prodan_row = row_of("Prodan Gabriela")
    assert ws.cell(row=prodan_row, column=8).value == "Co"
    assert ws.cell(row=prodan_row, column=ws.max_column).value == 1

    # Weekend column shading: Jul 4 is a Saturday -> column 6 header gray fill.
    fill = ws.cell(row=hdr, column=6).fill.fgColor.rgb
    assert str(fill).endswith("E8E8E8"), fill

    # Totals cell matches the validator total for that nurse. Total ore is the
    # first of the four totals columns (max_column-3).
    total_col = ws.max_column - 3
    assert ws.cell(row=hdr, column=total_col).value == "Total ore"
    detail = client.get(f"/api/schedules/{sid}", headers=h).json()
    rusu_total = next(t["total"] for t in detail["totals"] if t["user_id"] == ids["rusu.ana"])
    assert ws.cell(row=rusu_row, column=total_col).value == rusu_total
    # Rusu worked one 12h night this month.
    assert rusu_total == 12 and v["ok"] is False  # partial grid has coverage violations


def test_export_is_manager_only(client):
    h, ids = _manager(client)
    sid = _create_draft(client, h, ids)
    nurse_h = auth(login(client, "lung.paula"))
    # A nurse is rejected (manager-only), and a draft is invisible to her anyway.
    assert client.get(f"/api/schedules/{sid}/export.xlsx", headers=nurse_h).status_code == 403


def test_export_available_for_draft(client):
    h, ids = _manager(client)
    sid = _create_draft(client, h, ids)
    # Draft (never generated/approved) still exports 200.
    r = client.get(f"/api/schedules/{sid}/export.xlsx", headers=h)
    assert r.status_code == 200
    assert len(r.content) > 0


def test_export_row_order_follows_manager_position(client):
    """Export nurse rows honor the manager's global order (users.position). A
    custom, section-interleaved order produces the corresponding row order (and
    more than one section band, which the export tolerates)."""
    h, ids = _manager(client)
    all_ids = [u["id"] for u in client.get("/api/users", headers=h).json()]
    custom = list(reversed(all_ids))
    assert client.put("/api/users/order", headers=h,
                      json={"ordered_ids": custom}).status_code == 200

    sid = _create_draft(client, h, ids)
    r = client.get(f"/api/schedules/{sid}/export.xlsx", headers=h)
    assert r.status_code == 200, r.text
    ws = _open(r.content)
    hdr = _header_row(ws)

    # Collect nurse-name rows (column 2 non-empty, excluding section bands which
    # only fill column 1). Compare against the expected full_name order.
    id_by_name = {u: uid for u, uid in ids.items()}
    name_of = {uid: u.replace(".", " ").title() for u, uid in id_by_name.items()}
    sheet_names = [ws.cell(row=rr, column=2).value
                   for rr in range(hdr + 2, ws.max_row + 1)
                   if ws.cell(row=rr, column=2).value in name_of.values()]
    expected = [name_of[uid] for uid in custom]
    assert sheet_names == expected
