# va_explorer/va_data_management/management/commands/dq_pregnancy.py

from django.core.management.base import BaseCommand

from va_explorer.va_data_management.management.commands.dq_households import (
    _print_summary,
)
from va_explorer.va_data_management.models import Pregnancy
from va_explorer.va_data_management.utils.dq_specs import DQ_SPECS, run_dq_for_model


class Command(BaseCommand):
    help = (
        "Run data quality checks on Pregnancy records and persist results.\n"
        "- Duplicate checks: same woman/household/LMP\n"
        "- Duration: short interviews; Timeliness: late submissions\n"
        "- Completeness (MVR): pregnant woman, household ref, enumerator\n"
        "- Consent: PE_03 must be affirmative\n"
        "- Validity: LMP not in future, LMP <= EDD, antenatal-visit count valid\n"
        "Results are stored as DataQualityIssue records and written to "
        "reports/dq_pregnancy.{csv,json}."
    )

    def handle(self, *args, **opts):
        summary = run_dq_for_model(DQ_SPECS[Pregnancy])
        _print_summary(self, summary)
