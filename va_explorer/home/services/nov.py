from collections import Counter
from datetime import timedelta

from django.core.exceptions import FieldDoesNotExist
from django.db.models import Count, Max, Q, TextField
from django.db.models.functions import Greatest, Lower
from django.utils import timezone

from va_explorer.home.services.common import (
    apply_text_geography_filter,
    build_date_expr,
    parse_submission_timestamp,
)
from va_explorer.utils.profiling import timed_block
from va_explorer.va_data_management.models import (
    Death,
    Household,
    Pregnancy,
    PregnancyOutcome,
    VerbalAutopsy,
)
from va_explorer.va_data_management.utils.location_access import (
    restrict_queryset_to_user_locations,
)

CORE_EVENT_DATE_FIELDS = ("submissiondate", "start", "today")
VA_DATE_FIELDS = ("submissiondate", "Id10012", "created")


def _latest_text_expr(model, date_fields):
    date_exprs = [build_date_expr(model, field_name) for field_name in date_fields]
    return (
        Greatest(*date_exprs, output_field=TextField())
        if len(date_exprs) > 1
        else date_exprs[0]
    )


def _latest_group_queryset(queryset, key_field, date_fields):
    latest_expr = _latest_text_expr(queryset.model, date_fields)
    return (
        queryset.exclude(**{f"{key_field}__isnull": True})
        .exclude(**{key_field: ""})
        .values(key_field)
        .annotate(_latest_ts=Max(latest_expr))
        .exclude(_latest_ts__isnull=True)
    )


def _boundary_token(value):
    if value is None:
        return None
    return value.isoformat()


def _window_bounds(start_dt, end_dt):
    effective_end = end_dt or timezone.localtime(timezone.now())
    windows = {
        "today": effective_end - timedelta(days=1),
        "week": effective_end - timedelta(days=7),
        "last_30_days": effective_end - timedelta(days=30),
        "total": start_dt,
    }
    if start_dt is not None:
        for key, value in tuple(windows.items()):
            if value is None:
                windows[key] = start_dt
            else:
                windows[key] = max(value, start_dt)
    return windows, effective_end


def _filter_latest_groups(group_qs, start_dt, end_dt):
    filtered = group_qs
    start_token = _boundary_token(start_dt)
    end_token = _boundary_token(end_dt)
    if start_token:
        filtered = filtered.filter(_latest_ts__gte=start_token)
    if end_token:
        filtered = filtered.filter(_latest_ts__lte=end_token)
    return filtered


def _latest_range_q(start_dt, end_dt):
    query = Q()
    start_token = _boundary_token(start_dt)
    end_token = _boundary_token(end_dt)
    if start_token:
        query &= Q(_latest_ts__gte=start_token)
    if end_token:
        query &= Q(_latest_ts__lte=end_token)
    return query


def _field_is_unique(model, field_name):
    try:
        return bool(model._meta.get_field(field_name).unique)
    except FieldDoesNotExist:
        return False


def _aggregate_unique_key_windowed_counts(
    queryset, key_field, date_fields, start_dt, end_dt
):
    latest_expr = _latest_text_expr(queryset.model, date_fields)
    rows = (
        queryset.exclude(**{f"{key_field}__isnull": True})
        .exclude(**{key_field: ""})
        .annotate(_latest_ts=latest_expr)
        .exclude(_latest_ts__isnull=True)
    )
    windows, effective_end = _window_bounds(start_dt, end_dt)
    aggregate = rows.aggregate(
        total=Count("pk", filter=_latest_range_q(windows["total"], effective_end)),
        today=Count("pk", filter=_latest_range_q(windows["today"], effective_end)),
        week=Count("pk", filter=_latest_range_q(windows["week"], effective_end)),
        last_30_days=Count(
            "pk", filter=_latest_range_q(windows["last_30_days"], effective_end)
        ),
    )
    return {
        "today": aggregate.get("today") or 0,
        "week": aggregate.get("week") or 0,
        "last_30_days": aggregate.get("last_30_days") or 0,
        "total": aggregate.get("total") or 0,
    }


