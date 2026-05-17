import difflib
import logging
import math
import re
from collections import Counter, defaultdict
from contextlib import suppress
from datetime import date, datetime, time, timedelta

import pandas as pd
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.cache import cache
from django.db import transaction
from django.db.models import (
    Count,
    Max,
    OuterRef,
    Q,
    Subquery,
)
from django.db.models.functions import Substr
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date as django_parse_date
from django.utils.dateparse import parse_datetime as django_parse_datetime
from django.views.generic import TemplateView, View
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from zoneinfo import ZoneInfo

from va_explorer.home.services.cache import (
    HOME_FASTPATH_CACHE_TTL_SECONDS,
    HOME_FASTPATH_TABLE_CACHE_TTL_SECONDS,
)
from va_explorer.home.services.cache import (
    get_cached_home_chart_bundle as _get_cached_home_chart_bundle,
)
from va_explorer.home.services.cache import (
    get_cached_home_kpis as _get_cached_home_kpis,
)
from va_explorer.home.services.cache import (
    get_cached_nov_payload as _get_cached_nov_payload,
)
from va_explorer.home.services.cache import (
    get_cached_trends_bundle as _get_cached_trends_bundle,
)
from va_explorer.home.services.common import (
    coverage_band as _coverage_band_helper,
)
from va_explorer.home.services.common import (
    fastpath_cache_key as _fastpath_cache_key,
)
from va_explorer.home.services.common import (
    paginate_list as _paginate_list,
)
from va_explorer.home.services.common import (
    shallow_downsample as _shallow_downsample,
)
from va_explorer.home.services.map import (
    build_home_combined_map_payload as _build_home_combined_map_payload,
)
from va_explorer.utils.mixins import CustomAuthMixin
from va_explorer.utils.profiling import timed_block
from va_explorer.va_data_management.models import (
    CauseCodingIssue,
    CauseOfDeath,
    CSADailyTracker,
    CSASupervisorTargetMap,
    Location,
    ODKFormChoice,
    Pregnancy,
    PregnancyOutcome,
)
from va_explorer.va_data_management.utils.csa_names import (
    format_csa_display_name,
    normalize_csa_name_for_join,
)
from va_explorer.va_data_management.utils.date_parsing import parse_date
from va_explorer.va_data_management.utils.loading import (
    get_va_summary_stats,
)
from va_explorer.va_data_management.utils.location_access import (
    restrict_queryset_to_user_locations,
)
from va_explorer.vacms.cmsmodels.events import Event

logger = logging.getLogger(__name__)


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


trends_endpoint_view = transaction.non_atomic_requests(Trends.as_view())


class NationalOperationalFilterData(CustomAuthMixin, View):
    def get(self, request, *args, **kwargs):
        if str(request.GET.get("kpis_only", "")).strip() in {"1", "true", "True"}:
            with timed_block("home.nov.filter_payload.kpis_only", request=request):
                payload = {"kpis": _get_cached_home_kpis(request)}
            return JsonResponse(payload)
        with timed_block("home.nov.filter_payload", request=request):
            payload = _get_cached_nov_payload(request)
        return JsonResponse(payload)


national_operational_filter_data_view = transaction.non_atomic_requests(
    NationalOperationalFilterData.as_view()
)


class HomeOverviewMapData(CustomAuthMixin, View):
    def get(self, request, *args, **kwargs):
        cache_key = _fastpath_cache_key(
            "home_overview_map_data",
            request,
            extra={
                "component": "overview_map",
                "schema_version": 3,
                "time_preset": request.GET.get("time_preset", ""),
                "start_datetime": request.GET.get("start_datetime", ""),
                "end_datetime": request.GET.get("end_datetime", ""),
                "map_view": request.GET.get("map_view", ""),
                "geography_level": request.GET.get("geography_level", ""),
                "geography_value": request.GET.get("geography_value", ""),
                "include_gps_points": request.GET.get("include_gps_points", ""),
                "gps_limit": request.GET.get("gps_limit", ""),
                "gps_offset": request.GET.get("gps_offset", ""),
            },
        )
        payload = cache.get(cache_key)
        if payload is None:
            payload = _build_home_combined_map_payload(request)
            cache.set(cache_key, payload, timeout=HOME_FASTPATH_CACHE_TTL_SECONDS)
        return JsonResponse(payload)


home_overview_map_data_view = transaction.non_atomic_requests(
    HomeOverviewMapData.as_view()
)


# ---------------------------------------------------------------------------
# Rumor Details - real CSA Daily Tracker implementation
# ---------------------------------------------------------------------------

RUMOR_SOURCE_LABELS = {
    "01": "Social Media",
    "1": "Social Media",
    "02": "Community Group",
    "2": "Community Group",
    "03": "Offline Media",
    "3": "Offline Media",
    "04": "Other",
    "4": "Other",
}

_RUMOR_POSITIVE = {"01", "1", "yes", "true"}
_RUMOR_NEGATIVE = {"02", "0", "2", "no", "false"}


def _is_rumor_positive(value):
    v = (value or "").strip().lower()
    return v in _RUMOR_POSITIVE


def _rumor_date_str(row):
    """Return the best available date string for a CSADailyTracker row dict."""
    for field in ("today", "submissiondate", "start"):
        val = row.get(field)
        if val and str(val).strip() not in {"", "nan", "none", "null"}:
            return str(val).strip()
    return None


def _parse_rumor_date(value):
    if not value:
        return None
    raw = str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null", "nat"}:
        return None
    dt = django_parse_datetime(raw.replace("Z", "+00:00"))
    if dt:
        return timezone.localtime(dt).date() if timezone.is_aware(dt) else dt.date()
    d = django_parse_date(raw[:10])
    return d


def _build_csa_rumor_filter_state(request):
    time_preset = (request.GET.get("time_preset") or "all_time").strip()
    if time_preset not in {
        "all_time",
        "last_30_days",
        "last_7_days",
        "last_24_hours",
        "custom",
    }:
        time_preset = "all_time"
    map_view = (request.GET.get("map_view") or "Province").strip().title()
    if map_view not in {"Province", "District", "Constituency", "Ward", "Ea", "EA"}:
        map_view = "Province"
    if map_view == "Ea":
        map_view = "EA"
    return {
        "time_preset": time_preset,
        "start_datetime": (request.GET.get("start_datetime") or "").strip(),
        "end_datetime": (request.GET.get("end_datetime") or "").strip(),
        "map_view": map_view,
        "geography_level": (request.GET.get("geography_level") or "").strip().lower(),
        "geography_value": (
            request.GET.get("geography_value")
            or request.GET.get("geography")
            or request.GET.get("location")
            or ""
        ).strip(),
    }


def _csa_rumor_filter_window(filter_state):
    """Resolve a (start_dt, end_dt) window from the filter state.

    Returns (None, None) for the all_time preset or unparseable custom dates.
    """
    now = timezone.localtime(timezone.now())
    preset = filter_state.get("time_preset")
    if preset == "last_30_days":
        return now - timedelta(days=30), now
    if preset == "last_7_days":
        return now - timedelta(days=7), now
    if preset == "last_24_hours":
        return now - timedelta(hours=24), now
    if preset == "custom":
        start_dt = end_dt = None
        raw_start = filter_state.get("start_datetime") or ""
        raw_end = filter_state.get("end_datetime") or ""
        if raw_start:
            d = django_parse_date(raw_start)
            if d:
                start_dt = timezone.make_aware(datetime.combine(d, time.min))
        if raw_end:
            d = django_parse_date(raw_end)
            if d:
                end_dt = timezone.make_aware(datetime.combine(d, time.max))
        return start_dt, end_dt
    return None, None


def _build_csa_rumor_queryset(request):
    qs = CSADailyTracker.objects.all()
    qs = restrict_queryset_to_user_locations(qs, getattr(request, "user", None))

    # Filter to only positive rumor reports
    qs = qs.filter(rum__iregex=r"^(01|1|yes|true)$")

    filter_state = _build_csa_rumor_filter_state(request)

    # Geography drill-down
    geo_field_map = {
        "province": "province",
        "district": "district",
        "constituency": "constituency",
        "ward": "ward",
        "ea": "ea",
    }
    geo_level = filter_state["geography_level"]
    geo_value = filter_state["geography_value"]
    if geo_field_map.get(geo_level) and geo_value:
        qs = qs.filter(**{f"{geo_field_map[geo_level]}__iexact": geo_value})

    # Date range filter using today → submissiondate → start
    start_dt, end_dt = _csa_rumor_filter_window(filter_state)

    if start_dt or end_dt:
        date_fields = ("today", "submissiondate", "start")
        if start_dt:
            tok = start_dt.strftime("%Y-%m-%d")
            sq = Q()
            for f in date_fields:
                sq |= Q(**{f"{f}__gte": tok})
            qs = qs.filter(sq)
        if end_dt:
            tok = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            sq = Q()
            for f in date_fields:
                sq |= Q(**{f"{f}__lt": tok})
            qs = qs.filter(sq)

    return qs


