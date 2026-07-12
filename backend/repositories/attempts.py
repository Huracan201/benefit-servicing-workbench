"""Payment-attempt data-access gateway —
``scheduledContributions/{contributionId}/attempts/{attemptId}`` (specs/04 §4.8).

The subcollection is the **authoritative** attempt store (specs/04 §4.1 note).
Attempts are append-only with their own lifecycle fields (no common fields), so
there are no ``stamp_*`` helpers here. Deterministic id
``{contributionId}__att_{attemptNumber:03d}`` (``common.ids``).
"""

from __future__ import annotations

from typing import Any, Optional

from common.ids import attempt_id as _attempt_id

from . import refs


def _collection(client, contribution_id: str):
    return (
        client.collection(refs.SCHEDULED_CONTRIBUTIONS)
        .document(contribution_id)
        .collection(refs.ATTEMPTS)
    )


def ref(client, contribution_id: str, attempt_number: int):
    """``DocumentReference`` for the attempt ``(contribution_id, attempt_number)``."""
    return _collection(client, contribution_id).document(
        _attempt_id(contribution_id, attempt_number)
    )


def get(
    client, contribution_id: str, attempt_number: int
) -> Optional[dict[str, Any]]:
    """Read the attempt document as dict-with-id, or ``None``."""
    return refs.snapshot_to_dict(ref(client, contribution_id, attempt_number).get())


def list_for_contribution(
    client, contribution_id: str
) -> list[dict[str, Any]]:
    """All attempts for a contribution, ordered by ``attemptNumber``."""
    query = _collection(client, contribution_id).order_by("attemptNumber")
    return refs.stream_to_dicts(query)
