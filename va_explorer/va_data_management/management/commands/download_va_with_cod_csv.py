import csv
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from va_explorer.va_data_management.models import CauseOfDeath, VerbalAutopsy

DEFAULT_DOWNLOAD_DIR = Path("/srv/vacms/shared/media/downloads")
COD_VALUE_FIELDS = ("id", "cause", "algorithm", "settings", "created", "updated")


class Command(BaseCommand):
    help = (
        "Export verbal autopsy records joined with their latest cause-of-death "
        "record (one row per VA) to a CSV under "
        "/srv/vacms/shared/media/downloads/."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default=str(DEFAULT_DOWNLOAD_DIR),
            help="Directory to write the CSV into (default: %(default)s)",
        )
        parser.add_argument(
            "--filename",
            default=None,
            help=(
                "Override the output filename. "
                "Default: va_with_cod_<UTC-timestamp>.csv"
            ),
        )

    def handle(self, *args, **options):
        out_dir = Path(options["output_dir"])
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError) as exc:
            raise CommandError(f"Cannot create output directory {out_dir}.") from exc

        filename = options["filename"] or (
            f"va_with_cod_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.csv"
        )
        csv_path = out_dir / filename

        va_field_names = [
            f.name
            for f in VerbalAutopsy._meta.get_fields()
            if getattr(f, "concrete", False)
            and not f.many_to_many
            and not getattr(f, "one_to_many", False)
        ]

        # DISTINCT ON (verbalautopsy_id) ordered by updated DESC selects the
        # latest CauseOfDeath per VA in a single Postgres query. Soft-deleted
        # VAs are filtered out by CauseOfDeathManager.
        va_value_fields = [f"verbalautopsy__{name}" for name in va_field_names]

        queryset = (
            CauseOfDeath.objects.order_by("verbalautopsy_id", "-updated", "-id")
            .distinct("verbalautopsy_id")
            .values(*COD_VALUE_FIELDS, *va_value_fields)
        )

        fieldnames = list(va_field_names) + [f"cod_{n}" for n in COD_VALUE_FIELDS]

        exported = 0
        try:
            with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
                writer.writeheader()
                for row in queryset.iterator():
                    out_row = {
                        name: _serialize(row[f"verbalautopsy__{name}"])
                        for name in va_field_names
                    }
                    for name in COD_VALUE_FIELDS:
                        out_row[f"cod_{name}"] = _serialize(row[name])
                    writer.writerow(out_row)
                    exported += 1
        except PermissionError as exc:
            raise CommandError(f"Cannot write CSV file at {csv_path}.") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Exported {exported} VA-with-cause-of-death rows to {csv_path}"
            )
        )


def _serialize(value):
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
