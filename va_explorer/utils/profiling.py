import logging
import time
from contextlib import contextmanager

logger = logging.getLogger("dashboard.profiling")


def _normalize_request(request):
    if request is None:
        return None
    # DRF Request wraps Django HttpRequest on `_request`.
    return getattr(request, "_request", request)


def _ensure_request_steps(request):
    request = _normalize_request(request)
    if request is None:
        return None
    if not hasattr(request, "_dashboard_profile_steps"):
        request._dashboard_profile_steps = []
    return request._dashboard_profile_steps


def add_request_step(request, name, duration_ms):
    request = _normalize_request(request)
    steps = _ensure_request_steps(request)
    if steps is None:
        return
    steps.append({"name": name, "duration_ms": float(duration_ms)})


@contextmanager
def timed_block(name, *, request=None, log=False):
    started = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = (time.perf_counter() - started) * 1000.0
        add_request_step(request, name, duration_ms)
        if log:
            logger.info("[perf] step=%s duration_ms=%.2f", name, duration_ms)


def top_steps(request, *, limit=5):
    request = _normalize_request(request)
    steps = getattr(request, "_dashboard_profile_steps", []) or []
    ranked = sorted(steps, key=lambda item: item["duration_ms"], reverse=True)
    return ranked[:limit]
