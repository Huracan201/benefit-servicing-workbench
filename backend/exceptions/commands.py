"""exceptions.commands — the operational-exception WORKFLOW commands (specs/11 §11.4).

The lower-level ``exceptions.service`` module owns the *writers* used by the
payment pipeline (deterministic ``upsert`` on failure, pointer-based
``resolve`` / ``dismiss`` on retry-success / cancel). This module adds the
**operator-facing commands** — the ones a human in the workbench drives:

* :func:`create_exception`   — ``POST /exceptions`` (manual, auto-id, severity
  from the specs/04 §4.10 type map, ``EXCEPTION_CREATED`` event; bumps
  ``loan.openExceptionCount`` when loan-scoped).
* :func:`assign_exception`   — ``POST /exceptions/{id}/assign``. **Status-neutral**
  (specs/06 §6.4): ``assignedTo`` is a field change, never a transition.
* :func:`mark_in_review`     — ``POST /exceptions/{id}/mark-in-review``
  (``OPEN`` → ``IN_REVIEW``).
* :func:`resolve_exception`  — ``POST /exceptions/{id}/resolve``
  (→ ``RESOLVED``, records ``resolution{resolvedBy, note, resolvedByEvent}``,
  decrements ``loan.openExceptionCount``, ``EXCEPTION_RESOLVED`` event).
* :func:`dismiss_exception`  — ``POST /exceptions/{id}/dismiss``
  (→ ``DISMISSED``, records the ``reason``, decrements
  ``loan.openExceptionCount``, ``EXCEPTION_DISMISSED`` event).

Every command mirrors the reference idempotency-first ordering
(:mod:`benefits.services`, :mod:`payments.service`): inside **one**
``@transactional`` — reads → ``idempotency.begin`` → handle replay/in-progress/
reuse → assert transition + preconditions → writes → events → ``idempotency
.complete``. A :class:`commands.base.CommandError` raised inside the transaction
aborts it and discards the ``PENDING`` idempotency write.

Operational exceptions are exempt from the common ``revision`` field and carry
their own lifecycle timestamps (specs/04 §4.12a), so the exception document is
written with ``SERVER_TIMESTAMP`` directly rather than through the
revision-bumping ``repositories.stamp_*`` helpers (matching ``exceptions.service``).
"""

from __future__ import annotations

from typing import Any, Optional

from commands.base import (
    LEASE_TTL_SECONDS,
    RETRY_AFTER_IN_PROGRESS,
    CommandContext,
    CommandError,
    IdempotencyKeyReused,
    NotFound,
    OperationInProgress,
    Unprocessable,
    from_domain_error,
    transactional,
)
from common.enums import ExceptionStatus, ExceptionType, Severity, severity_rank
from common.errors import DomainError
from common.state_machines import assert_transition
from exceptions import service as exceptions_service
from idempotency import service as idempotency
from repositories import loans, operational_exceptions
from servicing import events as servicing_events

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
OPERATION_CREATE = "create-exception"
OPERATION_ASSIGN = "assign-exception"
OPERATION_MARK_IN_REVIEW = "mark-exception-in-review"
OPERATION_RESOLVE = "resolve-exception"
OPERATION_DISMISS = "dismiss-exception"

# entity_type tag used on the idempotency record for these commands.
ENTITY_TYPE = "OPERATIONAL_EXCEPTION"

# entityType values that scope an exception to a specific entity. LOAN drives
# openExceptionCount + the loan-id look-up; BORROWER/EMPLOYER populate the live
# pointer so the exception's mirror fields aren't null and its events mirror to
# that entity's timeline (specs/04 §4.9). All compared case-insensitively.
_LOAN_ENTITY_TYPE = "LOAN"
_BORROWER_ENTITY_TYPE = "BORROWER"
_EMPLOYER_ENTITY_TYPE = "EMPLOYER"


# --------------------------------------------------------------------------- #
# Lazy firestore + txn-read helpers (mirror payments.service / benefits.services)
# --------------------------------------------------------------------------- #
def _server_ts():
    from google.cloud import firestore  # lazy: package optional at import time

    return firestore.SERVER_TIMESTAMP


def _client_default(client):
    if client is not None:
        return client
    from common.firestore import get_client

    return get_client()


def _get_in_txn(txn, ref) -> Optional[dict]:
    """Read a single ``DocumentReference`` inside ``txn`` as dict-with-id/None."""
    got = txn.get(ref)
    snap = got if hasattr(got, "exists") else next(iter(got), None)
    if snap is None or not getattr(snap, "exists", False):
        return None
    data = snap.to_dict() or {}
    data["id"] = snap.id
    return data


