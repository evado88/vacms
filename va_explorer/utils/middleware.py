import time

from django.db import connection

from va_explorer.utils.profiling import logger, top_steps


class DashboardProfilingMiddleware:
    """
    Lightweight request profiler for Home/Outcomes dashboard routes.
    Enabled by default in debug/local environments through settings middleware.
    """

    PROFILE_EXACT_PATHS = (
        "/",
        "/trends/",
        "/national-operational/filter-data/",
    )

    PROFILE_PATH_PREFIXES = (
        "/regional-operations/",
        "/va_analytics/outcomes-dashboard/",
        "/va_analytics/api/pregnancy/",
        "/va_analytics/api/pregnancy-events/",
        "/va_analytics/api/pregnancy-outcomes/",
        "/va_analytics/api/outcomes/deaths/",
    )

    EXCLUDE_PATH_PREFIXES = (
        "/static/",
        "/media/",
        "/__debug__/",
        "/admin/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    @classmethod
    def _should_profile(cls, path):
        if any(path.startswith(prefix) for prefix in cls.EXCLUDE_PATH_PREFIXES):
            return False
        if path in cls.PROFILE_EXACT_PATHS:
            return True
        return any(path.startswith(prefix) for prefix in cls.PROFILE_PATH_PREFIXES)

    def __call__(self, request):
        path = request.path or ""
        if not self._should_profile(path):
            return self.get_response(request)

        started = time.perf_counter()
        sql_started_at = time.perf_counter()
        initial_query_count = len(connection.queries)
        previous_debug_cursor = connection.force_debug_cursor
        connection.force_debug_cursor = True

        response = self.get_response(request)
        response_ready_at = time.perf_counter()

        def finalize(_response):
            total_ms = (time.perf_counter() - started) * 1000.0
            response_phase_ms = (response_ready_at - started) * 1000.0
            render_phase_ms = (time.perf_counter() - response_ready_at) * 1000.0

            new_queries = connection.queries[initial_query_count:]
            db_ms = sum(float(q.get("time") or 0.0) * 1000.0 for q in new_queries)
            db_query_count = len(new_queries)
            db_window_ms = (time.perf_counter() - sql_started_at) * 1000.0

            step_summary = (
                ", ".join(
                    f"{step['name']}={step['duration_ms']:.2f}ms"
                    for step in top_steps(request, limit=5)
                )
                or "none"
            )

            logger.info(
                (
                    "[perf][request] method=%s path=%s status=%s total_ms=%.2f "
                    "response_ms=%.2f render_ms=%.2f db_ms=%.2f db_window_ms=%.2f "
                    "db_queries=%d top_steps=%s"
                ),
                request.method,
                path,
                getattr(_response, "status_code", "n/a"),
                total_ms,
                response_phase_ms,
                render_phase_ms,
                db_ms,
                db_window_ms,
                db_query_count,
                step_summary,
            )

            connection.force_debug_cursor = previous_debug_cursor
            return _response

        if hasattr(response, "add_post_render_callback"):
            response.add_post_render_callback(finalize)
            return response

        return finalize(response)
