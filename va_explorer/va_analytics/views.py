import csv
import hashlib
import json
import logging
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone
from statistics import median

import pandas as pd
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.cache import cache
from django.db.models import (
    Case,
    CharField,
    Count,
    DateField,
    F,
    Max,
    Q,
    Value,
    When,
)
from django.db.models.functions import Cast, Lower, Substr, Trim
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date as django_parse_date
from django.utils.dateparse import parse_datetime as django_parse_datetime
from django.views.generic import ListView, RedirectView, TemplateView, View
from numpy import round
from pandas import to_datetime as to_dt
from rest_framework.response import Response
from rest_framework.views import APIView

from va_explorer.home.services.common import (
    coverage_band,
    parse_filter_range,
    parse_location_filter,
)
from va_explorer.home.views import (
    HomeOverviewMapData,
    NationalOperationalFilterData,
)
from va_explorer.users.models import User
from va_explorer.utils.mixins import CsvDownloadPermissionMixin, CustomAuthMixin
from va_explorer.utils.profiling import timed_block
from va_explorer.va_analytics.filters import SupervisionFilter
from va_explorer.va_data_management.models import Death, Pregnancy, PregnancyOutcome
from va_explorer.va_data_management.utils.date_parsing import (
    get_interview_dates,
    parse_date,
)
from va_explorer.va_data_management.utils.geopoint import parse_geopoint
from va_explorer.va_data_management.utils.location_access import (
    restrict_queryset_to_user_locations,
)

from .utils.loading import _resolve_geo_name, get_filtered_va_queryset, load_va_data

logger = logging.getLogger(__name__)

GPS_DEFAULT_LIMIT = 2000
GPS_CACHE_TIMEOUT = 3600  # 1 hour

TREND_SUBMISSION_HARD_START_DATE = "2024-01-01"
PREGNANCY_TREND_HARD_START_DATE = "2024-09-01"


def get_pregnancy_outcomes_filter_state(request):
    time_preset = (request.GET.get("time_preset") or "all_time").strip()
    allowed_presets = {
        "all_time",
        "last_30_days",
        "last_7_days",
        "last_24_hours",
        "custom",
    }
    if time_preset not in allowed_presets:
        time_preset = "all_time"

    map_view = (request.GET.get("map_view") or "Province").strip().title()
    if map_view not in {"Province", "District", "Constituency", "Ward", "Ea", "EA"}:
        map_view = "Province"
    if map_view == "Ea":
        map_view = "EA"

    return {
        "pregnancy_outcome": (request.GET.get("pregnancy_outcome") or "").strip(),
        "time_preset": time_preset,
        "start_datetime": _normalize_date_input(request.GET.get("start_datetime")),
        "end_datetime": _normalize_date_input(request.GET.get("end_datetime")),
        "map_view": map_view,
        "geography_level": (request.GET.get("geography_level") or "").strip().lower(),
        "geography_value": (
            request.GET.get("geography_value")
            or request.GET.get("geography")
            or request.GET.get("location")
            or ""
        ).strip(),
    }


def _parse_iso_datetime(value):
    raw = (value or "").strip()
    if not raw:
        return None
    parsed = django_parse_datetime(raw)
    if parsed is None:
        parsed_date = django_parse_date(raw)
        if parsed_date is None:
            return None
        parsed = datetime.combine(parsed_date, time.min)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return timezone.localtime(parsed)


def _normalize_date_input(value):
    parsed = _parse_iso_datetime(value)
    return parsed.strftime("%Y-%m-%d") if parsed else ""


def _parse_outcome_datetime(value, *, end_of_day=False):
    raw = ("" if value is None else str(value)).strip()
    if not raw:
        return None

    normalized = raw.replace("Z", "+00:00")
    if len(normalized) >= 5 and normalized[-5] in "+-" and normalized[-4:].isdigit():
        normalized = normalized[:-5] + normalized[-5:-2] + ":" + normalized[-2:]

    parsed = django_parse_datetime(normalized)
    if parsed is None and "T" in normalized:
        parsed = django_parse_datetime(normalized.replace("T", " "))

    if parsed is None:
        parsed_date = django_parse_date(raw)
        if parsed_date is not None:
            parsed = datetime.combine(parsed_date, time.max if end_of_day else time.min)

    if parsed is None:
        fallback_date = parse_date(raw)
        if fallback_date and fallback_date not in {"dk", "nan"}:
            parsed_date = django_parse_date(fallback_date)
            if parsed_date is not None:
                parsed = datetime.combine(
                    parsed_date, time.max if end_of_day else time.min
                )

    if parsed is None:
        return None

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return timezone.localtime(parsed)


def _is_multiple_birth_true(value):
    normalized = ("" if value is None else str(value)).strip().lower()
    return normalized in {
        "yes",
        "y",
        "true",
        "1",
        "multiple",
        "multiple birth",
    }


def _build_outcomes_summary_cards(filtered_qs):
    rows = filtered_qs.values(
        "end",
        "submissiondate",
        "today",
        "start",
        "PO_41",
        "PO_45",
    ).iterator()

    last_data_update = None
    last_event_date = None
    total_events = filtered_qs.count()
    multiple_birth_true_q = (
        Q(PO_45__iexact="yes")
        | Q(PO_45__iexact="y")
        | Q(PO_45__iexact="true")
        | Q(PO_45__iexact="1")
        | Q(PO_45__iexact="multiple")
        | Q(PO_45__iexact="multiple birth")
    )
    multiple_birth_true = filtered_qs.filter(multiple_birth_true_q).count()

    for row in rows:
        candidate_update = (
            _parse_outcome_datetime(row.get("end"))
            or _parse_outcome_datetime(row.get("submissiondate"))
            or _parse_outcome_datetime(row.get("today"))
            or _parse_outcome_datetime(row.get("start"))
        )
        if candidate_update and (
            last_data_update is None or candidate_update > last_data_update
        ):
            last_data_update = candidate_update

        candidate_event = _parse_outcome_datetime(
            row.get("PO_41"), end_of_day=True
        ) or _parse_outcome_datetime(row.get("submissiondate"), end_of_day=True)
        if candidate_event and (
            last_event_date is None or candidate_event > last_event_date
        ):
            last_event_date = candidate_event

    multiple_birth_pct = (
        round((multiple_birth_true / total_events) * 100, 1) if total_events else 0.0
    )

    return {
        "card_last_data_update": (
            last_data_update.strftime("%Y-%m-%d") if last_data_update else "N/A"
        ),
        "card_last_event_date": (
            last_event_date.strftime("%Y-%m-%d") if last_event_date else "N/A"
        ),
        "card_number_of_events": total_events,
        "card_multiple_birth_pct": multiple_birth_pct,
    }


def _build_text_date_trend_series(filtered_qs, *, date_field, hard_start_date):
    """Group any text date field by month and return (labels, counts)."""
    month_rows = (
        filtered_qs.exclude(**{f"{date_field}__isnull": True})
        .exclude(**{date_field: ""})
        .exclude(**{f"{date_field}__iexact": "nan"})
        .exclude(**{f"{date_field}__iexact": "nat"})
        .exclude(**{f"{date_field}__iexact": "none"})
        .exclude(**{f"{date_field}__iexact": "null"})
        .filter(**{f"{date_field}__regex": r"^\d{4}-\d{2}-\d{2}"})
        .filter(**{f"{date_field}__gte": hard_start_date})
        .annotate(month_key=Substr(date_field, 1, 7))
        .values("month_key")
        .annotate(count=Count("pk"))
        .order_by("month_key")
    )
    labels = []
    counts = []
    for row in month_rows:
        month = row.get("month_key") or ""
        try:
            labels.append(datetime.strptime(month, "%Y-%m").strftime("%b %Y"))
            counts.append(row["count"])
        except ValueError:
            continue
    return labels, counts


def _build_submission_trend_series(filtered_qs):
    return _build_text_date_trend_series(
        filtered_qs,
        date_field="submissiondate",
        hard_start_date=TREND_SUBMISSION_HARD_START_DATE,
    )


def _build_pregnancy_outcomes_trend_series(filtered_qs):
    return _build_text_date_trend_series(
        filtered_qs,
        date_field="PO_41",
        hard_start_date=TREND_SUBMISSION_HARD_START_DATE,
    )


def _normalize_birth_outcome_category(value):
    normalized = ("" if value is None else str(value)).strip().lower()
    if not normalized:
        return None

    if "still" in normalized or "born dead" in normalized:
        return "Stillbirth"
    if "live" in normalized or "born alive" in normalized:
        return "Live Birth"
    return None


def _build_birth_outcomes_bar_data(filtered_qs):
    live_q = (
        Q(PO_46__icontains="live")
        | Q(PO_46__icontains="born alive")
        | (
            (Q(PO_46__isnull=True) | Q(PO_46=""))
            & (Q(po_group__icontains="live") | Q(po_group__icontains="born alive"))
        )
    )
    still_q = (
        Q(PO_46__icontains="still")
        | Q(PO_46__icontains="born dead")
        | (
            (Q(PO_46__isnull=True) | Q(PO_46=""))
            & (Q(po_group__icontains="still") | Q(po_group__icontains="born dead"))
        )
    )
    agg = filtered_qs.aggregate(
        live_birth=Count("pk", filter=live_q),
        stillbirth=Count("pk", filter=still_q),
    )
    live_birth = agg["live_birth"] or 0
    stillbirth = agg["stillbirth"] or 0

    labels = ["Live Birth", "Stillbirth"]
    count_data = [live_birth, stillbirth]
    total = sum(count_data)
    percentage_data = (
        [round((value / total) * 100, 1) for value in count_data]
        if total
        else [0.0, 0.0]
    )

    return labels, count_data, percentage_data


def _extract_gestational_age_weeks(po_44, po_44a):
    numeric_value = None
    for candidate in (po_44a, po_44):
        if candidate is None:
            continue
        text = str(candidate).strip()
        if not text:
            continue
        # Keep only first numeric token.
        cleaned = "".join(
            ch if (ch.isdigit() or ch == "." or ch == " ") else " " for ch in text
        )
        tokens = [t for t in cleaned.split() if t]
        if not tokens:
            continue
        try:
            numeric_value = float(tokens[0])
            break
        except ValueError:
            continue

    if numeric_value is None or numeric_value <= 0:
        return None

    unit_hint = f"{po_44 or ''} {po_44a or ''}".lower()
    if "month" in unit_hint or "months" in unit_hint:
        return numeric_value * 4.348
    return numeric_value


def _build_gestational_age_distribution(filtered_qs):
    bins = {
        "<28 weeks": 0,
        "28–32 weeks": 0,
        "32–37 weeks": 0,
        ">37 weeks": 0,
    }

    for row in filtered_qs.values("PO_44", "PO_44A").iterator():
        weeks = _extract_gestational_age_weeks(row.get("PO_44"), row.get("PO_44A"))
        if weeks is None:
            continue
        if weeks < 28:
            bins["<28 weeks"] += 1
        elif weeks < 32:
            bins["28–32 weeks"] += 1
        elif weeks <= 37:
            bins["32–37 weeks"] += 1
        else:
            bins[">37 weeks"] += 1

    labels = list(bins.keys())
    counts = [bins[label] for label in labels]
    return labels, counts