def _event_pointers(exc: dict) -> dict:
    """Denormalized entity pointers carried by every exception event (mirroring)."""
    return {
        "loan_id": exc.get("loanId"),
        "borrower_id": exc.get("borrowerId"),
        "employer_id": exc.get("employerId"),
        "benefit_agreement_id": exc.get("benefitAgreementId"),
    }


def _handle_non_new(outcome) -> Optional[dict]:
    """Translate a non-NEW idempotency outcome to a replay result or a raise.

    Returns the stored result dict for a replay; raises the typed CommandError
    for in-progress / reuse. Returns ``None`` when the outcome is NEW (proceed).
    """
    if outcome.is_replay:
        return outcome.result or {}
    if outcome.is_in_progress:
        raise OperationInProgress(retry_after=RETRY_AFTER_IN_PROGRESS)
    if outcome.is_reuse:
        raise IdempotencyKeyReused()
    return None


# --------------------------------------------------------------------------- #
# create_exception — POST /exceptions (specs/11 §11.4, specs/04 §4.10)
# --------------------------------------------------------------------------- #
def create_exception(
    *,
    ctx: CommandContext,
    exception_type,
    entity_type: str,
    entity_id: str,
    summary: str,
    details: Optional[str] = None,
    severity=None,
    client: Any = None,
) -> dict:
    """Create a manual operational exception (auto-id) and return its summary.

    ``severity`` defaults to the specs/04 §4.10 type→severity map; a caller may
    override it (the map permits manual override). When the exception is
    loan-scoped (``entityType == LOAN``) the loan's ``openExceptionCount`` is
    incremented in the same transaction and the event mirrors to the loan.
    """
    client = _client_default(client)

    exc_type = ExceptionType(exception_type)
    resolved_severity = (
        Severity(severity)
        if severity is not None
        else exceptions_service.TYPE_DEFAULT_SEVERITY.get(exc_type, Severity.MEDIUM)
    )

    # Scope the live entity pointers from the target so both the exception's
    # mirror fields AND its events point at the right subcollection (specs/04
    # §4.9, §4.10): LOAN -> loanId (also drives openExceptionCount), BORROWER ->
    # borrowerId (event mirrors to borrowers/{id}/events), EMPLOYER -> employerId.
    entity_type_upper = str(entity_type).upper()
    loan_scoped = entity_type_upper == _LOAN_ENTITY_TYPE
    loan_id = entity_id if loan_scoped else None
    borrower_id = entity_id if entity_type_upper == _BORROWER_ENTITY_TYPE else None
    employer_id = entity_id if entity_type_upper == _EMPLOYER_ENTITY_TYPE else None

    @transactional(client)
    def _run(txn: Any) -> dict:
        # -- reads (before begin writes the PENDING record) -----------------
        loan = _get_in_txn(txn, loans.ref(client, loan_id)) if loan_id else None
        if loan_id and loan is None:
            raise NotFound(f"loan {loan_id!r} not found for exception")

        # -- idempotency begin FIRST (replay / in-progress / reuse) ---------
        # The idempotency entity_id is the STABLE *target* entity — the loan/
        # borrower/agreement this exception is ABOUT — NOT the exception's own
        # auto-id. The auto-id is random per invocation, so keying idempotency on
        # it would make a legitimate same-key retry read a stored record whose
        # entityId no longer matches → a spurious 409 REUSE (or, if the first call
        # never committed, a DUPLICATE exception). The target entity_id is stable
        # across retries; a replayed COMPLETED outcome returns the stored result,
        # whose exceptionId is the id created on the original call.
        outcome = idempotency.begin(
            txn,
            key=ctx.idempotency_key,
            operation=OPERATION_CREATE,
            request_hash=ctx.request_hash,
            entity_id=entity_id,
            entity_type=ENTITY_TYPE,
            lease_ttl_seconds=LEASE_TTL_SECONDS,
            lease_owner=ctx.lease_owner,
            client=client,
        )
        replay = _handle_non_new(outcome)
        if replay is not None:
            return replay

        # -- NEW/proceed path only: mint the exception's auto-id now (manual
        #    exceptions use auto-ids — specs/04 §4.10). Minting inside the txn is
        #    safe: the exception doc, the loan bump and the idempotency result all
        #    commit atomically, so a Firestore contention retry that re-mints a
        #    different id leaves no orphan (nothing commits on abort) and the
        #    stored result always matches the committed exception doc.
        exc_ref = operational_exceptions.new_ref(client)
        exc_id = exc_ref.id

        now = _server_ts()
        record: dict[str, Any] = {
            "exceptionType": str(exc_type),
            "severity": str(resolved_severity),
            "severityRank": severity_rank(resolved_severity),
            "entityType": entity_type,
            "entityId": entity_id,
            "loanId": loan_id,
            "borrowerId": borrower_id,
            # *Name mirrors left null for a manual create — resolving them would
            # add an extra borrower/employer read; the timeline still resolves the
            # name from the entity doc (specs/04 §4.9 mirror is keyed by id).
            "borrowerName": None,
            "employerId": employer_id,
            "employerName": None,
            "status": str(ExceptionStatus.OPEN),
            "assignedTo": None,
            "occurrenceCount": 1,
            "firstSeenAt": now,
            "lastSeenAt": now,
            "summary": summary,
            "details": details,
            "resolution": None,
            "createdAt": now,
            "updatedAt": now,
            "resolvedAt": None,
        }
        txn.set(exc_ref, record)

        # -- bump the loan's open-exception count (loan-scoped only) --------
        if loan_id:
            loan_update = {
                "openExceptionCount": int((loan or {}).get("openExceptionCount", 0)) + 1
            }
            _stamp_update(loan_update, ctx.actor_id)
            txn.update(loans.ref(client, loan_id), loan_update)

        # -- event ----------------------------------------------------------
        servicing_events.append(
            txn,
            event_type="EXCEPTION_CREATED",
            entity_type=entity_type,
            entity_id=entity_id,
            actor_id=ctx.actor_id,
            actor_role=ctx.actor_role,
            actor_name=ctx.actor_name,
            correlation_id=ctx.correlation_id,
            sequence=1,
            metadata={
                "exceptionId": exc_id,
                "exceptionType": str(exc_type),
                "severity": str(resolved_severity),
                "summary": summary,
            },
            **_event_pointers(record),
        )

        result = {
            "exceptionId": exc_id,
            "exceptionType": str(exc_type),
            "severity": str(resolved_severity),
            "severityRank": severity_rank(resolved_severity),
            "entityType": entity_type,
            "entityId": entity_id,
            "loanId": loan_id,
            "status": str(ExceptionStatus.OPEN),
            "assignedTo": None,
            "correlationId": ctx.correlation_id,
        }
        idempotency.complete(txn, ctx.idempotency_key, result, client=client)
        return result

    return _dispatch(_run)


