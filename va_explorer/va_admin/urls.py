from django.urls import path

from va_explorer.va_admin.views import (
    admin_panel_alert_log_view,
    admin_panel_alerts_view,
    admin_panel_csrf_token_view,
    admin_panel_logs_view,
    admin_panel_run_view,
    admin_panel_upload_file_view,
    admin_panel_validate_file_view,
    admin_panel_view,
    baseline_census_config_view,
    user_activity_log_download_view,
    user_activity_log_view,
)

app_name = "va_admin"

urlpatterns = [
    path("", view=admin_panel_view, name="index"),
    path("run/", view=admin_panel_run_view, name="run"),
    path("upload-file/", view=admin_panel_upload_file_view, name="upload-file"),
    path("validate-file/", view=admin_panel_validate_file_view, name="validate-file"),
    path("logs/", view=admin_panel_logs_view, name="logs"),
    path("csrf-token/", view=admin_panel_csrf_token_view, name="csrf-token"),
    path("alerts/", view=admin_panel_alerts_view, name="alerts"),
    path("alerts/log/", view=admin_panel_alert_log_view, name="alerts-log"),
    path("activity/", view=user_activity_log_view, name="activity-log"),
    path(
        "activity/download/",
        view=user_activity_log_download_view,
        name="activity-log-download",
    ),
    path(
        "baseline-census-config/",
        view=baseline_census_config_view,
        name="baseline-census-config",
    ),
]