def _build_rumor_summary_data(qs):
    agg = qs.aggregate(
        total=Count("pk"),
        latest_today=Max("today"),
        latest_sub=Max("submissiondate"),
    )
    total = agg.get("total") or 0
    latest_date = agg.get("latest_today") or agg.get("latest_sub")
    if latest_date:
        d = _parse_rumor_date(latest_date)
        latest_str = d.strftime("%Y-%m-%d") if d else "N/A"
    else:
        latest_str = "N/A"

    provinces = (
        qs.exclude(province__isnull=True)
        .exclude(province="")
        .values_list("province", flat=True)
        .distinct()
        .count()
    )
    districts = (
        qs.exclude(district__isnull=True)
        .exclude(district="")
        .values_list("district", flat=True)
        .distinct()
        .count()
    )
    eas = (
        qs.exclude(ea__isnull=True)
        .exclude(ea="")
        .values_list("ea", flat=True)
        .distinct()
        .count()
    )
    enumerators = (
        qs.exclude(enumerator__isnull=True)
        .exclude(enumerator="")
        .values_list("enumerator", flat=True)
        .distinct()
        .count()
    )
    with_gps = (
        qs.exclude(hh_gps_latitude__isnull=True).exclude(hh_gps_latitude="").count()
    )
    missing_gps = total - with_gps

    source_row = (
        qs.exclude(sorce_rum__isnull=True)
        .exclude(sorce_rum="")
        .values("sorce_rum")
        .annotate(n=Count("pk"))
        .order_by("-n")
        .first()
    )
    top_source_code = (source_row or {}).get("sorce_rum") or ""
    top_source = RUMOR_SOURCE_LABELS.get(top_source_code, top_source_code or "Unknown")

    gps_coverage_pct = round(with_gps * 100 / total) if total else 0

    total_affected = 0
    for ru_num_val in (
        qs.exclude(ru_num__isnull=True)
        .exclude(ru_num="")
        .values_list("ru_num", flat=True)
        .iterator()
    ):
        with suppress(TypeError, ValueError):
            total_affected += int(float(ru_num_val))

    days_since_last = None
    if latest_date:
        d = _parse_rumor_date(latest_date)
        if d:
            days_since_last = (timezone.localdate() - d).days

    return {
        "card_last_data_update": latest_str,
        "card_number_of_events": total,
        "card_provinces_reporting": provinces,
        "card_districts_reporting": districts,
        "card_eas_reporting": eas,
        "card_enumerators": enumerators,
        "card_with_gps": with_gps,
        "card_missing_gps": missing_gps,
        "card_top_source": top_source,
        "card_gps_coverage_pct": gps_coverage_pct,
        "card_total_affected": total_affected,
        "card_days_since_last": days_since_last,
    }


def _build_rumor_trend_data(qs):
    month_rows = (
        qs.exclude(today__isnull=True)
        .exclude(today="")
        .exclude(today__iexact="nan")
        .exclude(today__iexact="none")
        .filter(today__regex=r"^\d{4}-\d{2}-\d{2}")
        .annotate(month_key=Substr("today", 1, 7))
        .values("month_key")
        .annotate(count=Count("pk"))
        .order_by("month_key")
    )
    labels, counts = [], []
    for row in month_rows:
        mk = row.get("month_key") or ""
        try:
            labels.append(datetime.strptime(mk, "%Y-%m").strftime("%b %Y"))
            counts.append(row["count"])
        except ValueError:
            continue
    return labels, counts


def _normalize_rumor_description(value):
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    if not raw or raw.lower() in {"nan", "none", "null", "nat"}:
        return ""
    return raw


def _build_top_rumors_trend_data(qs, *, top_n=5):
    totals = Counter()
    trend_counts = defaultdict(Counter)
    month_keys = set()

    for row in qs.values("desci", "today", "submissiondate", "start").iterator():
        rumor = _normalize_rumor_description(row.get("desci"))
        if not rumor:
            continue
        event_date = _parse_rumor_date(_rumor_date_str(row))
        if not event_date:
            continue
        month_key = event_date.strftime("%Y-%m")
        totals[rumor] += 1
        trend_counts[rumor][month_key] += 1
        month_keys.add(month_key)

    ordered_months = sorted(month_keys)
    labels = [
        datetime.strptime(month_key, "%Y-%m").strftime("%b %Y")
        for month_key in ordered_months
    ]
    series = [
        {
            "name": rumor,
            "values": [
                trend_counts[rumor].get(month_key, 0) for month_key in ordered_months
            ],
        }
        for rumor, _count in totals.most_common(top_n)
    ]
    return {
        "has_rumors": bool(series),
        "labels": labels,
        "periods": labels,
        "series": series,
    }


def _build_rumor_source_distribution(qs):
    source_rows = (
        qs.exclude(sorce_rum__isnull=True)
        .exclude(sorce_rum="")
        .values("sorce_rum")
        .annotate(count=Count("pk"))
        .order_by("-count")
    )
    labels, counts = [], []
    for row in source_rows:
        code = (row.get("sorce_rum") or "").strip()
        label = RUMOR_SOURCE_LABELS.get(code, f"Other ({code})" if code else "Unknown")
        labels.append(label)
        counts.append(row.get("count") or 0)
    # Add Unknown bucket for null/empty
    unknown_count = qs.filter(Q(sorce_rum__isnull=True) | Q(sorce_rum="")).count()
    if unknown_count:
        labels.append("Unknown")
        counts.append(unknown_count)
    return labels, counts


def _build_rumor_completeness_stats(qs):
    today_date = timezone.localdate()
    total = qs.count()

    def _recent(days):
        cutoff = (today_date - timedelta(days=days)).strftime("%Y-%m-%d")
        return qs.filter(Q(today__gte=cutoff) | Q(submissiondate__gte=cutoff)).count()

    missing_gps = qs.filter(
        Q(hh_gps_latitude__isnull=True) | Q(hh_gps_latitude="")
    ).count()
    missing_desc = qs.filter(Q(desci__isnull=True) | Q(desci="")).count()
    missing_source = qs.filter(Q(sorce_rum__isnull=True) | Q(sorce_rum="")).count()

    return {
        "total": total,
        "recent_7_days": _recent(7),
        "recent_30_days": _recent(30),
        "with_gps": total - missing_gps,
        "missing_gps": missing_gps,
        "missing_description": missing_desc,
        "missing_source": missing_source,
    }


def _build_rumor_map_payload(request):
    from va_explorer.va_analytics.views import _build_map_hierarchy_counts

    filter_state = _build_csa_rumor_filter_state(request)
    filtered_qs = _build_csa_rumor_queryset(request)
    hierarchy = _build_map_hierarchy_counts(filtered_qs)
    province_counts = [
        {"name": r.get("province_name") or "", "count": r.get("count") or 0}
        for r in hierarchy["map_province_sums"]
    ]
    district_counts = [
        {"name": r.get("district_name") or "", "count": r.get("count") or 0}
        for r in hierarchy["map_district_sums"]
    ]
    constituency_counts = [
        {"name": r.get("constituency_name") or "", "count": r.get("count") or 0}
        for r in hierarchy["map_constituency_sums"]
    ]
    ward_counts = [
        {"name": r.get("ward_name") or "", "count": r.get("count") or 0}
        for r in hierarchy["map_ward_sums"]
    ]
    ea_counts = [
        {"name": r.get("ea_name") or "", "count": r.get("count") or 0}
        for r in hierarchy["map_ea_sums"]
    ]
    counts_by_view = {
        "Province": province_counts,
        "District": district_counts,
        "Constituency": constituency_counts,
        "Ward": ward_counts,
        "EA": ea_counts,
    }

    # GPS points – CSADailyTracker has separate lat/lng columns; build them here.
    user = getattr(request, "user", None)
    gps_points = []
    if user and user.is_authenticated and user.has_perm("va_analytics.view_pii"):
        gps_qs = (
            filtered_qs.exclude(hh_gps_latitude__isnull=True)
            .exclude(hh_gps_latitude="")
            .exclude(hh_gps_longitude__isnull=True)
            .exclude(hh_gps_longitude="")
            .values(
                "hh_gps_latitude",
                "hh_gps_longitude",
                "province",
                "district",
                "constituency",
                "ward",
                "ea",
                "enumerator",
                "today",
                "submissiondate",
                "sorce_rum",
                "other_rum",
                "desci",
                "ru_num",
            )[:2000]
        )
        for row in gps_qs:
            try:
                lat = float(row["hh_gps_latitude"])
                lng = float(row["hh_gps_longitude"])
            except (TypeError, ValueError):
                continue
            report_date = _parse_rumor_date(
                row.get("today") or row.get("submissiondate")
            )
            source_code = (row.get("sorce_rum") or "").strip()
            gps_points.append(
                {
                    "lat": lat,
                    "lng": lng,
                    "province": row.get("province") or "",
                    "district": row.get("district") or "",
                    "constituency": row.get("constituency") or "",
                    "ward": row.get("ward") or "",
                    "ea": row.get("ea") or "",
                    "enumerator": row.get("enumerator") or "",
                    "report_date": (
                        report_date.strftime("%Y-%m-%d") if report_date else ""
                    ),
                    "rumor_source_code": source_code,
                    "rumor_source_label": RUMOR_SOURCE_LABELS.get(
                        source_code, source_code or "Unknown"
                    ),
                    "other_rum": row.get("other_rum") or "",
                    "desci": row.get("desci") or "",
                    "ru_num": row.get("ru_num") or "",
                }
            )

    payload = {
        "map_view": filter_state["map_view"],
        "counts": counts_by_view.get(filter_state["map_view"], province_counts),
        "province_counts": province_counts,
        "district_counts": district_counts,
        "constituency_counts": constituency_counts,
        "ward_counts": ward_counts,
        "ea_counts": ea_counts,
        "gps_points": gps_points,
        "gps_total": len(gps_points),
        "gps_truncated": False,
        "gps_pii_denied": not (
            user and user.is_authenticated and user.has_perm("va_analytics.view_pii")
        ),
        **hierarchy,
    }
    return payload


