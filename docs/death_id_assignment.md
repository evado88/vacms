# Death ID Assignment

Death IDs uniquely identify death records within a ward cluster and globally
across the system. The division of responsibility:

- **Death records** (community `E_DEATH` form) are registered **without** a
  Death ID. **VACMS creates and assigns** IDs — at import, via the periodic
  Celery sweeper, or via the `assign_death_ids` backfill command.
- **VASA records** (facility form) arrive **with** a `DeathID` entered by the
  interviewer. VACMS never generates or overwrites these — it validates them
  and flags problems as Data Quality issues (see "VASA Death ID validation").

All generation and validation logic lives in one shared module,
`va_explorer/va_data_management/utils/death_ids.py`, used by the management
command, the Celery task, and the CSV import hooks — so the checksum and
format cannot drift between call sites or the ODK form constraint.

## Format

A Death ID is a 10-character code `NNNNCCSSSS`:

| Chars | Meaning |
|-------|---------|
| 1–4   | 4-digit cluster base, first digit 1–9 (`[1-9][0-9]{3}`) |
| 5–6   | Two uppercase checksum letters computed from the base digits |
| 7–10  | 4-digit per-cluster sequence, **0001–0999** |

Checksum letters (mod-19 sums mapped through the letter table below):

- Char 5 = letter for `(d1*3 + d2*5 + d3*7 + d4*9) mod 19`
- Char 6 = letter for `(d1*9 + d2*7 + d3*5 + d4*3) mod 19`

```text
0=R, 1=G, 2=A, 3=B, 4=K, 5=Y, 6=N, 7=C, 8=L, 9=S,
10=H, 11=Z, 12=D, 13=E, 14=J, 15=P, 16=F, 17=T, 18=M
```

**Worked example** — base `2026`:
`(2*3 + 0*5 + 2*7 + 6*9) = 74 → 74 mod 19 = 17 → T` and
`(2*9 + 0*7 + 2*5 + 6*3) = 46 → 46 mod 19 = 8 → L`, so the prefix is
`2026TL` and the first death in that cluster is `2026TL0001`.

The canonical validation regex (also the ODK form constraint):

```text
^[1-9][0-9]{3}[RGABKYNCLSHZDEJPFTM]{2}0[0-9]{3}$
```

plus checksum recomputation and suffix ≠ `0000`. `2026TL0001` validates;
`DID-2026-0001` does not. Each ward cluster maintains its own independent
sequence; `format_death_id` refuses sequences above 0999 so VACMS can never
mint an ID the ODK form rejects.

## Prerequisites

- `ClusterLocationCodes` populated with ward cluster codes:
  `python manage.py generate_ward_codes --initial` (reads
  `va_explorer/static/data/wards.csv`). Cluster codes use the same checksum
  helper as Death ID validation.
- `Death` records must have `ward` populated. Ward matching is
  **case-insensitive against the ward name** (`ClusterLocationCodes.ward`),
  with a fallback to `ward_code` for deployments that store codes in
  `Death.ward`.

## How assignment happens

1. **At import** — `load_death_csv` and `load_form_csv death` assign IDs to
   the just-imported records synchronously.
2. **Periodic sweeper** — `assign_death_ids_task` runs every 15 minutes and
   picks up anything imported through other paths (it is also queued by the
   Death post_save signal for single-record saves).
3. **Backfill/manual** — `python manage.py assign_death_ids`.

All three call the same service, which:

- only fills **blank** `death_id` values (idempotent — re-imports never change
  an existing ID);
- orders deaths by occurrence date (`DE_06`), then `id` (unparseable dates
  last), so assignment order is deterministic;
- continues each cluster's sequence from the highest **valid** existing ID;
  malformed existing IDs are reported, ignored for sequencing, and never
  overwritten;
- serialises concurrent runs with a PostgreSQL advisory lock, and the
  `unique_death_id_when_set` DB constraint guarantees no duplicates;
- reports skipped wards (missing/invalid cluster code), malformed IDs, and
  sequence overflow (a cluster exhausting 0999 is loudly reported and left
  for operator action).

## Usage

```bash
# Preview without writing
python manage.py assign_death_ids --dry-run

# Assign for real
python manage.py assign_death_ids
```

Output shows a preview table (ward, death_id, deceased name), skipped-ward
warnings, malformed-ID warnings, and the assigned count.

## VASA Death ID validation

After every VASA load (`load_vasa_csv` / `load_vasa_from_dataframes`), VACMS
validates all `VASASocialAutopsy.DeathID` values:

- **Invalid format/checksum** → open `DataQualityIssue` (VALIDITY) titled
  *"VASA Death ID invalid"*.
- **Well-formed but not found in `Death.death_id`** → issue titled
  *"VASA Death ID not found in death register"*.
- Blank DeathIDs are not flagged (`VAID_decide` covers "don't have it").

Records are always imported and the stored DeathID is never modified. The
checks are idempotent and self-healing: once the DeathID is corrected or the
matching Death record is registered/assigned, the issue auto-resolves on the
next validation pass.

## Updating the ODK form

The facility VASA form (`va_explorer/static/data/
znphifacilityvasa_02_final.xlsx`, survey sheet, `DeathID` row) previously used
suffix alternatives `000[1-9]|0[1-9][0-9]|[1-9][0-9][0-9]` — the last two are
3 characters, so with `string-length(.) = 10` only `0001–0009` could ever
validate, and `[A-Z][A-Z]` accepted non-checksum letters. The constraint is
now:

```text
string-length(.) = 10 and regex(., '[1-9][0-9][0-9][0-9][RGABKYNCLSHZDEJPFTM][RGABKYNCLSHZDEJPFTM](000[1-9]|00[1-9][0-9]|0[1-9][0-9][0-9])')
```

which accepts the full intended suffix range 0001–0999 (e.g. `2026TL0010`).
**The revised form must be re-uploaded to ODK Central** for the fix to reach
ODK Collect. The community `E_DEATH` form has no Death ID field — deaths are
registered without one by design.

## Troubleshooting

**No deaths without death_ids found** — nothing to do; all deaths have IDs.

**No cluster code found for ward(s)** — populate/refresh the table:
```bash
python manage.py generate_ward_codes --initial      # first load
python manage.py generate_ward_codes --update       # sync new wards from CSV
python manage.py generate_ward_codes --update-codes # codes for codeless wards
```
Ward names in `Death.ward` must match `wards.csv` ward names
(case-insensitive).

**Malformed existing death_id warnings** — those IDs fail format/checksum
validation. They are ignored for sequence continuation and left in place;
correct or clear them manually if they should be regenerated.

**Sequence exhausted (max 0999)** — the cluster is full. This is reported as
an error and remaining deaths stay unassigned; raising the cap requires a
coordinated change to `death_ids.MAX_SEQUENCE` **and** the ODK form
constraint.
