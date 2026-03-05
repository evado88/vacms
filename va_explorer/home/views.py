from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
import difflib
import hashlib
import json
import logging
import re
from zoneinfo import ZoneInfo

import pandas as pd
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.cache import cache
from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import Substr
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date as django_parse_date
from django.utils.dateparse import parse_datetime as django_parse_datetime
from django.views.generic import TemplateView, View

from va_explorer.home.cache_utils import get_home_dashboard_fastpath_version
from va_explorer.home.model_trends import get_model_trends_data
from va_explorer.home.va_trends import get_trends_data
from va_explorer.utils.profiling import timed_block
from va_explorer.utils.mixins import CustomAuthMixin
from va_explorer.va_data_management.models import (
    CSADailyTracker,
    CauseCodingIssue,
    CauseOfDeath,
    Death,
    Household,
    HouseholdMember,
    Location,
    ODKFormChoice,
    Pregnancy,
    PregnancyOutcome,
    VerbalAutopsy,
)
from va_explorer.va_data_management.utils.date_parsing import parse_date
from va_explorer.va_data_management.utils.location_access import (
    restrict_queryset_to_user_locations,
)
from va_explorer.va_data_management.utils.loading import get_va_summary_stats
from va_explorer.vacms.cmsmodels.events import Event

logger = logging.getLogger(__name__)


def _fix_tz_offset(text):
    if len(text) >= 5 and (text[-5] in "+-") and text[-4:].isdigit():
        return text[:-5] + text[-5:-2] + ":" + text[-2:]
    return text


def _parse_submission_timestamp(value):
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min)
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        normalized = _fix_tz_offset(raw.replace("Z", "+00:00"))
        candidates = [normalized]
        if "T" in normalized:
            candidates.append(_fix_tz_offset(normalized.replace("T", " ")))

        dt = None
        for candidate in candidates:
            dt = django_parse_datetime(candidate)
            if dt:
                break
        if dt is None:
            return None

    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return timezone.localtime(dt)


def _first_valid_timestamp(record, date_fields):
    for field in date_fields:
        timestamp = _parse_submission_timestamp(getattr(record, field, None))
        if timestamp is not None:
            return timestamp
    return None


def _latest_timestamps_by_identifier(queryset, key_field, date_fields):
    filtered = queryset.exclude(**{f"{key_field}__isnull": True}).exclude(**{key_field: ""})
    latest_by_key = {}
    only_fields = list(date_fields) + [key_field]
    for record in filtered.only(*only_fields):
        identifier = getattr(record, key_field, None)
        if not identifier:
            continue
        timestamp = _first_valid_timestamp(record, date_fields)
        if timestamp is None:
            continue
        existing = latest_by_key.get(identifier)
        if existing is None or timestamp > existing:
            latest_by_key[identifier] = timestamp
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


def _windowed_counts(timestamps, start_dt, end_dt):
    filtered = _filter_timestamps(timestamps, start_dt, end_dt)
    if not filtered:
        return {"today": 0, "week": 0, "total": 0}

    effective_end = end_dt or timezone.localtime(timezone.now())
    today_start = effective_end - timedelta(days=1)
    week_start = effective_end - timedelta(days=7)
    if start_dt is not None:
        today_start = max(today_start, start_dt)
        week_start = max(week_start, start_dt)

    return {
        "today": sum(1 for ts in filtered if today_start <= ts <= effective_end),
        "week": sum(1 for ts in filtered if week_start <= ts <= effective_end),
        "total": len(filtered),
    }


def _build_chart_series(pregnancy_ts, outcome_ts, death_ts, va_ts, start_dt, end_dt):
    series = {
        "pregnancy": _filter_timestamps(pregnancy_ts, start_dt, end_dt),
        "pregnancy_outcome": _filter_timestamps(outcome_ts, start_dt, end_dt),
        "death": _filter_timestamps(death_ts, start_dt, end_dt),
        "va": _filter_timestamps(va_ts, start_dt, end_dt),
    }

    all_values = (
        series["pregnancy"] + series["pregnancy_outcome"] + series["death"] + series["va"]
    )
    if not all_values:
        return {"labels": [], "pregnancy": [], "pregnancy_outcome": [], "death": [], "va": []}

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
        keyed = {name: Counter(ts.date().isoformat() for ts in values) for name, values in series.items()}
    else:
        labels = [month.strftime("%Y-%m") for month in _iter_month_starts(chart_start, chart_end)]
        keyed = {name: Counter(ts.strftime("%Y-%m") for ts in values) for name, values in series.items()}

    return {
        "labels": labels,
        "pregnancy": [keyed["pregnancy"].get(label, 0) for label in labels],
        "pregnancy_outcome": [keyed["pregnancy_outcome"].get(label, 0) for label in labels],
        "death": [keyed["death"].get(label, 0) for label in labels],
        "va": [keyed["va"].get(label, 0) for label in labels],
    }


def _parse_filter_range(request):
    now_local = timezone.localtime(timezone.now())
    preset = (request.GET.get("preset") or "all").strip().lower()
    start_dt = None
    end_dt = now_local

    if preset == "30":
        start_dt = now_local - timedelta(days=30)
    elif preset == "7":
        start_dt = now_local - timedelta(days=7)
    elif preset == "24":
        start_dt = now_local - timedelta(days=1)

    start_raw = (request.GET.get("start") or "").strip()
    end_raw = (request.GET.get("end") or "").strip()

    parsed_start = django_parse_datetime(start_raw) if start_raw else None
    parsed_end = django_parse_datetime(end_raw) if end_raw else None
    if parsed_start is None and start_raw:
        parsed_start_date = django_parse_date(start_raw)
        if parsed_start_date is not None:
            parsed_start = datetime.combine(parsed_start_date, time.min)
    if parsed_end is None and end_raw:
        parsed_end_date = django_parse_date(end_raw)
        if parsed_end_date is not None:
            parsed_end = datetime.combine(parsed_end_date, time.max)
    if parsed_start is not None:
        if timezone.is_naive(parsed_start):
            parsed_start = timezone.make_aware(parsed_start, timezone.get_current_timezone())
        start_dt = timezone.localtime(parsed_start)
    if parsed_end is not None:
        if timezone.is_naive(parsed_end):
            parsed_end = timezone.make_aware(parsed_end, timezone.get_current_timezone())
        end_dt = timezone.localtime(parsed_end)

    if start_dt is not None and end_dt is not None and start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    has_custom_range = bool(start_raw or end_raw)
    return preset, start_dt, end_dt, has_custom_range


def _parse_location_filter(request):
    level = (request.GET.get("location_level") or "national").strip().lower()
    value = (request.GET.get("location_value") or "").strip()
    allowed_levels = {"national", "province", "district", "constituency", "ward", "ea"}
    if level not in allowed_levels:
        level = "national"
    if level == "national":
        return level, ""
    return level, value


def _model_has_field(model, field_name):
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


def _apply_text_geography_filter(queryset, model, level, value):
    if not value or level == "national":
        return queryset
    if not _model_has_field(model, level):
        return queryset
    return queryset.filter(**{f"{level}__iexact": value})


def _normalize_geo_value(value):
    normalized = " ".join(str(value or "").split()).strip()
    if not normalized:
        return ""
    if normalized.lower() in {"nan", "none", "null", "na", "n/a"}:
        return ""
    return normalized


def _serialize_geo_counter(counter, output_key):
    return [
        {output_key: name, "count": count}
        for name, count in sorted(counter.items(), key=lambda item: item[0].lower())
    ]


def _serialize_geo_counter_with_breakdown(counter, output_key, breakdown_counters):
    rows = []
    for name, count in sorted(counter.items(), key=lambda item: item[0].lower()):
        row = {output_key: name, "count": count}
        for field_name, field_counter in breakdown_counters.items():
            row[field_name] = int(field_counter.get(name, 0))
        rows.append(row)
    return rows


def _parse_home_map_filters(request):
    time_preset = (request.GET.get("time_preset") or "all_time").strip().lower()
    start_raw = (request.GET.get("start_datetime") or "").strip()
    end_raw = (request.GET.get("end_datetime") or "").strip()
    map_view = (request.GET.get("map_view") or "Province").strip() or "Province"
    geography_level = (request.GET.get("geography_level") or "").strip().lower()
    geography_value = (request.GET.get("geography_value") or "").strip()

    now = timezone.localtime(timezone.now())
    start_dt = None
    end_dt = now

    if time_preset == "last_30_days":
        start_dt = now - timedelta(days=30)
    elif time_preset == "last_7_days":
        start_dt = now - timedelta(days=7)
    elif time_preset == "last_24_hours":
        start_dt = now - timedelta(hours=24)

    parsed_start = django_parse_datetime(start_raw) if start_raw else None
    parsed_end = django_parse_datetime(end_raw) if end_raw else None
    if parsed_start is None and start_raw:
        parsed_date = django_parse_date(start_raw)
        if parsed_date:
            parsed_start = datetime.combine(parsed_date, time.min)
    if parsed_end is None and end_raw:
        parsed_date = django_parse_date(end_raw)
        if parsed_date:
            parsed_end = datetime.combine(parsed_date, time.max)

    if parsed_start is not None:
        if timezone.is_naive(parsed_start):
            parsed_start = timezone.make_aware(parsed_start, timezone.get_current_timezone())
        start_dt = timezone.localtime(parsed_start)
    if parsed_end is not None:
        if timezone.is_naive(parsed_end):
            parsed_end = timezone.make_aware(parsed_end, timezone.get_current_timezone())
        end_dt = timezone.localtime(parsed_end)

    if start_dt and end_dt and start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    allowed_levels = {"province", "district", "constituency", "ward", "ea"}
    if geography_level not in allowed_levels:
        geography_level = ""
        geography_value = ""

    return {
        "time_preset": time_preset,
        "start_dt": start_dt,
        "end_dt": end_dt,
        "map_view": map_view,
        "geography_level": geography_level,
        "geography_value": geography_value,
    }


