# Maps form field name -> (app_label, codename) for individual permission checkboxes
PERM_FIELD_MAP = {
    "view_pii":                   ("va_analytics", "view_pii"),
    "download_data":              ("va_analytics", "download_data"),
    "perm_view_dashboard":         ("va_analytics", "view_dashboard"),
    "perm_supervise_users":        ("va_analytics", "supervise_users"),
    "perm_add_user":               ("users", "add_user"),
    "perm_view_user":              ("users", "view_user"),
    "perm_change_user":            ("users", "change_user"),
    "perm_delete_user":            ("users", "delete_user"),
    "perm_view_verbalautopsy":     ("va_data_management", "view_verbalautopsy"),
    "perm_change_verbalautopsy":   ("va_data_management", "change_verbalautopsy"),
    "perm_delete_verbalautopsy":   ("va_data_management", "delete_verbalautopsy"),
    "perm_bulk_delete":            ("va_data_management", "bulk_delete"),
    "perm_view_household":         ("va_data_management", "view_household"),
    "perm_change_household":       ("va_data_management", "change_household"),
    "perm_view_pregnancy":         ("va_data_management", "view_pregnancy"),
    "perm_change_pregnancy":       ("va_data_management", "change_pregnancy"),
    "perm_view_pregnancyoutcome":  ("va_data_management", "view_pregnancyoutcome"),
    "perm_change_pregnancyoutcome":("va_data_management", "change_pregnancyoutcome"),
    "perm_view_death":             ("va_data_management", "view_death"),
    "perm_change_death":           ("va_data_management", "change_death"),
    "perm_view_datacleanup":       ("va_data_cleanup", "view_datacleanup"),
    "perm_download_datacleanup":   ("va_data_cleanup", "download"),
    "perm_bulk_download":          ("va_data_cleanup", "bulk_download"),
}

FEEDBACK_MODULE_CHOICES = (
    ("data_management", "Data Management"),
    ("personnel_management", "Personnel Management"),
    ("schedule_management", "Schedule Management"),
    ("analytics", "Dashboards (Analytics)"),
)

FEEDBACK_MODULE_FEATURES = {
    "data_management": [
        ("households", "Households"),
        ("pregnancies", "Pregnancies"),
        ("pregnancy_outcomes", "Pregnancy Outcomes"),
        ("deaths", "Deaths"),
        ("verbal_autopsies", "Verbal Autopsies"),
        ("data_cleanup", "Data Cleanup"),
        ("data_export", "Data Export"),
    ],
    "personnel_management": [
        ("users", "Users"),
        ("supervision", "Supervision"),
    ],
    "schedule_management": [
        ("scheduled_vas", "Scheduled Verbal Autopsies"),
    ],
    "analytics": [
        ("dashboards", "Analytics Dashboard"),
    ],
}
