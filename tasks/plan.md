# Literal `"nan"` in Death Records — Fix Plan

Branch: main
Reported: 2026-07-29, from `unscheduled_deaths_20260727_172446.csv`

## Context

Death records show `nan` across many fields in the line list, and CSV downloads show
`nan` in the same columns.

**This is not a display or export bug — the string `"nan"` is physically stored in
Postgres.** The CSV loader passes pandas' `float('nan')` straight into `Death(**row)`;
because every `Death` field is a `TextField`, Django's `get_prep_value()` calls
`str(nan)` and persists the four characters `n-a-n`. The export dumps it faithfully.

### Evidence

1. **The export is a pure ORM dump.** `views/deaths.py:170-171` does
   `value = getattr(death, name)` / `row.append("" if value is None else value)`.
   No pandas in the export path. A real `NULL` exports as an empty cell.
2. **Both states appear in one file.** In the reported CSV most rows show `DE_34` =
   `nan`, but rows `1695` and older show `DE_34` = *empty*. Same column, same export —
   some rows hold `NULL` (correct), others the string `"nan"` (corrupt).
3. **The UI contradicts itself, exactly as predicted.** The detail page pipes fields
   through the `replace` filter (`templatetags/va_explorer_tags.py:136-148`), which maps
   `"nan" → "N/A"`. The line list (`templates/partials/_death_table.html`) uses
   `|default:"-"`, which only fires on falsy values — and `"nan"` is truthy. The same
   record renders `N/A` on detail and `nan` in the list.
4. **The codebase already knows.** ~25 read-time workarounds of the form
   `.exclude(<field>__iexact="nan")` exist in `va_analytics/views.py`,
   `va_analytics/utils/loading.py` and `home/views.py` — the symptom has been papered
   over at read time instead of fixed at write time.

### Root cause

`utils/loading.py:384-393` — the only NaN guard applies to **integer fields only**:

```python
def nan_to_none_for_intfields(row, int_fields):
    return {k: (None if (k in int_fields and pd.isnull(v)) else v) for k, v in row.items()}

objects = [model(**nan_to_none_for_intfields(row, int_fields)) for row in df.to_dict(orient="records")]
```

`Death` has **zero** integer fields — every field in `models/death.py:11-151` is
`TextField(blank=True, null=True)`. So `int_fields == []` and the guard is a no-op for
every column. `null=True` never fires because the float is stringified first.

Aggravating spots:
- `utils/loading.py:336-340` — `lookup_label` *detects* the NaN then returns it unchanged:
  `return v if pd.isnull(v) or str(v).lower() == "nan" else v_norm`
- `management/commands/load_form_csv.py:62-63` — `model(**row)`, no cleaning at all.
- `load_death_csv.py:63` — `pd.read_csv` with no `na_values`/`keep_default_na`/`dtype`,
  so every blank cell becomes `float('nan')`.

### Ruled out — not the cause

- **The ODK pull path is clean.** `odk/service.py:123-126` defines `_clean_import_value`
  (`pd.isna → None`), applied at both the insert (L224) and upsert (L263) sites. The
  affected records came in via `load_death_csv` / `load_form_csv`, not the scheduled pull.
- **Skip logic is working correctly** (verified against `static/data/E_DEATH.xlsx`):
  `consent = NO` rows legitimately have nothing past `DE_02` (`death_group` is gated on
  `${consent} = '01'`); row `3885`'s blank `DE_16/17/18` is correct because `DE_05 = MALE`
  and those are gated on `selected(${DE-05},'02')`; row `4179` (FEMALE, ~77) correctly has
  `NO/NO/NO`. These *should* be blank — the defect is that blank became `"nan"`, not `NULL`.
- **`DE_32_*` / `DE_33_*` are `image`-type questions** (photos of MCCD, burial permit, NRC)
  — blank when no photo was taken. Same story.
- **`999` is genuine enumerator-entered data**, not a bug. It appears only in unconstrained
  free-text fields (`DE_08`/`DE_10`/`DE_23` NRC, `DE_12`) as an informal "refused/unknown"
  convention; the form's own sentinels are two-digit (`99 = UNKNOWN`). Left alone.
- **`Death.informant` is always empty because the question does not exist.** `E_DEATH.xlsx`
  has an `informant` *choice list* but no `informant` *question*, so `models/death.py:71`
  can never be populated by this form. Flagged for the data team; not fixed here.

---

## Scope

