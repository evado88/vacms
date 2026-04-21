"""
Refresh CSASupervisorTargetMap.tracker_first_date for every CSA.

Runs a single MIN(today) aggregation over CSADailyTracker, maps enumerator
names to normalised CSA keys (same logic the dashboard uses), then bulk-updates
only the rows whose stored first date differs from the computed one.

Called automatically at the end of load_csa_tracker so the field stays
current after every import.  Can also be run standalone:

    python manage.py refresh_csa_first_dates
"""

from datetime import date

from django.core.management.base import BaseCommand
from django.db.models import Min

from va_explorer.va_data_management.models import (
    CSADailyTracker,
    CSASupervisorTargetMap,
)
from va_explorer.va_data_management.utils.csa_names import (
    format_csa_display_name,
    normalize_csa_name_for_join,
)


class Command(BaseCommand):
    help = "Refresh CSASupervisorTargetMap.tracker_first_date from CSADailyTracker MIN(today)."

    def handle(self, *args, **options):
        # One aggregation query: MIN(today) per enumerator.
        # today is a TextField storing ISO dates (YYYY-MM-DD), so MIN is lexicographically correct.
        mins = (
            CSADailyTracker.objects.exclude(today__isnull=True)
            .exclude(today__exact="")
            .exclude(enumerator__isnull=True)
            .exclude(enumerator__exact="")
            .values("enumerator")
            .annotate(first_date=Min("today"))
        )

        # Build normalised_key -> earliest ISO date string.
        # Multiple enumerator values can map to the same normalised key; keep the minimum.
        first_date_by_norm: dict[str, str] = {}
        for row in mins:
            norm = normalize_csa_name_for_join(
                format_csa_display_name(row["enumerator"])
            )
            if not norm:
                continue
            existing = first_date_by_norm.get(norm)
            fd = row["first_date"]
            if existing is None or fd < existing:
                first_date_by_norm[norm] = fd

        if not first_date_by_norm:
            self.stdout.write("No tracker records found — nothing to update.")
            return

        # Load only the map rows that have a matching tracker enumerator.
        to_update = []
        for obj in CSASupervisorTargetMap.objects.filter(
            csa_name_normalized__in=list(first_date_by_norm)
        ):
            raw = first_date_by_norm.get(obj.csa_name_normalized)
            if not raw:
                continue
            try:
                new_date = date.fromisoformat(raw)
            except ValueError:
                continue
            if obj.tracker_first_date != new_date:
                obj.tracker_first_date = new_date
                to_update.append(obj)

        if to_update:
            CSASupervisorTargetMap.objects.bulk_update(
                to_update, ["tracker_first_date"], batch_size=500
            )

        self.stdout.write(
            f"refresh_csa_first_dates: updated {len(to_update)} map row(s) "
            f"from {len(first_date_by_norm)} tracker enumerator key(s)."
        )
