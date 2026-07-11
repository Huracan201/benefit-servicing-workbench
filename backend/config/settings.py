"""
Django settings for the BenefitServicing Workbench backend.

Phase 1 foundation: a lean, bootable Django project. Firestore is the ONLY
datastore — there are NO Django ORM models, and django.contrib.admin/auth/
sessions/contenttypes are intentionally absent. DATABASES points at a dummy
in-memory sqlite that is never used (the ORM stays wired up only so Django's
own machinery — e.g. `manage.py check` — is happy).

All configuration is read from the environment (see backend/.env.example and
specs/21 §21.3). Dev-friendly defaults are provided so the project boots with
no env set; production (demo) supplies real values via the environment /
Secret Manager.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Environment helpers (os.environ only — no third-party config dependency)
# ---------------------------------------------------------------------------
def env_str(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(key: str, default: str = "") -> list[str]:
    raw = os.environ.get(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
# Dev literal default; demo supplies DJANGO_SECRET_KEY via Secret Manager.
SECRET_KEY = env_str(
    "DJANGO_SECRET_KEY",
    "dev-insecure-secret-key-change-me-in-production",
)

DEBUG = env_bool("DEBUG", True)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "*")

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# The backend runs behind Cloud Run's TLS-terminating proxy.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
# NOTE: no django.contrib.admin/auth/sessions/contenttypes/staticfiles.
# Firestore is the datastore; Firebase Auth is the identity provider.
INSTALLED_APPS = [
    "rest_framework",
    "corsheaders",
    "common",
    "firebase_auth",
    "core",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "core.middleware.CorrelationIdMiddleware",
    "firebase_auth.middleware.InternalOIDCMiddleware",
]

# DRF needs a template backend for its browsable API / exception rendering.
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]


# ---------------------------------------------------------------------------
# Database — DUMMY. Firestore is the system of record; the ORM is never used.
# An in-memory sqlite keeps Django's checks/management commands happy without
# ever touching a real database.
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}


# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "firebase_auth.authentication.FirebaseAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    # specs/21 §21.1: REST pagination default 50, max 200.
    "PAGE_SIZE": 50,
    "UNAUTHENTICATED_USER": None,
}


# ---------------------------------------------------------------------------
# CORS (specs/21 §21.3 — normative). django-cors-headers.
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
CORS_ALLOW_CREDENTIALS = False
CORS_ALLOW_HEADERS = [
    "Authorization",
    "Content-Type",
    "Idempotency-Key",
    "If-Match",
    "X-Correlation-Id",
]
# The 202 poll contract is browser-invisible without exposing Retry-After.
CORS_EXPOSE_HEADERS = ["Retry-After"]


# ---------------------------------------------------------------------------
# Internationalization / time
# ---------------------------------------------------------------------------
# SYSTEM_TIMEZONE is the business calendar (period labels, scheduledDate).
# Django's TIME_ZONE is set to UTC; business-tz math lives in common.periods.
SYSTEM_TIMEZONE = env_str("SYSTEM_TIMEZONE", "America/New_York")
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True


# ---------------------------------------------------------------------------
# Firestore / GCP / Firebase
# ---------------------------------------------------------------------------
GOOGLE_CLOUD_PROJECT = env_str(
    "GOOGLE_CLOUD_PROJECT", "demo-benefitservicing-workbench"
)
# Presence of FIRESTORE_EMULATOR_HOST switches the stack into offline/dev mode
# (emulator-aware client, dev-secret internal auth bypass).
FIRESTORE_EMULATOR_HOST = env_str("FIRESTORE_EMULATOR_HOST", "")
FIREBASE_AUTH_EMULATOR_HOST = env_str("FIREBASE_AUTH_EMULATOR_HOST", "")


# ---------------------------------------------------------------------------
# Async task execution (specs/14, specs/21 §21.3)
# ---------------------------------------------------------------------------
# "inline" runs task handlers synchronously in-process (auto under emulator);
# "cloud" enqueues to Cloud Tasks.
TASK_EXECUTION_MODE = env_str(
    "TASK_EXECUTION_MODE", "inline" if FIRESTORE_EMULATOR_HOST else "cloud"
)
TASKS_AUDIENCE = env_str("TASKS_AUDIENCE", "")
TASKS_INVOKER_SA = env_str("TASKS_INVOKER_SA", "")
# Shared-secret used for /internal/* auth when running under the emulator.
INTERNAL_DEV_SECRET = env_str("INTERNAL_DEV_SECRET", "dev-internal-secret")


# ---------------------------------------------------------------------------
# Structured JSON logging (specs/16 §16.2)
# ---------------------------------------------------------------------------
LOG_LEVEL = env_str("LOG_LEVEL", "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "core.logging_utils.StructuredLogFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        # Application logger namespace for the domain apps.
        "bsw": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}
