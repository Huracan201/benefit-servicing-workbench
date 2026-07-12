"""Scheduled-contribution data-access gateway —
``scheduledContributions/{contributionId}`` (specs/04 §4.7).

Deterministic id ``{agreementId}__{installmentNumber:03d}`` (``common.ids``).
Queries here back the payment look-ahead (``next_scheduled``), the schedule
listing (``list_for_agreement``), and the monthly/sweeper due-run (``due``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from common.enums import ContributionStatus
from common.ids import contribution_id as _contribution_id

from . import refs


def ref(client, contribution_id: str):
    """``DocumentReference`` for ``scheduledContributions/{contribution_id}``."""
    return refs.doc(client, refs.SCHEDULED_CONTRIBUTIONS, contribution_id)


def ref_for_installment(client, agreement_id: str, installment_number: int):
    """``DocumentReference`` addressed by ``(agreement_id, installment_number)``."""
    return ref(client, _contribution_id(agreement_id, installment_number))


def get(client, contribution_id: str) -> Optional[dict[str, Any]]:
    """Read the contribution document as dict-with-id, or ``None``."""
    return refs.get(client, refs.SCHEDULED_CONTRIBUTIONS, contribution_id)


def list_for_agreement(client, agreement_id: str) -> list[dict[str, Any]]:
    """All contributions for an agreement, ordered by ``installmentNumber``."""
    query = (
        client.collection(refs.SCHEDULED_CONTRIBUTIONS)
        .where(filter=refs.field_filter("benefitAgreementId", "==", agreement_id))
        .order_by("installmentNumber")
    )
    return refs.stream_to_dicts(query)


def next_scheduled(client, agreement_id: str) -> Optional[dict[str, Any]]:
    """Lowest-``installmentNumber`` still-``SCHEDULED`` contribution, or ``None``.

    Backs the loan look-ahead fields (``nextContributionDate`` /
    ``nextContributionAmountCents``) — specs/04 §4.5.
    """
    query = (
        client.collection(refs.SCHEDULED_CONTRIBUTIONS)
        .where(filter=refs.field_filter("benefitAgreementId", "==", agreement_id))
        .where(filter=refs.field_filter("status", "==", str(ContributionStatus.SCHEDULED)))
        .order_by("installmentNumber")
        .limit(1)
    )
    docs = refs.stream_to_dicts(query)
    return docs[0] if docs else None


def due(
    client,
    as_of: datetime,
    *,
    status: str = str(ContributionStatus.SCHEDULED),
) -> list[dict[str, Any]]:
    """Contributions in ``status`` whose ``scheduledDate`` is on/before ``as_of``.

    Ordered by ``scheduledDate`` (the inequality field must lead the ordering).
    Drives the monthly processing run and the reconciliation sweeper.
    """
    query = (
        client.collection(refs.SCHEDULED_CONTRIBUTIONS)
        .where(filter=refs.field_filter("status", "==", str(status)))
        .where(filter=refs.field_filter("scheduledDate", "<=", as_of))
        .order_by("scheduledDate")
    )
    return refs.stream_to_dicts(query)
