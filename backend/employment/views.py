"""employment.views — HTTP surface for the employment-status command (specs/11).

``POST /borrowers/{borrowerId}/employment-status`` (``SERVICING_MANAGER``+,
``Idempotency-Key`` required). Thin: it authenticates (project-default Firebase
auth), enforces the role, builds the :class:`commands.base.CommandContext`
(correlation id + request hash + idempotency key), invokes
:func:`employment.services.change_employment_status`, and maps a
:class:`commands.base.CommandError` to the specs/11 §11.3 response.

Request body ``{ status, effectiveDate, reason }`` (specs/10 §10.4). The command
runs its benefit cascade + bounded inline follow-up synchronously (Phase 2), so
a success returns ``200`` with the resulting borrower/benefit summary; an
in-progress same-key replay yields ``202`` with a ``Retry-After`` hint.
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
from firebase_auth.permissions import RequireManager

from .services import change_employment_status


def _actor_name(user) -> str:
    """Best-effort display name for the audit trail (frozen onto events)."""
    claims = getattr(user, "claims", {}) or {}
    return claims.get("name") or getattr(user, "email", None) or getattr(user, "uid", "")


class EmploymentStatusView(APIView):
    """``POST /borrowers/{borrowerId}/employment-status`` (specs/10 §10.4)."""

    permission_classes = [RequireManager]
    throttle_scope = "employment-write"

    def post(self, request: Request, borrower_id: str) -> Response:
        correlation_id = getattr(request, "correlation_id", None)

        idempotency_key = request.headers.get("Idempotency-Key", "")
        if not idempotency_key.strip():
            err = ValidationError(
                "Idempotency-Key header is required",
                code="IDEMPOTENCY_KEY_REQUIRED",
            )
            return Response(err.to_body(correlation_id), status=err.http_status)

        body = request.data if isinstance(request.data, dict) else {}
        user = request.user
        ctx = CommandContext.build(
            actor_id=getattr(user, "uid", ""),
            actor_role=getattr(user, "role", None) or "",
            actor_name=_actor_name(user),
            method="POST",
            path=request.path,
            body=body,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

        try:
            result = change_employment_status(
                borrower_id=borrower_id,
                ctx=ctx,
                status=body.get("status"),
                effective_date=body.get("effectiveDate"),
                reason=body.get("reason"),
            )
        except OperationInProgress as exc:
            response = Response(exc.to_body(ctx.correlation_id), status=exc.http_status)
            response["Retry-After"] = str(exc.retry_after)
            return response
        except CommandError as exc:
            return Response(exc.to_body(ctx.correlation_id), status=exc.http_status)
        return Response(result, status=status.HTTP_200_OK)
