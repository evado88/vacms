from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("va_data_management", "0057_historicalvasa_submitted_at_and_more"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="vasa",
            index=models.Index(
                condition=models.Q(
                    ("deleted_at__isnull", True),
                    ("submitted_at__isnull", False),
                ),
                fields=["submitted_at"],
                name="vasa_submitted_at_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="verbalautopsy",
            index=models.Index(
                condition=models.Q(
                    ("deleted_at__isnull", True),
                    ("submitted_at__isnull", False),
                ),
                fields=["submitted_at"],
                name="va_submitted_at_idx",
            ),
        ),
    ]