# Category inference taxonomy for description table
_RUMOR_CATEGORY_PATTERNS = [
    (
        "Vaccine / Immunization",
        re.compile(r"vaccin|immuniz|immunis|injection|\bjab\b", re.I),
    ),
    (
        "Disease Outbreak",
        re.compile(
            r"outbreak|epidemic|cholera|malaria|typhoid|infect|ebola|covid|spread", re.I
        ),
    ),
    (
        "Death / Mortality",
        re.compile(r"\bdeath\b|\bdied\b|\bdead\b|\bkilled\b|fatal|mortalit", re.I),
    ),
    ("Family / Household", re.compile(r"family|household|child|children|parent", re.I)),
    (
        "Water / Sanitation",
        re.compile(r"\bwater\b|sanitat|hygiene|\btoilet\b|sewage|contamin", re.I),
    ),
    (
        "Food / Nutrition",
        re.compile(r"\bfood\b|hunger|nutrition|poison|toxic|malnutri", re.I),
    ),
    (
        "Health Facility",
        re.compile(
            r"hospital|clinic|\bnurse\b|\bdoctor\b|health cent|medicine|treatment", re.I
        ),
    ),
]

_SOURCE_DISPLAY_LABELS = {
    "Social Media": "Social Media",
    "Community Group": "Community Group",
    "Offline Media": "Offline Media",
    "Other": "Other",
}
_SOURCE_COLORS = {
    "Social Media": "#4e79a7",
    "Community Group": "#f28e2b",
    "Offline Media": "#59a14f",
    "Other": "#e15759",
    "Unknown": "#9ca3af",
}

_RUMOR_SENTIMENT_HIGH_RE = re.compile(
    r"outbreak|epidemic|\bdeath\b|\bdied\b|\bdead\b|\bkilled\b|fatal|panic"
    r"|danger|emergenc|spread|cholera|ebola|covid|crisis|alert",
    re.I,
)
_RUMOR_SENTIMENT_MOD_RE = re.compile(
    r"\bsick\b|hospital|worr|medic|infect|treatment|warning|concern"
    r"|illness|clinic|\bnurse\b|\bdoctor\b|symptom|fever|\bpain\b",
    re.I,
)

# Word cloud clustering parameters
_WC_TOP_N_PHRASES = 150
_WC_MAX_NODES = 60
_WC_CLUSTER_THRESHOLD = 75
_WC_EDGE_THRESHOLD = 40
_WC_NOISE_RE = re.compile(r"[^\w\s\-]")
_WC_SKIP_VALUES = {"", "nan", "none", "null", "nat", "n/a", "na"}


def _infer_rumor_category(text):
    if not text:
        return "General Concern"
    for name, pattern in _RUMOR_CATEGORY_PATTERNS:
        if pattern.search(text):
            return name
    return "General Concern"


def _wc_token_sort_ratio(a, b):
    try:
        from rapidfuzz import fuzz as rf

        return rf.token_sort_ratio(a, b)
    except Exception:
        ta = " ".join(sorted(a.split()))
        tb = " ".join(sorted(b.split()))
        return int(difflib.SequenceMatcher(None, ta, tb).ratio() * 100)


def _wc_token_set_ratio(a, b):
    try:
        from rapidfuzz import fuzz as rf

        return rf.token_set_ratio(a, b)
    except Exception:
        set_a, set_b = set(a.split()), set(b.split())
        shared = set_a & set_b
        if not shared:
            return 0
        return int(len(shared) / max(len(set_a), len(set_b)) * 100)


def _build_rumor_word_cloud_data(qs, *, max_nodes=_WC_MAX_NODES):
    raw_counts: Counter = Counter()
    for (desci,) in (
        qs.exclude(desci__isnull=True).exclude(desci="").values_list("desci").iterator()
    ):
        norm = re.sub(
            r"\s+", " ", _WC_NOISE_RE.sub(" ", (desci or "").strip().lower())
        ).strip()
        if not norm or norm in _WC_SKIP_VALUES or len(norm) < 3:
            continue
        raw_counts[norm] += 1

    total_events = sum(raw_counts.values())
    if not raw_counts:
        return {"nodes": [], "edges": [], "total_events": 0, "has_data": False}

    top_phrases = [p for p, _ in raw_counts.most_common(_WC_TOP_N_PHRASES)]

    # Union-Find clustering
    parent = {p: p for p in top_phrases}

    def _find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a, b):
        parent[_find(a)] = _find(b)

    for i, p1 in enumerate(top_phrases):
        for p2 in top_phrases[i + 1 :]:
            if _wc_token_sort_ratio(p1, p2) >= _WC_CLUSTER_THRESHOLD:
                _union(p1, p2)

    clusters: dict = defaultdict(list)
    for phrase in top_phrases:
        clusters[_find(phrase)].append(phrase)

    nodes = []
    for members in clusters.values():
        canonical = max(members, key=lambda p: raw_counts[p])
        freq = sum(raw_counts[p] for p in members)
        variants = sorted(
            (p for p in members if p != canonical), key=lambda p: -raw_counts[p]
        )[:5]
        nodes.append(
            {
                "id": canonical,
                "label": canonical.title(),
                "frequency": freq,
                "pct": round(freq / total_events * 100, 1) if total_events else 0.0,
                "variants": variants,
                "category": _infer_rumor_category(canonical),
            }
        )

    nodes.sort(key=lambda n: -n["frequency"])
    nodes = nodes[:max_nodes]
    node_ids = [n["id"] for n in nodes]

    edges = []
    for i, src in enumerate(node_ids):
        for tgt in node_ids[i + 1 :]:
            score = _wc_token_set_ratio(src, tgt)
            if score >= _WC_EDGE_THRESHOLD:
                edges.append(
                    {"source": src, "target": tgt, "weight": round(score / 100, 3)}
                )

    return {
        "nodes": nodes,
        "edges": edges,
        "total_events": total_events,
        "has_data": True,
    }


def _build_rumor_source_by_province(qs):
    province_choices = _get_csa_tracker_choice_label_map("province")
    rows = (
        qs.exclude(province__isnull=True)
        .exclude(province="")
        .values("province", "sorce_rum")
        .annotate(count=Count("pk"))
        .order_by("province")
    )
    data_map = {}
    for row in rows:
        prov = _resolve_csa_tracker_choice_label(province_choices, row["province"])
        code = (row.get("sorce_rum") or "").strip()
        label = RUMOR_SOURCE_LABELS.get(code, "Unknown")
        data_map.setdefault(prov, Counter())[label] += row["count"]

    provinces = sorted(data_map.keys())
    seen_labels, all_labels = set(), []
    for code in ["01", "02", "03", "04"]:
        lbl = RUMOR_SOURCE_LABELS[code]
        if lbl not in seen_labels:
            seen_labels.add(lbl)
            all_labels.append(lbl)
    if any("Unknown" in data_map[p] for p in provinces):
        all_labels.append("Unknown")

    series = [
        {
            "label": lbl,
            "data": [data_map.get(p, Counter()).get(lbl, 0) for p in provinces],
            "backgroundColor": _SOURCE_COLORS.get(lbl, "#999"),
        }
        for lbl in all_labels
        if any(data_map.get(p, Counter()).get(lbl, 0) > 0 for p in provinces)
    ]
    return {"provinces": provinces, "series": series}


def _build_rumor_affected_population(qs):
    rows = (
        qs.exclude(today__isnull=True)
        .exclude(today="")
        .exclude(today__iexact="nan")
        .exclude(today__iexact="none")
        .filter(today__regex=r"^\d{4}-\d{2}-\d{2}")
        .annotate(month_key=Substr("today", 1, 7))
        .values("month_key", "ru_num")
        .order_by("month_key")
    )
    month_events: Counter = Counter()
    month_affected: Counter = Counter()
    for row in rows:
        mk = row.get("month_key") or ""
        if not mk:
            continue
        month_events[mk] += 1
        with suppress(TypeError, ValueError):
            month_affected[mk] += int(float(row.get("ru_num") or 0))

    ordered_months = sorted(set(month_events) | set(month_affected))
    labels, event_counts, affected_counts = [], [], []
    for mk in ordered_months:
        try:
            labels.append(datetime.strptime(mk, "%Y-%m").strftime("%b %Y"))
        except ValueError:
            continue
        event_counts.append(month_events[mk])
        affected_counts.append(month_affected[mk])
    return {
        "labels": labels,
        "event_counts": event_counts,
        "affected_counts": affected_counts,
    }


