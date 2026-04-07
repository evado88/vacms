from django.core.management.utils import get_random_secret_key

from .base import *  # noqa
from .base import env

# General

DEBUG = env.bool("DJANGO_DEBUG", False)

# Guard against error when get_random_secret_key() starts with $
# https://github.com/joke2k/django-environ/issues/271#issuecomment-948038628
env.escape_proxy = True
SECRET_KEY = env("DJANGO_SECRET_KEY", default=get_random_secret_key())

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["va_explorer.org"])

CSRF_TRUSTED_ORIGINS = ["https://*.moh.gov.zm"]

# Security

# Require HTTPS for session and CSRF cookies.
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# The upstream proxy terminates TLS and forwards requests over HTTP to Nginx,
# which in turn passes X-Forwarded-Proto: https to Django. Declaring this
# header here tells Django to treat those requests as secure.
# If the proxy is ever reconfigured to NOT set this header, remove this line.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# HTTPS redirection is enforced by the upstream proxy before traffic reaches
# Django, so enabling SECURE_SSL_REDIRECT here would cause a redirect loop.
# Leave it False (or remove entirely) while the proxy handles enforcement.
SECURE_SSL_REDIRECT = False

# HSTS is disabled by default (0). Once the deployment is confirmed stable
# on HTTPS-only, set SECURE_HSTS_SECONDS in the environment:
#   - Trial run:   SECURE_HSTS_SECONDS=3600
#   - Production:  SECURE_HSTS_SECONDS=31536000
# Do NOT set SECURE_HSTS_INCLUDE_SUBDOMAINS or SECURE_HSTS_PRELOAD until
# every subdomain is HTTPS-only and you are ready to submit for preloading.
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)

# Debug Toolbar is a development-only tool; strip it from production.
INSTALLED_APPS = [app for app in INSTALLED_APPS if app != "debug_toolbar"]  # noqa F405
MIDDLEWARE = [  # noqa F405
    mw for mw in MIDDLEWARE  # noqa F405
    if mw != "debug_toolbar.middleware.DebugToolbarMiddleware"
]

# Media (uploads)
MEDIA_URL = "/media/"
MEDIA_ROOT = "/srv/vacms/shared/media"

# Static (collectstatic output)
STATIC_ROOT = "/srv/vacms/shared/static"
# Caches

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://localhost:6379/0"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            # Mimicking memcache behavior.
            # http://jazzband.github.io/django-redis/latest/#_memcached_exceptions_behavior
            "IGNORE_EXCEPTIONS": True,
            # Fail fast so a missing Redis (e.g. running outside Docker) does
            # not block every request while waiting for the OS TCP timeout.
            "SOCKET_CONNECT_TIMEOUT": 1,
            "SOCKET_TIMEOUT": 1,
        },
    }
}

# Static

STATICFILES_STORAGE = "whitenoise.storage.CompressedStaticFilesStorage"
WHITENOISE_MANIFEST_STRICT = False
# Templates

TEMPLATES[-1]["OPTIONS"]["loaders"] = [  # type: ignore[index] # noqa F405
    (
        "django.template.loaders.cached.Loader",
        [
            "django.template.loaders.filesystem.Loader",
            "django.template.loaders.app_directories.Loader",
        ],
    )
]

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"require_debug_false": {"()": "django.utils.log.RequireDebugFalse"}},
    "formatters": {
        "verbose": {
            "format": "%(levelname)s %(asctime)s %(module)s "
            "%(process)d %(thread)d %(message)s"
        },
        "debug": {
            "format": "%(asctime)s - %(name)s "
            "[%(filename)s:%(lineno)s - %(funcName)5s()]  %(message)s"
        },
    },
    "handlers": {
        "mail_admins": {
            "level": "ERROR",
            "filters": ["require_debug_false"],
            "class": "django.utils.log.AdminEmailHandler",
        },
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"level": "INFO", "handlers": ["console"]},
    "loggers": {
        "django.request": {
            "handlers": ["mail_admins"],
            "level": "ERROR",
            "propagate": True,
        },
        "django.security.DisallowedHost": {
            "level": "ERROR",
            "handlers": ["console", "mail_admins"],
            "propagate": True,
        },
    },
}
