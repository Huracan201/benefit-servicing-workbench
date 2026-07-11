"""
Root URL configuration.

Phase 1 wires only the operational endpoints (health/readiness) via core.urls.
The /api/v1/ (business commands, specs/11) and /internal/ (Cloud Tasks +
Scheduler handlers, specs/14) prefixes are reserved here; their routes land in
Phase 2.
"""

from django.urls import include, path

urlpatterns = [
    # Operational: GET /health, GET /readiness (specs/16 §16.5).
    path("", include("core.urls")),
    # Reserved — Phase 2:
    # path("api/v1/", include("api.urls")),      # business command endpoints (specs/11)
    # path("internal/", include("internal.urls")),  # task + scheduler handlers (specs/14)
]
