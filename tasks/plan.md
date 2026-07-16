# Death ID Hardening Plan (v2 — Death mints, VASA carries)

Specs: `docs/death_id_assignment.md`, `docs/death_id_hardening_prompt.md`
Branch: dev/vasa

## Context

Death IDs (`NNNNCCSSSS`: 4-digit base, 2 checksum letters, 4-digit sequence)
uniquely identify deaths within a ward cluster. The division of responsibility:

- **Death records** (community `E_DEATH` form) are registered **without** a
  death ID — verified: `E_DEATH.xlsx` has no death-ID field. **VACMS creates
  and assigns** the ID after import.
- **VASA records** (facility form `znphifacilityvasa_02_final.xlsx`) arrive
  **with** a DeathID already entered by the interviewer — the form captures it
  in the `DeathID` field (stored on `VASASocialAutopsy.DeathID`, a blank-able
  TextField) and validates it in-form with a checksum + regex constraint.
  **VACMS must never generate or overwrite these** — only validate and flag.

The existing implementation is partial and has verified bugs (below); the
facility form's regex bug means interviewers can only enter IDs ending
0001–0009, rejecting 99% of valid IDs.

## Verified current state (dev DB + code audit, 2026-07-08)

Data:
- 2,095 Death rows, **all missing death_id** (clean backfill; no malformed IDs).
- `ClusterLocationCodes` table **empty**; `wards.csv` has 250 wards.
- All 75 distinct `Death.ward` values match wards.csv **ward names**; 0 match
  numeric `ward_code`. Max deaths in one ward: 92 (0001–0999 cap is safe).

Code:
- `generate_ward_codes.py` checksum **already matches the spec** (weights
  3,5,7,9 / 9,7,5,3 mod 19; letter map R,G,A,B,K,Y,N,C,L,S,H,Z,D,E,J,P,F,T,M;
  cluster code = digits + letter(3579-sum) + letter(9753-sum)) — but buried in
  ~140 lines of pandas.
- `assign_death_ids.py` command and `tasks.py::assign_death_ids_task` carry
  **duplicated** assignment logic.
- **BUG:** both match `ClusterLocationCodes.ward_code__iexact = Death.ward`;
  Death.ward holds *names*, ward_code holds *numeric codes* → zero matches
  even after loading wards.csv. Must match the `ward` (name) column.
- Death `post_save` signal queues the task (single saves only);
  `load_death_csv`/`load_form_csv` use `bulk_create` → **no signal** → imports
  wait up to 15 min for the periodic task. No lock anywhere → signal/periodic/
  manual runs can race and duplicate sequences.
- `Death.death_id` = plain TextField, **no unique constraint**. Migration 0019
  created the column, yet both commands still carry subprocess-makemigrations
  / raw `ALTER TABLE` fallbacks (dead weight; remove).
- Sequence formatting `{seq:04d}` is unbounded → could emit IDs the form
  rejects (>0999).
- **Form bug confirmed in-repo** at `va_explorer/static/data/
  znphifacilityvasa_02_final.xlsx`: `string-length(.) =10 and regex(string(.),
  '[1-9][0-9][0-9][0-9][A-Z][A-Z](000[1-9]|0[1-9][0-9]|[1-9][0-9][0-9])?')` —
  with length 10 enforced, only suffixes 0001–0009 validate. Also `[A-Z][A-Z]`
  accepts any letters, not the 19-letter checksum alphabet.
- VASA import path: `utils/vasa_loading.py::load_vasa_from_csv` →
  `VASA.objects.create(...)` + `VASASocialAutopsy.objects.update_or_create(...)`
  (~line 221–224). VASA is not created by the ODK VA pull.
- DQ machinery to reuse: `utils/dq.py` (`upsert_issue`,
  `resolve_missing_issues`, stable signatures) — same pattern as
  `utils/repeat_group_dedup.py`.
- No tests exist for `assign_death_ids` / `generate_ward_codes`.

## User decisions (confirmed)
- Invalid VASA DeathID at import → **flag as DataQualityIssue, import anyway**.
- Cross-check VASA DeathID against `Death.death_id` → **lookup + flag
  unmatched as DQ issue; no FK/schema change**.
- Suffix range stays **0001–0999** (per form intent); overflow reported loudly.

## Implementation

