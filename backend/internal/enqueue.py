"""The enqueue seam + task/job registries + queue config (specs/14, specs/21).

``enqueue(task, payload, *, ctx)`` is the single door through which a command
schedules deferred work. It dispatches on ``settings.TASK_EXECUTION_MODE``:

* **inline** (auto under the emulator, and on CI) — look the task up in
  :data:`TASK_HANDLERS` and run it **synchronously**, with a freshly minted
  ``system_ctx(task)``. This is the *same* callable, invoked the *same* way, that
  the cloud ``/internal/tasks/<task>`` view runs (see :mod:`internal.views`) — the
  load-bearing seam that makes CI(inline) mirror prod(cloud). Exceptions
  propagate to the enqueuing caller (there is no retry envelope inline;
  ``dead_letter.is_final_attempt`` reads *not final* when the Cloud Tasks retry
  header is absent).
* **cloud** — mint a Cloud Tasks ``CreateTask`` POST to ``/internal/tasks/<task>``
  carrying an OIDC token for ``TASKS_INVOKER_SA`` (``aud == TASKS_AUDIENCE``), which
  the fail-closed :class:`~firebase_auth.middleware.InternalOIDCMiddleware` verifies
  at ingress (specs/12 §12.5). The ``google.cloud.tasks`` import is lazy; if the
  client library is absent, cloud mode raises a clear error.

:data:`QUEUE_CONFIG` pins each task's queue name, ``max_attempts``, and backoff
(specs/21 §21.2); ``max_attempts`` is what :func:`internal.dead_letter.is_final_attempt`
reads to decide final-attempt dead-lettering.

Registration point: later slices register their real handlers via
:func:`register_task` / :func:`register_job` (search for ``REGISTRATION POINT``).
Today only the ``noop`` round-trip task/job are registered.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger("bsw.internal")

# URL prefixes the handlers are mounted at (see internal.urls / config.urls).
INTERNAL_TASKS_PREFIX = "/internal/tasks/"
INTERNAL_JOBS_PREFIX = "/internal/jobs/"
# Propagate the originating command's correlation id to the async handler for
# tracing (the handler's OWN events use its namespaced system correlation).
CORRELATION_HEADER = "X-Correlation-Id"

# Handler signature (both registries): ``callable(payload: dict, ctx) -> Any``.
# Jobs ignore ``payload`` (scheduler triggers carry none) but share the shape so
# internal.views can dispatch tasks and jobs uniformly.
Handler = Callable[[dict, Any], Any]


# ---------------------------------------------------------------------------
# Queue config (specs/21 §21.2, pinned). Backoff is documentation of the GCP
# queue's configuration; max_attempts is read by dead_letter.is_final_attempt.
# ---------------------------------------------------------------------------
DEFAULT_MAX_ATTEMPTS = 5

QUEUE_CONFIG: dict[str, dict[str, Any]] = {
    "generate-schedule": {"queue": "generate-schedule", "max_attempts": 5, "backoff": (5, 60)},
    "process-contribution": {"queue": "process-contribution", "max_attempts": 5, "backoff": (10, 300)},
    "reconcile-contribution": {"queue": "reconcile-contribution", "max_attempts": 3, "backoff": (30, 30)},
    "cancel-future-contributions": {"queue": "cancel-future-contributions", "max_attempts": 5, "backoff": (10, 10)},
    "shift-schedule": {"queue": "shift-schedule", "max_attempts": 5, "backoff": (10, 10)},
    "propagate-denormalized": {"queue": "propagate-denormalized", "max_attempts": 3, "backoff": (30, 30)},
    "update-projection": {"queue": "update-projection", "max_attempts": 3, "backoff": (5, 5)},
    # Foundation round-trip task (specs/14 dev loop); harmless in cloud too.
    "noop": {"queue": "noop", "max_attempts": 3, "backoff": (5, 30)},
}


def max_attempts_for(task: str) -> int:
    """Queue ``max_attempts`` for ``task`` (specs/21 §21.2), or the default."""
    return int(QUEUE_CONFIG.get(task, {}).get("max_attempts", DEFAULT_MAX_ATTEMPTS))


# ---------------------------------------------------------------------------
# Registries. TASK_HANDLERS is the inline dispatch table AND the set the cloud
# task view invokes; SCHEDULER_JOBS is what ``manage.py run_job`` fires.
# ---------------------------------------------------------------------------
TASK_HANDLERS: dict[str, Handler] = {}
SCHEDULER_JOBS: dict[str, Handler] = {}


def register_task(name: str, handler: Handler) -> None:
    """Register a Cloud Tasks handler under ``name`` (idempotent overwrite)."""
    TASK_HANDLERS[name] = handler


def register_job(name: str, handler: Handler) -> None:
    """Register a Cloud Scheduler job under ``name`` (idempotent overwrite)."""
    SCHEDULER_JOBS[name] = handler


# ---------------------------------------------------------------------------
# enqueue() — dispatch on TASK_EXECUTION_MODE.
# ---------------------------------------------------------------------------
def enqueue(
    task: str, payload: dict, *, ctx, name: Optional[str] = None
) -> Optional[dict]:
    """Schedule ``task`` with ``payload`` (specs/14 §14.3).

    ``ctx`` is the *enqueuing* command's context — used for tracing (its
    correlation id is propagated) but NOT reused as the handler's actor: the
    handler runs as SYSTEM under a freshly minted ``system_ctx(task)`` in both
    modes, so an inline run mirrors a cloud run exactly.

    **Return value (the command seam relies on this — completion protocol step 3).**
    In **inline** mode the handler runs synchronously and its result dict is
    returned, so an async command can render its completed 200 immediately. In
    **cloud** mode the task is only *scheduled*, so ``None`` is returned and the
    command answers ``202`` (the client polls the same idempotency key until the
    task completes it). A command therefore chooses 200-vs-202 purely on whether
    ``enqueue`` returned a result.

    ``name`` (cloud only) sets a deterministic Cloud Tasks task name so a
    duplicate enqueue of the same unit of work (e.g. the same stuck contribution
    scanned by two overlapping ``reconcile-stuck-payments`` runs) is de-duplicated
    by Cloud Tasks. It is ignored inline (the caller de-dupes within its run).
    """
    from django.conf import settings

    mode = getattr(settings, "TASK_EXECUTION_MODE", "cloud")
    origin = getattr(ctx, "correlation_id", None)
    logger.info(
        "enqueue task=%s mode=%s origin_correlation=%s name=%s",
        task, mode, origin, name,
    )
    if mode == "inline":
        return _enqueue_inline(task, payload or {})
    _enqueue_cloud(task, payload or {}, origin_correlation=origin, name=name)
    return None


def _enqueue_inline(task: str, payload: dict) -> Optional[dict]:
    """Run the task's handler synchronously, exactly as the cloud view would.

    Returns the handler's result dict so the enqueuing command can complete
    (200) instead of polling (202) — the CI(inline)↔prod(cloud) seam.
    """
    from internal.system_context import system_ctx

    handler = TASK_HANDLERS.get(task)
    if handler is None:
        raise ValueError(f"no inline handler registered for task {task!r}")
    # Fresh SYSTEM context per run — mirrors internal.views. Exceptions propagate
    # to the caller (inline has no Cloud Tasks retry envelope).
    return handler(payload, system_ctx(task))


def _enqueue_cloud(
    task: str,
    payload: dict,
    *,
    origin_correlation: str | None,
    name: Optional[str] = None,
) -> None:
    """Create a Cloud Task POSTing to ``/internal/tasks/<task>`` with OIDC auth."""
    cfg = QUEUE_CONFIG.get(task)
    if cfg is None:
        raise ValueError(f"no queue configured for task {task!r}")

    try:
        from google.cloud import tasks_v2  # lazy: absent in the offline sandbox
    except ImportError as exc:  # pragma: no cover - exercised only in cloud mode
        raise RuntimeError(
            "TASK_EXECUTION_MODE=cloud requires the google-cloud-tasks client "
            "library, which is not installed."
        ) from exc

    from django.conf import settings

    audience = settings.TASKS_AUDIENCE
    invoker_sa = settings.TASKS_INVOKER_SA
    if not audience or not invoker_sa:
        raise RuntimeError(
            "TASK_EXECUTION_MODE=cloud requires TASKS_AUDIENCE and "
            "TASKS_INVOKER_SA to be configured."
        )

    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(
        settings.GOOGLE_CLOUD_PROJECT, settings.TASKS_LOCATION, cfg["queue"],
    )
    url = audience.rstrip("/") + f"{INTERNAL_TASKS_PREFIX}{task}"
    headers = {"Content-Type": "application/json"}
    if origin_correlation:
        headers[CORRELATION_HEADER] = origin_correlation
    task_body: dict[str, Any] = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": url,
            "headers": headers,
            "body": json.dumps(payload).encode("utf-8"),
            # Google-signed OIDC token the ingress middleware verifies.
            "oidc_token": {
                "service_account_email": invoker_sa,
                "audience": audience,
            },
        }
    }
    if name:
        # Deterministic task name → Cloud Tasks de-duplicates a same-name enqueue
        # within its de-dup window (specs/14 §14.2 reconcile-stuck-payments).
        task_body["name"] = client.task_path(
            settings.GOOGLE_CLOUD_PROJECT, settings.TASKS_LOCATION, cfg["queue"], name,
        )
    client.create_task(request={"parent": parent, "task": task_body})


# ---------------------------------------------------------------------------
# Foundation handlers — the noop round-trip that proves the inline+cloud seam.
# ---------------------------------------------------------------------------
def _noop_task(payload: dict, ctx) -> dict:
    """Trivial task proving the enqueue → handler → callable path (specs/14)."""
    logger.info(
        "noop task ran correlation=%s payload=%s", ctx.correlation_id, payload,
    )
    return {"task": "noop", "ok": True, "echo": payload}


def _noop_job(payload: dict, ctx) -> dict:
    """Trivial scheduler job: enqueues one noop task (proves the full loop)."""
    enqueue("noop", {"from": "noop-job"}, ctx=ctx)
    return {"job": "noop", "enqueued": 1}


# --- REGISTRATION POINT ------------------------------------------------------
# The foundation round-trip, plus every Phase-3 Cloud Tasks handler + Cloud
# Scheduler job. The task/job bodies live in :mod:`internal.tasks` /
# :mod:`internal.jobs` (imported here, at the bottom, so those modules can
# lazily import this one without a cycle); their (payload, ctx) callables are the
# SAME ones the ``/internal/tasks|jobs/<name>`` views invoke.
register_task("noop", _noop_task)
register_job("noop", _noop_job)


def _register_phase3_handlers() -> None:
    """Register the Phase-3 tasks + jobs (called once at import)."""
    from internal import jobs, tasks

    # Cloud Tasks — per-item units of work (specs/14 §14.3).
    register_task("generate-schedule", tasks.generate_schedule_task)
    register_task("process-contribution", tasks.process_contribution_task)
    register_task("reconcile-contribution", tasks.reconcile_contribution_task)
    register_task("cancel-future-contributions", tasks.cancel_future_contributions_task)
    register_task("shift-schedule", tasks.shift_schedule_task)

    # Cloud Scheduler — time-triggered jobs (specs/14 §14.2).
    register_job("enqueue-due-contributions", jobs.enqueue_due_contributions)
    register_job("reconcile-stuck-payments", jobs.reconcile_stuck_payments)
    register_job("reap-expired-leases", jobs.reap_expired_leases_job)


_register_phase3_handlers()
