VA_SCHEDULE_VIEW_PERMISSION = "vacms.view_event"
VA_SCHEDULE_CHANGE_PERMISSION = "vacms.change_event"

ALLOWED_VA_SCHEDULER_GROUPS = (
    "Admins",
    "Data Managers",
    "Mortality Surveillance Officer",
    "Community Surveillance Officer",
)


def _user_denied_permission(user, permission_name):
    return permission_name in set(getattr(user, "denied_permissions", None) or [])


def user_can_manage_va_schedule(user):
    if not getattr(user, "is_authenticated", False):
        return False
    if _user_denied_permission(user, VA_SCHEDULE_CHANGE_PERMISSION):
        return False

    return bool(
        user.is_superuser
        or user.has_perm(VA_SCHEDULE_CHANGE_PERMISSION)
        or user.groups.filter(name__in=ALLOWED_VA_SCHEDULER_GROUPS).exists()
    )


def user_can_view_va_schedule(user):
    if not getattr(user, "is_authenticated", False):
        return False
    if user_can_manage_va_schedule(user):
        return True
    if _user_denied_permission(user, VA_SCHEDULE_VIEW_PERMISSION):
        return False

    return bool(user.is_superuser or user.has_perm(VA_SCHEDULE_VIEW_PERMISSION))