- [ ] Stop the CSV loaders persisting `"nan"`; blank cells must become `NULL`
- [ ] Repair existing corrupted rows, including `simple_history` tables
- [ ] Investigate the ODK-pull column strip; fix only if reproduced
- [ ] Affected models: `Death`, `Pregnancy`, `PregnancyOutcome`, `Household` (all four
      share `load_odk_csv_to_model`), plus `HouseholdMember`, `SocialMobilization` and
      `CSADailyTracker` (reachable from `load_form_csv`'s `FORM_MODEL_MAP`)
- [ ] Affected apps: `va_data_management`

**Out of scope (user decision):** the related `.0` float artifact from the same
`read_csv` type inference — phone numbers exporting as `962336485.0`, `DE_10` as `999.0`
(rows 4179/3885). Fix would be `dtype=str, keep_default_na=False`, as
`utils/vasa_loading.py:865-868` and `utils/xlsform.py:72-73` already do.

## Design

- [ ] API endpoint changes: none
- [ ] Database schema changes: none (data-only repair; no migration)
- [ ] Permission/group changes: none
- [ ] Location scoping implications: none — repair is field-value normalisation, does not
      touch `Location` FKs or querysets. Export/list scoping is unchanged.
- [ ] Cache invalidation: none. Repaired fields are not inputs to `location_tree:version`
      or `home_dashboard_fastpath:version`. Dashboards currently *exclude* `"nan"` at read
      time, so their results are unchanged by the repair.
- [ ] Reuse, don't reinvent: mirror `odk/service.py:123` `_clean_import_value` rather
      than writing a new blank-value concept; field enumeration follows the
      `views/deaths.py:155` `model._meta.fields` pattern; `--dry-run` follows the
      `load_death_csv.py:30-34` convention.

## Implementation Steps

- [ ] **Step 1 — `utils/loading.py`: sanitize NaN for all fields**
  - Add module-level `clean_import_value(value)` (return `None` for `pd.isna(value)`),
    next to the existing `_has_meaningful_value` (L69-73), so blank handling has one home.
  - Replace `nan_to_none_for_intfields` (L384-393) with an unconditional
    `{k: clean_import_value(v) for k, v in row.items()}`. The int-field special case is
    subsumed — `pd.isnull` already covers it — so `int_fields` (L359-370) goes away.
  - Fix `lookup_label` (L340): `return None if pd.isnull(v) or str(v).lower() == "nan"
    else v_norm`.
  - This single change fixes all four loaders sharing the helper.
- [ ] **Step 2 — `management/commands/load_form_csv.py:62`**: route rows through
      `clean_import_value` before `model(**row)`.
- [ ] **Step 3 — new `management/commands/repair_nan_text.py`**
  - For every model reachable from the unsanitised loaders: enumerate
    `TextField`/`CharField` names from `model._meta.fields`.
  - Per field: `Model.objects.filter(**{f"{f}__iexact": "nan"}).update(**{f: blank})`,
    inside `transaction.atomic()`. **`blank` follows `field.null`** — `None` where the
    column is nullable, `""` where it is not. `Death` is entirely `null=True`, but
    `Pregnancy` (7), `PregnancyOutcome` (20) and `Household` (10) carry NOT NULL text
    columns that would raise `IntegrityError` on `None`.
  - Use `.update()` rather than `save()`: this is a data correction, not a domain event,
    and `Death.post_save` queues a Celery task that must not fan out per row.
  - **Repair the `simple_history` tables too** via `Model.history.model.objects` — all
    four declare `history = HistoricalRecords()` (`models/death.py:154`). Skipping
    `historicaldeath` leaves `nan` in the detail page's history tab
    (`views/deaths.py:214`) and lets a revert resurrect it.
  - `--dry-run` printing per-model / per-field counts before any write.
  - Match **only** case-insensitive `nan`. Do not extend to `none`/`null`/`999` — those
    are either rare or genuine data.
- [x] **Step 4 — investigate the ODK-pull column strip. CONFIRMED and fixed.**
      `odk/service.py:737` did
      `df.columns = [c.rsplit("-", 1)[-1] for c in df.columns]`. The death XLSForm names
      questions with **hyphens** (`DE-01`, `DE-32_mccd`), yielding `01` and `32_mccd`,
      which match no `Death` field and are silently dropped by
      `utils/loading.py:316`. If real, ODK-pulled deaths land with *empty* `DE_*` columns
      — the other half of "not displaying actual values".
  - **Reproduced in code:** feeding `import_dataframe_records` a pull-shaped frame with
    `DE-03`/`DE-06`/`DE-32_mccd` and applying the strip yields `Death.DE_03 is None`.
    All 44 `DE-*` questions in `E_DEATH.xlsx` are affected.
  - **Fix applied:** new `odk/service.py::_strip_group_prefix` collapses a trailing dash
    segment **only when it is a bare GPS component** (Latitude/Longitude/Altitude/
    Accuracy, via the new `utils/geopoint.py::is_bare_geopoint_component`). Everything
    else keeps its full name and is resolved by `normalize_dataframe_columns`, whose
    `odk/normalization.py::iter_odk_column_aliases` already emits `raw.replace("-", "_")`
    and maps `DE-01 → DE_01`, `DE-32_mccd → DE_32_mccd`.
  - A blanket removal of the strip was rejected: `FORM_PRE_NORMALIZE_RENAMES`
    (`odk/service.py:49`) keys `social_mobilization`'s GPS on the **bare** `Latitude`
    names the strip produces, so removing it would break that form.

> Note: `Death` has no `raw_answers` provenance JSON (unlike `models/vasa.py:76-79`), so
> the original ODK payload is not recoverable from the DB. Repair is necessarily a
> string-level normalisation — lossless only because `"nan"` never was real data.

> **Trap found while implementing:** a naive `NaN → None` breaks the sibling loaders.
> `Death` is entirely `null=True`, but `Pregnancy` (7 columns), `PregnancyOutcome` (20)
> and `Household` (10) have NOT NULL text columns where the old `"nan"` string happened
> to satisfy the constraint — writing `None` raises `IntegrityError` and fails the whole
> import. Hence `clean_import_value(value, blank)` plus `blank_values_for_model(model)`,
> which picks `None` or `""` per column.

## Celery / External Services

- [ ] Celery job changes: none
- [ ] ODK/DHIS2 implications: Step 4 only, and only if the pull bug is confirmed. The
      scheduled death pull would need one clean run verified afterwards.
- [ ] Redis cache keys to invalidate: none

## Testing Strategy

- [ ] `tests/test_form_import.py` — load a death CSV with genuinely blank cells (a
      `consent=NO` row, and a `consent=YES` row with blank `DE_32_*`); assert fields are
      `None` and explicitly `assert death.DE_15 != "nan"`. The existing
      `test_death_import_definition_and_csv` (L56-65) uses a fully-populated CSV, which
      is why this slipped through.
- [ ] Same assertion for one sibling loader (pregnancy) to lock in the shared-helper fix.
- [ ] New `tests/test_repair_nan_text.py` — seed a `Death` with `DE_15="nan"` plus a
      `HistoricalDeath` row; assert both become `NULL`; assert `--dry-run` writes nothing;
      assert a legitimate `"999"` is untouched.
- [ ] If Step 4 confirms the pull bug: regression test for
      `list_submissions`/`import_dataframe_records` with hyphenated `DE-03` /
      `DE-32_mccd` columns.
- [ ] Location scoping tests: not applicable — no queryset or scoping change.
- [ ] Soft-delete edge cases: not applicable — `Death` is not a `SoftDeletionModel`.
- [ ] Build `Death` rows via the command/bulk paths or `bulk_create`, **never**
      `Death.objects.create()` (post_save → Celery broker hang; see `tasks/lessons.md`).
- [ ] Coverage target: ≥ 60%

## Deployment Checklist

- [ ] Migrations: none. Deploy order is code-first, then run the repair command.
- [ ] Cache pre-warming: not needed.
- [ ] Celery schedule changes: none.
- [ ] Feature flags / gradual rollout: not needed. `--dry-run` is the safety gate.

### Runbook

1. Deploy the loader fixes (Steps 1–2).
2. `python manage.py repair_nan_text --dry-run` — review per-field counts. *(This also
   sizes the problem for the first time; a read-only probe was blocked during planning.)*
3. `python manage.py repair_nan_text`.
4. Re-download the unscheduled deaths CSV from `/va_data_management/deaths/`; confirm
   `DE_32_*` / `DE_33_*` are empty cells, not `nan`, and the line list renders `-`.
5. Cross-check one record (e.g. `death_id 1213SM0027`): list and detail should finally
   agree.
6. Step 4 (ODK pull) proceeds independently of steps 1–3.

### Commands

```shell
pre-commit run --files va_explorer/va_data_management/utils/loading.py \
  va_explorer/va_data_management/management/commands/load_form_csv.py \
  va_explorer/va_data_management/management/commands/repair_nan_text.py

pytest va_explorer/va_data_management/tests/test_form_import.py \
       va_explorer/va_data_management/tests/test_repair_nan_text.py \
       va_explorer/va_data_management/tests/test_deaths_csv_export.py -v

coverage run -m pytest va_explorer/va_data_management && coverage report
```

## Follow-ups (not in this change)

- The ~25 `.exclude(<field>__iexact="nan")` read-time workarounds in `va_analytics` and
  `home` become no-ops after the repair. Leave them for now — removing them is a
  separate, independently-testable cleanup.
- `Death.informant` is unreachable from the current `E_DEATH` form — either add the
  question to the XLSForm or drop the column from the export.
- The `.0` float artifact (out of scope above).
