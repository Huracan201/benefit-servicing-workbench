"""Servicing-event data-access gateway — ``servicingEvents/{eventId}`` and its
entity-scoped mirrors (specs/04 §4.9).

Events are immutable and append-only (``createdAt`` only — specs/04 §4.12a). This
gateway is address-only: it mints the global ref (``new_ref``) and builds the
most-specific mirror ref (loan -> borrower -> global-only). The dual-write inside
one transaction is orchestrated by ``servicing.events.append``.
"""

from __future__ import annotations

from typing import Any, Optional

from . import refs


def new_ref(client):
    """Auto-id ``DocumentReference`` in the global ``servicingEvents`` collection.

    The generated ``.id`` becomes the shared ``eventId`` reused for the mirror.
    """
    return refs.new_doc(client, refs.SERVICING_EVENTS)


def ref(client, event_id: str):
    """``DocumentReference`` for ``servicingEvents/{event_id}``."""
    return refs.doc(client, refs.SERVICING_EVENTS, event_id)


def get(client, event_id: str) -> Optional[dict[str, Any]]:
    """Read the global servicing-event document as dict-with-id, or ``None``."""
    return refs.get(client, refs.SERVICING_EVENTS, event_id)


def loan_mirror_ref(client, loan_id: str, event_id: str):
    """Mirror ref ``loans/{loan_id}/events/{event_id}``."""
    return (
        client.collection(refs.LOANS)
        .document(loan_id)
        .collection(refs.LOAN_EVENTS)
        .document(event_id)
    )


def borrower_mirror_ref(client, borrower_id: str, event_id: str):
    """Mirror ref ``borrowers/{borrower_id}/events/{event_id}``."""
    return (
        client.collection(refs.BORROWERS)
        .document(borrower_id)
        .collection(refs.BORROWER_EVENTS)
        .document(event_id)
    )