def _normalize_odk_lookup_value(value):
    text = str(value or "").strip()
    if re.fullmatch(r"\d+", text):
        return str(int(text))  # strips leading zeros (e.g. "01" → "1")
    if re.fullmatch(r"\d+\.0+", text):
        return str(int(text.split(".", 1)[0]))  # handles ODK exports like "01.0"
    return text


def _get_csa_tracker_choice_label_map(field_name):
    lookup = {}
    for row in (
        ODKFormChoice.objects.filter(field_name=field_name)
        .order_by("id")
        .values("value", "label")
    ):
        key = _normalize_odk_lookup_value(row["value"])
        if key and key not in lookup:
            lookup[key] = (row["label"] or "").strip()
    return lookup


def _resolve_csa_tracker_choice_label(choice_map, raw_value):
    raw_text = str(raw_value or "").strip()
    if not raw_text:
        return ""
    normalized = _normalize_odk_lookup_value(raw_text)
    label = choice_map.get(normalized)
    if label:
        return label
    return raw_text.replace("_", " ")


def _build_rumor_location_stats(qs, *, top_n=5):
    district_choices = _get_csa_tracker_choice_label_map("district")
    constituency_choices = _get_csa_tracker_choice_label_map("constituency")
    ward_choices = _get_csa_tracker_choice_label_map("ward")
    rows = (
        qs.exclude(district__isnull=True)
        .exclude(district="")
        .exclude(district__iexact="nan")
        .exclude(district__iexact="none")
        .exclude(district__iexact="null")
        .exclude(district__iexact="nat")
        .exclude(ward__isnull=True)
        .exclude(ward="")
        .exclude(ward__iexact="nan")
        .exclude(ward__iexact="none")
        .exclude(ward__iexact="null")
        .exclude(ward__iexact="nat")
        .values("district", "constituency", "ward")
        .annotate(count=Count("pk"), last_reported=Max("today"))
        .order_by("-count")
    )

    locations = {}
    for row in rows:
        district = _resolve_csa_tracker_choice_label(
            district_choices, row.get("district")
        )
        constituency = _resolve_csa_tracker_choice_label(
            constituency_choices, row.get("constituency")
        )
        ward = _resolve_csa_tracker_choice_label(ward_choices, row.get("ward"))
        location_name = f"{district} - {constituency} - {ward}"
        entry = locations.setdefault(
            location_name,
            {
                "name": location_name,
                "district": district,
                "constituency": constituency,
                "ward": ward,
                "count": 0,
                "last_reported": "",
            },
        )
        entry["count"] += row["count"]
        last_reported = row.get("last_reported") or ""
        if last_reported > entry["last_reported"]:
            entry["last_reported"] = last_reported

    top_rows = sorted(
        locations.values(), key=lambda row: (-row["count"], row["name"].lower())
    )[:top_n]
    return {
        "rows": top_rows,
    }


def _build_rumor_gps_coverage_by_province(qs):
    province_rows = (
        qs.exclude(province__isnull=True)
        .exclude(province="")
        .values("province")
        .annotate(
            with_gps=Count(
                "pk",
                filter=Q(hh_gps_latitude__isnull=False) & ~Q(hh_gps_latitude=""),
            ),
            total=Count("pk"),
        )
        .order_by("-total")
    )
    provinces, with_gps, missing_gps = [], [], []
    for row in province_rows:
        provinces.append(row["province"])
        wgps = row["with_gps"]
        total = row["total"]
        with_gps.append(wgps)
        missing_gps.append(total - wgps)
    return {"provinces": provinces, "with_gps": with_gps, "missing_gps": missing_gps}


_VADER_ANALYZER = SentimentIntensityAnalyzer()
_RUMOR_SKIP_TEXT_VALUES = {"nan", "none", "null", "nat"}

# Matches text describing SRS programme objectives (verbal autopsy, mortality
# surveillance, civil registration, etc.) and the broader subject matter the
# programme tracks (birth, death, pregnancy, maternal/health). VADER scores
# many of these terms as negative, but in this dataset they describe
# operational/programmatic content, not concern.
_SRS_DOMAIN_RE = re.compile(
    # Programme structures and workflow vocabulary
    r"\bverbal autopsy\b|\bautopsy\b|\bautopsies\b"
    r"|\bmortality surveil|\bmortality data\b"
    r"|\bcivil registr|\bdeath registr|\bbirth registr|\bCRVS\b"
    r"|\bCSA\b|\bcommunity surveillance\b|\bMSO\b|\bRDM\b|\bSRS\b"
    r"|\bsample.based registr|\bcause.of.death\b|\bcause of death\b"
    r"|\bdeath certificate\b|\bcertificate\b|\bvital statistics\b|\bSDG\b|\bDNRPC\b"
    r"|\binterva\b|\bodk\b|\bkobo\b"
    # Subject matter the programme tracks (vital events, maternal/child health)
    r"|\bbirth\b|\bbirths\b|\bdeath\b|\bdeaths\b"
    r"|\bpregnan\w*|\bmaternal\b|\bhealth\b",
    re.I,
)

# Domain text must exceed this VADER compound threshold to receive non-zero
# sentiment. Keeps factually neutral programme language pinned to 0.
_SRS_DOMAIN_SENTIMENT_THRESHOLD = 0.25


def _rumor_sentiment_score_detail(text):
    """Return (compound_score, is_srs_domain).

    Rumors describing SRS programme activities (verbal autopsy, mortality
    registration, CSA/MSO field work, etc.) are scored 0 unless the text
    carries clear positive or negative connotation beyond the threshold.
    Non-domain rumors use the raw VADER compound score.
    """
    if not text:
        return 0.0, False
    cleaned = text.strip()
    if not cleaned or cleaned.lower() in _RUMOR_SKIP_TEXT_VALUES:
        return 0.0, False
    compound = float(_VADER_ANALYZER.polarity_scores(cleaned)["compound"])
    is_domain = bool(_SRS_DOMAIN_RE.search(cleaned))
    if is_domain and abs(compound) < _SRS_DOMAIN_SENTIMENT_THRESHOLD:
        return 0.0, True
    return compound, is_domain


def _rumor_sentiment_score(text):
    """Backwards-compatible wrapper returning the score only."""
    return _rumor_sentiment_score_detail(text)[0]


def _rumor_ru_num_int(value):
    """Coerce ru_num (CharField) to a non-negative int; 0 on any failure."""
    if value is None:
        return 0
    raw = str(value).strip()
    if not raw or raw.lower() in _RUMOR_SKIP_TEXT_VALUES:
        return 0
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return 0
    return max(0, n)


def _rumor_source_label(sorce_rum, other_rum):
    code = (sorce_rum or "").strip()
    label = RUMOR_SOURCE_LABELS.get(code, "")
    if label == "Other":
        other = (other_rum or "").strip()
        if other and other.lower() not in _RUMOR_SKIP_TEXT_VALUES:
            return f"Other — {other}"
    return label or "Unknown"


def _build_rumor_sentiment_data(qs, *, filter_state=None):
    """Per-rumor scatter points for the sentiment-over-time chart.

    Each CSADailyTracker row that passes the filter becomes its own point
    (no aggregation by description) so that more rumor reports render as
    more dots, not fewer larger ones. Bubble size is driven separately by
    `ru_num` (population affected) per the dashboard spec.

    The response also carries:
      - `counts` for a positive/neutral/negative summary
      - `monthly_means` for a trendline overlay
      - `range` so the chart x-axis can span the full filter window even
        when some months have no points (surfacing reporting gaps)
    """
    points = []
    rows = qs.values(
        "pk",
        "desci",
        "ru_num",
        "today",
        "submissiondate",
        "start",
        "sorce_rum",
        "other_rum",
        "province",
        "district",
    ).iterator()

    for row in rows:
        desci = (row.get("desci") or "").strip()
        if not desci or desci.lower() in _RUMOR_SKIP_TEXT_VALUES:
            continue

        parsed = _parse_rumor_date(_rumor_date_str(row))
        if parsed is None:
            continue

        ru_num = _rumor_ru_num_int(row.get("ru_num"))
        sentiment, is_srs_domain = _rumor_sentiment_score_detail(desci)
        weighted = sentiment * math.log1p(ru_num)

        points.append(
            {
                "id": row.get("pk"),
                "date": parsed.strftime("%Y-%m-%d"),
                "month": parsed.strftime("%Y-%m"),
                "sentiment": round(sentiment, 4),
                "weighted": round(weighted, 4),
                "ru_num": ru_num,
                "description": desci,
                "category": _infer_rumor_category(desci),
                "is_srs_domain": is_srs_domain,
                "source": _rumor_source_label(
                    row.get("sorce_rum"), row.get("other_rum")
                ),
                "province": (row.get("province") or "").strip(),
                "district": (row.get("district") or "").strip(),
            }
        )

    # Counts by sentiment sign (zero counts as neutral).
    counts = {"positive": 0, "neutral": 0, "negative": 0}
    monthly_buckets = defaultdict(list)
    for p in points:
        w = p["weighted"]
        if w > 0:
            counts["positive"] += 1
        elif w < 0:
            counts["negative"] += 1
        else:
            counts["neutral"] += 1
        monthly_buckets[p["month"]].append(w)

    monthly_means = []
    for month_str in sorted(monthly_buckets.keys()):
        values = monthly_buckets[month_str]
        year, month = month_str.split("-")
        x = int(year) * 12 + (int(month) - 1)
        monthly_means.append(
            {
                "month": month_str,
                "x": x,
                "mean": round(sum(values) / len(values), 4),
                "count": len(values),
            }
        )

    # Range: prefer the filter window so empty months remain visible. Fall
    # back to the observed point months for the all_time preset.
    range_payload = {"start_month": None, "end_month": None}
    start_dt, end_dt = (
        _csa_rumor_filter_window(filter_state) if filter_state else (None, None)
    )
    if start_dt:
        range_payload["start_month"] = start_dt.strftime("%Y-%m")
    if end_dt:
        range_payload["end_month"] = end_dt.strftime("%Y-%m")
    if not range_payload["start_month"] and points:
        range_payload["start_month"] = min(p["month"] for p in points)
    if not range_payload["end_month"] and points:
        range_payload["end_month"] = max(p["month"] for p in points)

    return {
        "points": points,
        "total": len(points),
        "has_data": bool(points),
        "counts": counts,
        "monthly_means": monthly_means,
        "range": range_payload,
    }


