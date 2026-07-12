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

# Secure by default: a real deploy need only OMIT these to stay locked down. A
# missed env var can no longer silently enable traceback disclosure (DEBUG) or a
# wildcard Host (Host-header poisoning); the guardrail at the end of this module
# additionally refuses to boot on a leftover default under ENVIRONMENT=production.
DEBUG = env_bool("DEBUG", False)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")

# specs/21 §21.3: real deployments set ENVIRONMENT=production, which arms the
# fail-closed configuration guardrail at the end of this module. CI, local dev,
# and the emulator leave it "development" and keep these convenient defaults.
ENVIRONMENT = env_str("ENVIRONMENT", "development")

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
    # Phase 2 — domain command layer (specs/19 §19.2).
    "servicing",
    "exceptions",
    "payments",
    "benefits",
    "contributions",
    "notes",
    "administration",
    "employment",
    "seed",
    # Phase 3 — async infrastructure foundation (specs/14, specs/21 §21.5).
    "internal",
    # Phase 3 — read-model projection layer (specs/05): the recompute engine +
    # fanout behind the update-projection task and the rebuild-summaries job.
    "projections",
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
# Cache — backs DRF's ScopedRateThrottle request counters (specs/19 Phase 3).
# ---------------------------------------------------------------------------
# The ONLY consumer today is the mutating-endpoint rate limiter below. NOTE:
# LocMemCache is per-process, so on multi-instance Cloud Run each instance keeps
# its OWN counters and the effective ceiling is (rate x instance count). That is
# an acceptable coarse limit for the demo; a real deploy points CACHES at a
# shared Memorystore/Redis so the limit is enforced fleet-wide.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "bsw-throttle",
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
    "DEFAULT_PAGINATION_CLASS": "core.pagination.CappedLimitOffsetPagination",
    # specs/21 §21.1: REST pagination default 50, max 200.
    "PAGE_SIZE": 50,
    "UNAUTHENTICATED_USER": None,
    # Any exception a view does not itself translate renders as a generic
    # INTERNAL_ERROR 500 with the detail logged server-side — never a traceback
    # in the body, even if DEBUG is on (specs/11 §11.3, specs/16).
    "EXCEPTION_HANDLER": "core.exception_handler.custom_exception_handler",
    # Rate limiting on the mutating command endpoints (specs/19 Phase-3 security
    # prerequisite; security-review-phase-1-2 §7/§8). ScopedRateThrottle throttles
    # ONLY views that declare a ``throttle_scope``; scope-less views (reads,
    # /health, /readiness, and the /internal task handlers) always pass through.
    # A Throttled(429) is rendered centrally by the EXCEPTION_HANDLER above, so no
    # view needs per-request throttle handling. Each per-scope rate is
    # env-overridable so a deploy can tune (or disable, via a high value) a scope
    # without a code change.
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "payments-write": env_str("THROTTLE_PAYMENTS_WRITE", "60/min"),
        "benefit-write": env_str("THROTTLE_BENEFIT_WRITE", "60/min"),
        "employment-write": env_str("THROTTLE_EMPLOYMENT_WRITE", "60/min"),
        "exception-write": env_str("THROTTLE_EXCEPTION_WRITE", "60/min"),
        "note-write": env_str("THROTTLE_NOTE_WRITE", "60/min"),
        # Admin commands are rare and high-impact -> a lower default ceiling.
        "admin-write": env_str("THROTTLE_ADMIN_WRITE", "30/min"),
    },
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
# Cloud Tasks queue location (specs/21 §21.2 — region us-east4).
TASKS_LOCATION = env_str("TASKS_LOCATION", "us-east4")
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


# ---------------------------------------------------------------------------
# Production configuration guardrail (specs/21 §21.3 — fail closed)
# ---------------------------------------------------------------------------
# When ENVIRONMENT=production, refuse to boot on any leftover development
# default rather than silently running insecure: DEBUG would disclose
# tracebacks, a wildcard/empty ALLOWED_HOSTS invites Host-header poisoning, and
# the shared dev secrets must never reach a real deployment. CI, local dev, and
# the emulator leave ENVIRONMENT unset ("development") and are unaffected.
if ENVIRONMENT == "production":
    from django.core.exceptions import ImproperlyConfigured

    _misconfigured = []
    if DEBUG:
        _misconfigured.append("DEBUG must be 0")
    if SECRET_KEY == "dev-insecure-secret-key-change-me-in-production":
        _misconfigured.append("DJANGO_SECRET_KEY must be set (not the dev default)")
    _dev_allowed_hosts = {"localhost", "127.0.0.1", "testserver"}
    if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS or set(ALLOWED_HOSTS) <= _dev_allowed_hosts:
        _misconfigured.append(
            "ALLOWED_HOSTS must be an explicit non-wildcard list (not the dev default)"
        )
    if INTERNAL_DEV_SECRET == "dev-internal-secret":
        _misconfigured.append("INTERNAL_DEV_SECRET must be set (not the dev default)")
    if _misconfigured:
        raise ImproperlyConfigured(
            "Insecure production configuration — refusing to start: "
            + "; ".join(_misconfigured)
        )