def _latest_geo_by_identifier(queryset, key_field, date_fields, start_dt, end_dt):
    geo_fields = ("province", "district", "constituency", "ward", "ea")
    latest = {}
    rows = (
        queryset.exclude(**{f"{key_field}__isnull": True})
        .exclude(**{key_field: ""})
        .values(key_field, *date_fields, *geo_fields)
    )
    for row in rows.iterator():
        identifier = row.get(key_field)
        if not identifier:
            continue

        timestamp = None
        for field_name in date_fields:
            timestamp = _parse_submission_timestamp(row.get(field_name))
            if timestamp is not None:
                break
        if timestamp is None:
            continue
        if start_dt and timestamp < start_dt:
            continue
        if end_dt and timestamp > end_dt:
            continue

        existing = latest.get(identifier)
        if existing is not None and timestamp <= existing[0]:
            continue
        latest[identifier] = (
            timestamp,
            {
                "province": _normalize_geo_value(row.get("province")),
                "district": _normalize_geo_value(row.get("district")),
                "constituency": _normalize_geo_value(row.get("constituency")),
                "ward": _normalize_geo_value(row.get("ward")),
                "ea": _normalize_geo_value(row.get("ea")),
            },
        )
    return latest


def _build_home_combined_map_payload(request):
    filters = _parse_home_map_filters(request)
    geography_level = filters["geography_level"]
    geography_value = filters["geography_value"]
    start_dt = filters["start_dt"]
    end_dt = filters["end_dt"]

    with timed_block("home.map.qs.build", request=request):
        pregnancies_qs = _apply_text_geography_filter(
            restrict_queryset_to_user_locations(Pregnancy.objects.all(), getattr(request, "user", None)),
            Pregnancy,
            geography_level or "national",
            geography_value,
        )
        outcomes_qs = _apply_text_geography_filter(
            restrict_queryset_to_user_locations(PregnancyOutcome.objects.all(), getattr(request, "user", None)),
            PregnancyOutcome,
            geography_level or "national",
            geography_value,
        )
        deaths_qs = _apply_text_geography_filter(
            restrict_queryset_to_user_locations(Death.objects.all(), getattr(request, "user", None)),
            Death,
            geography_level or "national",
            geography_value,
        )
        if request and getattr(request, "user", None):
            vas_qs = request.user.verbal_autopsies().filter(deleted_at__isnull=True, duplicate=False)
        else:
            vas_qs = VerbalAutopsy.objects.filter(deleted_at__isnull=True, duplicate=False)
        vas_qs = _apply_text_geography_filter(
            vas_qs,
            VerbalAutopsy,
            geography_level or "national",
            geography_value,
        )

    with timed_block("home.map.aggregate", request=request):
        model_sets = (
            (
                "pregnancies_count",
                _latest_geo_by_identifier(
                    pregnancies_qs,
                    "key",
                    ("submissiondate", "start", "today"),
                    start_dt,
                    end_dt,
                ),
            ),
            (
                "pregnancy_outcomes_count",
                _latest_geo_by_identifier(
                    outcomes_qs,
                    "key",
                    ("submissiondate", "start", "today"),
                    start_dt,
                    end_dt,
                ),
            ),
            (
                "deaths_count",
                _latest_geo_by_identifier(
                    deaths_qs,
                    "key",
                    ("submissiondate", "start", "today"),
                    start_dt,
                    end_dt,
                ),
            ),
            (
                "verbal_autopsies_count",
                _latest_geo_by_identifier(
                    vas_qs,
                    "instanceid",
                    ("submissiondate", "Id10012", "created"),
                    start_dt,
                    end_dt,
                ),
            ),
        )

        province_counts = Counter()
        district_counts = Counter()
        constituency_counts = Counter()
        ward_counts = Counter()
        ea_counts = Counter()

        province_type_counts = defaultdict(Counter)
        district_type_counts = defaultdict(Counter)
        constituency_type_counts = defaultdict(Counter)
        ward_type_counts = defaultdict(Counter)
        ea_type_counts = defaultdict(Counter)
        total_events = 0

        for type_key, latest_map in model_sets:
            total_events += len(latest_map)
            for _identifier, (_timestamp, geo) in latest_map.items():
                if geo.get("province"):
                    province_name = geo["province"]
                    province_counts[province_name] += 1
                    province_type_counts[type_key][province_name] += 1
                if geo.get("district"):
                    district_name = geo["district"]
                    district_counts[district_name] += 1
                    district_type_counts[type_key][district_name] += 1
                if geo.get("constituency"):
                    constituency_name = geo["constituency"]
                    constituency_counts[constituency_name] += 1
                    constituency_type_counts[type_key][constituency_name] += 1
                if geo.get("ward"):
                    ward_name = geo["ward"]
                    ward_counts[ward_name] += 1
                    ward_type_counts[type_key][ward_name] += 1
                if geo.get("ea"):
                    ea_name = geo["ea"]
                    ea_counts[ea_name] += 1
                    ea_type_counts[type_key][ea_name] += 1

    province_sums = _serialize_geo_counter_with_breakdown(
        province_counts,
        "province_name",
        province_type_counts,
    )
    district_sums = _serialize_geo_counter_with_breakdown(
        district_counts,
        "district_name",
        district_type_counts,
    )
    constituency_sums = _serialize_geo_counter_with_breakdown(
        constituency_counts,
        "constituency_name",
        constituency_type_counts,
    )
    ward_sums = _serialize_geo_counter_with_breakdown(
        ward_counts,
        "ward_name",
        ward_type_counts,
    )
    ea_sums = _serialize_geo_counter_with_breakdown(
        ea_counts,
        "ea_name",
        ea_type_counts,
    )

    counts_by_view = {
        "Province": [{"name": row["province_name"], "count": row["count"]} for row in province_sums],
        "District": [{"name": row["district_name"], "count": row["count"]} for row in district_sums],
        "Constituency": [{"name": row["constituency_name"], "count": row["count"]} for row in constituency_sums],
        "Ward": [{"name": row["ward_name"], "count": row["count"]} for row in ward_sums],
        "EA": [{"name": row["ea_name"], "count": row["count"]} for row in ea_sums],
    }

    return {
        "map_view": filters["map_view"],
        "counts": counts_by_view.get(filters["map_view"], counts_by_view["Province"]),
        "province_counts": counts_by_view["Province"],
        "district_counts": counts_by_view["District"],
        "constituency_counts": counts_by_view["Constituency"],
        "ward_counts": counts_by_view["Ward"],
        "ea_counts": counts_by_view["EA"],
        "map_total_events": total_events,
        "map_province_sums": province_sums,
        "map_district_sums": district_sums,
        "map_constituency_sums": constituency_sums,
        "map_ward_sums": ward_sums,
        "map_ea_sums": ea_sums,
    }


def _scope_fingerprint(user):
    parts = [
        f"user:{user.pk}",
        f"super:{int(bool(user.is_superuser))}",
        f"staff:{int(bool(user.is_staff))}",
        f"fieldworker:{int(bool(user.is_fieldworker()))}",
        f"pii:{int(bool(getattr(user, 'can_view_pii', False)))}",
    ]
    location_ids = list(
        user.location_restrictions.order_by("pk").values_list("pk", flat=True)
    )
    parts.append("loc:" + ",".join(str(value) for value in location_ids))
    group_ids = list(user.groups.order_by("pk").values_list("pk", flat=True))
    parts.append("grp:" + ",".join(str(value) for value in group_ids))
    return "|".join(parts)


def _fastpath_cache_key(namespace, request, extra=None):
    preset = (request.GET.get("preset") or "all").strip().lower()
    raw_start = (request.GET.get("start") or "").strip()
    raw_end = (request.GET.get("end") or "").strip()
    if preset not in {"all", "30", "7", "24"}:
        preset = "all"
    start_token = raw_start
    end_token = raw_end
    if raw_start or raw_end:
        _preset, start_dt, end_dt, _has_custom = _parse_filter_range(request)
        start_token = start_dt.date().isoformat() if start_dt else ""
        end_token = end_dt.date().isoformat() if end_dt else ""

    location_level, location_value = _parse_location_filter(request)
    payload = {
        "version": get_home_dashboard_fastpath_version(),
        "scope": _scope_fingerprint(request.user),
        "preset": preset,
        "start": start_token,
        "end": end_token,
        "location_level": location_level,
        "location_value": location_value,
        "extra": extra or {},
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"home_fastpath:{namespace}:{digest}"


def _shallow_downsample(labels, series_map, max_points):
    try:
        max_points_int = int(max_points)
    except (TypeError, ValueError):
        max_points_int = 0

    if max_points_int <= 0 or len(labels) <= max_points_int:
        return labels, series_map

    step = max(1, len(labels) // max_points_int)
    sampled_idx = list(range(0, len(labels), step))
    if sampled_idx[-1] != len(labels) - 1:
        sampled_idx.append(len(labels) - 1)

    sampled_labels = [labels[idx] for idx in sampled_idx]
    sampled_series = {
        key: [values[idx] for idx in sampled_idx if idx < len(values)]
        for key, values in series_map.items()
    }
    return sampled_labels, sampled_series


def _paginate_list(items, page, page_size):
    total = len(items)
    try:
        page = int(page or 1)
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(page_size or 10)
    except (TypeError, ValueError):
        page_size = 10
    page = max(page, 1)
    page_size = max(min(page_size, 100), 1)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "results": items[start:end],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        },
    }


def _latest_household_timestamps(queryset):
    households_by_key = {}
    eas_by_key = {}
    rows = (
        queryset.exclude(key__isnull=True)
        .exclude(key="")
        .values("key", "ea", "submissiondate", "start", "today")
    )
    for row in rows.iterator():
        timestamp = (
            _parse_submission_timestamp(row.get("submissiondate"))
            or _parse_submission_timestamp(row.get("start"))
            or _parse_submission_timestamp(row.get("today"))
        )
        if timestamp is None:
            continue
        key_value = row.get("key")
        if key_value:
            existing_key_ts = households_by_key.get(key_value)
            if existing_key_ts is None or timestamp > existing_key_ts:
                households_by_key[key_value] = timestamp
        ea_value = row.get("ea")
        if ea_value:
            existing_ea_ts = eas_by_key.get(ea_value)
            if existing_ea_ts is None or timestamp > existing_ea_ts:
                eas_by_key[ea_value] = timestamp
    return households_by_key, eas_by_key


