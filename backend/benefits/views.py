"""benefits.views — HTTP surface for benefit-agreement commands (specs/11).

``POST /benefit-agreements/{agreementId}/activate`` (``SERVICING_MANAGER``+,
``Idempotency-Key`` required). Thin: it authenticates (project-default Firebase
auth), enforces the role, builds the :class:`commands.base.CommandContext`
(correlation id + request hash + idempotency key), invokes
:func:`benefits.services.activate_benefit`, and maps a
:class:`commands.base.CommandError` to the specs/11 §11.3 response.

For Phase 2 the schedule is generated inline, so a successful activation returns
``200`` with the now-``ACTIVE`` agreement (specs/10 §10.1 — "inline is fine to
return 200"). An in-progress same-key replay still yields ``202`` with a
``Retry-After`` hint.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from commands.base import CommandContext, CommandError, OperationInProgress, ValidationError
from firebase_auth.permissions import RequireManager

from .services import (
    activate_benefit,
    resume_benefit,
    suspend_benefit,
    terminate_benefit,
)


def _actor_name(user) -> str:
    """Best-effort display name for the audit trail (frozen onto events)."""
    claims = getattr(user, "claims", {}) or {}
    return claims.get("name") or getattr(user, "email", None) or getattr(user, "uid", "")


def _build_ctx(request: Request, correlation_id):
    """Build the :class:`CommandContext` from the request (shared by all views).

    Returns ``(ctx, None)`` on success, or ``(None, error_response)`` when the
    ``Idempotency-Key`` header is missing/whitespace-only (specs/11 §11.2 → 400).
    The RAW header value is stored so the persisted key == the client's.
    """
    idempotency_key = request.headers.get("Idempotency-Key", "")
    if not idempotency_key.strip():
        err = ValidationError(
            "Idempotency-Key header is required",
            code="IDEMPOTENCY_KEY_REQUIRED",
        )
        return None, Response(err.to_body(correlation_id), status=err.http_status)

    user = request.user
    ctx = CommandContext.build(
        actor_id=getattr(user, "uid", ""),
        actor_role=getattr(user, "role", None),
        actor_name=_actor_name(user),
        method="POST",
        path=request.path,
        body=request.data if isinstance(request.data, dict) else None,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )
    return ctx, None


def _respond(command, agreement_id: str, ctx) -> Response:
    """Invoke ``command(agreement_id, ctx)`` and map the result/CommandError.

    A success is ``200`` with the command body; an in-progress replay is ``202``
    with a ``Retry-After`` header; any other :class:`CommandError` is the typed
    specs/11 §11.3 envelope at its HTTP status.
    """
    try:
        result = command(agreement_id=agreement_id, ctx=ctx)
    except OperationInProgress as exc:
        response = Response(exc.to_body(ctx.correlation_id), status=exc.http_status)
        response["Retry-After"] = str(exc.retry_after)
        return response
    except CommandError as exc:
        return Response(exc.to_body(ctx.correlation_id), status=exc.http_status)
    return Response(result, status=status.HTTP_200_OK)


class ActivateBenefitView(APIView):
    """``POST /benefit-agreements/{agreementId}/activate`` (specs/10 §10.1)."""

    permission_classes = [RequireManager]

    def post(self, request: Request, agreement_id: str) -> Response:
        correlation_id = getattr(request, "correlation_id", None)
        ctx, err = _build_ctx(request, correlation_id)
        if err is not None:
            return err
        return _respond(activate_benefit, agreement_id, ctx)


class SuspendBenefitView(APIView):
    """``POST /benefit-agreements/{agreementId}/suspend`` (specs/10 §10.2)."""

    permission_classes = [RequireManager]

    def post(self, request: Request, agreement_id: str) -> Response:
        correlation_id = getattr(request, "correlation_id", None)
        ctx, err = _build_ctx(request, correlation_id)
        if err is not None:
            return err
        return _respond(suspend_benefit, agreement_id, ctx)


class ResumeBenefitView(APIView):
    """``POST /benefit-agreements/{agreementId}/resume`` (specs/10 §10.2)."""

    permission_classes = [RequireManager]

    def post(self, request: Request, agreement_id: str) -> Response:
        correlation_id = getattr(request, "correlation_id", None)
        ctx, err = _build_ctx(request, correlation_id)
        if err is not None:
            return err
        return _respond(resume_benefit, agreement_id, ctx)


class TerminateBenefitView(APIView):
    """``POST /benefit-agreements/{agreementId}/terminate`` (specs/10 §10.3)."""

    permission_classes = [RequireManager]

    def post(self, request: Request, agreement_id: str) -> Response:
        correlation_id = getattr(request, "correlation_id", None)
        ctx, err = _build_ctx(request, correlation_id)
        if err is not None:
            return err
        return _respond(terminate_benefit, agreement_id, ctx)
