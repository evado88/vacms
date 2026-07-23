# Death ↔ VA Linkage Pipeline (v2)

How VACMS links death notification records (ODK form `E_DEATH`) to verbal
autopsy records (community VA, facility VA, VASA), how to regenerate the
crosswalk, how to recalibrate, and how the ≥75% link-rate prior is monitored.
Implements `docs/death_va_linkage_hardenning.md`; v1 background is in
`docs/death_va_linkage.md`.

## Architecture

```
XLSForms (static/data) ──generate_linkage_crosswalk──▶ linkage/linkage_map.py
                                                       linkage/linkage_crosswalk.csv
                                                              │
Death / VerbalAutopsy / VASA ──build_*_profile (crosswalk-driven)──▶ LinkageProfile
                                                              │
                    Stage 1 deterministic keys  ◀── DeathIndex (per-run, in-memory)
                    Stage 2 union blocking + scoring (MatchingConfig weights)
                                                              │
                                              VADeathMatch (+ history, evidence)
                                                              │
                            Review UI (/va_data_management/linkage-review)
                            confirm → matched_manual · reject → VADeathMatchRejection
```

Key modules (all under `va_explorer/va_data_management/`):

| Concern | Module |
|---|---|
| Hand-maintained crosswalk rules (human confirmation record) | `linkage/crosswalk_rules.py` |
| Generated crosswalk artifacts | `linkage/linkage_map.py`, `linkage/linkage_crosswalk.csv` |
| Crosswalk generator | `management/commands/generate_linkage_crosswalk.py` |
| Normalisation + profiles (Stage 0) | `utils/matching/{normalization,profiles}.py` |
| Deterministic rules + union blocking | `utils/matching/candidate_selection.py` |
| Config-driven scoring | `utils/matching/scoring.py` (+ `config.py`) |
| Per-run Death register index | `utils/matching/death_index.py` |
| Orchestration + persistence | `utils/matching/service.py` |
| Command | `management/commands/link_deaths_to_vas.py` (renamed from `link_verbal_autopsies`) |
| Review UI | `views_linkage.py` + `templates/va_data_management/linkage_review/` |
| Config table | `MatchingConfig` (admin-editable, simple-history audited) |

## The crosswalk

The linkage feature space is defined once at WHO-Id level as *concepts*
(`deceased_name`, `sex`, `dod`, `nrc`, …). Each concept maps a Death-model
column (`de_field`) to WHO-form fields (`who_ids`); VASA columns resolve
through the existing `vasa/field_map.py` and are informational only (VASA
stores decoded WHO fields directly).

Only rows with `status: confirmed` in `linkage/crosswalk_rules.py` drive
linking. `proposed` rows appear in the reviewable CSV only; rows whose
Death-side field is missing from the current E_DEATH form are demoted to
`absent_in_form` and self-activate if a form revision adds the field
(`DE-03A`/`DE-02A` nicknames are the canonical example).

Regeneration (after any form revision):

```shell
python manage.py generate_linkage_crosswalk          # regenerate + report
python manage.py generate_linkage_crosswalk --check  # CI drift guard
git diff va_explorer/va_data_management/linkage/     # review, then commit
```

### Documented form discrepancies (do not "fix" these back)

* **`DE-08` is the deceased's NRC** (XLSForm label: "DE-08 NRC NUMBER for the
  Deceased"); the `Death` model's verbose name dropped the word "NRC".
  **`DE-23` is the informant's NRC** (informant block DE-19..DE-25) and is
  excluded from deceased-identity matching (see `EXCLUDED_DE_FIELDS`).
* **No death-registration-number field exists in E_DEATH v1.2.4** (`DE-30` is
  a yes/no flag), so the `death_reg_no` deterministic rule is coded but inert.
* **`DE-22` is the informant's residential address**, not the deceased's usual
  residence — mapped to `Id10055` as `proposed` only, pending data-owner
  confirmation.
* `zm_va_who_v1_5_2_7` exists only as a CSV (with data rows, not header-only);
  the generator reads its header for facility-VA column verification and takes
  semantics from `znphi_va_who_v1_5_2_7.xlsx`. Ask the data owner whether that
  CSV is a stale export that should leave `static/data/`.

