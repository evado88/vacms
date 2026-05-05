from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver

from va_explorer.users.models import UserActivity


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
        extra={"attempted_email": credentials.get("email", "")},
    )