### 1. Shared utility `va_explorer/va_data_management/utils/death_ids.py`
- `CHECKSUM_LETTERS = "RGABKYNCLSHZDEJPFTM"` (index = mod-19 value)
- `checksum_letters(base4)` → two letters (3,5,7,9 then 9,7,5,3 weights)
- `build_prefix(base4)` → `NNNNCC` (validates `[1-9][0-9]{3}`)
- `DEATH_ID_REGEX = r"^[1-9][0-9]{3}[RGABKYNCLSHZDEJPFTM]{2}0[0-9]{3}$"`
- `is_valid_death_id(v)` → regex + checksum recompute + suffix ≠ 0000
- `format_death_id(cluster_code, seq)` → raises if seq outside 1–999
- `extract_sequence(death_id)` → int | None (valid IDs only)
- `assign_death_ids(*, dry_run=False, death_ids=None)` — shared service:
  - ward→cluster: `ClusterLocationCodes.ward__iexact` (name) with
    `ward_code__iexact` fallback  ← fixes the zero-match bug
  - sequence continues from highest **valid** existing ID per cluster prefix;
    malformed existing IDs reported, excluded from continuation, never
    overwritten
  - deterministic order: parsed `DE_06` then `id`; unparseable dates last
  - idempotent; only fills blank death_id; `death_ids=` scopes to PKs
  - serialized under a Redis cache lock (signal/periodic/manual can't race)
  - returns summary: assigned, skipped_wards, malformed_existing, overflow

### 2. Migration (va_data_management)
- Conditional `UniqueConstraint` on `Death.death_id` (when set/non-blank) —
  DB-level backstop. Safe: zero IDs assigned on this DB.

### 3. Rewire existing call sites
- `management/commands/assign_death_ids.py` → thin wrapper over the service;
  keep `--dry-run` + preview table; add malformed/skipped reporting; **delete**
  the auto-migration/ALTER TABLE code.
- `tasks.py::assign_death_ids_task` → call the service (delete ~120 duplicated
  lines); keep 900s periodic schedule + post_save signal as sweepers.
- `generate_ward_codes.py` → use `checksum_letters()` instead of the pandas
  checksum block (keep candidate-filtering rules; add an equivalence test).

### 4. Death ingest hooks (mirror household-member dedup pattern)
- `load_death_csv` + `load_form_csv` (form "death"): after `bulk_create`, call
  `assign_death_ids(death_ids=<created PKs>)` synchronously. Re-imports stay
  idempotent (existing IDs untouched).

### 5. VASA-side validation (new; per confirmed decisions)
- In `utils/vasa_loading.py`, where `VASASocialAutopsy` is created/updated:
  collect each record's `DeathID` and after load run a validation pass:
  - blank DeathID → no issue (VAID_decide covers "don't have it")
  - fails `is_valid_death_id` → upsert `DataQualityIssue` (type VALIDITY or
    DUPLICATE-style stable signature via `utils/dq.py::upsert_issue`),
    title "VASA Death ID invalid"
  - valid but no `Death.death_id` match → upsert issue "VASA Death ID not
    found in death register"
  - never mutate the stored DeathID; import always proceeds
  - idempotent + self-healing on re-run (`resolve_missing_issues`)
- Report counts in `load_vasa_csv` summary output.

### 6. Fix the facility VASA form (in-repo)
- Edit constraint in `va_explorer/static/data/znphifacilityvasa_02_final.xlsx`
  (survey sheet, DeathID row) to:
  `regex(., '^[1-9][0-9]{3}[RGABKYNCLSHZDEJPFTM]{2}0[0-9]{3}$')` (full-string;
  keeps `string-length(.) = 10` redundant-but-harmless or drop it).
- Run `manage.py generate_vasa_mapping --check` to confirm no field-map drift
  (constraint text does not affect field defs).
- `E_DEATH.xlsx` needs **no change** (no death-ID field — by design).
- Deployment note: upload revised form to ODK Central (external step).

### 7. Docs
- Update `docs/death_id_assignment.md`: format spec + checksum table, worked
  example (2026 → TL), Death-mints/VASA-carries split, ward-name matching,
  ingest-time assignment, malformed-ID policy, VASA validation DQ issues,
  removal of auto-migration behavior, runbook.

## Files touched
- NEW `va_explorer/va_data_management/utils/death_ids.py`
- NEW migration (unique constraint)
- NEW `va_explorer/va_data_management/tests/test_death_ids.py`
- `management/commands/assign_death_ids.py`, `generate_ward_codes.py`,
  `load_death_csv.py`, `load_form_csv.py`
- `tasks.py` (assign_death_ids_task), `utils/vasa_loading.py`
- `va_explorer/static/data/znphifacilityvasa_02_final.xlsx`
- `docs/death_id_assignment.md`

## Verification
- Unit: checksum 2026→TL; `2026TL0001` valid; `DID-2026-0001` invalid;
  suffixes 0001/0009/0010/0999 valid; 0000 & 1000 invalid; extract_sequence;
  format overflow raises; pandas-vs-helper checksum equivalence sample.
- Service: DE_06-then-id ordering; continuation from existing valid IDs;
  malformed IDs don't poison sequences and aren't overwritten; skipped wards
  reported; idempotent re-run; PK scoping; dry-run writes nothing.
- Commands: `assign_death_ids --dry-run` previews without writing; real run
  backfills; ward-name lookup resolves (use ClusterLocationCodes fixtures).
- Import: `load_death_csv` test CSV → created rows get valid IDs; re-import
  preserves them. **Use command/bulk paths, never `Death.objects.create()` in
  tests** (post_save→Celery broker hang; see tasks/lessons.md).
- VASA: `load_vasa_csv` fixture (`tests/fixtures/vasa/znphifacilityvasa_02.csv`)
  with valid/invalid/unmatched DeathIDs → correct DQ issues raised, records
  imported, re-run idempotent, issues resolve when fixed.
- Form: unzip xlsx in a test (or one-off check) asserting the DeathID
  constraint contains the new regex and not `000[1-9]`.
- Suites: `pytest va_explorer/va_data_management/tests/test_death_ids.py`,
  existing `test_repeat_group_dedup.py` (DQ machinery untouched), targeted
  vasa loading tests; `pre-commit run --files ...`; coverage ≥ 60%.
- End-to-end (dev DB): `generate_ward_codes --initial` →
  `assign_death_ids --dry-run` → review → `assign_death_ids` → spot-check IDs
  validate and are unique (2,095 rows).

## Runbook (deploy order)
1. Deploy code + migration.
2. `python manage.py generate_ward_codes --initial` (250 wards).
3. `python manage.py assign_death_ids --dry-run` → review skipped wards.
4. `python manage.py assign_death_ids` (backfills 2,095 deaths).
5. Upload fixed facility VASA form to ODK Central; verify a 0010-suffix ID
   passes in ODK Collect.
6. New deaths: ingest hooks + periodic task keep IDs assigned automatically;
   VASA imports validate DeathIDs and raise DQ issues.

(On approval: sync this plan into `tasks/plan.md` per project convention.)
