"""``/internal`` URL map — Cloud Tasks + Scheduler handlers (specs/14, specs/12).

Mounted by ``config.urls`` at the ``internal/`` prefix. Every route here is
internet-reachable and executes as SYSTEM, so it is protected at ingress by the
fail-closed :class:`~firebase_auth.middleware.InternalOIDCMiddleware` (specs/12
§12.5) — the views add no auth of their own.

* ``POST /internal/tasks/<task>`` — one Cloud Tasks unit of work.
* ``POST /internal/jobs/<job>``   — one Cloud Scheduler job.

The ``<task>`` / ``<job>`` captures dispatch by name against the
:data:`internal.enqueue.TASK_HANDLERS` / :data:`~internal.enqueue.SCHEDULER_JOBS`
registries, so a single route serves every registered name (an unknown name is a
clean 404 in :func:`internal.views._dispatch`, not a routing miss). The names in
play (registered at :mod:`internal.enqueue` import):

* tasks — ``generate-schedule``, ``process-contribution``, ``reconcile-contribution``,
  ``cancel-future-contributions``, ``shift-schedule`` (+ the ``noop`` round-trip);
* jobs — ``enqueue-due-contributions``, ``reconcile-stuck-payments``,
  ``reap-expired-leases`` (+ ``noop``).
"""

from __future__ import annotations

from django.urls import path

from internal import views

app_name = "internal"

urlpatterns = [
    path("tasks/<str:task>", views.task_handler, name="task"),
    path("jobs/<str:job>", views.job_handler, name="job"),
]
