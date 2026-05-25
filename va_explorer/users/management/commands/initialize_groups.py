from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import BaseCommand

from va_explorer.users.models import User
from va_explorer.va_analytics.models import Dashboard
from va_explorer.va_data_cleanup.models import DataCleanup
from va_explorer.va_data_management.models import (
    Death,
    Household,
    Pregnancy,
    PregnancyOutcome,
    VerbalAutopsy,
)
from va_explorer.vacms.cmsmodels.events import Event

GROUPS_PERMISSIONS = {
    "Admins": {
        Dashboard: ["view_dashboard", "download_data", "view_pii", "supervise_users"],
        User: ["add_user", "change_user", "delete_user", "view_user"],
        VerbalAutopsy: [
            "change_verbalautopsy",
            "view_verbalautopsy",
            "delete_verbalautopsy",
            "bulk_delete",
        ],
        Household: ["view_household", "change_household"],
        Pregnancy: ["view_pregnancy", "change_pregnancy"],
        PregnancyOutcome: ["view_pregnancyoutcome", "change_pregnancyoutcome"],
        Death: ["view_death", "change_death"],
        Event: ["view_event", "change_event"],
        DataCleanup: ["view_datacleanup", "download", "bulk_download"],
    },
    "Data Managers": {
        Dashboard: ["view_dashboard", "download_data", "view_pii", "supervise_users"],
        User: [],
        VerbalAutopsy: [
            "change_verbalautopsy",
            "view_verbalautopsy",
        ],
        Household: ["view_household", "change_household"],
        Pregnancy: ["view_pregnancy", "change_pregnancy"],
        PregnancyOutcome: ["view_pregnancyoutcome", "change_pregnancyoutcome"],
        Death: ["view_death", "change_death"],
        Event: ["view_event", "change_event"],
        DataCleanup: ["view_datacleanup", "download", "bulk_download"],
    },
    "Data Viewers": {
        Dashboard: ["view_dashboard"],
        User: [],
        VerbalAutopsy: ["view_verbalautopsy"],
        Household: ["view_household"],
        Pregnancy: ["view_pregnancy"],
        PregnancyOutcome: ["view_pregnancyoutcome"],
        Death: ["view_death"],
        Event: [],
        DataCleanup: [],
    },
    "Senior Management": {
        Dashboard: ["view_dashboard", "supervise_users"],
        User: [],
        VerbalAutopsy: [],
        Event: [],
        DataCleanup: [],
    },
    "Field Workers": {
        Dashboard: ["view_dashboard"],
        User: [],
        VerbalAutopsy: ["view_verbalautopsy"],
        Event: [],
        DataCleanup: [],
    },
    "Mortality Surveillance Officer": {
        Dashboard: ["view_dashboard", "view_pii", "supervise_users"],
        User: ["add_user", "change_user", "view_user"],
        VerbalAutopsy: [
            "change_verbalautopsy",
            "view_verbalautopsy",
        ],
        Household: ["view_household"],
        Pregnancy: ["view_pregnancy"],
        PregnancyOutcome: ["view_pregnancyoutcome"],
        Death: ["view_death"],
        Event: ["view_event", "change_event"],
        DataCleanup: ["view_datacleanup", "download", "bulk_download"],
    },
    "Community Surveillance Officer": {
        Dashboard: ["view_dashboard", "view_pii", "supervise_users"],
        User: ["add_user", "change_user", "view_user"],
        VerbalAutopsy: [
            "change_verbalautopsy",
            "view_verbalautopsy",
        ],
        Household: ["view_household"],
        Pregnancy: ["view_pregnancy"],
        PregnancyOutcome: ["view_pregnancyoutcome"],
        Death: ["view_death"],
        Event: ["view_event", "change_event"],
        DataCleanup: ["view_datacleanup", "download", "bulk_download"],
    },
}


class Command(BaseCommand):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    help = "Create default groups and permissions"

    def add_arguments(self, parser):
        parser.add_argument("--debug", type=bool, nargs="?", default=False)

    def handle(self, *args, **options):
        debug = options.get("debug", False)
        # Loop through groups and permissions; add permissions, as applicable,
        # to related group objects
        for group_name, group_permissions in GROUPS_PERMISSIONS.items():
            group, _ = Group.objects.get_or_create(name=group_name)

            """
            Delete the permissions if they exist

            Assumption:
            If we are running this script, permissions have changed and we want to
            recreate them
            """
            if group.permissions.exists():
                group.permissions.clear()

            for model_class, model_permissions in group_permissions.items():
                for codename in model_permissions:
                    # Get the content type for the given model class.
                    content_type = ContentType.objects.get_for_model(model_class)

                    # Lookup permission based on content type and codename.
                    try:
                        permission = Permission.objects.get(
                            content_type=content_type, codename=codename
                        )
                        group.permissions.add(permission)
                        self.stdout.write(f"Adding {codename} to group {group}")

                    except Exception as instance:
                        if debug:
                            print(
                                f"{type(instance)} error:\nargs: \
                                {instance.args}\n{instance}"
                            )
                        self.stderr.write(f"{codename} not found")
