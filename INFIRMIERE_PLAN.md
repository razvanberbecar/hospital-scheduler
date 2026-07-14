# Plan: Infirmiere as a second staff type with a fully separate schedule

Date: 2026-07-14. Extends plan docx v0.9. Decided with the owner:

- Pediatrie has two staff types: **asistente** (nurses, everything built so far) and
  **infirmiere** (nurse aides). They are scheduled completely separately.
- Infirmiere have **no sections** — one ward-wide pool (users.section is NULL for them).
- Infirmiere daily coverage (HARD): **1 Z + 1 N + 1 × 8h shift**. Exception, penalized and
  used only when leave/hour targets force it: the 8h slot becomes a second Z → **2 Z + 1 N**.
  The manager may also produce 2Z+1N by hand-editing; the validator accepts both patterns.
- All other rules are IDENTICAL to nurses: rest 24h after Z / 48h after N (incl. prev_tail
  boundary), min 3 nights (with leave relaxation), monthly hour target + band, weekend ≤ normal
  worked hours, Co leave with 8h/day credit, requests (soft/hard), top-up T shifts (max 1/month,
  working days only), cell locking, regeneration around locks, Check, approval snapshots,
  reopen, month locking.
- **No kitchen (K) and no head (H) for infirmiere**; no "head infirmiera". Kitchen/head stay
  exclusive to the nurse schedule.
- Infirmiere get **login accounts** like nurses (role "nurse", read-only view of their own
  approved schedules).
- **Fully separate lifecycle**: one schedule per (year, month, staff_type). The draft→approved
  →locked chain, prev_tail, re-approval and month-locking logic all operate **within one staff
  type only**. Approving infirmiere July must NOT lock nurse June, etc.

## Design

### Domain (backend/app/solver/domain.py)
- `STAFF_NURSE = "asistenta"`, `STAFF_AIDE = "infirmiera"` constants.
- New shift: `Shift.EIGHT = "8"` — 8h daytime shift (07:00–15:00), exists every calendar day
  (weekends/holidays too), infirmiere schedules only. `FIXED_DURATION[EIGHT] = 8`.
  Hour buckets exactly like K: `cal.shift_hours(day, shift, 8, holidays)` (calendar.py already
  handles any non-night shift generically — verify, add EIGHT to `duration_of`).
- `Nurse` gains nothing (section stays; it is `None`/ignored for infirmiere).
- `ScheduleInput` gains `staff_type: str = STAFF_NURSE`.
- `Config` gains `w_eight_swap: int = 2000` — cost per day where the 8h slot is swapped to a
  second Z. Rationale: w_dev is 1000/hour, so a swap (adds 4h capacity) is chosen only when it
  saves ≥2h of total deviation or coverage is otherwise infeasible from leave. Keep it a Config
  field so it is tunable via the schedule config JSON like the others.
- Requests: kinds for infirmiere are Z | N | FREE (K is rejected at the API for infirmiere
  schedules; no request kind for the 8h shift — the manager can lock/hand-place an 8 cell).