def _extract_anc_visits_count(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    cleaned = "".join(ch if (ch.isdigit() or ch == " ") else " " for ch in text)
    tokens = [t for t in cleaned.split() if t]
    if not tokens:
        return None
    try:
        parsed = int(tokens[0])
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _build_anc_visits_distribution(filtered_qs):
    bins = {str(i): 0 for i in range(7)}  # 0..6
    seven_plus = 0

    for row in filtered_qs.values("PO_20").iterator():
        visits = _extract_anc_visits_count(row.get("PO_20"))
        if visits is None:
            continue
        if visits <= 6:
            bins[str(visits)] += 1
        else:
            seven_plus += 1

    labels = list(bins.keys())
    counts = [bins[label] for label in labels]
    if seven_plus > 0:
        labels.append("7+")
        counts.append(seven_plus)
    return labels, counts


def _build_outcomes_bottom_kpis(filtered_qs):
    age_values = []
    for row in filtered_qs.values("PO_05", "PO_41", "submissiondate").iterator():
        dob_dt = _parse_outcome_datetime(row.get("PO_05"))
        event_dt = _parse_outcome_datetime(row.get("PO_41")) or _parse_outcome_datetime(
            row.get("submissiondate")
        )
        if dob_dt is not None and event_dt is not None and event_dt >= dob_dt:
            age_years = (event_dt - dob_dt).days / 365.25
            age_values.append(age_years)

    hiv_known_filter = (
        ~Q(PO_21__isnull=True)
        & ~Q(PO_21="")
        & ~Q(PO_21__iexact="unknown")
        & ~Q(PO_21__iexact="unk")
        & ~Q(PO_21__iexact="na")
        & ~Q(PO_21__iexact="n/a")
        & ~Q(PO_21__iexact="not known")
    )
    hiv_positive_filter = hiv_known_filter & (
        Q(PO_21__icontains="positive")
        | Q(PO_21__iexact="pos")
        | Q(PO_21__iexact="hiv+")
        | Q(PO_21__iexact="yes")
    )
    hiv_agg = filtered_qs.aggregate(
        hiv_known=Count("pk", filter=hiv_known_filter),
        hiv_positive=Count("pk", filter=hiv_positive_filter),
    )
    hiv_known = hiv_agg["hiv_known"] or 0
    hiv_positive = hiv_agg["hiv_positive"] or 0

    mean_age = round(sum(age_values) / len(age_values), 1) if age_values else 0.0
    hiv_positive_pct = round((hiv_positive / hiv_known) * 100, 1) if hiv_known else 0.0
    return mean_age, hiv_positive_pct


def _normalize_place_of_birth_category(value):
    normalized = ("" if value is None else str(value)).strip().lower().replace("_", " ")
    if not normalized:
        return None

    if "route" in normalized and "hospital" in normalized:
        return "on route to hospital"
    if "district hospital" in normalized:
        return "district hospital"
    if (
        "health facility" in normalized
        or "health centre" in normalized
        or "health center" in normalized
        or "clinic" in normalized
    ):
        return "health facility"
    if "home" in normalized or "house" in normalized:
        return "home"
    if "hospital" in normalized:
        return "hospital"
    return "other"


def _build_place_of_birth_distribution(filtered_qs):
    categories = [
        "on route to hospital",
        "district hospital",
        "hospital",
        "health facility",
        "home",
        "other",
    ]
    categorized_rows = (
        filtered_qs.exclude(PO_43__isnull=True)
        .exclude(PO_43="")
        .annotate(
            category=Case(
                When(
                    Q(PO_43__icontains="route") & Q(PO_43__icontains="hospital"),
                    then=Value("on route to hospital"),
                ),
                When(
                    Q(PO_43__icontains="district hospital"),
                    then=Value("district hospital"),
                ),
                When(
                    Q(PO_43__icontains="health facility")
                    | Q(PO_43__icontains="health centre")
                    | Q(PO_43__icontains="health center")
                    | Q(PO_43__icontains="health_center")
                    | Q(PO_43__icontains="health_centre")
                    | Q(PO_43__icontains="clinic"),
                    then=Value("health facility"),
                ),
                When(
                    Q(PO_43__icontains="home") | Q(PO_43__icontains="house"),
                    then=Value("home"),
                ),
                When(Q(PO_43__icontains="hospital"), then=Value("hospital")),
                default=Value("other"),
                output_field=CharField(),
            )
        )
        .values("category")
        .annotate(count=Count("pk"))
    )
    counts = dict.fromkeys(categories, 0)
    for row in categorized_rows:
        category = row["category"]
        if category in counts:
            counts[category] = row["count"]

    labels = categories
    count_data = [counts[label] for label in labels]
    total = sum(count_data)
    percentage_data = (
        [round((value / total) * 100, 1) for value in count_data]
        if total
        else [0.0] * len(labels)
    )
    return labels, count_data, percentage_data


GEO_LEVEL_LABELS = {
    "province": "Province",
    "district": "District",
    "constituency": "Constituency",
    "ward": "Ward",
    "ea": "EA",
}


def _build_geo_level_counts(filtered_qs, *, field_name, output_key):
    level_label = GEO_LEVEL_LABELS.get(field_name)
    rows = (
        filtered_qs.exclude(**{f"{field_name}__isnull": True})
        .exclude(**{field_name: ""})
        .annotate(name=Trim(field_name))
        .values("name")
        .annotate(count=Count("pk"))
        .order_by(Lower("name"))
    )
    counts = {}
    for row in rows:
        normalized = " ".join((row.get("name") or "").split())
        normalized_lc = normalized.lower()
        if not normalized or normalized_lc in {"nan", "none", "null", "na", "n/a"}:
            continue
        if level_label:
            normalized = _resolve_geo_name(normalized, level_label)
        counts[normalized] = counts.get(normalized, 0) + (row.get("count") or 0)
    return [
        {output_key: name, "count": count}
        for name, count in sorted(counts.items(), key=lambda item: item[0].lower())
    ]


def _build_map_hierarchy_counts(filtered_qs):
    province_sums = _build_geo_level_counts(
        filtered_qs, field_name="province", output_key="province_name"
    )
    district_sums = _build_geo_level_counts(
        filtered_qs, field_name="district", output_key="district_name"
    )
    constituency_sums = _build_geo_level_counts(
        filtered_qs, field_name="constituency", output_key="constituency_name"
    )
    ward_sums = _build_geo_level_counts(
        filtered_qs, field_name="ward", output_key="ward_name"
    )
    ea_sums = _build_geo_level_counts(
        filtered_qs, field_name="ea", output_key="ea_name"
    )
    return {
        "map_total_events": filtered_qs.count(),
        "map_province_sums": province_sums,
        "map_district_sums": district_sums,
        "map_constituency_sums": constituency_sums,
        "map_ward_sums": ward_sums,
        "map_ea_sums": ea_sums,
    }


def _build_map_counts(filtered_qs):
    hierarchy = _build_map_hierarchy_counts(filtered_qs)
    province_data = [
        {"name": row.get("province_name") or "", "count": row.get("count") or 0}
        for row in hierarchy["map_province_sums"]
    ]
    district_data = [
        {"name": row.get("district_name") or "", "count": row.get("count") or 0}
        for row in hierarchy["map_district_sums"]
    ]
    return province_data, district_data


def _gps_cache_key(*, event_type, qs_signature, limit, offset, pii):
    """Build a PII-aware cache key for a GPS points response."""
    raw = json.dumps(
        {
            "et": event_type,
            "qs": qs_signature,
            "limit": limit,
            "offset": offset,
            "pii": bool(pii),
        },
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return f"gps_points:{event_type}:{digest}"


def _build_gps_points(
    filtered_qs,
    *,
    gps_field,
    event_type,
    limit=GPS_DEFAULT_LIMIT,
    offset=0,
    cache_signature=None,
):
    """Extract parsed GPS points from a queryset.

    Returns a tuple ``(points, total_count, truncated)``.
    Each point is a dict with id/event_type/latitude/longitude/altitude/accuracy
    plus province/district/ward/ea (when those columns exist on the model).
    """
    try:
        limit = max(0, int(limit))
    except (TypeError, ValueError):
        limit = GPS_DEFAULT_LIMIT
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0

    if cache_signature is not None:
        cache_key = _gps_cache_key(
            event_type=event_type,
            qs_signature=cache_signature,
            limit=limit,
            offset=offset,
            pii=True,
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    else:
        cache_key = None

    model = filtered_qs.model
    model_field_names = {f.name for f in model._meta.get_fields()}
    geo_fields = [
        f for f in ("province", "district", "ward", "ea") if f in model_field_names
    ]
    only_fields = ["id", gps_field] + geo_fields

    qs = filtered_qs.exclude(**{f"{gps_field}__isnull": True}).exclude(
        **{gps_field: ""}
    )
    total_with_gps = qs.count()
    # Clear any select_related so .only() doesn't collide with traversed FKs
    # (e.g. PregnancyOutcome's `cluster` FK). GPS extraction needs only scalar fields.
    qs = qs.select_related(None).only(*only_fields).order_by("id")
    if offset:
        qs = qs[offset:]
    # Iterate in chunks; collect up to limit valid (parseable) points.
    points = []
    for record in qs.iterator(chunk_size=500):
        if len(points) >= limit:
            break
        raw = getattr(record, gps_field, None)
        parsed = parse_geopoint(raw)
        if not parsed:
            continue
        point = {
            "id": f"{event_type}_{record.pk}",
            "event_type": event_type,
            "latitude": parsed["latitude"],
            "longitude": parsed["longitude"],
        }
        if parsed.get("altitude") is not None:
            point["altitude"] = parsed["altitude"]
        if parsed.get("accuracy") is not None:
            point["accuracy"] = parsed["accuracy"]
        for f in geo_fields:
            point[f] = (getattr(record, f, "") or "").strip()
        points.append(point)

    truncated = total_with_gps > (offset + len(points))
    result = (points, total_with_gps, truncated)
    if cache_key:
        cache.set(cache_key, result, timeout=GPS_CACHE_TIMEOUT)
    return result


def _gps_request_params(request):
    """Parse common GPS query parameters from a request."""
    include = (request.GET.get("include_gps_points") or "").lower() == "true"
    try:
        limit = int(request.GET.get("gps_limit") or GPS_DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = GPS_DEFAULT_LIMIT
    try:
        offset = int(request.GET.get("gps_offset") or 0)
    except (TypeError, ValueError):
        offset = 0
    return include, max(0, min(limit, GPS_DEFAULT_LIMIT)), max(0, offset)


def _qs_cache_signature(filtered_qs):
    """Build a deterministic signature for a queryset to use in cache keys."""
    try:
        return str(filtered_qs.query)
    except Exception:
        return None


def _attach_gps_payload(payload, request, filtered_qs, *, gps_field, event_type):
    """Attach GPS-related fields to a map API payload, applying PII gating."""
    include, limit, offset = _gps_request_params(request)
    if not include:
        return payload
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated and user.has_perm("va_analytics.view_pii")):
        logger.info(
            "PII GPS access denied",
            extra={
                "user_id": getattr(user, "id", None),
                "path": request.path,
                "event_type": event_type,
            },
        )
        payload["gps_points"] = []
        payload["gps_total"] = 0
        payload["gps_truncated"] = False
        payload["gps_pii_denied"] = True
        return payload
    points, total, truncated = _build_gps_points(
        filtered_qs,
        gps_field=gps_field,
        event_type=event_type,
        limit=limit,
        offset=offset,
        cache_signature=_qs_cache_signature(filtered_qs),
    )
    payload["gps_points"] = points
    payload["gps_total"] = total
    payload["gps_truncated"] = truncated
    payload["gps_pii_denied"] = False
    return payload


def _apply_geography_filter(qs, filter_state):
    geography_field_map = {
        "province": "province",
        "district": "district",
        "constituency": "constituency",
        "ward": "ward",
        "ea": "ea",
    }
    geography_level = filter_state.get("geography_level")
    geography_value = filter_state.get("geography_value")
    geo_field = geography_field_map.get(geography_level)
    if geo_field and geography_value:
        return qs.filter(**{f"{geo_field}__iexact": geography_value})
    return qs


def build_pregnancy_outcomes_qs(request):
    qs = PregnancyOutcome.objects.select_related("cluster").all()
    qs = restrict_queryset_to_user_locations(qs, getattr(request, "user", None))
    filter_state = get_pregnancy_outcomes_filter_state(request)

    pregnancy_outcome = filter_state["pregnancy_outcome"]
    if pregnancy_outcome:
        _po_lower = pregnancy_outcome.lower()
        if _po_lower in ("live birth", "born alive"):
            qs = qs.filter(
                Q(PO_46__icontains="live")
                | Q(PO_46__icontains="born alive")
                | (
                    (Q(PO_46__isnull=True) | Q(PO_46=""))
                    & (
                        Q(po_group__icontains="live")
                        | Q(po_group__icontains="born alive")
                    )
                )
            )
        elif _po_lower in ("stillbirth", "still birth", "born dead"):
            qs = qs.filter(
                Q(PO_46__icontains="still")
                | Q(PO_46__icontains="born dead")
                | (
                    (Q(PO_46__isnull=True) | Q(PO_46=""))
                    & (
                        Q(po_group__icontains="still")
                        | Q(po_group__icontains="born dead")
                    )
                )
            )
        else:
            qs = qs.filter(po_group__iexact=pregnancy_outcome)
    qs = _apply_geography_filter(qs, filter_state)

    time_preset = filter_state["time_preset"]
    start_datetime_raw = filter_state["start_datetime"]
    end_datetime_raw = filter_state["end_datetime"]

    now = timezone.localtime(timezone.now())
    start_dt = None
    end_dt = None

    if time_preset == "last_30_days":
        start_dt = now - timedelta(days=30)
        end_dt = now
    elif time_preset == "last_7_days":
        start_dt = now - timedelta(days=7)
        end_dt = now
    elif time_preset == "last_24_hours":
        start_dt = now - timedelta(hours=24)
        end_dt = now
    elif time_preset == "custom":
        start_dt = _parse_iso_datetime(start_datetime_raw)
        end_dt = _parse_iso_datetime(end_datetime_raw)
        if start_dt and end_dt and start_dt > end_dt:
            start_dt, end_dt = end_dt, start_dt

    if start_dt or end_dt:
        date_fields = ("PO_41", "submissiondate", "today", "start")
        if start_dt:
            start_token = start_dt.strftime("%Y-%m-%d")
            start_filter = Q()
            for field_name in date_fields:
                start_filter |= Q(**{f"{field_name}__gte": start_token})
            qs = qs.filter(start_filter)
        if end_dt:
            end_token = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            end_filter = Q()
            for field_name in date_fields:
                end_filter |= Q(**{f"{field_name}__lt": end_token})
            qs = qs.filter(end_filter)

    return qs


def build_pregnancy_qs(request):
    # Pregnancy model currently stores geography and person fields directly,
    # so there are no FK relations to eagerly load.
    qs = Pregnancy.objects.all()
    qs = restrict_queryset_to_user_locations(qs, getattr(request, "user", None))
    filter_state = get_pregnancy_outcomes_filter_state(request)
    qs = _apply_geography_filter(qs, filter_state)

    time_preset = filter_state["time_preset"]
    start_datetime_raw = filter_state["start_datetime"]
    end_datetime_raw = filter_state["end_datetime"]

    now = timezone.localtime(timezone.now())
    start_dt = None
    end_dt = None

    if time_preset == "last_30_days":
        start_dt = now - timedelta(days=30)
        end_dt = now
    elif time_preset == "last_7_days":
        start_dt = now - timedelta(days=7)
        end_dt = now
    elif time_preset == "last_24_hours":
        start_dt = now - timedelta(hours=24)
        end_dt = now
    elif time_preset == "custom":
        start_dt = _parse_iso_datetime(start_datetime_raw)
        end_dt = _parse_iso_datetime(end_datetime_raw)
        if start_dt and end_dt and start_dt > end_dt:
            start_dt, end_dt = end_dt, start_dt

    if start_dt or end_dt:
        qs = qs.exclude(PE_09A__isnull=True).exclude(PE_09A="")
        if start_dt:
            qs = qs.filter(PE_09A__gte=start_dt.strftime("%Y-%m-%d"))
        if end_dt:
            qs = qs.filter(PE_09A__lt=(end_dt + timedelta(days=1)).strftime("%Y-%m-%d"))

    return qs


def _compute_death_age_years(dob_value, dod_value):
    dob_dt = _parse_outcome_datetime(dob_value)
    dod_dt = _parse_outcome_datetime(dod_value)
    if dob_dt is None or dod_dt is None:
        return None, None
    if dod_dt < dob_dt:
        return None, None
    delta_days = (dod_dt - dob_dt).days
    return (delta_days / 365.25), delta_days


def _matches_death_age_group(age_group, dob_value, dod_value):
    age_years, age_days = _compute_death_age_years(dob_value, dod_value)
    if age_days is None:
        return False

    group = (age_group or "").strip().lower()
    if group == "neonate":
        return age_days < 28
    if group == "child":
        return age_days >= 28 and age_years <= 12
    if group == "adult":
        return age_years > 12
    return True


def get_deaths_filter_state(request):
    time_preset = (request.GET.get("time_preset") or "all_time").strip()
    allowed_presets = {
        "all_time",
        "last_30_days",
        "last_7_days",
        "last_24_hours",
        "custom",
    }
    if time_preset not in allowed_presets:
        time_preset = "all_time"

    sex = (request.GET.get("sex") or "").strip()
    age_group = (request.GET.get("age_group") or "").strip().lower()
    if age_group not in {"", "neonate", "child", "adult"}:
        age_group = ""

    map_view = (request.GET.get("map_view") or "Province").strip().title()
    if map_view not in {"Province", "District", "Constituency", "Ward", "Ea", "EA"}:
        map_view = "Province"
    if map_view == "Ea":
        map_view = "EA"

    return {
        "time_preset": time_preset,
        "start_datetime": _normalize_date_input(request.GET.get("start_datetime")),
        "end_datetime": _normalize_date_input(request.GET.get("end_datetime")),
        "sex": sex,
        "age_group": age_group,
        "place_of_death": (request.GET.get("place_of_death") or "").strip(),
        "coded_only": (request.GET.get("coded_only") or "").strip().lower(),
        "geography_level": (request.GET.get("geography_level") or "").strip().lower(),
        "geography_value": (
            request.GET.get("geography_value")
            or request.GET.get("geography")
            or request.GET.get("location")
            or ""
        ).strip(),
        "map_view": map_view,
    }


def build_deaths_qs(request, *, apply_time_filter=True):
    # Death model stores geography/person fields directly as text, so there are
    # currently no FK relationships to eagerly load.
    qs = Death.objects.all()
    qs = restrict_queryset_to_user_locations(qs, getattr(request, "user", None))

    filter_state = get_deaths_filter_state(request)

    sex = filter_state["sex"]
    if sex:
        qs = qs.filter(DE_05__iexact=sex)

    place_of_death = filter_state["place_of_death"]
    if place_of_death:
        qs = qs.filter(DE_07__icontains=place_of_death)

    coded_only = filter_state["coded_only"]
    if coded_only in {"1", "true", "yes", "on"}:
        qs = (
            qs.exclude(DE_15__isnull=True)
            .exclude(DE_15="")
            .exclude(DE_15__iexact="unknown")
            .exclude(DE_15__iexact="unk")
            .exclude(DE_15__iexact="na")
            .exclude(DE_15__iexact="n/a")
            .exclude(DE_15__iexact="not known")
        )

    qs = _apply_geography_filter(qs, filter_state)

    if apply_time_filter:
        time_preset = filter_state["time_preset"]
        start_datetime_raw = filter_state["start_datetime"]
        end_datetime_raw = filter_state["end_datetime"]
        now = timezone.localtime(timezone.now())
        start_dt = None
        end_dt = None

        if time_preset == "last_30_days":
            start_dt = now - timedelta(days=30)
            end_dt = now
        elif time_preset == "last_7_days":
            start_dt = now - timedelta(days=7)
            end_dt = now
        elif time_preset == "last_24_hours":
            start_dt = now - timedelta(hours=24)
            end_dt = now
        elif time_preset == "custom":
            start_dt = _parse_iso_datetime(start_datetime_raw)
            end_dt = _parse_iso_datetime(end_datetime_raw)
            if start_dt and end_dt and start_dt > end_dt:
                start_dt, end_dt = end_dt, start_dt

        if start_dt or end_dt:
            qs = qs.exclude(DE_06__isnull=True).exclude(DE_06="")
            if start_dt:
                qs = qs.filter(DE_06__gte=start_dt.strftime("%Y-%m-%d"))
            if end_dt:
                qs = qs.filter(
                    DE_06__lt=(end_dt + timedelta(days=1)).strftime("%Y-%m-%d")
                )

    age_group = filter_state["age_group"]
    if age_group in {"neonate", "child", "adult"}:
        matched_keys = []
        for row in qs.values("key", "DE_04", "DE_06").iterator():
            if _matches_death_age_group(age_group, row.get("DE_04"), row.get("DE_06")):
                matched_keys.append(row["key"])
        qs = qs.filter(key__in=matched_keys)

    return qs


def _build_deaths_summary_cards(filtered_qs):
    rows = filtered_qs.values(
        "submissiondate", "today", "start", "DE_06", "DE_04"
    ).iterator()

    last_data_update = None
    last_death_date = None
    under_5_count = 0
    age_values = []
    delay_days = []
    total_events = filtered_qs.count()

    for row in rows:
        candidate_update = (
            _parse_outcome_datetime(row.get("submissiondate"))
            or _parse_outcome_datetime(row.get("today"))
            or _parse_outcome_datetime(row.get("start"))
        )
        if candidate_update and (
            last_data_update is None or candidate_update > last_data_update
        ):
            last_data_update = candidate_update

        dod_dt = _parse_outcome_datetime(row.get("DE_06"), end_of_day=True)
        if dod_dt and (last_death_date is None or dod_dt > last_death_date):
            last_death_date = dod_dt

        report_dt = (
            _parse_outcome_datetime(row.get("submissiondate"), end_of_day=True)
            or _parse_outcome_datetime(row.get("today"), end_of_day=True)
            or _parse_outcome_datetime(row.get("start"), end_of_day=True)
        )
        if dod_dt and report_dt:
            delay = (report_dt.date() - dod_dt.date()).days
            if delay >= 0:
                delay_days.append(delay)

        age_years, age_days = _compute_death_age_years(
            row.get("DE_04"), row.get("DE_06")
        )
        if age_days is None:
            continue
        age_values.append(age_years)
        if age_years < 5:
            under_5_count += 1

    under_5_pct = (
        round((under_5_count / total_events) * 100, 1) if total_events else 0.0
    )
    mean_age = round(sum(age_values) / len(age_values), 1) if age_values else None
    median_delay = round(float(median(delay_days)), 1) if delay_days else None

    return {
        "death_card_last_data_update": (
            last_data_update.strftime("%Y-%m-%d") if last_data_update else "N/A"
        ),
        "death_card_last_death_date": (
            last_death_date.strftime("%Y-%m-%d") if last_death_date else "N/A"
        ),
        "death_card_total_events": total_events,
        "death_card_mean_age": mean_age,
        "death_card_under_5_pct": under_5_pct,
        "death_card_median_delay_days": median_delay,
    }


def _with_valid_death_date(qs, *, source_field="DE_06", alias="death_date"):
    # Death dates are text and may contain invalid sentinel values like "nan".
    # Restrict to parseable YYYY-MM-DD* tokens before DB cast to avoid DataError.
    return (
        qs.exclude(**{f"{source_field}__isnull": True})
        .exclude(**{source_field: ""})
        .exclude(**{f"{source_field}__iexact": "nan"})
        .exclude(**{f"{source_field}__iexact": "nat"})
        .exclude(**{f"{source_field}__iexact": "none"})
        .exclude(**{f"{source_field}__iexact": "null"})
        .filter(**{f"{source_field}__regex": r"^\d{4}-\d{2}-\d{2}.*$"})
        .annotate(
            **{alias: Cast(Substr(source_field, 1, 10), output_field=DateField())}
        )
        .exclude(**{f"{alias}__isnull": True})
    )


def _build_deaths_trend_series(filtered_qs):
    return _build_text_date_trend_series(
        filtered_qs,
        date_field="DE_06",
        hard_start_date=TREND_SUBMISSION_HARD_START_DATE,
    )


def _build_pregnancy_summary_cards(filtered_qs):
    # Single aggregate query replaces three separate evaluations (aggregate,
    # count, and iterator loop for last_event_date).
    agg = filtered_qs.aggregate(
        latest_updated=Max("updated"),
        latest_created=Max("created"),
        latest_submission=Max("submissiondate"),
        total=Count("pk"),
    )
    latest_update_dt = agg.get("latest_updated") or agg.get("latest_created")
    total_events = agg.get("total") or 0

    last_event_dt = _parse_outcome_datetime(
        agg.get("latest_submission"), end_of_day=True
    )

    mean_age, _hiv_positive_pct = _build_pregnancy_kpis(filtered_qs)

    return {
        "card_last_data_update": (
            timezone.localtime(latest_update_dt).strftime("%Y-%m-%d")
            if latest_update_dt
            else "N/A"
        ),
        "card_last_event_date": (
            last_event_dt.strftime("%Y-%m-%d") if last_event_dt else "N/A"
        ),
        "card_number_of_events": total_events,
        "card_mean_age": mean_age,
    }


def _build_pregnancy_trend_series(filtered_qs):
    return _build_text_date_trend_series(
        filtered_qs,
        date_field="PE_09A",
        hard_start_date=PREGNANCY_TREND_HARD_START_DATE,
    )


def _build_pregnancy_ga_anc_points(filtered_qs):
    max_anc_visits = 15
    points = []
    for row in filtered_qs.values(
        "PE_09A", "submissiondate", "today", "start", "PE_22"
    ).iterator():
        lmp_dt = _parse_outcome_datetime(row.get("PE_09A"))
        detection_dt = (
            _parse_outcome_datetime(row.get("submissiondate"))
            or _parse_outcome_datetime(row.get("today"))
            or _parse_outcome_datetime(row.get("start"))
        )
        if lmp_dt is None or detection_dt is None or detection_dt <= lmp_dt:
            continue
        try:
            anc_visits = int(row.get("PE_22"))
        except (TypeError, ValueError):
            continue
        if anc_visits < 0:
            continue
        if anc_visits > max_anc_visits:
            anc_visits = max_anc_visits

        ga_weeks = int((detection_dt - lmp_dt).days / 7)
        if ga_weeks < 1:
            ga_weeks = 1
        elif ga_weeks > 40:
            ga_weeks = 40
        points.append({"x": ga_weeks, "y": anc_visits})

    return {
        "points": points,
        "x_min": 1,
        "x_max": 40,
    }


def _build_pregnancy_ga_detection_distribution(filtered_qs):
    # GA at detection is derived as weeks between LMP (PE_09A) and
    # detection/registration date (submissiondate, fallback today/start).
    min_week = 1
    max_week = 40
    bins = dict.fromkeys(range(min_week, max_week + 1), 0)

    rows = filtered_qs.values("PE_09A", "submissiondate", "today", "start").iterator()
    for row in rows:
        lmp_dt = _parse_outcome_datetime(row.get("PE_09A"))
        detection_dt = (
            _parse_outcome_datetime(row.get("submissiondate"))
            or _parse_outcome_datetime(row.get("today"))
            or _parse_outcome_datetime(row.get("start"))
        )
        if lmp_dt is None or detection_dt is None:
            continue
        if detection_dt <= lmp_dt:
            continue

        ga_weeks = int((detection_dt - lmp_dt).days / 7)
        if ga_weeks < min_week:
            ga_weeks = min_week
        elif ga_weeks > max_week:
            ga_weeks = max_week
        bins[ga_weeks] += 1

    labels = [str(week) for week in range(min_week, max_week + 1)]
    counts = [bins[week] for week in range(min_week, max_week + 1)]
    return labels, counts


def _build_pregnancy_kpis(filtered_qs):
    # Use running sum/count instead of accumulating a list — avoids allocating
    # one float per row in memory for potentially large datasets.
    age_sum = 0.0
    age_count = 0
    rows = filtered_qs.values(
        "PE_07A", "PE_07", "submissiondate", "today", "start"
    ).iterator()
    for row in rows:
        direct_age = row.get("PE_07A")
        if direct_age is not None:
            try:
                parsed_age = float(direct_age)
            except (TypeError, ValueError):
                parsed_age = None
            if parsed_age is not None and parsed_age >= 0:
                age_sum += parsed_age
                age_count += 1
                continue

        dob_dt = _parse_outcome_datetime(row.get("PE_07"))
        event_dt = (
            _parse_outcome_datetime(row.get("submissiondate"))
            or _parse_outcome_datetime(row.get("today"))
            or _parse_outcome_datetime(row.get("start"))
        )
        if dob_dt is None or event_dt is None or event_dt < dob_dt:
            continue
        age_sum += (event_dt - dob_dt).days / 365.25
        age_count += 1

    mean_age = round(age_sum / age_count, 1) if age_count else 0.0

    hiv_known_filter = (
        ~Q(PE_23__isnull=True)
        & ~Q(PE_23="")
        & ~Q(PE_23__iexact="unknown")
        & ~Q(PE_23__iexact="unk")
        & ~Q(PE_23__iexact="na")
        & ~Q(PE_23__iexact="n/a")
        & ~Q(PE_23__iexact="not known")
    )
    hiv_positive_filter = hiv_known_filter & (
        Q(PE_23__icontains="positive")
        | Q(PE_23__iexact="pos")
        | Q(PE_23__iexact="hiv+")
        | Q(PE_23__iexact="yes")
    )
    hiv_agg = filtered_qs.aggregate(
        hiv_known=Count("pk", filter=hiv_known_filter),
        hiv_positive=Count("pk", filter=hiv_positive_filter),
    )
    hiv_known = hiv_agg["hiv_known"] or 0
    hiv_positive = hiv_agg["hiv_positive"] or 0
    hiv_positive_pct = round((hiv_positive / hiv_known) * 100, 1) if hiv_known else 0.0
    return mean_age, hiv_positive_pct


class DashboardAPIView(APIView):
    def get(self, request, format=None):
        start_date = request.query_params.get("start_date") or "1901-01-01"
        end_date = request.query_params.get("end_date") or datetime.today().strftime(
            "%Y-%m-%d"
        )
        cause_of_death = request.query_params.get("cause_of_death") or None
        region_of_interest = request.query_params.get("region_of_interest") or None
        age = request.query_params.get("age") or None
        sex = request.query_params.get("sex") or None
        source = request.query_params.get("source") or None
        compare_by = request.query_params.get("compare_by") or "province"
        filtered_qs = get_filtered_va_queryset(request)

        data = load_va_data(
            request.user,
            start_date=start_date,
            end_date=end_date,
            cause_of_death=cause_of_death,
            region_of_interest=region_of_interest,
            age=age,
            sex=sex,
            source=source,
            compare_by=compare_by,
            filtered_qs=filtered_qs,
        )
        _attach_gps_payload(
            data,
            request,
            filtered_qs,
            gps_field="geopoint",
            event_type="verbal_autopsy",
        )
        return Response(data)


class DashboardView(CustomAuthMixin, PermissionRequiredMixin, RedirectView):
    permanent = False
    query_string = True
    permission_required = "va_analytics.view_dashboard"

    def get_redirect_url(self, *args, **kwargs):
        return f"{reverse('va_analytics:outcomes-dashboard')}?tab=pregnancies"


dashboard_view = DashboardView.as_view()


class DashboardMapView(CustomAuthMixin, PermissionRequiredMixin, RedirectView):
    permanent = False
    query_string = True
    permission_required = "va_analytics.view_dashboard"

    def get_redirect_url(self, *args, **kwargs):
        return f"{reverse('va_analytics:outcomes-dashboard')}?tab=pregnancies"


dashboard_map_view = DashboardMapView.as_view()


class BaselineCensusDashboardView(
    CustomAuthMixin, PermissionRequiredMixin, TemplateView
):
    template_name = "va_analytics/baseline_census_dashboard.html"
    permission_required = "va_analytics.view_dashboard"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        raw_tab = (self.request.GET.get("tab") or "overview").strip().lower()
        context["baseline_active_tab"] = (
            raw_tab
            if raw_tab in {"overview", "table", "chart", "social_mob"}
            else "overview"
        )
        return context


baseline_census_dashboard_view = BaselineCensusDashboardView.as_view()


class BaselineCensusOverviewFilterDataView(NationalOperationalFilterData):
    """Baseline-census overview filter data.

    When the "Current Census" filter is on, the overview KPI cards are gated by
    the current-census province configuration on top of the always-on activated
    province filter, so the overview agrees with the Census Table and Census
    Chart tabs.
    """

    def get(self, request, *args, **kwargs):
        from va_explorer.home.services.nov import (
            get_national_operational_view_data,
        )
        from va_explorer.va_data_management.utils.baseline_census import (
            get_baseline_census_target_totals,
            get_dashboard_excluded_census_provinces,
        )

        kpis_only = str(request.GET.get("kpis_only", "")).strip() in {
            "1",
            "true",
            "True",
        }
        bc_current = str(request.GET.get("bc_current", "")).strip() in (
            "1",
            "true",
            "True",
        )
        if not kpis_only:
            return super().get(request, *args, **kwargs)

        preset, start_dt, end_dt, _has_custom = parse_filter_range(request)
        location_level, location_value = parse_location_filter(request)
        excluded = get_dashboard_excluded_census_provinces(include_current=bc_current)
        data = get_national_operational_view_data(
            start_dt=start_dt,
            end_dt=end_dt,
            request=request,
            location_level=location_level,
            location_value=location_value,
            include_chart_series=False,
            census_province_exclude=excluded,
        )
        kpis = data.get("kpis", {})
        # Real population/household estimates for the Enumeration Coverage
        # Summary chart, scoped the same way as the Census Table.
        kpis["targets"] = get_baseline_census_target_totals(
            excluded_provinces=excluded,
            location_level=location_level,
            location_value=location_value,
        )
        return JsonResponse({"kpis": kpis})


baseline_census_overview_filter_data_view = (
    BaselineCensusOverviewFilterDataView.as_view()
)


class BaselineCensusOverviewMapDataView(HomeOverviewMapData):
    """Baseline-census namespaced overview map data endpoint."""


baseline_census_overview_map_data_view = BaselineCensusOverviewMapDataView.as_view()


class BaselineCensusOverviewKpisComponent(CustomAuthMixin, TemplateView):
    template_name = "va_analytics/components/_baseline_overview_kpis_component.html"


baseline_census_overview_kpis_component_view = (
    BaselineCensusOverviewKpisComponent.as_view()
)


def normalize_ea_to_geoid(raw):
    """Resolve any model's EA reference to the canonical 11-digit geoid.

    Census models store the enumeration area inconsistently: ``Household.ea`` and
    ``SRSClusterLocation.name`` are bare digit strings (sometimes missing the
    leading zero), while ``Pregnancy``/``Death``/``PregnancyOutcome`` use an
    ``EA_`` prefix. ``SRSEAPopulationTarget.geoid`` is the canonical 11-digit,
    zero-padded form. Normalize everything to that so counts can be joined.
    """
    if not raw:
        return ""
    text = str(raw).strip().upper()
    if text.startswith("EA_"):
        text = text[3:]
    return text.zfill(11) if text.isdigit() else text


_BASELINE_CENSUS_VITAL_FORMS = ("pregnancy", "pregnancy_outcome", "death")
_BASELINE_CENSUS_GEO_FIELDS = ("province", "district", "constituency", "ward", "ea")
_BASELINE_CENSUS_TARGET_FIELDS = (
    "geoid",
    "province_name",
    "district_name",
    "constituency_name",
    "ward_name",
)


def _baseline_geo_key(value):
    if value is None:
        return ""
    from va_explorer.va_data_management.utils.loading import normalize_string

    return normalize_string(str(value)).strip().lower()


def _baseline_odk_location_choice_lookup():
    cache_key = "baseline_census:odk_location_choice_lookup:v1"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    from va_explorer.va_data_management.models import ODKFormChoice
    from va_explorer.va_data_management.utils.loading import (
        normalize_string,
        normalize_value,
    )

    normalized_forms = {normalize_string(form) for form in _BASELINE_CENSUS_VITAL_FORMS}
    normalized_fields = {
        normalize_string(field) for field in _BASELINE_CENSUS_GEO_FIELDS
    }
    lookup = {}
    choices = ODKFormChoice.objects.filter(form_name__in=normalized_forms).values(
        "form_name", "field_name", "value", "label"
    )
    for choice in choices:
        form_name = normalize_string(choice["form_name"])
        field_name = normalize_string(choice["field_name"])
        if field_name not in normalized_fields:
            continue
        label = (choice["label"] or "").strip()
        if not label:
            continue
        mapping = lookup.setdefault((form_name, field_name), {})
        mapping[normalize_value(choice["value"])] = label
        mapping[_baseline_geo_key(label)] = label

    if lookup:
        cache.set(cache_key, lookup, 300)
    return lookup


def _resolve_baseline_odk_location_value(choice_lookup, form_name, field_name, value):
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""

    from va_explorer.va_data_management.utils.loading import (
        normalize_string,
        normalize_value,
    )

    mapping = choice_lookup.get(
        (normalize_string(form_name), normalize_string(field_name)), {}
    )
    return (
        mapping.get(normalize_value(text))
        or mapping.get(_baseline_geo_key(text))
        or text
    )


def _baseline_target_by_geoid(target_rows):
    return {
        normalize_ea_to_geoid(row.get("geoid")): row
        for row in target_rows
        if normalize_ea_to_geoid(row.get("geoid"))
    }


def _resolve_vital_event_geoid(row, form_name, target_by_geoid, choice_lookup):
    resolved = {
        field: _resolve_baseline_odk_location_value(
            choice_lookup, form_name, field, row.get(field)
        )
        for field in _BASELINE_CENSUS_GEO_FIELDS
    }
    geoid = normalize_ea_to_geoid(resolved.get("ea") or row.get("ea"))
    target = target_by_geoid.get(geoid)
    if not target:
        return ""

    checks = (
        ("province", "province_name"),
        ("district", "district_name"),
        ("constituency", "constituency_name"),
        ("ward", "ward_name"),
    )
    for source_field, target_field in checks:
        source_value = resolved.get(source_field)
        if source_value and _baseline_geo_key(source_value) != _baseline_geo_key(
            target.get(target_field)
        ):
            return ""
    return geoid


def _vital_event_counts_by_geoid(
    model,
    form_name,
    start_iso,
    end_iso,
    date_filter,
    target_rows,
    include_cluster=False,
):
    target_by_geoid = _baseline_target_by_geoid(target_rows)
    if not target_by_geoid:
        return {}

    choice_lookup = _baseline_odk_location_choice_lookup()
    fields = list(_BASELINE_CENSUS_GEO_FIELDS)
    if include_cluster:
        fields.append("cluster__name")

    counts = {}
    rows = (
        date_filter(model.objects.all(), "submissiondate", start_iso, end_iso)
        .values(*fields)
        .annotate(cnt=Count("pk"))
    )
    for row in rows:
        geoid = ""
        if include_cluster:
            geoid = normalize_ea_to_geoid(row.get("cluster__name"))
        if geoid not in target_by_geoid:
            geoid = _resolve_vital_event_geoid(
                row, form_name, target_by_geoid, choice_lookup
            )
        if geoid:
            counts[geoid] = counts.get(geoid, 0) + row["cnt"]
    return counts


def _resolve_household_geoid(row, *, cluster_key, ea_key, target_by_geoid):
    """Resolve household-derived records to a known target geoid.

    Imported household data can have a missing cluster while still carrying the
    raw EA value. Prefer the linked cluster when it joins to a filtered target
    row, then fall back to ``Household.ea``.
    """
    cluster_geoid = normalize_ea_to_geoid(row.get(cluster_key))
    if cluster_geoid in target_by_geoid:
        return cluster_geoid

    ea_geoid = normalize_ea_to_geoid(row.get(ea_key))
    if ea_geoid in target_by_geoid:
        return ea_geoid
    return ""


def _household_counts_by_geoid(start_iso, end_iso, date_filter, target_rows):
    target_by_geoid = _baseline_target_by_geoid(target_rows)
    if not target_by_geoid:
        return {}

    from va_explorer.va_data_management.models import Household

    counts = {}
    rows = (
        date_filter(Household.objects.all(), "submissiondate", start_iso, end_iso)
        .values("cluster__name", "ea")
        .annotate(
            hh_enumerated=Count("pk"),
            refusals=Count(
                "pk",
                filter=Q(consent__iexact="No") | Q(result_list__iexact="Refused"),
            ),
            non_contact=Count("pk", filter=Q(result_list__iexact="Non-contact")),
        )
    )
    for row in rows:
        geoid = _resolve_household_geoid(
            row,
            cluster_key="cluster__name",
            ea_key="ea",
            target_by_geoid=target_by_geoid,
        )
        if not geoid:
            continue
        acc = counts.setdefault(
            geoid, {"hh_enumerated": 0, "refusals": 0, "non_contact": 0}
        )
        acc["hh_enumerated"] += row["hh_enumerated"]
        acc["refusals"] += row["refusals"]
        acc["non_contact"] += row["non_contact"]
    return counts


def _household_member_counts_by_geoid(start_iso, end_iso, date_filter, target_rows):
    target_by_geoid = _baseline_target_by_geoid(target_rows)
    if not target_by_geoid:
        return {}

    from va_explorer.va_data_management.models import HouseholdMember

    counts = {}
    rows = (
        date_filter(
            HouseholdMember.objects.exclude(household__isnull=True),
            "household__submissiondate",
            start_iso,
            end_iso,
        )
        .values("household__cluster__name", "household__ea")
        .annotate(cnt=Count("pk"))
    )
    for row in rows:
        geoid = _resolve_household_geoid(
            row,
            cluster_key="household__cluster__name",
            ea_key="household__ea",
            target_by_geoid=target_by_geoid,
        )
        if geoid:
            counts[geoid] = counts.get(geoid, 0) + row["cnt"]
    return counts


def _unmatched_household_member_counts(start_iso, end_iso, date_filter, target_rows):
    """Household members whose EA is absent from the target table.

    Mirrors :func:`_household_member_counts_by_geoid` but keeps the rows it
    *cannot* resolve, tagged with the household's denormalised administrative
    text so the chart can still attribute them to a province/district/etc.
    """
    target_by_geoid = _baseline_target_by_geoid(target_rows)
    if not target_by_geoid:
        return []

    from va_explorer.va_data_management.models import HouseholdMember

    rows = (
        date_filter(
            HouseholdMember.objects.exclude(household__isnull=True),
            "household__submissiondate",
            start_iso,
            end_iso,
        )
        .values(
            "household__cluster__name",
            "household__ea",
            "household__province",
            "household__district",
            "household__constituency",
            "household__ward",
        )
        .annotate(cnt=Count("pk"))
    )
    unmatched = []
    for row in rows:
        geoid = _resolve_household_geoid(
            row,
            cluster_key="household__cluster__name",
            ea_key="household__ea",
            target_by_geoid=target_by_geoid,
        )
        if geoid:
            continue
        unmatched.append(
            {
                "province": row["household__province"],
                "district": row["household__district"],
                "constituency": row["household__constituency"],
                "ward": row["household__ward"],
                "cnt": row["cnt"],
            }
        )
    return unmatched


def _unmatched_household_counts(start_iso, end_iso, date_filter, target_rows):
    """Households whose EA is absent from the target table.

    Companion to :func:`_unmatched_household_member_counts` for the Households
    indicator. Counts are tagged with the household's administrative text.
    """
    target_by_geoid = _baseline_target_by_geoid(target_rows)
    if not target_by_geoid:
        return []

    from va_explorer.va_data_management.models import Household

    rows = (
        date_filter(Household.objects.all(), "submissiondate", start_iso, end_iso)
        .values("cluster__name", "ea", "province", "district", "constituency", "ward")
        .annotate(cnt=Count("pk"))
    )
    unmatched = []
    for row in rows:
        geoid = _resolve_household_geoid(
            row,
            cluster_key="cluster__name",
            ea_key="ea",
            target_by_geoid=target_by_geoid,
        )
        if geoid:
            continue
        unmatched.append(
            {
                "province": row["province"],
                "district": row["district"],
                "constituency": row["constituency"],
                "ward": row["ward"],
                "cnt": row["cnt"],
            }
        )
    return unmatched


class BaselineCensusProvinceTableComponent(CustomAuthMixin, TemplateView):
    template_name = "va_analytics/components/_baseline_census_province_table.html"

    _VALID_SORT_FIELDS = {
        "province",
        "district",
        "ward",
        "ea",
        "hh_estimate",
        "hh_enumerated",
        "hh_coverage",
        "pop_estimate",
        "pop_enumerated",
        "pop_coverage",
        "refusals",
        "non_contact",
        "total_deaths",
        "total_pregnancies",
        "total_preg_outcomes",
    }
    _LOCATION_FIELD_MAP = {
        "province": "province_name",
        "district": "district_name",
        "constituency": "constituency_name",
        "ward": "ward_name",
        "ea": "geoid",
    }
    # EA-row columns that aggregate by summing across rows. Coverage columns
    # are excluded — they are recomputed from the summed numerator/denominator.
    _SUM_FIELDS = (
        "hh_estimate",
        "hh_enumerated",
        "pop_estimate",
        "pop_enumerated",
        "refusals",
        "non_contact",
        "total_deaths",
        "total_pregnancies",
        "total_preg_outcomes",
    )

    def _build_census_rows(
        self,
        *,
        bc_current=False,
        location_level="",
        location_value="",
        bc_province="",
        bc_district="",
        bc_ward="",
        bc_ea="",
        start_iso=None,
        end_iso=None,
    ):
        from va_explorer.va_data_management.models import (
            Death,
            Pregnancy,
            PregnancyOutcome,
            SRSEAPopulationTarget,
        )
        from va_explorer.va_data_management.utils.baseline_census import (
            get_dashboard_excluded_census_provinces,
        )

        # --- Enrichment counts, keyed by canonical geoid (one query per model) ---
        all_target_rows_for_geo = list(
            SRSEAPopulationTarget.objects.all().values(*_BASELINE_CENSUS_TARGET_FIELDS)
        )
        hh_by_geoid = _household_counts_by_geoid(
            start_iso,
            end_iso,
            self._date_filter,
            all_target_rows_for_geo,
        )
        pop_by_geoid = _household_member_counts_by_geoid(
            start_iso,
            end_iso,
            self._date_filter,
            all_target_rows_for_geo,
        )
        po_by_geoid = _vital_event_counts_by_geoid(
            PregnancyOutcome,
            "pregnancy_outcome",
            start_iso,
            end_iso,
            self._date_filter,
            all_target_rows_for_geo,
            include_cluster=True,
        )
        preg_by_geoid = _vital_event_counts_by_geoid(
            Pregnancy,
            "pregnancy",
            start_iso,
            end_iso,
            self._date_filter,
            all_target_rows_for_geo,
        )
        death_by_geoid = _vital_event_counts_by_geoid(
            Death,
            "death",
            start_iso,
            end_iso,
            self._date_filter,
            all_target_rows_for_geo,
        )

        # --- Base rows: every EA in the target table (all 10 provinces) ---
        targets = SRSEAPopulationTarget.objects.all()
        excluded_provinces = get_dashboard_excluded_census_provinces(
            include_current=bc_current
        )
        if excluded_provinces:
            targets = targets.exclude(province_name__in=excluded_provinces)
        lookup = self._LOCATION_FIELD_MAP.get((location_level or "").lower())
        if location_value and lookup:
            targets = targets.filter(**{f"{lookup}__icontains": location_value})
        if bc_province:
            targets = targets.filter(province_name__icontains=bc_province)
        if bc_district:
            targets = targets.filter(district_name__icontains=bc_district)
        if bc_ward:
            targets = targets.filter(ward_name__icontains=bc_ward)
        if bc_ea:
            targets = targets.filter(geoid__icontains=bc_ea)
        targets = targets.values(
            "geoid",
            "province_name",
            "district_name",
            "ward_name",
            "household_target",
            "population_target",
        )

        rows = []
        for target in targets:
            geoid = target["geoid"]
            hh = hh_by_geoid.get(geoid, {})
            hh_enumerated = hh.get("hh_enumerated", 0)
            pop_enumerated = pop_by_geoid.get(geoid, 0)
            hh_estimate = target["household_target"]
            pop_estimate = target["population_target"]
            hh_coverage = self._coverage_pct(hh_enumerated, hh_estimate)
            pop_coverage = self._coverage_pct(pop_enumerated, pop_estimate)
            rows.append(
                {
                    "province": target["province_name"] or "—",
                    "district": target["district_name"] or "—",
                    "ward": target["ward_name"] or "—",
                    "ea": geoid,
                    "hh_estimate": hh_estimate,
                    "hh_enumerated": hh_enumerated,
                    "hh_coverage": hh_coverage,
                    "hh_coverage_band": coverage_band(hh_coverage),
                    "pop_estimate": pop_estimate,
                    "pop_enumerated": pop_enumerated,
                    "pop_coverage": pop_coverage,
                    "pop_coverage_band": coverage_band(pop_coverage),
                    "refusals": hh.get("refusals", 0),
                    "non_contact": hh.get("non_contact", 0),
                    "total_deaths": death_by_geoid.get(geoid, 0),
                    "total_pregnancies": preg_by_geoid.get(geoid, 0),
                    "total_preg_outcomes": po_by_geoid.get(geoid, 0),
                }
            )
        return rows

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        req = self.request

        bc_sort = self._pick(
            req.GET.get("bc_sort", "province"), self._VALID_SORT_FIELDS, "province"
        )
        bc_dir = self._pick(req.GET.get("bc_dir", "asc"), {"asc", "desc"}, "asc")
        bc_province = req.GET.get("bc_province_search", "").strip()
        bc_district = req.GET.get("bc_district_search", "").strip()
        bc_ward = req.GET.get("bc_ward_search", "").strip()
        bc_ea = req.GET.get("bc_ea_search", "").strip()
        try:
            bc_page = max(1, int(req.GET.get("bc_page", 1) or 1))
        except (TypeError, ValueError):
            bc_page = 1

        location_level = req.GET.get("location_level", "").strip().lower()
        location_value = req.GET.get("location_value", "").strip()

        # The dashboard always filters to activated provinces. The "Current
        # Census" checkbox narrows further to the provinces marked current in
        # the admin panel config.
        bc_current = str(req.GET.get("bc_current", "")).strip() in (
            "1",
            "true",
            "True",
        )

        # Resolve the date window. Only an explicit preset or custom range adds
        # a time bound; the checkbox no longer applies a rolling window.
        preset, start_dt, end_dt, has_custom = parse_filter_range(req)
        explicit_time = has_custom or preset in {"30", "7", "24"}
        if not explicit_time:
            start_dt = None
            end_dt = None
        start_iso, end_iso = self._iso_bounds(start_dt, end_dt)

        rows = self._build_census_rows(
            bc_current=bc_current,
            location_level=location_level,
            location_value=location_value,
            bc_province=bc_province,
            bc_district=bc_district,
            bc_ward=bc_ward,
            bc_ea=bc_ea,
            start_iso=start_iso,
            end_iso=end_iso,
        )

        # Grand total spans every filtered EA; ward total spans the EAs of the
        # single ward shown on the active page.
        bc_grand_total = self._summarize_census_rows(rows)
        pagination = self._paginate_census_by_ward(rows, bc_page, bc_sort, bc_dir)
        bc_ward_total = self._summarize_census_rows(pagination["rows"])

        context.update(
            {
                "bc_stats": pagination["rows"],
                "bc_sort": bc_sort,
                "bc_dir": bc_dir,
                "bc_province_search": bc_province,
                "bc_district_search": bc_district,
                "bc_ward_search": bc_ward,
                "bc_ea_search": bc_ea,
                "bc_current": bc_current,
                "bc_ward_total": bc_ward_total,
                "bc_grand_total": bc_grand_total,
                "bc_page": pagination["page"],
                "bc_total_pages": pagination["total_pages"],
                "bc_has_prev": pagination["has_prev"],
                "bc_has_next": pagination["has_next"],
                "bc_prev_page": pagination["prev_page"],
                "bc_next_page": pagination["next_page"],
                "bc_visible_count": pagination["visible_count"],
                "bc_total_count": pagination["total_count"],
            }
        )
        return context

    @staticmethod
    def _iso_bounds(start_dt, end_dt):
        """UTC ISO-8601 text bounds for lexical comparison against the ISO
        ``submissiondate`` text fields. Returns ``None`` where unbounded."""
        start_iso = end_iso = None
        if start_dt is not None:
            start_iso = start_dt.astimezone(dt_timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
        if end_dt is not None:
            end_iso = end_dt.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        return start_iso, end_iso

    @staticmethod
    def _date_filter(queryset, field, start_iso, end_iso):
        if start_iso:
            queryset = queryset.filter(**{f"{field}__gte": start_iso})
        if end_iso:
            queryset = queryset.filter(**{f"{field}__lte": end_iso})
        return queryset

    @staticmethod
    def _coverage_pct(numerator, denominator):
        """Coverage percentage, or ``None`` when the target estimate is missing
        or zero (avoids division by zero)."""
        if not denominator:
            return None
        return float(f"{numerator / denominator * 100:.1f}")

    @staticmethod
    def _pick(value, valid_values, default):
        return value if value in valid_values else default

    @staticmethod
    def _sort_rows(rows, sort_key, sort_dir):
        reverse = sort_dir == "desc"
        text_keys = {"province", "district", "ward", "ea"}
        if sort_key in text_keys:
            return sorted(
                rows, key=lambda r: str(r.get(sort_key, "")).lower(), reverse=reverse
            )

        def numeric_key(row):
            val = row.get(sort_key)
            if val is None:
                return -1 if reverse else float("inf")
            try:
                return float(val)
            except (TypeError, ValueError):
                return 0

        return sorted(rows, key=numeric_key, reverse=reverse)

    @classmethod
    def _summarize_census_rows(cls, rows):
        """Totals/averages for a list of EA row dicts, or ``None`` when empty.

        Count columns are summed; coverage columns are recomputed from the
        summed numerator/denominator rather than averaging percentages."""
        if not rows:
            return None
        totals = dict.fromkeys(cls._SUM_FIELDS, 0)
        for row in rows:
            for field in cls._SUM_FIELDS:
                value = row.get(field)
                if value in (None, ""):
                    continue
                try:
                    totals[field] += int(value)
                except (TypeError, ValueError):
                    continue
        hh_coverage = cls._coverage_pct(totals["hh_enumerated"], totals["hh_estimate"])
        pop_coverage = cls._coverage_pct(
            totals["pop_enumerated"], totals["pop_estimate"]
        )
        totals["hh_coverage"] = hh_coverage
        totals["hh_coverage_band"] = coverage_band(hh_coverage)
        totals["pop_coverage"] = pop_coverage
        totals["pop_coverage_band"] = coverage_band(pop_coverage)
        return totals

    @staticmethod
    def _group_rows_by_ward(rows):
        """Group EA rows into ``(province, district, ward)`` buckets,
        preserving first-seen order. Returns a list of ``(key, [rows])``."""
        groups = {}
        order = []
        for row in rows:
            key = (row.get("province"), row.get("district"), row.get("ward"))
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)
        return [(key, groups[key]) for key in order]

    @classmethod
    def _paginate_census_by_ward(cls, rows, page, bc_sort, bc_dir):
        """Paginate EA rows one ward per page.

        EA rows within a ward are sorted by ``bc_sort``/``bc_dir``. The ward
        pages themselves are ordered by location when the sort column is a
        location field, otherwise by each ward's aggregate of the sort column
        (wards with no value sort last)."""
        groups = [
            (key, cls._sort_rows(ward_rows, bc_sort, bc_dir))
            for key, ward_rows in cls._group_rows_by_ward(rows)
        ]
        reverse = bc_dir == "desc"
        if bc_sort in {"province", "district", "ward", "ea"}:
            groups.sort(
                key=lambda item: tuple(str(part or "").lower() for part in item[0]),
                reverse=reverse,
            )
        else:

            def ward_metric(item):
                summary = cls._summarize_census_rows(item[1]) or {}
                value = summary.get(bc_sort)
                if value is None:
                    return float("-inf") if reverse else float("inf")
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return 0.0

            groups.sort(key=ward_metric, reverse=reverse)

        total_pages = max(1, len(groups))
        current_page = min(max(1, page), total_pages)
        ward_rows = groups[current_page - 1][1] if groups else []
        return {
            "rows": ward_rows,
            "page": current_page,
            "total_pages": total_pages,
            "has_prev": current_page > 1,
            "has_next": current_page < total_pages,
            "prev_page": current_page - 1 if current_page > 1 else 1,
            "next_page": (
                current_page + 1 if current_page < total_pages else total_pages
            ),
            "visible_count": len(ward_rows),
            "total_count": len(rows),
        }


baseline_census_province_table_component_view = (
    BaselineCensusProvinceTableComponent.as_view()
)


class BaselineCensusTableCsvExport(
    CsvDownloadPermissionMixin,
    PermissionRequiredMixin,
    BaselineCensusProvinceTableComponent,
):
    permission_required = "va_analytics.view_dashboard"

    _CSV_COLUMNS = (
        ("province", "Province"),
        ("district", "District"),
        ("ward", "Ward"),
        ("ea", "EA"),
        ("hh_estimate", "HH Estimate"),
        ("hh_enumerated", "HH Enumerated"),
        ("hh_coverage", "HH Coverage %"),
        ("pop_estimate", "Pop. Estimate"),
        ("pop_enumerated", "Pop. Enumerated"),
        ("pop_coverage", "Pop. Coverage %"),
        ("refusals", "Refusals"),
        ("non_contact", "Non-Contact"),
        ("total_deaths", "Total Deaths"),
        ("total_pregnancies", "Total Pregnancies"),
        ("total_preg_outcomes", "Total Preg. Outcomes"),
    )

    def get(self, request, *args, **kwargs):
        try:
            current_census = str(
                request.GET.get("current_census", request.GET.get("bc_current", ""))
            ).strip().lower() in {"1", "true", "yes", "on"}
            rows = self._sort_rows(
                self._build_census_rows(bc_current=current_census),
                "province",
                "asc",
            )
            total = self._summarize_census_rows(rows)
            return self._csv_response(rows, total)
        except Exception:
            logger.exception("Baseline Census CSV export failed")
            return JsonResponse(
                {"error": "Unable to export baseline census data."}, status=500
            )

    def _csv_response(self, rows, total):
        filename = timezone.now().strftime("baseline_census_data_%Y%m%d_%H%M%S.csv")
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        writer = csv.writer(response)
        writer.writerow([label for _, label in self._CSV_COLUMNS])
        for row in rows:
            writer.writerow(
                [self._csv_value(row.get(key)) for key, _ in self._CSV_COLUMNS]
            )
        if total:
            writer.writerow(
                [
                    "Grand Total / Average",
                    "",
                    "",
                    "",
                    self._csv_value(total.get("hh_estimate")),
                    self._csv_value(total.get("hh_enumerated")),
                    self._csv_value(total.get("hh_coverage")),
                    self._csv_value(total.get("pop_estimate")),
                    self._csv_value(total.get("pop_enumerated")),
                    self._csv_value(total.get("pop_coverage")),
                    self._csv_value(total.get("refusals")),
                    self._csv_value(total.get("non_contact")),
                    self._csv_value(total.get("total_deaths")),
                    self._csv_value(total.get("total_pregnancies")),
                    self._csv_value(total.get("total_preg_outcomes")),
                ]
            )
        return response

    @staticmethod
    def _csv_value(value):
        return "" if value is None else value


baseline_census_table_csv_export_view = BaselineCensusTableCsvExport.as_view()


# Administrative hierarchy used by the Census Chart drill-down, ordered from
# the broadest level to the narrowest. ``geoid`` is the canonical EA key.
_CENSUS_LEVEL_ORDER = ("province", "district", "constituency", "ward", "ea")
_CENSUS_LEVEL_FIELD = {
    "province": "province_name",
    "district": "district_name",
    "constituency": "constituency_name",
    "ward": "ward_name",
    "ea": "geoid",
}
# Bucket for enumerated records whose EA is missing from the target table.
_UNMATCHED_GROUP_KEY = "Unmatched"


def _new_census_group():
    return {
        "population_estimate": 0,
        "household_estimate": 0,
        "population_enumerated": 0,
        "household_enumerated": 0,
        "death_enumerated": 0,
        "pregnancy_enumerated": 0,
        "pregnancy_outcome_enumerated": 0,
    }


def _attribute_unmatched_counts(
    groups,
    order,
    level,
    parent_filters,
    location_level,
    location_value,
    excluded_provinces,
    *,
    unmatched_members,
    unmatched_households,
):
    """Fold EA-unresolved household counts into the level's groups.

    Rows whose EA is missing from the target table are attributed by the
    household's denormalised province/district/constituency/ward text. They
    land in the matching group when one exists, otherwise in an explicit
    "Unmatched" group, so chart totals never silently lose enumerated records.
    """
    if not unmatched_members and not unmatched_households:
        return

    group_by_norm = {_baseline_geo_key(key): key for key in order}
    excluded_norm = {_baseline_geo_key(name) for name in (excluded_provinces or [])}
    parent_levels = ("province", "district", "constituency", "ward")

    def resolve_group(row):
        # Province exclusion mirrors the target-row filtering.
        if excluded_norm and _baseline_geo_key(row.get("province")) in excluded_norm:
            return None
        # Drill-down parent filters.
        for parent in parent_levels:
            wanted = (parent_filters or {}).get(parent)
            if wanted and _baseline_geo_key(row.get(parent)) != _baseline_geo_key(
                wanted
            ):
                return None
        # Location search filter, applied to the matching admin field.
        if location_value and location_level in parent_levels:
            text = str(row.get(location_level) or "")
            if location_value.lower() not in text.lower():
                return None
        # An unmatched row has no target EA, so at EA granularity it can only
        # land in the explicit "Unmatched" group.
        if level == "ea":
            return _UNMATCHED_GROUP_KEY
        return group_by_norm.get(
            _baseline_geo_key(row.get(level)), _UNMATCHED_GROUP_KEY
        )

    def bucket(group_key):
        acc = groups.get(group_key)
        if acc is None:
            acc = _new_census_group()
            groups[group_key] = acc
            order.append(group_key)
        return acc

    for row in unmatched_members:
        group_key = resolve_group(row)
        if group_key is not None:
            bucket(group_key)["population_enumerated"] += row["cnt"]

    for row in unmatched_households:
        group_key = resolve_group(row)
        if group_key is not None:
            bucket(group_key)["household_enumerated"] += row["cnt"]


def aggregate_census_by_level(
    level,
    parent_filters,
    location_level,
    location_value,
    start_iso,
    end_iso,
    excluded_provinces=None,
):
    """Aggregate baseline-census estimates and enumerated counts to ``level``.

    ``SRSEAPopulationTarget`` holds one row per EA carrying the full
    administrative naming, so it doubles as the ``geoid -> administrative name``
    map. Enumerated counts (households, household members, deaths) are gathered
    once each, keyed by canonical geoid, then rolled up to the requested level
    in a single in-memory pass — no per-group queries.

    ``excluded_provinces`` drops provinces that are not part of the current
    baseline census (used when the "Current Baseline Census" filter is on).

    Returns ``(results, child_level)`` where ``results`` is a list of metric
    dicts and ``child_level`` is the next drill-down level (``None`` at EA).
    """
    from va_explorer.va_data_management.models import (
        Death,
        Pregnancy,
        PregnancyOutcome,
        SRSEAPopulationTarget,
    )

    group_field = _CENSUS_LEVEL_FIELD.get(level, "province_name")
    coverage = BaselineCensusProvinceTableComponent._coverage_pct
    date_filter = BaselineCensusProvinceTableComponent._date_filter

    # --- Target rows: geoid -> administrative names + estimates ---
    targets = SRSEAPopulationTarget.objects.all()
    if excluded_provinces:
        targets = targets.exclude(province_name__in=excluded_provinces)
    # User-facing location scoping, mirroring the Census Table tab. The target
    # table has no Location FK, so it is filtered on its denormalised names
    # rather than via restrict_queryset_to_user_locations().
    location_lookup = _CENSUS_LEVEL_FIELD.get((location_level or "").lower())
    if location_value and location_lookup:
        targets = targets.filter(**{f"{location_lookup}__icontains": location_value})
    # Drill-down parent filters use exact match — values come from chart clicks,
    # not free-text search.
    for parent_level in ("province", "district", "constituency", "ward"):
        value = (parent_filters or {}).get(parent_level)
        if value:
            targets = targets.filter(**{_CENSUS_LEVEL_FIELD[parent_level]: value})

    target_rows = list(
        targets.values(
            "geoid",
            "province_name",
            "district_name",
            "constituency_name",
            "ward_name",
            "household_target",
            "population_target",
        )
    )

    geoid_to_group = {}
    groups = {}
    order = []
    for row in target_rows:
        group_key = (row.get(group_field) or "").strip()
        geoid_to_group[row["geoid"]] = group_key
        acc = groups.get(group_key)
        if acc is None:
            acc = _new_census_group()
            groups[group_key] = acc
            order.append(group_key)
        acc["population_estimate"] += row["population_target"] or 0
        acc["household_estimate"] += row["household_target"] or 0

    # --- Enumerated counts, one query per model, keyed by canonical geoid ---
    hh_by_geoid = _household_counts_by_geoid(
        start_iso,
        end_iso,
        date_filter,
        target_rows,
    )
    pop_by_geoid = _household_member_counts_by_geoid(
        start_iso,
        end_iso,
        date_filter,
        target_rows,
    )
    death_by_geoid = _vital_event_counts_by_geoid(
        Death,
        "death",
        start_iso,
        end_iso,
        date_filter,
        target_rows,
    )
    pregnancy_by_geoid = _vital_event_counts_by_geoid(
        Pregnancy,
        "pregnancy",
        start_iso,
        end_iso,
        date_filter,
        target_rows,
    )
    pregnancy_outcome_by_geoid = _vital_event_counts_by_geoid(
        PregnancyOutcome,
        "pregnancy_outcome",
        start_iso,
        end_iso,
        date_filter,
        target_rows,
        include_cluster=True,
    )

    # --- Roll EA-level counts up to the requested level ---
    for geoid, group_key in geoid_to_group.items():
        acc = groups[group_key]
        acc["household_enumerated"] += hh_by_geoid.get(geoid, {}).get(
            "hh_enumerated", 0
        )
        acc["population_enumerated"] += pop_by_geoid.get(geoid, 0)
        acc["death_enumerated"] += death_by_geoid.get(geoid, 0)
        acc["pregnancy_enumerated"] += pregnancy_by_geoid.get(geoid, 0)
        acc["pregnancy_outcome_enumerated"] += pregnancy_outcome_by_geoid.get(geoid, 0)

    # --- Surface enumerated household data whose EA is absent from the target
    # table. _resolve_household_geoid drops these rows, so they never reach the
    # geoid roll-up above; attributing them by the household's own
    # administrative text keeps the chart total in step with the Enumeration
    # Coverage summary instead of silently losing people. ---
    _attribute_unmatched_counts(
        groups,
        order,
        level,
        parent_filters,
        location_level,
        location_value,
        excluded_provinces,
        unmatched_members=_unmatched_household_member_counts(
            start_iso, end_iso, date_filter, target_rows
        ),
        unmatched_households=_unmatched_household_counts(
            start_iso, end_iso, date_filter, target_rows
        ),
    )

    child_index = _CENSUS_LEVEL_ORDER.index(level) + 1
    child_level = (
        _CENSUS_LEVEL_ORDER[child_index]
        if child_index < len(_CENSUS_LEVEL_ORDER)
        else None
    )

    results = []
    for group_key in order:
        acc = groups[group_key]
        results.append(
            {
                "label": group_key or "—",
                "level": level,
                "value": group_key,
                "population_estimate": acc["population_estimate"],
                "population_enumerated": acc["population_enumerated"],
                "population_coverage": coverage(
                    acc["population_enumerated"], acc["population_estimate"]
                ),
                "household_estimate": acc["household_estimate"],
                "household_enumerated": acc["household_enumerated"],
                "household_coverage": coverage(
                    acc["household_enumerated"], acc["household_estimate"]
                ),
                # Deaths, pregnancies and pregnancy outcomes have no target in
                # SRSEAPopulationTarget, so their estimate is None. None keeps
                # coverage division-safe — _coverage_pct treats a falsy
                # denominator as "no coverage".
                "death_estimate": None,
                "death_enumerated": acc["death_enumerated"],
                "death_coverage": coverage(acc["death_enumerated"], None),
                "pregnancy_estimate": None,
                "pregnancy_enumerated": acc["pregnancy_enumerated"],
                "pregnancy_coverage": coverage(acc["pregnancy_enumerated"], None),
                "pregnancy_outcome_estimate": None,
                "pregnancy_outcome_enumerated": acc["pregnancy_outcome_enumerated"],
                "pregnancy_outcome_coverage": coverage(
                    acc["pregnancy_outcome_enumerated"], None
                ),
                "has_children": (
                    child_level is not None and group_key != _UNMATCHED_GROUP_KEY
                ),
            }
        )

    # Stable, user-friendly ordering by the geography label, with the
    # "Unmatched" bucket pinned last so it never displaces real geographies.
    results.sort(
        key=lambda row: (
            row["value"] == _UNMATCHED_GROUP_KEY,
            str(row["value"]).lower(),
        )
    )
    return results, child_level


class BaselineCensusChartDataView(CustomAuthMixin, PermissionRequiredMixin, View):
    """JSON endpoint backing the Census Chart tab of the Baseline Census
    Dashboard. Returns estimate/enumerated/coverage metrics for Population,
    Households, Deaths, Pregnancies and Pregnancy Outcomes aggregated to the
    requested administrative level, plus drill-down breadcrumbs.

    The response always carries every indicator; the optional ``indicator``
    query param only validates the caller's request (unsupported values are
    rejected) — the Census Chart sub-tabs render client-side from one payload.
    """

    permission_required = "va_analytics.view_dashboard"
    _PARENT_LEVELS = ("province", "district", "constituency", "ward")
    _CHILD_OF = {
        "province": "district",
        "district": "constituency",
        "constituency": "ward",
        "ward": "ea",
    }
    _SUPPORTED_INDICATORS = (
        "population",
        "households",
        "deaths",
        "pregnancies",
        "pregnancy_outcomes",
    )

    def get(self, request, *args, **kwargs):
        try:
            return self._build_response(request)
        except Exception:
            logger.exception("Census chart data endpoint failed")
            return JsonResponse(
                {"error": "Unable to load census chart data."}, status=500
            )

    def _build_response(self, request):
        from va_explorer.va_data_management.utils.baseline_census import (
            get_dashboard_excluded_census_provinces,
        )

        # Optional indicator filter — validate but do not narrow the payload,
        # which always carries every indicator for instant sub-tab switching.
        indicator = (request.GET.get("indicator", "") or "").strip().lower()
        if indicator and indicator not in self._SUPPORTED_INDICATORS:
            return JsonResponse(
                {"error": f"Unsupported indicator '{indicator}'."}, status=400
            )

        level = BaselineCensusProvinceTableComponent._pick(
            (request.GET.get("level", "province") or "province").strip().lower(),
            set(_CENSUS_LEVEL_ORDER),
            "province",
        )
        parent_filters = {
            parent: request.GET.get(parent, "").strip()
            for parent in self._PARENT_LEVELS
        }
        location_level = request.GET.get("location_level", "").strip().lower()
        location_value = request.GET.get("location_value", "").strip()

        # Date/province resolution mirrors the Census Table tab so the two tabs
        # always agree. Only an explicit preset/custom range adds a time bound.
        # The dashboard always filters to activated provinces, and the "Current
        # Census" checkbox narrows further to provinces marked current.
        bc_current = str(request.GET.get("bc_current", "")).strip() in (
            "1",
            "true",
            "True",
        )
        preset, start_dt, end_dt, has_custom = parse_filter_range(request)
        explicit_time = has_custom or preset in {"30", "7", "24"}
        if not explicit_time:
            start_dt = None
            end_dt = None
        start_iso, end_iso = BaselineCensusProvinceTableComponent._iso_bounds(
            start_dt, end_dt
        )

        excluded_provinces = get_dashboard_excluded_census_provinces(
            include_current=bc_current
        )
        results, child_level = aggregate_census_by_level(
            level,
            parent_filters,
            location_level,
            location_value,
            start_iso,
            end_iso,
            excluded_provinces=excluded_provinces,
        )

        return JsonResponse(
            {
                "level": level,
                "child_level": child_level,
                "indicator": indicator or None,
                "breadcrumbs": self._build_breadcrumbs(parent_filters),
                "results": results,
            }
        )

    def _build_breadcrumbs(self, parent_filters):
        """Drill path as clickable steps. Each crumb carries the level to load
        and the cumulative parent filters needed to reload at that step."""
        crumbs = [{"label": "All Provinces", "level": "province", "parents": {}}]
        cumulative = {}
        for parent in self._PARENT_LEVELS:
            value = parent_filters.get(parent)
            if not value:
                break
            cumulative = {**cumulative, parent: value}
            crumbs.append(
                {
                    "label": value,
                    "level": self._CHILD_OF[parent],
                    "parents": dict(cumulative),
                }
            )
        return crumbs


baseline_census_chart_data_view = BaselineCensusChartDataView.as_view()


# ---------------------------------------------------------------------------
# Social Mobilization tab (Baseline Census Dashboard)
# ---------------------------------------------------------------------------
# The Social Mobilization ODK tally is a flat table of TextField counts. Like
# the census tabs it is scoped by user location (province/district), filtered on
# the ISO ``submissiondate``, and rolled up in Python (text fields cannot be
# summed in SQL). Two endpoints back the tab: an HTML component (KPIs + an
# aggregated table + a rumour log) and a JSON chart-data endpoint (drill-down
# bars + a time trend), mirroring the Census Table and Census Chart tabs.

_SOCMOB_LEVEL_ORDER = (
    "province",
    "district",
    "health_facility",
    "village_neighbourhood",
)
_SOCMOB_LEVEL_FIELD = {level: level for level in _SOCMOB_LEVEL_ORDER}
_SOCMOB_CHILD_OF = {
    "province": "district",
    "district": "health_facility",
    "health_facility": "village_neighbourhood",
}
# Numeric tally fields summed during roll-up.
_SOCMOB_SUM_FIELDS = (
    "households_visited",
    "people_over_15",
    "children_5_14",
    "children_12_59months",
    "schools_reached",
    "religious_leaders_reached",
    "refusal_identified",
    "refusal_managed",
    "children_affected",
    "refusals_from_rumor",
)
# Location columns shown on the aggregated table (one row per village).
_SOCMOB_TABLE_GROUP_FIELDS = (
    "province",
    "district",
    "health_facility",
    "village_neighbourhood",
)
# Sort keys allowed on the aggregated table.
_SOCMOB_SORT_FIELDS = frozenset(
    _SOCMOB_TABLE_GROUP_FIELDS
    + _SOCMOB_SUM_FIELDS
    + ("people_reached", "refusal_managed_rate", "rumours_reported")
)


def _socmob_to_int(value):
    """Parse an ODK text count to int; blanks / junk become 0."""
    if value in (None, ""):
        return 0
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def _socmob_rate(numerator, denominator):
    """Refusals managed as a % of refusals identified, or ``None`` when none
    were identified (avoids division by zero)."""
    if not denominator:
        return None
    return float(f"{numerator / denominator * 100:.1f}")


def _socmob_normalize_location_label(value):
    return " ".join(str(value or "").replace("_", " ").split())


def _socmob_normalize_health_facility(value, district):
    facility = _socmob_normalize_location_label(value)
    parent = _socmob_normalize_location_label(district)
    if not facility or not parent:
        return facility

    if facility.casefold().startswith(parent.casefold()):
        remainder = facility[len(parent) :].strip(" -_/")
        if remainder:
            return remainder
    return facility


def _socmob_base_queryset(
    request,
    location_level,
    location_value,
    start_iso,
    end_iso,
    parent_filters=None,
):
    """Location-scoped, date-filtered ``SocialMobilization`` queryset shared by
    every Social Mobilization aggregation."""
    from va_explorer.va_data_management.models import SocialMobilization

    qs = SocialMobilization.objects.all()
    qs = restrict_queryset_to_user_locations(
        qs,
        getattr(request, "user", None),
        field_mapping={"province": "province", "district": "district"},
    )
    qs = BaselineCensusProvinceTableComponent._date_filter(
        qs, "submissiondate", start_iso, end_iso
    )
    # Free-form location scope from the shared filter (icontains, mirrors census).
    level_field = _SOCMOB_LEVEL_FIELD.get((location_level or "").lower())
    if location_value and level_field:
        qs = qs.filter(**{f"{level_field}__icontains": location_value})
    # Drill-down parents are exact matches (values come from chart clicks).
    for parent_level, parent_value in (parent_filters or {}).items():
        field = _SOCMOB_LEVEL_FIELD.get(parent_level)
        if parent_value and field:
            qs = qs.filter(**{field: parent_value})
    return qs


def _socmob_new_group():
    acc = dict.fromkeys(_SOCMOB_SUM_FIELDS, 0)
    acc["rumours_reported"] = 0
    return acc


def _socmob_accumulate(acc, row):
    for field in _SOCMOB_SUM_FIELDS:
        acc[field] += _socmob_to_int(row.get(field))
    if (row.get("rumor_description") or "").strip():
        acc["rumours_reported"] += 1


def _socmob_metrics_from_group(acc):
    """Derive the shared metric dict (sums + derived fields) from an accumulator."""
    people_reached = (
        acc["people_over_15"] + acc["children_5_14"] + acc["children_12_59months"]
    )
    return {
        "households_visited": acc["households_visited"],
        "people_over_15": acc["people_over_15"],
        "children_5_14": acc["children_5_14"],
        "children_12_59months": acc["children_12_59months"],
        "people_reached": people_reached,
        "schools_reached": acc["schools_reached"],
        "religious_leaders_reached": acc["religious_leaders_reached"],
        "refusal_identified": acc["refusal_identified"],
        "refusal_managed": acc["refusal_managed"],
        "refusal_managed_rate": _socmob_rate(
            acc["refusal_managed"], acc["refusal_identified"]
        ),
        "rumours_reported": acc["rumours_reported"],
        "children_affected": acc["children_affected"],
        "refusals_from_rumor": acc["refusals_from_rumor"],
    }


def aggregate_social_mobilization_by_level(
    request, level, parent_filters, location_level, location_value, start_iso, end_iso
):
    """Roll Social Mobilization counts up to ``level`` for the chart drill-down.

    Returns ``(results, child_level)`` where ``child_level`` is the next
    drill-down level (``None`` at village)."""
    group_field = _SOCMOB_LEVEL_FIELD.get(level, "province")
    qs = _socmob_base_queryset(
        request, location_level, location_value, start_iso, end_iso, parent_filters
    )
    rows = qs.values(group_field, *_SOCMOB_SUM_FIELDS, "rumor_description")

    groups = {}
    order = []
    for row in rows:
        key = (row.get(group_field) or "").strip()
        acc = groups.get(key)
        if acc is None:
            acc = _socmob_new_group()
            groups[key] = acc
            order.append(key)
        _socmob_accumulate(acc, row)

    child_index = _SOCMOB_LEVEL_ORDER.index(level) + 1
    child_level = (
        _SOCMOB_LEVEL_ORDER[child_index]
        if child_index < len(_SOCMOB_LEVEL_ORDER)
        else None
    )
    results = []
    for key in order:
        results.append(
            {
                "label": key or "—",
                "level": level,
                "value": key,
                "has_children": child_level is not None,
                **_socmob_metrics_from_group(groups[key]),
            }
        )
    results.sort(key=lambda row: str(row["value"]).lower())
    return results, child_level


def aggregate_social_mobilization_trend(
    request, parent_filters, location_level, location_value, start_iso, end_iso
):
    """Daily totals over the current scope, bucketed by the ``submissiondate``
    date part (YYYY-MM-DD)."""
    qs = _socmob_base_queryset(
        request, location_level, location_value, start_iso, end_iso, parent_filters
    )
    rows = qs.values(
        "submissiondate",
        "people_over_15",
        "children_5_14",
        "children_12_59months",
        "households_visited",
        "refusal_identified",
        "refusal_managed",
    )
    buckets = {}
    for row in rows:
        raw = (row.get("submissiondate") or "").strip()
        if not raw:
            continue
        day = raw[:10]
        acc = buckets.setdefault(
            day,
            {
                "households_visited": 0,
                "people_reached": 0,
                "refusals_identified": 0,
                "refusals_managed": 0,
            },
        )
        acc["households_visited"] += _socmob_to_int(row.get("households_visited"))
        acc["people_reached"] += (
            _socmob_to_int(row.get("people_over_15"))
            + _socmob_to_int(row.get("children_5_14"))
            + _socmob_to_int(row.get("children_12_59months"))
        )
        acc["refusals_identified"] += _socmob_to_int(row.get("refusal_identified"))
        acc["refusals_managed"] += _socmob_to_int(row.get("refusal_managed"))
    return [{"date": day, **buckets[day]} for day in sorted(buckets)]


def aggregate_social_mobilization_table(
    request, location_level, location_value, start_iso, end_iso
):
    """One row per ``(province, district, health_facility, village)`` plus
    scope-wide KPI totals. Returns ``(rows, totals)``."""
    qs = _socmob_base_queryset(
        request, location_level, location_value, start_iso, end_iso
    )
    raw_rows = qs.values(
        *_SOCMOB_TABLE_GROUP_FIELDS, *_SOCMOB_SUM_FIELDS, "rumor_description"
    )

    groups = {}
    order = []
    totals_acc = _socmob_new_group()
    for row in raw_rows:
        key = tuple(
            (row.get(field) or "").strip() for field in _SOCMOB_TABLE_GROUP_FIELDS
        )
        acc = groups.get(key)
        if acc is None:
            acc = _socmob_new_group()
            groups[key] = acc
            order.append(key)
        _socmob_accumulate(acc, row)
        _socmob_accumulate(totals_acc, row)

    rows = []
    for key in order:
        location = dict(zip(_SOCMOB_TABLE_GROUP_FIELDS, key))
        location["health_facility"] = _socmob_normalize_health_facility(
            location["health_facility"], location["district"]
        )
        rows.append({**location, **_socmob_metrics_from_group(groups[key])})
    return rows, _socmob_metrics_from_group(totals_acc)


def _socmob_sort_rows(rows, sort_key, sort_dir):
    reverse = sort_dir == "desc"
    if sort_key in _SOCMOB_TABLE_GROUP_FIELDS:
        return sorted(
            rows, key=lambda r: str(r.get(sort_key, "")).lower(), reverse=reverse
        )

    def numeric_key(row):
        val = row.get(sort_key)
        if val is None:
            return -1 if reverse else float("inf")
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0

    return sorted(rows, key=numeric_key, reverse=reverse)


def _socmob_totals_from_rows(rows):
    totals = {
        "households_visited": 0,
        "people_reached": 0,
        "schools_reached": 0,
        "religious_leaders_reached": 0,
        "refusal_identified": 0,
        "refusal_managed": 0,
        "rumours_reported": 0,
    }
    for row in rows:
        for field in totals:
            totals[field] += _socmob_to_int(row.get(field))
    totals["refusal_managed_rate"] = _socmob_rate(
        totals["refusal_managed"], totals["refusal_identified"]
    )
    return totals


def _socmob_rows_by_district(rows):
    district_pages = []
    district_index = {}
    for row in rows:
        key = (
            str(row.get("province") or "").casefold(),
            str(row.get("district") or "").casefold(),
        )
        if key not in district_index:
            district_index[key] = len(district_pages)
            district_pages.append(
                {
                    "province": row.get("province") or "",
                    "district": row.get("district") or "",
                    "rows": [],
                }
            )
        district_pages[district_index[key]]["rows"].append(row)
    return district_pages


class SocialMobilizationComponentView(
    CustomAuthMixin, PermissionRequiredMixin, TemplateView
):
    """HTML component for the Social Mobilization tab: KPI cards, an aggregated
    table (sortable / searchable / paginated), and a rumour log."""

    template_name = (
        "va_analytics/components/_baseline_social_mobilization_component.html"
    )
    permission_required = "va_analytics.view_dashboard"
    _PAGE_SIZE = 25
    _RUMOUR_LIMIT = 200
    # (sort_key, header label) pairs — order matches the template's <tbody> cells.
    _LOCATION_COLUMNS = (
        ("province", "Province"),
        ("district", "District"),
        ("health_facility", "Health Facility"),
        ("village_neighbourhood", "Village"),
    )
    _METRIC_COLUMNS = (
        ("households_visited", "Households Visited"),
        ("people_reached", "People Reached"),
        ("schools_reached", "Schools"),
        ("religious_leaders_reached", "Religious Leaders"),
        ("refusal_identified", "Refusals Identified"),
        ("refusal_managed", "Refusals Managed"),
        ("refusal_managed_rate", "Managed Rate"),
        ("rumours_reported", "Rumours"),
    )
    _WRAPPING_METRIC_COLUMNS = (
        "households_visited",
        "people_reached",
        "religious_leaders_reached",
        "refusal_identified",
        "refusal_managed",
        "refusal_managed_rate",
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        req = self.request
        location_level, location_value = parse_location_filter(req)
        start_iso, end_iso = self._scoped_date_bounds(req)

        rows, _totals = aggregate_social_mobilization_table(
            req, location_level, location_value, start_iso, end_iso
        )

        sm_q = (req.GET.get("sm_q") or "").strip()
        if sm_q:
            needle = sm_q.lower()
            rows = [
                row
                for row in rows
                if needle
                in " ".join(
                    str(row.get(field, "")) for field in _SOCMOB_TABLE_GROUP_FIELDS
                ).lower()
            ]

        sm_sort = BaselineCensusProvinceTableComponent._pick(
            (req.GET.get("sm_sort") or "province").strip(),
            _SOCMOB_SORT_FIELDS,
            "province",
        )
        sm_dir = BaselineCensusProvinceTableComponent._pick(
            (req.GET.get("sm_dir") or "asc").strip(), {"asc", "desc"}, "asc"
        )
        rows = _socmob_sort_rows(rows, sm_sort, sm_dir)

        total_count = len(rows)
        grand_totals = _socmob_totals_from_rows(rows)
        district_pages = _socmob_rows_by_district(rows)
        page_rows = []
        page_district = None
        page_totals = _socmob_totals_from_rows(page_rows)
        total_pages = max(1, len(district_pages))
        try:
            page = int(req.GET.get("sm_page") or 1)
        except (TypeError, ValueError):
            page = 1
        page = min(max(1, page), total_pages)
        if district_pages:
            district_page = district_pages[page - 1]
            page_rows = district_page["rows"]
            page_district = district_page["district"]
            page_totals = _socmob_totals_from_rows(page_rows)

        context.update(
            {
                "sm_stats": page_rows,
                "sm_totals": grand_totals,
                "sm_grand_totals": grand_totals,
                "sm_page_totals": page_totals,
                "sm_page_district": page_district,
                "sm_location_columns": self._LOCATION_COLUMNS,
                "sm_metric_columns": self._METRIC_COLUMNS,
                "sm_wrapping_metric_columns": self._WRAPPING_METRIC_COLUMNS,
                "sm_rumours": self._rumour_log(
                    req, location_level, location_value, start_iso, end_iso
                ),
                "sm_sort": sm_sort,
                "sm_dir": sm_dir,
                "sm_q": sm_q,
                "sm_page": page,
                "sm_total_pages": total_pages,
                "sm_total_count": total_count,
                "sm_visible_count": len(page_rows),
                "sm_has_prev": page > 1,
                "sm_has_next": page < total_pages,
                "sm_prev_page": page - 1 if page > 1 else 1,
                "sm_next_page": page + 1 if page < total_pages else total_pages,
            }
        )
        return context

    @staticmethod
    def _scoped_date_bounds(request):
        """ISO bounds that only apply when an explicit preset / custom range is
        set, mirroring the Census tabs (default view is all-time)."""
        preset, start_dt, end_dt, has_custom = parse_filter_range(request)
        if not (has_custom or preset in {"30", "7", "24"}):
            start_dt = end_dt = None
        return BaselineCensusProvinceTableComponent._iso_bounds(start_dt, end_dt)

    def _rumour_log(self, request, location_level, location_value, start_iso, end_iso):
        qs = _socmob_base_queryset(
            request, location_level, location_value, start_iso, end_iso
        )
        qs = qs.exclude(rumor_description__isnull=True).exclude(rumor_description="")
        records = qs.values(
            "submissiondate",
            "today",
            "province",
            "district",
            "health_facility",
            "village_neighbourhood",
            "rumor_source",
            "rumor_source_other",
            "rumor_description",
            "children_affected",
            "refusals_from_rumor",
        ).order_by("-submissiondate")[: self._RUMOUR_LIMIT]
        log = []
        for record in records:
            log.append(
                {
                    "date": (record.get("submissiondate") or "")[:10]
                    or (record.get("today") or ""),
                    "province": record.get("province"),
                    "district": record.get("district"),
                    "health_facility": record.get("health_facility"),
                    "village_neighbourhood": record.get("village_neighbourhood"),
                    "source": record.get("rumor_source_other")
                    or record.get("rumor_source"),
                    "description": record.get("rumor_description"),
                    "children_affected": _socmob_to_int(
                        record.get("children_affected")
                    ),
                    "refusals_from_rumor": _socmob_to_int(
                        record.get("refusals_from_rumor")
                    ),
                }
            )
        return log


social_mobilization_component_view = SocialMobilizationComponentView.as_view()


class SocialMobilizationKpisComponentView(
    CustomAuthMixin, PermissionRequiredMixin, TemplateView
):
    """KPI cards for the Social Mobilization tab. Rendered separately from the
    tables so the cards stay visible above the Charts/Tables sub-tabs."""

    template_name = (
        "va_analytics/components/_baseline_social_mobilization_kpis_component.html"
    )
    permission_required = "va_analytics.view_dashboard"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        req = self.request
        location_level, location_value = parse_location_filter(req)
        start_iso, end_iso = SocialMobilizationComponentView._scoped_date_bounds(req)
        _, totals = aggregate_social_mobilization_table(
            req, location_level, location_value, start_iso, end_iso
        )
        context["sm_totals"] = totals
        return context


social_mobilization_kpis_component_view = SocialMobilizationKpisComponentView.as_view()


class SocialMobilizationChartDataView(CustomAuthMixin, PermissionRequiredMixin, View):
    """JSON endpoint backing the Social Mobilization charts: per-location metrics
    with province → district → health facility → village drill-down, plus a
    daily time trend. Mirrors ``BaselineCensusChartDataView``."""

    permission_required = "va_analytics.view_dashboard"
    _PARENT_LEVELS = ("province", "district", "health_facility")

    def get(self, request, *args, **kwargs):
        try:
            return self._build_response(request)
        except Exception:
            logger.exception("Social mobilization chart data endpoint failed")
            return JsonResponse(
                {"error": "Unable to load social mobilization chart data."}, status=500
            )

    def _build_response(self, request):
        level = BaselineCensusProvinceTableComponent._pick(
            (request.GET.get("level", "province") or "province").strip().lower(),
            set(_SOCMOB_LEVEL_ORDER),
            "province",
        )
        parent_filters = {
            parent: request.GET.get(parent, "").strip()
            for parent in self._PARENT_LEVELS
        }
        location_level = request.GET.get("location_level", "").strip().lower()
        location_value = request.GET.get("location_value", "").strip()
        start_iso, end_iso = SocialMobilizationComponentView._scoped_date_bounds(
            request
        )

        results, child_level = aggregate_social_mobilization_by_level(
            request,
            level,
            parent_filters,
            location_level,
            location_value,
            start_iso,
            end_iso,
        )
        trend = aggregate_social_mobilization_trend(
            request, parent_filters, location_level, location_value, start_iso, end_iso
        )
        return JsonResponse(
            {
                "level": level,
                "child_level": child_level,
                "breadcrumbs": self._build_breadcrumbs(parent_filters),
                "results": results,
                "trend": trend,
            }
        )

    def _build_breadcrumbs(self, parent_filters):
        crumbs = [{"label": "All Provinces", "level": "province", "parents": {}}]
        cumulative = {}
        for parent in self._PARENT_LEVELS:
            value = parent_filters.get(parent)
            if not value:
                break
            cumulative = {**cumulative, parent: value}
            crumbs.append(
                {
                    "label": value,
                    "level": _SOCMOB_CHILD_OF[parent],
                    "parents": dict(cumulative),
                }
            )
        return crumbs


social_mobilization_chart_data_view = SocialMobilizationChartDataView.as_view()


class OutcomesDashboardView(CustomAuthMixin, PermissionRequiredMixin, TemplateView):
    template_name = "va_analytics/outcomes_dashboard.html"
    permission_required = "va_analytics.view_dashboard"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = self.request
        with timed_block("outcomes.page.filter_state", request=request):
            filter_state = get_pregnancy_outcomes_filter_state(request)
        raw_tab = (request.GET.get("tab") or "pregnancies").strip().lower()
        tab_aliases = {
            "pregnancy_outcomes": "pregnancy_outcomes",
            "pregnancy-outcomes": "pregnancy_outcomes",
            "outcomes": "pregnancy_outcomes",
            "pregnancies": "pregnancies",
            "pregnancy": "pregnancies",
            "pregnancy_events": "pregnancies",
            "pregnancy-events": "pregnancies",
            "deaths": "deaths",
            "death": "deaths",
            "verbal_autopsies": "verbal_autopsies",
            "verbal-autopsies": "verbal_autopsies",
            "verbal_autopsy": "verbal_autopsies",
            "verbal-autopsy": "verbal_autopsies",
            "autopsies": "verbal_autopsies",
            "va": "verbal_autopsies",
        }
        active_tab = tab_aliases.get(raw_tab, "pregnancies")

        with timed_block("outcomes.page.outcome_options", request=request):
            _db_options = list(
                restrict_queryset_to_user_locations(
                    PregnancyOutcome.objects.all(), request.user
                )
                .exclude(po_group__isnull=True)
                .exclude(po_group="")
                .values_list("po_group", flat=True)
                .distinct()
            )
            outcome_options = sorted(set(_db_options) | {"Live Birth", "Still Birth"})
        with timed_block("outcomes.page.filtered_qs", request=request):
            filtered_qs = build_pregnancy_outcomes_qs(request)
        with timed_block("outcomes.page.summary_cards", request=request):
            summary_cards = _build_outcomes_summary_cards(filtered_qs)
        with timed_block("outcomes.page.trend_series", request=request):
            trend_labels, trend_counts = _build_pregnancy_outcomes_trend_series(
                filtered_qs
            )
        with timed_block("outcomes.page.birth_outcomes", request=request):
            birth_labels, birth_count_data, birth_percentage_data = (
                _build_birth_outcomes_bar_data(filtered_qs)
            )
        with timed_block("outcomes.page.gest_age_distribution", request=request):
            gest_age_labels, gest_age_counts = _build_gestational_age_distribution(
                filtered_qs
            )
        with timed_block("outcomes.page.anc_visits_distribution", request=request):
            anc_visits_labels, anc_visits_counts = _build_anc_visits_distribution(
                filtered_qs
            )
        with timed_block("outcomes.page.place_of_birth_distribution", request=request):
            (
                place_of_birth_labels,
                place_of_birth_count_data,
                place_of_birth_percentage_data,
            ) = _build_place_of_birth_distribution(filtered_qs)
        with timed_block("outcomes.page.map_counts", request=request):
            map_province_counts, map_district_counts = _build_map_counts(filtered_qs)
        with timed_block("outcomes.page.bottom_kpis", request=request):
            mean_age, hiv_positive_pct = _build_outcomes_bottom_kpis(filtered_qs)
        with timed_block("outcomes.page.deaths_qs", request=request):
            deaths_qs = build_deaths_qs(request)
        with timed_block("outcomes.page.deaths_summary_cards", request=request):
            deaths_summary_cards = _build_deaths_summary_cards(deaths_qs)

        context.update(
            {
                "filter_pregnancy_outcome": filter_state["pregnancy_outcome"],
                "filter_time_preset": filter_state["time_preset"],
                "filter_start_datetime": filter_state["start_datetime"],
                "filter_end_datetime": filter_state["end_datetime"],
                "active_tab": active_tab,
                "pregnancy_outcome_options": outcome_options,
                "pregnancy_outcomes_qs": filtered_qs,
                "outcomes_trend_labels": trend_labels,
                "outcomes_trend_counts": trend_counts,
                "birth_outcomes_labels": birth_labels,
                "birth_outcomes_count_data": birth_count_data,
                "birth_outcomes_percentage_data": birth_percentage_data,
                "gestational_age_labels": gest_age_labels,
                "gestational_age_counts": gest_age_counts,
                "anc_visits_labels": anc_visits_labels,
                "anc_visits_counts": anc_visits_counts,
                "place_of_birth_labels": place_of_birth_labels,
                "place_of_birth_count_data": place_of_birth_count_data,
                "place_of_birth_percentage_data": place_of_birth_percentage_data,
                "map_province_counts": map_province_counts,
                "map_district_counts": map_district_counts,
                "filter_map_view": filter_state["map_view"],
                "card_mean_age": mean_age,
                "card_hiv_positive_pct": hiv_positive_pct,
                "deaths_qs": deaths_qs,
                **deaths_summary_cards,
                **summary_cards,
            }
        )
        return context


outcomes_dashboard_view = OutcomesDashboardView.as_view()


class _PregnancyOutcomesBaseAPIView(APIView):
    def _qs(self, request):
        with timed_block("outcomes.api.filtered_qs", request=request):
            return build_pregnancy_outcomes_qs(request)


class PregnancyOutcomesSummaryAPIView(_PregnancyOutcomesBaseAPIView):
    def get(self, request, format=None):
        with timed_block("outcomes.api.summary_cards", request=request):
            payload = _build_outcomes_summary_cards(self._qs(request))
        return Response(payload)


class PregnancyOutcomesTrendAPIView(_PregnancyOutcomesBaseAPIView):
    def get(self, request, format=None):
        with timed_block("outcomes.api.trend_series", request=request):
            labels, counts = _build_pregnancy_outcomes_trend_series(self._qs(request))
        return Response({"labels": labels, "data": counts})


class PregnancyOutcomesBirthOutcomesAPIView(_PregnancyOutcomesBaseAPIView):
    def get(self, request, format=None):
        labels, count_data, percentage_data = _build_birth_outcomes_bar_data(
            self._qs(request)
        )
        return Response(
            {
                "labels": labels,
                "count_data": count_data,
                "percentage_data": percentage_data,
            }
        )


class PregnancyOutcomesGestationalAgeAPIView(_PregnancyOutcomesBaseAPIView):
    def get(self, request, format=None):
        labels, counts = _build_gestational_age_distribution(self._qs(request))
        return Response({"labels": labels, "data": counts})


class PregnancyOutcomesAncVisitsAPIView(_PregnancyOutcomesBaseAPIView):
    def get(self, request, format=None):
        labels, counts = _build_anc_visits_distribution(self._qs(request))
        return Response({"labels": labels, "data": counts})


class PregnancyOutcomesKpisAPIView(_PregnancyOutcomesBaseAPIView):
    def get(self, request, format=None):
        mean_age, hiv_positive_pct = _build_outcomes_bottom_kpis(self._qs(request))
        return Response(
            {
                "mean_age": mean_age,
                "hiv_positive_pct": hiv_positive_pct,
            }
        )


class PregnancyOutcomesPlaceOfBirthAPIView(_PregnancyOutcomesBaseAPIView):
    def get(self, request, format=None):
        labels, count_data, percentage_data = _build_place_of_birth_distribution(
            self._qs(request)
        )
        return Response(
            {
                "labels": labels,
                "count_data": count_data,
                "percentage_data": percentage_data,
            }
        )


class PregnancyOutcomesMapAPIView(_PregnancyOutcomesBaseAPIView):
    gps_field = "gps"
    gps_event_type = "pregnancy_outcome"

    def get(self, request, format=None):
        filter_state = get_pregnancy_outcomes_filter_state(request)
        filtered_qs = self._qs(request)
        with timed_block("outcomes.api.map_hierarchy", request=request):
            hierarchy = _build_map_hierarchy_counts(filtered_qs)
        province_counts = [
            {"name": row.get("province_name") or "", "count": row.get("count") or 0}
            for row in hierarchy["map_province_sums"]
        ]
        district_counts = [
            {"name": row.get("district_name") or "", "count": row.get("count") or 0}
            for row in hierarchy["map_district_sums"]
        ]
        constituency_counts = [
            {"name": row.get("constituency_name") or "", "count": row.get("count") or 0}
            for row in hierarchy["map_constituency_sums"]
        ]
        ward_counts = [
            {"name": row.get("ward_name") or "", "count": row.get("count") or 0}
            for row in hierarchy["map_ward_sums"]
        ]
        ea_counts = [
            {"name": row.get("ea_name") or "", "count": row.get("count") or 0}
            for row in hierarchy["map_ea_sums"]
        ]
        counts_by_view = {
            "Province": province_counts,
            "District": district_counts,
            "Constituency": constituency_counts,
            "Ward": ward_counts,
            "EA": ea_counts,
        }
        counts = counts_by_view.get(filter_state["map_view"], province_counts)
        payload = {
            "map_view": filter_state["map_view"],
            "counts": counts,
            "province_counts": province_counts,
            "district_counts": district_counts,
            "constituency_counts": constituency_counts,
            "ward_counts": ward_counts,
            "ea_counts": ea_counts,
            **hierarchy,
        }
        _attach_gps_payload(
            payload,
            request,
            filtered_qs,
            gps_field=self.gps_field,
            event_type=self.gps_event_type,
        )
        return Response(payload)


class PregnancyDashboardView(OutcomesDashboardView):
    template_name = "va_analytics/pregnancy_dashboard.html"

    def get_context_data(self, **kwargs):
        context = TemplateView.get_context_data(self, **kwargs)
        request = self.request
        filter_state = get_pregnancy_outcomes_filter_state(request)

        filtered_qs = build_pregnancy_qs(request)
        summary_cards = _build_pregnancy_summary_cards(filtered_qs)
        trend_labels, trend_counts = _build_pregnancy_trend_series(filtered_qs)
        gest_age_labels, gest_age_counts = _build_pregnancy_ga_detection_distribution(
            filtered_qs
        )
        map_province_counts, map_district_counts = _build_map_counts(filtered_qs)
        mean_age, _hiv_positive_pct = _build_pregnancy_kpis(filtered_qs)

        context.update(
            {
                "filter_pregnancy_outcome": filter_state["pregnancy_outcome"],
                "filter_time_preset": filter_state["time_preset"],
                "filter_start_datetime": filter_state["start_datetime"],
                "filter_end_datetime": filter_state["end_datetime"],
                "pregnancy_outcome_options": [],
                "outcomes_trend_labels": trend_labels,
                "outcomes_trend_counts": trend_counts,
                "gestational_age_labels": gest_age_labels,
                "gestational_age_counts": gest_age_counts,
                "map_province_counts": map_province_counts,
                "map_district_counts": map_district_counts,
                "filter_map_view": filter_state["map_view"],
                "card_mean_age": mean_age,
                **summary_cards,
            }
        )
        return context


pregnancy_dashboard_view = PregnancyDashboardView.as_view()


class PregnancySummaryAPIView(PregnancyOutcomesSummaryAPIView):
    def get(self, request, format=None):
        return Response(_build_pregnancy_summary_cards(build_pregnancy_qs(request)))


class PregnancyTrendAPIView(PregnancyOutcomesTrendAPIView):
    def get(self, request, format=None):
        labels, counts = _build_pregnancy_trend_series(build_pregnancy_qs(request))
        return Response({"labels": labels, "data": counts})


class PregnancyGestationalAgeDetectionAPIView(PregnancyOutcomesGestationalAgeAPIView):
    def get(self, request, format=None):
        labels, counts = _build_pregnancy_ga_detection_distribution(
            build_pregnancy_qs(request)
        )
        return Response({"labels": labels, "data": counts})


class PregnancyGestationalAgeAncAPIView(PregnancyOutcomesAncVisitsAPIView):
    def get(self, request, format=None):
        return Response(_build_pregnancy_ga_anc_points(build_pregnancy_qs(request)))


class PregnancyMapAPIView(PregnancyOutcomesMapAPIView):
    gps_field = "PE_25"
    gps_event_type = "pregnancy"

    def get(self, request, format=None):
        filter_state = get_pregnancy_outcomes_filter_state(request)
        filtered_qs = build_pregnancy_qs(request)
        hierarchy = _build_map_hierarchy_counts(filtered_qs)
        province_counts = [
            {"name": row.get("province_name") or "", "count": row.get("count") or 0}
            for row in hierarchy["map_province_sums"]
        ]
        district_counts = [
            {"name": row.get("district_name") or "", "count": row.get("count") or 0}
            for row in hierarchy["map_district_sums"]
        ]
        constituency_counts = [
            {"name": row.get("constituency_name") or "", "count": row.get("count") or 0}
            for row in hierarchy["map_constituency_sums"]
        ]
        ward_counts = [
            {"name": row.get("ward_name") or "", "count": row.get("count") or 0}
            for row in hierarchy["map_ward_sums"]
        ]
        ea_counts = [
            {"name": row.get("ea_name") or "", "count": row.get("count") or 0}
            for row in hierarchy["map_ea_sums"]
        ]
        counts_by_view = {
            "Province": province_counts,
            "District": district_counts,
            "Constituency": constituency_counts,
            "Ward": ward_counts,
            "EA": ea_counts,
        }
        counts = counts_by_view.get(filter_state["map_view"], province_counts)
        payload = {
            "map_view": filter_state["map_view"],
            "counts": counts,
            "province_counts": province_counts,
            "district_counts": district_counts,
            "constituency_counts": constituency_counts,
            "ward_counts": ward_counts,
            "ea_counts": ea_counts,
            **hierarchy,
        }
        _attach_gps_payload(
            payload,
            request,
            filtered_qs,
            gps_field=self.gps_field,
            event_type=self.gps_event_type,
        )
        return Response(payload)


class PregnancyEventsDashboardView(OutcomesDashboardView):
    template_name = "va_analytics/pregnancy_events_dashboard.html"


pregnancy_events_dashboard_view = PregnancyEventsDashboardView.as_view()


class PregnancyEventsSummaryAPIView(PregnancyOutcomesSummaryAPIView):
    pass


class PregnancyEventsTrendAPIView(PregnancyOutcomesTrendAPIView):
    pass


class PregnancyEventsBirthOutcomesAPIView(PregnancyOutcomesBirthOutcomesAPIView):
    pass


class PregnancyEventsGestationalAgeAPIView(PregnancyOutcomesGestationalAgeAPIView):
    pass


class PregnancyEventsAncVisitsAPIView(PregnancyOutcomesAncVisitsAPIView):
    pass


class PregnancyEventsKpisAPIView(PregnancyOutcomesKpisAPIView):
    pass


class PregnancyEventsPlaceOfBirthAPIView(PregnancyOutcomesPlaceOfBirthAPIView):
    pass


class PregnancyEventsMapAPIView(PregnancyOutcomesMapAPIView):
    pass


def _validate_deaths_tab(request):
    tab = (request.GET.get("tab") or "").strip()
    if tab and tab != "deaths":
        return Response({"detail": "Invalid tab for deaths endpoint."}, status=400)
    return None


class DeathsTrendAPIView(APIView):
    def get(self, request, format=None):
        invalid_tab_response = _validate_deaths_tab(request)
        if invalid_tab_response is not None:
            return invalid_tab_response
        with timed_block("outcomes.deaths.trend_series", request=request):
            labels, counts = _build_deaths_trend_series(build_deaths_qs(request))
        return Response({"labels": labels, "data": counts})


class DeathsSummaryAPIView(APIView):
    def get(self, request, format=None):
        invalid_tab_response = _validate_deaths_tab(request)
        if invalid_tab_response is not None:
            return invalid_tab_response
        with timed_block("outcomes.deaths.summary_cards", request=request):
            payload = _build_deaths_summary_cards(build_deaths_qs(request))
        return Response(payload)


class DeathsMapAPIView(APIView):
    gps_field = "DE_34"
    gps_event_type = "death"

    def get(self, request, format=None):
        invalid_tab_response = _validate_deaths_tab(request)
        if invalid_tab_response is not None:
            return invalid_tab_response
        map_view = (request.GET.get("map_view") or "Province").strip().title()
        if map_view not in {"Province", "District", "Constituency", "Ward", "Ea", "EA"}:
            map_view = "Province"
        if map_view == "Ea":
            map_view = "EA"
        filtered_qs = build_deaths_qs(request)
        with timed_block("outcomes.deaths.map_hierarchy", request=request):
            hierarchy = _build_map_hierarchy_counts(filtered_qs)
        province_counts = [
            {"name": row.get("province_name") or "", "count": row.get("count") or 0}
            for row in hierarchy["map_province_sums"]
        ]
        district_counts = [
            {"name": row.get("district_name") or "", "count": row.get("count") or 0}
            for row in hierarchy["map_district_sums"]
        ]
        constituency_counts = [
            {"name": row.get("constituency_name") or "", "count": row.get("count") or 0}
            for row in hierarchy["map_constituency_sums"]
        ]
        ward_counts = [
            {"name": row.get("ward_name") or "", "count": row.get("count") or 0}
            for row in hierarchy["map_ward_sums"]
        ]
        ea_counts = [
            {"name": row.get("ea_name") or "", "count": row.get("count") or 0}
            for row in hierarchy["map_ea_sums"]
        ]
        counts_by_view = {
            "Province": province_counts,
            "District": district_counts,
            "Constituency": constituency_counts,
            "Ward": ward_counts,
            "EA": ea_counts,
        }
        counts = counts_by_view.get(map_view, province_counts)
        payload = {
            "map_view": map_view,
            "counts": counts,
            "province_counts": province_counts,
            "district_counts": district_counts,
            "constituency_counts": constituency_counts,
            "ward_counts": ward_counts,
            "ea_counts": ea_counts,
            **hierarchy,
        }
        _attach_gps_payload(
            payload,
            request,
            filtered_qs,
            gps_field=self.gps_field,
            event_type=self.gps_event_type,
        )
        return Response(payload)


def _build_deaths_age_sex_profile(filtered_qs):
    age_groups = [
        "Neonate (< 28 days)",
        "Child (≤ 12 years)",
        "Adult (> 12 years)",
    ]
    male_counts = [0] * len(age_groups)
    female_counts = [0] * len(age_groups)

    def _bucket(age_years, age_days):
        if age_days < 28:
            return 0
        if age_years <= 12:
            return 1
        return 2

    for row in filtered_qs.values("DE_04", "DE_06", "DE_05").iterator():
        age_years, age_days = _compute_death_age_years(
            row.get("DE_04"), row.get("DE_06")
        )
        if age_days is None:
            continue
        idx = _bucket(age_years, age_days)

        sex = (row.get("DE_05") or "").strip().lower()
        if sex.startswith("m"):
            male_counts[idx] += 1
        elif sex.startswith("f"):
            female_counts[idx] += 1

    return {
        "labels": age_groups,
        "male": male_counts,
        "female": female_counts,
    }


class DeathsAgeSexAPIView(APIView):
    def get(self, request, format=None):
        invalid_tab_response = _validate_deaths_tab(request)
        if invalid_tab_response is not None:
            return invalid_tab_response
        return Response(_build_deaths_age_sex_profile(build_deaths_qs(request)))


def _build_deaths_timeliness_distribution(filtered_qs):
    labels = ["0-1", "2-3", "4-7", "8-14", "15-30", ">30"]
    bins = dict.fromkeys(labels, 0)
    delay_days = []

    for row in filtered_qs.values(
        "DE_06", "submissiondate", "today", "start"
    ).iterator():
        dod_dt = _parse_outcome_datetime(row.get("DE_06"), end_of_day=True)
        report_dt = (
            _parse_outcome_datetime(row.get("submissiondate"), end_of_day=True)
            or _parse_outcome_datetime(row.get("today"), end_of_day=True)
            or _parse_outcome_datetime(row.get("start"), end_of_day=True)
        )
        if not dod_dt or not report_dt:
            continue
        delay = (report_dt.date() - dod_dt.date()).days
        if delay < 0:
            continue
        delay_days.append(delay)

        if delay <= 1:
            bins["0-1"] += 1
        elif delay <= 3:
            bins["2-3"] += 1
        elif delay <= 7:
            bins["4-7"] += 1
        elif delay <= 14:
            bins["8-14"] += 1
        elif delay <= 30:
            bins["15-30"] += 1
        else:
            bins[">30"] += 1

    return {
        "labels": labels,
        "data": [bins[label] for label in labels],
        "median_delay_days": (
            round(float(median(delay_days)), 1) if delay_days else None
        ),
    }


class DeathsTimelinessAPIView(APIView):
    def get(self, request, format=None):
        invalid_tab_response = _validate_deaths_tab(request)
        if invalid_tab_response is not None:
            return invalid_tab_response
        return Response(_build_deaths_timeliness_distribution(build_deaths_qs(request)))


def _build_deaths_place_distribution(filtered_qs):
    categories = ["Home", "Facility", "On route", "Other", "Unknown"]
    counts = dict.fromkeys(categories, 0)

    for row in filtered_qs.values("DE_07").iterator():
        raw = (row.get("DE_07") or "").strip().lower()
        if not raw:
            counts["Unknown"] += 1
            continue
        if "home" in raw or "house" in raw:
            counts["Home"] += 1
        elif "route" in raw or "en route" in raw or "on the way" in raw:
            counts["On route"] += 1
        elif (
            "facility" in raw
            or "hospital" in raw
            or "clinic" in raw
            or "health centre" in raw
            or "health center" in raw
        ):
            counts["Facility"] += 1
        elif raw in {"unknown", "unk", "na", "n/a", "not known"}:
            counts["Unknown"] += 1
        else:
            counts["Other"] += 1

    labels = categories
    count_data = [counts[label] for label in labels]
    total = sum(count_data)
    percentage_data = (
        [round((value / total) * 100, 1) for value in count_data]
        if total
        else [0.0] * len(labels)
    )
    return {
        "labels": labels,
        "count_data": count_data,
        "percentage_data": percentage_data,
    }


class DeathsPlaceAPIView(APIView):
    def get(self, request, format=None):
        invalid_tab_response = _validate_deaths_tab(request)
        if invalid_tab_response is not None:
            return invalid_tab_response
        return Response(_build_deaths_place_distribution(build_deaths_qs(request)))


def _build_deaths_top_causes(filtered_qs):
    coded_qs = (
        filtered_qs.exclude(DE_15__isnull=True)
        .exclude(DE_15="")
        .exclude(DE_15__iexact="unknown")
        .exclude(DE_15__iexact="unk")
        .exclude(DE_15__iexact="na")
        .exclude(DE_15__iexact="n/a")
        .exclude(DE_15__iexact="not known")
    )
    cause_rows = (
        coded_qs.values("DE_15")
        .annotate(count=Count("pk"))
        .order_by("-count", "DE_15")[:10]
    )
    labels = [row["DE_15"] for row in cause_rows]
    count_data = [row["count"] for row in cause_rows]
    total = sum(count_data)
    percentage_data = (
        [round((value / total) * 100, 1) for value in count_data]
        if total
        else [0.0] * len(count_data)
    )
    return {
        "has_coded": coded_qs.exists(),
        "labels": labels,
        "count_data": count_data,
        "percentage_data": percentage_data,
    }


def _build_deaths_cause_trend(filtered_qs):
    coded_qs = (
        filtered_qs.exclude(DE_15__isnull=True)
        .exclude(DE_15="")
        .exclude(DE_15__iexact="unknown")
        .exclude(DE_15__iexact="unk")
        .exclude(DE_15__iexact="na")
        .exclude(DE_15__iexact="n/a")
        .exclude(DE_15__iexact="not known")
    )
    if not coded_qs.exists():
        return {"has_coded": False, "labels": [], "datasets": []}

    top_causes = list(
        coded_qs.values("DE_15")
        .annotate(total=Count("pk"))
        .order_by("-total", "DE_15")
        .values_list("DE_15", flat=True)[:5]
    )

    monthly_rows = (
        coded_qs.filter(DE_15__in=top_causes)
        .exclude(submissiondate__isnull=True)
        .exclude(submissiondate="")
        .exclude(submissiondate__iexact="nan")
        .exclude(submissiondate__iexact="nat")
        .exclude(submissiondate__iexact="none")
        .exclude(submissiondate__iexact="null")
        .filter(submissiondate__regex=r"^\d{4}-\d{2}-\d{2}")
        .filter(submissiondate__gte=TREND_SUBMISSION_HARD_START_DATE)
        .annotate(month_key=Substr("submissiondate", 1, 7))
        .values("month_key", "DE_15")
        .annotate(count=Count("pk"))
        .order_by("month_key")
    )

    month_keys = sorted(
        {row["month_key"] for row in monthly_rows if row.get("month_key")}
    )
    month_labels = []
    for mk in month_keys:
        try:
            month_labels.append(datetime.strptime(mk, "%Y-%m").strftime("%b %Y"))
        except ValueError:
            month_labels.append(mk)

    cause_map = {cause: dict.fromkeys(month_keys, 0) for cause in top_causes}
    for row in monthly_rows:
        mk = row.get("month_key")
        cause = row.get("DE_15")
        if mk is None or cause not in cause_map:
            continue
        cause_map[cause][mk] = row.get("count") or 0

    datasets = []
    for cause in top_causes:
        datasets.append(
            {
                "label": cause,
                "data": [cause_map[cause][k] for k in month_keys],
            }
        )

    return {
        "has_coded": True,
        "labels": month_labels,
        "datasets": datasets,
    }


class DeathsTopCausesAPIView(APIView):
    def get(self, request, format=None):
        invalid_tab_response = _validate_deaths_tab(request)
        if invalid_tab_response is not None:
            return invalid_tab_response
        return Response(_build_deaths_top_causes(build_deaths_qs(request)))


class DeathsCauseTrendAPIView(APIView):
    def get(self, request, format=None):
        invalid_tab_response = _validate_deaths_tab(request)
        if invalid_tab_response is not None:
            return invalid_tab_response
        return Response(_build_deaths_cause_trend(build_deaths_qs(request)))


def _build_deaths_signals(request, *, threshold_pct=20.0, baseline_min=5):
    base_qs = build_deaths_qs(request, apply_time_filter=False)
    today = timezone.localdate()

    def _window_qs(days, *, previous=False):
        end = today - timedelta(days=days) if previous else today
        start = end - timedelta(days=days)
        return _with_valid_death_date(
            base_qs, source_field="DE_06", alias="death_date"
        ).filter(death_date__gt=start, death_date__lte=end)

    def _under5_count(qs):
        count = 0
        for row in qs.values("DE_04", "DE_06").iterator():
            age_years, age_days = _compute_death_age_years(
                row.get("DE_04"), row.get("DE_06")
            )
            if age_days is not None and age_years < 5:
                count += 1
        return count

    def _metric(current, baseline):
        diff = current - baseline
        pct = round((diff / baseline) * 100, 1) if baseline > 0 else None
        flag = bool(
            pct is not None and pct > threshold_pct and baseline >= baseline_min
        )
        return {
            "current": current,
            "baseline": baseline,
            "difference": diff,
            "percent_change": pct,
            "flag": flag,
        }

    current_7 = _window_qs(7, previous=False)
    previous_7 = _window_qs(7, previous=True)
    current_30 = _window_qs(30, previous=False)
    previous_30 = _window_qs(30, previous=True)

    all_current_7 = current_7.count()
    all_previous_7 = previous_7.count()
    all_current_30 = current_30.count()
    all_previous_30 = previous_30.count()

    u5_current_7 = _under5_count(current_7)
    u5_previous_7 = _under5_count(previous_7)
    u5_current_30 = _under5_count(current_30)
    u5_previous_30 = _under5_count(previous_30)

    return {
        "threshold_pct": threshold_pct,
        "baseline_min": baseline_min,
        "all_deaths_7d": _metric(all_current_7, all_previous_7),
        "all_deaths_30d": _metric(all_current_30, all_previous_30),
        "under5_deaths_7d": _metric(u5_current_7, u5_previous_7),
        "under5_deaths_30d": _metric(u5_current_30, u5_previous_30),
    }


class DeathsSignalsAPIView(APIView):
    def get(self, request, format=None):
        invalid_tab_response = _validate_deaths_tab(request)
        if invalid_tab_response is not None:
            return invalid_tab_response
        return Response(_build_deaths_signals(request))


class UserSupervisionView(CustomAuthMixin, PermissionRequiredMixin, ListView):
    permission_required = "va_analytics.supervise_users"
    template_name = "va_analytics/user_supervision_view.html"
    model = User

    def get_queryset(self):
        # Restrict to VAs this user can access and prefetch related for performance
        queryset = (
            self.request.user.verbal_autopsies()
            .prefetch_related("location", "causes", "coding_issues")
            .exclude(Id10010="")
        )

        self.filterset = SupervisionFilter(
            data=self.request.GET or None, queryset=queryset
        )

        return self.filterset.qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filterset"] = self.filterset

        # group column(s) - figure out appropriate level of aggregation based on filter
        group_col = context["filterset"].form.data.get("group_col", "interviewer")
        if group_col == "interviewer":
            index_cols = ["interviewer", "facility"]
        elif group_col == "facility":
            index_cols = ["facility"]
        else:
            index_cols = [group_col]

        # sort by chosen field (default is count)
        sort_col = self.request.GET.get("order_by", "Total VAs")
        # if order_by param starts with -, sort in descending order.
        # Otherwise, ascending
        is_ascending = sort_col.startswith("-")
        if is_ascending:
            sort_col = sort_col.lstrip("-")

        all_vas = (
            context["object_list"]
            .only("id", "Id10012", "Id10011", "Id10010")
            .select_related("location")
            .select_related("causes")
            .select_related("coding_issues")
            .values(
                "id",
                "Id10012",
                "Id10011",
                interviewer=F("Id10010"),
                facility=F("location__name"),
                cause=F("causes__cause"),
                errors=Count(
                    F("coding_issues"), filter=Q(coding_issues__severity="error")
                ),
                warnings=Count(
                    F("coding_issues"), filter=Q(coding_issues__severity="warning")
                ),
            )
        )
        va_df = pd.DataFrame(all_vas)

        if not va_df.empty:
            va_df["date"] = get_interview_dates(va_df)
            context["supervision_stats"] = (
                va_df.assign(date=lambda df: df["date"].apply(parse_date))
                .assign(date=lambda df: to_dt(df["date"], errors="coerce"))
                # only analyze vas with valid interview dates
                .query("date == date")
                .assign(
                    week_hash=lambda df: df["date"].dt.isocalendar().week
                    + 52 * df["date"].dt.year
                )
                .groupby(group_col)
                .agg(
                    {
                        "id": "count",
                        "warnings": "sum",
                        "errors": "sum",
                        "week_hash": "nunique",
                        "date": "max",
                    }
                )
                .assign(interview_rate=lambda df: round(df["id"] / df["week_hash"], 2))
                .reset_index()
                .merge(va_df[index_cols].drop_duplicates())
                .assign(date=lambda df: df["date"].dt.date)
                .rename(
                    columns={
                        "id": "Total VAs",
                        "week_hash": "Weeks of Data",
                        "interview_rate": "VAs / week",
                        "date": "Last Interview",
                    }
                )
                .sort_values(by=sort_col, ascending=is_ascending)
            ).to_dict(orient="records")

        return context


user_supervision_view = UserSupervisionView.as_view()
