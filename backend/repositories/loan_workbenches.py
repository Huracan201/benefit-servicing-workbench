"""Loan-workbench read-model gateway — ``loanWorkbenches/{loanId}`` (specs/05 §5.5).

The widest live mirror: everything to render a loan-portfolio row and the account
header without joins. A **read model** — derived, eventually consistent, and
*exempt* from the common ``revision``/``createdBy`` audit fields (specs/04 §4.12a):
a write stamps ``updatedAt = SERVER_TIMESTAMP`` directly (never
``stamp_create``/``stamp_update``).

Writes are full-document ``set`` overwrites — the projection engine recomputes the
whole doc from source (:mod:`projections.recompute`), so overwriting keeps a
redelivered ``update-projection`` task byte-identical.
"""

from __future__ import annotations

from typing import Any, Optional

from . import refs


def _stamped(doc: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``doc`` with ``updatedAt = SERVER_TIMESTAMP`` (read-model
    write: exempt from the common audit fields — specs/04 §4.12a)."""
    from google.cloud import firestore  # lazy — offline py_compile friendly

    return {**doc, "updatedAt": firestore.SERVER_TIMESTAMP}


def ref(client, loan_id: str):
    """``DocumentReference`` for ``loanWorkbenches/{loan_id}``."""
    return refs.doc(client, refs.LOAN_WORKBENCHES, loan_id)


def get(client, loan_id: str) -> Optional[dict[str, Any]]:
    """Read the loan-workbench doc as dict-with-id, or ``None``."""
    return refs.get(client, refs.LOAN_WORKBENCHES, loan_id)


def write(client, loan_id: str, doc: dict[str, Any]) -> None:
    """Overwrite ``loanWorkbenches/{loan_id}`` with ``doc`` + ``updatedAt`` server time."""
    ref(client, loan_id).set(_stamped(doc))