# --------------------------------------------------------------------------- #
# assign_exception — POST /exceptions/{id}/assign  (STATUS-NEUTRAL, specs/06 §6.4)
# --------------------------------------------------------------------------- #
def assign_exception(
    exception_id: str,
    *,
    ctx: CommandContext,
    assign_to: Optional[str],
    client: Any = None,
) -> dict:
    """Set/clear ``assignedTo`` on an exception — a field change, not a transition.

    ``assign_to`` is the already-resolved target (the view maps omitted → self and
    explicit ``null`` → unassign): a uid string assigns, ``None`` unassigns. The
    exception's status is untouched (specs/06 §6.4). Terminal exceptions
    (``RESOLVED`` / ``DISMISSED``) cannot be (re)assigned → ``422``.
    """
    client = _client_default(client)

    @transactional(client)
    def _run(txn: Any) -> dict:
        exc = _get_in_txn(txn, operational_exceptions.ref(client, exception_id))
        if exc is None:
            raise NotFound(f"exception {exception_id!r} not found")

        outcome = idempotency.begin(
            txn,
            key=ctx.idempotency_key,
            operation=OPERATION_ASSIGN,
            request_hash=ctx.request_hash,
            entity_id=exception_id,
            entity_type=ENTITY_TYPE,
            lease_ttl_seconds=LEASE_TTL_SECONDS,
            lease_owner=ctx.lease_owner,
            client=client,
        )
        replay = _handle_non_new(outcome)
        if replay is not None:
            return replay

        if exc.get("status") in (
            str(ExceptionStatus.RESOLVED),
            str(ExceptionStatus.DISMISSED),
        ):
            raise Unprocessable("cannot assign a terminal (resolved/dismissed) exception")

        now = _server_ts()
        txn.update(
            operational_exceptions.ref(client, exception_id),
            {"assignedTo": assign_to, "updatedAt": now},
        )

        result = {
            "exceptionId": exception_id,
            "status": exc.get("status"),
            "assignedTo": assign_to,
            "correlationId": ctx.correlation_id,
        }
        idempotency.complete(txn, ctx.idempotency_key, result, client=client)
        return result

    return _dispatch(_run)


