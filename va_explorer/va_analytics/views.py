import hashlib
import json
import logging
from datetime import datetime, time, timedelta
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
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date as django_parse_date
from django.utils.dateparse import parse_datetime as django_parse_datetime
from django.views.generic import ListView, RedirectView, TemplateView
from numpy import round
from pandas import to_datetime as to_dt
from rest_framework.response import Response
from rest_framework.views import APIView

from va_explorer.users.models import User
from va_explorer.utils.mixins import CustomAuthMixin
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
