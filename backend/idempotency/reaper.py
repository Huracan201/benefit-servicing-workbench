"""Lease reaper — reclaim abandoned ``PENDING`` idempotency keys (specs/08 §8.3).

The scheduled ``reap-expired-leases`` job's engine. It pages
:func:`repositories.idempotency_keys.expired_leases` (``status == PENDING`` AND
``leaseExpiresAt < now``) and, per record, drives the specs/08 §8.3 reclamation
for that record's ``operation``:

    ================================  ==========================================
    operation                         reclamation action
    ================================  ==========================================
    PROCESS_CONTRIBUTION / RETRY_…     re-drive reconciliation (get_status →
                                      finalize) for the in-flight attempt, then
                                      complete the record — NEVER a fresh charge
    activate-benefit                  re-enqueue generate-schedule if the schedule
                                      is not yet generated (installmentsGenerated)
    terminate-benefit                 re-enqueue cancel-future-contributions
    change-employment-status          (borrower-scoped cascade — see note below)
    resume-benefit                    re-enqueue shift-schedule
    exception / note / role / suspend re-run is not possible without the original
                                      request body → mark the orphaned record
                                      FAILED so a same-key retry is unblocked
    ================================  ==========================================

Two invariants make this safe:

* **Never a fresh side effect.** Every re-drive is idempotent by construction —
  reconciliation queries the processor with the attempt's deterministic
  ``processorIdempotencyKey`` (``get_status``, never ``charge``); the re-enqueued
  tasks key on deterministic downstream IDs (contribution / attempt / event ids)
  so redelivery is a no-op; the tiny sync ops either fully committed (then the
  record is COMPLETED, not PENDING) or fully rolled back (no side effect at all).
* **Never a live key.** ``expired_leases`` already excludes any key whose lease is
  still valid — a healthy async command mid-flight has ``leaseExpiresAt`` in the
  future (``ASYNC_LEASE_TTL``), so it is filtered out. The per-record ownership
  claim re-checks this inside a transaction, closing the read-then-act race with a
  same-key client retry that reclaims the key first.

Ownership is re-established (a fresh ``leaseOwner`` + extended ``leaseExpiresAt``)
in a small transaction *before* dispatch so two concurrent reaper passes cannot
both fire the same reclamation; the actual re-drive (which has side effects /
enqueues) runs *outside* that transaction.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from commands.base import LEASE_TTL_SECONDS, transactional
from common.enums import IdempotencyStatus
from repositories import idempotency_keys, refs

logger = logging.getLogger("bsw.internal")

# ---------------------------------------------------------------------------
# Operation strings — MUST match the values the command modules write into the
# idempotency record's ``operation`` field (grep ``OPERATION*`` in the commands).
# ---------------------------------------------------------------------------
OP_PROCESS = "PROCESS_CONTRIBUTION"          # payments.service.OPERATION_PROCESS
OP_RETRY = "RETRY_CONTRIBUTION"              # payments.service.OPERATION_RETRY
OP_ACTIVATE = "activate-benefit"            # benefits.services.OPERATION
OP_TERMINATE = "terminate-benefit"          # benefits.services.OPERATION_TERMINATE
OP_RESUME = "resume-benefit"                # benefits.services.OPERATION_RESUME
OP_EMPLOYMENT = "change-employment-status"  # employment.services.OPERATION

# Synchronous, tiny commands: begin + complete run in ONE transaction, so a
# persisted PENDING record means the transaction rolled back (no side effect).
# They carry no re-runnable payload → the reaper unblocks the key by FAILING it.
_TINY_SYNC_OPS = frozenset(
    {
        "suspend-benefit",
        "create-exception",
        "assign-exception",
        "mark-exception-in-review",
        "resolve-exception",
        "dismiss-exception",
        "add-note",
        "set-user-role",
        "set-employer-status",
    }
)

# How long the reaper holds its reclamation lease (bounds a concurrent reaper
# pass; the re-driven work either completes the key or fails it well within it).
REAP_LEASE_TTL_SECONDS = LEASE_TTL_SECONDS

# Canned reasons for re-enqueued cascades (the original reason is not stored).
_TERMINATE_REASON = "benefit terminated (lease-reaper re-drive)"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_dt(value: Any) -> Optional[datetime]:
    """Normalise a stored ``leaseExpiresAt`` to a tz-aware ``datetime``."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _lease_expired(record: dict, now: datetime) -> bool:
    """True if the record's lease is absent or lapsed relative to ``now``."""
    expires = _coerce_dt(record.get("leaseExpiresAt"))
    return expires is None or expires <= now


