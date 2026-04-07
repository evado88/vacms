from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("va_data_management", "0028_odkpullstate_last_server_submission_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="csasupervisortargetmap",
            name="tracker_first_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
