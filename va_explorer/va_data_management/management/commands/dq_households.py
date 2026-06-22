# va_explorer/va_data_management/management/commands/dq_households.py

from django.core.management.base import BaseCommand

from va_explorer.va_data_management.management.commands.dedupe_child_records import (
    print_dedup_summary,
)
from va_explorer.va_data_management.models import Household
from va_explorer.va_data_management.utils.dq_specs import DQ_SPECS, run_dq_for_model
from va_explorer.va_data_management.utils.repeat_group_dedup import (
    CHILD_DEDUP_SPECS,
    run_dedup_for_model,
)


class Command(BaseCommand):
    help = (
        "Run data quality checks on Household entries and persist results.\n"
        "- Duplicate checks: exact-field, EA+HUN+HHN within window, admin+HUN/HHN\n"
        "- Duration: short interviews\n"
        "- Timeliness: late submissions\n"
        "- Completeness (MVR): HH_01, HH_02, enumerator for household=YES; "
        "blank HH_* + valid result for household=NO\n"
        "- Consent: when household=YES, consent must be affirmative\n"
        "Results are stored as DataQualityIssue records and written to "
        "reports/dq_households.{csv,json}."
    )

    def handle(self, *args, **opts):
        summary = run_dq_for_model(DQ_SPECS[Household])
        _print_summary(self, summary)

        # Content-based duplicate detection for the household repeat-group rows.
        for target in ("members", "deaths"):
            child_summary = run_dedup_for_model(CHILD_DEDUP_SPECS[target])
            print_dedup_summary(self, child_summary)


def _print_summary(command, summary):
    command.stdout.write(
        command.style.SUCCESS(
            f"{summary['model']} data-quality checks complete "
            f"({summary['total_rows']} records scanned)."
        )
    )
    counts = {
        "duplicate groups": summary["duplicate"],
        "short-duration": summary["duration"],
        "timeliness": summary["timeliness"],
        "completeness": summary["incomplete"],
        "consent": summary["consent"],
        "validity": summary["validity"],
    }
    if not any(counts.values()):
        command.stdout.write(command.style.SUCCESS("No issues found."))
    else:
        command.stdout.write("Open issues by category:")
        for label, n in counts.items():
            if n:
                command.stdout.write(f"  - {label}: {n}")
    for issue_type, n in (summary.get("resolved") or {}).items():
        command.stdout.write(
            command.style.WARNING(f"Resolved {n} stale {issue_type} issue(s).")
        )
