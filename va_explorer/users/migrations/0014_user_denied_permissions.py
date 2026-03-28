from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0013_alter_feedback_attachment"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="denied_permissions",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
