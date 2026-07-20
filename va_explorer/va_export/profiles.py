"""Export field profiles for Verbal Autopsy downloads.

The ``Abridged VA`` export profile keeps only the columns needed for analysis and
record tracking: identifiers, location, demographics and cause-of-death. The lists
below are intentionally grouped and human-readable so the profile is easy to extend.

The export view (:mod:`va_explorer.va_export.views`) intersects
``ABRIDGED_VA_EXPORT_COLUMNS`` with the model-backed export schema, so any field
that is not available is silently skipped.
"""

# Identifier / tracking fields needed for linkage and audit.
VA_ABRIDGED_IDENTIFIER_FIELDS = [
    "id",
    "instanceid",
    "instancename",
    "deviceid",
    "unique_va_identifier",
    "duplicate",
    "key",
]

# Location fields aligned with the VACMS location hierarchy (facility + community/SRS)
# plus the WHO place-of-death fields and the GPS geopoint.
VA_ABRIDGED_LOCATION_FIELDS = [
    "location",
    "province",
    "district",
    "constituency",
    "ward",
    "ea",
    "hospital",
    "area",
    "geopoint",
    "Id10057",  # Place of death (free text geography)
    "Id10058",  # Where the deceased died (category)
]

# Person-level demographics used across VACMS dashboards and analytics.
VA_ABRIDGED_DEMOGRAPHIC_FIELDS = [
    "Id10019",  # Sex
    "Id10020",  # Date of birth known?
    "Id10021",  # Date of birth
    "ageInYears",
    "ageInMonths",
    "ageInDays",
    "age_group",
    "isNeonatal",
    "isChild",
    "isAdult",
    "Id10052",  # Nationality / citizenship
    "Id10059",  # Marital status
    # Pregnancy-related demographic indicators.
    "Id10305",
    "Id10306",
    "Id10307",
    "Id10308",
    "Id10309",
    "Id10310",
    "Id10312",
    "Id10315",
]

# Cause-of-death / coding results (annotated from the CauseOfDeath relationship).
VA_ABRIDGED_COD_FIELDS = [
    "cause",
    "cause_id",
    "cause_algorithm",
    "cause_created",
]

# Death date plus record timestamps for tracking.
VA_ABRIDGED_DATE_FIELDS = [
    "Id10023",  # Date of death
    "date",  # Annotated alias of Id10023
    "created",
    "updated",
]


def _ordered_unique(*groups):
    seen = set()
    ordered = []
    for group in groups:
        for field in group:
            if field not in seen:
                seen.add(field)
                ordered.append(field)
    return ordered


# Ordered, de-duplicated column list applied to the abridged VA export.
ABRIDGED_VA_EXPORT_COLUMNS = _ordered_unique(
    VA_ABRIDGED_IDENTIFIER_FIELDS,
    VA_ABRIDGED_LOCATION_FIELDS,
    VA_ABRIDGED_DEMOGRAPHIC_FIELDS,
    VA_ABRIDGED_COD_FIELDS,
    VA_ABRIDGED_DATE_FIELDS,
)
