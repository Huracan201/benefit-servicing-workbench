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

import uuid

from rest_framework.response import Response
from rest_framework.views import APIView

from commands.base import (
    CommandContext,
    CommandError,
    OperationInProgress,
    ValidationError,
)
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


def _error_response(exc: CommandError, correlation_id) -> Response:
    resp = Response(exc.to_body(correlation_id), status=exc.http_status)
    if isinstance(exc, OperationInProgress):
        resp["Retry-After"] = str(exc.retry_after)
    return resp


def _dispatch(request, contribution_id, command) -> Response:
    correlation_id = request.headers.get(CORRELATION_HEADER) or uuid.uuid4().hex

    idempotency_key = request.headers.get(IDEMPOTENCY_HEADER)
    if not idempotency_key:
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

    try:
        result = command(contribution_id, ctx)
    except CommandError as exc:
        return _error_response(exc, correlation_id)
    return Response(result, status=200)


class ProcessContributionView(APIView):
    """``POST /contributions/{contributionId}/process`` — MANAGER+ (specs/11)."""

    authentication_classes = [FirebaseAuthentication]
    permission_classes = [RequireManager]

    def post(self, request, *args, **kwargs) -> Response:
        return _dispatch(request, _contribution_id(kwargs), process_contribution)


class RetryContributionView(APIView):
    """``POST /contributions/{contributionId}/retry`` — OPERATIONS+ (specs/11)."""

    authentication_classes = [FirebaseAuthentication]
    permission_classes = [RequireOperations]

    def post(self, request, *args, **kwargs) -> Response:
        return _dispatch(request, _contribution_id(kwargs), retry_contribution)
