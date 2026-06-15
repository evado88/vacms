from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from typing import Iterable, Sequence

from django.core.cache import cache
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from va_explorer.va_data_management.models import (
    Death,
    Household,
    HouseholdMember,
    Pregnancy,
    PregnancyOutcome,
    VerbalAutopsy,
)
from va_explorer.va_data_management.utils.location_access import (
    restrict_queryset_to_user_locations,
)


def _fix_tz_offset(s: str) -> str:
    """
    Normalize timezone suffixes like +0000 -> +00:00 so Django can parse them.
    """
    if len(s) >= 5 and (s[-5] in "+-") and s[-4:].isdigit():
        return s[:-5] + s[-5:-2] + ":" + s[-2:]
    return s


def _parse_submission_timestamp(value: object) -> datetime | None:
    """Return a timezone-aware ``datetime`` for heterogeneous timestamp inputs."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, dt_time.min)
    else:
        if value is None:
            return None
        raw = str(value).strip()
        if not raw:
            return None

        # Common normalizations from ODK/Kobo/CSV exports
        normalised = raw.replace("Z", "+00:00")
        normalised = _fix_tz_offset(normalised)

        candidates = [normalised]
        # Also try a space instead of T
        if "T" in normalised:
            candidates.append(_fix_tz_offset(normalised.replace("T", " ")))
        # If there are fractional seconds, try a variant without them (keep TZ)
        if "." in normalised:
            prefix, _, suffix = normalised.partition(".")
            if suffix:
                tz_sep = "+" if "+" in suffix else ("-" if "-" in suffix else "")
                if tz_sep:
                    tz_index = suffix.find(tz_sep)
                    candidates.append(prefix + _fix_tz_offset(suffix[tz_index:]))
                else:
                    candidates.append(prefix)

        dt = None
        for candidate in candidates:
            dt = parse_datetime(candidate)
            if dt:
                break

        if dt is None:
            parsed_date = parse_date(normalised)
            if parsed_date is not None:
                dt = datetime.combine(parsed_date, dt_time.min)

        if dt is None:
            return None

    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _first_valid_timestamp(obj: object, fields: Sequence[str]) -> datetime | None:
    for field in fields:
        value = getattr(obj, field, None)
        timestamp = _parse_submission_timestamp(value)
        if timestamp is not None:
            return timestamp
    return None


def _normalise_queryset(qs, key_field: str):
    return qs.exclude(**{f"{key_field}__isnull": True}).exclude(**{key_field: ""})


def _count_recent_records(
    qs,
    *,
    key_field: str | None,
    date_fields: Iterable[str],
    return_identifiers: bool = False,
) -> tuple[int, int, int] | tuple[int, int, int, set, set]:
    """
    Return (total, count_in_last_24h, count_in_last_7_days) for a queryset.

    If key_field is provided, we will distinct on non-empty values of that field.
    Otherwise we use the queryset as-is (assumed already deduplicated).

    When ``return_identifiers`` is ``True`` the sets of identifiers contributing to
    the 24-hour and 7-day counts are also returned.
    """
    if key_field:
        filtered = _normalise_queryset(qs, key_field)
        total = filtered.values_list(key_field, flat=True).distinct().count()
    else:
        filtered = qs
        total = filtered.count()

    since_day = timezone.now() - timedelta(days=1)
    since_week = timezone.now() - timedelta(days=7)
    day_keys: set = set()
    week_keys: set = set()
    only_fields = list(date_fields)
    if key_field:
        only_fields.append(key_field)

    for record in filtered.only(*only_fields):
        timestamp = _first_valid_timestamp(record, date_fields)
        if not timestamp:
            continue

        identifier = getattr(record, key_field) if key_field else record.pk

        if timestamp >= since_week:
            week_keys.add(identifier)
            if timestamp >= since_day:
                day_keys.add(identifier)

    if return_identifiers:
        return total, len(day_keys), len(week_keys), day_keys, week_keys

    return total, len(day_keys), len(week_keys)


def _safe_int(value: object) -> int:
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


CACHE_KEY = "homepage_metrics:v2"
CACHE_TIMEOUT_SECONDS = 60 * 60 * 24  # 1 day
LOCK_TIMEOUT_SECONDS = 60
LOCK_WAIT_SECONDS = 2
LOCK_SLEEP_SECONDS = 0.1


def _scope_suffix(user) -> str:
    if not user or not getattr(user, "is_authenticated", False):
        return "anon"
    location_ids = ",".join(
        str(pk)
        for pk in user.location_restrictions.order_by("pk").values_list("pk", flat=True)
    )
    return f"user:{user.pk}|super:{int(bool(user.is_superuser))}|loc:{location_ids}"


def _compute_homepage_metrics(user=None) -> dict[str, int]:
    # -------------------------
    # Households / clusters
    # -------------------------
    households_qs = restrict_queryset_to_user_locations(Household.objects.all(), user)

    (
        total_households,
        today_households,
        week_households,
        today_household_keys,
        week_household_keys,
    ) = _count_recent_records(
        households_qs,
        key_field="key",
        date_fields=("submissiondate", "start", "today"),
        return_identifiers=True,
    )

    (
        total_eas,
        today_eas,
        week_eas,
    ) = _count_recent_records(
        households_qs,
        key_field="ea",
        date_fields=("submissiondate", "start", "today"),
    )

    # Total Number of people counted: count rows in HouseholdMember (counting IDs).
    # ``.canonical()`` excludes content-duplicate rows so each person counts once.
    total_people = HouseholdMember.objects.canonical().count()
    if user and getattr(user, "is_authenticated", False):
        total_people = (
            HouseholdMember.objects.canonical()
            .filter(household_id__in=households_qs.values_list("key", flat=True))
            .count()
        )
    today_people = (
        HouseholdMember.objects.canonical()
        .filter(household__key__in=list(today_household_keys))
        .count()
        if today_household_keys
        else 0
    )
    week_people = (
        HouseholdMember.objects.canonical()
        .filter(household__key__in=list(week_household_keys))
        .count()
        if week_household_keys
        else 0
    )

    # -------------------------
    # Pregnancies (use submissiondate/start/today)
    # -------------------------
    total_pregnancies, today_pregnancies, week_pregnancies = _count_recent_records(
        restrict_queryset_to_user_locations(Pregnancy.objects.all(), user),
        key_field="key",
        date_fields=("submissiondate", "start", "today"),
    )

    # -------------------------
    # Pregnancy Outcomes (use submissiondate/start/today)
    # -------------------------
    total_preg_outcomes, today_preg_outcomes, week_preg_outcomes = (
        _count_recent_records(
            restrict_queryset_to_user_locations(PregnancyOutcome.objects.all(), user),
            key_field="key",
            date_fields=("submissiondate", "start", "today"),
        )
    )

    # -------------------------
    # Deaths (use submissiondate/start/today)
    # -------------------------
    total_deaths, today_deaths, week_deaths = _count_recent_records(
        restrict_queryset_to_user_locations(Death.objects.all(), user),
        key_field="key",
        date_fields=("submissiondate", "start", "today"),
    )

    # -------------------------
    # Verbal Autopsies (canonical, non-deleted; use submissiondate/Id10012/created)
    # -------------------------
    if user and getattr(user, "is_authenticated", False):
        vas_canonical = user.verbal_autopsies().filter(
            deleted_at__isnull=True, duplicate=False
        )
    else:
        vas_canonical = VerbalAutopsy.objects.filter(
            deleted_at__isnull=True, duplicate=False
        )
    total_vas, today_vas, week_vas = _count_recent_records(
        vas_canonical,
        key_field="instanceid",
        date_fields=("submissiondate", "Id10012", "created"),
    )

    return {
        "total_eas": total_eas,
        "today_eas": today_eas,
        "week_eas": week_eas,
        "total_households": total_households,
        "today_households": today_households,
        "week_households": week_households,
        "total_people": total_people,
        "today_people": today_people,
        "week_people": week_people,
        "total_pregnancies": total_pregnancies,
        "today_pregnancies": today_pregnancies,
        "week_pregnancies": week_pregnancies,
        "total_preg_outcomes": total_preg_outcomes,
        "today_preg_outcomes": today_preg_outcomes,
        "week_preg_outcomes": week_preg_outcomes,
        "total_deaths": total_deaths,
        "today_deaths": today_deaths,
        "week_deaths": week_deaths,
        "total_vas": total_vas,
        "today_vas": today_vas,
        "week_vas": week_vas,
    }


def invalidate_homepage_metrics_cache() -> None:
    cache.delete(CACHE_KEY)


def get_homepage_metrics(user=None) -> dict[str, int]:
    scope_key = _scope_suffix(user)
    cache_key = f"{CACHE_KEY}:{scope_key}"
    lock_key = f"{cache_key}:lock"

    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    have_lock = cache.add(lock_key, True, timeout=LOCK_TIMEOUT_SECONDS)
    if have_lock:
        try:
            metrics = _compute_homepage_metrics(user=user)
            cache.set(cache_key, metrics, CACHE_TIMEOUT_SECONDS)
            return metrics
        finally:
            cache.delete(lock_key)

    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while time.monotonic() < deadline:
        metrics = cache.get(cache_key)
        if metrics is not None:
            return metrics
        time.sleep(LOCK_SLEEP_SECONDS)

    # Fallback: try once more to become the lock holder, otherwise compute without caching.
    if cache.add(lock_key, True, timeout=LOCK_TIMEOUT_SECONDS):
        try:
            metrics = _compute_homepage_metrics(user=user)
            cache.set(cache_key, metrics, CACHE_TIMEOUT_SECONDS)
            return metrics
        finally:
            cache.delete(lock_key)

    return _compute_homepage_metrics(user=user)
