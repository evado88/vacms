import argparse

from django.core.management.base import BaseCommand

from va_explorer.users.utils.user_form_backend import (
    parse_users_from_file,
    save_users_from_data,
)


class Command(BaseCommand):
    help = "Create user accounts, assigning a temporary password, given a csv \
            file that has at a minimum email and name. Group defaults to Data Viewers if not provided. \
            Run ./manage.py get_user_template to see all such options."

    def add_arguments(self, parser):
        parser.add_argument("user_list_file", type=argparse.FileType("r"))
        parser.add_argument("--email_confirmation", type=bool, nargs="?", default=False)

    def handle(self, *args, **options):
        user_file = options["user_list_file"]
        email_confirmation = options.get("email_confirmation", False)

        self.stdout.write("Parsing user file...")
        valid_users_raw, _, invalid_users = parse_users_from_file(
            user_file, debug=False
        )

        if invalid_users:
            self.stdout.write(
                self.style.WARNING(f"Found {len(invalid_users)} invalid rows:")
            )
            for item in invalid_users:
                self.stdout.write(
                    self.style.WARNING(
                        f"Row {item['row']} ({item['email']}): {item['errors']}"
                    )
                )

        if valid_users_raw:
            self.stdout.write(f"Saving {len(valid_users_raw)} valid users...")
            created = save_users_from_data(
                valid_users_raw, email_confirmation=email_confirmation
            )
            self.stdout.write(
                self.style.SUCCESS(f"Successfully created {len(created)} users!")
            )
        else:
            self.stdout.write(self.style.WARNING("No valid users found to create."))

        self.stdout.write(self.style.SUCCESS("Done!"))