## The algorithm

**Stage 0 — profiles.** Every record is normalised into a `LinkageProfile`:
name token sets, "Baby of <mother>" parsing, choice-map categories (sex,
place, marital, education, nationality, pregnancy flags), partial-precision
dates, age-in-days, NRC, GPS, geography resolved codes→names
(`ClusterLocationCodes` then the XLSForm-derived `GEO_CODE_MAPS`).

**Stage 1 — deterministic keys** (precedence order, each guarded by the full
scorer; sex or DOD-year conflict routes to review instead of linking):
`death_id` (VASA DeathID, format-validated) → `event_bridge` (CMS Event) →
`nrc` (valid + unique) → `death_reg_no` (inert) → `exact_composite` (name
token-set + sex + exact DOD + district, unique hit required; district skipped
for facility VAs).

**Stage 2 — probabilistic.** Union of blocks (all evaluated; missing a block
loses a true match): EA · constituency+ward · district+death-year · phonetic
surname (soundex) + district · name-initials+year · DOD ±60d regardless of
geography · province (facility VAs without a derivable district). Candidates
are scored with config-table weights across the full comparison vector (name
Jaro-Winkler/token-set blend, mother's name for neonates, DOD bands, DOB/age,
sex, geography, GPS bands, place category, pregnancy-flag consistency,
household head, informant, low-weight demographics). Missing values contribute
zero, never negative. Decision: `score ≥ auto` and separated → auto-link;
ties within `tie_margin` → ambiguous (review); `review ≤ score < auto` →
pending review; below → unlinked.

**Never silently unlink.** A previously auto-linked VA whose re-evaluation
proposes a different death (or none) is demoted to `pending_review` with the
previous death preserved at the top of `alternatives` (tagged
`previous_link`). Manual links are never touched except under `--recheck`.
Reviewer-rejected pairs (`VADeathMatchRejection`) are excluded from every
future run, deterministic rules included.

## Recalibration procedure

1. Edit (or clone) a `MatchingConfig` row in the Django admin — weights and
   thresholds are JSON overrides on the defaults in
   `utils/matching/config.py`. Changes are audited via simple-history.
2. Dry-run it: `python manage.py link_deaths_to_vas --calibrate --config
   <name> --export-report /tmp/calib.csv`.
3. Read the calibration block: link rate + candidate coverage vs the prior
   (PASS/INVESTIGATE), per-tool breakdown, per-block candidate stats
   (min/median/p95/max), contradiction-guard reviews, demoted previous links
   (each one must be explained or fixed), same-tool duplicates.
4. When satisfied, mark the row active (single-active constraint) and run
   `python manage.py link_deaths_to_vas --apply` off-peak.

The v1→v2 migration pass is `link_deaths_to_vas --reconcile-v1 --apply`: it
re-evaluates only rows last written by the v1 algorithm. Manual links are
skipped; changed outcomes land in review, never silently.

## Monitoring the 75% prior

* Weekly Celery task `linkage_calibration_report` (Mondays 03:00, only when
  `VA_DEATH_MATCH_ENABLED`) logs coverage and **warns** below
  `VA_DEATH_MATCH_EXPECTED_LINK_RATE` (default 75).
* The outcomes-dashboard Death→VA Timeline cards now include
  `review_queue_pending`, `review_queue_ambiguous` and
  `matches_with_contradictions` alongside `linked_coverage_pct`.
* Ad hoc: `python manage.py link_deaths_to_vas --calibrate`.

## Review workflow

`/va_data_management/linkage-review` (nav: "Linkage Review"). Viewing needs
`va_data_management.view_linkagereview`; confirm/reject needs
`va_data_management.review_linkage` (granted to Admins and Data Managers in
`initialize_groups` and `fixtures/group.json`). Tabs: Pending / Ambiguous /
Linked / Rejected / Duplicates. The detail page shows the per-field evidence
snapshot (`comparisons`) and ranked alternatives; confirming an alternative
re-points the link (method `manual`). Rejecting records the pair in
`VADeathMatchRejection`, audits via `UserActivity`, and immediately
re-evaluates so the next-best candidate surfaces.
