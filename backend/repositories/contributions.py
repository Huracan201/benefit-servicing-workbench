"""Scheduled-contribution data-access gateway —
``scheduledContributions/{contributionId}`` (specs/04 §4.7).

Deterministic id ``{agreementId}__{installmentNumber:03d}`` (``common.ids``).
Queries here back the payment look-ahead (``next_scheduled``), the schedule
listing (``list_for_agreement``), the paginated enqueue-due run (``due``), and
the reconciliation sweeper's stuck-payment scans (``stuck_processing`` +
``stale_started_attempts`` — specs/09 §9.4).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from common.enums import ContributionStatus, PaymentAttemptStatus
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
    limit: int = refs.BATCH_SIZE,
    start_after: Optional[Any] = None,
) -> tuple[list[dict[str, Any]], Optional[Any]]:
    """One page of contributions in ``status`` with ``scheduledDate <= as_of``.

    Ordered by ``scheduledDate`` (the inequality field must lead the ordering),
    then by ``__name__`` (the document id) as a **stable total-order tiebreak**:
    many installments share the same noon ``scheduledDate`` (SYSTEM_TIMEZONE), so
    ``scheduledDate`` alone is *not* a deterministic cursor — a page boundary that
    lands inside a shared timestamp would skip or duplicate rows. Ordering
    additionally by the unique id pins every cursor to an exact position.

    Returns ``(page, next_cursor)``. ``page`` is up to ``limit`` dict-with-id
    documents in ``(scheduledDate, id)`` order. ``next_cursor`` is the last
    document's snapshot — pass it back as ``start_after`` to fetch the next page —
    when a *full* page was returned, else ``None`` to signal the terminal page (a
    short or empty page means no rows remain).

    Drives the enqueue-due run: the caller loops, feeding ``next_cursor`` back in
    until it is ``None``. (The reconciliation sweeper is a *separate* query keyed
    on ``lastAttemptAt`` — not this one.) Requires the ``(status, scheduledDate,
    __name__)`` composite index.
    """
    query = (
        client.collection(refs.SCHEDULED_CONTRIBUTIONS)
        .where(filter=refs.field_filter("status", "==", str(status)))
        .where(filter=refs.field_filter("scheduledDate", "<=", as_of))
        .order_by("scheduledDate")
        .order_by("__name__")
    )
    if start_after is not None:
        query = query.start_after(start_after)
    snapshots = list(query.limit(limit).stream())
    page = [refs.snapshot_to_dict(snap) for snap in snapshots]
    next_cursor = snapshots[-1] if len(snapshots) == limit else None
    return page, next_cursor


def stuck_processing(
    client,
    *,
    older_than: datetime,
    limit: int = refs.BATCH_SIZE,
    start_after: Optional[Any] = None,
) -> tuple[list[dict[str, Any]], Optional[Any]]:
    """One page of contributions stuck ``PROCESSING`` since before ``older_than``.

    The **real driver** of the reconciliation sweeper (specs/09 §9.4 scan (a)): a
    contribution left ``PROCESSING`` whose ``lastAttemptAt`` predates
    ``now − STUCK_THRESHOLD`` is one whose two-phase payment began but never
    finalized — the driver died between Phase 2 (charge persisted at the
    processor) and Phase 3 (finalize), specs/08 §8.4. The caller re-queries the
    processor by the attempt's deterministic key and finalizes idempotently
    (:mod:`contributions.reconcile`). Pass ``older_than = now − STUCK_THRESHOLD``.

    Ordered by ``lastAttemptAt`` (the inequality field must lead the ordering),
    then by ``__name__`` (the document id) as a **stable total-order tiebreak** —
    exactly as ``due``: several contributions can share one ``lastAttemptAt``
    instant, so it is not a deterministic cursor on its own; ordering
    additionally by the unique id pins every page boundary to an exact position,
    so a fan-out never skips or double-reconciles a stuck contribution.

    Returns ``(page, next_cursor)`` with the same contract as ``due``: ``page`` is
    up to ``limit`` dict-with-id documents in ``(lastAttemptAt, id)`` order;
    ``next_cursor`` is the last snapshot when a *full* page was returned (feed it
    back as ``start_after`` for the next page), else ``None`` on the terminal
    (short or empty) page. Requires the ``(status, lastAttemptAt, __name__)``
    composite index.
    """
    query = (
        client.collection(refs.SCHEDULED_CONTRIBUTIONS)
        .where(
            filter=refs.field_filter(
                "status", "==", str(ContributionStatus.PROCESSING)
            )
        )
        .where(filter=refs.field_filter("lastAttemptAt", "<", older_than))
        .order_by("lastAttemptAt")
        .order_by("__name__")
    )
    if start_after is not None:
        query = query.start_after(start_after)
    snapshots = list(query.limit(limit).stream())
    page = [refs.snapshot_to_dict(snap) for snap in snapshots]
    next_cursor = snapshots[-1] if len(snapshots) == limit else None
    return page, next_cursor


def stale_started_attempts(
    client,
    *,
    older_than: datetime,
    limit: int = refs.BATCH_SIZE,
    start_after: Optional[Any] = None,
) -> tuple[list[dict[str, Any]], Optional[Any]]:
    """One page of payment attempts still ``STARTED`` since before ``older_than``.

    A **collection-group** scan across every ``attempts`` subcollection (specs/09
    §9.4 scan (b)) — the safety net for scan (a) above: it catches a ``STARTED``
    attempt whose contribution has already *moved* off ``PROCESSING`` (a
    mis-finalized stale driver — the contribution status advanced but the attempt
    was never closed), which the status-keyed ``stuck_processing`` scan cannot
    see. Each returned attempt carries its ``contributionId`` (specs/04 §4.8), so
    the sweeper reconciles the owning contribution directly with nothing the dead
    process held.

    Ordered by ``startedAt`` (the inequality field must lead), then ``__name__``
    — the same stable-cursor tiebreak as ``due`` / ``stuck_processing``; for a
    collection-group query ``__name__`` is the full document path, a total order
    spanning all subcollections. Returns ``(page, next_cursor)`` with the
    identical pagination contract (full page → cursor; short/empty → ``None``).
    Requires the ``attempts`` **collection-group** composite index
    ``(status, startedAt, __name__)``.
    """
    query = (
        client.collection_group(refs.ATTEMPTS)
        .where(
            filter=refs.field_filter(
                "status", "==", str(PaymentAttemptStatus.STARTED)
            )
        )
        .where(filter=refs.field_filter("startedAt", "<", older_than))
        .order_by("startedAt")
        .order_by("__name__")
    )
    if start_after is not None:
        query = query.start_after(start_after)
    snapshots = list(query.limit(limit).stream())
    page = [refs.snapshot_to_dict(snap) for snap in snapshots]
    next_cursor = snapshots[-1] if len(snapshots) == limit else None
    return page, next_cursor
