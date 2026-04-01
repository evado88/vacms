import calendar
from collections import Counter
from datetime import date, datetime, time, timedelta

from django.db.models import Max
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from va_explorer.va_data_management.models import Death, Household, Pregnancy, PregnancyOutcome
from va_explorer.va_data_management.utils.location_access import (
    restrict_queryset_to_user_locations,
)

WINDOW_COLUMNS = ("24", "1 week", "1 month", "Overall")


def _fix_tz_offset(text):
    if len(text) >= 5 and (text[-5] in "+-") and text[-4:].isdigit():
        return text[:-5] + text[-5:-2] + ":" + text[-2:]
    return text


def _parse_submission_datetime(raw_value):
    if isinstance(raw_value, datetime):
        dt = raw_value
    elif isinstance(raw_value, date):
        dt = datetime.combine(raw_value, time.min)
    else:
        raw = str(raw_value or "").strip()
        if not raw:
            return None

        normalized = _fix_tz_offset(raw.replace("Z", "+00:00"))
        candidates = [normalized]
        if "T" in normalized:
            candidates.append(_fix_tz_offset(normalized.replace("T", " ")))

        dt = None
        for candidate in candidates:
            dt = parse_datetime(candidate)
            if dt:
                break

        if dt is None:
            parsed_date = parse_date(normalized)
            if parsed_date is not None:
                dt = datetime.combine(parsed_date, time.min)

        if dt is None:
            return None

    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return timezone.localtime(dt)


def _sub_calendar_months(dt, months):
    year = dt.year
    month = dt.month - months
    while month <= 0:
        month += 12
        year -= 1

    # Clamp day for short months.
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _month_sequence_12(now_local):
    start = _sub_calendar_months(now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0), 11)
    months = []
    current = start
    for _ in range(12):
        months.append(current)
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return months


def _empty_metric_trend(month_labels):
    return {
        "table": {"recorded": {label: 0 for label in WINDOW_COLUMNS}},
        "graphs": {"recorded": {"x": month_labels, "y": [0.0] * len(month_labels)}},
    }


def _to_cumulative(values):
    running = 0.0
    cumulative = []
    for value in values:
        running += float(value)
        cumulative.append(running)
    return cumulative


def build_trend_for_queryset(queryset, key_field="key"):
    now_local = timezone.localtime(timezone.now())
    since_day = now_local - timedelta(days=1)
    since_week = now_local - timedelta(days=7)
    since_month = _sub_calendar_months(now_local, 1)

    months = _month_sequence_12(now_local)
    month_keys = [month.strftime("%Y-%m") for month in months]
    month_labels = [month.strftime("%b") for month in months]
    output = _empty_metric_trend(month_labels)

    # Use DB-side MAX aggregation to get the latest submissiondate per unique key.
    # This avoids loading every row into Python memory just to build a dict of
    # max-per-key — a single GROUP BY + MAX query is far cheaper for large tables.
    agg_rows = (
        queryset.exclude(**{f"{key_field}__isnull": True})
        .exclude(**{key_field: ""})
        .values(key_field)
        .annotate(latest_sub=Max("submissiondate"))
        .iterator()
    )

    timestamps = []
    for row in agg_rows:
        ts = _parse_submission_datetime(row.get("latest_sub"))
        if ts is not None:
            timestamps.append(ts)

    if not timestamps:
        return output
    output["table"]["recorded"]["Overall"] = len(timestamps)
    output["table"]["recorded"]["24"] = sum(1 for ts in timestamps if ts >= since_day)
    output["table"]["recorded"]["1 week"] = sum(1 for ts in timestamps if ts >= since_week)
    output["table"]["recorded"]["1 month"] = sum(1 for ts in timestamps if ts >= since_month)

    month_counter = Counter(ts.strftime("%Y-%m") for ts in timestamps)
    monthly_counts = [float(month_counter.get(key, 0)) for key in month_keys]
    output["graphs"]["recorded"]["y"] = _to_cumulative(monthly_counts)
    return output


def get_model_trends_data(user=None):
    households_qs = restrict_queryset_to_user_locations(Household.objects.all(), user)
    pregnancies_qs = restrict_queryset_to_user_locations(Pregnancy.objects.all(), user)
    outcomes_qs = restrict_queryset_to_user_locations(PregnancyOutcome.objects.all(), user)
    deaths_qs = restrict_queryset_to_user_locations(Death.objects.all(), user)
    return {
        "households": build_trend_for_queryset(households_qs),
        "pregnancies": build_trend_for_queryset(pregnancies_qs),
        "pregnancy_outcomes": build_trend_for_queryset(outcomes_qs),
        "deaths": build_trend_for_queryset(deaths_qs),
    }
