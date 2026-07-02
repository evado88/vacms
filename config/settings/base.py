"""
Base settings to build other settings files upon.
"""

import os
from pathlib import Path

import environ

ROOT_DIR = Path(__file__).resolve(strict=True).parent.parent.parent
APPS_DIR = ROOT_DIR / "va_explorer"

env = environ.Env()
env.read_env(str(ROOT_DIR / ".env"))

# General

TIME_ZONE = "UTC"
# Default display formats for date and datetime values when rendered in the UI.
DATE_FORMAT = "Y-m-d"
DATETIME_FORMAT = "Y-m-d H:i"
SHORT_DATE_FORMAT = DATE_FORMAT
SHORT_DATETIME_FORMAT = DATETIME_FORMAT
# accepted date formats for VA records
DATE_FORMATS = {
    "%Y-%m-%d": "yyyy-mm-dd",
    "%m/%d/%Y": "mm/dd/yyyy",
    "%m/%d/%y": "mm/dd/yy",
    "%d/%m/%Y": "dd/mm/yyyy",
    "%d/%m/%y": "dd/mm/yy",
    "%Y-%m-%d %H:%M:%S": "yyyy-mm-dd HH:MM:SS",
}
LANGUAGE_CODE = "en-us"
SITE_ID = 1
USE_I18N = True
USE_TZ = True
LOCALE_PATHS = [str(ROOT_DIR / "locale")]

# App-specific Configuration

# ==> Configuration for automatic duplicates detection
# VA Explorer will automatically mark Verbal Autopsies as duplicates if
# QUESTIONS_TO_AUTODETECT_DUPLICATES is defined
# Questions must be passed as a comma-separated list, for example,
# "Id10017, Id10018, Id10019, Id10020"
# The question IDs passed into the list must match a field in the VerbalAutopsy model
# By default, automatic duplicates detection is turned off
QUESTIONS_TO_AUTODETECT_DUPLICATES = os.environ.get(
    "QUESTIONS_TO_AUTODETECT_DUPLICATES", None
)

