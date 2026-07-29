"""Populate canonical VA/VASA submission timestamps from raw ODK values."""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from va_explorer.va_data_management.models import VASA, VerbalAutopsy
from va_explorer.va_data_management.utils.submission_timestamp import (
    parse_submission_timestamp,
)


class Command(BaseCommand):
    help = (
        "Backfill missing canonical submitted_at values for VA and VASA records "
        "without creating history entries."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report parse results without updating any records.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Maximum records processed in each transaction (default: 1000).",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]
        if batch_size <= 0:
            raise CommandError("--batch-size must be greater than zero")

        if dry_run:
            self.stdout.write("DRY RUN: no records will be updated.")

        total_invalid = 0
        for label, model in (("VA", VerbalAutopsy), ("VASA", VASA)):
            stats = self._backfill_model(
                model,
                batch_size=batch_size,
                dry_run=dry_run,
            )
            total_invalid += stats["invalid"]
            self.stdout.write(
                f"{label}: parsed={stats['parsed']} blank={stats['blank']} "
                f"invalid={stats['invalid']} updated={stats['updated']}"
            )

        # Cutover to submitted_at filtering is only safe once every record can
        # produce a canonical timestamp, so fail loudly rather than printing a
        # warning a deploy script would step over.
        if total_invalid:
            raise CommandError(
                f"Backfill finished with {total_invalid} unparseable raw "
                "submission timestamp(s); these records cannot appear in dated "
                "exports until the source values are corrected."
            )

    @staticmethod
    def _backfill_model(model, *, batch_size, dry_run):
        stats = {"parsed": 0, "blank": 0, "invalid": 0, "updated": 0}
        last_pk = 0

        while True:
            if dry_run:
                batch = list(
                    model.all_objects.filter(
                        submitted_at__isnull=True,
                        pk__gt=last_pk,
                    )
                    .order_by("pk")
                    .only("pk", "submissiondate")[:batch_size]
                )
                Command._parse_batch(batch, stats, update=False, model=model)
            else:
                with transaction.atomic():
                    batch = list(
                        model.all_objects.select_for_update()
                        .filter(
                            submitted_at__isnull=True,
                            pk__gt=last_pk,
                        )
                        .order_by("pk")
                        .only("pk", "submissiondate")[:batch_size]
                    )
                    Command._parse_batch(batch, stats, update=True, model=model)

            if not batch:
                break
            last_pk = batch[-1].pk

        return stats

    @staticmethod
    def _parse_batch(batch, stats, *, update, model):
        updates = []
        for record in batch:
            try:
                submitted_at = parse_submission_timestamp(record.submissiondate)
            except ValidationError:
                stats["invalid"] += 1
                continue

            if submitted_at is None:
                stats["blank"] += 1
                continue

            stats["parsed"] += 1
            record.submitted_at = submitted_at
            updates.append(record)

        if update and updates:
            model.all_objects.bulk_update(
                updates,
                ["submitted_at"],
                batch_size=len(updates),
            )
            stats["updated"] += len(updates)