def _get_national_operational_view_data(
    start_dt=None,
    end_dt=None,
    request=None,
    location_level="national",
    location_value="",
    include_chart_series=True,
):
    if end_dt is None:
        end_dt = timezone.localtime(timezone.now())

    households_qs = _apply_text_geography_filter(
        restrict_queryset_to_user_locations(Household.objects.all(), getattr(request, "user", None)),
        Household,
        location_level,
        location_value,
    )
    pregnancies_qs = _apply_text_geography_filter(
        restrict_queryset_to_user_locations(Pregnancy.objects.all(), getattr(request, "user", None)),
        Pregnancy,
        location_level,
        location_value,
    )
    pregnancy_outcomes_qs = _apply_text_geography_filter(
        restrict_queryset_to_user_locations(
            PregnancyOutcome.objects.all(), getattr(request, "user", None)
        ),
        PregnancyOutcome,
        location_level,
        location_value,
    )
    deaths_qs = _apply_text_geography_filter(
        restrict_queryset_to_user_locations(Death.objects.all(), getattr(request, "user", None)),
        Death,
        location_level,
        location_value,
    )
    if request and getattr(request, "user", None):
        va_qs = request.user.verbal_autopsies().filter(deleted_at__isnull=True, duplicate=False)
    else:
        va_qs = VerbalAutopsy.objects.filter(deleted_at__isnull=True, duplicate=False)
    va_qs = _apply_text_geography_filter(
        va_qs,
        VerbalAutopsy,
        location_level,
        location_value,
    )

    with timed_block("home.nov.latest_timestamps.households", request=request):
        households_by_key, eas_by_key = _latest_household_timestamps(households_qs)
    with timed_block("home.nov.latest_timestamps.pregnancies", request=request):
        pregnancies_by_key = _latest_timestamps_by_identifier(
            pregnancies_qs,
            "key",
            ("submissiondate", "start", "today"),
        )
    with timed_block("home.nov.latest_timestamps.pregnancy_outcomes", request=request):
        pregnancy_outcomes_by_key = _latest_timestamps_by_identifier(
            pregnancy_outcomes_qs,
            "key",
            ("submissiondate", "start", "today"),
        )
    with timed_block("home.nov.latest_timestamps.deaths", request=request):
        deaths_by_key = _latest_timestamps_by_identifier(
            deaths_qs,
            "key",
            ("submissiondate", "start", "today"),
        )
    with timed_block("home.nov.latest_timestamps.vas", request=request):
        vas_by_key = _latest_timestamps_by_identifier(
            va_qs,
            "instanceid",
            ("submissiondate", "Id10012", "created"),
        )

    with timed_block("home.nov.windowed_counts", request=request):
        households_totals = _windowed_counts(list(households_by_key.values()), start_dt, end_dt)
        eas_totals = _windowed_counts(list(eas_by_key.values()), start_dt, end_dt)
        pregnancies_totals = _windowed_counts(list(pregnancies_by_key.values()), start_dt, end_dt)
        pregnancy_outcomes_totals = _windowed_counts(
            list(pregnancy_outcomes_by_key.values()), start_dt, end_dt
        )
        deaths_totals = _windowed_counts(list(deaths_by_key.values()), start_dt, end_dt)
        vas_totals = _windowed_counts(list(vas_by_key.values()), start_dt, end_dt)

    household_keys_total = {
        key
        for key, ts in households_by_key.items()
        if (start_dt is None or ts >= start_dt) and ts <= end_dt
    }
    household_keys_today = {
        key
        for key, ts in households_by_key.items()
        if ts <= end_dt
        and ts >= max(
            end_dt - timedelta(days=1),
            start_dt or datetime.min.replace(tzinfo=end_dt.tzinfo),
        )
    }
    household_keys_week = {
        key
        for key, ts in households_by_key.items()
        if ts <= end_dt
        and ts >= max(
            end_dt - timedelta(days=7),
            start_dt or datetime.min.replace(tzinfo=end_dt.tzinfo),
        )
    }

    with timed_block("home.nov.people_aggregate", request=request):
        people_total = (
            HouseholdMember.objects.filter(household__key__in=list(household_keys_total)).count()
            if household_keys_total
            else 0
        )
        people_today = (
            HouseholdMember.objects.filter(household__key__in=list(household_keys_today)).count()
            if household_keys_today
            else 0
        )
        people_week = (
            HouseholdMember.objects.filter(household__key__in=list(household_keys_week)).count()
            if household_keys_week
            else 0
        )

    if include_chart_series:
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
        chart_data = {"labels": [], "pregnancy": [], "pregnancy_outcome": [], "death": [], "va": []}

    kpis = {
        "eas": eas_totals,
        "households": households_totals,
        "people": {"today": people_today, "week": people_week, "total": people_total},
        "pregnancies": pregnancies_totals,
        "preg_outcomes": pregnancy_outcomes_totals,
        "deaths": deaths_totals,
        "vas": vas_totals,
    }

    return {
        "chart_labels": chart_data["labels"],
        "pregnancy_values": chart_data["pregnancy"],
        "pregnancy_outcome_values": chart_data["pregnancy_outcome"],
        "death_values": chart_data["death"],
        "va_values": chart_data["va"],
        "kpis": kpis,
    }


HOME_FASTPATH_CACHE_TTL_SECONDS = 10 * 60
HOME_FASTPATH_TABLE_CACHE_TTL_SECONDS = 5 * 60


def _get_cached_nov_payload(request):
    preset, start_dt, end_dt, has_custom_range = _parse_filter_range(request)
    location_level, location_value = _parse_location_filter(request)
    extra = {
        "component": "nov_bundle",
        "location_level": location_level,
        "location_value": location_value,
    }
    cache_key = _fastpath_cache_key("nov_bundle", request, extra=extra)
    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        return cached_payload

    payload = _get_national_operational_view_data(
        start_dt=start_dt,
        end_dt=end_dt,
        request=request,
        location_level=location_level,
        location_value=location_value,
        include_chart_series=True,
    )
    cache.set(cache_key, payload, timeout=HOME_FASTPATH_CACHE_TTL_SECONDS)
    return payload


def _get_cached_home_kpis(request):
    extra = {"component": "kpis"}
    cache_key = _fastpath_cache_key("home_dashboard_kpis", request, extra=extra)
    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        return cached_payload

    _preset, start_dt, end_dt, _has_custom_range = _parse_filter_range(request)
    location_level, location_value = _parse_location_filter(request)
    payload = _get_national_operational_view_data(
        start_dt=start_dt,
        end_dt=end_dt,
        request=request,
        location_level=location_level,
        location_value=location_value,
        include_chart_series=False,
    ).get("kpis", {})
    cache.set(cache_key, payload, timeout=HOME_FASTPATH_CACHE_TTL_SECONDS)
    return payload


def _get_cached_home_chart_bundle(request):
    extra = {"component": "overview_chart"}
    cache_key = _fastpath_cache_key("home_dashboard_chart_bundle", request, extra=extra)
    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        return cached_payload

    nov_payload = _get_cached_nov_payload(request)
    chart_payload = {
        "labels": nov_payload.get("chart_labels", []),
        "pregnancy": nov_payload.get("pregnancy_values", []),
        "pregnancy_outcome": nov_payload.get("pregnancy_outcome_values", []),
        "death": nov_payload.get("death_values", []),
        "va": nov_payload.get("va_values", []),
    }
    cache.set(cache_key, chart_payload, timeout=HOME_FASTPATH_CACHE_TTL_SECONDS)
    return chart_payload


def _get_cached_trends_bundle(request, *, include_va=True, include_model=True):
    cache_key = _fastpath_cache_key(
        "home_trends_bundle",
        request,
        extra={
            "component": "trends",
            "schema_version": 2,
            "include_va": int(include_va),
            "include_model": int(include_model),
        },
    )
    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        return cached_payload

    va_table = {}
    graphs = {}
    issue_list = []
    indeterminate_cod_list = []
    additional_issues = 0
    additional_indeterminate_cods = 0
    model_trends = {}

    if include_va:
        with timed_block("home.trends.va_trends_data", request=request):
            (
                va_table,
                graphs,
                issue_list,
                indeterminate_cod_list,
                additional_issues,
                additional_indeterminate_cods,
            ) = get_trends_data(request.user)

    if include_model:
        with timed_block("home.trends.model_trends_data", request=request):
            model_trends = get_model_trends_data(request.user)

    payload = {
        "vaTable": va_table,
        "graphs": graphs,
        "issueList": issue_list,
        "indeterminateCodList": indeterminate_cod_list,
        "additionalIssues": additional_issues,
        "additionalIndeterminateCods": additional_indeterminate_cods,
        "isFieldWorker": request.user.is_fieldworker(),
        "modelTrends": model_trends,
    }
    cache.set(cache_key, payload, timeout=HOME_FASTPATH_TABLE_CACHE_TTL_SECONDS)
    return payload


class Index(CustomAuthMixin, TemplateView):
    template_name = "home/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context


class Trends(CustomAuthMixin, View):
    def get(self, request, *args, **kwargs):
        section = (request.GET.get("section") or "all").strip().lower()
        include_va = section in {"all", "va"}
        include_model = section in {"all", "model"}
        if section not in {"all", "va", "model"}:
            return JsonResponse({"detail": "Unsupported trends section."}, status=400)
        return JsonResponse(
            _get_cached_trends_bundle(
                request,
                include_va=include_va,
                include_model=include_model,
            )
        )


trends_endpoint_view = Trends.as_view()


class NationalOperationalFilterData(CustomAuthMixin, View):
    def get(self, request, *args, **kwargs):
        if str(request.GET.get("kpis_only", "")).strip() in {"1", "true", "True"}:
            with timed_block("home.nov.filter_payload.kpis_only", request=request):
                payload = {"kpis": _get_cached_home_kpis(request)}
            return JsonResponse(payload)
        with timed_block("home.nov.filter_payload", request=request):
            payload = _get_cached_nov_payload(request)
        return JsonResponse(payload)


national_operational_filter_data_view = NationalOperationalFilterData.as_view()


class HomeOverviewMapData(CustomAuthMixin, View):
    def get(self, request, *args, **kwargs):
        cache_key = _fastpath_cache_key(
            "home_overview_map_data",
            request,
            extra={"component": "overview_map", "schema_version": 2},
        )
        payload = cache.get(cache_key)
        if payload is None:
            payload = _build_home_combined_map_payload(request)
            cache.set(cache_key, payload, timeout=HOME_FASTPATH_CACHE_TTL_SECONDS)
        return JsonResponse(payload)


home_overview_map_data_view = HomeOverviewMapData.as_view()


class HomeDashboardKpisAPIView(CustomAuthMixin, PermissionRequiredMixin, View):
    permission_required = "va_analytics.view_dashboard"

    def get(self, request, *args, **kwargs):
        payload = _get_cached_home_kpis(request)
        return JsonResponse({"kpis": payload})


home_dashboard_kpis_api_view = HomeDashboardKpisAPIView.as_view()


