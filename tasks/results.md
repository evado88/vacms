# Results

## 2026-07-03 — VASA visualisation: validation & finalisation (dev/vasa)

### Requirement coverage (docs/vasa_visualisation.md)
| Requirement | Commit | Verified by |
|---|---|---|
| Operations dashboard KPIs/map/graphs fold VASA | 50b3179 | home/tests/test_vasa_dashboard.py (KPI+chart, overview map, trends) |
| Outcomes dashboard VA analytics fold VASA + source filter | b6ccbe7 | va_analytics/tests/test_loading_utils.py (fold-in, source=vasa/va/all) |
| Baseline census VA figures include VASA | 0f10ad5 | test_vasa_dashboard.py::test_baseline_census_exclusion_still_counts_vasa (census reuses ops KPIs; no census-specific VA UI) |
| Line list VASA rows + Source column | c058b29 | test_vasa_line_list_and_detail.py (rows/routing, filters) |
| VASA detail: Social Autopsy + 3 repeat-group tabs + Data Quality | c058b29 | test_vasa_line_list_and_detail.py (tab render, location scoping) |
| MSO supervision source filter incl. VASA | 572eac7 | home/tests/test_vasa_mso.py (option present, default fold, vasa-only filter) |

### Files modified (this session)
- `va_explorer/va_analytics/tests/test_loading_utils.py` — root location tree at a country so geo subquery depths match production (6af7e2c)
- `va_explorer/home/va_trends.py` — `_trend_window()`: compute TODAY/START_MONTH/MONTHS per call instead of at import (4f86b43); fixes stale trends window in long-lived workers and import-order-dependent test_trends failure surfaced by the new VASA test modules
- `docs/vasa_visualisation.md` — committed requirements doc (685c784)

### Test state
- Targeted sweep (home/tests, va_analytics, 6 VASA test files in va_data_management): **275 passed, 7 failed**
- All 7 failures verified pre-existing at pre-VASA base 5673999 (4 baseline-census CSV/table + 3 CSA CSV; test user lacks `can_download_csv_exports`) — see tasks/lessons.md and project memory
- Coverage gate not runnable (full va_data_management suite hangs ~2 h, documented); branch ships ~410 lines of new tests

### Cache invalidation
- `home/services/cache.py` schema_version 3→4 for nov bundle + trends bundle (part of the feature commits) — self-invalidating on deploy, no manual version bump
- No static (CSS/JS) changes → no collectstatic / whitenoise.compress needed

### Deployment notes
- VASA migrations (0046, 0047) predate this session and must run before code deploy
- Line list intentionally excludes VASA rows from CSV/API export (va_export not wired for VASA; id collision would corrupt exports)

## 2026-07-07 — Harden household-member deduplication (ingest-time flagging)

### What was hardened
The dedup engine already guaranteed "one unique (most complete, earliest) row
per duplicate set" and all dashboard metrics already counted via `.canonical()`.
The gap: dedup only ran when `dq_households`/`dedupe_child_records` was run
manually, so members imported in between (member CSVs usually have NULL keys)
counted as unique until the next manual run. Dedup now runs automatically,
scoped to the affected households, inside both member import commands.

### Files Modified
- `va_explorer/va_data_management/utils/repeat_group_dedup.py` — `run_dedup_for_model()`
  gained `parent_ids` scoping: scans only children of those parents, self-heal
  unflag restricted to scope, never resolves DQ issues globally when scoped.
- `va_explorer/va_data_management/management/commands/load_household_members.py` —
  runs scoped dedup after bulk_create; reports flagged count.
- `va_explorer/va_data_management/management/commands/load_form_csv.py` — same,
  generically for any model with a ChildDedupSpec (household_member today).

### Tests Added (va_data_management/tests/test_repeat_group_dedup.py — 16 pass)
- `test_scoped_run_only_flags_in_scope_parents`
- `test_scoped_run_does_not_unflag_or_resolve_out_of_scope`
- `test_scoped_run_self_heals_within_scope`
- `test_scoped_run_with_no_parents_is_a_noop`
- `test_load_household_members_flags_duplicates_at_ingest` (incl. keyless re-import)
- `test_load_household_members_distinct_people_stay_unique`

### Cache Invalidation
- None needed: `home/signals.py` already bumps `home_dashboard_fastpath:version`
  on HouseholdMember changes; scoped dedup updates rows via queryset `.update()`
  — dashboards refresh via the 5-min pre-warm task as before.

### Celery Tasks
- None modified/added.

### Deployment Notes
- No migrations. Existing data already flagged (600,896 of 856,914 members).
- Scoped runs do NOT resolve stale DataQualityIssue rows; periodic/manual
  `dq_households` full runs still do that cleanup.
- Pre-existing test failures unrelated: 3 home CSA CSV + 4 census CSV export
  (permission fixture), see tasks/lessons.md history.

## 2026-07-08 — Death ID hardening (Death mints, VASA carries)

### What was built
Death IDs (`NNNNCCSSSS`, checksum letters over mod-19 sums) are now minted by
VACMS for Death records (imported without one) and only *validated* on VASA
records (which arrive carrying a field-entered DeathID). One shared module
backs the management command, Celery task, and import hooks.