ODK_DEFAULT_PROJECT_ID = env.int("ODK_PROJECT_ID", default=None)
ODK_PULL_SCHEDULES = {
    # Death and other operational forms can run more frequently.
    "operational": {"minute": "7,22,37,52"},
    # VA pulls are lower urgency and heavier to process.
    "va_hourly": {"minute": "17"},
    # Household and pregnancy workflows can run less often.
    "routine": {"minute": "42", "hour": "*/3"},
}
ODK_PULL_OVERLAP_SECONDS = env.int("ODK_PULL_OVERLAP_SECONDS", default=300)
# Form configs used by scheduled pulls and as defaults for manual pulls.
ODK_PULL_FORMS = [
    {
        "form_id": env("ODK_ZNPHI_VA_FORM_ID", default=env("ODK_FORM_ID", default="")),
        "form_name": "verbal_autopsy",
        "project_id": ODK_DEFAULT_PROJECT_ID,
        "enabled": env.bool(
            "ODK_ZNPHI_VA_FORM_ENABLED",
            default=env.bool("ODK_FORM_ENABLED", default=True),
        ),
        "schedule_key": "va_hourly",
    },
    {
        "form_id": env("ODK_ZM_VA_FORM_ID", default=""),
        "form_name": "verbal_autopsy",
        "project_id": ODK_DEFAULT_PROJECT_ID,
        "enabled": env.bool("ODK_ZM_VA_FORM_ENABLED", default=True),
        "schedule_key": "va_hourly",
    },
    {
        "form_id": env("ODK_HOUSEHOLD_FORM_ID", default=""),
        "form_name": "household",
        "project_id": ODK_DEFAULT_PROJECT_ID,
        "enabled": env.bool("ODK_HOUSEHOLD_FORM_ENABLED", default=True),
        "schedule_key": "routine",
    },
    {
        "form_id": env("ODK_PREGNANCY_FORM_ID", default=""),
        "form_name": "pregnancy",
        "project_id": ODK_DEFAULT_PROJECT_ID,
        "enabled": env.bool("ODK_PREGNANCY_FORM_ENABLED", default=True),
        "schedule_key": "routine",
    },
    {
        "form_id": env("ODK_PREGNANCY_OUTCOME_FORM_ID", default=""),
        "form_name": "pregnancy_outcome",
        "project_id": ODK_DEFAULT_PROJECT_ID,
        "enabled": env.bool("ODK_PREGNANCY_OUTCOME_FORM_ENABLED", default=True),
        "schedule_key": "routine",
    },
    {
        "form_id": env("ODK_DEATH_FORM_ID", default=""),
        "form_name": "death",
        "project_id": ODK_DEFAULT_PROJECT_ID,
        "enabled": env.bool("ODK_DEATH_FORM_ENABLED", default=True),
        "schedule_key": "operational",
    },
    {
        "form_id": env("ODK_CSA_TRACKER_FORM_ID", default=""),
        "form_name": "csa_daily_tracker",
        "project_id": ODK_DEFAULT_PROJECT_ID,
        "enabled": env.bool("ODK_CSA_TRACKER_FORM_ENABLED", default=True),
        "schedule_key": "operational",
    },
    {
        "form_id": env("ODK_SOCIAL_MOBILIZATION_FORM_ID", default=""),
        "form_name": "social_mobilization",
        "project_id": ODK_DEFAULT_PROJECT_ID,
        "enabled": env.bool("ODK_SOCIAL_MOBILIZATION_FORM_ENABLED", default=True),
        "schedule_key": "operational",
    },
    {
        "form_id": env("ODK_SOCIAL_MOBILIZATION_FORM_ID", default=""),
        "form_name": "social_mobilization_rumor_log",
        "project_id": ODK_DEFAULT_PROJECT_ID,
        "enabled": env.bool("ODK_SOCIAL_MOBILIZATION_FORM_ENABLED", default=True),
        "schedule_key": "operational",
    },
    # VASA (Verbal + Social Autopsy) main form + its three care-seeking repeats.
    # The repeats share the VASA form_id but are served from separate OData tables
    # (see FORM_REPEAT_TABLE_MAP in odk/service.py).
    {
        "form_id": env("ODK_VASA_FORM_ID", default=""),
        "form_name": "vasa",
        "project_id": ODK_DEFAULT_PROJECT_ID,
        "enabled": env.bool("ODK_VASA_FORM_ENABLED", default=True),
        "schedule_key": "va_hourly",
    },
    {
        "form_id": env("ODK_VASA_FORM_ID", default=""),
        "form_name": "vasa_care_seeking_neonate",
        "project_id": ODK_DEFAULT_PROJECT_ID,
        "enabled": env.bool("ODK_VASA_FORM_ENABLED", default=True),
        "schedule_key": "va_hourly",
    },
    {
        "form_id": env("ODK_VASA_FORM_ID", default=""),
        "form_name": "vasa_care_seeking_child",
        "project_id": ODK_DEFAULT_PROJECT_ID,
        "enabled": env.bool("ODK_VASA_FORM_ENABLED", default=True),
        "schedule_key": "va_hourly",
    },
    {
        "form_id": env("ODK_VASA_FORM_ID", default=""),
        "form_name": "vasa_care_seeking_adult",
        "project_id": ODK_DEFAULT_PROJECT_ID,
        "enabled": env.bool("ODK_VASA_FORM_ENABLED", default=True),
        "schedule_key": "va_hourly",
    },
]

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

# Databases

db_user = os.environ.get("POSTGRES_USER", "postgres")
db_password = os.environ.get("POSTGRES_PASSWORD", "postgres")
db_host = os.environ.get("POSTGRES_HOST", "localhost")
db_port = os.environ.get("POSTGRES_PORT", "5432")
db_db = os.environ.get("POSTGRES_DB", "vae_cms_pilot_db")

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default=f"postgres://{db_user}:{db_password}@{db_host}:{db_port}/{db_db}",
    )
}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)

# URLs

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# Apps

DJANGO_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.sites",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # "django.contrib.humanize", # Handy template tags
    "django.forms",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "crispy_forms",
    "allauth",
    "allauth.account",
    "django_celery_beat",
    "simple_history",
    "debug_toolbar",
    "whitenoise.runserver_nostatic",
    "django_extensions",
    "bootstrap4",
    "django_filters",
    "django_pivot",
    "corsheaders",
    "bootstrap_datepicker_plus",  # required for date picker to work properly
]

