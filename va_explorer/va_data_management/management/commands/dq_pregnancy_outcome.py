# va_explorer/va_data_management/management/commands/dq_pregnancy_outcome.py

from django.core.management.base import BaseCommand

from va_explorer.va_data_management.management.commands.dedupe_child_records import (
    print_dedup_summary,
)
from va_explorer.va_data_management.management.commands.dq_households import (
    _print_summary,
)
from va_explorer.va_data_management.models import PregnancyOutcome
from va_explorer.va_data_management.utils.dq_specs import DQ_SPECS, run_dq_for_model
from va_explorer.va_data_management.utils.repeat_group_dedup import (
    CHILD_DEDUP_SPECS,
    run_dedup_for_model,
)


class Command(BaseCommand):
    help = (
        "Run data quality checks on Pregnancy Outcome records and persist results.\n"
        "- Duplicate checks: same mother/household/delivery date\n"
        "- Duration: short interviews; Timeliness: late submissions\n"
        "- Completeness (MVR): mother, household ref, delivery date, outcome, enumerator\n"
        "- Consent: consent must be affirmative\n"
        "- Validity: delivery date not in future, born-alive/dead counts, "
        "multiple-birth consistency\n"
        "Results are stored as DataQualityIssue records and written to "
        "reports/dq_pregnancy_outcome.{csv,json}."
    )

    def handle(self, *args, **opts):
        summary = run_dq_for_model(DQ_SPECS[PregnancyOutcome])
        _print_summary(self, summary)

        # Content-based duplicate detection for the baby repeat-group rows.
        child_summary = run_dedup_for_model(CHILD_DEDUP_SPECS["babies"])
        print_dedup_summary(self, child_summary)
