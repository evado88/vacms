import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from va_explorer.va_data_management.models import CauseOfDeath


class Command(BaseCommand):
    help = (
        "Download cause, algorithm, settings, created, updated, and verbalautopsy_id "
        "from va_data_management_causeofdeath to CSV."
    )

    def handle(self, *args, **options):
        _ = (args, options)
        app_dir = getattr(settings, "APPS_DIR", None)
        root_dir = getattr(settings, "ROOT_DIR", None)
        if app_dir:
            csv_path = Path(app_dir) / "static" / "data" / "causeofdeath.csv"
        elif root_dir:
            csv_path = Path(root_dir) / "va_explorer" / "static" / "data" / "causeofdeath.csv"
        else:
            # Fallback for non-standard settings modules.
            csv_path = Path.cwd() / "va_explorer" / "static" / "data" / "causeofdeath.csv"
        try:
            csv_path.parent.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            raise CommandError(
                "Cannot create output directory for causeofdeath export at "
                f"{csv_path}"
            ) from exc

        fieldnames = [
            "cause",
            "algorithm",
            "settings",
            "created",
            "updated",
            "verbalautopsy_id",
        ]

        queryset = CauseOfDeath.objects.values(
            "cause",
            "algorithm",
            "settings",
            "created",
            "updated",
            "verbalautopsy_id",
        ).order_by("id")

        exported = 0
        try:
            with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
                writer.writeheader()

                for row in queryset.iterator():
                    writer.writerow(
                        {
                            "cause": row["cause"],
                            "algorithm": row["algorithm"],
                            "settings": row["settings"],
                            "created": row["created"].isoformat() if row["created"] else "",
                            "updated": row["updated"].isoformat() if row["updated"] else "",
                            "verbalautopsy_id": row["verbalautopsy_id"],
                        }
                    )
                    exported += 1
        except PermissionError as exc:
            raise CommandError(
                f"Cannot write CSV file at {csv_path}."
            ) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Exported {exported} CauseOfDeath rows to {csv_path}"
            )
        )