### Solver (backend/app/solver/model.py)
Parametrize `solve()` by `inp.staff_type`; do NOT fork the module. For infirmiere:
- No head handling (no `_head_assignments`), no K variables, no kitchen soft terms.
- Per worker/day variables: Z, N, EIGHT (+ T4/T8 on working days, same as nurses).
- Rest rules: EIGHT behaves like K — it is in `day_vars` (blocked after N and can't follow a Z
  the next day / can't be on the same day as another shift) but places no constraint on the
  following day. Prev-tail boundary logic unchanged; a prev-tail "8" behaves like K there too
  (i.e. no carry-over restriction from it).
- Coverage per day d (replaces the per-section block):
  - `swap_d = BoolVar`; `sum(EIGHT_d) == 1 - swap_d`
  - `sum(Z_d) >= 1 + swap_d`
  - `sum(N_d) >= 1`
  - caps: `sum(Z_d) <= 1 + swap_d + cfg.extra_day_cap`, `sum(N_d) <= 1 + cfg.extra_night_cap`
  - objective += `cfg.w_eight_swap * sum(swap_d)`
- Locks: an "8" lock pins the EIGHT var (analogous to K). Everything else (nights min,
  requests, hours/band, spreads minus kitchen, cluster) carries over unchanged.
- `_diagnose` needs an infirmiere branch: soft-slack the Z/N/EIGHT coverage instead of the
  per-section slots.

### Prechecks (backend/app/solver/prechecks.py)
Branch by staff_type: for infirmiere skip section checks, kitchen-capable checks and head
checks; require a non-empty pool and enough staff for daily 1N + rest rules (reuse the generic
capacity math where possible; readable Romanian-friendly messages like the existing ones).
Reject/ignore K requests for infirmiere (kept out at the API layer too).

### Validator (backend/app/solver/validator.py)
Branch coverage by `inp.staff_type`:
- Infirmiere per-day: `report.per_day[day] = {"Z": z, "N": n, "E8": e}`.
  Violations when: `n < 1`, or `z < 1`, or `e > 1`, or `e + min(z,2) < 2` — i.e. the day is
  neither 1Z+1N+1×8 nor 2Z+1N at minimum; caps as in the model. A 2Z+1N day (e==0, z>=2) is
  VALID (no violation, no warning needed — it's an accepted exception; the swap count can be
  reported as an info-level line if cheap).
- Shift legality: "8" on a nurse schedule → violation; K or H on an infirmiere schedule →
  violation; skip head_missing / kitchen checks for infirmiere.
- Everything else (rest, double shift, top-ups, buckets, band, nights min, weekend rule,
  requests, cluster) runs unchanged. `NurseTotals.kitchens` stays 0 for infirmiere.

### DB (backend/app/models/tables.py + db.py)
- `users.staff_type` String(12) NOT NULL default `"asistenta"`; `users.section` becomes
  nullable (NULL for infirmiere; still required BIG|SMALL for asistente — enforce in schemas).
- `schedules.staff_type` String(12) NOT NULL default `"asistenta"`; unique constraint becomes
  `(year, month, staff_type)` (rename `uq_schedule_month` → `uq_schedule_month_staff`).
- Extend `run_schema_fixups` (db.py) idempotently for existing DBs (Postgres AND SQLite):
  add both columns with defaults, make users.section nullable, and swap the unique constraint
  (on Postgres: `ALTER TABLE ... DROP CONSTRAINT IF EXISTS uq_schedule_month; ADD CONSTRAINT ...`;
  on SQLite the constraint is baked into the table from create_all so fresh DBs are fine —
  follow the existing fixup style and extend tests/test_schema_fixup.py).

### API
- users (api/users.py + models/schemas.py): `staff_type` on create/update/out; validation:
  asistenta ⇒ section required BIG|SMALL; infirmiera ⇒ section must be null, kitchen flag
  forced False, role must be "nurse" (a manager is always an asistenta). staff_type of an
  account is immutable after creation (simplest safe rule; document it).
- schedules (api/schedules.py):
  - `ScheduleCreate` gains `staff_type` (default "asistenta"); duplicate check, `_prev_tail_
    from_previous`, approve's previous/next month lookups and re-approve guard ALL filter by
    `Schedule.staff_type`. **This is the highest-risk area — audit every
    `select(Schedule).where(year, month)` in the file.**
  - Roster validation: every rostered user must have `user.staff_type == schedule.staff_type`;
    head-count rule only applies to nurse schedules; kitchen_capable forced False on
    infirmiere rosters.
  - Requests: kind K rejected (422) on infirmiere schedules.
  - Edits: shift "8" allowed only on infirmiere schedules; H and K rejected (422) there.
  - Non-manager readers see only schedules of **their own staff_type** (list + detail).
  - List/detail payloads include `staff_type`. `DayCoverageOut` gains optional `Z`, `N`, `E8`
    ints (nurse schedules keep the existing per-section fields; make all coverage fields
    optional-with-default so both shapes fit one schema).
  - Export: filename `GRAFIC_Pediatrie_Infirmiere_YYYY-MM.xlsx` for infirmiere.
- Generate/validate/approve/reopen/delete: no signature changes, they follow the schedule row.

### Export (backend/app/export/xlsx.py)
Infirmiere variant: single staff band titled "Infirmiere" (no section grouping), legend/
coverage rows Z / N / 8, no kitchen column in totals; same header, weekend/holiday shading,
Total ore / Ore 100% columns, signature lines. Title row should say INFIRMIERE so the paper
forms are distinguishable.

### Frontend (frontend/src)
- api client + types: `staff_type` on User and Schedule; new coverage fields.
- Accounts.tsx: staff-type select (Asistentă/Infirmieră); section + kitchen controls hidden
  for infirmiere.
- SchedulesList.tsx: two tabs or a labelled split — Asistente / Infirmiere; create dialog
  carries the active staff type; month rows show their staff type.
- ScheduleSetup.tsx: roster picker lists only users of the schedule's staff type; kitchen
  column and K request kind hidden for infirmiere.
- ScheduleDetail.tsx: for infirmiere — no section bands (one group), cell palette Z/N/8/T/Co
  (no K/H), coverage footer shows Z / N / 8 per day, totals table without the kitchen column.
- Nurse (read-only) view already filters by approved; it now naturally shows the viewer's own
  staff type because of the API read filter.
- i18n: ro + en strings added together (Infirmieră/Infirmiere, "Schimb de 8 ore", tab labels,
  validation messages surfaced from the API stay backend-side).

### Tests (backend/tests) — extend, all existing 116 must stay green
- Solver: infirmiere happy path hits 1Z+1N+1×8 daily; a leave-heavy scenario proves the solver
  uses 2Z+1N only then (assert swap days == 0 in the unconstrained case); nights min, rest
  rules, band all hold; validator parity (validate(solve output) is clean) — mirror the
  existing test style.
- Validator: 2Z+1N accepted; 1Z+1N without 8 rejected; K/H on infirmiere rejected; "8" on
  nurse schedule rejected.
- API: separate lifecycle — create both staff types same month; approving infirmiere M does
  not lock nurse M-1 and vice versa; prev_tail drawn per staff type; roster staff-type
  mismatch 422; K request 422; read filtering for an infirmiera account; export smoke test.
- Schema fixup: old-shape DB gains the new columns/constraint (extend test_schema_fixup.py).

### Non-goals (v1 of this feature)
No head infirmiera, no kitchen for infirmiere, no request kind for the 8h shift, no combined
export, no cross-type constraints. staff_type immutable on accounts.

### Deployment note for the owner
The schema fixup handles the live Postgres volume on next backend start — no manual migration,
but the Docker images must be rebuilt (`docker compose build && docker compose up -d`).
