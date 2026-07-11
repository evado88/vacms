# Prompt: Harden Death ID Generation

You are working in the VA CMS Django codebase. Harden the death ID implementation so every imported death record receives a valid 10-character Death ID that satisfies the ODK community and facility form constraints, and add a safe backfill path for existing records.

## Current Context

There is already a partial implementation:

- `va_explorer/va_data_management/management/commands/generate_ward_codes.py` generates ward/cluster codes with a 4-digit base plus two checksum letters.
- `va_explorer/va_data_management/management/commands/assign_death_ids.py` assigns `Death.death_id` values using `cluster_code + sequence:04d`.
- `va_explorer/va_data_management/tasks.py::assign_death_ids_task` duplicates the same assignment logic as a background task.
- `docs/death_id_assignment.md` describes the current behavior.
- `Death.death_id` exists on `va_explorer.va_data_management.models.Death`.

Do not replace this whole system with an unrelated design. Reuse the existing concepts where appropriate, but centralize shared logic so the management command, Celery task, and import path cannot drift.

## Required Death ID Format

The Death ID must be a 10-character code:

```text
NNNNCCSSSS
```

Structure:

- Characters 1-4: a 4-digit number, first digit `1-9`, matching `[1-9][0-9][0-9][0-9]`.
- Characters 5-6: two uppercase checksum letters computed from the first 4 digits.
- Characters 7-10: a 4-digit sequence suffix.

Checksum letters:

- Character 5 = letter for `(d1 * 3 + d2 * 5 + d3 * 7 + d4 * 9) mod 19`.
- Character 6 = letter for `(d1 * 9 + d2 * 7 + d3 * 5 + d4 * 3) mod 19`.

Number-to-letter map:

```text
0=R, 1=G, 2=A, 3=B, 4=K, 5=Y, 6=N, 7=C, 8=L, 9=S,
10=H, 11=Z, 12=D, 13=E, 14=J, 15=P, 16=F, 17=T, 18=M
```

Worked example:

```text
digits = 2026
char 5 = T because (2*3 + 0*5 + 2*7 + 6*9) = 74, and 74 mod 19 = 17
char 6 = L because (2*9 + 0*7 + 2*5 + 6*3) = 46, and 46 mod 19 = 8
valid ID = 2026TL0001
```

## Form Bug To Address

The form currently has a suffix regex bug. It appears to intend suffixes `0001-0999`, but the alternatives are inconsistent with the required total string length of 10:

```text
000[1-9] | 0[1-9][0-9] | [1-9][0-9][0-9]
```

The second and third alternatives are only 3 characters, so with `string-length(.) = 10` and full-string ODK regex matching, only `0001-0009` can validate.

Fix the form definition so the Death ID regex accepts the intended 4-digit suffix range. Prefer a clear full-string regex equivalent to:

```text
^[1-9][0-9]{3}[RGABKYNCLSHZDEJPFTM]{2}0[0-9]{3}$
```

If the intended sequence range is broader than `0001-0999`, confirm and encode that explicitly. Do not silently generate IDs that ODK Collect rejects.

## Implementation Requirements

1. Create a reusable death-ID utility module.

   Put the checksum, validation, formatting, sequence extraction, and assignment helpers in one shared module, for example:

   ```text
   va_explorer/va_data_management/utils/death_ids.py
   ```

   Include functions that can:

   - Compute the two checksum letters from a 4-digit base.
   - Build a valid cluster/death prefix `NNNNCC`.
   - Validate a full death ID.
   - Format a full death ID from `cluster_code` and sequence.
   - Extract the numeric sequence from an existing valid death ID.

2. Make the existing ward code generation use the same checksum helper.

   `generate_ward_codes.py` should not carry a separate checksum implementation that can drift from Death ID validation.

3. Harden `assign_death_ids`.

   Reuse the existing management command, but update it to:

   - Use the shared death-ID utility.
   - Validate existing IDs before using them for sequence continuation.
   - Skip or report malformed existing IDs clearly.
   - Keep deterministic assignment ordering, preferably by death occurrence date (`DE_06`) then `id`, with a stable fallback for unparseable dates.
   - Support `--dry-run`.
   - Support assigning IDs to all existing records missing `death_id`.
   - Report skipped wards, malformed existing IDs, assigned count, and next sequence behavior.

4. Ensure every imported death record gets a Death ID.

   Update the death import path so records imported through the existing loaders receive a valid `death_id` during or immediately after import. At minimum, cover:

   - `load_death_csv`
   - the generic ODK/import path if Death records can enter through it
   - any task-driven import flow that creates `Death` records

   The assignment should be idempotent: importing the same data again must not change an existing valid `death_id`.

5. Add or update a management command for backfill.

   Reuse the existing `assign_death_ids` command unless there is a strong reason to add a separate wrapper. The command must generate Death IDs for all existing `Death` records that do not have one.

6. Remove duplicated assignment logic from the Celery task.

   `assign_death_ids_task` should call the same shared service used by the management command.

7. Add tests.

   Include focused tests for:

   - Checksum generation, including `2026 -> TL`.
   - Full Death ID validation.
   - Rejection of malformed IDs such as `DID-2026-0001`.
   - Correct handling of suffixes `0001`, `0009`, `0010`, and `0999`.
   - The fixed form regex, if the form source is in the repository.
   - Management command dry-run and real assignment.
   - Importing deaths creates valid Death IDs.
   - Existing valid Death IDs are preserved.
   - Existing malformed IDs do not poison sequence continuation.

## Acceptance Criteria

- A newly imported death gets a nonblank, valid `Death.death_id`.
- `python manage.py assign_death_ids --dry-run` previews assignments without writing.
- `python manage.py assign_death_ids` backfills all assignable existing deaths.
- Generated IDs match `NNNNCCSSSS`, are 10 characters, and pass checksum validation.
- `2026TL0001` validates.
- `DID-2026-0001` does not validate.
- IDs with suffix `0010` validate after the ODK form regex fix.
- Management command, Celery task, and import paths use the same shared assignment logic.
- Existing unrelated user changes are not reverted.
