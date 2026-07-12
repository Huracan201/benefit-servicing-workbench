"""Operational-exception service — deterministic upsert + pointer-based resolve.

Implements the canonical Phase-2 ``backend/exceptions/service.py`` seam:

    upsert(write, client, *, exception_type, entity_type, entity_id, summary,
           details, loan_id, borrower_id, borrower_name, employer_id,
           employer_name, severity=None) -> exception_id
    resolve(write, client, exception_id, *, resolved_by_event_id) -> None
    dismiss(write, client, exception_id, *, reason) -> None

All three write into ``operationalExceptions/{exceptionId}`` (specs/04 §4.1,
§4.10). Auto-created exceptions use the deterministic id ``{entityId}__{type}``
(``common.ids.exception_id``) so a repeat of the same failure *upserts* the
same row — ``occurrenceCount++`` and ``lastSeenAt`` bump — rather than piling
up duplicates (specs/04 §4.10, specs/09 §9.3). Resolution is pointer-based:
the caller stores the returned id on ``contribution.currentExceptionId`` and
later calls :func:`resolve` / :func:`dismiss` with that exact id.

``operationalExceptions`` carry their own lifecycle timestamps and have **no**
``revision`` (specs/04 §4.12a) — this module sets the fields directly with
``SERVER_TIMESTAMP`` rather than going through the revision-bumping
``repositories.stamp_*`` helpers.

``write`` may be a firestore ``Transaction`` or ``WriteBatch``. When it is a
transaction the current row is read *through* the transaction so the
read-modify-write on ``occurrenceCount`` / ``status`` is consistent; a batch
read falls back to a plain (non-transactional) get.
"""

from __future__ import annotations

from typing import Any, Optional

from common.enums import ExceptionStatus, ExceptionType, Severity, severity_rank
from common.ids import exception_id as _exception_id
from repositories import doc, refs

# Collection name — sourced from repositories.refs (specs/04 §4.1, single source
# of truth) rather than re-declaring the literal here.
COLLECTION = refs.OPERATIONAL_EXCEPTIONS

# Exception type -> default severity (closed map, specs/04 §4.10). Auto-created
# exceptions use this; a caller may override via the ``severity`` argument.
TYPE_DEFAULT_SEVERITY: dict[ExceptionType, Severity] = {
    ExceptionType.PAYMENT_FAILED: Severity.HIGH,
    ExceptionType.PAYMENT_STUCK_PROCESSING: Severity.CRITICAL,
    ExceptionType.LOAN_BALANCE_MISMATCH: Severity.HIGH,
    ExceptionType.TASK_FAILED: Severity.HIGH,
    ExceptionType.SERVICER_SYNC_FAILURE: Severity.MEDIUM,
    ExceptionType.EMPLOYMENT_VERIFICATION_REQUIRED: Severity.MEDIUM,
    ExceptionType.BENEFIT_CONFIGURATION_ERROR: Severity.MEDIUM,
}


def _server_timestamp():
    from google.cloud import firestore  # lazy: package need not be present to compile

    return firestore.SERVER_TIMESTAMP


def _read_snapshot(write, ref):
    """Read ``ref`` through ``write`` when it is a transaction, else plainly."""
    from google.cloud import firestore  # lazy import

    if isinstance(write, firestore.Transaction):
        return ref.get(transaction=write)
    return ref.get()


def _default_severity(exception_type: ExceptionType, override) -> Severity:
    if override is not None:
        return Severity(override)
    return TYPE_DEFAULT_SEVERITY.get(ExceptionType(exception_type), Severity.MEDIUM)