LOCAL_APPS = [
    "va_explorer.home.apps.HomeConfig",
    "va_explorer.users.apps.UsersConfig",
    "va_explorer.va_analytics.apps.VaAnalyticsConfig",
    "va_explorer.va_data_management.apps.VaDataManagementConfig",
    "va_explorer.dhis_manager.apps.DhisManagerConfig",
    "va_explorer.va_export.apps.VaExportConfig",
    "va_explorer.va_data_cleanup.apps.VaDataCleanupConfig",
    "va_explorer.vacms.apps.VacmsConfig",
    "va_explorer.va_admin.apps.VaAdminConfig",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# Authentication

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
AUTH_USER_MODEL = "users.User"
LOGIN_REDIRECT_URL = "users:redirect"
LOGIN_URL = "account_login"
ACCOUNT_LOGOUT_REDIRECT_URL = "account_login"

# Passwords

PASSWORD_HASHERS = [
    # https://docs.djangoproject.com/en/dev/topics/auth/passwords/#using-argon2-with-django
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "va_explorer.users.validators.PasswordComplexityValidator"},
    {"NAME": "va_explorer.users.validators.PasswordHistoryValidator"},
]

# Middleware

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.common.BrokenLinkEmailsMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Track which user makes VA data edits
    "simple_history.middleware.HistoryRequestMiddleware",
    "debug_toolbar.middleware.DebugToolbarMiddleware",
    # Track authenticated user page views
    "va_explorer.utils.middleware.UserActivityMiddleware",
]

# Profiling middleware is dev-only — adds per-request overhead in production.
if env.bool("DJANGO_DEBUG", False):
    MIDDLEWARE.append("va_explorer.utils.middleware.DashboardProfilingMiddleware")


def _show_debug_toolbar(request):
    """Only show the toolbar for full HTML page loads, not writes or AJAX/JSON fan-out."""
    from django.conf import settings

    if not settings.DEBUG:
        return False
    # Keep the toolbar off mutating requests. The SQL panel can choke on very
    # large write queries during response rendering, and POST/PUT/PATCH/DELETE
    # responses are not the full-page inspection path we care about here.
    if request.method not in {"GET", "HEAD"}:
        return False
    # Skip for AJAX requests (dashboard component loads, filter-data, trends, map-data)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return False
    # Skip for paths that serve JSON — they receive no HTML to inject the toolbar into
    _json_prefixes = (
        "/national-operational/",
        "/regional-operations/",
        "/trends/",
        "/components/",
        "/va_analytics/api/",
    )
    if any(request.path.startswith(p) for p in _json_prefixes):
        return False
    from debug_toolbar.middleware import show_toolbar  # default check (INTERNAL_IPS)

    return show_toolbar(request)


DEBUG_TOOLBAR_CONFIG = {
    "DISABLE_PANELS": ["debug_toolbar.panels.redirects.RedirectsPanel"],
    "SHOW_TEMPLATE_CONTEXT": True,
    "SHOW_TOOLBAR_CALLBACK": "config.settings.base._show_debug_toolbar",
}

# Static

STATIC_ROOT = str(ROOT_DIR / "staticfiles")
STATIC_URL = "/static/"
STATICFILES_DIRS = [str(APPS_DIR / "static")]
STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]

# Media

MEDIA_ROOT = str(APPS_DIR / "media")
MEDIA_URL = "/media/"

# Directory where admin-panel CSV uploads are stored and command-input files are
# resolved from. Kept on the release-independent shared volume so uploads survive
# release swaps. Created on demand by va_admin.command_registry.data_dir().
ADMIN_PANEL_UPLOAD_DIR = os.environ.get(
    "ADMIN_PANEL_UPLOAD_DIR", "/srv/vacms/shared/media/uploads/manual"
)

# Templates

TEMPLATES = [
    {
        # https://docs.djangoproject.com/en/dev/ref/settings/#std:setting-TEMPLATES-BACKEND
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # https://docs.djangoproject.com/en/dev/ref/settings/#template-dirs
        "DIRS": [str(APPS_DIR / "templates")],
        "OPTIONS": {
            # https://docs.djangoproject.com/en/dev/ref/settings/#template-loaders
            # https://docs.djangoproject.com/en/dev/ref/templates/api/#loader-types
            "loaders": [
                "django.template.loaders.filesystem.Loader",
                "django.template.loaders.app_directories.Loader",
            ],
            # https://docs.djangoproject.com/en/dev/ref/settings/#template-context-processors
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.csrf",
                "django.contrib.auth.context_processors.auth",
                "django.template.context_processors.i18n",
                "django.template.context_processors.media",
                "django.template.context_processors.static",
                "django.template.context_processors.tz",
                "django.contrib.messages.context_processors.messages",
                "va_explorer.utils.context_processors.settings_context",
                "va_explorer.utils.context_processors.auto_detect_duplicates",
                "va_explorer.utils.context_processors.duplicates_count",
                "va_explorer.utils.context_processors.mailbox",
                "va_explorer.utils.context_processors.feedback_counts",
            ],
            "libraries": {
                "va_explorer_tags": "va_explorer.templatetags.va_explorer_tags"
            },
        },
    }
]

