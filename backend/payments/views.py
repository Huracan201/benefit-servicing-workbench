"""DRF write-path views for contribution processing (specs/11 §11.4, specs/09).

Two mutating command endpoints:

* ``POST /contributions/{contributionId}/process``  — ``SERVICING_MANAGER``+
* ``POST /contributions/{contributionId}/retry``    — ``OPERATIONS_USER``+

Both require the ``Idempotency-Key`` header (missing ⇒ ``400
IDEMPOTENCY_KEY_REQUIRED``, specs/11 §11.2) and render :class:`CommandError`
subclasses to their pinned HTTP status + the specs/11 §11.3 error envelope
``{ "error": { code, message, correlationId } }``.

**A declined payment is not an HTTP error.** ``process`` returns ``200`` with a
body whose ``status`` is ``FAILED`` (or ``CANCELED``) — the command *succeeded*;
the business outcome is a decline (specs/09 §9.1). Only malformed requests,
illegal transitions, and conflicts map to 4xx.

The URL is wired by the routing/wiring agent (``backend/config/urls.py``); these
view classes are the importable seam it references.
"""

from __future__ import annotations

import logging
import time
import uuid

from rest_framework.response import Response
from rest_framework.views import APIView

from commands.base import (
    CommandContext,
    CommandError,
    OperationInProgress,
    ValidationError,
)
from core.logging_utils import log_event
from firebase_auth.authentication import FirebaseAuthentication
from firebase_auth.permissions import RequireManager, RequireOperations
from payments.service import process_contribution, retry_contribution

IDEMPOTENCY_HEADER = "Idempotency-Key"
CORRELATION_HEADER = "X-Correlation-Id"


def _actor_name(user) -> str:
    claims = getattr(user, "claims", {}) or {}
    return (
        claims.get("name")
        or claims.get("displayName")
        or getattr(user, "email", None)
        or getattr(user, "uid", "unknown")
    )


def _contribution_id(kwargs: dict):
    return kwargs.get("contributionId") or kwargs.get("contribution_id")


_logger = logging.getLogger("bsw.command")


def _error_response(exc: CommandError, correlation_id) -> Response:
    resp = Response(exc.to_body(correlation_id), status=exc.http_status)
    if isinstance(exc, OperationInProgress):
        resp["Retry-After"] = str(exc.retry_after)
    return resp


def _dispatch(request, contribution_id, command) -> Response:
    # Reuse the id CorrelationIdMiddleware already resolved (honouring an inbound
    # X-Correlation-Id or minting one); only fall back if middleware is absent.
    correlation_id = (
        getattr(request, "correlation_id", None)
        or request.headers.get(CORRELATION_HEADER)
        or uuid.uuid4().hex
    )

    idempotency_key = request.headers.get(IDEMPOTENCY_HEADER)
    # Missing OR whitespace-only is a 400; validate on a stripped copy but pass
    # the RAW header value through so the stored idempotency key == the client's.
    if not idempotency_key or not idempotency_key.strip():
        return _error_response(
            ValidationError(
                "Idempotency-Key header is required",
                code="IDEMPOTENCY_KEY_REQUIRED",
            ),
            correlation_id,
        )
    if not contribution_id:
        return _error_response(
            ValidationError("contributionId path parameter is required"),
            correlation_id,
        )

    user = request.user
    body = request.data if isinstance(getattr(request, "data", None), dict) else {}
    ctx = CommandContext.build(
        actor_id=getattr(user, "uid", None),
        actor_role=getattr(user, "role", None),
        actor_name=_actor_name(user),
        method="POST",
        path=request.path,
        body=body or None,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )

    started = time.monotonic()
    operation = getattr(command, "__name__", "command")

    def _log(result: str, *, level: int = logging.INFO, error_code=None) -> None:
        # Structured completion line on the two-phase-payment path (specs/16 §16.2) — the
        # money path previously emitted nothing.
        log_event(
            _logger,
            level,
            "command completed",
            operation=operation,
            entityId=contribution_id,
            result=result,
            durationMs=round((time.monotonic() - started) * 1000),
            correlationId=ctx.correlation_id,
            idempotencyKey=ctx.idempotency_key,
            errorCode=error_code,
        )

    try:
        result = command(contribution_id, ctx)
    except CommandError as exc:
        in_progress = exc.http_status == 202
        _log(
            "IN_PROGRESS" if in_progress else "ERROR",
            level=logging.INFO if in_progress else logging.WARNING,
            error_code=None if in_progress else exc.code,
        )
        return _error_response(exc, correlation_id)
    _log("OK")
    return Response(result, status=200)


class ProcessContributionView(APIView):
    """``POST /contributions/{contributionId}/process`` — MANAGER+ (specs/11)."""

    authentication_classes = [FirebaseAuthentication]
    permission_classes = [RequireManager]
    throttle_scope = "payments-write"

    def post(self, request, *args, **kwargs) -> Response:
        return _dispatch(request, _contribution_id(kwargs), process_contribution)


class RetryContributionView(APIView):
    """``POST /contributions/{contributionId}/retry`` — OPERATIONS+ (specs/11)."""

    authentication_classes = [FirebaseAuthentication]
    permission_classes = [RequireOperations]
    throttle_scope = "payments-write"

    def post(self, request, *args, **kwargs) -> Response:
        return _dispatch(request, _contribution_id(kwargs), retry_contribution)
