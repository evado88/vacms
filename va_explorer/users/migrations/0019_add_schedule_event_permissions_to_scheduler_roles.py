from django.db import migrations


SCHEDULER_GROUPS = (
    "Admins",
    "Data Managers",
    "Mortality Surveillance Officer",
    "Community Surveillance Officer",
)


def add_schedule_event_permissions(apps, schema_editor):
    group_model = apps.get_model("auth", "Group")
    permission_model = apps.get_model("auth", "Permission")
    content_type_model = apps.get_model("contenttypes", "ContentType")

    event_content_type, _ = content_type_model.objects.get_or_create(
        app_label="vacms",
        model="event",
    )
    permissions = []
    for codename, name in (
        ("view_event", "Can view event"),
        ("change_event", "Can change event"),
    ):
        permission, _ = permission_model.objects.get_or_create(
            content_type=event_content_type,
            codename=codename,
            defaults={"name": name},
        )
        permissions.append(permission)

    for group_name in SCHEDULER_GROUPS:
        group, _ = group_model.objects.get_or_create(name=group_name)
        group.permissions.add(*permissions)


class Migration(migrations.Migration):
    dependencies = [
        ("vacms", "0007_event_performance_indexes"),
        ("users", "0018_restore_denied_permissions"),
    ]

    operations = [
        migrations.RunPython(add_schedule_event_permissions, migrations.RunPython.noop),
    ]
