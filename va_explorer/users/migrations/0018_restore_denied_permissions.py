from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0017_readd_denied_permissions_stable_storage"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="denied_permissions",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
