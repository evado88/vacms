Troubleshoot the VACMS Django command:

```bash
python manage.py load_vasa_csv /srv/vacms/shared/media/uploads/manual/znphi_vasa_who_2016.csv
```

The command completed without import errors and reported:

```text
Loaded VASA: created=16, updated=0, skipped=0, errors=0, who_id_collisions=11
DeathID validation: checked=0, invalid=0, unmatched=0, resolved=0
```

During processing, 11 Verbal Autopsy records generated the same warning:

```text
1 duplicate WHO-Id collisions across sections: [('Id10017', 'Q1202')]
```

The log also states:

```text
ODK pull on startup disabled via ODK_PULL_ON_STARTUP=0
```

This appears informational and should not be treated as the primary error unless it affects the loader.

## Troubleshooting objectives

Investigate and explain:

1. Why `Id10017` and `Q1202` are being treated as duplicate WHO-ID values across different VASA sections.
2. Whether these fields represent:

   * The same WHO variable duplicated in the source CSV
   * Different questions incorrectly mapped to the same model field
   * A legitimate repeated identifier across sections
   * A loader configuration or field-mapping defect
3. Which value is currently retained when the collision occurs and whether any data is silently overwritten or discarded.
4. Why the collision occurred for 11 of the 16 imported records rather than all records.
5. Why DeathID validation reports `checked=0` even though 16 VASA records were successfully created.
6. Whether the CSV contains a DeathID field and whether the loader:

   * Recognises the correct CSV column
   * Maps it to the correct VASA field
   * Normalises empty or malformed values
   * Invokes DeathID validation after record creation
7. Whether DeathID validation is being skipped because:

   * The relevant field is empty
   * The CSV header differs from the expected field name
   * The validation function is not called by `load_vasa_csv`
   * Validation only runs for updated records or PyODK ingestion
   * The validation queryset or condition excludes newly created records
   * The VASA model uses a different death identifier field
8. Whether the newly created VASA records should have been linked to existing Death records or Case Management events.

## Required investigation

Inspect the following before modifying code:

* The `load_vasa_csv` management command
* The VASA CSV field-mapping configuration
* WHO-ID mapping and section-processing logic
* The code that detects `who_id_collisions`
* The definitions and expected meanings of `Id10017` and `Q1202`
* The VerbalAutopsy model fields
* The Death model’s authoritative unique identifier
* Existing VASA-to-Death relationship fields
* DeathID validation and resolution functions
* Any shared VASA loading service used by PyODK and management commands
* Tests covering duplicate WHO IDs and DeathID validation

Inspect the source CSV headers and representative rows, especially the 11 UUIDs that generated warnings.

## Expected corrective behaviour

The loader should:

* Preserve all legitimate VASA data.
* Avoid silently overwriting values during WHO-ID collisions.
* Distinguish between intentional duplicate identifiers and incorrect mappings.
* Log which section and source column supplied each conflicting value.
* Log both values where a collision occurs.
* Apply a documented precedence rule only where the business meaning is clear.
* Treat unresolved conflicting values as validation issues rather than silently selecting one.
* Validate every non-empty DeathID loaded from the CSV.
* Clearly report why a record was not checked for DeathID matching.
* Use the same DeathID validation and VASA-to-Death linking logic used by other ingestion pathways.
* Avoid forcing a Death-record link where no reliable match exists.
* Automatically link only where the match confidence is greater than 90%.
* Leave unmatched facility VAs unlinked where no corresponding Death record exists.

## Improve command reporting

Update the command summary so it distinguishes between:

* Total CSV rows read
* VASA records created
* VASA records updated
* Records skipped
* Import errors
* Records containing WHO-ID collisions
* Total collision instances
* Collisions resolved by precedence
* Collisions left unresolved
* Records with a DeathID value
* Records without a DeathID value
* DeathIDs checked
* Valid DeathIDs
* Invalid DeathIDs
* Unmatched DeathIDs
* DeathIDs resolved to a Death record
* VAs automatically linked
* VAs requiring manual review
* VAs left unmatched

The command should not report `checked=0` without explaining whether this resulted from absent DeathID values, unrecognised columns, disabled validation, or another condition.

## Deliverables

Provide:

1. The identified root cause of the `Id10017` and `Q1202` collision.
2. The identified reason DeathID validation checked zero records.
3. A summary of whether any imported data was overwritten, omitted, or incorrectly mapped.
4. The affected files, functions, models, and mappings.
5. A safe code fix.
6. Any required migration or mapping correction.
7. Improved logging and command summary output.
8. Automated tests covering:

   * A row without a WHO-ID collision
   * A row with identical duplicate values
   * A row with conflicting duplicate values
   * A row with a valid DeathID
   * A row with an invalid DeathID
   * A row with an unmatched DeathID
   * A row without a DeathID
   * Successful VASA-to-Death linking
   * A facility VASA with no corresponding Death record
9. A dry-run verification procedure using the same CSV.
10. The expected corrected command output.

Do not modify production data until the collision behaviour and the reason for `DeathID validation: checked=0` are fully understood. Preserve the 16 successfully created VASA records unless there is evidence that they contain incorrectly mapped or overwritten data.
