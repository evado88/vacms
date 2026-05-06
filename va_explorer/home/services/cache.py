import time

from django.core.cache import cache

from va_explorer.home.cache_utils import (
    HOME_DASHBOARD_PREWARM_CHART_KEY,
    HOME_DASHBOARD_PREWARM_KEY,
    HOME_DASHBOARD_PREWARM_KPIS_KEY,
)
from va_explorer.home.model_trends import get_model_trends_data
from va_explorer.home.services.common import (
    fastpath_cache_key,
    parse_filter_range,
    parse_location_filter,
)
from va_explorer.home.services.nov import get_national_operational_view_data
from va_explorer.home.va_trends import get_trends_data
from va_explorer.utils.profiling import timed_block

HOME_FASTPATH_CACHE_TTL_SECONDS = 10 * 60
HOME_FASTPATH_TABLE_CACHE_TTL_SECONDS = 5 * 60
HOME_FASTPATH_LOCK_TIMEOUT_SECONDS = 60
HOME_FASTPATH_LOCK_WAIT_SECONDS = 2
HOME_FASTPATH_LOCK_SLEEP_SECONDS = 0.1


def _get_or_build(cache_key, timeout, builder):
    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        return cached_payload

    lock_key = f"{cache_key}:lock"
    have_lock = cache.add(lock_key, True, timeout=HOME_FASTPATH_LOCK_TIMEOUT_SECONDS)
    if have_lock:
        try:
            payload = builder()
            cache.set(cache_key, payload, timeout=timeout)
            return payload
        finally:
            cache.delete(lock_key)

    deadline = time.monotonic() + HOME_FASTPATH_LOCK_WAIT_SECONDS
    while time.monotonic() < deadline:
        cached_payload = cache.get(cache_key)
        if cached_payload is not None:
            return cached_payload
        time.sleep(HOME_FASTPATH_LOCK_SLEEP_SECONDS)

    payload = builder()
    cache.set(cache_key, payload, timeout=timeout)
    return payload


def _is_unrestricted_national_request(request, preset, location_level, location_value):
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if user.location_restrictions.exists():
        return False
    if preset != "all":
        return False
    return location_level in (None, "", "national") and location_value in (None, "")


def _kpis_have_last_30_days(kpis):
    return all(
        "last_30_days" in (kpis.get(key) or {})
        for key in (
            "eas",
            "households",
            "people",
            "pregnancies",
            "preg_outcomes",
            "deaths",
            "vas",
        )
    )


def get_cached_nov_payload(request):
    preset, start_dt, end_dt, _has_custom_range = parse_filter_range(request)
    location_level, location_value = parse_location_filter(request)

    if _is_unrestricted_national_request(
        request, preset, location_level, location_value
    ):
        prewarm_payload = cache.get(HOME_DASHBOARD_PREWARM_KEY)
        if prewarm_payload is not None and _kpis_have_last_30_days(
            prewarm_payload.get("kpis", {})
        ):
            return prewarm_payload

    extra = {
        "component": "nov_bundle",
        "schema_version": 3,
        "location_level": location_level,
        "location_value": location_value,
    }
    cache_key = fastpath_cache_key("nov_bundle", request, extra=extra)

    return _get_or_build(
        cache_key,
        HOME_FASTPATH_CACHE_TTL_SECONDS,
        lambda: get_national_operational_view_data(
            start_dt=start_dt,
            end_dt=end_dt,
            request=request,
            location_level=location_level,
            location_value=location_value,
            include_chart_series=True,
        ),
    )


def get_cached_home_kpis(request):
    preset, start_dt, end_dt, _has_custom_range = parse_filter_range(request)
    location_level, location_value = parse_location_filter(request)

    if _is_unrestricted_national_request(
        request, preset, location_level, location_value
    ):
        prewarm_payload = cache.get(HOME_DASHBOARD_PREWARM_KPIS_KEY)
        if prewarm_payload is not None and _kpis_have_last_30_days(prewarm_payload):
            return prewarm_payload

    extra = {"component": "kpis", "schema_version": 3}
    cache_key = fastpath_cache_key("home_dashboard_kpis", request, extra=extra)

    return _get_or_build(
        cache_key,
        HOME_FASTPATH_CACHE_TTL_SECONDS,
        lambda: get_national_operational_view_data(
            start_dt=start_dt,
            end_dt=end_dt,
            request=request,
            location_level=location_level,
            location_value=location_value,
            include_chart_series=False,
        ).get("kpis", {}),
    )


def get_cached_home_chart_bundle(request):
    preset, _start_dt, _end_dt, _has_custom_range = parse_filter_range(request)
    location_level, location_value = parse_location_filter(request)

    if _is_unrestricted_national_request(
        request, preset, location_level, location_value
    ):
        prewarm_payload = cache.get(HOME_DASHBOARD_PREWARM_CHART_KEY)
        if prewarm_payload is not None:
            return prewarm_payload

    extra = {"component": "overview_chart", "schema_version": 3}
    cache_key = fastpath_cache_key("home_dashboard_chart_bundle", request, extra=extra)

    def build():
        nov_payload = get_cached_nov_payload(request)
        return {
            "labels": nov_payload.get("chart_labels", []),
            "pregnancy": nov_payload.get("pregnancy_values", []),
            "pregnancy_outcome": nov_payload.get("pregnancy_outcome_values", []),
            "death": nov_payload.get("death_values", []),
            "va": nov_payload.get("va_values", []),
        }

    return _get_or_build(cache_key, HOME_FASTPATH_CACHE_TTL_SECONDS, build)


def get_cached_trends_bundle(request, *, include_va=True, include_model=True):
    cache_key = fastpath_cache_key(
        "home_trends_bundle",
        request,
        extra={
            "component": "trends",
            "schema_version": 3,
            "include_va": int(include_va),
            "include_model": int(include_model),
        },
    )

    def build():
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

        return {
            "vaTable": va_table,
            "graphs": graphs,
            "issueList": issue_list,
            "indeterminateCodList": indeterminate_cod_list,
            "additionalIssues": additional_issues,
            "additionalIndeterminateCods": additional_indeterminate_cods,
            "isFieldWorker": request.user.is_fieldworker(),
            "modelTrends": model_trends,
        }

    return _get_or_build(cache_key, HOME_FASTPATH_TABLE_CACHE_TTL_SECONDS, build)
