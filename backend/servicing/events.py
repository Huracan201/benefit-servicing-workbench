"""servicing.events — append an immutable servicing event (specs/04 §4.9).

Every material change appends an immutable ``servicingEvent`` in the *same*
write as the change itself (specs/README, normative). This module owns the
mechanics of that append:

  * write the global ``servicingEvents/{eventId}`` document, and
  * *additionally* mirror it, in the same write, to the most-specific entity
    subcollection — ``loans/{loanId}/events/{eventId}`` if ``loanId`` is set,
    else ``borrowers/{borrowerId}/events/{eventId}`` if ``borrowerId`` is set,
    else **global-only, no mirror** (specs/04 §4.9 mirroring rule).

The caller (a domain command) owns the transaction/batch and passes it in as
``write`` along with the correlation id and the monotonic ``sequence`` — this
module assigns neither. ``event_type`` is validated against the closed
``EVENT_TYPES`` enum (specs/04 §4.9).

``servicingEvents`` documents are immutable and append-only: they carry
``createdAt`` only — no ``revision``/``updatedAt``/``createdBy`` (specs/04
§4.12). Denormalized names on the event are **frozen snapshots**.
"""

from __future__ import annotations

from typing import Any, Optional

# --- Collection / subcollection names come from repositories.refs (specs/04
#     §4.1, single source of truth); imported lazily inside append() to keep this
#     module import-clean in the offline sandbox (see append's import note). ---

# --- Canonical closed eventType enum (specs/04 §4.9) -------------------------
# Extend HERE first if a new event type is ever needed.
EVENT_TYPES: frozenset[str] = frozenset(
    {
        "BENEFIT_ACTIVATION_STARTED",
        "BENEFIT_ACTIVATED",
        "BENEFIT_SUSPENDED",
        "BENEFIT_RESUMED",
        "BENEFIT_TERMINATED",
        "BENEFIT_COMPLETED",
        "SCHEDULE_SHIFTED",
        "PAYMENT_PROCESSING",
        "PAYMENT_POSTED",
        "PAYMENT_FAILED",
        "PAYMENT_RETRY_SCHEDULED",
        "PAYMENT_CANCELED",
        "PAYMENT_RECONCILED",
        "FUTURE_CONTRIBUTIONS_CANCELED",
        "LOAN_BALANCE_UPDATED",
        "EMPLOYMENT_STATUS_CHANGED",
        "EXCEPTION_CREATED",
        "EXCEPTION_RESOLVED",
        "EXCEPTION_DISMISSED",
        "MANUAL_NOTE_ADDED",
        "USER_ROLE_CHANGED",
        "EMPLOYER_STATUS_CHANGED",
    }
)


def _client_of(write: Any):
    """Recover the firestore client bound to a Transaction/WriteBatch handle.

    Both ``google.cloud.firestore`` Transaction and WriteBatch objects hold a
    reference to their originating client on ``_client``. We need it to build
    DocumentReferences and to mint the event id.
    """
    client = getattr(write, "_client", None)
    if client is None:
        raise TypeError(
            "servicing.events.append requires a firestore Transaction or "
            "WriteBatch (with a bound client) as its 'write' handle"
        )
    return client


def _server_timestamp():
    """firestore.SERVER_TIMESTAMP sentinel (lazy import — package is optional)."""
    from google.cloud import firestore  # lazy: not importable in the pure sandbox

    return firestore.SERVER_TIMESTAMP


def append(
    write: Any,
    *,
    event_type: str,
    entity_type: str,
    entity_id: str,
    actor_id: str,
    actor_role: Optional[str],
    actor_name: str,
    correlation_id: str,
    sequence: int,
    metadata: Optional[dict[str, Any]] = None,
    loan_id: Optional[str] = None,
    borrower_id: Optional[str] = None,
    employer_id: Optional[str] = None,
    benefit_agreement_id: Optional[str] = None,
) -> str:
    """Append one immutable servicing event on the caller's ``write`` handle.

    Writes ``servicingEvents/{eventId}`` and, in the SAME ``write``, the
    most-specific entity mirror (loan → borrower → global-only). Returns the
    generated ``eventId``.

    ``write``      a firestore Transaction or WriteBatch (owns atomicity).
    ``sequence``   monotonic-within-``correlation_id`` tiebreaker; caller-assigned.
    ``actor_role`` may be ``None`` for SYSTEM actors.

    The actor type is derived from ``actor_id``: ids of the form
    ``system:<job>`` are SYSTEM, all others are USER (specs/12 §12.5, README
    ``createdBy`` convention).
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(
            f"unknown eventType {event_type!r}; must be one of the closed "
            f"specs/04 §4.9 enum (extend EVENT_TYPES first)"
        )
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise ValueError("sequence must be a positive int assigned by the command")

    # Lazy: repositories may import google.cloud at module load, which is absent
    # in the pure/offline sandbox. Importing here keeps this module import-clean.
    from repositories import doc, refs

    client = _client_of(write)
    actor_type = "SYSTEM" if str(actor_id).startswith("system:") else "USER"

    # Mint the event id from the global collection (random auto-id); the same id
    # is reused for the mirror so the two copies are trivially correlatable.
    event_id = client.collection(refs.SERVICING_EVENTS).document().id

    record: dict[str, Any] = {
        "eventType": event_type,
        "entityType": entity_type,
        "entityId": entity_id,
        "loanId": loan_id,
        "borrowerId": borrower_id,
        "employerId": employer_id,
        "benefitAgreementId": benefit_agreement_id,
        "actorType": actor_type,
        "actorId": actor_id,
        "actorRole": actor_role,
        "actorName": actor_name,
        "correlationId": correlation_id,
        "sequence": sequence,
        "metadata": dict(metadata) if metadata else {},
        "createdAt": _server_timestamp(),
    }

    # 1) Global audit stream (always).
    global_ref = doc(client, refs.SERVICING_EVENTS, event_id)
    write.set(global_ref, record)

    # 2) Most-specific entity mirror, in the SAME write (specs/04 §4.9).
    mirror_parent = None
    mirror_sub = None
    if loan_id:
        mirror_parent = doc(client, refs.LOANS, loan_id)
        mirror_sub = refs.LOAN_EVENTS
    elif borrower_id:
        mirror_parent = doc(client, refs.BORROWERS, borrower_id)
        mirror_sub = refs.BORROWER_EVENTS

    if mirror_parent is not None:
        mirror_ref = mirror_parent.collection(mirror_sub).document(event_id)
        write.set(mirror_ref, record)

    return event_id
