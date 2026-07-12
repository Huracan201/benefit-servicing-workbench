"""exceptions.views — HTTP surface for the operational-exception commands (specs/11 §11.4).

Five thin ``OPERATIONS_USER``+ command endpoints, each requiring an
``Idempotency-Key`` header (specs/11 §11.2):

* ``POST /exceptions``                      — create a manual exception.
* ``POST /exceptions/{id}/assign``          — set/clear ``assignedTo`` (status-neutral).
* ``POST /exceptions/{id}/mark-in-review``  — ``OPEN`` → ``IN_REVIEW``.
* ``POST /exceptions/{id}/resolve``         — → ``RESOLVED``.
* ``POST /exceptions/{id}/dismiss``         — → ``DISMISSED``.

Each view authenticates (project-default Firebase auth), enforces the role,
validates the request body (returning ``400`` on malformed input before any
transaction runs), builds the :class:`commands.base.CommandContext`, invokes the
matching :mod:`exceptions.commands` service, and maps a
:class:`commands.base.CommandError` to the specs/11 §11.3 response. An in-progress
same-key replay yields ``202`` with a ``Retry-After`` hint.
"""

from __future__ import annotations

from typing import Any, Optional

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from commands.base import (
    MAX_FREETEXT_LEN,
    MAX_IDENTIFIER_LEN,
    MAX_SHORTTEXT_LEN,
    CommandContext,
    CommandError,
    OperationInProgress,
    ValidationError,
)
from common.enums import ExceptionType, Severity
from firebase_auth.permissions import RequireOperations

from . import commands as exception_commands


def _actor_name(user) -> str:
    """Best-effort display name for the audit trail (frozen onto events)."""
    claims = getattr(user, "claims", {}) or {}
    return claims.get("name") or getattr(user, "email", None) or getattr(user, "uid", "")


def _require_idempotency_key(request: Request, correlation_id: Optional[str]):
    """Return the RAW Idempotency-Key, or a 400 Response when missing/blank.

    Missing OR whitespace-only is a ``400 IDEMPOTENCY_KEY_REQUIRED`` (specs/11
    §11.2); the raw header value is stored so the persisted key == the client's.
    """
    idempotency_key = request.headers.get("Idempotency-Key", "")
    if not idempotency_key.strip():
        err = ValidationError(
            "Idempotency-Key header is required",
            code="IDEMPOTENCY_KEY_REQUIRED",
        )
        return None, Response(err.to_body(correlation_id), status=err.http_status)
    return idempotency_key, None


def _build_ctx(request: Request, idempotency_key: str, correlation_id, body) -> CommandContext:
    user = request.user
    return CommandContext.build(
        actor_id=getattr(user, "uid", ""),
        actor_role=getattr(user, "role", None),
        actor_name=_actor_name(user),
        method="POST",
        path=request.path,
        body=body if isinstance(body, dict) else None,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )


def _respond(result: dict, correlation_id) -> Response:
    return Response(result, status=status.HTTP_200_OK)


def _error_response(exc: CommandError, correlation_id) -> Response:
    if isinstance(exc, OperationInProgress):
        response = Response(exc.to_body(correlation_id), status=exc.http_status)
        response["Retry-After"] = str(exc.retry_after)
        return response
    return Response(exc.to_body(correlation_id), status=exc.http_status)


def _str_field(
    body: dict, name: str, *, required: bool, max_len: int = MAX_SHORTTEXT_LEN
) -> Optional[str]:
    """Extract a required/optional non-empty string field, or raise 400.

    ``max_len`` bounds the accepted length (DoS guard, specs/06 §6.4): an
    over-long value is a 400, never a persisted unbounded document.
    """
    value = body.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValidationError(f"{name} is required")
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    if len(value) > max_len:
        raise ValidationError(f"{name} must be at most {max_len} characters")
    return value


class CreateExceptionView(APIView):
    """``POST /exceptions`` — create a manual operational exception (specs/11 §11.4)."""

    permission_classes = [RequireOperations]

    def post(self, request: Request) -> Response:
        correlation_id = getattr(request, "correlation_id", None)
        idempotency_key, err = _require_idempotency_key(request, correlation_id)
        if err is not None:
            return err

        body = request.data if isinstance(request.data, dict) else {}
        try:
            exception_type = _str_field(
                body, "exceptionType", required=True, max_len=MAX_IDENTIFIER_LEN
            )
            entity_type = _str_field(
                body, "entityType", required=True, max_len=MAX_IDENTIFIER_LEN
            )
            entity_id = _str_field(
                body, "entityId", required=True, max_len=MAX_IDENTIFIER_LEN
            )
            summary = _str_field(body, "summary", required=True)
            details = _str_field(
                body, "details", required=False, max_len=MAX_FREETEXT_LEN
            )
            # Validate the enums up-front so a bad value is a 400, not a 500.
            try:
                ExceptionType(exception_type)
            except ValueError:
                raise ValidationError(f"unknown exceptionType {exception_type!r}")
            severity = body.get("severity")
            if severity is not None:
                try:
                    Severity(severity)
                except ValueError:
                    raise ValidationError(f"unknown severity {severity!r}")
        except ValidationError as exc:
            return Response(exc.to_body(correlation_id), status=exc.http_status)

        ctx = _build_ctx(request, idempotency_key, correlation_id, body)
        try:
            result = exception_commands.create_exception(
                ctx=ctx,
                exception_type=exception_type,
                entity_type=entity_type,
                entity_id=entity_id,
                summary=summary,
                details=details,
                severity=severity,
            )
        except CommandError as exc:
            return _error_response(exc, ctx.correlation_id)
        return _respond(result, ctx.correlation_id)


