from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("va_data_management", "0030_optimize_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="csasupervisortargetmap",
            name="status",
            field=models.CharField(
                choices=[("active", "Active"), ("inactive", "Inactive")],
                db_index=True,
                default="inactive",
                max_length=10,
            ),
        ),
    ]
