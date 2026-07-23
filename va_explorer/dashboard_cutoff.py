from datetime import date, datetime, time

from django.utils import timezone

DASHBOARD_CHART_START_DATE = date(2025, 1, 1)
DASHBOARD_CHART_START_ISO = DASHBOARD_CHART_START_DATE.isoformat()


def dashboard_chart_start_datetime():
    return timezone.make_aware(
        datetime.combine(DASHBOARD_CHART_START_DATE, time.min),
        timezone.get_current_timezone(),
    )


def clamp_chart_start_dt(start_dt):
    cutoff = dashboard_chart_start_datetime()
    if start_dt is None or start_dt < cutoff:
        return cutoff
    return start_dt


def clamp_chart_start_iso(start_iso):
    if not start_iso or start_iso < DASHBOARD_CHART_START_ISO:
        return DASHBOARD_CHART_START_ISO
    return start_iso


def date_is_on_or_after_dashboard_cutoff(value):
    if value is None:
        return False
    if isinstance(value, datetime):
        value = (
            timezone.localtime(value).date()
            if timezone.is_aware(value)
            else value.date()
        )
    if isinstance(value, date):
        return value >= DASHBOARD_CHART_START_DATE
    return str(value)[:10] >= DASHBOARD_CHART_START_ISO
