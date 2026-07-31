"""Populate canonical submission timestamps from raw ODK values.

Migration 0059 backfills every form model, so this command is the re-runnable
operator tool: use ``--dry-run`` to audit raw values that cannot be parsed, and
a plain run to repair records imported by a path that bypassed ``save()``.
"""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from va_explorer.va_data_management.models import (
    VASA,
    CSADailyTracker,
    Death,
    Household,
    Pregnancy,
    PregnancyOutcome,
    VerbalAutopsy,
)
from va_explorer.va_data_management.models.mixins import derive_submitted_at

# Every model carrying SubmissionTimestampMixin. Each declares its own
# SUBMISSION_TIMESTAMP_SOURCES, so the derivation (including the ``today``
# fallback where one applies) stays defined in exactly one place.
BACKFILL_MODELS = (
    ("VA", VerbalAutopsy),
    ("VASA", VASA),
    ("Household", Household),
    ("Pregnancy", Pregnancy),
    ("PregnancyOutcome", PregnancyOutcome),
    ("Death", Death),
    ("CSADailyTracker", CSADailyTracker),
)


class Command(BaseCommand):
    help = (
        "Backfill missing canonical submitted_at values for every ODK form "
        "model without creating history entries."
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
        for label, model in BACKFILL_MODELS:
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
    def _manager(model):
        """Soft-deleted rows still need a timestamp; plain models have no
        ``all_objects``."""
        return getattr(model, "all_objects", model.objects)

    @staticmethod
    def _backfill_model(model, *, batch_size, dry_run):
        stats = {"parsed": 0, "blank": 0, "invalid": 0, "updated": 0}
        manager = Command._manager(model)
        source_fields = [
            field_name for field_name, _parser in model.SUBMISSION_TIMESTAMP_SOURCES
        ]
        last_pk = 0

        while True:
            if dry_run:
                batch = list(
                    manager.filter(
                        submitted_at__isnull=True,
                        pk__gt=last_pk,
                    )
                    .order_by("pk")
                    .only("pk", *source_fields)[:batch_size]
                )
                Command._parse_batch(batch, stats, update=False, model=model)
            else:
                with transaction.atomic():
                    batch = list(
                        manager.select_for_update()
                        .filter(
                            submitted_at__isnull=True,
                            pk__gt=last_pk,
                        )
                        .order_by("pk")
                        .only("pk", *source_fields)[:batch_size]
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
                submitted_at = derive_submitted_at(
                    record, model.SUBMISSION_TIMESTAMP_SOURCES
                )
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
            Command._manager(model).bulk_update(
                updates,
                ["submitted_at"],
                batch_size=len(updates),
            )
            stats["updated"] += len(updates)
