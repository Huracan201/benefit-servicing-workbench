"""Django AppConfig for the ``projections`` app.

``projections`` owns the read-model recompute engine (specs/05): the pure
source-derivation functions (:mod:`projections.recompute`) behind the
``update-projection`` task and the ``rebuild-summaries`` job, plus the fan-out that
maps a servicing event to the summary keys it dirties. It holds no ORM models —
Firestore is the only datastore (see specs/02, specs/04). Adding it to
``INSTALLED_APPS`` is safe: no models, no migrations.
"""

from __future__ import annotations

from django.apps import AppConfig


class ProjectionsConfig(AppConfig):
    name = "projections"
    label = "projections"
    verbose_name = "BenefitServicing Read-Model Projections"
