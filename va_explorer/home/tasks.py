import logging

from django.core.cache import cache
from django.utils import timezone

from config.celery_app import app
from va_explorer.home.cache_utils import (
    HOME_DASHBOARD_PREWARM_CHART_KEY,
    HOME_DASHBOARD_PREWARM_KEY,
    HOME_DASHBOARD_PREWARM_KPIS_KEY,
    HOME_DASHBOARD_PREWARM_TTL,
)

logger = logging.getLogger(__name__)


@app.task(name="va_explorer.home.tasks.prewarm_home_dashboard_cache")
def prewarm_home_dashboard_cache():
    """Pre-compute the unrestricted national/all-time NOV payload and warm the cache.

    Runs on a 5-minute schedule so unrestricted users requesting the default home
    dashboard view always hit a warm cache entry rather than the expensive live query.
    """
    from va_explorer.home.services.nov import get_national_operational_view_data

    try:
        end_dt = timezone.localtime(timezone.now())
        payload = get_national_operational_view_data(
            start_dt=None,
            end_dt=end_dt,
            request=None,
            location_level="national",
            location_value="",
            include_chart_series=True,
        )
        cache.set(
            HOME_DASHBOARD_PREWARM_KEY, payload, timeout=HOME_DASHBOARD_PREWARM_TTL
        )
        cache.set(
            HOME_DASHBOARD_PREWARM_KPIS_KEY,
            payload.get("kpis", {}),
            timeout=HOME_DASHBOARD_PREWARM_TTL,
        )
        cache.set(
            HOME_DASHBOARD_PREWARM_CHART_KEY,
            {
                "labels": payload.get("chart_labels", []),
                "pregnancy": payload.get("pregnancy_values", []),
                "pregnancy_outcome": payload.get("pregnancy_outcome_values", []),
                "death": payload.get("death_values", []),
                "va": payload.get("va_values", []),
            },
            timeout=HOME_DASHBOARD_PREWARM_TTL,
        )
        logger.info("Home dashboard prewarm cache updated")
        return {"status": "ok"}
    except Exception as exc:
        logger.exception("Home dashboard prewarm failed: %s", exc)
        return {"status": "error", "error": str(exc)}
