"""
Refresh CSASupervisorTargetMap tracker dates and active/inactive status.

Runs a single MIN(today)/MAX(today) aggregation over CSADailyTracker, maps
enumerator names to normalised CSA keys (same logic the dashboard uses), then
bulk-updates rows whose stored first date or status differs from the computed
values. A CSA is active when its latest tracker date is within the past 14 days.

Called automatically at the end of load_csa_tracker so the field stays
current after every import.  Can also be run standalone:

    python manage.py refresh_csa_first_dates
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db.models import Max, Min
from django.utils import timezone

from va_explorer.va_data_management.models import (
    CSADailyTracker,
    CSASupervisorTargetMap,
)
from va_explorer.va_data_management.utils.csa_names import (
    format_csa_display_name,
    normalize_csa_name_for_join,
)


class Command(BaseCommand):
    help = (
        "Refresh CSASupervisorTargetMap.tracker_first_date and active/inactive "
        "status from CSADailyTracker dates."
    )

    def handle(self, *args, **options):
        # today is a TextField storing ISO dates (YYYY-MM-DD), so MIN/MAX are
        # lexicographically correct.
        date_bounds = (
            CSADailyTracker.objects.exclude(today__isnull=True)
            .exclude(today__exact="")
            .exclude(enumerator__isnull=True)
            .exclude(enumerator__exact="")
            .values("enumerator")
            .annotate(first_date=Min("today"), last_date=Max("today"))
        )

        # Build normalised_key -> earliest/latest ISO date strings.
        # Multiple enumerator values can map to the same normalised key; keep the minimum.
        first_date_by_norm: dict[str, str] = {}
        last_date_by_norm: dict[str, str] = {}
        for row in date_bounds:
            norm = normalize_csa_name_for_join(
                format_csa_display_name(row["enumerator"])
            )
            if not norm:
                continue

            existing = first_date_by_norm.get(norm)
            fd = row["first_date"]
            if existing is None or fd < existing:
                first_date_by_norm[norm] = fd

            existing_last = last_date_by_norm.get(norm)
            ld = row["last_date"]
            if existing_last is None or ld > existing_last:
                last_date_by_norm[norm] = ld

        if not first_date_by_norm:
            self.stdout.write("No tracker records found — nothing to update.")
            return

        # Load only the map rows that have a matching tracker enumerator.
        to_update = []
        cutoff = date.today() - timedelta(days=14)
        updated_at = timezone.now()
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

            raw_last = last_date_by_norm.get(obj.csa_name_normalized)
            try:
                last_date = date.fromisoformat(raw_last) if raw_last else None
            except ValueError:
                last_date = None
            new_status = (
                CSASupervisorTargetMap.STATUS_ACTIVE
                if last_date and last_date >= cutoff
                else CSASupervisorTargetMap.STATUS_INACTIVE
            )

            if obj.tracker_first_date != new_date or obj.status != new_status:
                obj.tracker_first_date = new_date
                obj.status = new_status
                obj.updated = updated_at
                to_update.append(obj)

        if to_update:
            CSASupervisorTargetMap.objects.bulk_update(
                to_update, ["tracker_first_date", "status", "updated"], batch_size=500
            )

        self.stdout.write(
            f"refresh_csa_first_dates: updated {len(to_update)} map row(s) "
            f"from {len(first_date_by_norm)} tracker enumerator key(s)."
        )
