"""Routes for the liveness/readiness endpoints — specs/16 §16.5.

The project URLconf includes this module at the root so the endpoints resolve at
``GET /health`` and ``GET /readiness`` (see specs/openapi.yaml).
"""

from __future__ import annotations

from django.urls import path

from core import views

app_name = "core"

urlpatterns = [
    path("health", views.health, name="health"),
    path("readiness", views.readiness, name="readiness"),
]
