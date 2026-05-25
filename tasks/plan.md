# GPS Upsert for CSV Loaders Plan

Date: 2026-05-08
Branch target: `main`

## Scope

Enable updating (and adding when missing) of GPS coordinate values on **existing** records when re-loading CSVs for:

- VerbalAutopsy (`load_va_csv`) — GPS field: `geopoint`, key: `instanceid`/`instancename`
- Death (`load_death_csv`) — GPS field: `DE_34`, key: `key`
- Pregnancy (`load_pregnancy_csv`) — GPS field: `PE_25`, key: `key`
- PregnancyOutcome (`load_pregnancy_outcome_csv`) — GPS field: `gps`, key: `key`

Add an opt-in `--upsert` flag (default off) so the existing "skip if exists" behaviour is preserved unless the operator explicitly asks for GPS updates.

### Acceptance criteria

- [ ] Each command accepts `--upsert` (no value, just a flag)
- [ ] Without `--upsert`, behaviour is byte-for-byte identical to today (regression suite passes unchanged)
- [ ] With `--upsert`, existing records have their GPS field updated when the incoming CSV row has a meaningful GPS value
- [ ] `simple_history` records the change (use `bulk_update_with_history`)
- [ ] Soft-deleted VAs (`deleted_at IS NOT NULL`) are NOT touched
- [ ] No other fields are modified — GPS-only upsert (intentional narrow scope)
- [ ] Anti-clobber rule: empty/blank incoming GPS never overwrites a populated existing value
- [ ] Output summary line reports counts: `gps_updated`, `gps_skipped_no_change`, `gps_skipped_no_value`
- [ ] Coverage stays >= 60%

## Design Decisions (and why)

### 1. GPS-only, not full upsert

Rationale: the existing flow for Death/Pregnancy/PO uses `bulk_create(ignore_conflicts=True)` and was designed around the assumption that ODK CSV is the source of truth at insert time. Once a record exists, fields may have been corrected via the UI, simple_history, or data-cleanup workflows. A full upsert would silently clobber those. The user's request is explicitly about GPS, so we keep the scope tight.

If full upsert is wanted later, this lays the groundwork for `--upsert-fields=...`.

### 2. Match existing records by `key` (Death/Pregnancy/PO) and `instanceid`/`instancename` (VA)

These are already the de-duplication keys today, so we re-use them. No new uniqueness assumptions.

### 3. Anti-clobber: don't overwrite a populated value with blank

Operators sometimes re-export from ODK with stripped GPS columns or partial dumps. Treating an empty incoming value as "delete the existing GPS" would destroy data.

Rule:
- existing empty + incoming meaningful → write
- existing populated + incoming meaningful → write (this is the new behaviour gated by `--upsert`)
- existing populated + incoming empty → skip
- existing empty + incoming empty → skip

### 4. VA loader already has partial enrichment — keep it, but extend

