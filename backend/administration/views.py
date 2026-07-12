"""administration.views — HTTP surface for the ADMIN-only commands (specs/11 §11.4).

``POST /admin/users/{uid}/role`` and ``POST /admin/employers/{employerId}/status``
(both ``ADMINISTRATOR`` only, both ``Idempotency-Key`` required). Thin views that
authenticate (project-default Firebase auth), enforce ``RequireAdmin``, validate
the ``Idempotency-Key`` header (missing/whitespace-only → ``400``, RAW key passed
through), build the :class:`commands.base.CommandContext`, invoke the service, and
map a :class:`commands.base.CommandError` to the specs/11 §11.3 response
(``202`` + ``Retry-After`` for an in-progress replay).
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from commands.base import (
    CommandContext,
    CommandError,
    OperationInProgress,
    ValidationError,
)
from firebase_auth.permissions import RequireAdmin

from .services import set_employer_status, set_user_role


def _actor_name(user) -> str:
    """Best-effort display name for the audit trail (frozen onto events)."""
    claims = getattr(user, "claims", {}) or {}
    return claims.get("name") or getattr(user, "email", None) or getattr(user, "uid", "")


def _require_idempotency_key(request: Request, correlation_id):
    """Return the RAW ``Idempotency-Key`` header, or a ``400`` Response if absent.

    Missing OR whitespace-only is a ``400`` (specs/11 §11.2); the check is on a
    stripped copy but the RAW header value is returned so the stored key matches
    the client's exactly.
    """
    idempotency_key = request.headers.get("Idempotency-Key", "")
    if not idempotency_key.strip():
        err = ValidationError(
            "Idempotency-Key header is required",
            code="IDEMPOTENCY_KEY_REQUIRED",
        )
        return None, Response(err.to_body(correlation_id), status=err.http_status)
    return idempotency_key, None


def _dispatch(result_or_error, ctx):
    """Map an ``OperationInProgress``/``CommandError`` to its specs/11 response."""
    if isinstance(result_or_error, OperationInProgress):
        response = Response(
            result_or_error.to_body(ctx.correlation_id),
            status=result_or_error.http_status,
        )
        response["Retry-After"] = str(result_or_error.retry_after)
        return response
    return Response(
        result_or_error.to_body(ctx.correlation_id), status=result_or_error.http_status
    )


class SetUserRoleView(APIView):
    """``POST /admin/users/{uid}/role`` (specs/12 §12.3) — body ``{"role": ...}``."""

    permission_classes = [RequireAdmin]
    throttle_scope = "admin-write"

    def post(self, request: Request, uid: str) -> Response:
        correlation_id = getattr(request, "correlation_id", None)

        idempotency_key, error = _require_idempotency_key(request, correlation_id)
        if error is not None:
            return error

        body = request.data if isinstance(request.data, dict) else {}
        role = body.get("role", "")

        user = request.user
        ctx = CommandContext.build(
            actor_id=getattr(user, "uid", ""),
            actor_role=getattr(user, "role", None),
            actor_name=_actor_name(user),
            method="POST",
            path=request.path,
            body=body,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

        try:
            result = set_user_role(uid=uid, role=role, ctx=ctx)
        except CommandError as exc:
            return _dispatch(exc, ctx)

        return Response(result, status=status.HTTP_200_OK)


class SetEmployerStatusView(APIView):
    """``POST /admin/employers/{employerId}/status`` (specs/06 §6.6a, §11.4)."""

    permission_classes = [RequireAdmin]
    throttle_scope = "admin-write"

    def post(self, request: Request, employer_id: str) -> Response:
        correlation_id = getattr(request, "correlation_id", None)

        idempotency_key, error = _require_idempotency_key(request, correlation_id)
        if error is not None:
            return error

        body = request.data if isinstance(request.data, dict) else {}
        new_status = body.get("status", "")

        user = request.user
        ctx = CommandContext.build(
            actor_id=getattr(user, "uid", ""),
            actor_role=getattr(user, "role", None),
            actor_name=_actor_name(user),
            method="POST",
            path=request.path,
            body=body,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

        try:
            result = set_employer_status(
                employer_id=employer_id, status=new_status, ctx=ctx
            )
        except CommandError as exc:
            return _dispatch(exc, ctx)

        return Response(result, status=status.HTTP_200_OK)
