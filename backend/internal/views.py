"""Base ``/internal/*`` handlers for Cloud Tasks + Scheduler (specs/14, specs/12).

Two entrypoints, both plain Django views (not DRF — ingress auth is handled by
:class:`~firebase_auth.middleware.InternalOIDCMiddleware`, which is already
fail-closed; do NOT re-authenticate here):

* ``POST /internal/tasks/<task>`` — run one Cloud Tasks unit of work.
* ``POST /internal/jobs/<job>``   — run one Cloud Scheduler job (which typically
  enqueues tasks).

Both parse the JSON payload, mint a SYSTEM ``system_ctx(name)``, invoke the
registered callable (the SAME callable ``internal.enqueue`` runs inline — the seam
that makes CI mirror prod), and translate the outcome through the
:mod:`internal.dead_letter` envelope: transient failures retry (5xx) until the
final attempt, terminal failures stop (2xx) and are dead-lettered.

Authority is enforced twice: ``InternalOIDCMiddleware`` verifies the caller at
ingress and stamps ``request.internal_verified``; each handler then re-asserts it
via :func:`~commands.authz.require_system_or_role` before minting SYSTEM, so a
bypassed/misconfigured middleware fails closed (403) rather than running SYSTEM
for an unauthenticated caller.
"""

from __future__ import annotations

import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from commands.authz import require_system_or_role
from commands.base import CommandError, OperationInProgress, StaleWrite
from common.errors import DomainError
from firebase_auth.permissions import ADMINISTRATOR
from internal import dead_letter
from internal.enqueue import SCHEDULER_JOBS, TASK_HANDLERS
from internal.system_context import system_ctx

logger = logging.getLogger("bsw.internal")

# CommandErrors a redelivery could still resolve → retry (5xx). Everything else
# (validation, not-found, invariant, transition, authority) is a terminal
# business outcome → dead-letter (2xx). specs/14 §14.5.
RETRYABLE_COMMAND_ERRORS = (OperationInProgress, StaleWrite)


def _parse_payload(request) -> dict:
    """Decode the JSON request body to a dict (empty body → ``{}``)."""
    body = request.body
    if not body:
        return {}
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("task payload must be a JSON object")
    return data


def _dispatch(request, name: str, registry: dict, *, kind: str) -> JsonResponse:
    """Shared task/job handler: verify ingress → parse → run → dead-letter envelope."""
    # --- command-boundary authority (specs/12 §12.5, defense-in-depth) --------
    # InternalOIDCMiddleware stamps request.internal_verified once it authorizes
    # the OIDC invoker (cloud) or dev secret (emulator). We mint SYSTEM ONLY when
    # that marker is present, then re-assert authority: a request that bypassed
    # the middleware yields a non-SYSTEM ctx and require_system_or_role fails
    # closed with a 403. This is an INGRESS failure, not a task business failure,
    # so it is a hard 403 — never a dead-lettered 2xx.
    verified = getattr(request, "internal_verified", False)
    ctx = system_ctx(name, verified=verified)
    try:
        require_system_or_role(ctx, min_role=ADMINISTRATOR)
    except CommandError as exc:
        logger.error(
            "/internal %s '%s' denied at command boundary (verified=%s): %s",
            kind, name, verified, exc,
        )
        return JsonResponse(
            {"detail": str(exc), "correlationId": ctx.correlation_id},
            status=exc.http_status,
        )
    # -------------------------------------------------------------------------

    handler = registry.get(name)
    if handler is None:
        logger.warning("unknown %s: %s", kind, name)
        return JsonResponse(
            {"status": "UNKNOWN", "error": f"unknown {kind}: {name}",
             "correlationId": ctx.correlation_id},
            status=404,
        )

    try:
        payload = _parse_payload(request)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # Malformed payload is terminal — retrying won't fix it (specs/14 §14.5).
        return dead_letter.task_response(
            retryable=False, ctx=ctx, task=name, request=request, error=str(exc),
        )

    try:
        result = handler(payload, ctx)
    except (CommandError, DomainError) as exc:
        # Transient conflicts (lease held, optimistic-concurrency) can succeed on
        # redelivery → retry; every other business/validation outcome is terminal.
        retryable = isinstance(exc, RETRYABLE_COMMAND_ERRORS) or (
            getattr(exc, "http_status", 400) >= 500
        )
        return dead_letter.task_response(
            retryable=retryable, ctx=ctx, task=name, request=request, error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 — treat unknown errors as transient
        return dead_letter.task_response(
            retryable=True, ctx=ctx, task=name, request=request, error=str(exc),
        )

    return JsonResponse(
        {"status": "OK", "correlationId": ctx.correlation_id, "result": result},
        status=200,
    )


@csrf_exempt
@require_POST
def task_handler(request, task: str) -> JsonResponse:
    """``POST /internal/tasks/<task>`` — run a Cloud Tasks unit of work."""
    return _dispatch(request, task, TASK_HANDLERS, kind="task")


@csrf_exempt
@require_POST
def job_handler(request, job: str) -> JsonResponse:
    """``POST /internal/jobs/<job>`` — run a Cloud Scheduler job."""
    return _dispatch(request, job, SCHEDULER_JOBS, kind="job")
