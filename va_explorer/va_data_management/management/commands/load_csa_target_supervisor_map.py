import argparse
import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from va_explorer.va_data_management.models import CSASupervisorTargetMap
from va_explorer.va_data_management.utils.csa_names import (
    format_csa_display_name,
    normalize_csa_name_for_join,
)


def parse_target(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = int(float(text))
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


class Command(BaseCommand):
    help = "Load CSA supervisor/target CSV mapping into the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_file",
            nargs="?",
            type=argparse.FileType("r", encoding="utf-8-sig"),
            help="Path to CSV file (defaults to MEDIA_ROOT/uploads/csa_target_supervisor_map.csv)",
        )
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Delete DB mapping rows not present in the input file.",
        )

    def handle(self, *args, **options):
        file_obj = options.get("csv_file")
        close_after = False
        if file_obj is None:
            default_path = (
                Path(getattr(settings, "MEDIA_ROOT", ""))
                / "uploads"
                / "csa_target_supervisor_map.csv"
            )
            if not default_path.exists():
                raise CommandError(f"CSV file not found: {default_path}")
            file_obj = default_path.open("r", encoding="utf-8-sig", newline="")
            close_after = True

        try:
            reader = csv.DictReader(file_obj)
            required_headers = {"CSA", "Supervisor", "CSA Target"}
            present_headers = set(reader.fieldnames or [])
            missing = required_headers - present_headers
            if missing:
                raise CommandError(
                    "Missing required CSV columns: " + ", ".join(sorted(missing))
                )

            rows_by_key = {}
            skipped_blank_csa = 0
            duplicate_rows = 0
            for row in reader:
                csa_raw = (row.get("CSA") or "").strip()
                display_name = format_csa_display_name(csa_raw)
                normalized_name = normalize_csa_name_for_join(display_name)
                if not normalized_name:
                    skipped_blank_csa += 1
                    continue

                if normalized_name in rows_by_key:
                    duplicate_rows += 1

                rows_by_key[normalized_name] = {
                    "csa_name_raw": csa_raw,
                    "csa_display_name": display_name,
                    "csa_name_normalized": normalized_name,
                    "supervisor_name": (row.get("Supervisor") or "").strip(),
                    "csa_target": parse_target(row.get("CSA Target")),
                }

            existing_by_key = {
                obj.csa_name_normalized: obj
                for obj in CSASupervisorTargetMap.objects.filter(
                    csa_name_normalized__in=list(rows_by_key.keys())
                )
            }

            to_create = []
            to_update = []
            source_filename = Path(getattr(file_obj, "name", "")).name
            updated_at = timezone.now()

            for key, payload in rows_by_key.items():
                obj = existing_by_key.get(key)
                if obj is None:
                    to_create.append(
                        CSASupervisorTargetMap(
                            **payload,
                            source_filename=source_filename,
                        )
                    )
                    continue

                changed = False
                for field_name in (
                    "csa_name_raw",
                    "csa_display_name",
                    "supervisor_name",
                    "csa_target",
                ):
                    new_value = payload[field_name]
                    if getattr(obj, field_name) != new_value:
                        setattr(obj, field_name, new_value)
                        changed = True

                if obj.source_filename != source_filename:
                    obj.source_filename = source_filename
                    changed = True

                if changed:
                    obj.updated = updated_at
                    to_update.append(obj)

            deleted_count = 0
            with transaction.atomic():
                if to_create:
                    CSASupervisorTargetMap.objects.bulk_create(to_create, batch_size=1000)
                if to_update:
                    CSASupervisorTargetMap.objects.bulk_update(
                        to_update,
                        [
                            "csa_name_raw",
                            "csa_display_name",
                            "supervisor_name",
                            "csa_target",
                            "source_filename",
                            "updated",
                        ],
                        batch_size=1000,
                    )
                if options.get("prune"):
                    deleted_count, _ = CSASupervisorTargetMap.objects.exclude(
                        csa_name_normalized__in=list(rows_by_key.keys())
                    ).delete()

            self.stdout.write(
                self.style.SUCCESS(
                    "CSA mapping import complete: "
                    f"created={len(to_create)}, updated={len(to_update)}, "
                    f"deleted={deleted_count}, duplicate_rows={duplicate_rows}, "
                    f"skipped_blank_csa={skipped_blank_csa}, "
                    f"total_keys={len(rows_by_key)}"
                )
            )
        finally:
            if close_after:
                file_obj.close()
