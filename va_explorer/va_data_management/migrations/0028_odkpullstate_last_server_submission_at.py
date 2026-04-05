from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("va_data_management", "0027_csa_mso_performance_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="odkpullstate",
            name="last_server_submission_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
