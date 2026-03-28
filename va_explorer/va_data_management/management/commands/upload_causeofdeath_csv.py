import csv
import json
from ast import literal_eval
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from va_explorer.va_data_management.models import CauseOfDeath, VerbalAutopsy


def _parse_datetime_or_none(value):
    raw = (value or "").strip()
    if not raw:
        return None

    dt = parse_datetime(raw)
    if dt is None:
        if raw.endswith("Z"):
            dt = parse_datetime(raw.replace("Z", "+00:00"))
    if dt is None:
        return None

    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _parse_settings_or_none(raw_value):
    raw = (raw_value or "").strip()
    if not raw:
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Backward compatibility for exported rows with Python-style dict strings.
        try:
            parsed = literal_eval(raw)
        except (ValueError, SyntaxError):
            return None

    return parsed if isinstance(parsed, dict) else None


def _parse_verbalautopsy_id_or_none(raw_value):
    raw = (raw_value or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    # Common spreadsheet coercion of integers to floats (e.g., 1074.0).
    if raw.endswith(".0"):
        without_decimal = raw[:-2]
        if without_decimal.isdigit():
            return int(without_decimal)
    return None


class Command(BaseCommand):
    help = (
        "Upload cause, algorithm, settings, created, updated, and verbalautopsy_id "
        "to va_data_management_causeofdeath from CSV."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path",
            type=str,
            help="Input CSV file path (absolute or relative).",
        )
        parser.add_argument(
            "--clear-existing",
            action="store_true",
            help="Delete all existing CauseOfDeath rows before upload.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and report results without inserting rows.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        _ = args
        csv_path = Path(options["csv_path"]).expanduser()
        if not csv_path.is_absolute():
            csv_path = Path.cwd() / csv_path
        if not csv_path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")
        if not csv_path.is_file():
            raise CommandError(f"CSV path is not a file: {csv_path}")

        if options["clear_existing"]:
            deleted, _ = CauseOfDeath.objects.all().delete()
            self.stdout.write(
                self.style.WARNING(f"Deleted {deleted} existing CauseOfDeath rows.")
            )

        valid_va_ids = set(VerbalAutopsy.objects.values_list("id", flat=True))
        created_count = 0
        updated_count = 0
        skipped_count = 0
        timestamp_updates = 0
        skip_invalid_id_or_cause = 0
        skip_missing_va = 0
        skip_invalid_settings = 0
        skipped_samples = defaultdict(list)

        with csv_path.open("r", newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            raw_headers = reader.fieldnames or []
            headers = {h.strip() for h in raw_headers if h}

            # Accept both correct and requested misspelled header.
            algorithm_column = "algorithm" if "algorithm" in headers else "algrorithm"
            required = {"cause", "settings", "created", "updated", "verbalautopsy_id"}
            if algorithm_column == "algorithm":
                required.add("algorithm")
            else:
                required.add("algrorithm")

            if not required.issubset(headers):
                raise CommandError(f"CSV must contain columns: {', '.join(sorted(required))}")

            for row in reader:
                cause = (row.get("cause") or "").strip()
                algorithm = (row.get(algorithm_column) or "").strip()
                settings_raw = (row.get("settings") or "").strip()
                verbalautopsy_id_raw = row.get("verbalautopsy_id")
                verbalautopsy_id = _parse_verbalautopsy_id_or_none(verbalautopsy_id_raw)

                if not cause or verbalautopsy_id is None:
                    skipped_count += 1
                    skip_invalid_id_or_cause += 1
                    if len(skipped_samples["invalid_id_or_cause"]) < 5:
                        skipped_samples["invalid_id_or_cause"].append(
                            {
                                "cause": cause,
                                "verbalautopsy_id": (verbalautopsy_id_raw or "").strip(),
                            }
                        )
                    continue

                if verbalautopsy_id not in valid_va_ids:
                    skipped_count += 1
                    skip_missing_va += 1
                    if len(skipped_samples["missing_verbalautopsy"]) < 5:
                        skipped_samples["missing_verbalautopsy"].append(verbalautopsy_id)
                    continue

                settings = _parse_settings_or_none(settings_raw)
                if settings is None:
                    skipped_count += 1
                    skip_invalid_settings += 1
                    if len(skipped_samples["invalid_settings"]) < 5:
                        skipped_samples["invalid_settings"].append(
                            {
                                "verbalautopsy_id": verbalautopsy_id,
                                "settings_preview": settings_raw[:120],
                            }
                        )
                    continue

                existing = CauseOfDeath.objects.filter(
                    verbalautopsy_id=verbalautopsy_id,
                    algorithm=algorithm,
                ).first()

                if options["dry_run"]:
                    if existing is None:
                        created_count += 1
                    else:
                        updated_count += 1
                    continue

                created_dt = _parse_datetime_or_none(row.get("created"))
                updated_dt = _parse_datetime_or_none(row.get("updated"))

                if existing is None:
                    cod = CauseOfDeath.objects.create(
                        cause=cause,
                        algorithm=algorithm,
                        settings=settings,
                        verbalautopsy_id=verbalautopsy_id,
                    )
                    created_count += 1
                else:
                    existing.cause = cause
                    existing.settings = settings
                    existing.save(update_fields=["cause", "settings", "updated"])
                    cod = existing
                    updated_count += 1

                if created_dt is not None or updated_dt is not None:
                    CauseOfDeath.objects.filter(pk=cod.pk).update(
                        created=created_dt or cod.created,
                        updated=updated_dt or cod.updated,
                    )
                    timestamp_updates += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Upload complete. "
                f"Created={created_count}, Updated={updated_count}, "
                f"TimestampUpdated={timestamp_updates}, Skipped={skipped_count} "
                f"(invalid_id_or_cause={skip_invalid_id_or_cause}, "
                f"missing_verbalautopsy={skip_missing_va}, invalid_settings={skip_invalid_settings})"
            )
        )
        for reason, samples in skipped_samples.items():
            self.stdout.write(f"Skip samples for {reason}: {samples}")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run enabled: no rows were inserted."))
