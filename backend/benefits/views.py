"""benefits.views — HTTP surface for benefit-agreement commands (specs/11).

``POST /benefit-agreements/{agreementId}/activate`` (``SERVICING_MANAGER``+,
``Idempotency-Key`` required). Thin: it authenticates (project-default Firebase
auth), enforces the role, builds the :class:`commands.base.CommandContext`
(correlation id + request hash + idempotency key), invokes
:func:`benefits.services.activate_benefit`, and maps a
:class:`commands.base.CommandError` to the specs/11 §11.3 response.

The command hands its follow-up (schedule generation on activate; schedule-shift
on resume; cancel-future on terminate) onto an async task (COMPLETION PROTOCOL,
Decision A). Under ``TASK_EXECUTION_MODE=inline`` (CI + the emulator) the task
runs synchronously and the command returns ``200``; under ``cloud`` the task is
deferred and the command raises :class:`OperationInProgress`, which
:func:`_respond` renders as ``202`` + ``Retry-After`` (the client polls the same
idempotency key). An in-progress same-key replay likewise yields ``202``.
"""

from __future__ import annotations

import logging
import time

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from commands.base import CommandContext, CommandError, OperationInProgress, ValidationError
from core.logging_utils import log_event
from firebase_auth.permissions import RequireManager

from .services import (
    activate_benefit,
    resume_benefit,
    suspend_benefit,
    terminate_benefit,
)

# The shared command logger — the completion line every command emits via `_respond` below
# turns the specs/16 §16.2 structured-logging substrate live on the command surface
# (payments/benefits/notes/admin route through this helper).
_logger = logging.getLogger("bsw.command")


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

    # Optional If-Match → expected_revision (optimistic concurrency, specs/08 §8.4). Absent =
    # no precondition; a non-integer value is a client error (400) rather than a silent skip.
    if_match = request.headers.get("If-Match", "").strip()
    expected_revision = None
    if if_match:
        try:
            expected_revision = int(if_match.strip('"'))
        except ValueError:
            err = ValidationError(
                "If-Match must be an integer revision", code="INVALID_IF_MATCH"
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
        expected_revision=expected_revision,
    )
    return ctx, None


def _respond(command, agreement_id: str, ctx) -> Response:
    """Invoke ``command(agreement_id, ctx)`` and map the result/CommandError.

    A success (inline follow-up) is ``200`` with the command body; a deferred
    follow-up (cloud) or an in-progress same-key replay surfaces as
    :class:`OperationInProgress` → ``202`` with a ``Retry-After`` header; any
    other :class:`CommandError` is the typed specs/11 §11.3 envelope at its HTTP
    status.
    """
    started = time.monotonic()
    operation = getattr(command, "__name__", "command")

    def _log(result: str, *, level: int = logging.INFO, error_code=None) -> None:
        # One structured completion line per command (specs/16 §16.2) — turns the built-but-
        # unwired logging substrate live on the command surface, incl. the two-phase payment.
        log_event(
            _logger,
            level,
            "command completed",
            operation=operation,
            entityId=agreement_id,
            result=result,
            durationMs=round((time.monotonic() - started) * 1000),
            correlationId=ctx.correlation_id,
            idempotencyKey=ctx.idempotency_key,
            errorCode=error_code,
        )

    try:
        result = command(agreement_id=agreement_id, ctx=ctx)
    except OperationInProgress as exc:
        _log("IN_PROGRESS")
        response = Response(exc.to_body(ctx.correlation_id), status=exc.http_status)
        response["Retry-After"] = str(exc.retry_after)
        return response
    except CommandError as exc:
        _log("ERROR", level=logging.WARNING, error_code=exc.code)
        return Response(exc.to_body(ctx.correlation_id), status=exc.http_status)
    _log("OK")
    return Response(result, status=status.HTTP_200_OK)


class ActivateBenefitView(APIView):
    """``POST /benefit-agreements/{agreementId}/activate`` (specs/10 §10.1)."""

    permission_classes = [RequireManager]
    throttle_scope = "benefit-write"

    def post(self, request: Request, agreement_id: str) -> Response:
        correlation_id = getattr(request, "correlation_id", None)
        ctx, err = _build_ctx(request, correlation_id)
        if err is not None:
            return err
        return _respond(activate_benefit, agreement_id, ctx)


class SuspendBenefitView(APIView):
    """``POST /benefit-agreements/{agreementId}/suspend`` (specs/10 §10.2)."""

    permission_classes = [RequireManager]
    throttle_scope = "benefit-write"

    def post(self, request: Request, agreement_id: str) -> Response:
        correlation_id = getattr(request, "correlation_id", None)
        ctx, err = _build_ctx(request, correlation_id)
        if err is not None:
            return err
        return _respond(suspend_benefit, agreement_id, ctx)


class ResumeBenefitView(APIView):
    """``POST /benefit-agreements/{agreementId}/resume`` (specs/10 §10.2)."""

    permission_classes = [RequireManager]
    throttle_scope = "benefit-write"

    def post(self, request: Request, agreement_id: str) -> Response:
        correlation_id = getattr(request, "correlation_id", None)
        ctx, err = _build_ctx(request, correlation_id)
        if err is not None:
            return err
        return _respond(resume_benefit, agreement_id, ctx)


class TerminateBenefitView(APIView):
    """``POST /benefit-agreements/{agreementId}/terminate`` (specs/10 §10.3)."""

    permission_classes = [RequireManager]
    throttle_scope = "benefit-write"

    def post(self, request: Request, agreement_id: str) -> Response:
        correlation_id = getattr(request, "correlation_id", None)
        ctx, err = _build_ctx(request, correlation_id)
        if err is not None:
            return err
        return _respond(terminate_benefit, agreement_id, ctx)