# --------------------------------------------------------------------------- #
# mark_in_review — POST /exceptions/{id}/mark-in-review  (OPEN -> IN_REVIEW)
# --------------------------------------------------------------------------- #
def mark_in_review(
    exception_id: str, *, ctx: CommandContext, client: Any = None
) -> dict:
    """Transition ``OPEN`` → ``IN_REVIEW`` (specs/06 §6.4). No event, no count change."""
    client = _client_default(client)

    @transactional(client)
    def _run(txn: Any) -> dict:
        exc = _get_in_txn(txn, operational_exceptions.ref(client, exception_id))
        if exc is None:
            raise NotFound(f"exception {exception_id!r} not found")

        outcome = idempotency.begin(
            txn,
            key=ctx.idempotency_key,
            operation=OPERATION_MARK_IN_REVIEW,
            request_hash=ctx.request_hash,
            entity_id=exception_id,
            entity_type=ENTITY_TYPE,
            lease_ttl_seconds=LEASE_TTL_SECONDS,
            lease_owner=ctx.lease_owner,
            client=client,
        )
        replay = _handle_non_new(outcome)
        if replay is not None:
            return replay

        assert_transition("exception", exc.get("status"), ExceptionStatus.IN_REVIEW)

        now = _server_ts()
        txn.update(
            operational_exceptions.ref(client, exception_id),
            {"status": str(ExceptionStatus.IN_REVIEW), "updatedAt": now},
        )

        result = {
            "exceptionId": exception_id,
            "status": str(ExceptionStatus.IN_REVIEW),
            "correlationId": ctx.correlation_id,
        }
        idempotency.complete(txn, ctx.idempotency_key, result, client=client)
        return result

    return _dispatch(_run)


# --------------------------------------------------------------------------- #
# resolve_exception — POST /exceptions/{id}/resolve  (-> RESOLVED)
# --------------------------------------------------------------------------- #
def resolve_exception(
    exception_id: str,
    *,
    ctx: CommandContext,
    note: Optional[str] = None,
    client: Any = None,
) -> dict:
    """Transition ``OPEN``/``IN_REVIEW`` → ``RESOLVED`` (specs/06 §6.4).

    Records ``resolution{resolvedBy, note, resolvedByEvent}`` (the actor, the
    operator note, and the ``EXCEPTION_RESOLVED`` event id) and — when the
    exception is loan-scoped — decrements the loan's ``openExceptionCount``.
    """
    client = _client_default(client)

    @transactional(client)
    def _run(txn: Any) -> dict:
        exc = _get_in_txn(txn, operational_exceptions.ref(client, exception_id))
        if exc is None:
            raise NotFound(f"exception {exception_id!r} not found")
        loan_id = exc.get("loanId")
        loan = _get_in_txn(txn, loans.ref(client, loan_id)) if loan_id else None

        outcome = idempotency.begin(
            txn,
            key=ctx.idempotency_key,
            operation=OPERATION_RESOLVE,
            request_hash=ctx.request_hash,
            entity_id=exception_id,
            entity_type=ENTITY_TYPE,
            lease_ttl_seconds=LEASE_TTL_SECONDS,
            lease_owner=ctx.lease_owner,
            client=client,
        )
        replay = _handle_non_new(outcome)
        if replay is not None:
            return replay

        assert_transition("exception", exc.get("status"), ExceptionStatus.RESOLVED)

        now = _server_ts()
        # Event first so its id can be linked into resolution.resolvedByEvent.
        event_id = servicing_events.append(
            txn,
            event_type="EXCEPTION_RESOLVED",
            entity_type=exc.get("entityType"),
            entity_id=exc.get("entityId"),
            actor_id=ctx.actor_id,
            actor_role=ctx.actor_role,
            actor_name=ctx.actor_name,
            correlation_id=ctx.correlation_id,
            sequence=1,
            metadata={
                "exceptionId": exception_id,
                "exceptionType": exc.get("exceptionType"),
                "note": note,
            },
            **_event_pointers(exc),
        )

        resolution = {
            "resolvedBy": ctx.actor_id,
            "note": note,
            "resolvedByEvent": event_id,
        }
        txn.update(
            operational_exceptions.ref(client, exception_id),
            {
                "status": str(ExceptionStatus.RESOLVED),
                "resolution": resolution,
                "resolvedAt": now,
                "updatedAt": now,
            },
        )

        if loan_id and loan is not None:
            loan_update = {
                "openExceptionCount": max(0, int(loan.get("openExceptionCount", 0)) - 1)
            }
            _stamp_update(loan_update, ctx.actor_id)
            txn.update(loans.ref(client, loan_id), loan_update)

        result = {
            "exceptionId": exception_id,
            "status": str(ExceptionStatus.RESOLVED),
            "resolution": resolution,
            "correlationId": ctx.correlation_id,
        }
        idempotency.complete(txn, ctx.idempotency_key, result, client=client)
        return result

    return _dispatch(_run)


