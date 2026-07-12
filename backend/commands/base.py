"""Command-layer primitives shared by every domain command (specs/08, specs/11).

This module is the seam ``backend/commands/base.py`` referenced by the Phase-2
plan. It provides:

* :func:`request_hash` — the normative ``requestHash`` (specs/08 §8.2):
  ``SHA-256( method + "\\n" + path + "\\n" + canonical-JSON(body) )``. Including
  the **path** is essential: empty-body commands (``process``) must not be able
  to replay a key against a different entity.
* :class:`CommandError` and its subclasses, each carrying an ``http_status`` and
  a stable error ``code`` so views can render the specs/11 §11.3 error body
  ``{ "error": { "code", "message", "correlationId" } }``.
* :func:`from_domain_error` — maps the pure-core domain errors
  (:class:`common.errors.InvalidTransition` / :class:`~common.errors.InvariantViolation`)
  onto their command-layer HTTP equivalents.
* :class:`CommandContext` — the per-request identity bundle threaded through a
  handler (actor, correlation id, idempotency key + request hash, lease owner).
* :func:`transactional` — a thin wrapper over ``@firestore.transactional`` that
  obtains a transaction from a client and drives the handler with retries.

Keep this module import-light: the ``google.cloud`` import is lazy so the module
imports cleanly in environments where the package is absent.
"""

from __future__ import annotations

import functools
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Pinned constants (specs/21 §21.1) that the command layer needs by value.
# ---------------------------------------------------------------------------
LEASE_TTL_SECONDS = 120           # sync commands
ASYNC_LEASE_TTL_SECONDS = 30 * 60  # async commands (activation, termination)
RETRY_AFTER_IN_PROGRESS = 2       # seconds, in-progress idempotency key
RETRY_AFTER_ACTIVATION = 5        # seconds, async activation


# ---------------------------------------------------------------------------
# requestHash (specs/08 §8.2, normative)
# ---------------------------------------------------------------------------
def _canonical_json(body: Any) -> str:
    """Deterministic JSON encoding of a request body, or ``""`` if none.

    Keys are sorted at every level and separators are tight so the same logical
    body always hashes identically regardless of key order or whitespace.
    An absent/empty body hashes as the empty string (specs/08 §8.2).
    """
    if not body:
        return ""
    return json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def request_hash(method: str, path: str, body: Any = None) -> str:
    """Return the normative ``requestHash`` for a command request (specs/08 §8.2).

    ``requestHash = SHA-256( METHOD + "\\n" + path + "\\n" + canonical-JSON(body) )``.
    Returned in the stored ``"sha256:<hex>"`` form (specs/04 §4.11). The path is
    part of the hash so a key cannot be replayed against a different entity.
    """
    payload = f"{(method or '').upper()}\n{path or ''}\n{_canonical_json(body)}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# ---------------------------------------------------------------------------
