"""Portfolio-summary read-model gateway — ``portfolioSummaries/{docId}``
(specs/05 §5.3).

Two doc shapes share the collection: the point-in-time ``portfolioSummaries/current``
and the per-period ``portfolioSummaries/{YYYY-MM}`` flow docs. Both are **read
models** — derived, eventually consistent, and *exempt* from the common
``revision``/``createdBy`` audit fields (specs/04 §4.12a): a write stamps
``updatedAt = SERVER_TIMESTAMP`` directly (never ``stamp_create``/``stamp_update``).

Writes are full-document ``set`` overwrites: the projection engine recomputes the
whole doc from source (:mod:`projections.recompute`), so overwriting — rather than
merging — is what makes a redelivered ``update-projection`` task byte-identical and
drops any field a prior schema left behind.
"""

from __future__ import annotations

from typing import Any, Optional

from . import refs

# Point-in-time doc id (the other ids are period labels, "YYYY-MM").
CURRENT_DOC_ID = "current"


def _stamped(doc: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``doc`` with ``updatedAt = SERVER_TIMESTAMP`` (read-model
    write: exempt from the common audit fields — specs/04 §4.12a)."""
    from google.cloud import firestore  # lazy — offline py_compile friendly

    return {**doc, "updatedAt": firestore.SERVER_TIMESTAMP}


def current_ref(client):
    """``DocumentReference`` for ``portfolioSummaries/current``."""
    return refs.doc(client, refs.PORTFOLIO_SUMMARIES, CURRENT_DOC_ID)


def period_ref(client, period: str):
    """``DocumentReference`` for ``portfolioSummaries/{period}`` (``period`` = "YYYY-MM")."""
    return refs.doc(client, refs.PORTFOLIO_SUMMARIES, period)


def get_current(client) -> Optional[dict[str, Any]]:
    """Read ``portfolioSummaries/current`` as dict-with-id, or ``None``."""
    return refs.get(client, refs.PORTFOLIO_SUMMARIES, CURRENT_DOC_ID)


def get_period(client, period: str) -> Optional[dict[str, Any]]:
    """Read ``portfolioSummaries/{period}`` as dict-with-id, or ``None``."""
    return refs.get(client, refs.PORTFOLIO_SUMMARIES, period)


def write_current(client, doc: dict[str, Any]) -> None:
    """Overwrite ``portfolioSummaries/current`` with ``doc`` + ``updatedAt`` server time."""
    current_ref(client).set(_stamped(doc))


def write_period(client, period: str, doc: dict[str, Any]) -> None:
    """Overwrite ``portfolioSummaries/{period}`` with ``doc`` + ``updatedAt`` server time."""
    period_ref(client, period).set(_stamped(doc))
