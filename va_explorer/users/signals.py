from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver
from simple_history.models import HistoricalRecords

from va_explorer.users.models import UserActivity

User = get_user_model()


def _current_request():
    """Best-effort lookup of the active request.

    Reuses simple_history's request middleware (already installed) so audit
    entries triggered from signals can record the acting user without each call
    site having to thread the request through.
    """
    return getattr(HistoricalRecords.context, "request", None)


def _actor_kwargs():
    request = _current_request()
    user = getattr(request, "user", None) if request is not None else None
    if user is not None and not getattr(user, "is_authenticated", False):
        user = None
    return {"request": request, "user": user}


# --- Authentication events ---


@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    UserActivity.log(UserActivity.LOGIN, user=user, request=request)


@receiver(user_logged_out)
def on_user_logged_out(sender, request, user, **kwargs):
    UserActivity.log(UserActivity.LOGOUT, user=user, request=request)


@receiver(user_login_failed)
def on_user_login_failed(sender, credentials, request, **kwargs):
    UserActivity.log(
        UserActivity.LOGIN_FAILED,
        request=request,
        status=UserActivity.STATUS_FAILURE,
        extra={"attempted_email": credentials.get("email", "")},
    )


# --- User / role / permission changes ---


@receiver(m2m_changed, sender=User.groups.through)
def on_user_groups_changed(sender, instance, action, pk_set, **kwargs):
    if action not in {"post_add", "post_remove", "post_clear"}:
        return
    group_names = list(
        Group.objects.filter(pk__in=pk_set).values_list("name", flat=True)
        if pk_set
        else []
    )
    UserActivity.log(
        UserActivity.ROLE_CHANGED,
        target_type="user",
        target_id=instance.pk,
        target_display=getattr(instance, "email", str(instance)),
        details=f"Groups {action.replace('post_', '')}: "
        f"{', '.join(group_names) if group_names else '(all)'}",
        extra={"action": action, "groups": group_names},
        **_actor_kwargs(),
    )


@receiver(m2m_changed, sender=User.user_permissions.through)
def on_user_permissions_changed(sender, instance, action, pk_set, **kwargs):
    if action not in {"post_add", "post_remove", "post_clear"}:
        return
    perm_names = list(
        Permission.objects.filter(pk__in=pk_set).values_list("codename", flat=True)
        if pk_set
        else []
    )
    UserActivity.log(
        UserActivity.PERMISSION_CHANGED,
        target_type="user",
        target_id=instance.pk,
        target_display=getattr(instance, "email", str(instance)),
        details=f"Permissions {action.replace('post_', '')}: "
        f"{', '.join(perm_names) if perm_names else '(all)'}",
        extra={"action": action, "permissions": perm_names},
        **_actor_kwargs(),
    )


@receiver(m2m_changed, sender=Group.permissions.through)
def on_group_permissions_changed(sender, instance, action, pk_set, **kwargs):
    if action not in {"post_add", "post_remove", "post_clear"}:
        return
    perm_names = list(
        Permission.objects.filter(pk__in=pk_set).values_list("codename", flat=True)
        if pk_set
        else []
    )
    UserActivity.log(
        UserActivity.PERMISSION_CHANGED,
        target_type="group",
        target_id=instance.pk,
        target_display=getattr(instance, "name", str(instance)),
        details=f"Group permissions {action.replace('post_', '')}: "
        f"{', '.join(perm_names) if perm_names else '(all)'}",
        extra={"action": action, "permissions": perm_names},
        **_actor_kwargs(),
    )


@receiver(post_save, sender=User)
def on_user_profile_updated(sender, instance, created, update_fields, **kwargs):
    # Only meaningful profile edits — skip creation and bookkeeping-only saves
    # (e.g. allauth's last_login update on every login) to avoid audit noise.
    if created:
        return
    if update_fields is not None and set(update_fields) <= {"last_login"}:
        return
    UserActivity.log(
        UserActivity.PROFILE_UPDATED,
        target_type="user",
        target_id=instance.pk,
        target_display=getattr(instance, "email", str(instance)),
        **_actor_kwargs(),
    )
