"""``/internal`` URL map — Cloud Tasks + Scheduler handlers (specs/14, specs/12).

Mounted by ``config.urls`` at the ``internal/`` prefix. Every route here is
internet-reachable and executes as SYSTEM, so it is protected at ingress by the
fail-closed :class:`~firebase_auth.middleware.InternalOIDCMiddleware` (specs/12
§12.5) — the views add no auth of their own.

* ``POST /internal/tasks/<task>`` — one Cloud Tasks unit of work.
* ``POST /internal/jobs/<job>``   — one Cloud Scheduler job.

The ``noop`` task/job (registered in ``internal.enqueue``) prove the round-trip.
"""

from __future__ import annotations

from django.urls import path

from internal import views

app_name = "internal"

urlpatterns = [
    path("tasks/<str:task>", views.task_handler, name="task"),
    path("jobs/<str:job>", views.job_handler, name="job"),
]
