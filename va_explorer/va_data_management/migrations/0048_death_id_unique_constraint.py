from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        (
            "va_data_management",
            "0047_causecodingissue_vasa_causeofdeath_vasa_and_more",
        ),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="death",
            constraint=models.UniqueConstraint(
                fields=["death_id"],
                condition=Q(death_id__isnull=False) & ~Q(death_id=""),
                name="unique_death_id_when_set",
            ),
        ),
    ]
