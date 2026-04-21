from django.conf import settings
from django.core.cache import cache

from ..users.models import Feedback
from ..va_data_management.models import (
    VerbalAutopsy,
    questions_to_autodetect_duplicates,
)

_CTX_CACHE_TTL = 60  # seconds


def settings_context(_request):
    return {"settings": settings}


def auto_detect_duplicates(_request):
    key = "ctx:auto_detect_duplicates"
    result = cache.get(key)
    if result is None:
        result = len(questions_to_autodetect_duplicates()) > 0
        cache.set(key, result, timeout=_CTX_CACHE_TTL)
    return {"AUTO_DETECT_DUPLICATES": result}


def duplicates_count(_request):
    key = "ctx:duplicates_count"
    count = cache.get(key)
    if count is None:
        count = VerbalAutopsy.objects.filter(duplicate=True).count()
        cache.set(key, count, timeout=_CTX_CACHE_TTL)
    return {"DUPLICATES_COUNT": count}


def mailbox(request):
    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        key = f"ctx:mailbox_unread:{user.pk}"
        count = cache.get(key)
        if count is None:
            count = user.mailbox_unread_count
            cache.set(key, count, timeout=_CTX_CACHE_TTL)
        return {"MAILBOX_UNREAD_COUNT": count}
    return {"MAILBOX_UNREAD_COUNT": 0}


def feedback_counts(request):
    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        if user.is_superuser or user.groups.filter(name="Admins").exists():
            key = "ctx:feedback_new_count"
            count = cache.get(key)
            if count is None:
                count = Feedback.objects.filter(status=Feedback.Status.NEW).count()
                cache.set(key, count, timeout=_CTX_CACHE_TTL)
            return {"FEEDBACK_NEW_COUNT": count}
    return {"FEEDBACK_NEW_COUNT": 0}
