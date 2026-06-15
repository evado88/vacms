# va_explorer/va_data_management/management/commands/dedupe_child_records.py

from django.core.management.base import BaseCommand

from va_explorer.va_data_management.utils.repeat_group_dedup import (
    CHILD_DEDUP_SPECS,
    run_dedup_for_model,
)

TARGET_CHOICES = (*CHILD_DEDUP_SPECS.keys(), "all")


def print_dedup_summary(command, summary):
    command.stdout.write(
        command.style.SUCCESS(
            f"{summary['label']} duplicate scan complete "
            f"({summary['total_rows']} records scanned)."
        )
    )
    if summary["groups"]:
        command.stdout.write(
            f"  - duplicate sets: {summary['groups']}; "
            f"flagged: {summary['flagged']}; "
            f"un-flagged (self-healed): {summary['unflagged']}"
        )
    else:
        command.stdout.write("  - no content duplicates found.")
    if summary["resolved"]:
        command.stdout.write(
            command.style.WARNING(
                f"  - resolved {summary['resolved']} stale duplicate issue(s)."
            )
        )


class Command(BaseCommand):
    help = (
        "Flag content-based duplicate repeat-group records (household members, "
        "household deaths, pregnancy outcome babies) so each duplicate set is "
        "counted once. Matches on parent + name + DOB + sex; keeps the "
        "most-complete row canonical and records a DataQualityIssue per set. "
        "Idempotent and self-healing on re-run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--target",
            choices=TARGET_CHOICES,
            default="all",
            help="Which child records to scan (default: all).",
        )

    def handle(self, *args, **options):
        target = options["target"]
        targets = list(CHILD_DEDUP_SPECS.keys()) if target == "all" else [target]
        for name in targets:
            summary = run_dedup_for_model(CHILD_DEDUP_SPECS[name])
            print_dedup_summary(self, summary)