def _single_snapshot(got: Any):
    """Normalise ``Transaction.get`` (snapshot or 1-item iterable) to a snapshot."""
    if hasattr(got, "exists"):
        return got
    for candidate in got:
        return candidate
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def reap_expired_leases(
    client: Any = None,
    ctx: Any = None,
    *,
    now: Optional[datetime] = None,
    limit: int = refs.BATCH_SIZE,
    max_pages: int = 50,
) -> dict:
    """Reclaim every expired ``PENDING`` idempotency key (the job engine).

    Pages :func:`repositories.idempotency_keys.expired_leases` and dispatches the
    specs/08 §8.3 reclamation per record. ``max_pages`` bounds a single run so an
    unexpectedly huge backlog cannot loop unboundedly — the next scheduled run
    picks up the remainder (every action is idempotent, so re-scanning is safe).

    Returns a summary ``{"scanned", "reclaimed", "skipped", "results"}``.
    """
    if client is None:
        from common.firestore import get_client

        client = get_client()
    if ctx is None:
        from internal.system_context import system_ctx

        ctx = system_ctx("reap-expired-leases")

    at = now or _now()
    results: list[dict] = []
    cursor: Optional[Any] = None
    pages = 0
    while pages < max_pages:
        page, cursor = idempotency_keys.expired_leases(
            client, now=at, limit=limit, start_after=cursor
        )
        for record in page:
            results.append(_reclaim_one(client, ctx, record, now=at))
        pages += 1
        if cursor is None:
            break

    reclaimed = sum(1 for r in results if r.get("reclaimed"))
    summary = {
        "job": "reap-expired-leases",
        "scanned": len(results),
        "reclaimed": reclaimed,
        "skipped": len(results) - reclaimed,
        "results": results,
    }
    logger.info(
        "reap-expired-leases scanned=%s reclaimed=%s skipped=%s",
        summary["scanned"], summary["reclaimed"], summary["skipped"],
    )
    return summary


# ---------------------------------------------------------------------------
# Per-record reclamation
# ---------------------------------------------------------------------------
def _reclaim_one(client: Any, ctx: Any, record: dict, *, now: datetime) -> dict:
    """Claim ownership of one expired lease, then dispatch its reclamation."""
    key = record.get("id")
    operation = record.get("operation")

    # 1) Re-establish ownership transactionally. Re-reads the record inside the
    #    txn and bails if it is no longer a lease-expired PENDING (a same-key
    #    client retry or a prior reaper pass may have claimed/resolved it first).
    claimed = _claim_ownership(client, key, now=now)
    if claimed is None:
        return _skip(key, operation, "no longer a lease-expired PENDING record")

    # 2) Dispatch the reclamation OUTSIDE the txn (it enqueues / calls adapters).
    entity_id = claimed.get("entityId")
    try:
        if operation in (OP_PROCESS, OP_RETRY):
            return _reclaim_process(client, ctx, key, entity_id, operation)
        if operation == OP_ACTIVATE:
            return _reclaim_activate(client, ctx, key, entity_id, operation)
        if operation == OP_TERMINATE:
            return _reclaim_cancel_future(
                client, ctx, key, entity_id, operation, reason=_TERMINATE_REASON
            )
        if operation == OP_RESUME:
            return _reclaim_shift(client, ctx, key, entity_id, operation)
        if operation == OP_EMPLOYMENT:
            # Borrower-scoped cascade: entityId is a borrowerId, but the re-drive
            # (cancel-future-contributions / shift-schedule) is agreement-scoped
            # and there is no borrower→agreements repository query yet. The
            # employment task layer owns this re-drive; the reaper defers rather
            # than guess an agreement id. See contractNotes.
            return _skip(
                key, operation,
                "employment cascade re-drive deferred to the task layer "
                "(needs a borrower→agreements fan-out)",
            )
        if operation in _TINY_SYNC_OPS:
            return _reclaim_tiny(client, key, operation)
        return _skip(key, operation, "no reclamation registered for operation")
    except Exception as exc:  # noqa: BLE001 — one bad record must not abort the run
        logger.exception("reap dispatch failed key=%s operation=%s", key, operation)
        return {
            "key": key,
            "operation": operation,
            "action": "error",
            "reclaimed": False,
            "error": str(exc),
        }


def _claim_ownership(client: Any, key: str, *, now: datetime) -> Optional[dict]:
    """Extend the lease to a fresh reaper owner iff still an expired PENDING.

    Returns the record dict on success, or ``None`` if the in-txn re-read shows
    the record is gone, no longer PENDING, or its lease is now valid (a live
    driver / retry reclaimed it between the query and here).
    """
    from google.cloud import firestore  # lazy import

    ref = idempotency_keys.ref(client, key)
    owner = f"reaper_{uuid.uuid4().hex}"

    def _run(txn: Any) -> Optional[dict]:
        snap = _single_snapshot(txn.get(ref))
        if snap is None or not getattr(snap, "exists", False):
            return None
        rec = snap.to_dict() or {}
        rec["id"] = snap.id
        if rec.get("status") != IdempotencyStatus.PENDING.value:
            return None
        if not _lease_expired(rec, now):
            return None
        txn.update(
            ref,
            {
                "leaseOwner": owner,
                "leaseExpiresAt": now + timedelta(seconds=REAP_LEASE_TTL_SECONDS),
                "updatedAt": firestore.SERVER_TIMESTAMP,
            },
        )
        return rec

    return transactional(client)(_run)()


