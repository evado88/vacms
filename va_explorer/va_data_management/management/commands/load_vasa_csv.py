"""Load VASA submissions from CSV exports (main + optional repeat groups).

Mirrors ``load_va_csv`` for the VASA form. The repeat CSVs are the ODK
repeat-group exports joined to the main export by ``PARENT_KEY`` -> ``KEY``.

Examples::

    python manage.py load_vasa_csv main.csv \\
        --repeat br_N2213=neonate_repeat.csv \\
        --repeat AD_C3253_1=child_repeat.csv \\
        --repeat ar_A4252=adult_repeat.csv
"""

from django.core.management.base import BaseCommand, CommandError

from va_explorer.va_data_management.utils.vasa_loading import (
    REPEAT_MODELS,
    load_vasa_from_csv,
)


class Command(BaseCommand):
    help = "Load a VASA CSV export (and optional repeat-group CSVs) into the database"

    def add_arguments(self, parser):
        parser.add_argument("csv_file")
        parser.add_argument(
            "--repeat",
            action="append",
            default=[],
            metavar="GROUP=PATH",
            help=(
                "Repeat-group CSV as GROUP=PATH; GROUP is one of "
                f"{', '.join(REPEAT_MODELS)}. May be repeated."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        repeat_csvs = {}
        for item in options["repeat"]:
            if "=" not in item:
                raise CommandError(f"--repeat expects GROUP=PATH, got '{item}'")
            group, path = item.split("=", 1)
            group = group.strip()
            if group not in REPEAT_MODELS:
                raise CommandError(
                    f"Unknown repeat group '{group}'. Expected one of "
                    f"{', '.join(REPEAT_MODELS)}."
                )
            repeat_csvs[group] = path.strip()

        dry_run = options["dry_run"]
        summary = load_vasa_from_csv(options["csv_file"], repeat_csvs, dry_run=dry_run)

        def out(line):
            self.stdout.write(f"DRY RUN: {line}" if dry_run else line)

        out(f"Rows read: {summary['rows']}")
        out(
            "Loaded VASA: "
            f"created={summary['created']}, updated={summary['updated']}, "
            f"skipped={summary['skipped']}, errors={summary['errors']}"
        )
        out(
            f"WHO-Id collisions: {summary['collisions']} event(s) across "
            f"{summary['records_with_collisions']} record(s) — "
            f"resolved by precedence: {summary['collisions_resolved']}, "
            f"unresolved (DataQualityIssue flagged): "
            f"{summary['collisions_unresolved']}"
        )
        out(
            f"DeathID: {summary['rows_with_death_id']} of {summary['rows']} rows "
            f"contained a DeathID ({summary['rows_without_death_id']} without)"
        )
        for group, stats in summary["repeats"].items():
            out(f"  repeat {group}: {stats}")

        validation = summary.get("death_id_validation")
        if dry_run:
            out("DeathID validation: skipped (dry run)")
        elif validation is None:
            self.stdout.write(
                self.style.WARNING("DeathID validation failed; see logs.")
            )
        else:
            out(
                "DeathID validation (whole table): "
                f"checked={validation['checked']}, "
                f"invalid={validation['invalid']}, "
                f"unmatched={validation['unmatched']}, "
                f"resolved={validation['resolved']}"
            )
            if validation["checked"] == 0:
                out(
                    "  note: checked=0 because "
                    f"{summary['rows_with_death_id']} of {summary['rows']} "
                    "imported rows contained a DeathID and no other VASA record "
                    "carries one"
                )
            if validation["invalid"] or validation["unmatched"]:
                self.stdout.write(
                    self.style.WARNING(
                        "Flagged DeathID problems as DataQualityIssues "
                        "(records were still imported)."
                    )
                )

        linking = summary["linking"]
        out(
            "Death linking: "
            + (
                f"enabled — scheduled {linking['scheduled']} record(s)"
                if linking["enabled"]
                else "disabled (VA_DEATH_MATCH_ENABLED off) — scheduled 0 record(s)"
            )
        )

        if dry_run:
            for change in summary.get("changes", []):
                if change["action"] == "create":
                    out(f"  would create {change['instanceid']}")
                    continue
                fields = change.get("fields", {})
                if not fields:
                    out(f"  no changes for {change['instanceid']}")
                    continue
                out(f"  would update {change['instanceid']}:")
                for field, diff in fields.items():
                    out(f"    {field}: {diff['old']!r} -> {diff['new']!r}")
