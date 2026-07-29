# VA/VASA Export: Indexed Submission-Date Filtering

## Summary

Before this change, `/va_export/` applied two `Id10023` date-of-death gates.
Changing only the final filter would still have excluded records with blank or
`dk` death dates.

Both VA and VASA inherit the raw ODK `submissiondate`. Preserve that field for
provenance and backward compatibility, and normalize it into a canonical
`submitted_at = DateTimeField(null=True, blank=True, editable=False)`. This
retains timestamps and timezone information; a `DateField` would discard them.

The current production snapshot contains parseable timestamps for all 2,665
active VAs and 858 active VASAs. A full submission-date range should therefore
return 3,523 distinct records before multiple-cause row expansion, 36 more than
the death-date-filtered result. Interpret VA/VASA date ranges as submission
calendar dates in `VA_EXPORT_TIME_ZONE` (default `Africa/Lusaka`), which is
deliberately separate from `TIME_ZONE`. Storage stays UTC; only day boundaries
are local, so a date picked in the export UI means the day field staff actually
worked. Bucketing in UTC would push submissions made in the first two local
hours of a day into the previous day's download.

## Implementation changes

- Add `submitted_at` to `BaseVerbalAutopsy`, which materializes it on VA, VASA,
  and their history models. Do not backfill old historical rows; future history
  entries capture the canonical value.
- Add partial btree indexes to the active concrete tables:
  `va_submitted_at_idx` and `vasa_submitted_at_idx`. Each indexes `submitted_at`
  where `deleted_at IS NULL AND submitted_at IS NOT NULL`.
- Create the indexes with PostgreSQL `AddIndexConcurrently` in a separate,
  non-atomic migration. Retain the raw VA `submissiondate` index because
  existing dashboard queries still use it.
- Add one strict shared parser for VA ingestion, VASA ingestion, direct model
  saves, and backfill. It must accept aware ISO-8601 timestamps with `Z` or
  offsets, normalize canonical values to UTC, and interpret ISO date-only
  legacy values as midnight in `VA_EXPORT_TIME_ZONE`. Reject ambiguous or
  malformed nonblank values; never substitute `created`, `Id10023`, or the
  current time.
- The parser is strict; its callers are not. A malformed value must never cost
  more than the canonical timestamp of the record that carries it:
  - VA ingestion leaves `submitted_at` null and raises a `CauseCodingIssue`
    from `validate_vas_for_dashboard`, exactly as an unparseable `Id10023`
    does. Raising instead would roll back an entire ODK pull, because callers
    wrap the loader in a transaction.
  - VASA ingestion leaves `submitted_at` null and raises a VALIDITY
    `DataQualityIssue` (`check=unparseable_submissiondate`), auto-resolved when
    a corrected value re-imports.
  - `save()` re-derives only when the raw value is actually being written, and
    clears rather than raises on failure. Soft deletion is implemented as
    `save()`, so a strict save would let one bad legacy value block deletes,
    edits, re-coding, and geocoding for that record permanently.
  - `backfill_va_submitted_at` reports per-source counts and then exits
    non-zero if any value failed to parse, so it gates the cutover below.
- Update both ingestion paths to populate raw `submissiondate` and canonical
  `submitted_at`. Bulk VA ingestion must normalize before
  `bulk_create_with_history`; VASA create/update must use the same parser.
- Add an idempotent `backfill_va_submitted_at` command with `--dry-run` and a
  configurable batch size. It must process both models through `all_objects`,
  including soft-deleted rows; update only missing canonical values in bounded
  transactions; avoid history events; preserve raw values; and report
  per-source parsed, blank, and invalid counts without exposing row contents.

## Export behavior and interfaces

- In the VA export query, start from each model's active manager rather than
  `user.verbal_autopsies()` or `user.vasa_records()`, because those helpers
  prefilter on `Id10023`. Reapply the same canonical and export-specific
  location restrictions so permissions do not broaden, and remove the explicit
  `Id10023="dk"` exclusion.
- Filter `submitted_at` with index-friendly, timezone-aware bounds:
  `submitted_at >= start_date at 00:00` and `submitted_at < midnight after
  end_date`. When the end date is `9999-12-31`, omit the overflowing exclusive
  upper bound. Do not use `__date`, casts, substring expressions, or functions
  on the indexed column.
