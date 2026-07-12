"""notes.services — the add-servicing-note command (specs/10 §10.5).

Appends an immutable, author-attributed note to a loan's timeline. The note doc
(``loans/{loanId}/notes/{noteId}``) and its ``MANUAL_NOTE_ADDED`` servicing event
are written in a *single* Firestore transaction, following the idempotency-first
ordering of :func:`benefits.services.activate_benefit`:

    reads -> idempotency.begin -> handle replay/in_progress/reuse
          -> create note doc + MANUAL_NOTE_ADDED event -> idempotency.complete

Contract highlights:

* **Text** (specs/10 §10.5): required and non-empty — an empty/whitespace-only
  body is rejected ``400`` at the view; the service defends the same invariant
  as a ``422`` should a caller reach it directly.
* **Attribution** (specs/10 §10.5): ``authorId``/``authorName`` are the
  authenticated user, frozen at write time; the note is append-only — there is
  no edit or delete command in the MVP.
* **Idempotency** (specs/08 §8.2): the ``idempotencyKeys/{key}`` record is
  created and completed inside the same transaction; a replay returns the stored
  result (same ``noteId``); a same-key/different-hash request is a ``409``; a
  live lease is ``202``.
* **Events** (specs/04 §4.9): one ``MANUAL_NOTE_ADDED`` event (sequence 1),
  mirrored to ``loans/{loanId}/events`` via the loan id it carries.
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
from common import errors as domain_errors
from idempotency import service as idempotency
from repositories import loans
from servicing import events as servicing_events

OPERATION = "add-note"
ENTITY_TYPE = "LOAN"
EVENT_TYPE = "MANUAL_NOTE_ADDED"


# --------------------------------------------------------------------------- #
# Lazy firestore server-timestamp (mirrors servicing.events / exceptions.service)
# --------------------------------------------------------------------------- #
def _server_ts():
    from google.cloud import firestore  # lazy: package optional at import time

    return firestore.SERVER_TIMESTAMP


# --------------------------------------------------------------------------- #
# Transactional read helper (mirrors benefits.services._txn_get)
# --------------------------------------------------------------------------- #
def _txn_get(txn: Any, ref: Any) -> Optional[dict]:
    """Read a single document *inside* the transaction, as dict-with-id or None."""
    got = txn.get(ref)
    snap = got if hasattr(got, "exists") else next(iter(got), None)
    if snap is None or not getattr(snap, "exists", False):
        return None
    data = snap.to_dict() or {}
    data["id"] = snap.id
    return data


# --------------------------------------------------------------------------- #
# The command
# --------------------------------------------------------------------------- #
def add_note(
    *, loan_id: str, text: str, ctx: CommandContext, client: Any = None
) -> dict:
    """Append an author-attributed servicing note to a loan (specs/10 §10.5).

    Returns the response body (the created note as a serialisable summary) — the
    same object stored for idempotent replay. Raises a
    :class:`commands.base.CommandError` subclass on any precondition/idempotency
    failure, which the view maps to the specs/11 §11.3 HTTP response.
    """
    if text is not None and not isinstance(text, str):
        raise Unprocessable("note text must be a string")
    cleaned = (text or "").strip()
    if not cleaned:
        # Defensive: the view already rejects empty text with a 400. Any caller
        # reaching the service directly gets a 422 (specs/10 §10.5 non-empty).
        raise Unprocessable("note text is required and must be non-empty")

    if client is None:
        from common.firestore import get_client

        client = get_client()

    @transactional(client)
    def _run(txn: Any) -> dict:
        # --- reads (all before any write — Firestore ordering rule) ----------
        loan = _txn_get(txn, loans.ref(client, loan_id))
        if loan is None:
            raise NotFound(f"loan {loan_id!r} not found")

        # --- idempotency: begin inside the txn (reads then writes PENDING) ----
        outcome = idempotency.begin(
            txn,
            key=ctx.idempotency_key,
            operation=OPERATION,
            request_hash=ctx.request_hash,
            entity_id=loan_id,
            entity_type=ENTITY_TYPE,
            lease_ttl_seconds=LEASE_TTL_SECONDS,
            lease_owner=ctx.lease_owner,
            client=client,
        )
        if outcome.is_replay:
            return outcome.result or {}
        if outcome.is_in_progress:
            raise OperationInProgress(
                "note creation already in progress",
                retry_after=RETRY_AFTER_IN_PROGRESS,
                state={"loanId": loan_id},
            )
        if outcome.is_reuse:
            raise IdempotencyKeyReused(
                "idempotency key reused with a different request"
            )

        borrower_id = loan.get("borrowerId")
        employer_id = loan.get("employerId")

        # --- create the note doc (auto-id; append-only, never overwritten) ---
        note_ref = loans.new_note_ref(client, loan_id)
        note_id = note_ref.id
        note = {
            "loanId": loan_id,
            "text": cleaned,
            "authorId": ctx.actor_id,
            "authorName": ctx.actor_name,
            # Append-only note: carries createdAt ONLY — no revision/updatedBy
            # (specs/10 §10.5, specs/04 §4.12a). Set the timestamp directly like
            # servicingEvents / operationalExceptions do, rather than through the
            # revision-bumping stamp_create helper.
            "createdAt": _server_ts(),
        }
        txn.create(note_ref, note)

        # --- event (MANUAL_NOTE_ADDED, sequence 1, mirrored to the loan) -----
        event_id = servicing_events.append(
            txn,
            event_type=EVENT_TYPE,
            entity_type=ENTITY_TYPE,
            entity_id=loan_id,
            actor_id=ctx.actor_id,
            actor_role=ctx.actor_role,
            actor_name=ctx.actor_name,
            correlation_id=ctx.correlation_id,
            sequence=1,
            metadata={"noteId": note_id},
            loan_id=loan_id,
            borrower_id=borrower_id,
            employer_id=employer_id,
        )

        result = {
            "noteId": note_id,
            "loanId": loan_id,
            "text": cleaned,
            "authorId": ctx.actor_id,
            "authorName": ctx.actor_name,
            "eventId": event_id,
            "correlationId": ctx.correlation_id,
        }

        # --- idempotency COMPLETED, in the same transaction ------------------
        idempotency.complete(txn, ctx.idempotency_key, result, client=client)
        return result

    try:
        return _run()
    except CommandError:
        raise
    except domain_errors.DomainError as exc:
        raise from_domain_error(exc) from exc
