# Death `"nan"` and Missing-Answer Recovery

Reported: 2026-07-29
Evidence file: `unscheduled_deaths_20260727_172446.csv`

## Findings

- The supplied CSV has 52 rows and 2,113 literal `"nan"` cells. Forty-eight
  submissions declined consent, so their later Death questions were correctly
  skipped but incorrectly persisted as the string `"nan"`.
- CSV import allowed pandas to infer types. Empty cells became floating-point
  NaN and text-like identifiers could acquire `.0` or lose leading zeroes.
- The ODK pull previously stripped everything before the final dash in a column
  name. This changed `DE-03` to `03`, so Death answers were silently discarded.
- ODK updates were built from every model field rather than the incoming
  columns. A source replay could therefore clear fields absent from the form,
  including `death_id` and `eventid`.
- The list treated stored `"nan"` as valid, and the CSV exporter emitted the
  stored placeholder unchanged.
- The supplied export no longer maps reliably by database `id` or historical
  `death_id`: those values can be reused or re-minted. The ODK submission `key`
  remains authoritative.

## Implemented Safeguards

- [x] Centralize form CSV reading with `dtype=str`, `keep_default_na=False`, and
      `low_memory=False`.
- [x] Normalize pandas NaN, empty strings, and literal `"nan"` through the shared
      model-aware blank-value helper before model construction.
- [x] Apply the reader to Death, Pregnancy, Pregnancy Outcome, Household,
      repeat-group, CSA tracker, Social Mobilization, and rumor imports.
- [x] Preserve hyphenated ODK questions while continuing to collapse bare GPS
      component suffixes.
- [x] Normalize ODK columns before applying choice-label mappings so fields such
      as `DE-05` map through `DE_05` choices correctly.
- [x] Update only fields present in the normalized ODK frame.
- [x] Protect `death_id`, `eventid`, the primary key, and the ODK key from form
      updates; Death IDs are assigned only when blank.
- [x] Warn when a consented Death payload lacks core `DE_03`, `DE_05`, or
      `DE_06` columns after normalization.
- [x] Exclude literal `"nan"` placeholders from operational line lists and
      serialize optional stored `"nan"` values as blank CSV cells.
- [x] Gate Death completeness checks on affirmative consent while retaining the
      existing consent issue/reporting behavior.

## Reconciliation Command

The unrestricted `repair_nan_text` command has been removed. It would have
rewritten live and historical rows across multiple models without recovering
source answers.

`reconcile_death_nan` now provides two explicit phases:

1. Audit mode reads a bounded CSV containing `key`, `death_id`, or `id`, and an
   optional raw ODK Death export. It writes a reviewable JSON manifest and does
   not modify the database.
2. Apply mode accepts only the reviewed manifest, locks and revalidates every
   target, then updates form-owned Death fields with
   `bulk_update_with_history`.

Matching precedence is `key`, then `death_id`, then database `id`. A supplied
database ID or Death ID cannot redirect a row when an ODK key is available.

For each manifest record:

- A single matched, consented ODK submission restores source form fields.
- A no-consent submission only converts literal `"nan"` to `NULL`.
- A consented record with missing or ambiguous source is normalized but marked
  `normalize_unrecovered` in the manifest.
- `id`, `key`, `death_id`, and `eventid` are captured and protected.
- Original and proposed values are recorded for review and rollback.
- Existing historical rows remain unchanged; one new repair history entry is
  appended for each changed live Death.
- Reapplying the same manifest is idempotent. Any intervening protected or
  repaired-field change aborts the transaction and requires a new audit.

```shell
python manage.py reconcile_death_nan \
  --keys-file unscheduled_deaths_20260727_172446.csv \
  --source-file odk_death_snapshot.csv \
  --manifest /tmp/death_nan_manifest.json

python manage.py reconcile_death_nan \
  --manifest /tmp/death_nan_manifest.json \
  --apply
```

## Validation Completed

- [x] Audit the supplied CSV against the configured database without applying
      changes: 52 inputs, 47 matched Deaths, five unmatched historical rows,
      zero ambiguous identities, and zero protected-field changes.
- [x] Validate audit-only behavior, source restoration, no-consent
      normalization, missing/ambiguous sources, stale-manifest rejection,
      rollback data, history preservation, identifier protection, and
      idempotency.
- [x] Validate partial ODK updates preserve absent fields plus `death_id` and
      `eventid`.
- [x] Validate hyphenated fields, choice mapping, GPS mapping, blank cells,
      leading-zero values, list exclusion, CSV blank serialization, and
      consent-gated DQ checks.
- [x] Focused regression suites: 118 passed.
- [x] Ruff, Black, `git diff --check`, and all applicable pre-commit hooks pass.
- [x] Remove test-generated `reports/dq_*` changes from the implementation diff.

The broader data-management suite cannot currently be treated as a release
gate: it contains missing fixture files, unrelated stale expectations, an
existing VA location NOT NULL failure, and a Celery/Redis teardown hang. Before
the hang, 271 tests passed and four unrelated failures were reported. The
focused affected suites are green.

## Deployment Runbook

- [ ] Back up the live Death and HistoricalDeath tables.
- [ ] Deploy the ingestion, upsert, list, export, and reconciliation safeguards.
- [ ] Obtain a fresh raw ODK Death export without advancing `ODKPullState`.
- [ ] Generate the production manifest using the affected-record CSV and ODK
      source snapshot.
- [ ] Review unmatched, ambiguous, `normalize_unrecovered`, protected, original,
      and proposed values. Do not apply an unreviewed manifest.
- [ ] Apply the reviewed manifest.
- [ ] Run Death DQ checks and verify repaired consented records display their
      source answers while no-consent rows remain stored but are excluded from
      the operational list/export.
- [ ] Confirm Death row counts, `death_id`, and `eventid` are unchanged; existing
      history remains intact and one repair history row was appended per change.
- [ ] Resume scheduled ODK pulls and monitor for missing normalized core columns
      or newly stored literal `"nan"` values.

The ODK endpoint was unreachable from the development environment, so source
recovery and a post-deployment pull must be validated from an environment with
production ODK connectivity before recovery is considered complete.