def _db_windowed_counts(queryset, key_field, date_fields, start_dt, end_dt):
    if _field_is_unique(queryset.model, key_field):
        return _aggregate_unique_key_windowed_counts(
            queryset, key_field, date_fields, start_dt, end_dt
        )

    group_qs = _latest_group_queryset(queryset, key_field, date_fields)
    windows, effective_end = _window_bounds(start_dt, end_dt)
    return {
        name: _filter_latest_groups(group_qs, window_start, effective_end).count()
        for name, window_start in windows.items()
    }


def _safe_int(value):
    """Parse a household self-reported count from a free-text field.

    Household census counts (HH_46A / HH_47A / death_size) are stored as
    ``TextField``s, so values are parsed in Python rather than DB-cast. Mirrors
    the tolerant parsing in ``home.dashboard_metrics._safe_int``.
    """
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    text = str(value).strip().replace(",", "")
    if not text:
        return 0
    try:
        return int(float(text))
    except (TypeError, ValueError):
        digits = "".join(ch for ch in text if (ch.isdigit() or ch == "-"))
        try:
            return int(digits)
        except (TypeError, ValueError):
            return 0


def _household_detected_windowed_sums(households_qs, field, start_dt, end_dt):
    """Sum a parsed household self-reported count field across time windows.

    Mirrors :func:`_db_windowed_counts` but sums parsed text values instead of
    counting rows. Each household is timestamped via the latest of
    ``CORE_EVENT_DATE_FIELDS`` and bucketed into the today/week/last_30_days/
    total windows in a single Python pass.
    """
    latest_expr = _latest_text_expr(households_qs.model, CORE_EVENT_DATE_FIELDS)
    rows = (
        households_qs.annotate(_latest_ts=latest_expr)
        .exclude(_latest_ts__isnull=True)
        .values_list("_latest_ts", field)
    )
    windows, effective_end = _window_bounds(start_dt, end_dt)
    totals = dict.fromkeys(windows, 0)
    for raw_ts, raw_value in rows.iterator():
        ts = parse_submission_timestamp(raw_ts)
        if ts is None:
            continue
        if effective_end is not None and ts > effective_end:
            continue
        value = _safe_int(raw_value)
        if not value:
            continue
        for name, window_start in windows.items():
            if window_start is None or ts >= window_start:
                totals[name] += value
    return totals


def _people_counts_for_households(households_qs, start_dt, end_dt):
    windows, effective_end = _window_bounds(start_dt, end_dt)

    latest_expr = _latest_text_expr(households_qs.model, CORE_EVENT_DATE_FIELDS)
    household_rows = (
        households_qs.exclude(key__isnull=True)
        .exclude(key="")
        .annotate(_latest_ts=latest_expr)
        .exclude(_latest_ts__isnull=True)
    )

    aggregate = household_rows.aggregate(
        total=Count(
            "members",
            filter=_latest_range_q(windows["total"], effective_end),
        ),
        today=Count(
            "members",
            filter=_latest_range_q(windows["today"], effective_end),
        ),
        week=Count(
            "members",
            filter=_latest_range_q(windows["week"], effective_end),
        ),
        last_30_days=Count(
            "members",
            filter=_latest_range_q(windows["last_30_days"], effective_end),
        ),
    )
    return {
        "today": aggregate.get("today") or 0,
        "week": aggregate.get("week") or 0,
        "last_30_days": aggregate.get("last_30_days") or 0,
        "total": aggregate.get("total") or 0,
    }


def latest_timestamps_by_identifier(queryset, key_field, date_fields):
    agg_rows = (
        _latest_group_queryset(queryset, key_field, date_fields)
        .values_list(key_field, "_latest_ts")
        .iterator()
    )
    latest_by_key = {}
    for identifier, raw_ts in agg_rows:
        if not identifier or not raw_ts:
            continue
        ts = parse_submission_timestamp(raw_ts)
        if ts is not None:
            latest_by_key[identifier] = ts
    return latest_by_key


def _iter_month_starts(start_dt, end_dt):
    current = start_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_month = end_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while current <= end_month:
        yield current
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)


def _filter_timestamps(timestamps, start_dt, end_dt):
    return [
        ts
        for ts in timestamps
        if (start_dt is None or ts >= start_dt) and (end_dt is None or ts <= end_dt)
    ]


