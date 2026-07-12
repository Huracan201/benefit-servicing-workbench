"""notes.views — HTTP surface for the add-servicing-note command (specs/11).

``POST /loans/{loanId}/notes`` (``OPERATIONS_USER``+, ``Idempotency-Key``
required). Thin: it authenticates (project-default Firebase auth), enforces the
role, validates the body (``text`` non-empty), builds the
:class:`commands.base.CommandContext`, invokes :func:`notes.services.add_note`,
and maps a :class:`commands.base.CommandError` to the specs/11 §11.3 response.

A successful add returns ``201`` with the created note. An in-progress same-key
replay yields ``202`` with a ``Retry-After`` hint (specs/08 §8.2).
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from commands.base import (
    MAX_FREETEXT_LEN,
    CommandContext,
    CommandError,
    OperationInProgress,
    ValidationError,
)
from firebase_auth.permissions import RequireOperations

from .services import add_note


def _actor_name(user) -> str:
    """Best-effort display name for the audit trail (frozen onto the note/event)."""
    claims = getattr(user, "claims", {}) or {}
    return claims.get("name") or getattr(user, "email", None) or getattr(user, "uid", "")


class AddNoteView(APIView):
    """``POST /loans/{loanId}/notes`` (specs/10 §10.5)."""

    permission_classes = [RequireOperations]

    def post(self, request: Request, loan_id: str) -> Response:
        correlation_id = getattr(request, "correlation_id", None)

        idempotency_key = request.headers.get("Idempotency-Key", "")
        # Missing OR whitespace-only is a 400; validate on a stripped copy but
        # pass the RAW header value through so the stored key == the client's.
        if not idempotency_key.strip():
            err = ValidationError(
                "Idempotency-Key header is required",
                code="IDEMPOTENCY_KEY_REQUIRED",
            )
            return Response(err.to_body(correlation_id), status=err.http_status)

        body = request.data if isinstance(request.data, dict) else {}
        text = body.get("text")
        # specs/10 §10.5 — note text must be present and non-empty (empty or
        # whitespace-only is rejected 400).
        if not isinstance(text, str) or not text.strip():
            err = ValidationError(
                "note text is required and must be non-empty",
                code="NOTE_TEXT_REQUIRED",
            )
            return Response(err.to_body(correlation_id), status=err.http_status)
        if len(text) > MAX_FREETEXT_LEN:
            err = ValidationError(
                f"note text must be at most {MAX_FREETEXT_LEN} characters",
                code="NOTE_TEXT_TOO_LONG",
            )
            return Response(err.to_body(correlation_id), status=err.http_status)

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
            result = add_note(loan_id=loan_id, text=text, ctx=ctx)
        except OperationInProgress as exc:
            response = Response(
                exc.to_body(ctx.correlation_id), status=exc.http_status
            )
            response["Retry-After"] = str(exc.retry_after)
            return response
        except CommandError as exc:
            return Response(exc.to_body(ctx.correlation_id), status=exc.http_status)

        return Response(result, status=status.HTTP_201_CREATED)
