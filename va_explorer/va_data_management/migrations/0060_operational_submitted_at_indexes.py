"""Index the column every dated export now filters.

Mirrors ``0058`` for the operational forms. Household alone is ~152k rows and
this predicate runs on every download.
"""

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("va_data_management", "0059_canonical_submission_timestamp"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="household",
            index=models.Index(
                condition=models.Q(("submitted_at__isnull", False)),
                fields=["submitted_at"],
                name="household_submitted_at_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="pregnancy",
            index=models.Index(
                condition=models.Q(("submitted_at__isnull", False)),
                fields=["submitted_at"],
                name="pregnancy_submitted_at_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="pregnancyoutcome",
            index=models.Index(
                condition=models.Q(("submitted_at__isnull", False)),
                fields=["submitted_at"],
                name="preg_outcome_submitted_at_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="death",
            index=models.Index(
                condition=models.Q(("submitted_at__isnull", False)),
                fields=["submitted_at"],
                name="death_submitted_at_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="csadailytracker",
            index=models.Index(
                condition=models.Q(("submitted_at__isnull", False)),
                fields=["submitted_at"],
                name="csa_tracker_submitted_at_idx",
            ),
        ),
    ]