def _build_chart_series(pregnancy_ts, outcome_ts, death_ts, va_ts, start_dt, end_dt):
    series = {
        "pregnancy": _filter_timestamps(pregnancy_ts, start_dt, end_dt),
        "pregnancy_outcome": _filter_timestamps(outcome_ts, start_dt, end_dt),
        "death": _filter_timestamps(death_ts, start_dt, end_dt),
        "va": _filter_timestamps(va_ts, start_dt, end_dt),
    }

    all_values = (
        series["pregnancy"]
        + series["pregnancy_outcome"]
        + series["death"]
        + series["va"]
    )
    if not all_values:
        return {
            "labels": [],
            "pregnancy": [],
            "pregnancy_outcome": [],
            "death": [],
            "va": [],
        }

    chart_start = start_dt or min(all_values)
    chart_end = end_dt or max(all_values)
    day_span = (chart_end.date() - chart_start.date()).days
    use_daily = day_span <= 62

    if use_daily:
        labels = []
        label = chart_start.date()
        end_label = chart_end.date()
        while label <= end_label:
            labels.append(label.isoformat())
            label += timedelta(days=1)
        keyed = {
            name: Counter(ts.date().isoformat() for ts in values)
            for name, values in series.items()
        }
    else:
        labels = [
            month.strftime("%Y-%m")
            for month in _iter_month_starts(chart_start, chart_end)
        ]
        keyed = {
            name: Counter(ts.strftime("%Y-%m") for ts in values)
            for name, values in series.items()
        }

    return {
        "labels": labels,
        "pregnancy": [keyed["pregnancy"].get(label, 0) for label in labels],
        "pregnancy_outcome": [
            keyed["pregnancy_outcome"].get(label, 0) for label in labels
        ],
        "death": [keyed["death"].get(label, 0) for label in labels],
        "va": [keyed["va"].get(label, 0) for label in labels],
    }


def _build_scoped_querysets(request, location_level, location_value):
    user = getattr(request, "user", None)
    households_qs = apply_text_geography_filter(
        restrict_queryset_to_user_locations(Household.objects.all(), user),
        Household,
        location_level,
        location_value,
    )
    pregnancies_qs = apply_text_geography_filter(
        restrict_queryset_to_user_locations(Pregnancy.objects.all(), user),
        Pregnancy,
        location_level,
        location_value,
    )
    pregnancy_outcomes_qs = apply_text_geography_filter(
        restrict_queryset_to_user_locations(PregnancyOutcome.objects.all(), user),
        PregnancyOutcome,
        location_level,
        location_value,
    )
    deaths_qs = apply_text_geography_filter(
        restrict_queryset_to_user_locations(Death.objects.all(), user),
        Death,
        location_level,
        location_value,
    )
    if user:
        va_qs = user.verbal_autopsies().filter(deleted_at__isnull=True, duplicate=False)
    else:
        va_qs = VerbalAutopsy.objects.filter(deleted_at__isnull=True, duplicate=False)
    va_qs = apply_text_geography_filter(
        va_qs,
        VerbalAutopsy,
        location_level,
        location_value,
    )
    return households_qs, pregnancies_qs, pregnancy_outcomes_qs, deaths_qs, va_qs


def _exclude_census_querysets_by_province(querysets, excluded_provinces):
    """Drop rows in ``excluded_provinces`` from the four census querysets.

    Used by the Baseline Census Dashboard's overview KPIs when the "Current
    Baseline Census" filter is on. Matching is case-insensitive against the
    denormalised ``province`` text field. The VerbalAutopsy queryset (5th) is
    returned untouched — it has no plain ``province`` field and the
    baseline-census overview does not surface VAs.
    """
    lowered = {str(p).strip().lower() for p in (excluded_provinces or []) if p}
    if not lowered:
        return querysets
    households_qs, pregnancies_qs, pregnancy_outcomes_qs, deaths_qs, va_qs = querysets
    filtered = tuple(
        qs.annotate(_province_lower=Lower("province")).exclude(
            _province_lower__in=lowered
        )
        for qs in (households_qs, pregnancies_qs, pregnancy_outcomes_qs, deaths_qs)
    )
    return (*filtered, va_qs)


def _prefilter_by_submissiondate(querysets, start_dt):
    if start_dt is None:
        return querysets
    cutoff = (start_dt.date() - timedelta(days=1)).isoformat()
    date_prefilter = (
        Q(submissiondate__gte=cutoff)
        | Q(submissiondate__isnull=True)
        | Q(submissiondate="")
    )
    return tuple(qs.filter(date_prefilter) for qs in querysets)