def _build_rumor_description_table(qs, *, page=1, page_size=5):
    ws_re = re.compile(r"\s+")
    desc_data = {}
    for row in (
        qs.exclude(desci__isnull=True)
        .exclude(desci="")
        .values("desci", "sorce_rum")
        .iterator()
    ):
        raw = (row.get("desci") or "").strip()
        normalized = ws_re.sub(" ", raw).lower().strip()
        if not normalized or normalized in {"nan", "none", "null", "nat"}:
            continue
        if normalized not in desc_data:
            desc_data[normalized] = {"count": 0, "original": raw, "sources": Counter()}
        desc_data[normalized]["count"] += 1
        code = (row.get("sorce_rum") or "").strip()
        desc_data[normalized]["sources"][RUMOR_SOURCE_LABELS.get(code, "Unknown")] += 1

    sorted_items = sorted(
        desc_data.items(), key=lambda kv: kv[1]["count"], reverse=True
    )
    page_size = 5
    page = 1
    page_slice = sorted_items[:page_size]
    total_events = sum(entry["count"] for _norm, entry in sorted_items)

    rows = []
    for _norm, entry in page_slice:
        top_source = (
            entry["sources"].most_common(1)[0][0] if entry["sources"] else "Unknown"
        )
        rows.append(
            {
                "description": entry["original"],
                "category": _infer_rumor_category(entry["original"]),
                "count": entry["count"],
                "pct": (
                    round(entry["count"] * 100 / total_events, 1)
                    if total_events
                    else 0.0
                ),
                "source": top_source,
            }
        )
    return {
        "rows": rows,
        "total_rows": len(rows),
        "page": page,
        "page_size": page_size,
        "total_pages": 1,
    }


class RumorOverviewSummaryAPIView(CustomAuthMixin, View):
    def get(self, request, *args, **kwargs):
        return JsonResponse(
            _build_rumor_summary_data(_build_csa_rumor_queryset(request))
        )


rumor_overview_summary_api_view = transaction.non_atomic_requests(
    RumorOverviewSummaryAPIView.as_view()
)


class RumorOverviewTrendAPIView(CustomAuthMixin, View):
    def get(self, request, *args, **kwargs):
        labels, counts = _build_rumor_trend_data(_build_csa_rumor_queryset(request))
        return JsonResponse({"labels": labels, "data": counts})


rumor_overview_trend_api_view = transaction.non_atomic_requests(
    RumorOverviewTrendAPIView.as_view()
)


class RumorOverviewTopRumorsTrendAPIView(CustomAuthMixin, View):
    def get(self, request, *args, **kwargs):
        return JsonResponse(
            _build_top_rumors_trend_data(_build_csa_rumor_queryset(request))
        )


rumor_overview_top_rumors_trend_api_view = transaction.non_atomic_requests(
    RumorOverviewTopRumorsTrendAPIView.as_view()
)


class RumorOverviewDetectionAPIView(CustomAuthMixin, View):
    """Source distribution – kept on the 'detection' URL for API stability."""

    def get(self, request, *args, **kwargs):
        labels, counts = _build_rumor_source_distribution(
            _build_csa_rumor_queryset(request)
        )
        return JsonResponse({"labels": labels, "data": counts})


rumor_overview_detection_api_view = transaction.non_atomic_requests(
    RumorOverviewDetectionAPIView.as_view()
)


class RumorOverviewFollowUpAPIView(CustomAuthMixin, View):
    """Data completeness stats – kept on the 'follow-up' URL for API stability."""

    def get(self, request, *args, **kwargs):
        return JsonResponse(
            _build_rumor_completeness_stats(_build_csa_rumor_queryset(request))
        )


rumor_overview_follow_up_api_view = transaction.non_atomic_requests(
    RumorOverviewFollowUpAPIView.as_view()
)


class RumorOverviewMapAPIView(CustomAuthMixin, View):
    def get(self, request, *args, **kwargs):
        return JsonResponse(_build_rumor_map_payload(request))


rumor_overview_map_api_view = transaction.non_atomic_requests(
    RumorOverviewMapAPIView.as_view()
)


class RumorOverviewSourceByProvinceAPIView(CustomAuthMixin, View):
    def get(self, request, *args, **kwargs):
        return JsonResponse(
            _build_rumor_source_by_province(_build_csa_rumor_queryset(request))
        )


rumor_overview_source_by_province_api_view = transaction.non_atomic_requests(
    RumorOverviewSourceByProvinceAPIView.as_view()
)


class RumorOverviewAffectedPopulationAPIView(CustomAuthMixin, View):
    def get(self, request, *args, **kwargs):
        return JsonResponse(
            _build_rumor_affected_population(_build_csa_rumor_queryset(request))
        )


rumor_overview_affected_population_api_view = transaction.non_atomic_requests(
    RumorOverviewAffectedPopulationAPIView.as_view()
)


class RumorDetailsEnumeratorStatsAPIView(CustomAuthMixin, View):
    def get(self, request, *args, **kwargs):
        return JsonResponse(
            _build_rumor_location_stats(_build_csa_rumor_queryset(request))
        )


rumor_details_enumerator_stats_api_view = transaction.non_atomic_requests(
    RumorDetailsEnumeratorStatsAPIView.as_view()
)


class RumorOverviewGpsCoverageAPIView(CustomAuthMixin, View):
    def get(self, request, *args, **kwargs):
        return JsonResponse(
            _build_rumor_gps_coverage_by_province(_build_csa_rumor_queryset(request))
        )


rumor_overview_gps_coverage_api_view = transaction.non_atomic_requests(
    RumorOverviewGpsCoverageAPIView.as_view()
)