class HomeDashboardTabChartAPIView(CustomAuthMixin, PermissionRequiredMixin, View):
    permission_required = "va_analytics.view_dashboard"

    def get(self, request, tab, chart, *args, **kwargs):
        tab_key = (tab or "").strip().lower()
        chart_key = (chart or "").strip().lower()
        max_points = request.GET.get("max_points")
        response_cache_key = _fastpath_cache_key(
            "home_dashboard_tab_chart_response",
            request,
            extra={"tab": tab_key, "chart": chart_key, "max_points": max_points or ""},
        )
        cached_response = cache.get(response_cache_key)
        if cached_response is not None:
            return JsonResponse(cached_response)

        if tab_key == "overview" and chart_key == "events":
            chart_payload = _get_cached_home_chart_bundle(request)
            labels, series = _shallow_downsample(
                chart_payload.get("labels", []),
                {
                    "pregnancy": chart_payload.get("pregnancy", []),
                    "pregnancy_outcome": chart_payload.get("pregnancy_outcome", []),
                    "death": chart_payload.get("death", []),
                    "va": chart_payload.get("va", []),
                },
                max_points,
            )
            payload = {"labels": labels, **series}
            cache.set(
                response_cache_key,
                payload,
                timeout=HOME_FASTPATH_CACHE_TTL_SECONDS,
            )
            return JsonResponse(payload)

        if tab_key == "trends":
            trends_payload = _get_cached_trends_bundle(
                request,
                include_va=False,
                include_model=True,
            )
            metric_map = {
                "households": "households",
                "pregnancies": "pregnancies",
                "pregnancy_outcomes": "pregnancy_outcomes",
                "deaths": "deaths",
            }
            metric = metric_map.get(chart_key)
            if not metric:
                return JsonResponse({"detail": "Unsupported trends chart."}, status=400)
            graphs = (
                trends_payload.get("modelTrends", {})
                .get(metric, {})
                .get("graphs", {})
                .get("recorded", {})
            )
            labels, series = _shallow_downsample(
                graphs.get("x", []),
                {"y": graphs.get("y", [])},
                max_points,
            )
            payload = {"x": labels, "y": series.get("y", [])}
            cache.set(
                response_cache_key,
                payload,
                timeout=HOME_FASTPATH_TABLE_CACHE_TTL_SECONDS,
            )
            return JsonResponse(payload)

        if tab_key == "va_statistics":
            trends_payload = _get_cached_trends_bundle(
                request,
                include_va=True,
                include_model=False,
            )
            graph_map = {
                "interviewed": "collected",
                "coded": "coded",
                "uncoded": "uncoded",
            }
            graph_key = graph_map.get(chart_key)
            if not graph_key:
                return JsonResponse({"detail": "Unsupported va_statistics chart."}, status=400)
            graph = trends_payload.get("graphs", {}).get(graph_key, {})
            labels, series = _shallow_downsample(
                graph.get("x", []),
                {"y": graph.get("y", [])},
                max_points,
            )
            payload = {"x": labels, "y": series.get("y", [])}
            cache.set(
                response_cache_key,
                payload,
                timeout=HOME_FASTPATH_TABLE_CACHE_TTL_SECONDS,
            )
            return JsonResponse(payload)

        return JsonResponse({"detail": "Unsupported tab/chart combination."}, status=400)


home_dashboard_tab_chart_api_view = HomeDashboardTabChartAPIView.as_view()


class HomeDashboardTabTableAPIView(CustomAuthMixin, PermissionRequiredMixin, View):
    permission_required = "va_analytics.view_dashboard"

    def get(self, request, tab, table, *args, **kwargs):
        tab_key = (tab or "").strip().lower()
        table_key = (table or "").strip().lower()
        page = request.GET.get("page", "1")
        page_size = request.GET.get("page_size", "10")
        response_cache_key = _fastpath_cache_key(
            "home_dashboard_tab_table_response",
            request,
            extra={
                "tab": tab_key,
                "table": table_key,
                "page": str(page),
                "page_size": str(page_size),
            },
        )
        cached_response = cache.get(response_cache_key)
        if cached_response is not None:
            return JsonResponse(cached_response)

        if tab_key == "overview" and table_key == "kpis":
            payload = {"kpis": _get_cached_home_kpis(request)}
            cache.set(
                response_cache_key,
                payload,
                timeout=HOME_FASTPATH_CACHE_TTL_SECONDS,
            )
            return JsonResponse(payload)

        if tab_key == "trends":
            trends_payload = _get_cached_trends_bundle(
                request,
                include_va=False,
                include_model=True,
            )
            metric_map = {
                "households": "households",
                "pregnancies": "pregnancies",
                "pregnancy_outcomes": "pregnancy_outcomes",
                "deaths": "deaths",
            }
            metric = metric_map.get(table_key)
            if not metric:
                return JsonResponse({"detail": "Unsupported trends table."}, status=400)
            table_payload = (
                trends_payload.get("modelTrends", {})
                .get(metric, {})
                .get("table", {})
            )
            payload = {"table": table_payload}
            cache.set(
                response_cache_key,
                payload,
                timeout=HOME_FASTPATH_TABLE_CACHE_TTL_SECONDS,
            )
            return JsonResponse(payload)

        if tab_key == "va_statistics":
            trends_payload = _get_cached_trends_bundle(
                request,
                include_va=True,
                include_model=False,
            )
            if table_key == "summary":
                payload = {"vaTable": trends_payload.get("vaTable", {})}
                cache.set(
                    response_cache_key,
                    payload,
                    timeout=HOME_FASTPATH_TABLE_CACHE_TTL_SECONDS,
                )
                return JsonResponse(payload)
            if table_key == "issues":
                rows = trends_payload.get("issueList", [])
                paged = _paginate_list(rows, page=page, page_size=page_size)
                paged["additional"] = trends_payload.get("additionalIssues", 0)
                cache.set(
                    response_cache_key,
                    paged,
                    timeout=HOME_FASTPATH_TABLE_CACHE_TTL_SECONDS,
                )
                return JsonResponse(paged)
            if table_key == "indeterminate":
                rows = trends_payload.get("indeterminateCodList", [])
                paged = _paginate_list(rows, page=page, page_size=page_size)
                paged["additional"] = trends_payload.get("additionalIndeterminateCods", 0)
                cache.set(
                    response_cache_key,
                    paged,
                    timeout=HOME_FASTPATH_TABLE_CACHE_TTL_SECONDS,
                )
                return JsonResponse(paged)
            return JsonResponse({"detail": "Unsupported va_statistics table."}, status=400)

        return JsonResponse({"detail": "Unsupported tab/table combination."}, status=400)


home_dashboard_tab_table_api_view = HomeDashboardTabTableAPIView.as_view()


class HomeOverviewEventsComponent(CustomAuthMixin, TemplateView):
    template_name = "home/components/_home_overview_events_component.html"


home_overview_events_component_view = HomeOverviewEventsComponent.as_view()


class HomeOverviewKpisComponent(CustomAuthMixin, TemplateView):
    template_name = "home/components/_home_overview_kpis_component.html"


home_overview_kpis_component_view = HomeOverviewKpisComponent.as_view()


class HomeTrendsComponent(CustomAuthMixin, TemplateView):
    template_name = "home/components/_home_trends_component.html"


home_trends_component_view = HomeTrendsComponent.as_view()


class HomeVaStatisticsComponent(CustomAuthMixin, TemplateView):
    template_name = "home/components/_home_va_statistics_component.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        with timed_block("home.va_statistics.summary_stats", request=self.request):
            context.update(get_va_summary_stats(user.verbal_autopsies()))
        context["locations"] = "All Regions"
        if user.location_restrictions.count() > 0:
            context["locations"] = ", ".join(
                [location.name for location in user.location_restrictions.all()]
            )
        return context


home_va_statistics_component_view = HomeVaStatisticsComponent.as_view()


class About(CustomAuthMixin, TemplateView):
    template_name = "home/about.html"


