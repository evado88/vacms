import csv
import json
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from va_explorer.va_admin.models import AdminPanelAlert


class Command(BaseCommand):
    help = (
        "Backup admin panel alerts to JSON and CSV files (supports external disk path)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            dest="output_dir",
            required=True,
            help="Directory path for backup files (can be mounted external disk path).",
        )
        parser.add_argument(
            "--prefix",
            dest="prefix",
            default="admin_panel_alerts",
            help="Filename prefix for generated backup files.",
        )

    def handle(self, *args, **options):
        output_dir = Path(str(options["output_dir"]).strip()).expanduser()
        prefix = (
            str(options["prefix"] or "admin_panel_alerts").strip()
            or "admin_panel_alerts"
        )

        if not output_dir:
            raise CommandError("--output-dir is required")

        output_dir.mkdir(parents=True, exist_ok=True)

        now_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = output_dir / f"{prefix}_{now_stamp}.json"
        csv_path = output_dir / f"{prefix}_{now_stamp}.csv"

        alerts = list(
            AdminPanelAlert.objects.select_related("user").order_by("created_at")
        )
        records = []
        for alert in alerts:
            records.append(
                {
                    "id": alert.id,
                    "user_id": alert.user_id,
                    "user_email": (
                        getattr(alert.user, "email", "") if alert.user else ""
                    ),
                    "category": alert.category,
                    "severity": alert.severity,
                    "title": alert.title,
                    "summary": alert.summary,
                    "details": alert.details,
                    "context": alert.context,
                    "path": alert.path,
                    "ip_address": alert.ip_address,
                    "user_agent": alert.user_agent,
                    "created_at": alert.created_at.isoformat(),
                }
            )

        with open(json_path, "w", encoding="utf-8") as json_file:
            json.dump(records, json_file, indent=2, ensure_ascii=False)

        fieldnames = [
            "id",
            "user_id",
            "user_email",
            "category",
            "severity",
            "title",
            "summary",
            "details",
            "context",
            "path",
            "ip_address",
            "user_agent",
            "created_at",
        ]
        with open(csv_path, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            for row in records:
                csv_row = dict(row)
                csv_row["context"] = json.dumps(
                    csv_row.get("context") or {}, ensure_ascii=False
                )
                writer.writerow(csv_row)

        self.stdout.write(
            self.style.SUCCESS(
                f"Backed up {len(records)} alerts to {json_path} and {csv_path}"
            )
        )