class RumorDetailsDescriptionTableAPIView(CustomAuthMixin, View):
    def get(self, request, *args, **kwargs):
        try:
            page = max(1, int(request.GET.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = min(100, max(5, int(request.GET.get("page_size", 5))))
        except (TypeError, ValueError):
            page_size = 5
        return JsonResponse(
            _build_rumor_description_table(
                _build_csa_rumor_queryset(request), page=page, page_size=page_size
            )
        )


rumor_details_description_table_api_view = transaction.non_atomic_requests(
    RumorDetailsDescriptionTableAPIView.as_view()
)


class RumorDetailsSentimentAPIView(CustomAuthMixin, View):
    def get(self, request, *args, **kwargs):
        return JsonResponse(
            _build_rumor_sentiment_data(
                _build_csa_rumor_queryset(request),
                filter_state=_build_csa_rumor_filter_state(request),
            )
        )


rumor_details_sentiment_api_view = transaction.non_atomic_requests(
    RumorDetailsSentimentAPIView.as_view()
)


class RumorDetailsWordCloudAPIView(CustomAuthMixin, View):
    def get(self, request, *args, **kwargs):
        return JsonResponse(
            _build_rumor_word_cloud_data(_build_csa_rumor_queryset(request))
        )


rumor_details_word_cloud_api_view = transaction.non_atomic_requests(
    RumorDetailsWordCloudAPIView.as_view()
)


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
                return JsonResponse(
                    {"detail": "Unsupported va_statistics chart."}, status=400
                )
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

        return JsonResponse(
            {"detail": "Unsupported tab/chart combination."}, status=400
        )


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
                trends_payload.get("modelTrends", {}).get(metric, {}).get("table", {})
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
                paged["additional"] = trends_payload.get(
                    "additionalIndeterminateCods", 0
                )
                cache.set(
                    response_cache_key,
                    paged,
                    timeout=HOME_FASTPATH_TABLE_CACHE_TTL_SECONDS,
                )
                return JsonResponse(paged)
            return JsonResponse(
                {"detail": "Unsupported va_statistics table."}, status=400
            )

        return JsonResponse(
            {"detail": "Unsupported tab/table combination."}, status=400
        )


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
        ("name", "CSA Name"),
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
        ("supervisor", "Supervisor"),
        ("csa_target", "CSA Target"),
        ("coverage_pct", "Coverage %"),
        ("status", "Status"),
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
        raw_name = (va_row.get("Id10010") or "").strip() or (
            va_row.get("location__name") or ""
        ).strip()
        return RegionalOperationsComponentContextMixin._format_mso_display_name(
            raw_name
        )

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
    def _build_norm_match_cache(norm_series, candidate_norms, threshold=85):
        """Return a dict mapping each unique norm value to its (matched, best, score) tuple.

        Deduplicating before fuzzy-matching reduces calls from O(rows × candidates)
        to O(unique_norms × candidates), which is the main speed-up for the MSO tab.
        """
        cache = {}
        for norm in norm_series.dropna().unique():
            cache[norm] = (
                RegionalOperationsComponentContextMixin._best_match_name_norm_with_score(
                    norm, candidate_norms, threshold
                )
            )
        return cache

    @staticmethod
    def _apply_norm_match_cache(df, col, cache):
        """Expand a match cache onto a DataFrame column, returning matched_norm/best_norm/best_score."""

        def _lookup(value):
            return pd.Series(cache.get(value, (None, None, 0.0)))

        return (
            df[col]
            .map(lambda v: _lookup(v))
            .apply(pd.Series)
            .rename(columns={0: "matched_norm", 1: "best_norm", 2: "best_score"})
        )

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
        return format_csa_display_name(raw_name)

    @staticmethod
    def _format_mso_display_name(raw_name):
        name = (raw_name or "").strip().replace("_", " ")
        # Drop trailing numeric token after the last space.
        name = re.sub(r"\s+\d+$", "", name).strip()
        return name or "Unassigned MSO"

    @staticmethod
    def _normalize_name_search_text(value):
        # Keep legacy behavior for non-CSA name matching (e.g., MSO token normalization).
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
        geo = (
            (self.request.GET.get("geo") or self.request.GET.get("geography") or "")
            .strip()
            .lower()
        )
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
            start_dt = datetime.combine(
                start_date, datetime.min.time(), tzinfo=ZoneInfo("Africa/Lusaka")
            )
            end_dt = datetime.combine(
                end_date, datetime.max.time(), tzinfo=ZoneInfo("Africa/Lusaka")
            )
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
            start_dt = now_local - timedelta(days=29)
            end_dt = now_local
            time_label = "Last 30 days"
        elif selected_preset == "7":
            start_dt = now_local - timedelta(days=6)
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

            for name in (
                location.get_descendants()
                .filter(depth=2)
                .values_list("name", flat=True)
            ):
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

    def _province_access_q_with_codes(self, field_name, province_names):
        """Like _province_access_q but also matches ODK choice codes stored in the field.

        Models such as CSADailyTracker and Pregnancy store province as an ODK choice
        value (e.g. "5") rather than a human-readable name. Without code-based matching,
        users with location restrictions get an empty queryset because the name-based
        iexact filters never match a stored code.
        """
        q = self._province_access_q(field_name, province_names)
        if province_names is None:
            return q

        choice_map = self._get_choice_label_map("province")  # {code: label}
        for province_name in province_names:
            norm_scope = self._normalize_province_name(province_name)
            for code, label in choice_map.items():
                if self._normalize_province_name(label) == norm_scope:
                    q |= Q(**{f"{field_name}__exact": code})
                    # Normalise leading zeros (e.g. "05" ↔ "5").
                    norm_code = self._normalize_choice_lookup_value(code)
                    if norm_code != code:
                        q |= Q(**{f"{field_name}__exact": norm_code})
        return q

    @staticmethod
    def _sort_rows(rows, sort_key, sort_dir):
        reverse = sort_dir == "desc"
        if sort_key == "name":
            return sorted(
                rows, key=lambda r: str(r.get("name", "")).lower(), reverse=reverse
            )
        if sort_key in {"district", "ward", "province", "supervisor", "status"}:
            return sorted(
                rows, key=lambda r: str(r.get(sort_key, "")).lower(), reverse=reverse
            )

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
            "next_page": (
                current_page + 1 if current_page < total_pages else total_pages
            ),
            "visible_count": len(rows[start:end]),
            "total_count": total,
        }

    @staticmethod
    def _coverage_band(value):
        return _coverage_band_helper(value)

    def _get_csa_target_supervisor_map(self):
        cache_attr = "_csa_target_supervisor_map_cache"
        cache_meta = CSASupervisorTargetMap.objects.aggregate(
            latest_updated=Max("updated"),
            count=Count("id"),
            latest_id=Max("id"),
        )
        cache_version = (
            cache_meta["latest_updated"],
            cache_meta["count"],
            cache_meta["latest_id"],
        )
        cached = getattr(self.__class__, cache_attr, None)
        if cached and cached.get("version") == cache_version:
            return cached.get("data", {})

        mapping = {}
        for row in CSASupervisorTargetMap.objects.values(
            "csa_name_normalized",
            "csa_display_name",
            "supervisor_name",
            "csa_target",
            "tracker_first_date",
            "status",
        ):
            csa_name = self._format_csa_display_name(row.get("csa_display_name"))
            normalized_key = row.get(
                "csa_name_normalized"
            ) or normalize_csa_name_for_join(csa_name)
            display_key = csa_name.strip().lower()
            if not normalized_key and not display_key:
                continue

            target_raw = row.get("csa_target")
            payload = {
                "supervisor": (row.get("supervisor_name") or "").strip() or "—",
                "csa_target": self._to_int(target_raw) if target_raw is not None else 0,
                "tracker_first_date": row.get("tracker_first_date"),
                "status": row.get("status") or CSASupervisorTargetMap.STATUS_INACTIVE,
            }
            if normalized_key:
                mapping[normalized_key] = payload
            if display_key:
                mapping[display_key] = payload

        setattr(
            self.__class__,
            cache_attr,
            {"version": cache_version, "data": mapping},
        )
        return mapping

    def _build_csa_rows(
        self,
        selected_geo,
        start_date_str,
        end_date_str,
        province_scope,
        selected_preset="all",
    ):
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
                "supervisor": "—",
                "csa_target": 0,
                "status": CSASupervisorTargetMap.STATUS_INACTIVE,
                "coverage_pct": None,
                "coverage_band": "empty",
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

        # If no tracker records exist there is nothing to display; skip the
        # Pregnancy query entirely rather than running a second DB query.
        if not counts_by_csa:
            return []

        # Compute the shared window length so the stored weekly csa_target can be
        # scaled to match the selected filter.
        # - 7-day preset   → window_days = 7  (scale = 1, target unchanged)
        # - 30-day preset  → window_days = 30 (scale = 30/7)
        # - 24-hour preset → window_days = 1
        # - custom range   → window_days = (end - start + 1)
        # - "all time"     → window_days = None → resolved per-CSA in the loop
        #                    using tracker_first_date from CSASupervisorTargetMap
        today = date.today()
        if selected_preset == "24":
            window_days = 1
        elif start_date_str and end_date_str:
            _cov_start = date.fromisoformat(start_date_str)
            _cov_end = date.fromisoformat(end_date_str)
            window_days = max(1, (_cov_end - _cov_start).days + 1)
        else:
            window_days = None  # resolved per-CSA below

        overdue_cutoff = datetime.today().date() - timedelta(days=180)

        pregnancy_rows = Pregnancy.objects.filter(
            self._province_access_q("province", province_scope)
        ).filter(self._province_filter_q("province", selected_geo))
        pregnancy_rows = pregnancy_rows.filter(PE_10A__lte=overdue_cutoff.isoformat())
        if start_date_str and end_date_str:
            pregnancy_rows = pregnancy_rows.filter(
                PE_10A__gte=start_date_str,
                PE_10A__lte=end_date_str,
            )
        # Exclude pregnancies that already have a recorded outcome using a DB-level
        # EXISTS subquery. This replaces a full-table load of PregnancyOutcome into
        # a Python set, which was O(N) memory for the entire outcomes table.
        pregnancy_rows = pregnancy_rows.exclude(
            Q(
                key__in=PregnancyOutcome.objects.exclude(pregnancy_id__isnull=True)
                .exclude(pregnancy_id__exact="")
                .values("pregnancy_id")
            )
        )

        for row in pregnancy_rows.values(
            "enumerator",
            "province",
            "district",
            "ward",
            "PE_10A",
        ):
            due_date = self._coerce_date(row.get("PE_10A"))
            if not due_date or due_date > overdue_cutoff:
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

        csa_mapping = self._get_csa_target_supervisor_map()

        for entry in counts_by_csa.values():
            display_key = str(entry.get("name") or "").strip().lower()
            normalized_key = normalize_csa_name_for_join(entry.get("name"))
            mapping_row = (
                csa_mapping.get(normalized_key) or csa_mapping.get(display_key) or {}
            )

            entry["supervisor"] = mapping_row.get("supervisor", "—")
            entry["status"] = mapping_row.get(
                "status", CSASupervisorTargetMap.STATUS_INACTIVE
            )
            weekly_target = self._to_int(mapping_row.get("csa_target"))

            # Resolve the effective window length for this CSA.
            # For "all time" (window_days is None) use the per-CSA tracker_first_date
            # stored on CSASupervisorTargetMap, avoiding a live MIN(today) query.
            if window_days is not None:
                effective_window = window_days
            else:
                first_date = mapping_row.get("tracker_first_date")
                effective_window = (
                    max(1, (today - first_date).days + 1) if first_date else None
                )

            if weekly_target > 0 and effective_window is not None:
                scaled_target = weekly_target * (effective_window / 7)
                entry["csa_target"] = round(scaled_target)
                entry["coverage_pct"] = round(
                    (entry["visits"] / scaled_target) * 100, 1
                )
            else:
                entry["csa_target"] = weekly_target
                entry["coverage_pct"] = None
            entry["coverage_band"] = self._coverage_band(entry["coverage_pct"])

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

        # Single VA queryset pass: fetch base fields AND duration fields together to
        # avoid a second full-table VA scan later for duration outlier calculations.
        va_to_mso_name = {}
        base_va_records = []
        duration_records = []
        for va_row in va_queryset.values(
            "id",
            "Id10010",
            "location__name",
            "province_name",
            "province",
            "Id10012",  # interview_date (for duration outliers)
            "Id10011",  # start_time
            "Id10481",  # end_datetime
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
            duration_records.append(
                {
                    "name": mso_name,
                    "mso_name_norm": mso_name_norm,
                    "interview_date": va_row.get("Id10012"),
                    "start_time": va_row.get("Id10011"),
                    "end_datetime": va_row.get("Id10481"),
                }
            )

        if base_va_records:
            df_base_va = pd.DataFrame.from_records(base_va_records)
            df_va_totals = df_base_va.groupby(
                ["name", "mso_name_norm"], as_index=False
            ).agg(
                va_total=("va_id", "count"),
                province=("province", "first"),
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

        # Build per-VA cause/error metrics via two focused queries instead of
        # loading full ORM objects with prefetch_related (much faster at scale).
        # va_to_mso_name was already populated in the single VA pass above.
        va_ids = list(va_to_mso_name.keys())

        causes_by_va = defaultdict(list)
        for row in CauseOfDeath.objects.filter(verbalautopsy_id__in=va_ids).values(
            "verbalautopsy_id", "cause"
        ):
            causes_by_va[row["verbalautopsy_id"]].append(row["cause"])

        va_ids_with_error = set(
            CauseCodingIssue.objects.filter(
                verbalautopsy_id__in=va_ids, severity="error"
            ).values_list("verbalautopsy_id", flat=True)
        )

        va_metric_records = []
        for va_id in va_ids:
            mso_name = va_to_mso_name.get(va_id, "Unassigned MSO")
            causes = causes_by_va.get(va_id, [])
            valid_cod = int(
                any(
                    (cause or "").strip() and cause != self.INDETERMINATE_LABEL
                    for cause in causes
                )
            )
            indeterminate = int(
                any(cause == self.INDETERMINATE_LABEL for cause in causes)
            )
            error = int(va_id in va_ids_with_error)
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
            df_mso = df_mso.merge(
                df_va_metrics, on=["name", "mso_name_norm"], how="outer"
            )
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

        start_date_obj = self._coerce_date(start_date_str)
        end_date_obj = self._coerce_date(end_date_str)

        # Single Event query for all death-type events with province scope.
        # Previously two separate queries (death_rows + event_rows) hit the same table
        # with the same province filter — merged here to halve DB round trips.
        # Date filtering is done in Python so both death-count and scheduling logic
        # can apply their respective criteria without restricting the result set.
        all_event_rows = list(
            Event.objects.filter(
                self._province_access_q_with_codes("province", province_scope)
            )
            .filter(self._province_filter_q("province", selected_geo))
            .filter(
                Q(event_type=Event.EventType.DEATH)
                | Q(event_type_code__icontains="death")
            )
            .values(
                "event_status",
                "va_interview_status",
                "interview_scheduled_date",
                "interview_complete_date",
                "submission_date",
                "va__Id10010",
                # va_interview_staff__name is the User.name assigned in case
                # management scheduling.  For newly scheduled events where no VA
                # has been submitted yet, va__Id10010 is null — fall back to this
                # field so those events still appear in va_scheduled counts.
                "va_interview_staff__name",
                "death_id",
                "death__DE_06",
            )
        )

        # Build one shared fuzzy-match cache over all unique MSO name norms present
        # in the event rows.  Includes names from both va__Id10010 (submitted VA
        # form) and va_interview_staff__name (case management scheduling) so that
        # events without a submitted VA are still matched to an MSO row.
        _all_event_name_norms = {
            self.normalize_person_name(self._format_mso_display_name(raw))
            for e in all_event_rows
            for raw in (
                (e.get("va__Id10010") or "").strip(),
                (e.get("va_interview_staff__name") or "").strip(),
            )
            if raw
        }
        shared_norm_cache = self._build_norm_match_cache(
            pd.Series(sorted(_all_event_name_norms), dtype=object), mso_name_norms, 85
        )

        # ── Death event count ─────────────────────────────────────────────────
        death_records = []
        for event in all_event_rows:
            if event.get("death_id") is None:
                continue
            death_name_raw = (event.get("va__Id10010") or "").strip() or (
                event.get("va_interview_staff__name") or ""
            ).strip()
            if not death_name_raw:
                continue
            death_date = self._coerce_date(event.get("death__DE_06"))
            if not death_date:
                continue
            if start_date_obj and death_date < start_date_obj:
                continue
            if end_date_obj and death_date > end_date_obj:
                continue
            death_name = self._format_mso_display_name(death_name_raw)
            death_records.append(
                {
                    "agg_name": death_name,
                    "agg_name_norm": self.normalize_person_name(death_name),
                    "death_id": event.get("death_id"),
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
                    lambda v: pd.Series(shared_norm_cache.get(v, (None, None, 0.0)))
                )
                unmatched_agg_logs.extend(
                    [
                        {
                            "source": "death_events",
                            "agg_name": row.get("agg_name"),
                            "best_match": mso_name_norm_to_name.get(
                                row.get("best_norm"), row.get("best_norm")
                            ),
                            "score": row.get("best_score", 0.0),
                        }
                        for _, row in df_death[
                            df_death["matched_norm"].isna()
                        ].iterrows()
                    ]
                )
                df_death = df_death[df_death["matched_norm"].notna()]
                df_death["name"] = df_death["matched_norm"].map(mso_name_norm_to_name)
                df_death = df_death[df_death["name"].notna()]
                df_death = df_death.groupby("name", as_index=False)[
                    "death_events"
                ].sum()
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

        scheduling_records = []
        for event in all_event_rows:
            # Prefer the actual VA form interviewer name; fall back to the User
            # assigned in case management for events where the VA has not yet
            # been submitted (va is null, so va__Id10010 is blank).
            mso_name_raw = (event.get("va__Id10010") or "").strip() or (
                event.get("va_interview_staff__name") or ""
            ).strip()
            if not mso_name_raw:
                continue
            mso_name = self._format_mso_display_name(mso_name_raw)
            is_scheduled = event.get(
                "event_status"
            ) == Event.EventStatus.VA_INTERVIEW_SCHEDULED or (
                event.get("va_interview_status") == Event.VAInterviewStatus.SCHEDULED
                and event.get("interview_scheduled_date") is not None
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
            ].apply(lambda v: pd.Series(shared_norm_cache.get(v, (None, None, 0.0))))
            unmatched_agg_logs.extend(
                [
                    {
                        "source": "scheduling",
                        "agg_name": row.get("agg_name"),
                        "best_match": mso_name_norm_to_name.get(
                            row.get("best_norm"), row.get("best_norm")
                        ),
                        "score": row.get("best_score", 0.0),
                    }
                    for _, row in df_scheduling[
                        df_scheduling["matched_norm"].isna()
                    ].iterrows()
                ]
            )
            df_scheduling = df_scheduling[df_scheduling["matched_norm"].notna()]
            df_scheduling["name"] = df_scheduling["matched_norm"].map(
                mso_name_norm_to_name
            )
            df_scheduling = df_scheduling[df_scheduling["name"].notna()]
            df_scheduling = df_scheduling.groupby("name", as_index=False)[
                ["va_scheduled", "va_not_complete"]
            ].sum()
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
        for event in all_event_rows:
            if (
                event.get("death_id") is None
                or event.get("interview_complete_date") is None
            ):
                continue
            death_date = self._coerce_date(event.get("death__DE_06"))
            complete_date = event.get("interview_complete_date")
            if not death_date or not complete_date:
                continue
            mean_name_raw = (event.get("va__Id10010") or "").strip() or (
                event.get("va_interview_staff__name") or ""
            ).strip()
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
            ].apply(lambda v: pd.Series(shared_norm_cache.get(v, (None, None, 0.0))))
            unmatched_agg_logs.extend(
                [
                    {
                        "source": "mean_days",
                        "agg_name": row.get("agg_name"),
                        "best_match": mso_name_norm_to_name.get(
                            row.get("best_norm"), row.get("best_norm")
                        ),
                        "score": row.get("best_score", 0.0),
                    }
                    for _, row in df_mean_days[
                        df_mean_days["matched_norm"].isna()
                    ].iterrows()
                ]
            )
            df_mean_days = df_mean_days[df_mean_days["matched_norm"].notna()]
            df_mean_days["name"] = df_mean_days["matched_norm"].map(
                mso_name_norm_to_name
            )
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

        # duration_records was built in the single VA pass at the top of this method.
        if duration_records:
            df_duration = pd.DataFrame.from_records(duration_records)
            # Id10011 (start_time) is already a full ISO-8601 datetime with
            # timezone offset — use it directly instead of concatenating with
            # the separate date field, which forced dateutil string parsing
            # (0.6 s for 1 500 rows). infer_datetime_format=True lets pandas
            # use its fast C path for recognised ISO-8601 patterns.
            df_duration["start_dt"] = pd.to_datetime(
                df_duration["start_time"].fillna("").astype(str).str.strip(),
                errors="coerce",
                infer_datetime_format=True,
                utc=True,
            )
            df_duration["end_dt"] = pd.to_datetime(
                df_duration["end_datetime"],
                errors="coerce",
                infer_datetime_format=True,
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
                df_duration_metrics["mso_name_norm"] = df_duration_metrics[
                    "name"
                ].apply(self.normalize_person_name)
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
        df_mso["mean_death_to_va_complete"] = df_mso["mean_death_to_va_complete"].where(
            pd.notna(df_mso["mean_death_to_va_complete"]), ""
        )

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

    def _resolve_regional_params(self):
        """Resolve all request parameters shared by both CSA and MSO contexts."""
        selected_geo = self._normalized_geo()
        time_window = self._resolve_time_window()
        province_scope = self._allowed_province_names()
        return {
            "selected_geo": selected_geo,
            "time_window": time_window,
            "province_scope": province_scope,
        }

    def _get_csa_context(self, params):
        """Build context for the CSA supervision table only."""
        selected_geo = params["selected_geo"]
        time_window = params["time_window"]
        province_scope = params["province_scope"]

        csa_sort = self._pick(
            self.request.GET.get("csa_sort", "visits"),
            {key for key, _ in self.csa_column_map},
            "visits",
        )
        csa_dir = self._pick(
            self.request.GET.get("csa_dir", "desc"), {"asc", "desc"}, "desc"
        )
        csa_search = self.request.GET.get("csa_search", "").strip()
        csa_supervisor_search = self.request.GET.get(
            "csa_supervisor_search", ""
        ).strip()
        csa_district_search = self.request.GET.get("csa_district_search", "").strip()
        csa_ward_search = self.request.GET.get("csa_ward_search", "").strip()
        csa_page = max(1, self._to_int(self.request.GET.get("csa_page", "1")))

        csa_rows = self._build_csa_rows(
            selected_geo,
            time_window["start_date_str"],
            time_window["end_date_str"],
            province_scope,
            time_window["selected_preset"],
        )
        if selected_geo and selected_geo != "national":
            csa_rows = [
                row
                for row in csa_rows
                if self._matches_geo_by_province(row.get("province"), selected_geo)
            ]
        if csa_search:
            csa_rows = [
                row
                for row in csa_rows
                if self._matches_name_search(row.get("name", ""), csa_search)
            ]
        if csa_supervisor_search:
            supervisor_query = csa_supervisor_search.lower()
            csa_rows = [
                row
                for row in csa_rows
                if supervisor_query in str(row.get("supervisor") or "").lower()
            ]
        if csa_district_search:
            district_query = csa_district_search.lower()
            csa_rows = [
                row
                for row in csa_rows
                if district_query in str(row.get("district") or "").lower()
            ]
        if csa_ward_search:
            ward_query = csa_ward_search.lower()
            csa_rows = [
                row
                for row in csa_rows
                if ward_query in str(row.get("ward") or "").lower()
            ]
        csa_rows_sorted = self._sort_rows(csa_rows, csa_sort, csa_dir)
        csa_sort_param = (self.request.GET.get("csa_sort") or "").strip().lower()
        csa_dir_param = (self.request.GET.get("csa_dir") or "").strip().lower()
        using_default_sort = csa_sort_param in {"", "visits"} and csa_dir_param in {
            "",
            "desc",
        }
        if using_default_sort:
            csa_rows_sorted = sorted(
                csa_rows_sorted,
                key=lambda row: (
                    str(row.get("status") or "").strip().lower()
                    == CSASupervisorTargetMap.STATUS_INACTIVE
                ),
            )
        csa_pagination = self._paginate_rows(csa_rows_sorted, csa_page, per_page=10)

        return {
            "csa_stats": csa_pagination["rows"],
            "csa_rows": csa_pagination["rows"],
            "csa_sort": csa_sort,
            "csa_dir": csa_dir,
            "csa_search": csa_search,
            "csa_supervisor_search": csa_supervisor_search,
            "csa_district_search": csa_district_search,
            "csa_ward_search": csa_ward_search,
            "csa_page": csa_pagination["page"],
            "csa_total_pages": csa_pagination["total_pages"],
            "csa_has_prev": csa_pagination["has_prev"],
            "csa_has_next": csa_pagination["has_next"],
            "csa_prev_page": csa_pagination["prev_page"],
            "csa_next_page": csa_pagination["next_page"],
            "csa_visible_count": csa_pagination["visible_count"],
            "csa_total_count": csa_pagination["total_count"],
        }

    def _get_mso_context(self, params):
        """Build context for the MSO supervision table only."""
        selected_geo = params["selected_geo"]
        time_window = params["time_window"]
        province_scope = params["province_scope"]

        allowed_mso_sources = {item["value"] for item in self.mso_source_options}
        requested_mso_source = (self.request.GET.get("source") or "").strip().lower()
        selected_mso_source = (
            requested_mso_source if requested_mso_source in allowed_mso_sources else ""
        )
        mso_sort = self._pick(
            self.request.GET.get("mso_sort", "death_events"),
            {key for key, _ in self.mso_column_map},
            "death_events",
        )
        mso_dir = self._pick(
            self.request.GET.get("mso_dir", "desc"), {"asc", "desc"}, "desc"
        )
        mso_search = self.request.GET.get("mso_search", "").strip()
        mso_province_search = self.request.GET.get("mso_province_search", "").strip()
        mso_page = max(1, self._to_int(self.request.GET.get("mso_page", "1")))

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

        def matches_mso_filter(value, query):
            return query.lower() in str(value or "").lower()

        if mso_search:
            mso_rows = [
                row
                for row in mso_rows
                if matches_mso_filter(row.get("name"), mso_search)
            ]
        if mso_province_search:
            mso_rows = [
                row
                for row in mso_rows
                if matches_mso_filter(row.get("province"), mso_province_search)
            ]
        mso_rows_sorted = self._sort_rows(mso_rows, mso_sort, mso_dir)
        mso_pagination = self._paginate_rows(mso_rows_sorted, mso_page, per_page=10)

        return {
            "mso_source_options": self.mso_source_options,
            "selected_mso_source": selected_mso_source,
            "mso_stats": mso_pagination["rows"],
            "mso_rows": mso_pagination["rows"],
            "mso_sort": mso_sort,
            "mso_dir": mso_dir,
            "mso_search": mso_search,
            "mso_province_search": mso_province_search,
            "mso_page": mso_pagination["page"],
            "mso_total_pages": mso_pagination["total_pages"],
            "mso_has_prev": mso_pagination["has_prev"],
            "mso_has_next": mso_pagination["has_next"],
            "mso_prev_page": mso_pagination["prev_page"],
            "mso_next_page": mso_pagination["next_page"],
            "mso_visible_count": mso_pagination["visible_count"],
            "mso_total_count": mso_pagination["total_count"],
        }

    def get_regional_operations_context(self):
        params = self._resolve_regional_params()
        selected_geo = params["selected_geo"]
        time_window = params["time_window"]
        selected_time_preset = time_window["selected_time_preset"]
        ctx = {
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
        }
        ctx.update(self._get_csa_context(params))
        ctx.update(self._get_mso_context(params))
        return ctx

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
        va_queryset = va_queryset.filter(
            self._province_filter_q("province_name", selected_geo)
        )
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

    def get_context_data(self, **kwargs):
        # Skip the mixin's get_context_data (which builds both CSA and MSO
        # rows) since the filter template needs only the lightweight filter
        # parameters.
        context = super(RegionalOperationsComponentContextMixin, self).get_context_data(
            **kwargs
        )
        params = self._resolve_regional_params()
        time_window = params["time_window"]
        context["geography_options"] = self.geography_options
        context["time_presets"] = self.time_presets
        context["selected_geography"] = params["selected_geo"]
        context["selected_time_preset"] = time_window["selected_time_preset"]
        context["selected_start_date"] = time_window["start_value"]
        context["selected_end_date"] = time_window["end_value"]
        filter_scope = (self.request.GET.get("filter_scope") or "").strip().lower()
        context["filter_suffix"] = "_mso" if filter_scope == "mso" else ""
        return context


class RegionalOperationsCsaComponent(
    RegionalOperationsComponentContextMixin, CustomAuthMixin, TemplateView
):
    template_name = "home/components/_regional_operations_csa_table.html"

    def get_context_data(self, **kwargs):
        context = super(RegionalOperationsComponentContextMixin, self).get_context_data(
            **kwargs
        )
        params = self._resolve_regional_params()
        context.update(self._get_csa_context(params))
        return context


class RegionalOperationsMsoComponent(
    RegionalOperationsComponentContextMixin, CustomAuthMixin, TemplateView
):
    template_name = "home/components/_regional_operations_mso_table.html"

    def get_context_data(self, **kwargs):
        context = super(RegionalOperationsComponentContextMixin, self).get_context_data(
            **kwargs
        )
        params = self._resolve_regional_params()
        context.update(self._get_mso_context(params))
        return context