# --- reclamation actions ---------------------------------------------------
def _reclaim_process(
    client: Any, ctx: Any, key: str, entity_id: str, operation: str
) -> dict:
    """PROCESS_CONTRIBUTION: re-drive reconciliation, which completes the key.

    ``reconcile_contribution`` re-queries the processor with the attempt's
    deterministic ``processorIdempotencyKey`` (``get_status``, never a fresh
    charge) and finalizes; passing ``idempotency_key`` makes it complete THIS
    record inside that finalize (specs/08 §8.3 "then complete the record").
    """
    from contributions.reconcile import reconcile_contribution  # lazy: avoid cycle

    result = reconcile_contribution(
        entity_id, ctx=ctx, idempotency_key=key, client=client
    )
    return {
        "key": key,
        "operation": operation,
        "action": "reconciled",
        "reclaimed": True,
        "entityId": entity_id,
        "result": result,
    }


def _reclaim_activate(
    client: Any, ctx: Any, key: str, entity_id: str, operation: str
) -> dict:
    """ACTIVATE_BENEFIT: re-enqueue generate-schedule unless already generated.

    Guarded by ``agreement.installmentsGenerated`` (specs/08 §8.3): if the tail
    already generated the schedule, the reaper does not re-enqueue — the tail task
    owns completing the key. Otherwise re-enqueue ``generate-schedule``
    (idempotent — deterministic contribution ids make redelivery a no-op). The
    ``idempotencyKey`` is threaded through so ``generate_schedule_task`` completes
    THIS activate key on the finalized (ACTIVE) outcome — otherwise the key wedges
    PENDING forever and a same-key client replay never resolves past ``202``.
    """
    from repositories import agreements

    agreement = agreements.get(client, entity_id)
    if agreement is None:
        return _skip(key, operation, f"agreement {entity_id} not found")
    if agreement.get("installmentsGenerated"):
        return _skip(
            key, operation,
            "schedule already generated; tail task owns key completion",
        )
    _enqueue(
        ctx, "generate-schedule", {"agreementId": entity_id, "idempotencyKey": key}
    )
    return {
        "key": key,
        "operation": operation,
        "action": "re-enqueued generate-schedule",
        "reclaimed": True,
        "entityId": entity_id,
    }


def _reclaim_cancel_future(
    client: Any, ctx: Any, key: str, entity_id: str, operation: str, *, reason: str
) -> dict:
    """TERMINATE_BENEFIT: re-enqueue the agreement-scoped cancel-future cascade.

    The tail task completes the key (it carries ``idempotencyKey``); the
    status-guarded transitions make redelivery safe.
    """
    _enqueue(
        ctx,
        "cancel-future-contributions",
        {"agreementId": entity_id, "reason": reason, "idempotencyKey": key},
    )
    return {
        "key": key,
        "operation": operation,
        "action": "re-enqueued cancel-future-contributions",
        "reclaimed": True,
        "entityId": entity_id,
    }


def _reclaim_shift(
    client: Any, ctx: Any, key: str, entity_id: str, operation: str
) -> dict:
    """RESUME: re-enqueue shift-schedule (the tail task completes the key)."""
    _enqueue(
        ctx,
        "shift-schedule",
        {"agreementId": entity_id, "idempotencyKey": key},
    )
    return {
        "key": key,
        "operation": operation,
        "action": "re-enqueued shift-schedule",
        "reclaimed": True,
        "entityId": entity_id,
    }


def _reclaim_tiny(client: Any, key: str, operation: str) -> dict:
    """Tiny sync op: unblock the orphaned key by marking it FAILED.

    A tiny command's ``begin`` + ``complete`` share one transaction, so a
    persisted PENDING with an expired lease means the transaction never committed
    (no side effect occurred). The record cannot be re-run (its request body is
    not stored), so the reaper marks it ``FAILED`` — a retryable terminal state:
    a later same-key retry with a matching request hash begins a fresh attempt
    (:func:`idempotency.service.begin`), instead of the key wedging PENDING.
    """
    from idempotency import service as idempotency

    transactional(client)(
        lambda txn: idempotency.fail(
            txn, key, reason="lease reaped (orphaned sync op)", client=client
        )
    )()
    return {
        "key": key,
        "operation": operation,
        "action": "failed (orphaned tiny sync op unblocked for retry)",
        "reclaimed": True,
    }


# --- helpers ---------------------------------------------------------------
def _enqueue(ctx: Any, task: str, payload: dict) -> None:
    """Re-enqueue a tail task (lazy import of the enqueue seam to avoid a cycle)."""
    from internal.enqueue import enqueue

    enqueue(task, payload, ctx=ctx)


def _skip(key: Optional[str], operation: Any, reason: str) -> dict:
    return {
        "key": key,
        "operation": operation,
        "action": "skipped",
        "reclaimed": False,
        "reason": reason,
    }