`load_records_from_dataframe` already enriches `geopoint` on existing VAs **only when the existing geopoint is empty** (see [loading.py:556-590](va_explorer/va_data_management/utils/loading.py#L556-L590)). That stays as the default.

`--upsert` on the VA loader adds: also overwrite when existing geopoint is populated and the incoming value is meaningful AND different.

### 5. No new model fields, no migrations

GPS fields already exist on all four models. This is a code-only change.

### 6. Shared helper to avoid duplication

Death, Pregnancy and PO loaders are nearly identical. Extract the upsert step into one helper in [va_explorer/va_data_management/utils/loading.py](va_explorer/va_data_management/utils/loading.py) — `upsert_gps_for_existing(model, df, key_field, gps_field)` — and call it from each command.

## Affected Files

### New / modified

- [va_explorer/va_data_management/utils/loading.py](va_explorer/va_data_management/utils/loading.py) — add `upsert_gps_for_existing()` helper and extend `load_records_from_dataframe()` to honour `upsert_gps` and `dry_run` kwargs
- [va_explorer/va_data_management/management/commands/load_va_csv.py](va_explorer/va_data_management/management/commands/load_va_csv.py) — add `--upsert` and `--dry-run` flags, thread through to the loader
- [va_explorer/va_data_management/management/commands/load_death_csv.py](va_explorer/va_data_management/management/commands/load_death_csv.py) — add `--upsert` and `--dry-run` flags, call helper before bulk_create
- [va_explorer/va_data_management/management/commands/load_pregnancy_csv.py](va_explorer/va_data_management/management/commands/load_pregnancy_csv.py) — same
- [va_explorer/va_data_management/management/commands/load_pregnancy_outcome_csv.py](va_explorer/va_data_management/management/commands/load_pregnancy_outcome_csv.py) — same
- [va_explorer/va_data_management/odk/service.py](va_explorer/va_data_management/odk/service.py) — `ODKPullService.upsert_into_models()` passes `upsert_gps=True` to `load_records_from_dataframe` (VA branch only) so scheduled VA pulls auto-upsert GPS

### Tests added

- `va_explorer/va_data_management/tests/test_load_csv_upsert.py` — covers all four loaders × all four anti-clobber combinations × flag-on / flag-off

### Not affected

- No model files
- No migrations
- No URL routes, views, templates, JS
- No Celery tasks
- No fixtures

## Permission / Group / Location-scoping Implications

None. These are management commands run from the shell by ops/admins. They do not flow through `CustomAuthMixin` / `LocationRestrictedQuerySetMixin`. No `fixtures/group.json` changes.

The data they touch (Death, Pregnancy, PO, VA) IS location-scoped at view time, but that's unaffected — we're only mutating the GPS column on records that already exist.

## Cache Invalidation Strategy

GPS isn't displayed on the home dashboard fastpath, so no bump of `home_dashboard_fastpath:version`.

GPS isn't part of the location tree, so no bump of `location_tree:version`.

If a downstream feature later visualises GPS on a dashboard, that feature should add its own `analytics:gps_map:version` key. Out of scope here.

## Celery / External Service Implications

- **Scheduled ODK pulls auto-upsert GPS** (Q2 = yes):
  - Death/Pregnancy/PO branch (`import_dataframe_records`) already does full-field upsert with GPS anti-clobber. No change needed.
  - VA branch (`load_records_from_dataframe` via `ODKPullService.upsert_into_models`) gets `upsert_gps=True` passed in. New code path validated by `TestScheduledODKVAUpsert`.
- `import_from_kobo` ([commands/import_from_kobo.py:42,56](va_explorer/va_data_management/management/commands/import_from_kobo.py#L42)) and `load_form_csv` keep insert-only behaviour by NOT passing `upsert_gps=True`. Document in PR.
- No Celery schedule changes; no new periodic tasks.

## Database / FK Considerations

- `Event` has `on_delete=RESTRICT` against Death/Pregnancy/PO/VA. We are NOT deleting; UPDATE on a parent record never violates RESTRICT. Safe.
- VerbalAutopsy is a `SoftDeletionModel`. Use `VerbalAutopsy.objects` (live only) when matching incoming rows for upsert — never `all_objects`. Soft-deleted records stay untouched.
- No new indexes needed; `key` is already `unique=True, db_index=True` on all three secondary models, and `instanceid` lookups already work.

## Implementation Steps

### Step 1 — Add the helper

In [va_explorer/va_data_management/utils/loading.py](va_explorer/va_data_management/utils/loading.py), add:

```python
def upsert_gps_for_existing(*, model, df, key_field, gps_field, dry_run=False):
    """
    Update <gps_field> on existing <model> rows whose <key_field> matches a row in <df>.
    Anti-clobber: never overwrite populated GPS with empty incoming value.
    Uses bulk_update_with_history so simple_history captures the change.

    When dry_run=True: compute the plan but do NOT call bulk_update_with_history.

    Returns dict:
      {
        "updated": int,             # number of rows that would change (or did change)
        "skipped_no_change": int,   # incoming GPS identical to existing
        "skipped_no_value": int,    # incoming GPS empty/blank
        "sample": [                 # up to 10 examples for ops review
            {"key": "...", "existing": "...", "incoming": "..."},
            ...
        ],
      }
    """
```

Implementation outline:
1. Build `incoming = {row[key_field]: row[gps_field] for row in df.to_dict('records')}`
2. Fetch existing records: `model.objects.filter(**{f"{key_field}__in": list(incoming.keys())})`
   - For VerbalAutopsy specifically, `objects` (live only) — never `all_objects`
3. For each existing row, classify:
   - `_has_meaningful_value(incoming)` is False → `skipped_no_value`
   - existing == incoming → `skipped_no_change`
   - else → queue for update, append to `sample[:10]`
4. If `not dry_run` and updates exist: `bulk_update_with_history(updates, model, fields=[gps_field], batch_size=500)`
5. Return tally

### Step 2 — Wire `--upsert` and `--dry-run` into Death/Pregnancy/PO commands

For each of the three commands:

```python
def add_arguments(self, parser):
    parser.add_argument("csv_file", type=argparse.FileType("r"))
    parser.add_argument(
        "--upsert",
        action="store_true",
        help="Also update GPS on records that already exist (matched by key).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the load/upsert plan and print the summary without writing.",
    )
```

In `handle`, BEFORE the line `df = df[~df["key"].astype(str).isin(existing_keys)]` — branch:

```python
upsert_stats = {"updated": 0, "skipped_no_change": 0, "skipped_no_value": 0, "sample": []}
if options["upsert"]:
    existing_in_csv_df = df[df["key"].astype(str).isin(existing_keys)]
    upsert_stats = upsert_gps_for_existing(
        model=Death,                    # or Pregnancy / PregnancyOutcome
        df=existing_in_csv_df,
        key_field="key",
        gps_field="DE_34",              # or "PE_25" / "gps"
        dry_run=options["dry_run"],
    )
```

Then continue with the existing skip-existing-keys filter. Wrap the `bulk_create` so it is **skipped when `dry_run=True`** but the count is still reported.

Append the upsert tally to the final `self.stdout.write(...)` summary. Prefix the whole summary with `[DRY RUN]` when `--dry-run` is on.

### Step 3 — Wire `--upsert` and `--dry-run` into VA loader

`load_va_csv.py`:

```python
parser.add_argument("--upsert", action="store_true", help="...")
parser.add_argument("--dry-run", action="store_true", help="...")
...
results = load_records_from_dataframe(
    csv_data,
    random_locations,
    upsert_gps=options["upsert"],
    dry_run=options["dry_run"],
)
```

`load_records_from_dataframe` signature:

```python
def load_records_from_dataframe(
    record_df,
    random_locations=False,
    debug=False,
    *,
    upsert_gps=False,
    dry_run=False,
):
```

Inside, the existing block at [loading.py:556-590](va_explorer/va_data_management/utils/loading.py#L556-L590) currently fills only when existing geopoint is empty. Modify so that when `upsert_gps=True`, we also enrich when existing geopoint is populated and the incoming value is meaningful **and different** — append to `enriched_vas` either way.

When `dry_run=True`, build the same `enriched_vas` / `created_vas` lists but skip `bulk_create_with_history(...)` and `bulk_update_with_history(...)`. The return dict is identical so `load_va_csv` reports the same summary; the command prefixes its output with `[DRY RUN]`.

The trailing `bulk_update_with_history(enriched_vas, VerbalAutopsy, fields=["geopoint"], ...)` ([loading.py:677](va_explorer/va_data_management/utils/loading.py#L677)) already handles persistence for real runs — no new write path needed.

### Step 3b — Wire `upsert_gps=True` into the scheduled VA pull

In [va_explorer/va_data_management/odk/service.py](va_explorer/va_data_management/odk/service.py):

```python
def upsert_into_models(self, df, form_name):
    if form_name and form_name in FORM_MODEL_MAP:
        return import_dataframe_records(form_name, df)   # already upserts incl. GPS
    with transaction.atomic():
        results = load_records_from_dataframe(df, upsert_gps=True)   # NEW kwarg
    return {...}
```

This satisfies Q2 for the VA branch. Death/Pregnancy/PO branches already upsert via `import_dataframe_records` — no change there.

Note: `import_from_kobo` ([management/commands/import_from_kobo.py:42,56](va_explorer/va_data_management/management/commands/import_from_kobo.py#L42)) does NOT pass `upsert_gps=True`, preserving its insert-only behaviour. Add a `--upsert` flag to it too if we want symmetry, otherwise leave as-is and document.

### Step 4 — Update output messages

Each command should log the GPS upsert tally on its own line so ops can verify what happened:

```
Imported N death records (skipped X existing, Y intra-file duplicates, dropped Z blank-key rows).
Upsert: updated K existing GPS values (M unchanged, P incoming had no GPS value).
```

If `--upsert` was not passed, omit the second line.

If `--dry-run` was passed, prefix the entire output block with `[DRY RUN] ` and append a "Sample of changes that would be applied:" section listing up to 10 `(key, existing → incoming)` rows from `upsert_stats["sample"]`.

Example with `--upsert --dry-run`:

```
[DRY RUN] Would import 12 death records (skipped 348 existing, 2 intra-file duplicates, dropped 0 blank-key rows).
[DRY RUN] Upsert: would update 7 existing GPS values (341 unchanged, 0 incoming had no GPS value).
Sample of changes that would be applied:
  key=DTH-2026-001  -15.4067 28.2833  →  -15.4112 28.2901
  key=DTH-2026-014  (empty)           →  -15.3987 28.3120
  ...
```

### Step 5 — Tests

New test file [va_explorer/va_data_management/tests/test_load_csv_upsert.py](va_explorer/va_data_management/tests/test_load_csv_upsert.py):

```python
class TestUpsertGPSDeath(TestCase):
    def test_no_flag_keeps_existing_gps_untouched(self): ...
    def test_flag_overwrites_populated_gps(self): ...
    def test_flag_fills_empty_gps(self): ...
    def test_anti_clobber_skips_blank_incoming(self): ...
    def test_soft_deleted_va_not_touched(self):    # VA only
    def test_history_record_created(self):          # check simple_history captured
    def test_summary_counts_correct(self): ...
    def test_dry_run_no_writes(self):               # no DB change, no history row
    def test_dry_run_summary_matches_real_run(self): # tally identical to real run

class TestScheduledODKVAUpsert(TestCase):
    """Scheduled VA pull (ODKPullService) auto-upserts GPS — Q2."""
    def test_va_geopoint_overwritten_on_scheduled_pull(self): ...
    def test_va_geopoint_anti_clobber_on_scheduled_pull(self): ...
```

Replicate the upsert matrix for Pregnancy, PregnancyOutcome, VA. The four behavioural cases (empty/populated × meaningful/blank) are the core; the rest is fixtures.

Run: `pytest va_explorer/va_data_management/tests/test_load_csv_upsert.py -v`

### Step 6 — Manual smoke test

```bash
# Backup a small subset first
pg_dump -t va_data_management_death -t va_data_management_pregnancy \
        -t va_data_management_pregnancyoutcome -t va_data_management_verbalautopsy \
        > /tmp/pre_upsert_backup.sql

# Load CSV without flag (regression check — should match previous output)
python manage.py load_death_csv test_data/death_with_gps.csv

# Modify a few rows in the CSV — change GPS values, keep keys

# Preview what would change (no writes)
python manage.py load_death_csv test_data/death_with_gps_modified.csv --upsert --dry-run

# Then commit the upsert
python manage.py load_death_csv test_data/death_with_gps_modified.csv --upsert

# Verify
python manage.py shell -c "
from va_explorer.va_data_management.models import Death
print(Death.objects.filter(key='<known-key>').values('key', 'DE_34').first())
print(Death.history.filter(key='<known-key>').values('history_date', 'DE_34'))
"
```

Repeat for pregnancy, pregnancy_outcome, va.

## Test Coverage Strategy

| Test                                          | Asserts                                         |
|-----------------------------------------------|-------------------------------------------------|
| flag-off: existing record GPS unchanged       | regression — current behaviour intact           |
| flag-on + empty existing + meaningful new     | gps_field updated                               |
| flag-on + populated existing + meaningful new | gps_field overwritten                           |
| flag-on + populated existing + blank new      | gps_field unchanged (anti-clobber)              |
| flag-on + meaningful new but identical to existing | no spurious history row                    |
| flag-on + soft-deleted VA in DB               | VA untouched (matched off `objects` not `all_objects`) |
| flag-on + non-existent key                    | row falls through to bulk_create as before      |
| Output summary line                            | counts match actual updates                    |
| simple_history row created                     | one history entry per real update              |

Coverage target: ≥ 60% on changed files, with new code paths at 100%.

## Deployment Checklist

- [ ] No DB migrations
- [ ] No cache pre-warming required
- [ ] No Celery schedule changes
- [ ] No feature-flag wiring required (the flag is a CLI argument, opt-in)
- [ ] PR description must call out: scheduled ODK pulls remain insert-only by design
- [ ] Communicate to ops team: usage `--upsert` for GPS-only re-loads; document in `va_admin/management` runbook if one exists

## Risks & Mitigations

| Risk                                              | Mitigation                                                    |
|---------------------------------------------------|---------------------------------------------------------------|
| Operator runs `--upsert` with a partial CSV (no GPS column) and clobbers GPS | Anti-clobber: blank incoming never overwrites; tested |
| Bulk update bypasses signals / history             | Use `bulk_update_with_history`, not `bulk_update`             |
| Soft-deleted VAs revived                           | Match via `VerbalAutopsy.objects` (live only); tested         |
| Performance on 100k+ row CSVs                      | `bulk_update_with_history` batched at 500 (matches existing pattern) |
| Race condition with concurrent ODK pull            | `bulk_update` is per-row UPDATE; no FK constraint risk; small window |

## Resolved Open Questions (2026-05-08)

1. **GPS-only forever** — `--upsert` will only ever update the GPS field. No future expansion to other fields. Flag name stays `--upsert` (no `--upsert-fields`).
2. **Scheduled ODK pulls auto-upsert GPS** — yes. See "Scheduled ODK Pull Path" section below for what changes (and what already works today).
3. **`--dry-run` flag** — yes, add to all four commands. Computes the upsert plan and prints the summary (and a sample) without writing.

## Scheduled ODK Pull Path (Q2)

Investigation finding: today's behaviour is split between two code paths.

| Form                | Scheduled-pull entry point                     | Today's upsert behaviour                                      |
|---------------------|------------------------------------------------|---------------------------------------------------------------|
| Death               | [odk/service.py:99](va_explorer/va_data_management/odk/service.py#L99) `import_dataframe_records` | **Already full upsert** with GPS anti-clobber ([service.py:185-194](va_explorer/va_data_management/odk/service.py#L185-L194)) |
| Pregnancy           | same                                            | same                                                          |
| PregnancyOutcome    | same                                            | same                                                          |
| VerbalAutopsy       | [odk/service.py:461](va_explorer/va_data_management/odk/service.py#L461) `load_records_from_dataframe` | Inserts new VAs; only enriches GPS when existing geopoint is **empty** ([loading.py:556-590](va_explorer/va_data_management/utils/loading.py#L556-L590)) |

So Death/Pregnancy/PO scheduled pulls already auto-upsert GPS today (and more — they full-field upsert). The only behaviour to add for Q2 is on the **VA path**: scheduled VA pulls should overwrite a populated `geopoint` when ODK delivers a new meaningful value.

### Changes for the scheduled-pull path

- [va_explorer/va_data_management/odk/service.py](va_explorer/va_data_management/odk/service.py) — `ODKPullService.upsert_into_models()` calls `load_records_from_dataframe(df, upsert_gps=True)` for the VA branch. Death/Pregnancy/PO branch (`import_dataframe_records`) is left as-is — it already covers the case.
- `import_from_kobo` and any other batch-import callers stay opt-in by virtue of NOT setting `upsert_gps=True`.

### Why we don't tighten `import_dataframe_records` to GPS-only

It already updates ALL fields on existing records during scheduled pulls — has been doing so. Narrowing it to GPS-only would be a behaviour change for non-GPS fields, which is out of scope. Q1 ("GPS-only") refers to the new `--upsert` flag, not to retroactive narrowing of an existing path.

## --dry-run Specification (Q3)

Each of the four management commands accepts `--dry-run` (boolean, default off). When set:

- All filtering, normalisation, GPS composition runs as normal
- The upsert plan is computed (which records would be touched, with old vs new GPS)
- **No writes** — neither `bulk_create` nor `bulk_update_with_history` is called
- Output: same summary line as a real run, plus a sample of up to 10 (`key`, `existing_gps`, `incoming_gps`) tuples
- Exit code 0 on success, non-zero only on parse / argument errors

`--dry-run` may be combined with or without `--upsert`. Without `--upsert`, dry-run only previews which records would be inserted (current insert flow with no actual create). With `--upsert`, it also previews the GPS update plan.

Implementation: add a `dry_run` flag to `upsert_gps_for_existing()` so the helper can compute the plan and return the tally + sample without writing. Each command short-circuits the `bulk_create` and write paths when dry-run is on.

## Final Acceptance criteria (updated)

- [ ] All four commands accept `--upsert` and `--dry-run`
- [ ] `ODKPullService.upsert_into_models` passes `upsert_gps=True` for the VA branch
- [ ] `import_from_kobo` and CLI commands without `--upsert` retain insert-only behaviour
- [ ] `--dry-run` produces the same summary as a real run, with no writes (verified by absence of new history rows)
- [ ] All other criteria from the original §"Acceptance criteria" still hold