# Command error hierarchy -> HTTP status (specs/11 §11.3)
# ---------------------------------------------------------------------------
class CommandError(Exception):
    """Base class for command-layer errors that map to an HTTP response.

    Every subclass carries a class-level ``http_status`` and stable ``code``.
    The rendered body follows specs/11 §11.3:
    ``{ "error": { "code", "message", "correlationId" } }``.
    """

    http_status: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str = "", *, code: Optional[str] = None,
                 http_status: Optional[int] = None):
        self.message = message or self.__class__.__doc__ or self.code
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        super().__init__(self.message)

    def to_body(self, correlation_id: Optional[str] = None) -> dict:
        """Render the specs/11 §11.3 error envelope."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "correlationId": correlation_id,
            }
        }


class InvalidTransition(CommandError):
    """A state-machine transition is not permitted (specs/06)."""

    http_status = 409
    code = "INVALID_TRANSITION"


class InvariantViolation(CommandError):
    """A financial invariant would be violated (specs/07 §7.2)."""

    http_status = 409
    code = "INVARIANT_VIOLATION"


class IdempotencyKeyReused(CommandError):
    """Same idempotency key, different request hash (specs/08 §8.2)."""

    http_status = 409
    code = "IDEMPOTENCY_KEY_REUSED"


class StaleWrite(CommandError):
    """Optimistic-concurrency conflict: the entity changed under us."""

    http_status = 409
    code = "STALE_WRITE"


class BenefitNotAcceptingPayments(CommandError):
    """The benefit is not in a state that accepts payments (specs/06/09)."""

    http_status = 409
    code = "BENEFIT_NOT_ACCEPTING_PAYMENTS"


class Unprocessable(CommandError):
    """Well-formed but business-invalid (specs/11 §11.3, 422)."""

    http_status = 422
    code = "UNPROCESSABLE"


class ValidationError(CommandError):
    """Malformed request (missing key, empty note, bad enum) — 400."""

    http_status = 400
    code = "VALIDATION_ERROR"


class NotFound(CommandError):
    """Target entity does not exist — 404."""

    http_status = 404
    code = "NOT_FOUND"


class OperationInProgress(CommandError):
    """An in-progress idempotency key: accept + poll (specs/08 §8.3, 202).

    Carries a ``retry_after`` hint (seconds) and an optional ``state`` payload
    describing the current operation/entity state, per specs/11 §11.3.
    """

    http_status = 202
    code = "IN_PROGRESS"

    def __init__(self, message: str = "operation in progress", *,
                 retry_after: int = RETRY_AFTER_IN_PROGRESS,
                 state: Optional[dict] = None):
        self.retry_after = retry_after
        self.state = state or {}
        super().__init__(message)

    def to_body(self, correlation_id: Optional[str] = None) -> dict:
        body = {
            "status": "IN_PROGRESS",
            "retryAfter": self.retry_after,
            "correlationId": correlation_id,
        }
        if self.state:
            body["state"] = self.state
        return body


def from_domain_error(exc: Exception) -> CommandError:
    """Map a pure-core :class:`common.errors.DomainError` to a CommandError.

    Domain code (state_machines, invariants) raises framework-free errors; the
    command layer catches them and re-raises the HTTP-aware equivalent so views
    can render a typed 409. Unknown domain errors fall back to 409/Unprocessable
    conservatively rather than leaking a 500.
    """
    from common import errors as domain_errors

    if isinstance(exc, domain_errors.InvalidTransition):
        return InvalidTransition(str(exc))
    if isinstance(exc, domain_errors.InvariantViolation):
        return InvariantViolation(str(exc))
    if isinstance(exc, domain_errors.DomainError):
        return CommandError(str(exc), code="DOMAIN_ERROR", http_status=409)
    if isinstance(exc, CommandError):
        return exc
    return CommandError(str(exc))


# ---------------------------------------------------------------------------
# CommandContext — per-request identity threaded through a handler
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CommandContext:
    """Identity + idempotency bundle carried through one command invocation.

    * ``actor_id`` / ``actor_role`` / ``actor_name`` — the authenticated user
      driving the command (recorded on every servicingEvent).
    * ``correlation_id`` — shared by all events/writes of this command so the
      timeline can be reconstructed (specs/08 §8.5).
    * ``idempotency_key`` — the client ``Idempotency-Key`` header, verbatim.
    * ``request_hash`` — :func:`request_hash` of this request (specs/08 §8.2).
    * ``lease_owner`` — id of this process/attempt holding the idempotency lease
      (specs/08 §8.3); defaults to a fresh token.
    """

    actor_id: str
    actor_role: str
    actor_name: str
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    idempotency_key: str = ""
    request_hash: str = ""
    lease_owner: str = field(default_factory=lambda: f"run_{uuid.uuid4().hex}")

    @classmethod
    def build(cls, *, actor_id: str, actor_role: str, actor_name: str,
              method: str, path: str, body: Any = None,
              idempotency_key: str = "",
              correlation_id: Optional[str] = None) -> "CommandContext":
        """Construct a context, computing the request hash from the request.

        Convenience for views: pass the raw request pieces and get a fully
        populated context with a deterministic ``request_hash``.
        """
        return cls(
            actor_id=actor_id,
            actor_role=actor_role,
            actor_name=actor_name,
            correlation_id=correlation_id or uuid.uuid4().hex,
            idempotency_key=idempotency_key,
            request_hash=request_hash(method, path, body),
        )


# ---------------------------------------------------------------------------
# transactional() — wrapper over @firestore.transactional (specs/08 §8.1)
# ---------------------------------------------------------------------------
def transactional(client: Any = None, *, max_attempts: Optional[int] = None) -> Callable:
    """Decorator factory that runs a handler inside a Firestore transaction.

    Usage::

        @transactional(client)
        def _run(txn):
            outcome = idempotency.begin(txn, ...)
            ...
            return result

        result = _run()

    The decorated handler receives the ``transaction`` as its first positional
    argument (followed by any args passed at call time). Firestore's
    ``@firestore.transactional`` retries the whole handler on contention
    (specs/08 §8.1) — the handler must therefore be free of external side
    effects (all reads before all writes, no adapter calls inside).
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            from google.cloud import firestore  # lazy: package may be absent at import

            active_client = client
            if active_client is None:
                from common.firestore import get_client
                active_client = get_client()

            txn_kwargs = {}
            if max_attempts is not None:
                txn_kwargs["max_attempts"] = max_attempts
            txn = active_client.transaction(**txn_kwargs)

            @firestore.transactional
            def _inner(transaction):
                return fn(transaction, *args, **kwargs)

            return _inner(txn)

        return wrapper

    return decorator
