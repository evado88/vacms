# Death↔VA Linkage Hardening (v2)

## Context

`docs/death_va_linkage_hardenning.md` specifies v2 of the death↔VA/VASA linkage: a generated E_DEATH↔WHO-Id crosswalk, a recall-oriented two-stage (deterministic + probabilistic) matcher tuned against a ≥75% link-rate prior, a config table with audit, calibration reporting, and a human-review workflow. A v1 already exists (engine `va_explorer/va_data_management/utils/matching/`, `VADeathMatch` model, `link_verbal_autopsies` command, hourly Celery sweeper gated on `VA_DEATH_MATCH_ENABLED`). v2 is an iteration on v1, not a rebuild.

**User decisions (locked):** extend `VADeathMatch` (no new RecordLink model); add `jellyfish` dependency for Jaro-Winkler/soundex/metaphone; **rename** `link_verbal_autopsies` → `link_deaths_to_vas` and extend it.

**Spec-vs-reality resolutions (verified against files):**
- `DE-08` **is** the deceased NRC — E_DEATH.xlsx label is "DE-08 NRC NUMBER for the Deceased"; the model verbose name ([death.py:54](va_explorer/va_data_management/models/death.py#L54)) dropped "NRC". `DE_23` is the **informant's** NRC (DE-19..DE-25 informant block) — must be excluded from deceased-identity comparison. Crosswalk: `DE_08 ↔ Id10073`; flag in report/docs (note `Death.__str__` prints DE_23).
- `DE-03A`/`DE-02A` nicknames **do not exist** in E_DEATH v1.2.4 — comparators are crosswalk-driven so they self-activate on a future form revision; generator emits these seed concepts as `absent_in_form`.
- **No death-registration-number field** on E_DEATH/Death (`DE_30` is a yes/no flag). Implement the `death_reg_no` rule keyed off the crosswalk concept; it stays inert; calibration report prints "unavailable (no E_DEATH source field)".
- Facility form `zm_va_who_v1_5_2_7` exists only as CSV (`va_explorer/static/data/zm_va_who_v1_5_2_7.csv` — has data rows, not header-only as spec claims; use header only, take semantics from `znphi_va_who_v1_5_2_7.xlsx`; flag file provenance to data owner).
- VASA→WHO crosswalk is **already done** (`vasa/field_map.py`: `PSEUDO_TO_WHO`, `COMBINED_WHO` incl. `Q1202→[Id10017,Id10018]`); reuse it, never re-derive.
- Geography: VA stores codes, Death stores names — bridged by `ClusterLocationCodes` + `normalization.resolve_ward_name`/`normalize_ea` (exists). No hospital→district table; use `va.location` FK / `resolve_location_context()` for facility VAs.

**Key v1 gaps being closed:** narrowest-block-wins blocking → union of blocks; missing comparators (mother/"Baby of", household head, NRC, age-in-days — `MAX_AGE_TOLERANCE_YEARS` setting exists but unused — place/choice maps, pregnancy flags, GPS, low-weight demographics); hardcoded weights → config table; no calibration/75%-prior report; no review UI (pending_review/ambiguous rows accumulate silently, admin-only); `confirmed_by`/`reviewed_by`/`rejected` fields exist but nothing writes them.

## Phases (dependency order; each shippable)

### Phase 1 — Crosswalk layer
- New `va_explorer/va_data_management/linkage/` package:
  - `crosswalk_rules.py` (hand-maintained): `SEED_CROSSWALK` (spec table with resolutions above), `MANUAL_OVERRIDES` (human-confirmation mechanism — only `confirmed` rows drive linking; `proposed` rows emitted for review only), `CATEGORY_MAPS` alias rules (sex: E_DEATH `01/02` ↔ `male/female`; place: `01`→facility/`02`→community ↔ `Id10058` categories; marital/education/nationality category-level).
  - Generated artifacts (version-controlled): `linkage_map.py` (`CONCEPTS`, `CHOICE_CATEGORY_MAPS`, `XLSFORM_HASHES`, `CROSSWALK_VERSION`) + `linkage_crosswalk.csv` (reviewable artifact: `de_field, who_id, vasa_names, label_de, label_who, confidence, rule, status`).
- New command `generate_linkage_crosswalk` modeled on [generate_vasa_mapping.py](va_explorer/va_data_management/management/commands/generate_vasa_mapping.py) (same `--check` drift guard via hashes, `--quiet`); factor shared helpers (`_sha256`, `_norm_label`, choices parsing, module rendering) into `va_explorer/va_data_management/utils/xlsform.py` used by both.
- Pipeline: parse survey+choices sheets of E_DEATH.xlsx + znphi_va_who xlsx + VASA xlsx (skip note/group rows); normalize labels (strip `DE-xx`/`(Idxxxxx)` prefixes, `${…}`, bracketed notes; lowercase); token-overlap + `fuzz.token_set_ratio` + concept-keyword scoring of DE↔WHO labels; merge with seeds/overrides; VASA names resolved via existing `field_map.PSEUDO_TO_WHO`/`COMBINED_WHO`; verify facility columns against zm CSV header. Report: proposed vs confirmed, failed seed verifications, absent-in-form seeds, facility columns missing.

### Phase 2 — Config + rejection models (migrations 0051–0053)
In [va_death_match.py](va_explorer/va_data_management/models/va_death_match.py) (+ `models/__init__.py` exports, register in admin read-mostly):
- `MatchingConfig`: `name`, `is_active` (partial unique on True), `weights` JSON, `thresholds` JSON (auto, review, min_sep, tie_margin, dod window, max_candidates_per_block), `algorithm_version="2.0"`, `notes`, `updated_by`, `HistoricalRecords()`. `MatchingConfig.active()` → merged dict falling back to `settings.VA_DEATH_MATCH_*` + module defaults; passed explicitly through the engine (like `_thresholds()` today). Data migration seeds initial active row.
- `VADeathMatchRejection` (rejected-pair memory — required because `VADeathMatch` is OneToOne-per-VA and only holds the current proposal): `verbal_autopsy` XOR `vasa` (CheckConstraint like `vadeathmatch_exactly_one_va`), `death` FK, `rejected_by`, `reason`, `created`; unique on (va, death) / (vasa, death).
- `VADeathMatch` additions: method choices `nrc`, `death_reg_no`, `exact_composite`, `probabilistic` (keep v1 values); fields `comparisons` JSON (per-field contributions — v1 computes but discards in `_persist`), `block_sources` JSON, `config_version`.

### Phase 3 — Stage 0: profiles + normalization
- New `utils/matching/profiles.py`: `LinkageProfile` dataclass (name/nickname/household-head/mother token sets, "Baby of" mother, sex, dob/dod with precision, age_days, age_group, place/marital/education/occupation/nationality categories, pregnancy flags, nrc, death_id, gps, geo dict, tool ∈ {community_va, facility_va, vasa, death}, surname_phonetic, initials); `build_death_profile()` / `build_va_profile()` crosswalk-driven via `linkage_map.CONCEPTS` (confirmed rows only) — replaces hardcoded accessor dicts in scoring.py. Community vs facility via existing `is_community_va`.
- Extend [normalization.py](va_explorer/va_data_management/utils/matching/normalization.py) (keep all existing helpers): `name_token_set`, `parse_baby_of`, `name_initials`, `surname_phonetic` (jellyfish soundex, metaphone fallback), `jaro_winkler`, `best_token_alignment_similarity` (greedy JW token pairing), `parse_partial_date` (text/partial dates with precision, extends `dq.parse_when`), `normalize_nrc`, `parse_gps` + `gps_distance_km` (haversine), `category_for(concept, side, code)` via `CHOICE_CATEGORY_MAPS` (rewire `normalize_sex` to consult it first), `age_in_days_at_death`.
- Add `jellyfish==1.0.3` to `requirements/base.txt`.

### Phase 4 — Stage 1: deterministic rules
In [candidate_selection.py](va_explorer/va_data_management/utils/matching/candidate_selection.py) + [service.py](va_explorer/va_data_management/utils/matching/service.py):
- `deterministic_candidate()` precedence: (1) `death_id` — existing VASA `social_autopsy.DeathID` match, now **format-validated** via `death_ids.is_valid_death_id`; keep `event_bridge`; (2) `nrc` — normalized `Id10073` == `DE_08`, require validity + **unique** hit (multiple deaths sharing NRC ⇒ review); (3) `death_reg_no` — coded, inert; (4) `exact_composite` — name token-set equality AND sex AND exact DOD AND same district (district skipped for facility VA).
- Generalize the contradiction guard: after any deterministic hit run the full scorer; sex disagreement or **DOD-year** conflict ⇒ `pending_review` with method preserved. Drop v1's unconditional `score=100.0` except for `death_id`.

### Phase 5 — Stage 2: probabilistic engine
- Replace `fuzzy_candidates` with `union_block_candidates(profile, va, config, death_index)` — union of blocks, each capped, dedup with per-death block provenance: `district_year`, `constituency_ward` (add constituency resolver from `ClusterLocationCodes`), `dod_60d` (±60d regardless of geography), `initials_year`, `phonetic_surname_district`, `ea` (v1's finest block), facility: `province` + location-FK-derived district. Excludes pairs in `VADeathMatchRejection`. Returns per-block candidate counts.
- New `utils/matching/death_index.py`: `DeathIndex.build(queryset)` — per-run in-memory indexes (by district, (district,year), dod buckets, initials+year, phonetic+district, ea, normalized NRC), since Death fields are free text and un-SQL-able. Built once per `run_matching`; single-VA ingestion path falls back to bounded per-block SQL like v1.
- Rewrite [scoring.py](va_explorer/va_data_management/utils/matching/scoring.py) around profile pairs with **config-driven weights**. Comparison vector: name (max of JW best-token-alignment and token_set_ratio; nickname + household-head `DE_02↔Id10061/Q1209` as capped supporting bonus), mother_name (`Id10062` vs `parse_baby_of(DE_03)`, neonate/child only), sex (hard on disagree), dod bands (exact/≤7d/≤30d/≤90d; year-only precision at reduced weight), dob_or_age (uses `MAX_AGE_TOLERANCE_YEARS` — finally wired), age_group, place_category (choice maps), ward/ea/district, gps bands (<1/<5/<15 km), pregnancy-flag consistency (female adults only), marital/education/occupation/nationality at weight 1. Missing ⇒ zero, never negative. Initial v2 weights in the seeded MatchingConfig row (name 28, dod 22, dob_or_age 12, sex 10, mother 8, ea 8, ward 6, gps 5, place 4, district 3, pregnancy 3, head 3, demographics 1 each) — calibration finalizes.
- Decision (in `service._evaluate`): ≥auto + no hard contradiction + separation ≥ min_sep ⇒ `MATCHED_AUTO`/`probabilistic`; ties within `tie_margin` ⇒ `AMBIGUOUS` with ranked alternatives; review ≤ score < auto ⇒ `PENDING_REVIEW`; else `UNMATCHED`. Recall bias: initial config review≈60/auto≈88 (final from calibration; config table, not settings).
- `_persist` also writes `comparisons`, `block_sources`, `config_version`; keep the no-history-churn idempotency invariant. Same-tool duplicates (one death, >1 linked VA of same model) reported in run summary + review-UI "Duplicates" tab (query-derived, no schema change).

### Phase 6 — Legacy-link reconciliation (no destructive migration)
`service.reconcile_v1()` used by the command's `--reconcile-v1` flag: `matched_manual`/`is_manual` never touched; v1 auto + v2 same death ⇒ refresh in place; v1 auto + v2 different/below-auto ⇒ **never silently unlink** — set `pending_review`, keep v1 death as `alternatives[0]` tagged `{"v1_link": true}`, `source="v2_reconcile"`, and list row-by-row in the calibration diff (spec: every lost v1 link explained or fixed); other v1 statuses ⇒ plain re-evaluation.

### Phase 7 — Command rename + calibration
- **Rename** `management/commands/link_verbal_autopsies.py` → `link_deaths_to_vas.py` (git mv). Add spec-named flags: `--tool {community|facility|vasa|all}` (extends `--model` with `is_community_va` split), `--since` (alias of `--from-date`), `--rebuild` (alias of `--recheck`), plus `--calibrate` (report-only, forbids `--apply`), `--reconcile-v1`, `--config <name>`. Keep dry-run-by-default/`--apply`, `--export-report`, batch streaming. Update references: `tests/test_va_death_matching.py` call_command names, `docs/death_va_linkage.md`, `docs/death_va_linkage_implementation.md`.
- Calibration report (stdout + JSON export): per-method counts, queue/ambiguous/unmatched sizes, contradiction-guard triggers, **link-rate (auto + review-queued-likely) vs 75% prior with PASS/INVESTIGATE line**, per-block candidate stats (min/median/p95/max), per-tool breakdown, same-tool duplicates, v1-vs-v2 diff table.

### Phase 8 — Review workflow UI (can ship after Phase 2)
Follow the `va_data_cleanup` precedent ([views.py](va_explorer/va_data_cleanup/views.py) tab/`_paginate` pattern, unmanaged permission model):
- `models/linkage_review.py`: `LinkageReview(managed=False, default_permissions=("view",), permissions=(("review_linkage", …),))`; migration 0054.
- `views/linkage_review.py`: `LinkageReviewIndexView(CustomAuthMixin, PermissionRequiredMixin, ListView)` — tabs Pending / Ambiguous / Recently confirmed / Rejected / Duplicates, evidence chips from `comparisons`, ranked alternatives, district/tool/date filters; `LinkageReviewDetailView` (VA vs candidate deaths field-by-field); `ConfirmLinkView` POST (sets `matched_manual`, `is_manual`, `confirmed_by`/`reviewed_by`/`matched_at`, `UserActivity.log("va_death_link_confirmed", …)`); `RejectLinkView` POST (creates `VADeathMatchRejection`, sets `rejected`+`rejected_reason`, immediately re-evaluates so next-best surfaces, logs). Unlink of a confirmed link = same view with revert, audited.
- Routes in existing `va_data_management/urls.py`; templates `va_explorer/templates/va_data_management/linkage_review/{index,detail}.html` + evidence partial; nav entry beside Data Cleanup.
- Permissions: add `LinkageReview: ["view_linkagereview", "review_linkage"]` for Admin/Data Manager tiers in [initialize_groups.py](va_explorer/users/management/commands/initialize_groups.py) `GROUPS_PERMISSIONS` + `users/fixtures/group.json`.

### Phase 9 — Scheduled hook + docs
- [tasks.py](va_explorer/va_data_management/tasks.py): sweeper unchanged structurally (stays gated on `VA_DEATH_MATCH_ENABLED`); pass active MatchingConfig; add weekly `linkage_calibration_report` task (crontab Monday) logging summary, warning when link-rate < prior-derived floor.
- Extend `va_analytics/views.py::_build_death_va_timeline` (~L4149) with review-queue size + contradiction counts (backward-compatible keys).
- Docs: new `docs/linkage_pipeline.md` — crosswalk regeneration + `--check` in CI, recalibration procedure (edit MatchingConfig → `--calibrate` → compare), 75%-prior monitoring, DE-08/DE-23 + death_reg_no + nickname discrepancies.

### Phase 10 — Tests (all in `va_explorer/va_data_management/tests/`)
**Always create Deaths via the `_make_death` bulk_create helper** ([test_va_death_matching.py:23-42](va_explorer/va_data_management/tests/test_va_death_matching.py#L23-L42)) — Death post_save queues Celery to an unreachable broker.
- `test_linkage_crosswalk.py`: `Q1209→Id10061→DE_02` chain; `Id10017/18` combined via `COMBINED_WHO`; DE-08→Id10073 with DE-23 exclusion; absent-in-form seeds reported not confirmed; `--check` drift; facility CSV header verification.
- `test_linkage_normalization.py`: "Baby of", nicknames, text/partial dates with precision, choice-code maps, NRC, GPS, age-in-days.
- `test_linkage_deterministic.py`: each rule links; sex/DOD-year guards route to review; non-unique NRC ⇒ review; invalid death_id format rejected.
- `test_linkage_blocking.py`: recall property — every synthetic true pair shares ≥1 block (incl. facility VA, missing DOD, misspelled surname via phonetic block); block stats emitted.
- `test_linkage_scoring.py`: clear-match/near-miss/tie fixtures hit the right bands; missing⇒zero; config weight override changes outcome; pregnancy flags female-adult only.
- Extend `test_va_death_matching.py`: facility end-to-end, idempotency (no history churn), rejected-pair exclusion + next-best, one-death-per-VA, duplicate flagging.
- `test_linkage_legacy_migration.py`: manual untouched; same-death refresh; changed outcome ⇒ pending_review with v1 death in alternatives + in calibration diff.
- `test_linkage_review_views.py`: 403 without `review_linkage`; confirm writes `confirmed_by` + UserActivity; reject writes rejection row + re-eval.
- `test_linkage_command.py`: `--calibrate` forbids `--apply`; `--tool vasa --since` mapping; report contains 75%-prior line and v1-diff table.

## Verification
1. `pytest va_explorer/va_data_management/tests/test_linkage_*.py va_explorer/va_data_management/tests/test_va_death_matching.py` (venv: `/srv/vacms/shared/venv`). Full suite has known pre-existing failures (see memory) — compare against baseline, not zero.
2. `python manage.py generate_linkage_crosswalk` then `--check` (idempotent, no drift); eyeball `linkage_crosswalk.csv` — DE-08 row maps to Id10073, DE-23 row status `excluded/informant`.
3. `python manage.py link_deaths_to_vas --calibrate --export-report /tmp/…/calib` on real data: confirm link-rate line vs 75% prior, per-block stats, empty "v1 links missing" table (or each row explained).
4. `--reconcile-v1` dry-run: assert zero `matched_manual` rows changed (`VADeathMatch.objects.filter(is_manual=True)` before/after identical).
5. Review UI: as Data Manager confirm + reject a pending row; verify `confirmed_by`, `UserActivity` rows, rejection excluded on re-run (`link_deaths_to_vas --va-id <pk> --apply` never re-proposes).
6. `coverage run -m pytest && coverage report` ≥60%; `pre-commit run --all-files`.

## Risks
- **Performance/memory** of `DeathIndex` on a large register — measure in calibration; fallback to filtered builds.
- **Queue growth** from recall-biased thresholds — review UI must ship before loosening thresholds in the active config.
- **Facility VA district coverage** unknown (`va.location` FK) — calibration breaks out facility block coverage; a seeded facility→district table is explicitly deferred.
- One-time history volume when v1 rows re-bucket under v2 — run off-peak with tuned `--batch-size`.
