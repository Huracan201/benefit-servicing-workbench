"""Employer-summary read-model gateway — ``employerSummaries/{employerId}`` and
its ``periods/{YYYY-MM}`` subcollection (specs/05 §5.4).

The base doc holds point-in-time per-employer totals; each ``periods`` doc holds
that employer's per-month flow. Both are **read models** — derived, eventually
consistent, and *exempt* from the common ``revision``/``createdBy`` audit fields
(specs/04 §4.12a): a write stamps ``updatedAt = SERVER_TIMESTAMP`` directly (never
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


def ref(client, employer_id: str):
    """``DocumentReference`` for ``employerSummaries/{employer_id}``."""
    return refs.doc(client, refs.EMPLOYER_SUMMARIES, employer_id)


def period_ref(client, employer_id: str, period: str):
    """``DocumentReference`` for ``employerSummaries/{employer_id}/periods/{period}``."""
    return (
        ref(client, employer_id)
        .collection(refs.EMPLOYER_SUMMARY_PERIODS)
        .document(period)
    )


def get(client, employer_id: str) -> Optional[dict[str, Any]]:
    """Read the employer-summary base doc as dict-with-id, or ``None``."""
    return refs.get(client, refs.EMPLOYER_SUMMARIES, employer_id)


def get_period(client, employer_id: str, period: str) -> Optional[dict[str, Any]]:
    """Read ``employerSummaries/{employer_id}/periods/{period}``, or ``None``."""
    return refs.snapshot_to_dict(period_ref(client, employer_id, period).get())


def write(client, employer_id: str, doc: dict[str, Any]) -> None:
    """Overwrite the employer-summary base doc with ``doc`` + ``updatedAt`` server time."""
    ref(client, employer_id).set(_stamped(doc))


def write_period(client, employer_id: str, period: str, doc: dict[str, Any]) -> None:
    """Overwrite the employer period doc with ``doc`` + ``updatedAt`` server time."""
    period_ref(client, employer_id, period).set(_stamped(doc))
