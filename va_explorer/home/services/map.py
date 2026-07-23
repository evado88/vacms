import logging
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta

from django.db.models import Max, OuterRef, Q, Subquery, TextField
from django.db.models.functions import Greatest, Substr
from django.utils import timezone
from django.utils.dateparse import parse_date as django_parse_date
from django.utils.dateparse import parse_datetime as django_parse_datetime

from va_explorer.dashboard_cutoff import clamp_chart_start_dt
from va_explorer.home.services.common import (
    apply_text_geography_filter,
    build_date_expr,
    parse_submission_timestamp,
)
from va_explorer.home.services.nov import CORE_EVENT_DATE_FIELDS, VA_DATE_FIELDS
from va_explorer.utils.profiling import timed_block
from va_explorer.va_analytics.utils.loading import (
    _derive_va_geo_levels,
    _resolve_geo_name,
)
from va_explorer.va_data_management.models import (
    VASA,
    Death,
    Location,
    Pregnancy,
    PregnancyOutcome,
    VerbalAutopsy,
)
from va_explorer.va_data_management.utils.location_access import (
    restrict_queryset_to_user_locations,
)

logger = logging.getLogger(__name__)

HOME_GEO_LEVEL_LABELS = {
    "province": "Province",
    "district": "District",
    "constituency": "Constituency",
    "ward": "Ward",
    "ea": "EA",
}


def _normalize_geo_value(value, level=None):
    normalized = " ".join(str(value or "").split()).strip()
    if not normalized:
        return ""
    if normalized.lower() in {"nan", "none", "null", "na", "n/a"}:
        return ""
    if level:
        return _resolve_geo_name(normalized, level)
    return normalized


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
            parsed_start = timezone.make_aware(
                parsed_start, timezone.get_current_timezone()
            )
        start_dt = timezone.localtime(parsed_start)
    if parsed_end is not None:
        if timezone.is_naive(parsed_end):
            parsed_end = timezone.make_aware(
                parsed_end, timezone.get_current_timezone()
            )
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


def _latest_timestamp_from_mapping(row, date_fields):
    timestamps = [
        parse_submission_timestamp(row.get(field_name)) for field_name in date_fields
    ]
    timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
    return max(timestamps) if timestamps else None


def _latest_timestamp_from_object(obj, date_fields):
    timestamps = [
        parse_submission_timestamp(getattr(obj, field_name, None))
        for field_name in date_fields
    ]
    timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
    return max(timestamps) if timestamps else None


def _latest_geo_by_identifier(queryset, key_field, date_fields, start_dt, end_dt):
    geo_fields = ("province", "district", "constituency", "ward", "ea")
    date_exprs = [build_date_expr(queryset.model, f) for f in date_fields]
    latest_expr = (
        Greatest(*date_exprs, output_field=TextField())
        if len(date_exprs) > 1
        else date_exprs[0]
    )

    base = queryset.exclude(**{f"{key_field}__isnull": True}).exclude(**{key_field: ""})

    max_rows = (
        base.values(key_field)
        .annotate(_latest_ts=Max(latest_expr))
        .values_list(key_field, "_latest_ts")
        .iterator()
    )
    max_ts_by_key = {}
    for identifier, raw_ts in max_rows:
        if not identifier or not raw_ts:
            continue
        ts = parse_submission_timestamp(raw_ts)
        if ts is None:
            continue
        if start_dt and ts < start_dt:
            continue
        if end_dt and ts > end_dt:
            continue
        max_ts_by_key[identifier] = ts

    if not max_ts_by_key:
        return {}

    latest = {}
    for row in (
        base.filter(**{f"{key_field}__in": list(max_ts_by_key)})
        .values(key_field, *date_fields, *geo_fields)
        .iterator(chunk_size=2000)
    ):
        identifier = row.get(key_field)
        if not identifier or identifier not in max_ts_by_key:
            continue
        row_ts = _latest_timestamp_from_mapping(row, date_fields)
        if row_ts is None:
            continue
        if start_dt and row_ts < start_dt:
            continue
        if end_dt and row_ts > end_dt:
            continue
        if identifier in latest and row_ts <= latest[identifier][0]:
            continue
        latest[identifier] = (
            row_ts,
            {
                "province": _normalize_geo_value(
                    row.get("province"), HOME_GEO_LEVEL_LABELS["province"]
                ),
                "district": _normalize_geo_value(
                    row.get("district"), HOME_GEO_LEVEL_LABELS["district"]
                ),
                "constituency": _normalize_geo_value(
                    row.get("constituency"), HOME_GEO_LEVEL_LABELS["constituency"]
                ),
                "ward": _normalize_geo_value(
                    row.get("ward"), HOME_GEO_LEVEL_LABELS["ward"]
                ),
                "ea": _normalize_geo_value(row.get("ea"), HOME_GEO_LEVEL_LABELS["ea"]),
            },
        )
    return latest


