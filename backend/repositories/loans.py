"""Loan data-access gateway — ``loans/{loanId}`` (specs/04 §4.5).

The authoritative borrower->loan link is ``loan.borrowerId`` (specs/04 §4.4);
``list_for_borrower`` is the canonical query for it.
"""

from __future__ import annotations

from typing import Any, Optional

from . import refs


def ref(client, loan_id: str):
    """``DocumentReference`` for ``loans/{loan_id}``."""
    return refs.doc(client, refs.LOANS, loan_id)


def get(client, loan_id: str) -> Optional[dict[str, Any]]:
    """Read the loan document as dict-with-id, or ``None``."""
    return refs.get(client, refs.LOANS, loan_id)


def note_ref(client, loan_id: str, note_id: str):
    """``DocumentReference`` for ``loans/{loan_id}/notes/{note_id}``."""
    return ref(client, loan_id).collection(refs.LOAN_NOTES).document(note_id)


def new_note_ref(client, loan_id: str):
    """Auto-id ``DocumentReference`` under ``loans/{loan_id}/notes``."""
    return ref(client, loan_id).collection(refs.LOAN_NOTES).document()


def list_for_borrower(client, borrower_id: str) -> list[dict[str, Any]]:
    """All loans for a borrower (``loans where borrowerId ==``)."""
    query = client.collection(refs.LOANS).where(
        filter=refs.field_filter("borrowerId", "==", borrower_id)
    )
    return refs.stream_to_dicts(query)