def upsert(
    write,
    client,
    *,
    exception_type,
    entity_type: str,
    entity_id: str,
    summary: str,
    details: Optional[str],
    loan_id: Optional[str],
    borrower_id: Optional[str],
    borrower_name: Optional[str],
    employer_id: Optional[str],
    employer_name: Optional[str],
    severity=None,
) -> str:
    """Idempotently create-or-bump the operational exception for ``entity_id``.

    Returns the deterministic exception id ``{entity_id}__{exception_type}``.
    On first sight the row is created (``occurrenceCount=1``, ``status=OPEN``).
    On a repeat the SAME row is upserted: ``occurrenceCount++``, ``lastSeenAt``
    bumped, the live mirror fields / ``summary`` / ``details`` / ``severity``
    refreshed, and — if the row had been resolved or dismissed — it is
    re-opened (a recurring failure must leave an actionable OPEN exception;
    specs/04 §4.10, specs/09 §9.3).

    Setting ``contribution.currentExceptionId`` to the returned id is the
    caller's job (specs/09 §9.3).
    """
    exc_type = ExceptionType(exception_type)
    exc_id = _exception_id(entity_id, exc_type)
    ref = doc(client, COLLECTION, exc_id)
    now = _server_timestamp()
    resolved_severity = _default_severity(exc_type, severity)

    snapshot = _read_snapshot(write, ref)

    # Fields refreshed on both create and repeat (live mirrors, latest context).
    live: dict[str, Any] = {
        "exceptionType": str(exc_type),
        "severity": str(resolved_severity),
        "severityRank": severity_rank(resolved_severity),
        "entityType": entity_type,
        "entityId": entity_id,
        "loanId": loan_id,
        "borrowerId": borrower_id,
        "borrowerName": borrower_name,
        "employerId": employer_id,
        "employerName": employer_name,
        "summary": summary,
        "details": details,
        "updatedAt": now,
        "lastSeenAt": now,
    }

    if snapshot is not None and snapshot.exists:
        existing = snapshot.to_dict() or {}
        prior_count = existing.get("occurrenceCount", 0) or 0
        update: dict[str, Any] = dict(live)
        update["occurrenceCount"] = prior_count + 1
        # Re-open on recurrence: a resolved/dismissed row that fails again must
        # become an actionable OPEN exception once more (specs/09 §9.3).
        update["status"] = str(ExceptionStatus.OPEN)
        update["resolution"] = None
        update["resolvedAt"] = None
        write.update(ref, update)
        return exc_id

    # First occurrence — create the row.
    data: dict[str, Any] = dict(live)
    data.update(
        {
            "status": str(ExceptionStatus.OPEN),
            "assignedTo": None,
            "occurrenceCount": 1,
            "firstSeenAt": now,
            "resolution": None,
            "resolvedAt": None,
            "createdAt": now,
        }
    )
    write.set(ref, data)
    return exc_id


def _terminate(
    write,
    client,
    exception_id: str,
    *,
    status: ExceptionStatus,
    resolution: dict,
) -> None:
    """Shared close-out for :func:`resolve` / :func:`dismiss`.

    ``client`` is the caller's Firestore client — the one bound to ``write`` —
    so the exception ref resolves against the same (possibly injected/test)
    client rather than a globally fetched one.
    """
    ref = doc(client, COLLECTION, exception_id)
    now = _server_timestamp()
    write.update(
        ref,
        {
            "status": str(status),
            "resolution": resolution,
            "resolvedAt": now,
            "updatedAt": now,
        },
    )


def resolve(write, client, exception_id: str, *, resolved_by_event_id: str) -> None:
    """Mark ``exception_id`` RESOLVED, linking the servicing event that did it.

    Called with the exact id held on ``contribution.currentExceptionId`` — a
    successful retry resolves precisely that row (pointer-based, query-free;
    specs/09 §9.3). ``resolvedByEvent`` records the ``PAYMENT_POSTED`` /
    reconciliation event id that drove the resolution.
    """
    _terminate(
        write,
        client,
        exception_id,
        status=ExceptionStatus.RESOLVED,
        resolution={
            "resolvedBy": None,
            "note": None,
            "resolvedByEvent": resolved_by_event_id,
        },
    )


def dismiss(write, client, exception_id: str, *, reason: str) -> None:
    """Mark ``exception_id`` DISMISSED with ``reason`` (stored on the note).

    Used when the underlying condition no longer applies — e.g. cancelling a
    FAILED contribution on a TERMINATED agreement dismisses its open exception
    so cancellation never orphans one (specs/09 §9.3).
    """
    _terminate(
        write,
        client,
        exception_id,
        status=ExceptionStatus.DISMISSED,
        resolution={
            "resolvedBy": None,
            "note": reason,
            "resolvedByEvent": None,
        },
    )
