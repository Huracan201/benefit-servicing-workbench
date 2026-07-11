"""Django AppConfig for the ``core`` app.

``core`` owns cross-cutting request plumbing (correlation-id middleware,
structured logging), the liveness/readiness endpoints, and the Firestore
document TypedDicts (``core.schema``). It holds no ORM models — Firestore is
the only datastore (see specs/02, specs/04).
"""

from __future__ import annotations

from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"
    label = "core"
    verbose_name = "BenefitServicing Core"
