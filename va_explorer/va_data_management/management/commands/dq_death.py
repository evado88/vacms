# va_explorer/va_data_management/management/commands/dq_death.py

from django.core.management.base import BaseCommand

from va_explorer.va_data_management.management.commands.dq_households import (
    _print_summary,
)
from va_explorer.va_data_management.models import Death
from va_explorer.va_data_management.utils.dq_specs import DQ_SPECS, run_dq_for_model


class Command(BaseCommand):
    help = (
        "Run data quality checks on Death records and persist results.\n"
        "- Duplicate checks: same deceased/household/date of death\n"
        "- Timeliness: late submissions\n"
        "- Completeness (MVR): deceased name, sex, date of death, household ref, "
        "enumerator\n"
        "- Consent: consent must be affirmative\n"
        "- Validity: date of death not in future, DOB <= DOD, maternal-death flags "
        "consistent with sex\n"
        "Results are stored as DataQualityIssue records and written to "
        "reports/dq_death.{csv,json}."
    )

    def handle(self, *args, **opts):
        summary = run_dq_for_model(DQ_SPECS[Death])
        _print_summary(self, summary)