def _annotate_va_location_names(queryset):
    return queryset.annotate(
        province_name_from_location=Subquery(
            Location.objects.values("name").filter(
                Q(path=Substr(OuterRef("location__path"), 1, 8)),
                Q(depth=2),
            )[:1]
        ),
        district_name_from_location=Subquery(
            Location.objects.values("name").filter(
                Q(path=Substr(OuterRef("location__path"), 1, 12)),
                Q(depth=3),
            )[:1]
        ),
    )


def _va_matches_resolved_geography(va, geography_level, geography_value):
    if not geography_level or not geography_value:
        return True
    level_label = HOME_GEO_LEVEL_LABELS.get(geography_level)
    if not level_label:
        return True

    expected = _normalize_geo_value(geography_value, level_label).casefold()
    if not expected:
        return True

    geo = _derive_va_geo_levels(va)
    actual = _normalize_geo_value(geo.get(level_label), level_label).casefold()
    return bool(actual and actual == expected)


def _apply_va_geography_filter(queryset, geography_level, geography_value):
    if not geography_level or geography_level == "national" or not geography_value:
        return queryset

    matching_ids = []
    for va in (
        _annotate_va_location_names(queryset)
        .select_related("location", "cluster")
        .iterator(chunk_size=2000)
    ):
        if _va_matches_resolved_geography(va, geography_level, geography_value):
            matching_ids.append(va.pk)
    return queryset.filter(pk__in=matching_ids)


def _latest_va_geo_by_identifier(queryset, start_dt, end_dt):
    key_field = "instanceid"
    date_exprs = [build_date_expr(queryset.model, field) for field in VA_DATE_FIELDS]
    latest_expr = Greatest(*date_exprs, output_field=TextField())
    base = queryset.exclude(instanceid__isnull=True).exclude(instanceid="")

    max_rows = (
        base.values(key_field)
        .annotate(_latest_ts=Max(latest_expr))
        .values_list(key_field, "_latest_ts")
        .iterator()
    )
    max_ts_by_key = {}
    for identifier, raw_ts in max_rows:
        if not identifier or not raw_ts:
            continue
        ts = parse_submission_timestamp(raw_ts)
        if ts is None:
            continue
        if start_dt and ts < start_dt:
            continue
        if end_dt and ts > end_dt:
            continue
        max_ts_by_key[identifier] = ts

    if not max_ts_by_key:
        return {}

    annotated = _annotate_va_location_names(
        base.filter(instanceid__in=list(max_ts_by_key))
    )

    latest = {}
    for va in annotated.select_related("location", "cluster").iterator(chunk_size=2000):
        identifier = va.instanceid
        if not identifier or identifier not in max_ts_by_key:
            continue
        row_ts = _latest_timestamp_from_object(va, VA_DATE_FIELDS)
        if row_ts is None:
            continue
        if start_dt and row_ts < start_dt:
            continue
        if end_dt and row_ts > end_dt:
            continue
        if identifier in latest and row_ts <= latest[identifier][0]:
            continue

        geo = _derive_va_geo_levels(va)
        latest[identifier] = (
            row_ts,
            {
                "province": geo.get("Province", ""),
                "district": geo.get("District", ""),
                "constituency": geo.get("Constituency", ""),
                "ward": geo.get("Ward", ""),
                "ea": geo.get("EA", ""),
            },
        )
    return latest


def _filter_qs_by_date_window(queryset, date_fields, start_dt, end_dt):
    window_q = Q()
    if start_dt is not None:
        start_token = start_dt.date().isoformat()
        start_q = Q()
        for field_name in date_fields:
            start_q |= Q(**{f"{field_name}__gte": start_token})
        window_q &= start_q
    if end_dt is not None:
        end_token = end_dt.isoformat()
        end_q = Q()
        for field_name in date_fields:
            end_q |= Q(**{f"{field_name}__lte": end_token})
        window_q &= end_q
    return queryset.filter(window_q) if window_q else queryset