DEBUG_TOOLBAR_PANELS = [
    "debug_toolbar.panels.versions.VersionsPanel",
    "debug_toolbar.panels.timer.TimerPanel",
    "debug_toolbar.panels.settings.SettingsPanel",
    "debug_toolbar.panels.sql.SQLPanel",
    "debug_toolbar.panels.headers.HeadersPanel",
    "debug_toolbar.panels.request.RequestPanel",
    "debug_toolbar.panels.staticfiles.StaticFilesPanel",
    "debug_toolbar.panels.templates.TemplatesPanel",
    "debug_toolbar.panels.cache.CachePanel",
    "debug_toolbar.panels.signals.SignalsPanel",
    "debug_toolbar.panels.logging.LoggingPanel",
    "debug_toolbar.panels.redirects.RedirectsPanel",
]

FORM_RENDERER = "django.forms.renderers.TemplatesSetting"
CRISPY_TEMPLATE_PACK = "bootstrap4"

FIXTURE_DIRS = (str(APPS_DIR / "fixtures"),)

# Security

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SECURE_BROWSER_XSS_FILTER = True
# OSM public tile servers block requests when the browser omits Referer.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
DATA_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024  # 100 MB — prevent OOM on large imports

# Email

email_config = env.email_url("EMAIL_URL", default="consolemail://")
EMAIL_BACKEND = email_config["EMAIL_BACKEND"]
EMAIL_HOST_USER = email_config["EMAIL_HOST_USER"]
EMAIL_HOST_PASSWORD = email_config["EMAIL_HOST_PASSWORD"]
EMAIL_HOST = email_config["EMAIL_HOST"]
EMAIL_PORT = email_config["EMAIL_PORT"]
EMAIL_FILE_PATH = email_config["EMAIL_FILE_PATH"]
EMAIL_TIMEOUT = 5

DEFAULT_FROM_EMAIL = env(
    "DJANGO_DEFAULT_FROM_EMAIL", default="VA Explorer <noreply@vaexplorer.org>"
)
SERVER_EMAIL = env("DJANGO_SERVER_EMAIL", default=DEFAULT_FROM_EMAIL)
EMAIL_SUBJECT_PREFIX = env("DJANGO_EMAIL_SUBJECT_PREFIX", default="[VA Explorer] ")

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
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
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"level": "INFO", "handlers": ["console"]},
}

# Caches

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "",
        "OPTIONS": {
            "MAX_ENTRIES": 500,
            "CULL_FREQUENCY": 4,
        },
    }
}

# Celery
if USE_TZ:
    CELERY_TIMEZONE = TIME_ZONE
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://redis:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_RESULT_EXTENDED = True
CELERY_RESULT_BACKEND_ALWAYS_RETRY = True
CELERY_RESULT_BACKEND_MAX_RETRIES = 5
CELERY_RESULT_EXPIRES = 3600  # Expire task results after 1 hour to prevent Redis bloat
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

# 2700 = 45min before worker task exception + potential cleanup
# 3000 = 50min before forced termination of worker task
# Needed for long-running import & coding jobs
CELERY_TASK_TIME_LIMIT = 3000
CELERY_TASK_SOFT_TIME_LIMIT = 2700

CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_WORKER_SEND_TASK_EVENTS = True
CELERY_TASK_SEND_SENT_EVENT = True

# Allauth
ACCOUNT_ALLOW_REGISTRATION = env.bool("DJANGO_ACCOUNT_ALLOW_REGISTRATION", False)
ACCOUNT_AUTHENTICATION_METHOD = "email"
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_ADAPTER = "va_explorer.users.adapters.AccountAdapter"
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_USER_DISPLAY = lambda user: user.name  # noqa: E731 - allow this limited use
