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

        summary = load_vasa_from_csv(options["csv_file"], repeat_csvs)

        self.stdout.write(
            "Loaded VASA: "
            f"created={summary['created']}, updated={summary['updated']}, "
            f"skipped={summary['skipped']}, errors={summary['errors']}, "
            f"who_id_collisions={summary['collisions']}"
        )
        for group, stats in summary["repeats"].items():
            self.stdout.write(f"  repeat {group}: {stats}")

        validation = summary.get("death_id_validation")
        if validation is None:
            self.stdout.write(
                self.style.WARNING("DeathID validation failed; see logs.")
            )
        else:
            self.stdout.write(
                f"DeathID validation: checked={validation['checked']}, "
                f"invalid={validation['invalid']}, "
                f"unmatched={validation['unmatched']}, "
                f"resolved={validation['resolved']}"
            )
            if validation["invalid"] or validation["unmatched"]:
                self.stdout.write(
                    self.style.WARNING(
                        "Flagged DeathID problems as DataQualityIssues "
                        "(records were still imported)."
                    )
                )