def get_national_operational_view_data(
    start_dt=None,
    end_dt=None,
    request=None,
    location_level="national",
    location_value="",
    include_chart_series=True,
    census_province_exclude=None,
):
    if end_dt is None:
        end_dt = timezone.localtime(timezone.now())

    scoped_querysets = _build_scoped_querysets(request, location_level, location_value)
    if census_province_exclude:
        scoped_querysets = _exclude_census_querysets_by_province(
            scoped_querysets, census_province_exclude
        )

    (
        households_qs,
        pregnancies_qs,
        pregnancy_outcomes_qs,
        deaths_qs,
        va_qs,
    ) = _prefilter_by_submissiondate(scoped_querysets, start_dt)

    with timed_block("home.nov.kpi_counts.households", request=request):
        households_totals = _db_windowed_counts(
            households_qs, "key", CORE_EVENT_DATE_FIELDS, start_dt, end_dt
        )
        eas_totals = _db_windowed_counts(
            households_qs, "ea", CORE_EVENT_DATE_FIELDS, start_dt, end_dt
        )
    with timed_block("home.nov.people_aggregate", request=request):
        people_totals = _people_counts_for_households(households_qs, start_dt, end_dt)
    with timed_block("home.nov.kpi_counts.events", request=request):
        pregnancies_totals = _db_windowed_counts(
            pregnancies_qs, "key", CORE_EVENT_DATE_FIELDS, start_dt, end_dt
        )
        pregnancy_outcomes_totals = _db_windowed_counts(
            pregnancy_outcomes_qs, "key", CORE_EVENT_DATE_FIELDS, start_dt, end_dt
        )
        deaths_totals = _db_windowed_counts(
            deaths_qs, "key", CORE_EVENT_DATE_FIELDS, start_dt, end_dt
        )
        vas_totals = _db_windowed_counts(
            va_qs, "instanceid", VA_DATE_FIELDS, start_dt, end_dt
        )
    with timed_block("home.nov.kpi_counts.detected", request=request):
        pregnancies_detected = _household_detected_windowed_sums(
            households_qs, "HH_46A", start_dt, end_dt
        )
        preg_outcomes_detected = _household_detected_windowed_sums(
            households_qs, "HH_47A", start_dt, end_dt
        )
        deaths_detected = _household_detected_windowed_sums(
            households_qs, "death_size", start_dt, end_dt
        )

    if include_chart_series:
        with timed_block("home.nov.latest_timestamps.events", request=request):
            pregnancies_by_key = latest_timestamps_by_identifier(
                pregnancies_qs,
                "key",
                CORE_EVENT_DATE_FIELDS,
            )
            pregnancy_outcomes_by_key = latest_timestamps_by_identifier(
                pregnancy_outcomes_qs,
                "key",
                CORE_EVENT_DATE_FIELDS,
            )
            deaths_by_key = latest_timestamps_by_identifier(
                deaths_qs,
                "key",
                CORE_EVENT_DATE_FIELDS,
            )
            vas_by_key = latest_timestamps_by_identifier(
                va_qs,
                "instanceid",
                VA_DATE_FIELDS,
            )
        with timed_block("home.nov.chart_series", request=request):
            chart_data = _build_chart_series(
                list(pregnancies_by_key.values()),
                list(pregnancy_outcomes_by_key.values()),
                list(deaths_by_key.values()),
                list(vas_by_key.values()),
                start_dt,
                end_dt,
            )
    else:
        chart_data = {
            "labels": [],
            "pregnancy": [],
            "pregnancy_outcome": [],
            "death": [],
            "va": [],
        }

    kpis = {
        "eas": eas_totals,
        "households": households_totals,
        "people": people_totals,
        "pregnancies": pregnancies_totals,
        "preg_outcomes": pregnancy_outcomes_totals,
        "deaths": deaths_totals,
        "vas": vas_totals,
        "pregnancies_detected": pregnancies_detected,
        "preg_outcomes_detected": preg_outcomes_detected,
        "deaths_detected": deaths_detected,
    }

    return {
        "chart_labels": chart_data["labels"],
        "pregnancy_values": chart_data["pregnancy"],
        "pregnancy_outcome_values": chart_data["pregnancy_outcome"],
        "death_values": chart_data["death"],
        "va_values": chart_data["va"],
        "kpis": kpis,
    }
