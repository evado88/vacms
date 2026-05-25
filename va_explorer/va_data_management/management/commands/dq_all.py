# va_explorer/va_data_management/management/commands/dq_all.py

from django.core.management.base import BaseCommand

from va_explorer.va_data_management.management.commands.dq_households import (
    _print_summary,
)
from va_explorer.va_data_management.utils.dq_specs import DQ_SPECS, run_dq_for_model


class Command(BaseCommand):
    help = (
        "Run data quality checks for ALL datasets (Household, Pregnancy, "
        "Pregnancy Outcome, Death) in one pass and persist the results.\n"
        "Equivalent to running dq_households, dq_pregnancy, dq_pregnancy_outcome and "
        "dq_death together. Results are stored as DataQualityIssue records and written "
        "to reports/dq_<dataset>.{csv,json}."
    )

    def handle(self, *args, **opts):
        for spec in DQ_SPECS.values():
            summary = run_dq_for_model(spec)
            _print_summary(self, summary)
            self.stdout.write("")  # blank line between datasets
        self.stdout.write(
            self.style.SUCCESS("All dataset data-quality checks complete.")
        )
