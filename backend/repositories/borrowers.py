"""Borrower data-access gateway — ``borrowers/{borrowerId}`` (specs/04 §4.4)."""

from __future__ import annotations

from typing import Any, Optional

from . import refs


def ref(client, borrower_id: str):
    """``DocumentReference`` for ``borrowers/{borrower_id}``."""
    return refs.doc(client, refs.BORROWERS, borrower_id)


def get(client, borrower_id: str) -> Optional[dict[str, Any]]:
    """Read the borrower document as dict-with-id, or ``None``."""
    return refs.get(client, refs.BORROWERS, borrower_id)


def list_for_employer(client, employer_id: str) -> list[dict[str, Any]]:
    """All borrowers for an employer (``borrowers where employerId ==``)."""
    query = client.collection(refs.BORROWERS).where(
        filter=refs.field_filter("employerId", "==", employer_id)
    )
    return refs.stream_to_dicts(query)
