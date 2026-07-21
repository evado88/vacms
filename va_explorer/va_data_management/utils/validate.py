from va_explorer.va_data_management.models import CauseCodingIssue
from va_explorer.va_data_management.utils.date_parsing import parse_date
from va_explorer.va_data_management.utils.location_assignment import assign_va_location


def _has_meaningful_text(value):
    normalized = ("" if value is None else str(value)).strip().lower()
    return normalized not in {"", "nan", "none", "null"}


def _coding_issue(va, text, severity):
    return CauseCodingIssue(
        verbalautopsy_id=va.id,
        text=text,
        severity=severity,
        algorithm="",
        settings="",
    )


def _append_location_issues(va, issues):
    """Validate only the location fields applicable to this VA's source mode."""
    community_mode = va.community_va_normalized
    if community_mode is None:
        issues.append(
            _coding_issue(
                va,
                "Warning: unable to determine whether VA is community or facility "
                "(community_va unresolved).",
                "warning",
            )
        )
        return

    if community_mode == "yes":
        # Facility, area and Location belong to facility VAs and are intentionally
        # ignored here. A cluster is preferred, while any persisted community
        # geography is enough to avoid a false error when cluster matching is
        # ambiguous or has not run yet.
        has_admin_marker = any(
            _has_meaningful_text(candidate)
            for candidate in (va.district, va.constituency, va.ward, va.ea)
        )
        if not va.cluster and not has_admin_marker:
            issues.append(
                _coding_issue(
                    va,
                    "ERROR: unmatched EA/cluster for community VA",
                    "error",
                )
            )
        return

    # District, constituency, ward, EA and cluster belong to community VAs and
    # are intentionally ignored for facility VAs.
    if not va.location:
        va = assign_va_location(va)
        if not va.location:
            issues.append(
                _coding_issue(
                    va,
                    "ERROR: no location provided (or none detected)",
                    "error",
                )
            )

    if not va.location:
        return

    hospital = str(va.hospital or "").casefold()
    location_name = str(va.location.name or "").casefold()
    if not va.location.is_active and location_name != "unknown" and hospital != "other":
        issues.append(
            _coding_issue(
                va,
                "Warning: VA location was matched to facility known to be inactive. "
                "Consider updating the location to an active facility instead, or "
                "update the facility list.",
                "warning",
            )
        )

    if location_name == "unknown" and hospital == "other":
        issues.append(
            _coding_issue(
                va,
                "Warning: location field (parsed from hospital) was parsed as "
                "'Other Facility'. May not fully show on dashboards until "
                "underlying data is corrected to actual location.",
                "warning",
            )
        )

    if location_name == "unknown" and hospital != "other":
        issues.append(
            _coding_issue(
                va,
                "ERROR: location field (parsed from hospital) did not match any "
                "known facilities in the facility list. VA Explorer set the "
                "location to 'Unknown.' This VA will not show on dashboards until "
                "underlying data is corrected to actual location.",
                "error",
            )
        )


def validate_vas_for_dashboard(verbal_autopsies):
    # This validator is used to determine whether there is sufficient data to include
    # the record in the dashboard. Any errors or warnings are collected and reported
    # in the data manager.
    # The validation has to occur after the va's are created so we can reference
    # the va_id in the CauseCodingIssue.
    # The validator runs after va's are loaded and after a va is edited or reset.
    # TODO: would it be possible to move this to the VA model clean function?
    issues = []
    for va in verbal_autopsies:
        # clear all data related errors in case any were addressed
        CauseCodingIssue.objects.filter(verbalautopsy_id=va.id, algorithm="").delete()

        # Validate: date of death
        # Id10023 is required for the dashboard time frame filters
        # VA form guarantees this field is either "dk" or a valid datetime.date value
        try:
            va.Id10023 = parse_date(va.Id10023, strict=True)
        except:  # noqa E722 - Intent is to save to db, not do anything with exception
            issue_text = f"Error: field Id10023, couldn't parse date from {va.Id10023}"
            issue = CauseCodingIssue(
                verbalautopsy_id=va.id,
                text=issue_text,
                severity="error",
                algorithm="",
                settings="",
            )
            issues.append(issue)

        # Validate: ageInYears2
        # ageInYears2 is required for calculating mean age of death
        try:
            _ = int(float(va.ageInYears2))
        except:  # noqa E722 - Intent is to save to db, not do anything with exception
            issue_text = (
                "Warning: field ageInYears2, age was not provided or not a number."
            )
            issue = CauseCodingIssue(
                verbalautopsy_id=va.id,
                text=issue_text,
                severity="warning",
                algorithm="",
                settings="",
            )
            issues.append(issue)

        # Validate: age
        # age group can be determined from multiple fields, it's required for
        # filtering demographics
        if (
            va.isNeonatal != 1
            and va.isNeonatal != 1.0
            and va.isNeonatal != "1"
            and va.isNeonatal1 != 1
            and va.isNeonatal1 != 1.0
            and va.isNeonatal1 != "1"
            and va.isNeonatal2 != 1
            and va.isNeonatal2 != 1.0
            and va.isNeonatal2 != "1"
            and va.isChild != 1
            and va.isChild != 1.0
            and va.isChild != "1"
            and va.isChild1 != 1
            and va.isChild1 != 1.0
            and va.isChild1 != "1"
            and va.isChild2 != 1
            and va.isChild2 != 1.0
            and va.isChild2 != "1"
            and va.isAdult != 1
            and va.isAdult != 1.0
            and va.isAdult != "1"
            and va.isAdult1 != 1
            and va.isAdult1 != 1.0
            and va.isAdult1 != "1"
            and va.isAdult2 != 1
            and va.isAdult2 != 1.0
            and va.isAdult2 != "1"
        ):
            issue_text = "Warning: field age_group, no relevant data was found in \
                fields; isNeonatal, isNeonatal1, isNeonatal2, isChild, isChild1, \
                isChild2 isAdult, isAdult1, or isAdult2."
            issue = CauseCodingIssue(
                verbalautopsy_id=va.id,
                text=issue_text,
                severity="warning",
                algorithm="",
                settings="",
            )
            issues.append(issue)

        _append_location_issues(va, issues)

    CauseCodingIssue.objects.bulk_create(issues)