class RegionalOperationsComponentContextMixin:
    INDETERMINATE_LABEL = "Indeterminate"
    geography_options = (
        {"value": "national", "label": "National"},
        {"value": "lusaka", "label": "Lusaka"},
        {"value": "southern", "label": "Southern"},
    )
    time_presets = (
        {"id": "timeAll", "label": "All time"},
        {"id": "time30", "label": "Last 30 days"},
        {"id": "time7", "label": "Last 7 days"},
        {"id": "time24", "label": "Last 24 hours"},
    )
    mso_source_options = (
        {"value": "community", "label": "Community"},
        {"value": "facility", "label": "Facility"},
    )
    csa_column_map = (
        ("name", "CSA Name (Search)"),
        ("district", "District"),
        ("ward", "Ward"),
        ("visits", "Visits"),
        ("events", "Events"),
        ("deaths", "Deaths"),
        ("pregnancies", "Pregnancies"),
        ("pregnancy_outcomes", "Pregnancy Outcomes"),
        (
            "overdue_without_interview",
            "Pregnancies ≥ 6 mo. Past Due Date Without Interview",
        ),
    )
    mso_column_map = (
        ("name", "MSO Name (Search)"),
        ("province", "Province"),
        ("death_events", "Death Events"),
        ("va_scheduled", "VAs Scheduled"),
        ("va_not_complete", "VA Scheduled but Not Complete"),
        ("mean_death_to_va_complete", "Mean Days (Death→Complete)"),
        ("va_total", "VA"),
        ("valid_cod", "Valid COD"),
        ("indeterminate", "Indeterminate"),
        ("error", "Error"),
        ("duration_outliers", "≤15 or ≥90m"),
    )
    geo_filter_values = {"", "national", "lusaka", "southern"}
    geo_filter_map = {
        "lusaka": {
            "names": ("lusaka", "lusaka province"),
            "codes": ("5", "05"),
        },
        "southern": {
            "names": ("southern", "southern province"),
            "codes": ("9", "09"),
        },
    }
    preset_to_ui_id = {
        "all": "timeAll",
        "30": "time30",
        "7": "time7",
        "24": "time24",
    }
    ui_id_to_preset = {v: k for k, v in preset_to_ui_id.items()}

    @staticmethod
    def _pick(value, valid_values, default):
        return value if value in valid_values else default

    @staticmethod
    def _to_int(value):
        try:
            return int(str(value).strip() or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _coerce_date(value):
        if isinstance(value, date):
            return value
        parsed = parse_date(str(value or ""))
        if not parsed or parsed == "dk":
            return None
        return datetime.strptime(parsed, "%Y-%m-%d").date()

    @staticmethod
    def _safe_days(start_date, end_date):
        if not start_date or not end_date:
            return None
        return (end_date - start_date).days

    @staticmethod
    def _mean(values):
        if not values:
            return 0
        return round(sum(values) / len(values), 1)

    @staticmethod
    def _staff_name(user_obj, fallback=""):
        if user_obj is None:
            return fallback or "Unassigned MSO"
        return (
            user_obj.name
            or user_obj.get_full_name()
            or user_obj.email
            or fallback
            or "Unassigned MSO"
        )

    @staticmethod
    def _mso_name_from_va_row(va_row):
        raw_name = (
            (va_row.get("Id10010") or "").strip()
            or (va_row.get("location__name") or "").strip()
        )
        return RegionalOperationsComponentContextMixin._format_mso_display_name(raw_name)

    @staticmethod
    def normalize_person_name(value):
        text = (value or "").strip().replace("_", " ")
        text = re.sub(r"[^\w\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip().lower()
        return text

    @staticmethod
    def _best_match_name_norm(query_norm, candidate_norms, threshold=85):
        query = (query_norm or "").strip()
        if not query or not candidate_norms:
            return None

        try:
            from rapidfuzz import fuzz, process

            best = process.extractOne(query, candidate_norms, scorer=fuzz.ratio)
            if not best:
                return None
            matched_norm, score, _ = best
            return matched_norm if score >= threshold else None
        except Exception:
            best_score = 0
            best_match = None
            for candidate in candidate_norms:
                score = difflib.SequenceMatcher(None, query, candidate).ratio() * 100
                if score > best_score:
                    best_score = score
                    best_match = candidate
            return best_match if best_score >= threshold else None

    @staticmethod
    def _best_match_name_norm_with_score(query_norm, candidate_norms, threshold=85):
        query = (query_norm or "").strip()
        if not query or not candidate_norms:
            return (None, None, 0.0)

        try:
            from rapidfuzz import fuzz, process

            best = process.extractOne(query, candidate_norms, scorer=fuzz.ratio)
            if not best:
                return (None, None, 0.0)
            best_norm, score, _ = best
            matched = best_norm if score >= threshold else None
            return (matched, best_norm, float(score))
        except Exception:
            best_score = 0.0
            best_norm = None
            for candidate in candidate_norms:
                score = difflib.SequenceMatcher(None, query, candidate).ratio() * 100
                if score > best_score:
                    best_score = score
                    best_norm = candidate
            matched = best_norm if best_score >= threshold else None
            return (matched, best_norm, float(best_score))

    @staticmethod
    def _format_csa_display_name(raw_name):
        name = (raw_name or "").strip().replace("_", " ")
        # Drop any trailing numeric sequence, including digits attached to text.
        name = re.sub(r"\d+$", "", name).strip()
        return name or "Unassigned CSA"

    @staticmethod
    def _format_mso_display_name(raw_name):
        name = (raw_name or "").strip().replace("_", " ")
        # Drop trailing numeric token after the last space.
        name = re.sub(r"\s+\d+$", "", name).strip()
        return name or "Unassigned MSO"

    @staticmethod
    def _normalize_name_search_text(value):
        text = (value or "").strip().replace("_", " ")
        text = re.sub(r"\s+\d+$", "", text).strip().lower()
        text = re.sub(r"\s+", " ", text)
        return text

    @staticmethod
    def _normalize_choice_lookup_value(value):
        text = str(value or "").strip()
        if re.fullmatch(r"\d+", text):
            return str(int(text))
        return text

    def _get_choice_label_map(self, field_name):
        cache = getattr(self, "_choice_label_map_cache", None)
        if cache is None:
            cache = {}
            self._choice_label_map_cache = cache
        if field_name in cache:
            return cache[field_name]

        lookup = {}
        choice_rows = (
            ODKFormChoice.objects.filter(field_name=field_name)
            .order_by("id")
            .values("value", "label")
        )
        for row in choice_rows:
            key = self._normalize_choice_lookup_value(row.get("value"))
            if key and key not in lookup:
                # Keep the first label match by insertion order.
                lookup[key] = (row.get("label") or "").strip()
        cache[field_name] = lookup
        return lookup

    def _resolve_choice_label(self, field_name, raw_value):
        text_value = str(raw_value or "").strip()
        if not text_value:
            return "—"
        lookup = self._get_choice_label_map(field_name)
        normalized = self._normalize_choice_lookup_value(text_value)
        label = lookup.get(normalized)
        if label:
            return label
        return text_value.replace("_", " ")

    def _matches_name_search(self, name_value, raw_query):
        query = self._normalize_name_search_text(raw_query)
        if not query:
            return True

        name = self._normalize_name_search_text(name_value)
        if not name:
            return False

        if query in name:
            return True

        if all(token in name for token in query.split()):
            return True

        similarity = difflib.SequenceMatcher(None, query, name).ratio()
        if similarity >= 0.62:
            return True

        return any(
            difflib.SequenceMatcher(None, query, token).ratio() >= 0.8
            for token in name.split()
        )

    def _normalized_geo(self):
        geo = (self.request.GET.get("geo") or self.request.GET.get("geography") or "").strip().lower()
        return geo if geo in self.geo_filter_values else ""

    def _normalized_preset(self):
        preset_raw = (self.request.GET.get("time_preset") or "all").strip().lower()
        if preset_raw in self.ui_id_to_preset:
            return self.ui_id_to_preset[preset_raw]
        return preset_raw if preset_raw in {"all", "30", "7", "24"} else "all"

    def _resolve_time_window(self):
        selected_preset = self._normalized_preset()
        start_value = (self.request.GET.get("start_date") or "").strip()
        end_value = (self.request.GET.get("end_date") or "").strip()

        start_date = self._coerce_date(start_value)
        end_date = self._coerce_date(end_value)
        manual_valid = bool(start_date and end_date and end_date >= start_date)

        now_local = timezone.now().astimezone(ZoneInfo("Africa/Lusaka"))

        if manual_valid:
            start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=ZoneInfo("Africa/Lusaka"))
            end_dt = datetime.combine(end_date, datetime.max.time(), tzinfo=ZoneInfo("Africa/Lusaka"))
            time_label = f"{start_date.isoformat()} to {end_date.isoformat()}"
            return {
                "selected_preset": selected_preset,
                "selected_time_preset": self.preset_to_ui_id[selected_preset],
                "start_value": start_value,
                "end_value": end_value,
                "start_dt": start_dt,
                "end_dt": end_dt,
                "start_date_str": start_date.isoformat(),
                "end_date_str": end_date.isoformat(),
                "time_label": time_label,
            }

        if selected_preset == "30":
            start_dt = now_local - timedelta(days=30)
            end_dt = now_local
            time_label = "Last 30 days"
        elif selected_preset == "7":
            start_dt = now_local - timedelta(days=7)
            end_dt = now_local
            time_label = "Last 7 days"
        elif selected_preset == "24":
            start_dt = now_local - timedelta(hours=24)
            end_dt = now_local
            time_label = "Last 24 hours"
        else:
            start_dt = None
            end_dt = None
            time_label = "All time"

        return {
            "selected_preset": selected_preset,
            "selected_time_preset": self.preset_to_ui_id[selected_preset],
            "start_value": start_value,
            "end_value": end_value,
            "start_dt": start_dt,
            "end_dt": end_dt,
            "start_date_str": start_dt.date().isoformat() if start_dt else "",
            "end_date_str": end_dt.date().isoformat() if end_dt else "",
            "time_label": time_label,
        }

    @staticmethod
    def _province_filter_q(field_name, geo_value):
        if not geo_value or geo_value == "national":
            return Q()
        meta = RegionalOperationsComponentContextMixin.geo_filter_map.get(geo_value)
        if not meta:
            return Q()

        query = Q()
        for name in meta["names"]:
            query |= Q(**{f"{field_name}__iexact": name})
        for code in meta["codes"]:
            query |= Q(**{f"{field_name}__exact": code})
        return query

    @staticmethod
    def _normalize_province_name(value):
        text = str(value or "").strip().lower()
        text = re.sub(r"\s+", " ", text)
        if text.endswith(" province"):
            text = text[: -len(" province")].strip()
        return text

    def _geo_match_meta(self, geo_value):
        if not geo_value or geo_value == "national":
            return None
        raw_meta = self.geo_filter_map.get(geo_value)
        if not raw_meta:
            return None

        names = {
            self._normalize_province_name(name)
            for name in raw_meta.get("names", ())
            if self._normalize_province_name(name)
        }
        codes = {
            self._normalize_choice_lookup_value(code)
            for code in raw_meta.get("codes", ())
            if str(code or "").strip()
        }
        province_lookup = self._get_choice_label_map("province")
        normalized_lookup = {
            self._normalize_choice_lookup_value(key): (value or "").strip()
            for key, value in province_lookup.items()
        }

        for code in list(codes):
            label = normalized_lookup.get(code, "")
            if label:
                names.add(self._normalize_province_name(label))

        for code, label in normalized_lookup.items():
            if self._normalize_province_name(label) in names:
                codes.add(code)

        return {"names": names, "codes": codes}

    def _matches_geo_by_province(self, province_value, geo_value):
        if not geo_value or geo_value == "national":
            return True

        meta = self._geo_match_meta(geo_value)
        if not meta:
            return True

        province_text = str(province_value or "").strip()
        if not province_text:
            return False

        resolved_label = self._resolve_choice_label("province", province_text)

        if self._normalize_province_name(province_text) in meta["names"]:
            return True

        if self._normalize_province_name(resolved_label) in meta["names"]:
            return True

        return self._normalize_choice_lookup_value(province_text) in meta["codes"]

    def _allowed_province_names(self):
        restrictions = list(self.request.user.location_restrictions.all())
        if not restrictions:
            return None

        provinces = set()
        for location in restrictions:
            if getattr(location, "depth", None) == 2:
                provinces.add(location.name.strip().lower())

            for ancestor in location.get_ancestors():
                if getattr(ancestor, "depth", None) == 2:
                    provinces.add(ancestor.name.strip().lower())

            for name in location.get_descendants().filter(depth=2).values_list("name", flat=True):
                if name:
                    provinces.add(name.strip().lower())
        return provinces

    @staticmethod
    def _province_access_q(field_name, province_names):
        if province_names is None:
            return Q()

        query = Q()
        for province_name in province_names:
            query |= Q(**{f"{field_name}__iexact": province_name})
            # CSA/Event rows may omit "Province" suffix.
            if province_name.endswith(" province"):
                query |= Q(
                    **{
                        f"{field_name}__iexact": province_name.replace(
                            " province", ""
                        ).strip()
                    }
                )
        return query

    @staticmethod
    def _sort_rows(rows, sort_key, sort_dir):
        reverse = sort_dir == "desc"
        if sort_key == "name":
            return sorted(rows, key=lambda r: str(r.get("name", "")).lower(), reverse=reverse)
        if sort_key in {"district", "ward", "province"}:
            return sorted(rows, key=lambda r: str(r.get(sort_key, "")).lower(), reverse=reverse)
        def numeric_key(row):
            value = row.get(sort_key, 0)
            if value in (None, ""):
                return 0
            try:
                if pd.isna(value):
                    return 0
            except TypeError:
                pass
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0

        return sorted(rows, key=numeric_key, reverse=reverse)

    @staticmethod
    def _paginate_rows(rows, page, per_page=5):
        total = len(rows)
        total_pages = max(1, (total + per_page - 1) // per_page)
        current_page = min(max(1, page), total_pages)
        start = (current_page - 1) * per_page
        end = start + per_page
        return {
            "rows": rows[start:end],
            "page": current_page,
            "total_pages": total_pages,
            "has_prev": current_page > 1,
            "has_next": current_page < total_pages,
            "prev_page": current_page - 1 if current_page > 1 else 1,
            "next_page": current_page + 1 if current_page < total_pages else total_pages,
            "visible_count": len(rows[start:end]),
            "total_count": total,
        }

    def _build_csa_rows(self, selected_geo, start_date_str, end_date_str, province_scope):
        counts_by_csa = defaultdict(
            lambda: {
                "name": "",
                "province": "—",
                "district": "",
                "ward": "",
                "visits": 0,
                "events": 0,
                "deaths": 0,
                "pregnancies": 0,
                "pregnancy_outcomes": 0,
                "overdue_without_interview": 0,
            }
        )
        tracker_enumerator_names = set()
        tracker_rows = CSADailyTracker.objects.filter(
            self._province_access_q("province", province_scope)
        ).filter(self._province_filter_q("province", selected_geo))

        if start_date_str and end_date_str:
            tracker_rows = tracker_rows.filter(
                today__gte=start_date_str,
                today__lte=end_date_str,
            )

        tracker_rows = tracker_rows.values(
            "enumerator",
            "province",
            "district",
            "ward",
            "today",
            "num_death",
            "num_preg",
            "num_outcome",
        )

        for row in tracker_rows:
            name = self._format_csa_display_name(row.get("enumerator"))
            province = self._resolve_choice_label("province", row.get("province"))
            district = self._resolve_choice_label("district", row.get("district"))
            ward = self._resolve_choice_label("ward", row.get("ward"))
            group_key = (name, province, district, ward)
            entry = counts_by_csa[group_key]
            entry["name"] = name
            entry["province"] = province
            entry["district"] = district
            entry["ward"] = ward
            tracker_enumerator_names.add(name)
            entry["visits"] += 1
            deaths = self._to_int(row.get("num_death"))
            pregnancies = self._to_int(row.get("num_preg"))
            outcomes = self._to_int(row.get("num_outcome"))
            entry["deaths"] += deaths
            entry["pregnancies"] += pregnancies
            entry["pregnancy_outcomes"] += outcomes
            entry["events"] += deaths + pregnancies + outcomes

        overdue_cutoff = datetime.today().date() - timedelta(days=180)
        outcome_pregnancy_ids = set(
            PregnancyOutcome.objects.exclude(pregnancy_id__isnull=True)
            .exclude(pregnancy_id__exact="")
            .values_list("pregnancy_id", flat=True)
        )

        pregnancy_rows = Pregnancy.objects.filter(
            self._province_access_q("province", province_scope)
        ).filter(self._province_filter_q("province", selected_geo))
        pregnancy_rows = pregnancy_rows.filter(PE_10A__lte=overdue_cutoff.isoformat())
        if start_date_str and end_date_str:
            pregnancy_rows = pregnancy_rows.filter(
                PE_10A__gte=start_date_str,
                PE_10A__lte=end_date_str,
            )

        for row in pregnancy_rows.values(
            "key",
            "enumerator",
            "province",
            "district",
            "ward",
            "PE_10A",
        ):
            due_date = self._coerce_date(row.get("PE_10A"))
            if not due_date or due_date > overdue_cutoff:
                continue

            key = row.get("key")
            if key and key in outcome_pregnancy_ids:
                continue

            name = self._format_csa_display_name(row.get("enumerator"))
            if name not in tracker_enumerator_names:
                continue
            province = self._resolve_choice_label("province", row.get("province"))
            district = self._resolve_choice_label("district", row.get("district"))
            ward = self._resolve_choice_label("ward", row.get("ward"))
            group_key = (name, province, district, ward)
            entry = counts_by_csa[group_key]
            entry["name"] = name
            entry["province"] = province
            entry["district"] = district
            entry["ward"] = ward
            entry["overdue_without_interview"] += 1

        return list(counts_by_csa.values())

    def _build_mso_rows(
        self,
        selected_geo,
        selected_mso_source,
        va_queryset,
        province_scope,
        start_date_str="",
        end_date_str="",
    ):
        stats_by_mso = defaultdict(
            lambda: {
                "name": "",
                "province": "—",
                "death_events": 0,
                "va_scheduled": 0,
                "va_not_complete": 0,
                "mean_death_to_va_complete": 0,
                "va_total": 0,
                "valid_cod": 0,
                "indeterminate": 0,
                "error": 0,
                "duration_outliers": 0,
                "_death_to_complete_days": [],
            }
        )

        # Base MSO population must come from scoped VA dataset so rows render whenever VAs exist.
        va_to_mso_name = {}
        base_va_records = []
        for va_row in va_queryset.values(
            "id",
            "Id10010",
            "location__name",
            "province_name",
            "province",
        ):
            mso_name = self._mso_name_from_va_row(va_row)
            mso_name_norm = self.normalize_person_name(mso_name)
            province_value = (
                va_row.get("province")
                if va_row.get("province") not in (None, "")
                else va_row.get("province_name")
            )
            province_label = self._resolve_choice_label("province", province_value)
            va_to_mso_name[va_row["id"]] = mso_name
            base_va_records.append(
                {
                    "name": mso_name,
                    "province": province_label,
                    "mso_name_norm": mso_name_norm,
                    "va_id": va_row["id"],
                    "location_name": va_row.get("location__name"),
                    "province_name": va_row.get("province_name"),
                }
            )

        if base_va_records:
            df_base_va = pd.DataFrame.from_records(base_va_records)
            df_va_totals = (
                df_base_va.groupby(["name", "mso_name_norm"], as_index=False)
                .agg(
                    va_total=("va_id", "count"),
                    province=("province", "first"),
                )
            )
        else:
            df_base_va = pd.DataFrame(
                columns=[
                    "name",
                    "province",
                    "mso_name_norm",
                    "va_id",
                    "location_name",
                    "province_name",
                ]
            )
            df_va_totals = pd.DataFrame(
                columns=["name", "province", "mso_name_norm", "va_total"]
            )

        df_mso = df_va_totals.copy()
        mso_names = sorted(df_mso["name"].dropna().unique().tolist())
        df_mso["mso_name_norm"] = df_mso["name"].apply(self.normalize_person_name)
        mso_name_norm_to_name = {
            row["mso_name_norm"]: row["name"]
            for _, row in df_mso[["name", "mso_name_norm"]].drop_duplicates().iterrows()
            if row["mso_name_norm"]
        }
        mso_name_norms = sorted(mso_name_norm_to_name.keys())
        for row in df_mso.to_dict("records"):
            mso_name = row["name"]
            entry = stats_by_mso[mso_name]
            entry["name"] = mso_name
            entry["province"] = row.get("province") or "—"
            entry["va_total"] = int(row.get("va_total", 0))
        unmatched_agg_logs = []

        scoped_va_queryset = (
            va_queryset.select_related("location").prefetch_related("causes", "coding_issues")
        )
        va_metric_records = []
        for va in scoped_va_queryset:
            mso_name = va_to_mso_name.get(va.id) or self._mso_name_from_va_row(
                {
                    "Id10010": getattr(va, "Id10010", None),
                    "location__name": (
                        va.location.name
                        if getattr(va, "location", None) is not None
                        else None
                    ),
                }
            )
            causes = [cause.cause for cause in va.causes.all()]
            valid_cod = int(
                any(
                    (cause or "").strip()
                    and cause != self.INDETERMINATE_LABEL
                    for cause in causes
                )
            )
            indeterminate = int(
                any(cause == self.INDETERMINATE_LABEL for cause in causes)
            )
            error = int(
                any(issue.severity == "error" for issue in va.coding_issues.all())
            )
            va_metric_records.append(
                {
                    "name": mso_name,
                    "mso_name_norm": self.normalize_person_name(mso_name),
                    "valid_cod": valid_cod,
                    "indeterminate": indeterminate,
                    "error": error,
                }
            )

        if va_metric_records:
            df_va_metrics = (
                pd.DataFrame.from_records(va_metric_records)
                .groupby(["name", "mso_name_norm"], as_index=False)[
                    ["valid_cod", "indeterminate", "error"]
                ]
                .sum()
            )
            df_mso = df_mso.merge(df_va_metrics, on=["name", "mso_name_norm"], how="outer")
            for col in ("va_total", "valid_cod", "indeterminate", "error"):
                if col not in df_mso:
                    df_mso[col] = 0
                df_mso[col] = df_mso[col].fillna(0).astype(int)
            for row in df_mso.to_dict("records"):
                mso_name = row.get("name")
                if not mso_name:
                    continue
                entry = stats_by_mso[mso_name]
                entry["name"] = mso_name
                entry["province"] = row.get("province") or "—"
                entry["va_total"] = int(row.get("va_total", 0))
                entry["valid_cod"] = int(row.get("valid_cod", 0))
                entry["indeterminate"] = int(row.get("indeterminate", 0))
                entry["error"] = int(row.get("error", 0))

        death_records = []
        start_date_obj = self._coerce_date(start_date_str)
        end_date_obj = self._coerce_date(end_date_str)
        death_rows = Event.objects.filter(
            self._province_access_q("province", province_scope)
        ).filter(self._province_filter_q("province", selected_geo))
        death_rows = death_rows.filter(death__isnull=False, va__isnull=False)
        for death in death_rows.values("death_id", "death__DE_06", "va__Id10010"):
            death_date = self._coerce_date(death.get("death__DE_06"))
            if not death_date:
                continue
            if start_date_obj and death_date < start_date_obj:
                continue
            if end_date_obj and death_date > end_date_obj:
                continue
            death_name_raw = (death.get("va__Id10010") or "").strip()
            if not death_name_raw:
                continue
            death_name = self._format_mso_display_name(death_name_raw)
            death_records.append(
                {
                    "agg_name": death_name,
                    "agg_name_norm": self.normalize_person_name(death_name),
                    "death_id": death.get("death_id"),
                    "death_events": 1,
                }
            )

        if death_records:
            df_death = pd.DataFrame.from_records(death_records)
            df_death = df_death.dropna(subset=["death_id"])
            if not df_death.empty:
                df_death = df_death.drop_duplicates(subset=["agg_name", "death_id"])
                df_death[["matched_norm", "best_norm", "best_score"]] = df_death[
                    "agg_name_norm"
                ].apply(
                    lambda value: pd.Series(
                        self._best_match_name_norm_with_score(value, mso_name_norms, 85)
                    )
                )
                unmatched_agg_logs.extend(
                    [
                        {
                            "source": "death_events",
                            "agg_name": row.get("agg_name"),
                            "best_match": mso_name_norm_to_name.get(row.get("best_norm"), row.get("best_norm")),
                            "score": row.get("best_score", 0.0),
                        }
                        for _, row in df_death[df_death["matched_norm"].isna()].iterrows()
                    ]
                )
                df_death = df_death[df_death["matched_norm"].notna()]
                df_death["name"] = df_death["matched_norm"].map(mso_name_norm_to_name)
                df_death = df_death[df_death["name"].notna()]
                df_death = (
                    df_death.groupby("name", as_index=False)["death_events"]
                    .sum()
                )
                df_mso = df_mso.merge(df_death, on="name", how="left")
                if "death_events" not in df_mso:
                    df_mso["death_events"] = 0
                df_mso["death_events"] = df_mso["death_events"].fillna(0).astype(int)
                for row in df_mso.to_dict("records"):
                    mso_name = row.get("name")
                    if not mso_name:
                        continue
                    entry = stats_by_mso[mso_name]
                    entry["name"] = mso_name
                    entry["death_events"] = int(row.get("death_events", 0))

        event_rows = Event.objects.filter(
            self._province_access_q("province", province_scope)
        ).filter(self._province_filter_q("province", selected_geo))
        if start_date_str and end_date_str:
            event_rows = event_rows.filter(
                Q(interview_complete_date__gte=start_date_str, interview_complete_date__lte=end_date_str)
                | Q(interview_scheduled_date__gte=start_date_str, interview_scheduled_date__lte=end_date_str)
                | Q(submission_date__gte=start_date_str, submission_date__lte=end_date_str)
            )
        # Death-related events only, without requiring explicit death/VA links.
        event_rows = event_rows.filter(
            Q(event_type=Event.EventType.DEATH) | Q(event_type_code__icontains="death")
        )

        scheduling_records = []
        for event in event_rows.values(
            "event_status",
            "va_interview_status",
            "interview_scheduled_date",
            "interview_complete_date",
            "va__Id10010",
        ):
            mso_name_raw = (event.get("va__Id10010") or "").strip()
            if not mso_name_raw:
                continue
            mso_name = self._format_mso_display_name(mso_name_raw)
            is_scheduled = (
                event.get("event_status") == Event.EventStatus.VA_INTERVIEW_SCHEDULED
                or (
                    event.get("va_interview_status") == Event.VAInterviewStatus.SCHEDULED
                    and event.get("interview_scheduled_date") is not None
                )
            )
            is_not_complete = (
                event.get("va_interview_status") == Event.VAInterviewStatus.SCHEDULED
                and event.get("interview_complete_date") is None
            )
            scheduling_records.append(
                {
                    "agg_name": mso_name,
                    "agg_name_norm": self.normalize_person_name(mso_name),
                    "va_scheduled": int(is_scheduled),
                    "va_not_complete": int(is_not_complete),
                }
            )

        if scheduling_records:
            df_scheduling = pd.DataFrame.from_records(scheduling_records)
            df_scheduling[["matched_norm", "best_norm", "best_score"]] = df_scheduling[
                "agg_name_norm"
            ].apply(
                lambda value: pd.Series(
                    self._best_match_name_norm_with_score(value, mso_name_norms, 85)
                )
            )
            unmatched_agg_logs.extend(
                [
                    {
                        "source": "scheduling",
                        "agg_name": row.get("agg_name"),
                        "best_match": mso_name_norm_to_name.get(row.get("best_norm"), row.get("best_norm")),
                        "score": row.get("best_score", 0.0),
                    }
                    for _, row in df_scheduling[df_scheduling["matched_norm"].isna()].iterrows()
                ]
            )
            df_scheduling = df_scheduling[df_scheduling["matched_norm"].notna()]
            df_scheduling["name"] = df_scheduling["matched_norm"].map(mso_name_norm_to_name)
            df_scheduling = df_scheduling[df_scheduling["name"].notna()]
            df_scheduling = (
                df_scheduling.groupby("name", as_index=False)[
                    ["va_scheduled", "va_not_complete"]
                ]
                .sum()
            )
            df_mso = df_mso.merge(df_scheduling, on="name", how="left")
            for col in ("va_scheduled", "va_not_complete"):
                if col not in df_mso:
                    df_mso[col] = 0
                df_mso[col] = df_mso[col].fillna(0).astype(int)
            for row in df_mso.to_dict("records"):
                mso_name = row.get("name")
                if not mso_name:
                    continue
                entry = stats_by_mso[mso_name]
                entry["name"] = mso_name
                entry["va_scheduled"] = int(row.get("va_scheduled", 0))
                entry["va_not_complete"] = int(row.get("va_not_complete", 0))

        mean_days_records = []
        for event in event_rows.filter(
            death__isnull=False,
            interview_complete_date__isnull=False,
        ).values(
            "death_id",
            "death__DE_06",
            "interview_complete_date",
            "va__Id10010",
        ):
            death_date = self._coerce_date(event.get("death__DE_06"))
            complete_date = event.get("interview_complete_date")
            if not death_date or not complete_date:
                continue
            mean_name_raw = (event.get("va__Id10010") or "").strip()
            if not mean_name_raw:
                continue
            mean_name = self._format_mso_display_name(mean_name_raw)
            mean_days_records.append(
                {
                    "agg_name": mean_name,
                    "agg_name_norm": self.normalize_person_name(mean_name),
                    "days_to_complete": (complete_date - death_date).days,
                }
            )

        if mean_days_records:
            df_mean_days = pd.DataFrame.from_records(mean_days_records)
            df_mean_days[["matched_norm", "best_norm", "best_score"]] = df_mean_days[
                "agg_name_norm"
            ].apply(
                lambda value: pd.Series(
                    self._best_match_name_norm_with_score(value, mso_name_norms, 85)
                )
            )
            unmatched_agg_logs.extend(
                [
                    {
                        "source": "mean_days",
                        "agg_name": row.get("agg_name"),
                        "best_match": mso_name_norm_to_name.get(row.get("best_norm"), row.get("best_norm")),
                        "score": row.get("best_score", 0.0),
                    }
                    for _, row in df_mean_days[df_mean_days["matched_norm"].isna()].iterrows()
                ]
            )
            df_mean_days = df_mean_days[df_mean_days["matched_norm"].notna()]
            df_mean_days["name"] = df_mean_days["matched_norm"].map(mso_name_norm_to_name)
            df_mean_days = df_mean_days[df_mean_days["name"].notna()]
            df_mean_days = (
                df_mean_days.groupby("name", as_index=False)["days_to_complete"]
                .mean()
                .round(1)
                .rename(columns={"days_to_complete": "mean_death_to_va_complete"})
            )
            df_mso = df_mso.merge(df_mean_days, on="name", how="left")
            if "mean_death_to_va_complete" not in df_mso:
                df_mso["mean_death_to_va_complete"] = ""
            df_mso["mean_death_to_va_complete"] = df_mso[
                "mean_death_to_va_complete"
            ].where(
                pd.notna(df_mso["mean_death_to_va_complete"]),
                "",
            )
            for row in df_mso.to_dict("records"):
                mso_name = row.get("name")
                if not mso_name:
                    continue
                entry = stats_by_mso[mso_name]
                entry["name"] = mso_name
                entry["mean_death_to_va_complete"] = row.get(
                    "mean_death_to_va_complete", ""
                )

        duration_records = []
        for va_row in scoped_va_queryset.values("id", "Id10012", "Id10011", "Id10481", "Id10010", "location__name"):
            mso_name = va_to_mso_name.get(va_row["id"]) or self._mso_name_from_va_row(
                {
                    "Id10010": va_row.get("Id10010"),
                    "location__name": va_row.get("location__name"),
                }
            )
            duration_records.append(
                {
                    "name": mso_name,
                    "mso_name_norm": self.normalize_person_name(mso_name),
                    "interview_date": va_row.get("Id10012"),
                    "start_time": va_row.get("Id10011"),
                    "end_datetime": va_row.get("Id10481"),
                }
            )

        if duration_records:
            df_duration = pd.DataFrame.from_records(duration_records)
            start_dt_text = (
                df_duration["interview_date"].fillna("").astype(str).str.strip()
                + " "
                + df_duration["start_time"].fillna("").astype(str).str.strip()
            ).str.strip()
            df_duration["start_dt"] = pd.to_datetime(
                start_dt_text,
                errors="coerce",
                utc=True,
            )
            df_duration["end_dt"] = pd.to_datetime(
                df_duration["end_datetime"],
                errors="coerce",
                utc=True,
            )
            df_duration = df_duration.dropna(subset=["start_dt", "end_dt"])
            if not df_duration.empty:
                duration_delta = df_duration["end_dt"].sub(df_duration["start_dt"])
                df_duration["duration_minutes"] = (
                    duration_delta.dt.total_seconds() / 60.0
                )
                df_duration["duration_outlier"] = (
                    (df_duration["duration_minutes"] < 15)
                    | (df_duration["duration_minutes"] > 90)
                ).astype(int)
                df_duration_metrics = (
                    df_duration.groupby("name", as_index=False)["duration_outlier"]
                    .sum()
                    .rename(columns={"duration_outlier": "duration_outliers"})
                )
                df_duration_metrics["mso_name_norm"] = df_duration_metrics["name"].apply(
                    self.normalize_person_name
                )
                df_mso = df_mso.merge(
                    df_duration_metrics,
                    on=["name", "mso_name_norm"],
                    how="outer",
                )
                if "duration_outliers" not in df_mso:
                    df_mso["duration_outliers"] = 0
                df_mso["duration_outliers"] = (
                    df_mso["duration_outliers"].fillna(0).astype(int)
                )
                for row in df_mso.to_dict("records"):
                    mso_name = row.get("name")
                    if not mso_name:
                        continue
                    entry = stats_by_mso[mso_name]
                    entry["name"] = mso_name
                    entry["duration_outliers"] = int(row.get("duration_outliers", 0))

        if mso_names:
            df_mso = df_mso[df_mso["name"].isin(mso_names)].copy()
        else:
            df_mso = df_mso.iloc[0:0].copy()

        for col in (
            "death_events",
            "va_scheduled",
            "va_not_complete",
            "va_total",
            "valid_cod",
            "indeterminate",
            "error",
            "duration_outliers",
        ):
            if col not in df_mso:
                df_mso[col] = 0
            df_mso[col] = df_mso[col].fillna(0).astype(int)
        if "mean_death_to_va_complete" not in df_mso:
            df_mso["mean_death_to_va_complete"] = ""
        df_mso["mean_death_to_va_complete"] = df_mso[
            "mean_death_to_va_complete"
        ].where(pd.notna(df_mso["mean_death_to_va_complete"]), "")

        if unmatched_agg_logs:
            unmatched_count = len(unmatched_agg_logs)
            top_unmatched = sorted(
                unmatched_agg_logs,
                key=lambda item: item.get("score", 0.0),
                reverse=True,
            )[:20]
            logger.debug(
                "MSO unmatched aggregate names: %s | Top 20: %s",
                unmatched_count,
                top_unmatched,
            )

        count_columns = (
            "death_events",
            "va_scheduled",
            "va_not_complete",
            "va_total",
            "valid_cod",
            "indeterminate",
            "error",
            "duration_outliers",
        )
        final_rows = []
        for raw_row in df_mso.to_dict("records"):
            mean_value = raw_row.get("mean_death_to_va_complete", "")
            if pd.isna(mean_value) or mean_value in (None, ""):
                mean_value = ""
            else:
                mean_value = round(float(mean_value), 1)

            row = {
                "name": self._format_mso_display_name(raw_row.get("name")),
                "province": raw_row.get("province") or "—",
                "death_events": 0,
                "va_scheduled": 0,
                "va_not_complete": 0,
                "mean_death_to_va_complete": mean_value,
                "va_total": 0,
                "valid_cod": 0,
                "indeterminate": 0,
                "error": 0,
                "duration_outliers": 0,
            }
            for key in count_columns:
                value = raw_row.get(key, 0)
                row[key] = 0 if pd.isna(value) else int(value)
            final_rows.append(row)
        return final_rows

    def get_regional_operations_context(self):
        selected_geo = self._normalized_geo()
        time_window = self._resolve_time_window()
        selected_time_preset = time_window["selected_time_preset"]
        selected_mso_source = self._pick(
            self.request.GET.get("source", "community"),
            {item["value"] for item in self.mso_source_options},
            "community",
        )
        csa_sort = self._pick(
            self.request.GET.get("csa_sort", "visits"),
            {key for key, _ in self.csa_column_map},
            "visits",
        )
        csa_search = self.request.GET.get("csa_search", "").strip()
        mso_search = self.request.GET.get("mso_search", "").strip()
        csa_dir = self._pick(self.request.GET.get("csa_dir", "desc"), {"asc", "desc"}, "desc")
        mso_sort = self._pick(
            self.request.GET.get("mso_sort", "death_events"),
            {key for key, _ in self.mso_column_map},
            "death_events",
        )
        mso_dir = self._pick(self.request.GET.get("mso_dir", "desc"), {"asc", "desc"}, "desc")
        csa_page = max(1, self._to_int(self.request.GET.get("csa_page", "1")))
        mso_page = max(1, self._to_int(self.request.GET.get("mso_page", "1")))

        province_scope = self._allowed_province_names()
        va_queryset = self.request.user.verbal_autopsies().annotate(
            province_name=Subquery(
                Location.objects.values("name").filter(
                    Q(path=Substr(OuterRef("location__path"), 1, 8)),
                    Q(depth=2),
                )[:1]
            )
        )
        if time_window["start_date_str"] and time_window["end_date_str"]:
            va_queryset = va_queryset.filter(
                Id10012__gte=time_window["start_date_str"],
                Id10012__lte=time_window["end_date_str"],
            )

        csa_rows = self._build_csa_rows(
                selected_geo,
                time_window["start_date_str"],
                time_window["end_date_str"],
                province_scope,
            )
        if selected_geo and selected_geo != "national":
            csa_rows = [
                row
                for row in csa_rows
                if self._matches_geo_by_province(row.get("province"), selected_geo)
            ]
        if csa_search:
            csa_rows = [
                row for row in csa_rows if self._matches_name_search(row.get("name", ""), csa_search)
            ]
        csa_rows_sorted = self._sort_rows(
            csa_rows,
            csa_sort,
            csa_dir,
        )
        mso_rows = self._build_mso_rows(
            selected_geo,
            selected_mso_source,
            va_queryset,
            province_scope,
            time_window["start_date_str"],
            time_window["end_date_str"],
        )
        if selected_geo and selected_geo != "national":
            mso_rows = [
                row
                for row in mso_rows
                if self._matches_geo_by_province(row.get("province"), selected_geo)
            ]
        if mso_search:
            mso_search_lower = mso_search.lower()
            mso_rows = [
                row
                for row in mso_rows
                if mso_search_lower in str(row.get("name", "")).lower()
            ]
        mso_rows_sorted = self._sort_rows(
            mso_rows,
            mso_sort,
            mso_dir,
        )
        csa_pagination = self._paginate_rows(csa_rows_sorted, csa_page)
        mso_pagination = self._paginate_rows(mso_rows_sorted, mso_page)

        return {
            "geography_options": self.geography_options,
            "selected_geography": selected_geo,
            "time_presets": self.time_presets,
            "selected_time_preset": selected_time_preset,
            "selected_preset": time_window["selected_preset"],
            "start_value": time_window["start_value"],
            "end_value": time_window["end_value"],
            "time_label": time_window["time_label"],
            "selected_start_date": time_window["start_value"],
            "selected_end_date": time_window["end_value"],
            "mso_source_options": self.mso_source_options,
            "selected_mso_source": selected_mso_source,
            "csa_stats": csa_pagination["rows"],
            "csa_rows": csa_pagination["rows"],
            "csa_sort": csa_sort,
            "csa_dir": csa_dir,
            "csa_search": csa_search,
            "csa_page": csa_pagination["page"],
            "csa_total_pages": csa_pagination["total_pages"],
            "csa_has_prev": csa_pagination["has_prev"],
            "csa_has_next": csa_pagination["has_next"],
            "csa_prev_page": csa_pagination["prev_page"],
            "csa_next_page": csa_pagination["next_page"],
            "csa_visible_count": csa_pagination["visible_count"],
            "csa_total_count": csa_pagination["total_count"],
            "mso_stats": mso_pagination["rows"],
            "mso_rows": mso_pagination["rows"],
            "mso_sort": mso_sort,
            "mso_dir": mso_dir,
            "mso_search": mso_search,
            "mso_page": mso_pagination["page"],
            "mso_total_pages": mso_pagination["total_pages"],
            "mso_has_prev": mso_pagination["has_prev"],
            "mso_has_next": mso_pagination["has_next"],
            "mso_prev_page": mso_pagination["prev_page"],
            "mso_next_page": mso_pagination["next_page"],
            "mso_visible_count": mso_pagination["visible_count"],
            "mso_total_count": mso_pagination["total_count"],
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_regional_operations_context())
        return context


class RegionalOperations(
    RegionalOperationsComponentContextMixin, CustomAuthMixin, TemplateView
):
    template_name = "home/regional_operations.html"


class RegionalOperationsMapData(
    RegionalOperationsComponentContextMixin, CustomAuthMixin, View
):
    def get(self, request, *args, **kwargs):
        selected_geo = self._normalized_geo()
        time_window = self._resolve_time_window()
        province_scope = self._allowed_province_names()

        va_queryset = request.user.verbal_autopsies().annotate(
            province_name=Subquery(
                Location.objects.values("name").filter(
                    Q(path=Substr(OuterRef("location__path"), 1, 8)),
                    Q(depth=2),
                )[:1]
            ),
            district_name=Subquery(
                Location.objects.values("name").filter(
                    Q(path=Substr(OuterRef("location__path"), 1, 12)),
                    Q(depth=3),
                )[:1]
            ),
        )
        va_queryset = va_queryset.filter(self._province_filter_q("province_name", selected_geo))
        va_queryset = va_queryset.filter(
            self._province_access_q("province_name", province_scope)
        )
        if time_window["start_date_str"] and time_window["end_date_str"]:
            va_queryset = va_queryset.filter(
                Id10012__gte=time_window["start_date_str"],
                Id10012__lte=time_window["end_date_str"],
            )

        geographic_province_sums = (
            va_queryset.filter(causes__isnull=False)
            .exclude(province_name__isnull=True)
            .values("province_name")
            .annotate(count=Count("pk"))
        )
        geographic_district_sums = (
            va_queryset.filter(causes__isnull=False)
            .exclude(district_name__isnull=True)
            .values("district_name")
            .annotate(count=Count("pk"))
        )

        return JsonResponse(
            {
                "geographic_province_sums": list(geographic_province_sums),
                "geographic_district_sums": list(geographic_district_sums),
            }
        )


class RegionalOperationsFiltersComponent(
    RegionalOperationsComponentContextMixin, CustomAuthMixin, TemplateView
):
    template_name = "home/components/_regional_operations_filters.html"


class RegionalOperationsCsaComponent(
    RegionalOperationsComponentContextMixin, CustomAuthMixin, TemplateView
):
    template_name = "home/components/_regional_operations_csa_table.html"


class RegionalOperationsMsoComponent(
    RegionalOperationsComponentContextMixin, CustomAuthMixin, TemplateView
):
    template_name = "home/components/_regional_operations_mso_table.html"
