"""Django AppConfig for the ``internal`` app.

``internal`` owns the async infrastructure foundation (specs/14): the enqueue
seam, the SYSTEM context, the dead-letter envelope, and the ``/internal/*``
task/scheduler handlers. It holds no ORM models — Firestore is the only
datastore (see specs/02, specs/04). Adding it to ``INSTALLED_APPS`` is safe:
no models, no migrations.
"""

from __future__ import annotations

from django.apps import AppConfig


class InternalConfig(AppConfig):
    name = "internal"
    label = "internal"
    verbose_name = "BenefitServicing Async Infrastructure"