def build_home_combined_map_payload(request):
    filters = _parse_home_map_filters(request)
    geography_level = filters["geography_level"]
    geography_value = filters["geography_value"]
    start_dt = clamp_chart_start_dt(filters["start_dt"])
    end_dt = filters["end_dt"]

    with timed_block("home.map.qs.build", request=request):
        user = getattr(request, "user", None)
        pregnancies_qs = apply_text_geography_filter(
            restrict_queryset_to_user_locations(Pregnancy.objects.all(), user),
            Pregnancy,
            geography_level or "national",
            geography_value,
        )
        outcomes_qs = apply_text_geography_filter(
            restrict_queryset_to_user_locations(PregnancyOutcome.objects.all(), user),
            PregnancyOutcome,
            geography_level or "national",
            geography_value,
        )
        deaths_qs = apply_text_geography_filter(
            restrict_queryset_to_user_locations(Death.objects.all(), user),
            Death,
            geography_level or "national",
            geography_value,
        )
        if user:
            vas_qs = user.verbal_autopsies().filter(
                deleted_at__isnull=True, duplicate=False
            )
            vasa_qs = user.vasa_records().filter(deleted_at__isnull=True)
        else:
            vas_qs = VerbalAutopsy.objects.filter(
                deleted_at__isnull=True, duplicate=False
            )
            vasa_qs = VASA.objects.filter(deleted_at__isnull=True)
        vas_qs = _apply_va_geography_filter(vas_qs, geography_level, geography_value)
        # VASA records are folded into the verbal-autopsy map layer (same geo
        # derivation and geopoint field via BaseVerbalAutopsy).
        vasa_qs = _apply_va_geography_filter(vasa_qs, geography_level, geography_value)

    with timed_block("home.map.aggregate", request=request):
        model_sets = (
            (
                "pregnancies_count",
                _latest_geo_by_identifier(
                    pregnancies_qs,
                    "key",
                    CORE_EVENT_DATE_FIELDS,
                    start_dt,
                    end_dt,
                ),
            ),
            (
                "pregnancy_outcomes_count",
                _latest_geo_by_identifier(
                    outcomes_qs,
                    "key",
                    CORE_EVENT_DATE_FIELDS,
                    start_dt,
                    end_dt,
                ),
            ),
            (
                "deaths_count",
                _latest_geo_by_identifier(
                    deaths_qs,
                    "key",
                    CORE_EVENT_DATE_FIELDS,
                    start_dt,
                    end_dt,
                ),
            ),
            (
                "verbal_autopsies_count",
                {
                    **_latest_va_geo_by_identifier(vas_qs, start_dt, end_dt),
                    **_latest_va_geo_by_identifier(vasa_qs, start_dt, end_dt),
                },
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
        "Province": [
            {"name": row["province_name"], "count": row["count"]}
            for row in province_sums
        ],
        "District": [
            {"name": row["district_name"], "count": row["count"]}
            for row in district_sums
        ],
        "Constituency": [
            {"name": row["constituency_name"], "count": row["count"]}
            for row in constituency_sums
        ],
        "Ward": [
            {"name": row["ward_name"], "count": row["count"]} for row in ward_sums
        ],
        "EA": [{"name": row["ea_name"], "count": row["count"]} for row in ea_sums],
    }

    payload = {
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

    gps_pregnancies_qs = pregnancies_qs
    gps_outcomes_qs = outcomes_qs
    gps_deaths_qs = deaths_qs
    gps_vas_qs = vas_qs
    gps_vasa_qs = vasa_qs
    if end_dt is not None and end_dt < start_dt:
        gps_pregnancies_qs = pregnancies_qs.none()
        gps_outcomes_qs = outcomes_qs.none()
        gps_deaths_qs = deaths_qs.none()
        gps_vas_qs = vas_qs.none()
        gps_vasa_qs = vasa_qs.none()
    else:
        gps_pregnancies_qs = _filter_qs_by_date_window(
            pregnancies_qs, CORE_EVENT_DATE_FIELDS, start_dt, end_dt
        )
        gps_outcomes_qs = _filter_qs_by_date_window(
            outcomes_qs, CORE_EVENT_DATE_FIELDS, start_dt, end_dt
        )
        gps_deaths_qs = _filter_qs_by_date_window(
            deaths_qs, CORE_EVENT_DATE_FIELDS, start_dt, end_dt
        )
        gps_vas_qs = _filter_qs_by_date_window(vas_qs, VA_DATE_FIELDS, start_dt, end_dt)
        gps_vasa_qs = _filter_qs_by_date_window(
            vasa_qs, VA_DATE_FIELDS, start_dt, end_dt
        )

    _attach_home_gps_payload(
        payload,
        request,
        pregnancies_qs=gps_pregnancies_qs,
        outcomes_qs=gps_outcomes_qs,
        deaths_qs=gps_deaths_qs,
        vas_qs=gps_vas_qs,
        vasa_qs=gps_vasa_qs,
    )
    return payload


def _attach_home_gps_payload(
    payload, request, *, pregnancies_qs, outcomes_qs, deaths_qs, vas_qs, vasa_qs=None
):
    """Attach combined GPS points across all four event models to the home payload."""
    from va_explorer.va_analytics.views import (
        GPS_DEFAULT_LIMIT,
        _build_gps_points,
        _gps_request_params,
        _qs_cache_signature,
    )

    include, limit, _offset = _gps_request_params(request)
    if not include:
        return
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated and user.has_perm("va_analytics.view_pii")):
        logger.info(
            "PII GPS access denied for home overview",
            extra={"user_id": getattr(user, "id", None), "path": request.path},
        )
        payload["gps_points"] = []
        payload["gps_total"] = 0
        payload["gps_truncated"] = False
        payload["gps_pii_denied"] = True
        return

    sources = [
        (pregnancies_qs, "PE_25", "pregnancy"),
        (outcomes_qs, "gps", "pregnancy_outcome"),
        (deaths_qs, "DE_34", "death"),
        (vas_qs, "geopoint", "verbal_autopsy"),
    ]
    if vasa_qs is not None:
        # VASA GPS points render on the same verbal-autopsy map layer.
        sources.append((vasa_qs, "geopoint", "verbal_autopsy"))

    source_counts = [
        qs.exclude(**{f"{gps_field}__isnull": True}).exclude(**{gps_field: ""}).count()
        for qs, gps_field, _event_type in sources
    ]
    event_types = list(dict.fromkeys(event_type for _qs, _field, event_type in sources))
    event_counts = [
        sum(
            source_counts[index]
            for index, source in enumerate(sources)
            if source[2] == event_type
        )
        for event_type in event_types
    ]
    event_limits = _allocate_proportional_gps_limits(event_counts, limit)
    source_limits = [0] * len(sources)
    for event_type, event_limit in zip(event_types, event_limits):
        source_indexes = [
            index for index, source in enumerate(sources) if source[2] == event_type
        ]
        limits = _allocate_proportional_gps_limits(
            [source_counts[index] for index in source_indexes], event_limit
        )
        for source_index, source_limit in zip(source_indexes, limits):
            source_limits[source_index] = source_limit

    combined_points = []
    combined_total = sum(source_counts)
    for (qs, gps_field, event_type), source_limit in zip(sources, source_limits):
        if source_limit <= 0:
            continue
        points, _total, _truncated = _build_gps_points(
            qs,
            gps_field=gps_field,
            event_type=event_type,
            limit=min(source_limit, GPS_DEFAULT_LIMIT),
            offset=0,
            cache_signature=_qs_cache_signature(qs),
        )
        combined_points.extend(points)
    payload["gps_points"] = combined_points
    payload["gps_total"] = combined_total
    payload["gps_truncated"] = combined_total > len(combined_points)
    payload["gps_pii_denied"] = False


def _allocate_proportional_gps_limits(counts, limit):
    """Allocate a capped sample without starving small non-empty sources."""
    capacities = [max(0, int(count or 0)) for count in counts]
    allocation = [0] * len(capacities)
    active_indexes = [index for index, count in enumerate(capacities) if count]
    target = min(max(0, int(limit or 0)), sum(capacities))
    if not active_indexes or target <= 0:
        return allocation

    if target < len(active_indexes):
        ranked = sorted(active_indexes, key=lambda index: (-capacities[index], index))
        for index in ranked[:target]:
            allocation[index] = 1
        return allocation

    for index in active_indexes:
        allocation[index] = 1
    remaining = target - len(active_indexes)
    if remaining <= 0:
        return allocation

    residual_capacities = [
        capacities[index] - allocation[index] for index in range(len(capacities))
    ]
    residual_total = sum(residual_capacities)
    if residual_total <= 0:
        return allocation

    remainders = []
    for index in active_indexes:
        exact_share = remaining * residual_capacities[index] / residual_total
        whole_share = min(residual_capacities[index], int(exact_share))
        allocation[index] += whole_share
        remainders.append((exact_share - whole_share, capacities[index], index))

    leftover = target - sum(allocation)
    for _fraction, _capacity, index in sorted(
        remainders, key=lambda item: (-item[0], -item[1], item[2])
    ):
        if leftover <= 0:
            break
        if allocation[index] >= capacities[index]:
            continue
        allocation[index] += 1
        leftover -= 1
    return allocation
