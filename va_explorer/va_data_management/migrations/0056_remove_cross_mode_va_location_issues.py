from django.db import migrations
from django.db.models import Q


FACILITY_ISSUE_PREFIXES = (
    "ERROR: no location provided",
    "ERROR: location field (parsed from hospital)",
    "Warning: VA location was matched to facility known",
    "Warning: location field (parsed from hospital)",
)
COMMUNITY_ISSUE_PREFIX = "ERROR: unmatched EA/cluster for community VA"


def _has_meaningful_text(value):
    normalized = ("" if value is None else str(value)).strip().lower()
    return normalized not in {"", "nan", "none", "null"}


def _va_mode(row):
    explicit_mode = (row["verbalautopsy__community_va"] or "").strip().lower()
    if explicit_mode in {"yes", "y", "true", "1"}:
        return "community"
    if explicit_mode in {"no", "n", "false", "0"}:
        return "facility"
    if any(
        _has_meaningful_text(row[field])
        for field in (
            "verbalautopsy__district",
            "verbalautopsy__constituency",
            "verbalautopsy__ward",
            "verbalautopsy__ea",
        )
    ):
        return "community"
    if _has_meaningful_text(row["verbalautopsy__hospital"]):
        return "facility"
    if _has_meaningful_text(row["verbalautopsy__area"]):
        return "community"
    return None


def remove_cross_mode_location_issues(apps, schema_editor):
    CauseCodingIssue = apps.get_model("va_data_management", "CauseCodingIssue")
    issues = CauseCodingIssue.objects
    if schema_editor is not None:
        issues = issues.using(schema_editor.connection.alias)

    location_issue_filter = Q(text__startswith=COMMUNITY_ISSUE_PREFIX)
    for prefix in FACILITY_ISSUE_PREFIXES:
        location_issue_filter |= Q(text__startswith=prefix)

    issue_rows = (
        issues.filter(
            location_issue_filter,
            verbalautopsy_id__isnull=False,
            algorithm="",
        )
        .values(
            "id",
            "text",
            "verbalautopsy__community_va",
            "verbalautopsy__district",
            "verbalautopsy__constituency",
            "verbalautopsy__ward",
            "verbalautopsy__ea",
            "verbalautopsy__area",
            "verbalautopsy__hospital",
        )
        .iterator(chunk_size=2000)
    )

    stale_ids = []
    for row in issue_rows:
        mode = _va_mode(row)
        text = row["text"] or ""
        if mode == "community" and text.startswith(FACILITY_ISSUE_PREFIXES):
            stale_ids.append(row["id"])
        elif mode == "facility" and text.startswith(COMMUNITY_ISSUE_PREFIX):
            stale_ids.append(row["id"])

    for start in range(0, len(stale_ids), 1000):
        issues.filter(id__in=stale_ids[start : start + 1000]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("va_data_management", "0055_dataqualityissue_dq_target_status_idx"),
    ]

    operations = [
        migrations.RunPython(
            remove_cross_mode_location_issues,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
