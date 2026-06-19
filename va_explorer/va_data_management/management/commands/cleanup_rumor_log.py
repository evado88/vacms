from django.core.management.base import BaseCommand

from va_explorer.va_data_management.odk.service import purge_junk_rumor_logs


class Command(BaseCommand):
    help = (
        "Remove any RumorLog records whose rumour description is a known junk "
        "value (typos, placeholders, 'No rumour', 'Nil', etc.). "
        "Safe to run multiple times — idempotent once all junk records are gone."
    )

    def handle(self, *args, **options):
        purged = purge_junk_rumor_logs()
        if purged:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Purged {purged} junk rumour log record(s) from the database."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "No junk rumour log records found — nothing to purge."
                )
            )
