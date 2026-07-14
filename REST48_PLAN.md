# Plan: 48h-rest relaxation + infeasibility explanations + leave-crunch prechecks + startup seeding

Date: 2026-07-14 (evening). Owner decisions from today's August-2026 investigation:
- The paper GRAFIC for August is still in the making; summers are leave-heavy.
- The 48h rest after a night becomes a PENALIZED EXCEPTION ("rarely overlooked").
  The 24h rest stays ABSOLUTELY HARD: the first full day after a night is never worked.
- The three app improvements agreed earlier: (A) infeasible runs must explain which
  must-have requests cannot be honored, (B) the slack diagnosis must respect locks,
  (C) instant prechecks for section leave crunches with plain-language messages.
- Fresh databases self-seed the real 14-nurse roster; admin anca.berbecar / "anca.berbecar".

## 1. 48h rest -> soft (both staff types)

Semantics: a night N on day d ends 07:00 on d+1.
- Day d+1: ALL shifts hard-forbidden (unchanged - this is the 24h floor).
- Day d+2: any shift allowed but penalized (one penalty per night whose second
  rest day is worked). This replaces today's hard block.

domain.py:
- Config gains `w_rest48: int = 2000` - cost per worked second-day-after-night.
  Rationale: w_dev is 1000/h so an exception is taken only when it saves >=2h of
  deviation or is forced by coverage/hard pins; a soft request denial (500) stays
  cheaper, so wishes are dropped before rest is bent. Tunable via schedule config.

model.py solve() (the rest-rules block is shared by nurses and infirmiere):
- Keep the di+1 hard block exactly as today.
- di+2: for each night var nd of nurse n on day di, create ONE BoolVar
  `r48_{n}_{di}`; for every v in day_vars(n, di+2): m.Add(v + nd <= 1 + r48).
  (AddAtMostOne per day already guarantees at most one worked shift, so r48
  counts one exception per night.) objective += cfg.w_rest48 * sum(r48 vars).
- prev_tail boundary (same file, same loop):
  - last_prev is NIGHT: day 0 stays HARD-blocked; day 1 becomes soft - BoolVar
    per nurse, m.Add(v <= exc) for v in day_vars(n, 1), penalized w_rest48.
  - second_prev is NIGHT: day 0 becomes soft the same way (was hard).
- _diagnose(): its night blocks keep only the di+1 block (drop the di+2 block);
  it is a relaxation so this is safe and matches the new rule.

validator.py (shared rest section):
- prev1 is NIGHT -> stays a violation, code `rest_after_night`, message now says
  24h ("the first day after a night shift can never be worked").
- prev2 is NIGHT -> becomes a WARNING, new code `rest48_exception`, message like
  "{name}: works {shift} on {day}, only one full day after the night of {day-2} -
  48h-rest exception (allowed, keep rare)."

Existing tests in tests/test_rest_rules.py (and anywhere else) that assert the
second-day case is a violation must be UPDATED to assert the warning instead;
the first-day case stays a violation. Add solver tests proving:
- an exception is used only under pressure (e.g. a section down to 3 available
  nurses for 3 consecutive days now SOLVES, with >=1 rest48 exception and the
  validator reporting warnings, zero violations);
- an unconstrained month uses zero exceptions (r48 all 0; no new warnings).

## 2. (A) INFEASIBLE explains unsatisfiable must-have requests

