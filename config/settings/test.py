"""
With these settings, tests run faster.
"""

from .base import *  # noqa
from .base import ROOT_DIR, env

# General

SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="TSjr7VIP1j98nKm5dc3AqbkZKXwMAguZh1M2UALvL1GITCNFl3LHnNgfcfCRiHy6",
)

TEST_RUNNER = "django.test.runner.DiscoverRunner"

# Passwords

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

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

# Email

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Media

# Keep admin-panel upload writes inside the repo during tests rather than the
# shared system volume used in production.
ADMIN_PANEL_UPLOAD_DIR = str(ROOT_DIR / ".test-uploads")
