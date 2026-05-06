import hashlib
import json
from datetime import date, datetime, time, timedelta

from django.db.models import F, TextField, Value
from django.db.models.functions import Cast, NullIf
from django.utils import timezone
from django.utils.dateparse import parse_date as django_parse_date
from django.utils.dateparse import parse_datetime as django_parse_datetime

from va_explorer.home.cache_utils import get_home_dashboard_fastpath_version


def fix_tz_offset(text):
    if len(text) >= 5 and (text[-5] in "+-") and text[-4:].isdigit():
        return text[:-5] + text[-5:-2] + ":" + text[-2:]
    return text


def parse_submission_timestamp(value):
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min)
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        normalized = fix_tz_offset(raw.replace("Z", "+00:00"))
        candidates = [normalized]
        if "T" in normalized:
            candidates.append(fix_tz_offset(normalized.replace("T", " ")))

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


def first_valid_timestamp(record, date_fields):
    for field in date_fields:
        timestamp = parse_submission_timestamp(getattr(record, field, None))
        if timestamp is not None:
            return timestamp
    return None


def build_date_expr(model, field_name):
    """Return a text-typed ORM expression for a date field."""
    from django.db.models import CharField, DateField, DateTimeField

    field = model._meta.get_field(field_name)
    if isinstance(field, (DateField, DateTimeField, CharField)):
        return Cast(F(field_name), output_field=TextField())
    return NullIf(
        F(field_name), Value("", output_field=TextField()), output_field=TextField()
    )


def parse_filter_range(request):
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

    if start_dt is not None and end_dt is not None and start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    has_custom_range = bool(start_raw or end_raw)
    return preset, start_dt, end_dt, has_custom_range


def parse_location_filter(request):
    level = (request.GET.get("location_level") or "national").strip().lower()
    value = (request.GET.get("location_value") or "").strip()
    allowed_levels = {"national", "province", "district", "constituency", "ward", "ea"}
    if level not in allowed_levels:
        level = "national"
    if level == "national":
        return level, ""
    return level, value


def model_has_field(model, field_name):
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


def apply_text_geography_filter(queryset, model, level, value):
    if not value or level == "national":
        return queryset
    if not model_has_field(model, level):
        return queryset
    return queryset.filter(**{f"{level}__iexact": value})


def scope_fingerprint(user):
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


def fastpath_cache_key(namespace, request, extra=None):
    preset = (request.GET.get("preset") or "all").strip().lower()
    raw_start = (request.GET.get("start") or "").strip()
    raw_end = (request.GET.get("end") or "").strip()
    if preset not in {"all", "30", "7", "24"}:
        preset = "all"
    start_token = raw_start
    end_token = raw_end
    if raw_start or raw_end:
        _preset, start_dt, end_dt, _has_custom = parse_filter_range(request)
        start_token = start_dt.date().isoformat() if start_dt else ""
        end_token = end_dt.date().isoformat() if end_dt else ""

    location_level, location_value = parse_location_filter(request)
    payload = {
        "version": get_home_dashboard_fastpath_version(),
        "scope": scope_fingerprint(request.user),
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


def shallow_downsample(labels, series_map, max_points):
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


def paginate_list(items, page, page_size):
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