### Files Modified / Added
- NEW `va_data_management/utils/death_ids.py` — checksum/validate/format/
  extract + `assign_death_ids()` service (pg advisory lock, DE_06→id ordering,
  malformed-ID quarantine, 0999 overflow guard, PK scoping, dry-run) +
  `validate_vasa_death_ids()` (DQ VALIDITY issues, self-healing)
- NEW migration `0048_death_id_unique_constraint` — conditional UniqueConstraint
  on Death.death_id (+ model Meta)
- `management/commands/assign_death_ids.py` — thin wrapper; auto-migration/
  ALTER TABLE hacks removed; ward lookup fixed (name column, was ward_code)
- `tasks.py::assign_death_ids_task` — delegates to service (~120 dup lines gone)
- `management/commands/generate_ward_codes.py` — pandas checksum block replaced
  with shared `checksum_letters()`; subprocess-migration fallback removed
- `load_death_csv.py`, `load_form_csv.py` — assign IDs at ingest (scoped PKs)
- `utils/vasa_loading.py`, `load_vasa_csv.py` — DeathID validation after load
- `va_explorer/static/data/znphifacilityvasa_02_final.xlsx` — DeathID
  constraint fixed (suffix 0001–0999, checksum alphabet); was only accepting
  0001–0009; `generate_vasa_mapping --check` clean
- `docs/death_id_assignment.md` — rewritten for the new behavior

### Tests Added (test_death_ids.py — 44 pass)
Checksum (2026→TL, 1213→SM), validation (2026TL0001 ok; DID-2026-0001,
0000/1000 suffixes, wrong letters rejected), suffix range 0001/0009/0010/0999,
service ordering/continuation/malformed/skip/dry-run/idempotency/scoping/
overflow, mixed-case ward regression, command dry-run+real, generated cluster
codes validate, VASA valid/invalid/unmatched/self-heal/blank, xlsx constraint.

### Bug found by the DB constraint during E2E
First real backfill hit `unique_death_id_when_set`: one ward exists under two
casings, and raw-string ward grouping let both groups mint the same sequence.
Fixed by normalised ward grouping + per-cluster sequence state for the run;
regression test added. (The constraint prevented any bad write.)

### E2E verification (dev DB)
- `generate_ward_codes --initial` → 249 wards coded
- `assign_death_ids --dry-run` → 2,095 previewed, 0 skipped wards
- `assign_death_ids` → 2,095 assigned; re-run: "No deaths without death_ids"
- Post-check: 0 missing, 0 invalid, all unique

### Deployment Notes
- Migration must run before backfill (constraint is safe pre-data).
- Prod runbook: migrate → `generate_ward_codes --initial` →
  `assign_death_ids --dry-run` → `assign_death_ids`.
- Upload fixed facility VASA form to ODK Central (external step) so suffixes
  0010+ validate in ODK Collect.
- Pre-existing unrelated failure: test_data_management_list_updates.py::
  test_households_list_excludes_placeholder_rows_and_reorders_filters
  (fails on clean tree).

## VASA WHO-Id collision fix + DeathID reporting (2026-07-14)

### Root causes
- `Q1202` (combined full name) mapped straight to `Id10017` via PSEUDO_TO_WHO;
  the loader ignored `COMBINED_WHO`, so the section given-name field (A4001/
  C3012/N2023) won first-write, Q1202 was discarded as a "collision", and
  `Id10018` (surname) was never populated. 11 of 16 znphi_vasa_who_2016.csv
  records silently lost the surname.
- `DeathID validation: checked=0` was correct (all 16 rows had blank DeathID;
  VAID_decide=2) but unexplained by the command output. Latent bug: the SA
  builder read the `DeathID` header case-sensitively.

### Files Modified
- `va_data_management/utils/vasa_loading.py` — COMBINED_WHO splitting
  (`_split_full_name`/`_reconcile_names`/`_route_n2005`), collision events log
  both values + sections, unresolved conflicts flagged as VALIDITY
  DataQualityIssues (keys.check=name_conflict/who_id_conflict, self-healing),
  case-insensitive SA columns, `dry_run` mode with field diffs, extended
  summary (rows, resolved/unresolved, DeathID row counts, linking status).
- `va_data_management/utils/matching/{service,__init__}.py` — `matching_enabled()`.
- `management/commands/load_vasa_csv.py` — `--dry-run`, self-explanatory summary
  (explains checked=0, reports linking status).

### Tests Added (all passing; 58 vasa/mapping + 65 death-id/matching)
- `tests/test_vasa_loading.py` — split/consistent/component/conflict name
  reconciliation, DQ flag + self-heal, re-import repair, N2005 unit routing,
  DeathID row counts + case-insensitive header, dry-run no-writes + diffs,
  command --dry-run, linking flag summary.

### Data remediation
- Dry-run reviewed, then re-imported the CSV: 0 created / 16 updated; 13
  records gained `Id10018`, 3 correctly without (single-token names; reversed-
  order record keeps full name in Id10017). 0 conflict issues open.

### Cache/Celery/Deploy
- No cache keys, migrations, or Celery changes. ODK/pyODK ingestion shares the
  fixed loader automatically (dry_run is keyword-only, call sites unchanged).
