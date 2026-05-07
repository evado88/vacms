import csv
import itertools
import json
import os
from datetime import date, datetime
from functools import lru_cache
from operator import itemgetter
from pathlib import Path

from django.db.models import (
    Case,
    CharField,
    Count,
    F,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Substr

from va_explorer.va_data_management.models import (
    CauseOfDeath,
    Location,
    questions_to_autodetect_duplicates,
)
from va_explorer.va_data_management.utils.loading import get_va_summary_stats

COD_TREND_START_MONTH = date(2024, 12, 1)


def load_cod_groupings(cause_of_death: str):
    INTERVA_GROUPCODE = os.environ.get("INTERVA_GROUPCODE") == "True"
    if INTERVA_GROUPCODE:
        filename = "cod_groupings_interva_groupcode_true.csv"
    else:
        filename = "cod_groupings_interva_groupcode_false.csv"
    path = Path(__file__).parent.parent / "data" / filename

    with open(path) as csvfile:
        filereader = csv.DictReader(csvfile)
        remove = ["algorithm", "cod"]
        headers = [header for header in filereader.fieldnames if header not in remove]

        data = []
        for row in filereader:
            data.append(row)

        cods = sorted([row.get("cod") for row in data] + headers)

    filter_causes = []
    if cause_of_death:
        for row in data:
            if row.get("cod") == cause_of_death:
                filter_causes.append(row.get("cod"))
                break

            for key, value in row.items():
                if cause_of_death == key and value == "1":
                    filter_causes.append(row.get("cod"))

    return {"dropdown_options": cods, "filter_causes": filter_causes}


def _compact_spaces(value):
    return " ".join(str(value or "").strip().split())


def _strip_geo_suffix(value, level):
    text = _compact_spaces(value)
    if not text:
        return ""

    suffixes = {
        "Province": ("province",),
        "District": ("district",),
        "Constituency": ("constituency",),
        "Ward": ("ward",),
        "EA": ("ea", "enumeration area"),
    }.get(level, ())

    lowered = text.lower()
    for suffix in suffixes:
        token = f" {suffix}"
        if lowered.endswith(token):
            return _compact_spaces(text[: -len(token)])
    return text


def _region_name_variants(name, level):
    base = _strip_geo_suffix(name, level)
    if not base:
        return set()

    variants = {base}
    if level == "Province":
        variants.add(f"{base} Province")
    elif level == "District":
        variants.add(f"{base} District")
    elif level == "Constituency":
        variants.add(f"{base} Constituency")
    elif level == "Ward":
        variants.add(f"{base} Ward")
    elif level == "EA":
        variants.add(f"{base} EA")
        variants.add(f"{base} Enumeration Area")
    return {_compact_spaces(v) for v in variants if _compact_spaces(v)}


def _parse_region_of_interest(region_of_interest):
    text = _compact_spaces(region_of_interest)
    if not text:
        return "", None

    lowered = text.lower()
    if lowered in {"zambia", "zambia country", "country zambia"}:
        return "Zambia", "Zambia"

    for level in ("EA", "Ward", "Constituency", "District", "Province"):
        token = f" {level.lower()}"
        if lowered.endswith(token):
            return text[: -len(token)].strip(), level
    return text, None


def _serialize_geo_counts(counts, key_name):
    return [{key_name: name, "count": count} for name, count in sorted(counts.items())]


def _to_int_string(value):
    raw = _compact_spaces(value)
    if not raw:
        return ""
    try:
        return str(int(float(raw)))
    except (TypeError, ValueError):
        return ""


@lru_cache(maxsize=1)
def _geojson_level_lookup():
    data_dir = Path(__file__).resolve().parents[2] / "static" / "data" / "geojson"
    files = {
        "Province": "level_1_provinces.geojson",
        "District": "level_2_districts.geojson",
        "Constituency": "level_3_constituencies.geojson",
        "Ward": "level_4_wards.geojson",
        "EA": "level_5_ea.geojson",
    }
    lookup = {level: {} for level in files}

    for level, filename in files.items():
        path = data_dir / filename
        if not path.exists():
            continue
        payload = json.loads(path.read_text())
        for feature in payload.get("features", []):
            props = feature.get("properties", {})
            canonical_name = _compact_spaces(props.get("area_name"))
            if not canonical_name:
                continue

            by_name = canonical_name.lower()
            if by_name:
                lookup[level][by_name] = canonical_name

            id_token = _to_int_string(props.get("area_id"))
            if id_token:
                lookup[level][id_token] = canonical_name

            if level == "Province" and id_token:
                try:
                    short_id = str(int(id_token) - 100)
                    lookup[level][short_id] = canonical_name
                except ValueError:
                    pass

            if level == "EA":
                zero_stripped = canonical_name.lstrip("0")
                if zero_stripped:
                    lookup[level][zero_stripped] = canonical_name

    return lookup


def _resolve_geo_name(value, level):
    text = _strip_geo_suffix(value, level)
    if not text:
        return ""

    lookup = _geojson_level_lookup().get(level, {})
    lookup_keys = [text.lower(), text, _to_int_string(text)]
    if level == "EA":
        digit_token = "".join(char for char in text if char.isdigit())
        if digit_token:
            lookup_keys.extend(
                [
                    digit_token,
                    digit_token.lstrip("0"),
                    _to_int_string(digit_token),
                ]
            )
    mapped = next(
        (lookup.get(key) for key in lookup_keys if key and lookup.get(key)), None
    )
    return _strip_geo_suffix(mapped or text, level)


def _build_region_match_keys(region_name, region_level):
    keys = set()
    for candidate in _region_name_variants(region_name, region_level) | {region_name}:
        for raw in (candidate, _resolve_geo_name(candidate, region_level)):
            normalized = _strip_geo_suffix(raw, region_level).strip().lower()
            if normalized:
                keys.add(normalized)
    return keys


def _cluster_ancestor_value(cluster, location_type):
    if not cluster:
        return None
    normalized_type = (location_type or "").strip().lower()
    lineage = list(cluster.get_ancestors()) + [cluster]
    for node in lineage:
        if ((node.location_type or "").strip().lower()) == normalized_type:
            return getattr(node, "name", None)
    return None


def _cluster_region_candidates(cluster, region_level):
    if not cluster:
        return []

    if region_level == "Province":
        return [_cluster_ancestor_value(cluster, "province")]
    if region_level == "District":
        return [_cluster_ancestor_value(cluster, "district")]
    if region_level == "Constituency":
        return [_cluster_ancestor_value(cluster, "constituency")]
    if region_level == "Ward":
        return [_cluster_ancestor_value(cluster, "ward")]
    if region_level == "EA":
        return [
            _cluster_ancestor_value(cluster, "ea"),
            getattr(cluster, "name", None),
            getattr(cluster, "code", None),
        ]
    return []


def _va_region_values(va, region_level):
    context = va.resolve_location_context()
    mode = context.get("mode")

    if mode == "community":
        cluster = context.get("cluster")
        if region_level == "Province":
            return [
                context.get("province"),
                *_cluster_region_candidates(cluster, "Province"),
            ]
        if region_level == "District":
            return [
                context.get("district"),
                *_cluster_region_candidates(cluster, "District"),
            ]
        if region_level == "Constituency":
            return [
                context.get("constituency"),
                *_cluster_region_candidates(cluster, "Constituency"),
            ]
        if region_level == "Ward":
            return [context.get("ward"), *_cluster_region_candidates(cluster, "Ward")]
        if region_level == "EA":
            return [
                context.get("ea"),
                *_cluster_region_candidates(cluster, "EA"),
            ]
        return []

    # Facility mode fallback keeps legacy behavior.
    if region_level == "Province":
        return [
            context.get("province"),
            getattr(va, "province_name_from_location", None),
        ]
    if region_level == "District":
        return [
            context.get("area"),
            getattr(va, "district_name_from_location", None),
            va.district,
        ]
    if region_level == "Constituency":
        return [va.constituency]
    if region_level == "Ward":
        return [va.ward, context.get("area")]
    if region_level == "EA":
        return [va.ea]
    return []


def _derive_va_geo_levels(va):
    context = va.resolve_location_context()
    cluster = context.get("cluster")

    def _first_resolved(level, *candidates):
        for candidate in candidates:
            resolved = _resolve_geo_name(candidate, level)
            if resolved:
                return resolved
        return ""

    if context.get("mode") == "community":
        province = _first_resolved(
            "Province",
            context.get("province"),
            *_cluster_region_candidates(cluster, "Province"),
            getattr(va, "province", None),
            getattr(va, "province_name_from_location", None),
        )
        district = _first_resolved(
            "District",
            context.get("district"),
            *_cluster_region_candidates(cluster, "District"),
            context.get("area"),
            getattr(va, "district_name_from_location", None),
            va.district,
        )
        constituency = _first_resolved(
            "Constituency",
            context.get("constituency"),
            *_cluster_region_candidates(cluster, "Constituency"),
            va.constituency,
        )
        ward = _first_resolved(
            "Ward",
            context.get("ward"),
            *_cluster_region_candidates(cluster, "Ward"),
            va.ward,
            context.get("area"),
        )
        ea = _first_resolved(
            "EA",
            context.get("ea"),
            *_cluster_region_candidates(cluster, "EA"),
            va.ea,
        )
    else:
        province = _first_resolved(
            "Province",
            context.get("province"),
            getattr(va, "province", None),
            getattr(va, "province_name_from_location", None),
        )
        district = _first_resolved(
            "District",
            getattr(va, "district_name_from_location", None),
            context.get("district"),
            context.get("area"),
            va.district,
        )
        constituency = _first_resolved(
            "Constituency",
            context.get("constituency"),
            va.constituency,
        )
        ward = _first_resolved(
            "Ward",
            context.get("ward"),
            va.ward,
            context.get("area"),
        )
        ea = _first_resolved(
            "EA",
            context.get("ea"),
            va.ea,
        )

    return {
        "Province": province,
        "District": district,
        "Constituency": constituency,
        "Ward": ward,
        "EA": ea,
    }


def _normalize_region_level_from_filter(region_of_interest):
    _name, level = _parse_region_of_interest(region_of_interest)
    return level


def _next_comparison_level(region_level):
    order = {
        None: "Province",
        "Zambia": "Province",
        "Province": "District",
        "District": "Constituency",
        "Constituency": "Ward",
        "Ward": "EA",
        "EA": "EA",
    }
    return order.get(region_level, "Province")


def _top_n_rows(rows, label_key, value_key="count", top_n=10):
    rows = list(rows)
    top = rows[:top_n]
    return [
        {label_key: row.get(label_key), value_key: row.get(value_key) or 0}
        for row in top
    ]


def _month_starts_between(start_month, end_month):
    if start_month > end_month:
        return []
    months = []
    current = date(start_month.year, start_month.month, 1)
    boundary = date(end_month.year, end_month.month, 1)
    while current <= boundary:
        months.append(current)
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return months


def _build_va_cause_trend_payload(filtered_qs, top_causes):
    start_key = COD_TREND_START_MONTH.strftime("%Y-%m")
    rows = (
        filtered_qs.exclude(final_cause__isnull=True)
        .exclude(final_cause="")
        .exclude(Id10023__isnull=True)
        .exclude(Id10023="")
        .exclude(Id10023__iexact="nan")
        .exclude(Id10023__iexact="nat")
        .exclude(Id10023__iexact="none")
        .exclude(Id10023__iexact="null")
        .exclude(Id10023__iexact="dk")
        .filter(Id10023__regex=r"^\d{4}-\d{2}-\d{2}")
        .filter(Id10023__gte=COD_TREND_START_MONTH.strftime("%Y-%m-%d"))
        .annotate(month_key=Substr("Id10023", 1, 7))
        .filter(month_key__gte=start_key)
        .values("month_key", cause=F("final_cause"))
        .annotate(count=Count("pk"))
        .order_by("month_key")
    )

    row_month_keys = sorted({row["month_key"] for row in rows if row.get("month_key")})
    if row_month_keys:
        last_month = (
            datetime.strptime(row_month_keys[-1], "%Y-%m").date().replace(day=1)
        )
        month_range = _month_starts_between(COD_TREND_START_MONTH, last_month)
    else:
        month_range = []

    month_keys = [m.strftime("%Y-%m") for m in month_range]
    periods = [m.strftime("%b %Y") for m in month_range]
    if not month_keys:
        return {
            "has_coded": False,
            "periods": [],
            "series": [],
            # Backward-compatible aliases for existing consumers.
            "labels": [],
            "datasets": [],
        }

    cause_buckets = list(top_causes)
    trend_map = {cause: dict.fromkeys(month_keys, 0) for cause in cause_buckets}

    for row in rows:
        mk = row.get("month_key")
        if not mk:
            continue
        cause = row.get("cause")
        if cause not in trend_map:
            continue
        trend_map[cause][mk] += row.get("count") or 0

    series_rows = []
    datasets = []
    for cause in cause_buckets:
        values = [trend_map[cause][k] for k in month_keys]
        if sum(values) == 0:
            continue
        series_rows.append({"name": cause, "values": values})
        datasets.append({"label": cause, "data": values})

    return {
        "has_coded": len(series_rows) > 0,
        "periods": periods,
        "series": series_rows,
        # Backward-compatible aliases for existing consumers.
        "labels": periods,
        "datasets": datasets,
    }


def _build_regional_cod_comparison(filtered_qs, region_of_interest, top_causes):
    def _is_truthy_age_flag(value):
        return _compact_spaces(value) in {"1", "1.0"}

    def _age_group_label(va):
        if any(
            _is_truthy_age_flag(getattr(va, field_name, ""))
            for field_name in ("isNeonatal", "isNeonatal1", "isNeonatal2")
        ):
            return "Neonate (< 28 days)"
        if any(
            _is_truthy_age_flag(getattr(va, field_name, ""))
            for field_name in ("isChild", "isChild1", "isChild2")
        ):
            return "Child (≤ 12 years)"
        if any(
            _is_truthy_age_flag(getattr(va, field_name, ""))
            for field_name in ("isAdult", "isAdult1", "isAdult2")
        ):
            return "Adult (> 12 years)"
        return "Unknown"

    def _source_label(va):
        normalized = _compact_spaces(getattr(va, "community_va_normalized", "")).lower()
        if normalized == "no":
            return "Facility VA"
        if normalized == "yes":
            return "Community VA"
        return "Unknown Source"

    compare_key = (_compact_spaces(region_of_interest) or "province").lower()
    cod_categories = list(top_causes)
    if not cod_categories:
        return _empty_regional_cod_comparison(compare_key)

    grouped_counts = {}
    coded_qs = (
        filtered_qs.exclude(final_cause__isnull=True)
        .exclude(final_cause="")
        .select_related("location", "cluster")
    )
    for va in coded_qs:
        if compare_key == "age":
            group_name = _age_group_label(va)
        elif compare_key == "source":
            group_name = _source_label(va)
        else:
            group_name = ""
            for candidate in _va_region_values(va, "Province"):
                group_name = _resolve_geo_name(candidate, "Province")
                if group_name:
                    break
            if not group_name:
                group_name = "Unknown Province"

        cause_name = getattr(va, "final_cause", None)
        if cause_name not in top_causes:
            continue
        group_bucket = grouped_counts.setdefault(
            group_name, dict.fromkeys(cod_categories, 0)
        )
        group_bucket[cause_name] += 1

    if compare_key == "age":
        order = {
            "Neonate (< 28 days)": 0,
            "Child (≤ 12 years)": 1,
            "Adult (> 12 years)": 2,
            "Unknown": 3,
        }
        groups = sorted(
            grouped_counts.keys(), key=lambda name: (order.get(name, 9), name)
        )
    elif compare_key == "source":
        order = {"Community VA": 0, "Facility VA": 1, "Unknown Source": 2}
        groups = sorted(
            grouped_counts.keys(), key=lambda name: (order.get(name, 9), name)
        )
    else:
        groups = sorted(grouped_counts.keys(), key=lambda name: name.lower())

    matrix_percent = []
    for group in groups:
        row_counts = [
            grouped_counts[group].get(category, 0) for category in cod_categories
        ]
        row_total = sum(row_counts)
        if row_total > 0:
            matrix_percent.append(
                [round((count / row_total) * 100, 1) for count in row_counts]
            )
        else:
            matrix_percent.append([0.0 for _ in cod_categories])

    return {
        "compare_by": compare_key,
        "groups": groups,
        "cod_categories": cod_categories,
        "matrix_percent": matrix_percent,
        # Backward-compatible aliases.
        "regions": groups,
        "causes": cod_categories,
        "percentage_matrix": matrix_percent,
    }


def _empty_regional_cod_comparison(compare_by="province"):
    return {
        "compare_by": compare_by,
        "groups": [],
        "cod_categories": [],
        "matrix_percent": [],
        "regions": [],
        "causes": [],
        "percentage_matrix": [],
    }


def _request_param(request, key, default=""):
    if hasattr(request, "query_params"):
        value = request.query_params.get(key, default)
        if value is not None:
            return value
    if hasattr(request, "GET"):
        value = request.GET.get(key, default)
        if value is not None:
            return value
    return default


def _meaningful_text_q(field_name):
    return (
        Q(**{f"{field_name}__isnull": False})
        & ~Q(**{f"{field_name}__exact": ""})
        & ~Q(**{f"{field_name}__iexact": "nan"})
        & ~Q(**{f"{field_name}__iexact": "none"})
        & ~Q(**{f"{field_name}__iexact": "null"})
    )


def _apply_source_filter(queryset, source):
    if not source:
        return queryset

    source_key = _compact_spaces(source).lower()
    if source_key in {"all", "any", "*"}:
        return queryset

    if source_key in {
        "community",
        "community_va",
        "community va",
        "yes",
        "y",
        "1",
        "true",
    }:
        return queryset.filter(community_va__iexact="yes")
    if source_key in {
        "facility",
        "facility_va",
        "facility va",
        "no",
        "n",
        "0",
        "false",
    }:
        return queryset.filter(community_va__iexact="no")
    return queryset


def _apply_age_filter(queryset, age):
    if not age:
        return queryset

    age_key = _compact_spaces(age).lower()
    if age_key == "adult":
        return queryset.filter(
            Q(isAdult="1")
            | Q(isAdult="1.0")
            | Q(isAdult1="1")
            | Q(isAdult1="1.0")
            | Q(isAdult2="1")
            | Q(isAdult2="1.0")
        )
    if age_key == "child":
        return queryset.filter(
            Q(isChild="1")
            | Q(isChild="1.0")
            | Q(isChild1="1")
            | Q(isChild1="1.0")
            | Q(isChild2="1")
            | Q(isChild2="1.0")
        )
    if age_key == "neonate":
        return queryset.filter(
            Q(isNeonatal="1")
            | Q(isNeonatal="1.0")
            | Q(isNeonatal1="1")
            | Q(isNeonatal1="1.0")
            | Q(isNeonatal2="1")
            | Q(isNeonatal2="1.0")
        )
    return queryset


def _apply_region_filter(queryset, region_of_interest):
    if not region_of_interest:
        return queryset

    region_name, region_level = _parse_region_of_interest(region_of_interest)
    if not region_level or region_level == "Zambia":
        return queryset

    match_keys = _build_region_match_keys(region_name, region_level)
    matching_ids = []
    for va in queryset.select_related("location", "cluster").iterator():
        values = _va_region_values(va, region_level)
        matched = False
        for value in values:
            resolved = _resolve_geo_name(value, region_level) or value
            normalized = _strip_geo_suffix(resolved, region_level).strip().lower()
            if normalized and normalized in match_keys:
                matched = True
                break
        if matched:
            matching_ids.append(va.pk)

    return queryset.filter(pk__in=matching_ids)


def _get_filtered_va_queryset(
    user,
    start_date,
    end_date,
    cause_of_death=None,
    region_of_interest=None,
    age=None,
    sex=None,
    source=None,
):
    user_vas = user.verbal_autopsies(date_cutoff=start_date, end_date=end_date)
    queryset = user_vas.exclude(Id10023__in=["dk", "DK"]).annotate(
        province_name_from_location=Subquery(
            Location.objects.values("name").filter(
                Q(path=Substr(OuterRef("location__path"), 1, 8)), Q(depth=2)
            )[:1]
        ),
        district_name_from_location=Subquery(
            Location.objects.values("name").filter(
                Q(path=Substr(OuterRef("location__path"), 1, 12)),
                Q(depth=3),
            )[:1]
        ),
        final_cause=Subquery(
            CauseOfDeath.objects.filter(verbalautopsy=OuterRef("pk"))
            .order_by("-created")
            .values("cause")[:1]
        ),
    )

    if cause_of_death:
        causes = load_cod_groupings(cause_of_death=cause_of_death)["filter_causes"]
        if causes:
            queryset = queryset.filter(final_cause__in=causes)
        else:
            queryset = queryset.filter(final_cause=cause_of_death)

    queryset = _apply_region_filter(queryset, region_of_interest)
    queryset = _apply_age_filter(queryset, age)
    if sex:
        queryset = queryset.filter(Id10019=sex)
    queryset = _apply_source_filter(queryset, source)
    return queryset


def get_filtered_va_queryset(request):
    start_date = _request_param(request, "start_date", "1901-01-01") or "1901-01-01"
    end_date = _request_param(request, "end_date", "") or datetime.today().strftime(
        "%Y-%m-%d"
    )
    cause_of_death = _request_param(request, "cause_of_death", "") or None
    region_of_interest = _request_param(request, "region_of_interest", "") or None
    age = _request_param(request, "age", "") or None
    sex = _request_param(request, "sex", "") or None
    source = _request_param(request, "source", "") or None
    return _get_filtered_va_queryset(
        request.user,
        start_date=start_date,
        end_date=end_date,
        cause_of_death=cause_of_death,
        region_of_interest=region_of_interest,
        age=age,
        sex=sex,
        source=source,
    )


# ============ VA Data =================
def load_va_data(
    user,
    cause_of_death,
    start_date,
    end_date,
    region_of_interest,
    age,
    sex,
    source=None,
    compare_by="province",
    filtered_qs=None,
):
    user_vas_filtered = filtered_qs or _get_filtered_va_queryset(
        user,
        start_date=start_date,
        end_date=end_date,
        cause_of_death=cause_of_death,
        region_of_interest=region_of_interest,
        age=age,
        sex=sex,
        source=source,
    )
    # Cards must reflect the active filter set (including selected map region).
    # Bypass global cached summary to avoid stale unfiltered values.
    update_stats = get_va_summary_stats(user_vas_filtered, cache_key=None)
    if len(questions_to_autodetect_duplicates()) > 0:
        update_stats["duplicates"] = user_vas_filtered.filter(duplicate=True).count()

    uncoded_vas = user_vas_filtered.filter(
        Q(final_cause__isnull=True) | Q(final_cause="")
    ).count()

    demographics = (
        user_vas_filtered.filter(causes__isnull=False)
        .values(
            gender=F("Id10019"),
            age_group_named=Case(
                When(isNeonatal="1", then=Value("neonate")),
                When(isNeonatal="1.0", then=Value("neonate")),
                When(isNeonatal1="1", then=Value("neonate")),
                When(isNeonatal1="1.0", then=Value("neonate")),
                When(isNeonatal2="1", then=Value("neonate")),
                When(isNeonatal2="1.0", then=Value("neonate")),
                When(isChild="1", then=Value("child")),
                When(isChild="1.0", then=Value("child")),
                When(isChild1="1", then=Value("child")),
                When(isChild1="1.0", then=Value("child")),
                When(isChild2="1", then=Value("child")),
                When(isChild2="1.0", then=Value("child")),
                When(isAdult="1", then=Value("adult")),
                When(isAdult="1.0", then=Value("adult")),
                When(isAdult1="1", then=Value("adult")),
                When(isAdult1="1.0", then=Value("adult")),
                When(isAdult2="1", then=Value("adult")),
                When(isAdult2="1.0", then=Value("adult")),
                default=Value("Unknown"),
                output_field=CharField(),
            ),
        )
        .annotate(count=Count("pk"))
        .order_by("age_group_named")
    )

    demographics = [
        {
            "age_group": key,
            **{item.get("gender"): item.get("count") for item in list(group)},
        }
        for key, group in itertools.groupby(demographics, itemgetter("age_group_named"))
    ]

    cod_sums_qs = (
        user_vas_filtered.exclude(final_cause__isnull=True)
        .exclude(final_cause="")
        .values(cause=F("final_cause"))
        .annotate(count=Count("pk"))
        .order_by("-count")
    )
    cod_sums_rows = list(cod_sums_qs)
    top_causes_for_trend = [
        row.get("cause") for row in cod_sums_rows[:5] if row.get("cause")
    ]
    cod_total = sum((row.get("count") or 0) for row in cod_sums_rows)
    COD_sums = _top_n_rows(
        cod_sums_rows,
        label_key="cause",
        value_key="count",
        top_n=10,
    )
    COD_sums = [{**row, "total": cod_total} for row in COD_sums]

    COD_trend = (
        user_vas_filtered.filter(causes__isnull=False)
        .exclude(Id10023__isnull=True)
        .exclude(Id10023="")
        .exclude(Id10023__iexact="nan")
        .exclude(Id10023__iexact="nat")
        .exclude(Id10023__iexact="none")
        .exclude(Id10023__iexact="null")
        .exclude(Id10023__iexact="dk")
        .filter(Id10023__regex=r"^\d{4}-\d{2}-\d{2}")
        .filter(Id10023__gte=COD_TREND_START_MONTH.strftime("%Y-%m-%d"))
        .annotate(month_key=Substr("Id10023", 1, 7))
        .values("month_key")
        .annotate(count=Count("pk"))
        .order_by("month_key")
    )
    va_cause_trend = _build_va_cause_trend_payload(
        user_vas_filtered, top_causes_for_trend
    )
    regional_cod_comparison = (
        _build_regional_cod_comparison(
            user_vas_filtered,
            region_of_interest=compare_by,
            top_causes=top_causes_for_trend,
        )
        if top_causes_for_trend
        else _empty_regional_cod_comparison(compare_by)
    )

    place_of_death = (
        user_vas_filtered.filter(causes__isnull=False)
        .values(place=F("Id10058"))
        .annotate(count=Count("pk"))
        .order_by("-count")
    )

    map_counts = {
        "Province": {},
        "District": {},
        "Constituency": {},
        "Ward": {},
        "EA": {},
    }
    coded_geo_counts = {
        "Province": {},
        "District": {},
    }
    total_map_vas = 0
    total_map_coded_vas = 0
    for va in user_vas_filtered.select_related("location", "cluster").iterator():
        total_map_vas += 1
        is_coded = bool(_compact_spaces(getattr(va, "final_cause", "")))
        if is_coded:
            total_map_coded_vas += 1

        geo = _derive_va_geo_levels(va)
        province = geo["Province"]
        district = geo["District"]
        constituency = geo["Constituency"]
        ward = geo["Ward"]
        ea = geo["EA"]

        if province:
            map_counts["Province"][province] = (
                map_counts["Province"].get(province, 0) + 1
            )
            if is_coded:
                coded_geo_counts["Province"][province] = (
                    coded_geo_counts["Province"].get(province, 0) + 1
                )
        if district:
            map_counts["District"][district] = (
                map_counts["District"].get(district, 0) + 1
            )
            if is_coded:
                coded_geo_counts["District"][district] = (
                    coded_geo_counts["District"].get(district, 0) + 1
                )
        if constituency:
            map_counts["Constituency"][constituency] = (
                map_counts["Constituency"].get(constituency, 0) + 1
            )
        if ward:
            map_counts["Ward"][ward] = map_counts["Ward"].get(ward, 0) + 1
        if ea:
            map_counts["EA"][ea] = map_counts["EA"].get(ea, 0) + 1

    geographic_province_sums = _serialize_geo_counts(
        coded_geo_counts["Province"], "province_name"
    )
    geographic_district_sums = _serialize_geo_counts(
        coded_geo_counts["District"], "district_name"
    )

    data = {
        "COD_grouping": COD_sums,
        "COD_trend": COD_trend,
        "place_of_death": place_of_death,
        "demographics": demographics,
        "geographic_province_sums": geographic_province_sums,
        "geographic_district_sums": geographic_district_sums,
        "uncoded_vas": uncoded_vas,
        "update_stats": update_stats,
        "all_causes_list": load_cod_groupings(cause_of_death=cause_of_death)[
            "dropdown_options"
        ],
        "va_cause_trend": va_cause_trend,
        "regional_cod_comparison": regional_cod_comparison,
        "map_province_sums": _serialize_geo_counts(
            map_counts["Province"], "province_name"
        ),
        "map_district_sums": _serialize_geo_counts(
            map_counts["District"], "district_name"
        ),
        "map_constituency_sums": _serialize_geo_counts(
            map_counts["Constituency"], "constituency_name"
        ),
        "map_ward_sums": _serialize_geo_counts(map_counts["Ward"], "ward_name"),
        "map_ea_sums": _serialize_geo_counts(map_counts["EA"], "ea_name"),
        # Explicit totals for map semantics.
        "map_total_vas": total_map_vas,
        # Backward-compatible key used by older frontend code paths.
        "map_total_coded_vas": total_map_coded_vas,
    }

    return data
