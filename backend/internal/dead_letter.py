"""Handler-side dead-lettering + the retryable/terminal → HTTP envelope (14 §14.5).

Cloud Tasks has no native dead-letter queue, so a handler implements it: compare
``X-CloudTasks-TaskRetryCount`` against the queue's ``max_attempts`` and, on the
**final** attempt, record a ``TASK_FAILED`` (HIGH) operational exception and return
2xx to stop the retry loop — never silently dropped (specs/14 §14.5).

The retryable/terminal contract:

* **retryable** (transient Firestore/adapter error) → 5xx so Cloud Tasks retries,
  *until* the final attempt, at which point it is dead-lettered (2xx + exception).
* **terminal** (bad input, invariant violation) → 2xx immediately to stop retries,
  recording the failure so it is visible.

Inline mode has no retry header, so :func:`is_final_attempt` returns ``False`` and
inline exceptions propagate to the enqueuing caller (see :mod:`internal.enqueue`)
rather than being swallowed here.

The ``google.cloud`` / Firestore imports are lazy so this module ``py_compile``s
offline.
"""

from __future__ import annotations

import logging
from typing import Optional

from django.http import JsonResponse

from internal.enqueue import max_attempts_for

logger = logging.getLogger("bsw.internal")

# Cloud Tasks sets this to the number of *retries* so far — 0 on first delivery.
RETRY_COUNT_HEADER = "HTTP_X_CLOUDTASKS_TASKRETRYCOUNT"


def is_final_attempt(request, task: str) -> bool:
    """Is this the last delivery Cloud Tasks will make for ``task``?

    Reads ``X-CloudTasks-TaskRetryCount`` (retries-so-far, 0-based) against the
    queue's ``max_attempts``. Header **absent** (inline, or a non-Cloud-Tasks
    caller) → ``False`` so inline exceptions propagate instead of dead-lettering.
    """
    raw = request.META.get(RETRY_COUNT_HEADER) if request is not None else None
    if raw is None:
        return False
    try:
        retry_count = int(raw)
    except (TypeError, ValueError):
        return False
    # Attempts are numbered 1..max_attempts; retry_count is one less than the
    # current attempt number, so the final attempt is retry_count == max-1.
    return retry_count >= max_attempts_for(task) - 1


def task_response(
    *,
    retryable: bool,
    ctx,
    task: str,
    request=None,
    entity_id: Optional[str] = None,
    error: Optional[str] = None,
    client=None,
) -> JsonResponse:
    """Map a handler outcome to the Cloud Tasks HTTP envelope (specs/14 §14.5).

    * ``retryable`` transient error, NOT the final attempt → 503 (Cloud Tasks
      retries).
    * ``retryable`` transient error ON the final attempt → dead-letter it
      (record ``TASK_FAILED``, return 200 to stop retries).
    * terminal error (``retryable=False``) → record ``TASK_FAILED``, return 200.

    ``entity_id`` scopes the recorded exception to the affected entity when the
    caller knows it (else the task name is used).
    """
    correlation_id = getattr(ctx, "correlation_id", None)

    if retryable and not is_final_attempt(request, task):
        logger.warning(
            "task=%s transient failure, will retry correlation=%s error=%s",
            task, correlation_id, error,
        )
        return JsonResponse(
            {
                "status": "RETRY",
                "task": task,
                "correlationId": correlation_id,
                "error": error,
            },
            status=503,
        )

    # Terminal, or a retryable error exhausted on the final attempt: dead-letter.
    reason = "terminal" if not retryable else "retries exhausted"
    logger.error(
        "task=%s dead-lettered (%s) correlation=%s entity=%s error=%s",
        task, reason, correlation_id, entity_id, error,
    )
    _record_task_failed(ctx=ctx, task=task, entity_id=entity_id, error=error, client=client)
    return JsonResponse(
        {
            "status": "DEAD_LETTERED",
            "task": task,
            "reason": reason,
            "correlationId": correlation_id,
            "error": error,
        },
        status=200,
    )


def _record_task_failed(*, ctx, task: str, entity_id: Optional[str], error, client) -> None:
    """Record a ``TASK_FAILED`` HIGH operational exception (specs/14 §14.5).

    Best-effort and self-contained: it reuses the existing deterministic upsert
    in :mod:`exceptions.service` (importable without edits) inside its own small
    transaction, and never lets a recording failure change the 2xx dead-letter
    response. ``entity_id`` defaults to the task name so the deterministic id is
    ``{task}__TASK_FAILED``.

    TODO(U2+/later slices): pass full entity context (loan/borrower/employer) from
    the specific handler's payload so the exception carries actionable mirrors.
    """
    try:
        from commands.base import transactional
        from common.enums import ExceptionType
        from common.firestore import get_client
        from exceptions import service as exceptions_service

        active_client = client if client is not None else get_client()
        eid = entity_id or task

        def _run(txn):
            exceptions_service.upsert(
                txn,
                active_client,
                exception_type=ExceptionType.TASK_FAILED,
                entity_type="task",
                entity_id=eid,
                summary=f"Async task {task} failed",
                details=str(error) if error is not None else None,
                loan_id=None,
                borrower_id=None,
                borrower_name=None,
                employer_id=None,
                employer_name=None,
            )

        transactional(active_client)(_run)()
    except Exception:  # noqa: BLE001 — recording is best-effort; never mask the 2xx
        logger.exception(
            "failed to record TASK_FAILED exception for task=%s entity=%s",
            task, entity_id,
        )