# --------------------------------------------------------------------------- #
# dismiss_exception — POST /exceptions/{id}/dismiss  (-> DISMISSED)
# --------------------------------------------------------------------------- #
def dismiss_exception(
    exception_id: str,
    *,
    ctx: CommandContext,
    reason: str,
    client: Any = None,
) -> dict:
    """Transition ``OPEN``/``IN_REVIEW`` → ``DISMISSED`` (specs/06 §6.4).

    Records the ``reason`` (stored on ``resolution.note`` via the shared
    ``exceptions.service.dismiss`` writer) and — when loan-scoped — decrements
    the loan's ``openExceptionCount``.
    """
    client = _client_default(client)

    @transactional(client)
    def _run(txn: Any) -> dict:
        exc = _get_in_txn(txn, operational_exceptions.ref(client, exception_id))
        if exc is None:
            raise NotFound(f"exception {exception_id!r} not found")
        loan_id = exc.get("loanId")
        loan = _get_in_txn(txn, loans.ref(client, loan_id)) if loan_id else None

        outcome = idempotency.begin(
            txn,
            key=ctx.idempotency_key,
            operation=OPERATION_DISMISS,
            request_hash=ctx.request_hash,
            entity_id=exception_id,
            entity_type=ENTITY_TYPE,
            lease_ttl_seconds=LEASE_TTL_SECONDS,
            lease_owner=ctx.lease_owner,
            client=client,
        )
        replay = _handle_non_new(outcome)
        if replay is not None:
            return replay

        assert_transition("exception", exc.get("status"), ExceptionStatus.DISMISSED)

        # Event FIRST so its id can be linked into resolution.resolvedByEvent
        # (mirrors resolve_exception — a manual dismissal is operator-attributed).
        event_id = servicing_events.append(
            txn,
            event_type="EXCEPTION_DISMISSED",
            entity_type=exc.get("entityType"),
            entity_id=exc.get("entityId"),
            actor_id=ctx.actor_id,
            actor_role=ctx.actor_role,
            actor_name=ctx.actor_name,
            correlation_id=ctx.correlation_id,
            sequence=1,
            metadata={
                "exceptionId": exception_id,
                "exceptionType": exc.get("exceptionType"),
                "reason": reason,
            },
            **_event_pointers(exc),
        )

        # Shared writer sets status/resolution/resolvedAt/updatedAt; pass the
        # operator + dismissing event so the resolution records WHO dismissed and
        # via WHICH event (the automation cancel path leaves both defaulted None).
        exceptions_service.dismiss(
            txn,
            client,
            exception_id,
            reason=reason,
            resolved_by=ctx.actor_id,
            resolved_by_event=event_id,
        )

        if loan_id and loan is not None:
            loan_update = {
                "openExceptionCount": max(0, int(loan.get("openExceptionCount", 0)) - 1)
            }
            _stamp_update(loan_update, ctx.actor_id)
            txn.update(loans.ref(client, loan_id), loan_update)

        result = {
            "exceptionId": exception_id,
            "status": str(ExceptionStatus.DISMISSED),
            "reason": reason,
            "correlationId": ctx.correlation_id,
        }
        idempotency.complete(txn, ctx.idempotency_key, result, client=client)
        return result

    return _dispatch(_run)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _stamp_update(data: dict, actor_id: str) -> None:
    """Apply the common revision-bumping update fields (loans carry a revision)."""
    from repositories import stamp_update

    stamp_update(data, actor_id)


def _dispatch(run) -> dict:
    """Run a transactional handler, normalising domain errors to CommandErrors."""
    try:
        return run()
    except CommandError:
        raise
    except DomainError as exc:
        raise from_domain_error(exc) from exc
