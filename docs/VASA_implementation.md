Claude Code Prompt: Implement VASA (Verbal Autopsy + Social Autopsy) Support in VACMS
Role and objective
You are working in the VACMS (Verbal Autopsy Case Management System) Django codebase. VACMS already has a complete, production implementation for WHO 2016 Verbal Autopsy (VA) records: an ODK XLSForm (`znphi_va_who_v1_5_2_7.xlsx`, form id `znphi_va_who_v1_5_2_7`) is collected in ODK, submissions are ingested via pyODK / a Django management command into the `VerbalAutopsy` model, and the records are then coded and assigned a cause of death (COD).
Your task: implement equivalent, end-to-end support for the new VASA form (`znphifacilityvasa_02_final.xlsx`, form id `znphi_vasa_who_2016`, title `ZNPHI_VASA_WHO_2016`), so that VASA records can be pulled from ODK Central, persisted in new VASA models, and coded / assigned a cause of death by reusing the existing VA coding and COD-assignment pipeline unchanged wherever possible.
Both XLSForms and sample CSV exports are provided in the repo/working directory:
`znphi_va_who_v1_5_2_7.xlsx` — existing WHO VA XLSForm (reference for how the current implementation is anchored).
`znphifacilityvasa_02_final.xlsx` — the VASA XLSForm to implement (the authoritative source of the field mapping).
`znphifacilityvasa_02.csv` — sample main-submission export from a previous form version (has `province`, `area`, `hospital` columns where the final form has `constituency`, `ward`, and `EA`). 6 sample records.
`znphifacilityvasa_02-br_N2213.csv`, `znphifacilityvasa_02-AD_C3253_1.csv`, `znphifacilityvasa_02-ar_A4252.csv` — sample repeat-group exports, joined to the main export via `PARENT_KEY` → main `KEY`.
Ground-truth facts about the two forms (verified by analysis — re-verify programmatically before coding)
WHO VA form (`znphi_va_who_v1_5_2_7.xlsx`)
Survey sheet columns include `type`, `name`, labels/hints in 4 languages, `relevant`, `calculation`, etc. There is no `WHO_name` column — the `name` column IS the WHO name.
~619 survey rows; 403 data fields whose `name` matches `Id\d+` (e.g. `Id10002`…`Id10462`) plus metadata-typed Id fields (`Id10012` = `today`, `Id10011` = `start`, `Id10481` = `end`).
~49 non-Id helper/admin fields: `deviceid`, `province`, `district`, `constituency`, `ward`, `ea`, `interview_type`, age calculates (`ageInDays`, `ageInYears`, `isNeonatal`, `isChild`, `isAdult`, `age_group`, …), unit helpers (`id10120_unit`, `id10161_unit`, `id10196_unit`), and one `geopoint` field named `geopoint`.
31 `begin_group`/`end_group` pairs, no repeats.
The existing coding + COD assignment implementation is keyed on these `Idxxxxx` column names. This is the invariant the VASA work must preserve.
VASA form (`znphifacilityvasa_02_final.xlsx`)
Survey sheet columns: `rn`, `WHO_name`, `type`, `name`, `label::English(en)`, `hint`, `relevant`, `required`, `notes`, `appearance`, `calculation`, `default`, `constraint`, `choice_filter`, `repeat_count`, etc.
~2,257 survey rows: 159 `begin group` + 160 `end group`, 3 `begin repeat`/`end repeat`, ~180 `note` rows, and ~1,749 remaining question/calculate fields.
Structure (top-level → key sections):
`NA_1`, `NA_2` — presets/admin (province, area/constituency/ward/EA, interviewer, IDs like `Q1302` → `Id10010c`, DOB/DOD groups).
`NA_CT/consented/NA_4`, `NA_5`, `NA_6` — common respondent/deceased/consent questions shared by all four instruments (mostly `Q1xxx` names; 82 `Q1xxx` fields, 24 of which carry a `WHO_name`).
`NA_CT/consented/AD_NS0` — Neonate instrument, field names prefixed `N2xxx`.
`NA_CT/consented/AD_CH0` — Child instrument, prefixed `C3xxx`.
`NA_CT/consented/AD_AD0` — Adult instrument, prefixed `A4xxx`; the Maternal questions live inside this section as subgroups (`NA_woman`, `NA_m1`–`NA_m20`, `NA_4351`, `NA_A4402`, …). Do not assume a separate top-level Maternal section — derive section membership from the group hierarchy in the sheet.
The `name` column values are VASA pseudo-names (`Q1xxx`, `N2xxx`, `C3xxx`, `A4xxx`, plus helper calculates like `days4…`, `dia…`, `cal_…`). The original WHO name was moved to the `WHO_name` column.
~704 fields have a non-empty `WHO_name`. Of these, 575 are exact `Id\d+` values covering 318 distinct WHO Ids, and 185 of those Ids appear in more than one instrument section (e.g. `Id10354` ← `N2001` and `C3001`; `Id10360` ← `N2006`, `N2006_6`, `N2006_10`, `N2006_12`, `C3006`).
2 exact-Id WHO_names do not exist as data fields in the WHO form: `Id10078`, `Id10481` (`Id10481` is the `end` metadata field in the WHO form). Handle explicitly, don't crash.
~87 distinct `WHO_name` values are non-standard variants that need explicit handling rules: unit/duration helpers (`Id10120_unit`, `Id10148_units`, `Id10161_unit`, …), sentinel/`z` variants (`Id10120z`, `Id10148z`, …), check fields (`Id10071_check`, `Id10439_check`, `Id10440_check`), suffixed splits (`Id10007a`/`Id10007b`, `Id10023_a`/`_b`, `Id10077a`/`b`, `Id10308_1`/`_2`), combined names (`Id10017/18`, `Id10358/9`), and junk values (`audit`, `b`, `language`). Compare each against the WHO form: if the WHO form has a matching helper field (e.g. WHO form's `id10120_unit`), map to it; if a combined name like `Id10017/18` corresponds to two WHO fields, resolve per the WHO form's semantics (e.g. `Q1202` holds the full name that the WHO form splits into `Id10017`/`Id10018`); anything unresolvable goes into the social-autopsy/extras bucket and is logged, never silently dropped.
Social autopsy questions have an empty `WHO_name` and must be persisted but excluded from the data handed to the coding pipeline.
3 repeat groups — all social autopsy (health-care-seeking pathway), none has a WHO_name:
`br_N2213` (neonate: `cal_2213`, `N2213_2`, `N2213_33`, `N2213_3a`, `N2213_4`, `facility_match`, `bplace`)
`AD_C3253_1` (child: `cal_3253`, `C3253_1`…`C3253_4`, `facility_matchC`, `cbplace`)
`ar_A4252` (adult: `cal_adpos`, `A4252_1`…`A4252_4`, `facility_matchA`)
One geopoint field named `GPS` — exported as `GPS-Latitude`, `GPS-Longitude`, `GPS-Altitude`, `GPS-Accuracy` columns in CSV.
Main CSV export is flat (no group-path prefixes on column names), 1,921 columns, with ODK Central metadata columns: `SubmissionDate`, `starttime`, `endtime`, `deviceid`, `instanceID`, `KEY`, `SubmitterID`, `SubmitterName`, `AttachmentsPresent`, `Status`, `ReviewState`, `Edits`, `FormVersion`.
In each submission, exactly one instrument section is populated (e.g. `N2001` xor `C3001` xor `A4001` non-empty in the samples) — because the repeated WHO Ids live in mutually exclusive sections, the per-record coalesce of duplicate WHO_name columns is unambiguous.
Requirements
Analyse before you build. Write a small parser (script or reusable module) that reads the `survey` sheet of `znphifacilityvasa_02_final.xlsx` and produces the canonical field map: for every row, capture `name`, `WHO_name`, `type`, group path, and instrument section (Neonate / Child / Adult / Maternal / Common / Admin). Rules:
Skip rows of type `note`, `begin group`/`begin_group`, `end group`/`end_group` entirely (also `begin repeat`/`end repeat` markers themselves).
Repeat-group contents are captured, but as social autopsy repeat data, not VA fields.
Classify each remaining field as: `va` (valid, resolvable WHO_name), `sa` (empty/unresolvable WHO_name → social autopsy), or `admin/meta`.
Cross-check every resolved WHO name against the WHO form's field list from `znphi_va_who_v1_5_2_7.xlsx`; emit a validation report (mapped, unmapped, duplicates per WHO Id, WHO Ids in VASA missing from WHO form, WHO Ids in WHO form not covered by VASA — expect ~87 of the latter).
Persist the mapping as generated, version-controlled artifact(s) (e.g. JSON or a generated Python module) — do not hand-type 700 mappings, and do not bury the mapping inside ingestion logic. Regeneration must be repeatable via a management command when the form is revised.
Models. Study the existing `VerbalAutopsy` model (and any related coding/COD models: cause assignments, coding runs, field metadata, location FKs, user/audit fields) before designing. Then:
Create a `VASA` (or similarly named) model whose VA-relevant columns use the WHO names (`Idxxxxx`) as field names, mirroring the `VerbalAutopsy` field definitions (types, choice handling, null semantics) for every WHO Id present in the VASA form. Strongly prefer extracting a shared abstract base class (or shared field-definition source) from `VerbalAutopsy` so VA and VASA cannot drift apart, rather than copy-pasting ~400 field definitions — but do this as a non-breaking refactor: existing VA migrations, data, and behavior must be untouched (verify with the existing test suite and a no-op `makemigrations` check for the VA app after refactoring).
Add social-autopsy storage: either dedicated typed fields or a structured `JSONField` keyed by the VASA pseudo-name — pick whichever matches existing codebase conventions, and justify the choice in the plan. SA data must be queryable and exportable but must never flow into coding input.
Add child models for the three repeats (`br_N2213`, `AD_C3253_1`, `ar_A4252`) — either one model per repeat or one unified care-seeking-event model with an instrument discriminator; FK to the VASA record; preserve `KEY`/`PARENT_KEY` linkage.
Store ODK metadata the same way the VA implementation does (instanceID/KEY as the natural idempotency key, submission date, submitter, form version, device id, review state).
Handle the geopoint exactly as the VA implementation handles its `geopoint` field (find and follow that precedent — latitude/longitude/altitude/accuracy handling, field types, parsing from both pyODK JSON and CSV forms).
Location/admin fields: the final form uses `constituency`, `ward`, `EA`; the older sample CSVs use `province`/`area`/`hospital`. Support the final form as primary; tolerate the legacy columns during ingestion of old submissions (map or store, don't crash), following however the VA implementation models province/district/constituency/ward/ea.
Ingestion. Mirror the existing VA ingestion architecture (find the existing pyODK client usage and the VA load management command; reuse its patterns, settings, error handling, logging, and idempotency approach):
A Django management command (and/or scheduled task, matching VA) that pulls `znphi_vasa_who_2016` submissions via pyODK, including repeat-group data (pyODK/OData nested JSON, or CSV export equivalents with `PARENT_KEY` joins).
Transform each submission: strip group paths, drop notes, map pseudo-names → WHO names using the generated mapping, coalesce duplicated WHO Ids across the mutually exclusive instrument sections (if more than one section unexpectedly has a value for the same WHO Id, log a data-quality warning and apply a deterministic precedence rule), route empty-WHO_name fields to SA storage, parse geopoint, normalize dates/times/selects the same way VA ingestion does.
Idempotent upsert keyed on instanceID/KEY; safe to re-run; per-record error isolation (one bad submission must not abort the batch); summary report (loaded / updated / skipped / errors / unmapped-field counts).
Also support loading from CSV export files (like the provided samples) if the VA implementation supports that path; use the sample CSVs as fixtures.
Coding / cause of death. Wire VASA records into the existing coding and COD-assignment machinery with maximal reuse:
Identify how VA records are fed to coding (queryset → algorithm input built from `Idxxxxx` columns). Refactor minimally so the input builder accepts either model (shared base class, protocol/duck typing, or a common queryset interface) rather than duplicating the pipeline.
VASA records must appear in the same coding workflows/UI/state machine (whatever exists: coding status, assignment, review) with a source/instrument discriminator so users can tell VA from VASA (and Neonate/Child/Adult/Maternal within VASA, derived from which instrument section was completed — the age-group calculates and/or which of `N2xxx`/`C3xxx`/`A4xxx` is populated).
WHO Ids absent from the VASA form (~87) must surface to the coding input the same way a blank answer does for VA — confirm the algorithm input builder treats missing/null correctly.
Social autopsy fields must be verifiably absent from coding input — add a test asserting this.
Quality bar / best practices.
Plan first (explore the codebase, then present an implementation plan) before writing code.
Migrations reviewed and reversible; no changes to existing VA tables' schema/data semantics.
Tests: mapping-generator unit tests (including every non-standard WHO_name variant class listed above), ingestion tests using the four sample CSVs as fixtures (expect 6 main records: 2 neonate, 1 child, 3 adult; 3 + 0 + 4 repeat rows), idempotency test (run twice, no dupes), coding-input test proving a VASA record produces the same shaped input as an equivalent VA record and excludes SA fields, geopoint parsing test, legacy-columns (facility-era) tolerance test.
Follow existing code style, naming, admin registration, and any DRF/serializer/export patterns the VA implementation has — VASA should feel like a native sibling of VA everywhere VA is exposed.
Document: README/docs section describing the mapping pipeline, how to regenerate the mapping after a form revision, and the ingestion command usage.
Suggested execution order
Explore: `VerbalAutopsy` model, coding pipeline, pyODK usage, VA management command, tests. Summarize findings.
Build and run the XLSForm mapping generator; produce and review the validation report; resolve every non-standard WHO_name with an explicit rule table.
Present the implementation plan (models, refactors, ingestion, coding wiring) for approval.
Implement models + migrations (with the non-breaking shared-base refactor).
Implement mapping artifact + ingestion command; load the sample CSVs end-to-end.
Wire coding/COD assignment; run a coding pass on the sample records.
Tests, docs, final verification (full test suite green, `makemigrations --check` clean, sample data ingested and codable).