model.py solve(), in the INFEASIBLE branch: if any kept hard requests exist,
re-solve ONCE: deep-copied input, every request hard=False, config copy with
w_request = 1_000_000 (denials then happen only when unavoidable, so the denied
set is the minimal explanation). skip_prechecks=True. The retry has no hard
requests so it cannot recurse.
- If the retry is ok: result.diagnosis = a header line ("The must-have requests
  cannot all be honored; a valid schedule exists without these:") followed by one
  line per ORIGINALLY-HARD request denied in the retry ("{name}: {kind} on
  {date}"). Keep status INFEASIBLE.
- If the retry is also infeasible: fall through to _diagnose as today.

## 3. (B) _diagnose respects locks

- Worker locks (Z/N/K/EIGHT): pin the matching var when it exists (same skip
  rules as solve()).
- Head locks (nurse schedules): a locked head Z/N adds a constant to that
  section's slack constraint for its day; a locked head K adds to the kitchen
  sum. (Prevents the misleading "day X short by 1" when the manager already
  covered day X with a locked head cell.)

## 4. (C) leave-crunch prechecks (math is for the RELAXED rule above)

Nurse schedules, per section, availability = non-head section workers not on Co
that day. A locked head Z/N on a day counts as +1 available for that day (lock-
aware so the head-cover workaround is not blocked).
- Each day: available < 2 -> ERROR (2 distinct people are needed for Z+N even
  with every exception; keep the existing pool==0 message for that case).
- Each 2-day window: |union of available| < 3 -> ERROR. Proof sketch: N(d)
  blocks d+1 (hard 24h), and after a Z only an N may follow next day, so two
  people cannot fill Z+N on two consecutive days.
- Each 3-day window: |union| < 4 -> WARNING: "Copii Mari: only {names} are
  available on {d}..{d+2} ({leave names} on Co) - expect 48h-rest exceptions."
Infirmiere schedules (no sections, pool-wide): each day available < 3 -> ERROR
(1 Z + 1 N + 1x8h - or 2Z+1N - always needs 3 distinct people).
Deduplicate overlapping windows sensibly (do not spam one issue per day for a
long crunch; one issue per maximal window is enough).
Messages follow the existing prechecks style, with nurse names.

## 5. Startup seeding (fresh databases only)

New app/seed.py: seed_default_staff(session, settings) -> None. If the users
table has ANY row, do nothing. Otherwise insert, positions 1..14 in this order:

| # | username | full name | section | kitchen | role |
|---|----------|-----------|---------|---------|------|
| 1 | drulea.florica | Drulea Florica | BIG | no | nurse |
| 2 | pop.delia | Pop Delia | BIG | no | nurse |
| 3 | prodan.gabriela | Prodan Gabriela | BIG | yes | nurse |
| 4 | codre.aurelia | Codre Aurelia | BIG | no | nurse |
| 5 | matei.sorina | Matei Sorina | BIG | no | nurse |
| 6 | pop.marinela | Pop Marinela | BIG | yes | nurse |
| 7 | campian.aurelia | Campian Aurelia | SMALL | yes | nurse |
| 8 | rusu.ana | Rusu Ana | SMALL | no | nurse |
| 9 | blaga.mirela | Blaga Mirela | SMALL | yes | nurse |
| 10 | lung.paula | Lung Paula | SMALL | yes | nurse |
| 11 | anca.berbecar | Berbecar Anca | BIG | no | manager_nurse |
| 12 | boda.erika | Boda Erika | SMALL | yes | nurse |
| 13 | ardelean.camelia | Ardelean Camelia | SMALL | yes | nurse |
| 14 | opris.elena | Opris Elena | SMALL | yes | nurse |

All staff_type "asistenta", active. anca.berbecar: password "anca.berbecar",
must_change_password=False. Everyone else: password "Parola123!",
must_change_password=True.

Wire-up: called from create_app after run_schema_fixups, gated by a new
Settings flag `seed_default_staff: bool = True`. tests/api_helpers.make_settings
(and the conftest fixtures if they build Settings directly) MUST pass
seed_default_staff=False so the 100+ existing API tests keep their empty-DB
assumption. Dedicated tests: fresh empty DB with the flag on -> 14 users,
login anca.berbecar/anca.berbecar succeeds without forced change, positions
and kitchen flags match the table; a DB that already has any user -> untouched.

## Acceptance (reviewer runs after delivery)

The August-2026 test schedule (id 4 in the dev SQLite DB) with its 35 hard
requests and NO locks must now generate FEASIBLE at target 176: all 35 must-have
requests honored, zero violations, some `rest48_exception` warnings, everyone
within the +/-12h band.
