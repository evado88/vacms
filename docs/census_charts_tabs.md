You are working in the VACMS codebase.

Update the `va_export` implementation for Verbal Autopsy dataset exports.

Requirement:
When the user selects `Verbal Autopsy` as the dataset in `va_export`, the export output must depend on the selected export type:

1. `Abridged VA`
   Label/meaning:
   `Abridged VA (Location, Demographics, CoD, PII)`

   When this option is selected, the downloaded file must include only the following categories of fields:
   - Necessary identifier fields for analysis, linkage, and tracking
   - Location-related fields
   - Demographic fields
   - Cause-of-death-related fields

   The abridged export must exclude unrelated full verbal autopsy questionnaire fields, narrative fields, internal processing fields, and any other fields that are not required for location, demographics, cause of death, analysis identifiers, or tracking.

2. `Full VA Record`
   When this option is selected, the downloaded file must include the complete Verbal Autopsy record, preserving the existing full-record export behavior unless improvements are needed for correctness, performance, or maintainability.

Implementation instructions:
- Reuse the existing `va_export` implementation as much as possible.
- Do not duplicate export logic unnecessarily.
- Introduce or refine a clear field-selection mechanism for Verbal Autopsy exports, such as a field group, export profile, serializer profile, column map, or similar maintainable structure.
- Ensure that the selected export type controls the columns included in the downloaded file.
- Preserve all existing filters, permissions, dataset selection behavior, file format behavior, and download workflows unless changes are required for this feature.
- Ensure the abridged export includes enough identifier values to support analysis and record tracking, for example VA record ID/UUID, ODK instance ID if available, household/member identifiers if available, location IDs/names, and relevant coding result identifiers.
- Ensure the cause-of-death section includes relevant fields from the implemented VA cause/coding model relationships, such as underlying cause, immediate cause, contributing causes, algorithm outputs, certainty/probability scores, coding source/algorithm, coding status, and coding timestamps where available.
- Ensure demographic fields include the key person-level variables required for VA analysis, such as sex, age/date of birth or equivalent age variables, pregnancy-related demographic indicators where applicable, and other existing demographic fields used by VACMS dashboards or analytics.
- Ensure location fields align with existing VACMS location hierarchy usage, such as province, district, constituency, ward, EA/cluster, facility/community location fields, and GPS/geopoint fields where available.
- Use human-readable column names where the existing export implementation already does so.
- Keep database access efficient by using `select_related`, `prefetch_related`, annotations, or optimized queryset logic where appropriate.
- Avoid N+1 queries when exporting cause-of-death or location-related data.
- Add validation so that an invalid or missing export type does not silently produce the wrong export.
- Maintain backward compatibility where possible. If an existing default is required, use `Full VA Record` as the safest default unless the current UI already specifies another default.
- Update the UI labels/options where necessary so users clearly understand the difference between `Abridged VA` and `Full VA Record`.
- Add or update tests to confirm:
  - Selecting `Verbal Autopsy` + `Abridged VA` exports only identifier, location, demographic, and cause-of-death fields.
  - Selecting `Verbal Autopsy` + `Full VA Record` exports the complete Verbal Autopsy record.
  - Existing filters still apply correctly to both export types.
  - The abridged export excludes unrelated VA questionnaire fields.
  - Cause-of-death fields are populated correctly when related coding results exist.
  - Exports still work when cause-of-death fields are missing or null.
- Ensure the implementation follows existing VACMS coding style, naming conventions, permissions, and export patterns.

Expected outcome:
The `va_export` module should allow users exporting the `Verbal Autopsy` dataset to choose between:
- A focused abridged VA export containing only Location, Demographics, Cause of Death, and essential tracking/analysis identifiers.
- A full VA export containing the complete verbal autopsy record.
