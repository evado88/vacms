"""Give every ODK form a canonical, filterable submission timestamp.

Adds ``submitted_at`` to the operational forms and backfills it for all six
form models. ``0057`` added the column to VA/VASA without a data migration, so
existing rows stayed NULL and were invisible to every dated export until an
operator remembered to run ``backfill_va_submitted_at`` by hand. Backfilling
here means ``migrate`` alone repairs any environment.
"""

import logging

from django.core.exceptions import ValidationError
from django.db import migrations, models

from va_explorer.va_data_management.utils.submission_timestamp import (
    parse_form_date,
    parse_submission_timestamp,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 1000

# Mirrors ``SUBMISSION_TIMESTAMP_SOURCES`` on the models. Declared here rather
# than read off the model because historical models resolved through
# ``apps.get_model()`` do not carry the class attribute.
SOURCES_BY_MODEL = {
    "VerbalAutopsy": (("submissiondate", parse_submission_timestamp),),
    "VASA": (("submissiondate", parse_submission_timestamp),),
    "Household": (("submissiondate", parse_submission_timestamp),),
    "Death": (("submissiondate", parse_submission_timestamp),),
    "Pregnancy": (
        ("submissiondate", parse_submission_timestamp),
        ("today", parse_form_date),
    ),
    "PregnancyOutcome": (
        ("submissiondate", parse_submission_timestamp),
        ("today", parse_form_date),
    ),
    "CSADailyTracker": (
        ("submissiondate", parse_submission_timestamp),
        ("today", parse_form_date),
    ),
}


def _derive(record, sources):
    error = None
    for field_name, parser in sources:
        try:
            parsed = parser(getattr(record, field_name, None))
        except ValidationError as exc:
            error = error or exc
            continue
        if parsed is not None:
            return parsed
    if error is not None:
        raise error
    return None


def _backfill_model(model, sources):
    """Populate ``submitted_at`` wherever it is NULL, in bounded batches.

    ``apps.get_model()`` hands back a plain auto-created manager rather than the
    project's ``SoftDeletionManager``, so soft-deleted rows are included — the
    same coverage as ``all_objects``.
    """
    source_fields = [field_name for field_name, _parser in sources]
    stats = {"updated": 0, "blank": 0, "invalid": 0}
    last_pk = 0

    while True:
        batch = list(
            model._default_manager.filter(
                submitted_at__isnull=True,
                pk__gt=last_pk,
            )
            .order_by("pk")
            .only("pk", *source_fields)[:BATCH_SIZE]
        )
        if not batch:
            break
        last_pk = batch[-1].pk

        updates = []
        for record in batch:
            try:
                submitted_at = _derive(record, sources)
            except ValidationError:
                # An unattended ``migrate`` must not abort on dirty legacy data.
                # Leave the row NULL and report the total; the record stays
                # visible to ``backfill_va_submitted_at --dry-run``, which is
                # the operator tool for chasing bad source values.
                stats["invalid"] += 1
                continue
            if submitted_at is None:
                stats["blank"] += 1
                continue
            record.submitted_at = submitted_at
            updates.append(record)

        if updates:
            model._default_manager.bulk_update(
                updates, ["submitted_at"], batch_size=len(updates)
            )
            stats["updated"] += len(updates)

    return stats


def backfill_submitted_at(apps, schema_editor):
    for model_name, sources in SOURCES_BY_MODEL.items():
        model = apps.get_model("va_data_management", model_name)
        stats = _backfill_model(model, sources)
        message = (
            f"{model_name}: submitted_at updated={stats['updated']} "
            f"blank={stats['blank']} invalid={stats['invalid']}"
        )
        if stats["invalid"]:
            logger.warning("%s (left NULL, excluded from dated exports)", message)
        else:
            logger.info(message)


class Migration(migrations.Migration):
    # bulk_update over ~175k rows should not be held in a single transaction,
    # and each batch is individually safe to re-run.
    atomic = False

    dependencies = [
        ("va_data_management", "0058_va_vasa_submitted_at_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="death",
            name="submitted_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="Canonical submission timestamp",
            ),
        ),
        migrations.AddField(
            model_name="historicaldeath",
            name="submitted_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="Canonical submission timestamp",
            ),
        ),
        migrations.AddField(
            model_name="historicalhousehold",
            name="submitted_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="Canonical submission timestamp",
            ),
        ),
        migrations.AddField(
            model_name="historicalpregnancy",
            name="submitted_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="Canonical submission timestamp",
            ),
        ),
        migrations.AddField(
            model_name="historicalpregnancyoutcome",
            name="submitted_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="Canonical submission timestamp",
            ),
        ),
        migrations.AddField(
            model_name="household",
            name="submitted_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="Canonical submission timestamp",
            ),
        ),
        migrations.AddField(
            model_name="pregnancy",
            name="submitted_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="Canonical submission timestamp",
            ),
        ),
        migrations.AddField(
            model_name="pregnancyoutcome",
            name="submitted_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="Canonical submission timestamp",
            ),
        ),
        migrations.AddField(
            model_name="csadailytracker",
            name="submitted_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="Canonical submission timestamp",
            ),
        ),
        migrations.AddField(
            model_name="historicalcsadailytracker",
            name="submitted_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="Canonical submission timestamp",
            ),
        ),
        # Reverse is a no-op: reversing the AddFields drops the column outright,
        # and the VA/VASA rows this also repairs predate this migration.
        migrations.RunPython(backfill_submitted_at, migrations.RunPython.noop),
    ]
