"""
Root URL configuration.

Phase 1 wired the operational endpoints (health/readiness) via core.urls; Phase 2
added the /api/v1/ business commands (specs/11); Phase 3 adds the /internal/
Cloud Tasks + Scheduler handlers (specs/14), fail-closed at ingress via
firebase_auth.middleware.InternalOIDCMiddleware (specs/12 §12.5).
"""

from django.urls import include, path

urlpatterns = [
    # Operational: GET /health, GET /readiness (specs/16 §16.5).
    path("", include("core.urls")),
    # Phase 2: business command endpoints (specs/11 §11.4).
    path("api/v1/", include("api.urls")),
    # Phase 3: Cloud Tasks + Scheduler handlers (specs/14). Ingress is
    # fail-closed via firebase_auth.middleware.InternalOIDCMiddleware (specs/12 §12.5).
    path("internal/", include("internal.urls")),
]
