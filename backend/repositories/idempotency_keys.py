"""Idempotency-record data-access gateway — ``idempotencyKeys/{idempotencyKey}``
(specs/04 §4.11).

The id is the client ``Idempotency-Key`` header verbatim. The record is created
**inside** the state-transition transaction with a create-precondition (specs/08
§8.2) — the idempotency service owns that logic; this gateway is address-only.
Own lifecycle timestamps, no ``revision`` (specs/04 §4.12a).

``expired_leases`` backs the ``reap-expired-leases`` job (specs/08 §8.3): a
paginated scan of ``PENDING`` records whose lease has lapsed — the abandoned keys
a crashed driver left behind. Healthy in-flight async keys (still within their
``ASYNC_LEASE_TTL``) have ``leaseExpiresAt`` in the future and are excluded by the
inequality, so the reaper never touches a live task's key.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from common.enums import IdempotencyStatus

from . import refs


def ref(client, key: str):
    """``DocumentReference`` for ``idempotencyKeys/{key}``."""
    return refs.doc(client, refs.IDEMPOTENCY_KEYS, key)


def get(client, key: str) -> Optional[dict[str, Any]]:
    """Read the idempotency record as dict-with-id, or ``None``."""
    return refs.get(client, refs.IDEMPOTENCY_KEYS, key)


def expired_leases(
    client,
    *,
    now: datetime,
    limit: int = refs.BATCH_SIZE,
    start_after: Optional[Any] = None,
) -> tuple[list[dict[str, Any]], Optional[Any]]:
    """One page of ``PENDING`` records whose lease expired before ``now``.

    Query: ``status == PENDING`` AND ``leaseExpiresAt < now``, ordered by
    ``leaseExpiresAt`` (the inequality field must lead the ordering) then by
    ``__name__`` (the document id) as a **stable total-order tiebreak** — many
    records can share a lease expiry, so ``leaseExpiresAt`` alone is not a
    deterministic cursor; the unique id pins every cursor to an exact position
    (mirrors ``contributions.due``).

    Returns ``(page, next_cursor)``. ``page`` is up to ``limit`` dict-with-id
    records in ``(leaseExpiresAt, id)`` order. ``next_cursor`` is the last
    document's snapshot when a *full* page was returned — pass it back as
    ``start_after`` for the next page — else ``None`` (terminal page).

    Served by the existing ``(status, leaseExpiresAt)`` composite index:
    Firestore appends ``__name__`` ascending as the implicit final ordering, so
    the ``__name__`` tiebreak needs no additional index.
    """
    query = (
        client.collection(refs.IDEMPOTENCY_KEYS)
        .where(filter=refs.field_filter("status", "==", IdempotencyStatus.PENDING.value))
        .where(filter=refs.field_filter("leaseExpiresAt", "<", now))
        .order_by("leaseExpiresAt")
        .order_by("__name__")
    )
    if start_after is not None:
        query = query.start_after(start_after)
    snapshots = list(query.limit(limit).stream())
    page = [refs.snapshot_to_dict(snap) for snap in snapshots]
    next_cursor = snapshots[-1] if len(snapshots) == limit else None
    return page, next_cursor