- Keep `/va_export/`, `/va_export/verbalautopsy/`, `start_date`, and `end_date`
  unchanged. For dataset `verbalautopsy`, those fields now mean inclusive ODK
  submission dates; non-VA datasets retain their existing date semantics.
- Update form help text to state that Verbal Autopsy ranges use inclusive ODK
  submission dates.
- Preserve ID-targeted exports: after implicit access scoping, `ids` and
  `vasa_ids` continue to bypass date, explicit-location, and cause filters.
- With no cause filter, preserve coded and uncoded VA/VASA rows through the
  outer join. An explicit cause filter continues to exclude uncoded records.
  Multiple linked causes continue to produce one deterministically ordered row
  per cause.
- Preserve `source`, permissions, PII redaction, soft deletion, VA-before-VASA
  ordering, and source-specific IDs.
- Keep `Id10023` and the existing `date` output as date of death. Add canonical
  `submitted_at` to abridged output; full output contains both `submitted_at`
  and unchanged raw `submissiondate`. Serialize `submitted_at` as ISO-8601.
- Add `date_basis=submitted_at`, `export_tz`, and per-source counts of scoped
  active records with a null `submitted_at` to the existing export log, while
  retaining its per-source output-row counts. A null canonical timestamp can
  never satisfy `submitted_at >=`, so those records are invisible to every
  dated download; the log must name that rather than leave an unexplained
  shortfall.

## Rollout

1. Deploy the nullable schema, parser, dual-writers, and backfill command while
   exports still use `Id10023`.
2. Run the backfill in staging and production, first with `--dry-run`, then in
   batches. Require zero active VA/VASA rows with null `submitted_at` and zero
   parsing failures before cutover.
3. Create the partial indexes concurrently, deploy the export cutover, restart
   all web and ingestion workers, and confirm the running revision.
4. If rollback is required, restore the previous export reader while leaving
   the additive field, raw provenance, backfilled data, and indexes intact.

Update `docs/vacms_va_vasa_coding_architecture.md` and `docs/vasa.md` to
distinguish combined VA/VASA downloads from traditional-VA-only DHIS behavior
and document raw `submissiondate` versus canonical `submitted_at`. No
`docs/index.md` entry is required.

## Test and acceptance plan

- Unit-test parsing of `Z`, positive and negative offsets, naive timestamps,
  date-only values, blanks, and malformed values.
- Test VA bulk ingestion and VASA create/update dual-writing, normalization
  after raw-value changes, and explicit invalid-value reporting.
- Test backfill dry-run safety, both sources, soft-deleted rows, batching,
  resumability, idempotence, invalid counts, and unchanged historical rows.
- Add a production-shaped HTML regression containing coded and uncoded VA/VASA
  records; blank, `dk`, in-range, and out-of-range death dates; submission
  timestamps at both boundaries and immediately outside them; a death date
  inside but submission date outside the range; and multiple linked causes.
- Assert abridged/full CSV and JSON membership, source ordering, cause
  values/blanks, canonical timestamp output, permissions, location scoping,
  PII redaction, soft deletion, and ID-only behavior.
- Test the `9999-12-31` safeguard and confirm non-VA date filtering is
  unchanged.
- On staging, verify distinct exported source records equal the user-scoped
  active VA and VASA querysets over the same canonical range. For the current
  production snapshot, a complete range should include 2,665 VAs and 858
  VASAs.
- Use `EXPLAIN (ANALYZE, BUFFERS)` for representative selective ranges to
  confirm the query is index-eligible. A sequential scan for a full-dataset
  download is acceptable.

## Assumptions

- "All records" means active records visible to the requesting user whose
  canonical submission timestamp falls in the inclusive selected date range.
- Records with multiple causes intentionally produce multiple rows; records
  without a cause produce one row.
- The raw `submissiondate` remains the source value and is never rewritten by
  normalization or backfill.
- Cutover is blocked until all active VA and VASA records have a valid
  `submitted_at`; invalid nonblank values require data remediation rather than
  a fallback timestamp. Remediation is a data task, never a reason to reject or
  drop an incoming record — ingestion always loads it and flags it.
