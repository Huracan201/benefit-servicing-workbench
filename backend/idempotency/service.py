"""Idempotency record lifecycle — the central retry-safety mechanism.

Implements the specs/08 §8.2 create-in-transaction protocol and the §8.3 lease.
The record lives at ``idempotencyKeys/{key}`` (specs/04 §4.11) and is:

* **created inside** the state-transition transaction with a create-precondition
  (Firestore ``transaction.create`` fails if the doc already exists), so two
  concurrent same-key requests cannot both proceed — the loser's transaction
  aborts and, on retry, reads the now-existing ``PENDING`` record and returns
  ``IN_PROGRESS``;
* resolved to ``COMPLETED`` (with the response to replay) or ``FAILED`` (a
  retryable terminal state, distinct from a *completed failure* such as a
  declined payment which is ``COMPLETED`` with a failure result);
* protected by a **lease** (``leaseOwner`` / ``leaseExpiresAt``): a ``PENDING``
  record whose lease has expired is reclaimable so a crashed in-flight request
  cannot wedge the key forever.

``begin`` returns an :class:`Outcome` whose ``state`` tells the caller what to
do: proceed (``NEW``), replay the stored result (``COMPLETED``), return 202
(``IN_PROGRESS``), or reject with 409 (``REUSE``). The caller — a command
handler running inside the same transaction — is responsible for translating
those into HTTP via :mod:`commands.base`.

The idempotency doc is exempt from the common fields (no ``revision``; own
lifecycle timestamps — specs/04 §4.12a), so it is written directly rather than
through the repository stamping helpers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Optional

from common.enums import IdempotencyStatus

# ---------------------------------------------------------------------------
# Constants (specs/04 §4.1 collection name; specs/21 §21.1 retention).
# ---------------------------------------------------------------------------
IDEMPOTENCY_COLLECTION = "idempotencyKeys"
RETENTION_DAYS = 30  # expiresAt = completedAt + 30 days (specs/21 §21.1)


class BeginState(StrEnum):
    """Result state of :func:`begin` (the seam's ``NEW|COMPLETED|IN_PROGRESS|REUSE``)."""

    NEW = "NEW"                # no live record — proceed with the operation
    COMPLETED = "COMPLETED"    # prior success — replay ``result`` (200)
    IN_PROGRESS = "IN_PROGRESS"  # live lease held elsewhere — 202, client polls
    REUSE = "REUSE"            # same key, different request hash — 409


@dataclass
class Outcome:
    """What :func:`begin` decided for this ``(key, request_hash)`` pair.

    ``state``      — one of :class:`BeginState`.
    ``result``     — the stored response to replay (only when ``COMPLETED``).
    ``reclaimed``  — True when a ``NEW`` outcome came from reclaiming an
                     abandoned/expired lease (or retrying a ``FAILED`` record)
                     rather than a first-ever create; the command should re-drive
                     idempotently (specs/08 §8.3), never issue a fresh charge.
    ``existing``   — the prior record dict, when one was read (diagnostics).
    ``lease_owner``— the lease owner token now recorded on the PENDING record.
    """

    state: BeginState
    result: Any = None
    reclaimed: bool = False
    existing: Optional[dict] = None
    lease_owner: str = ""

    @property
    def is_new(self) -> bool:
        return self.state == BeginState.NEW

    @property
    def is_replay(self) -> bool:
        return self.state == BeginState.COMPLETED

    @property
    def is_in_progress(self) -> bool:
        return self.state == BeginState.IN_PROGRESS

    @property
    def is_reuse(self) -> bool:
        return self.state == BeginState.REUSE


# ---------------------------------------------------------------------------
# Firestore helpers
# ---------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _idem_ref(client: Any, key: str):
    """DocumentReference for ``idempotencyKeys/{key}``.

    Prefers the repository seam ``repositories.doc``; falls back to building the
    reference directly so the service is usable even before the repositories
    package is importable (and in unit tests with a fake client).
    """
    try:
        from repositories import doc as _doc  # type: ignore

        return _doc(client, IDEMPOTENCY_COLLECTION, key)
    except Exception:  # pragma: no cover - defensive fallback
        return client.collection(IDEMPOTENCY_COLLECTION).document(key)


def _read(txn: Any, ref: Any) -> Optional[dict]:
    """Read a document inside a transaction, returning its dict or ``None``.

    Firestore's ``Transaction.get`` yields ``DocumentSnapshot`` objects; a single
    document reference yields exactly one (possibly non-existent) snapshot.
    """
    got = txn.get(ref)
    # ``get`` may return a generator of snapshots or a single snapshot depending
    # on the client version — normalise both.
    snap = None
    if hasattr(got, "exists"):
        snap = got
    else:
        for candidate in got:
            snap = candidate
            break
    if snap is None or not getattr(snap, "exists", False):
        return None
    data = snap.to_dict() or {}
    data["id"] = snap.id
    return data


def _lease_valid(record: dict) -> bool:
    """True if the record's lease has NOT expired (still owned by a live driver)."""
    expires = record.get("leaseExpiresAt")
    if expires is None:
        return False
    if isinstance(expires, (int, float)):
        expires = datetime.fromtimestamp(expires, tz=timezone.utc)
    # Firestore returns tz-aware DatetimeWithNanoseconds; guard naive values.
    if getattr(expires, "tzinfo", None) is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > _now()


# ---------------------------------------------------------------------------
# begin / complete / fail
# ---------------------------------------------------------------------------
def begin(
    txn: Any,
    *,
    key: str,
    operation: str,
    request_hash: str,
    entity_id: str,
    lease_ttl_seconds: int,
    entity_type: Optional[str] = None,
    lease_owner: Optional[str] = None,
    client: Any = None,
) -> Outcome:
    """Open (or replay) an idempotency record inside the caller's transaction.

    MUST be called after the caller has decided to mutate, and its writes MUST
    be part of the *same* transaction as the state change (specs/08 §8.2). The
    record is read first (part of the transaction read set) and, when a fresh
    attempt is warranted, written with a create-precondition so concurrent
    same-key requests cannot both win.

    Decision table (specs/08 §8.2):

    ======================================  ===========================
    Prior record                            Outcome
    ======================================  ===========================
    absent                                  NEW (create PENDING)
    COMPLETED, hash matches                 COMPLETED (replay result)
    COMPLETED / FAILED, hash differs        REUSE (409)
    PENDING, lease valid                    IN_PROGRESS (202)
    PENDING, lease expired, hash matches    NEW, reclaimed (overwrite PENDING)
    PENDING, lease expired, hash differs    REUSE (409)
    FAILED, hash matches                    NEW, reclaimed (overwrite PENDING)
    ======================================  ===========================

    On a ``NEW`` outcome the record has been (over)written to ``PENDING`` with a
    fresh lease inside ``txn``; the caller proceeds to write the state change and
    later calls :func:`complete`. On any non-NEW outcome the caller has written
    nothing and should return/raise per the state.
    """
    if client is None:
        from common.firestore import get_client

        client = get_client()

    owner = lease_owner or f"run_{uuid.uuid4().hex}"
    ref = _idem_ref(client, key)
    existing = _read(txn, ref)

    if existing is not None:
        status = existing.get("status")
        stored_hash = existing.get("requestHash")
        stored_entity = existing.get("entityId")
        hash_matches = stored_hash == request_hash and (
            stored_entity is None or stored_entity == entity_id
        )

        if status == IdempotencyStatus.COMPLETED:
            if hash_matches:
                return Outcome(
                    state=BeginState.COMPLETED,
                    result=existing.get("result"),
                    existing=existing,
                )
            return Outcome(state=BeginState.REUSE, existing=existing)

        if status == IdempotencyStatus.PENDING:
            if _lease_valid(existing):
                return Outcome(state=BeginState.IN_PROGRESS, existing=existing)
            # Lease expired -> reclaimable, but ONLY for the same request. A
            # different requestHash (or entityId) on an expired PENDING lease is
            # key reuse, exactly as in the COMPLETED / FAILED branches — never
            # reclaim it for a different operation/body/entity.
            if not hash_matches:
                return Outcome(state=BeginState.REUSE, existing=existing)
            # Same request -> reclaim: overwrite to a fresh PENDING below.
            _write_pending(
                txn, ref, key=key, operation=operation, request_hash=request_hash,
                entity_id=entity_id, entity_type=entity_type, owner=owner,
                lease_ttl_seconds=lease_ttl_seconds, overwrite=True,
                previous=existing,
            )
            return Outcome(state=BeginState.NEW, reclaimed=True,
                           existing=existing, lease_owner=owner)

        if status == IdempotencyStatus.FAILED:
            if hash_matches:
                # A retryable terminal failure with the same request -> fresh attempt.
                _write_pending(
                    txn, ref, key=key, operation=operation,
                    request_hash=request_hash, entity_id=entity_id,
                    entity_type=entity_type, owner=owner,
                    lease_ttl_seconds=lease_ttl_seconds, overwrite=True,
                    previous=existing,
                )
                return Outcome(state=BeginState.NEW, reclaimed=True,
                               existing=existing, lease_owner=owner)
            return Outcome(state=BeginState.REUSE, existing=existing)

        # Unknown status -> treat conservatively as reuse (do not double-execute).
        return Outcome(state=BeginState.REUSE, existing=existing)

    # No prior record: create with a create-precondition (the race primitive).
    _write_pending(
        txn, ref, key=key, operation=operation, request_hash=request_hash,
        entity_id=entity_id, entity_type=entity_type, owner=owner,
        lease_ttl_seconds=lease_ttl_seconds, overwrite=False,
    )
    return Outcome(state=BeginState.NEW, reclaimed=False, lease_owner=owner)


def _write_pending(
    txn: Any,
    ref: Any,
    *,
    key: str,
    operation: str,
    request_hash: str,
    entity_id: str,
    entity_type: Optional[str],
    owner: str,
    lease_ttl_seconds: int,
    overwrite: bool,
    previous: Optional[dict] = None,
) -> None:
    """Write a PENDING record. ``overwrite=False`` uses a create-precondition."""
    from google.cloud import firestore  # lazy import

    lease_expires = _now() + timedelta(seconds=lease_ttl_seconds)
    data = {
        "operation": operation,
        "requestHash": request_hash,
        "status": IdempotencyStatus.PENDING.value,
        "entityType": entity_type,
        "entityId": entity_id,
        "leaseOwner": owner,
        "leaseExpiresAt": lease_expires,
        "result": None,
        "completedAt": None,
        "expiresAt": None,
        "updatedAt": firestore.SERVER_TIMESTAMP,
    }
    if overwrite:
        # Reclamation: preserve original createdAt, bump updatedAt/lease.
        data["createdAt"] = (previous or {}).get("createdAt") or firestore.SERVER_TIMESTAMP
        txn.set(ref, data)
    else:
        # First attempt: create-precondition (fails at commit if it now exists),
        # the atomic "first request wins" guarantee (specs/08 §8.2).
        data["createdAt"] = firestore.SERVER_TIMESTAMP
        txn.create(ref, data)


def complete(txn: Any, key: str, result: Any, *, client: Any = None) -> None:
    """Mark the record COMPLETED with the response to replay (specs/08 §8.2).

    Called in the finalization transaction (for the two-phase payment, Phase 3).
    Clears the lease, stores ``result`` for replay, and sets ``expiresAt`` for
    TTL retention. Must run in the same transaction as the state change it
    finalizes so the completion fact and the outcome commit together.
    """
    from google.cloud import firestore  # lazy import

    if client is None:
        from common.firestore import get_client

        client = get_client()
    ref = _idem_ref(client, key)
    expires_at = _now() + timedelta(days=RETENTION_DAYS)
    txn.update(
        ref,
        {
            "status": IdempotencyStatus.COMPLETED.value,
            "result": result,
            "completedAt": firestore.SERVER_TIMESTAMP,
            "updatedAt": firestore.SERVER_TIMESTAMP,
            "expiresAt": expires_at,
            "leaseOwner": None,
            "leaseExpiresAt": None,
        },
    )


def fail(txn: Any, key: str, *, reason: Optional[str] = None,
         client: Any = None) -> None:
    """Mark the record FAILED — a retryable terminal state (specs/08 §8.2).

    Distinct from a *completed failure* (a declined payment) which is
    ``COMPLETED`` with a failure result via :func:`complete`. A ``FAILED``
    record with a matching request hash lets a later same-key retry begin a
    fresh attempt (:func:`begin`).
    """
    from google.cloud import firestore  # lazy import

    if client is None:
        from common.firestore import get_client

        client = get_client()
    ref = _idem_ref(client, key)
    expires_at = _now() + timedelta(days=RETENTION_DAYS)
    patch = {
        "status": IdempotencyStatus.FAILED.value,
        "updatedAt": firestore.SERVER_TIMESTAMP,
        "expiresAt": expires_at,
        "leaseOwner": None,
        "leaseExpiresAt": None,
    }
    if reason is not None:
        patch["failureReason"] = reason
    txn.update(ref, patch)