class AssignExceptionView(APIView):
    """``POST /exceptions/{exceptionId}/assign`` — status-neutral (specs/06 §6.4)."""

    permission_classes = [RequireOperations]

    def post(self, request: Request, exception_id: str) -> Response:
        correlation_id = getattr(request, "correlation_id", None)
        idempotency_key, err = _require_idempotency_key(request, correlation_id)
        if err is not None:
            return err

        body = request.data if isinstance(request.data, dict) else {}
        user = request.user
        # assignToUid semantics (specs/11 §11.4): key omitted -> self; explicit
        # null -> unassign; a uid string -> that uid.
        if "assignToUid" not in body:
            assign_to: Optional[str] = getattr(user, "uid", "")
        else:
            raw = body["assignToUid"]
            if raw is None:
                assign_to = None
            elif isinstance(raw, str) and raw.strip():
                assign_to = raw
            else:
                err_v = ValidationError("assignToUid must be a uid string or null")
                return Response(err_v.to_body(correlation_id), status=err_v.http_status)

        ctx = _build_ctx(request, idempotency_key, correlation_id, body)
        try:
            result = exception_commands.assign_exception(
                exception_id, ctx=ctx, assign_to=assign_to
            )
        except CommandError as exc:
            return _error_response(exc, ctx.correlation_id)
        return _respond(result, ctx.correlation_id)


class MarkInReviewView(APIView):
    """``POST /exceptions/{exceptionId}/mark-in-review`` (``OPEN`` → ``IN_REVIEW``)."""

    permission_classes = [RequireOperations]

    def post(self, request: Request, exception_id: str) -> Response:
        correlation_id = getattr(request, "correlation_id", None)
        idempotency_key, err = _require_idempotency_key(request, correlation_id)
        if err is not None:
            return err

        body = request.data if isinstance(request.data, dict) else {}
        ctx = _build_ctx(request, idempotency_key, correlation_id, body)
        try:
            result = exception_commands.mark_in_review(exception_id, ctx=ctx)
        except CommandError as exc:
            return _error_response(exc, ctx.correlation_id)
        return _respond(result, ctx.correlation_id)


class ResolveExceptionView(APIView):
    """``POST /exceptions/{exceptionId}/resolve`` (→ ``RESOLVED``)."""

    permission_classes = [RequireOperations]

    def post(self, request: Request, exception_id: str) -> Response:
        correlation_id = getattr(request, "correlation_id", None)
        idempotency_key, err = _require_idempotency_key(request, correlation_id)
        if err is not None:
            return err

        body = request.data if isinstance(request.data, dict) else {}
        note = body.get("note")
        if note is not None and not isinstance(note, str):
            err_v = ValidationError("note must be a string")
            return Response(err_v.to_body(correlation_id), status=err_v.http_status)
        if isinstance(note, str) and len(note) > MAX_SHORTTEXT_LEN:
            err_v = ValidationError(
                f"note must be at most {MAX_SHORTTEXT_LEN} characters"
            )
            return Response(err_v.to_body(correlation_id), status=err_v.http_status)

        ctx = _build_ctx(request, idempotency_key, correlation_id, body)
        try:
            result = exception_commands.resolve_exception(
                exception_id, ctx=ctx, note=note
            )
        except CommandError as exc:
            return _error_response(exc, ctx.correlation_id)
        return _respond(result, ctx.correlation_id)


class DismissExceptionView(APIView):
    """``POST /exceptions/{exceptionId}/dismiss`` (→ ``DISMISSED``)."""

    permission_classes = [RequireOperations]

    def post(self, request: Request, exception_id: str) -> Response:
        correlation_id = getattr(request, "correlation_id", None)
        idempotency_key, err = _require_idempotency_key(request, correlation_id)
        if err is not None:
            return err

        body = request.data if isinstance(request.data, dict) else {}
        try:
            reason = _str_field(body, "reason", required=True)
        except ValidationError as exc:
            return Response(exc.to_body(correlation_id), status=exc.http_status)

        ctx = _build_ctx(request, idempotency_key, correlation_id, body)
        try:
            result = exception_commands.dismiss_exception(
                exception_id, ctx=ctx, reason=reason
            )
        except CommandError as exc:
            return _error_response(exc, ctx.correlation_id)
        return _respond(result, ctx.correlation_id)
