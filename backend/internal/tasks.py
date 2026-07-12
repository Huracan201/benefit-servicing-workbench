"""internal.tasks — Cloud Tasks handler bodies (specs/14 §14.3).

Thin ``fn(payload: dict, ctx) -> dict`` adapters over the Phase-3 domain
callables, registered in :mod:`internal.enqueue` at import time. Each is invoked
identically by the inline seam (``internal.enqueue._enqueue_inline``) and the
cloud view (``internal.views.task_handler``), so CI(inline) mirrors prod(cloud).

Two adapters carry logic beyond "pull ids, call the callable":

* :func:`process_contribution_task` — derives the command idempotency key the
  scheduler-driven process needs (the ``{"contributionId"}`` payload carries no
  key): a fresh per-cycle key for a ``SCHEDULED``/``RETRY_PENDING`` contribution,
  or — for a redelivery of an already in-flight (``PROCESSING``) attempt — the
  in-flight attempt's stored key so ``process_contribution`` **reclaims** it
  (get_status → finalize) rather than starting a second charge (specs/08 §8.3).
  A **declined** payment is a successful command (its result carries
  ``status == FAILED``) rendered ``200``, not a failure; and a crash *after*
  Phase 1 (contribution left ``PROCESSING``) is **not** dead-lettered — the
  ``reconcile-stuck-payments`` sweeper recovers it by the attempt's deterministic
  key (specs/09 §9.4).
* :func:`generate_schedule_task` — on a **terminal** generate failure (an
  effective dead-letter) rolls the agreement ``ACTIVATING → PENDING`` so it is
  not wedged mid-activation (specs/14 §14.5, specs/10 §10.1), then re-raises so
  the views envelope records ``TASK_FAILED``. A *transient* failure is re-raised
  unchanged (Cloud Tasks retries; ``reap-expired-leases`` re-drives on exhaustion).

**Completion protocol (step 4, specs/08 §8.3).** The async tail callables
(``generate_schedule`` / ``cancel_future_contributions`` / ``shift_schedule``) are
pure — they do NOT own the idempotency key. After running the tail, the adapter
**completes** the ``idempotencyKey`` named in the payload (idempotently: a no-op
on an already-COMPLETED key or when no key is present, e.g. a reaper re-drive). The
value stored is the enqueuing COMMAND's response body, threaded through the payload
as ``commandResult`` (NOT the tail's side-effect summary), so a same-key client
replay / cloud poll returns the identical body the first caller received and inline
mirrors cloud (specs/08 §8.2); a reaper re-drive, which has no command body, falls
back to the tail summary. ``generate_schedule`` is the exception: its finalized
summary IS shaped as ``activate_benefit``'s response, so the adapter completes the
ACTIVATE key with that directly; a *halted* generate (the agreement left
``ACTIVATING`` mid-run) instead drives the key ``FAILED`` so it is never left
wedged PENDING (which would loop/perma-skip the reaper). ``process-contribution`` /
``reconcile-contribution`` complete their own key internally, so their adapters
add no completion.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("bsw.internal")

# Bounded batch for the dead-letter rollback's partial-contribution cleanup. Each
# item is a single delete, so 100/txn stays far under Firestore's 500-writes cap.
_ROLLBACK_DELETE_BATCH = 100


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
def _get_in_txn(txn: Any, ref: Any) -> Optional[dict]:
    """Read a single ``DocumentReference`` inside ``txn`` as dict-with-id/None."""
    got = txn.get(ref)
    snap = got if hasattr(got, "exists") else next(iter(got), None)
    if snap is None or not getattr(snap, "exists", False):
        return None
    data = snap.to_dict() or {}
    data["id"] = snap.id
    return data


def _complete_key(client: Any, key: Optional[str], result: dict) -> None:
    """Complete the ``idempotencyKeys/{key}`` record with ``result`` (step 4).

    Idempotent and self-contained: a missing key (reaper re-drive without one) or
    an already-COMPLETED record (redelivery) is a no-op, so it never
    double-completes and never raises on a key the tail was driven without.
    """
    if not key:
        return
    from commands.base import transactional
    from common.enums import IdempotencyStatus
    from idempotency import service as idempotency
    from repositories import idempotency_keys

    def _run(txn: Any) -> bool:
        rec = _get_in_txn(txn, idempotency_keys.ref(client, key))
        if rec is None:
            return False  # no command key to complete (e.g. reaper re-drive)
        if rec.get("status") == str(IdempotencyStatus.COMPLETED):
            return False  # already completed (redelivery) — idempotent no-op
        idempotency.complete(txn, key, result, client=client)
        return True

    if transactional(client)(_run)():
        logger.info("async tail completed idempotency key %s", key)


def _fail_key(client: Any, key: Optional[str], reason: str) -> None:
    """Drive a still-``PENDING`` ``idempotencyKeys/{key}`` record to ``FAILED``.

    Idempotent and self-contained (mirrors :func:`_complete_key`): a missing key,
    or a record already resolved (``COMPLETED``/``FAILED``), is a no-op. ``FAILED``
    is a *retryable terminal* state (specs/08 §8.2): it unwedges a key that would
    otherwise stay ``PENDING`` forever, so a same-key client retry can begin a
    fresh attempt and the ``PENDING``-only lease reaper stops re-driving it.
    """
    if not key:
        return
    from commands.base import transactional
    from common.enums import IdempotencyStatus
    from idempotency import service as idempotency
    from repositories import idempotency_keys

    def _run(txn: Any) -> bool:
        rec = _get_in_txn(txn, idempotency_keys.ref(client, key))
        if rec is None:
            return False  # no command key to fail (e.g. reaper re-drive)
        if rec.get("status") != str(IdempotencyStatus.PENDING):
            return False  # already resolved (COMPLETED/FAILED) — idempotent no-op
        idempotency.fail(txn, key, reason=reason, client=client)
        return True

    if transactional(client)(_run)():
        logger.info("async tail failed idempotency key %s (%s)", key, reason)


def _is_retryable(exc: Exception) -> bool:
    """Mirror the views envelope's retryable classification (specs/14 §14.5).

    Transient conflicts (lease held, optimistic-concurrency) and 5xx-mapped
    errors can succeed on redelivery → retryable; every other business/validation
    outcome is terminal.
    """
    from commands.base import OperationInProgress, StaleWrite

    return isinstance(exc, (OperationInProgress, StaleWrite)) or (
        getattr(exc, "http_status", 400) >= 500
    )


# --------------------------------------------------------------------------- #
# generate-schedule
# --------------------------------------------------------------------------- #
def generate_schedule_task(payload: dict, ctx) -> dict:
    """Run the resumable schedule generation, completing the ACTIVATE key.

    On a terminal failure, rolls ``ACTIVATING → PENDING`` before re-raising
    (specs/14 §14.5). The completion uses ``generate_schedule``'s finalized
    summary, shaped like ``activate_benefit``'s result, so an ACTIVATE-key replay
    returns the now-ACTIVE agreement.
    """
    from common.errors import DomainError
    from common.firestore import get_client
    from commands.base import CommandError
    from contributions.generate import generate_schedule

    agreement_id = payload["agreementId"]
    idem_key = payload.get("idempotencyKey")
    client = get_client()

    try:
        result = generate_schedule(agreement_id, ctx, client=client)
    except (CommandError, DomainError) as exc:
        if _is_retryable(exc):
            raise  # transient — Cloud Tasks retries; reaper re-drives on exhaustion
        _rollback_activating(client, agreement_id, ctx)
        raise

    # Complete the ACTIVATE key on a finalized (ACTIVE) outcome. On a HALTED run
    # (agreement left ACTIVATING mid-generation, e.g. a concurrent terminate) the
    # activation did not finalize, so the key must NOT store a non-activation as
    # the activate result — but it must not be left PENDING either (that wedges the
    # key forever and makes the reaper loop/perma-skip). Drive it to FAILED: a
    # same-key client retry re-runs activate (now correctly 409-ing on the
    # terminated agreement) and the PENDING-only reaper stops re-driving. The
    # partial contributions are cancelled by the terminate cascade separately —
    # do NOT double-cancel here (specs/08 §8.3, specs/14 §14.5).
    if result.get("finalized"):
        _complete_key(client, idem_key, result)
    elif result.get("halted"):
        _fail_key(client, idem_key, "activation halted: agreement left ACTIVATING")
    return result


def _rollback_activating(client: Any, agreement_id: str, ctx) -> None:
    """Roll a still-``ACTIVATING`` agreement back to a CLEAN ``PENDING`` (best-effort).

    Status-guarded and idempotent — only touches an agreement still ``ACTIVATING``
    — and never lets its own failure mask the re-raised terminal error.

    Crucially, this DELETES any partially-created contributions before resetting
    the witness, so ``PENDING`` is a *true* clean slate. Leaving them behind while
    resetting ``installmentsGenerated = 0`` would make the witness lie:
    ``activate_benefit`` itself re-writes ``installmentsGenerated = 0`` every time
    it re-enters ``PENDING → ACTIVATING``, so re-activation would resume generation
    from 0 and its ``txn.create`` create-precondition would fail on the orphaned
    installment 1 (deterministic id ``{agreementId}__001`` already exists) — wedging
    re-activation. On the MVP single-atomic path (term ≤ ``SYNC_GENERATION_MAX``)
    the failing generate transaction aborted atomically, so nothing was created and
    the delete step is a no-op; it only does work on the resumable multi-batch path
    (specs/10 §10.1, specs/14 §14.5).
    """
    from commands.base import transactional
    from common.enums import BenefitStatus
    from common.state_machines import assert_transition
    from repositories import agreements, stamp_update

    # 1) Clear the partial schedule so the rolled-back PENDING is contribution-free.
    _delete_partial_contributions(client, agreement_id)

    # 2) Flip ACTIVATING -> PENDING with a truthful (now clean) zero witness.
    def _run(txn: Any) -> bool:
        agreement = _get_in_txn(txn, agreements.ref(client, agreement_id))
        if agreement is None or agreement.get("status") != str(BenefitStatus.ACTIVATING):
            return False
        assert_transition("benefit", agreement.get("status"), BenefitStatus.PENDING)
        update = {
            "status": str(BenefitStatus.PENDING),
            "acceptingPayments": False,
            "scheduleGenerated": False,
            "installmentsGenerated": 0,
        }
        stamp_update(update, ctx.actor_id)
        txn.update(agreements.ref(client, agreement_id), update)
        return True

    try:
        if transactional(client)(_run)():
            logger.warning(
                "generate-schedule dead-letter: rolled agreement %s ACTIVATING->PENDING",
                agreement_id,
            )
    except Exception:  # noqa: BLE001 — never mask the terminal error being re-raised
        logger.exception(
            "generate-schedule: failed to roll agreement %s ACTIVATING->PENDING",
            agreement_id,
        )


def _delete_partial_contributions(client: Any, agreement_id: str) -> None:
    """Delete an agreement's already-created ``SCHEDULED`` contributions (bounded).

    Clears the partial schedule a crashed resumable generation left behind so the
    rolled-back ``PENDING`` agreement is a clean slate. Only ``SCHEDULED`` rows are
    deleted — during ``ACTIVATING`` ``acceptingPayments`` is false, so nothing can
    have been charged; each row is re-read + status-guarded inside its transaction,
    so a row a concurrent terminate cascade already moved to ``CANCELED`` is left to
    it (staleness can only skip, never mis-delete). Bounded batches keep every
    transaction well under Firestore's 500-writes cap. Best-effort: a failure here
    must never mask the terminal generate error being re-raised.
    """
    from commands.base import transactional
    from common.enums import ContributionStatus
    from repositories import contributions

    try:
        schedule = contributions.list_for_agreement(client, agreement_id)
    except Exception:  # noqa: BLE001 — best-effort cleanup; never mask the caller's error
        logger.exception(
            "generate-schedule rollback: failed to list contributions for %s",
            agreement_id,
        )
        return

    scheduled_ids = [
        c["id"]
        for c in schedule
        if c.get("status") == str(ContributionStatus.SCHEDULED)
    ]

    for start in range(0, len(scheduled_ids), _ROLLBACK_DELETE_BATCH):
        batch_ids = scheduled_ids[start : start + _ROLLBACK_DELETE_BATCH]

        def _run(txn: Any, batch_ids=batch_ids) -> int:
            # Read-before-write: re-read each row in-txn and delete only if it is
            # STILL SCHEDULED (never fight a concurrent terminate cascade, and never
            # delete a POSTED/PROCESSING row — which cannot exist mid-ACTIVATING).
            to_delete = [
                cid
                for cid in batch_ids
                if (doc := _get_in_txn(txn, contributions.ref(client, cid))) is not None
                and doc.get("status") == str(ContributionStatus.SCHEDULED)
            ]
            for cid in to_delete:
                txn.delete(contributions.ref(client, cid))
            return len(to_delete)

        try:
            deleted = transactional(client)(_run)()
            if deleted:
                logger.warning(
                    "generate-schedule rollback: deleted %s partial contribution(s) "
                    "for agreement %s",
                    deleted, agreement_id,
                )
        except Exception:  # noqa: BLE001 — best-effort; never mask the terminal error
            logger.exception(
                "generate-schedule rollback: failed to delete partial contributions "
                "for agreement %s",
                agreement_id,
            )


# --------------------------------------------------------------------------- #
# process-contribution
# --------------------------------------------------------------------------- #
def process_contribution_task(payload: dict, ctx) -> dict:
    """Drive the two-phase payment for one contribution (specs/09 §9.1).

    Derives the command idempotency key (see module docstring), then runs
    ``process_contribution``. A declined payment returns its ``status == FAILED``
    result (a 200); a post-Phase-1 crash is deferred to the sweeper, never
    dead-lettered.
    """
    from dataclasses import replace

    from commands.base import CommandError, request_hash
    from common.enums import ContributionStatus
    from common.firestore import get_client
    from payments.service import process_contribution
    from repositories import attempts, contributions

    contribution_id = payload["contributionId"]
    client = get_client()

    contribution = contributions.get(client, contribution_id)
    if contribution is None:
        return {
            "contributionId": contribution_id,
            "processed": False,
            "reason": "contribution not found",
        }

    status = contribution.get("status")
    attempt_count = int(contribution.get("attemptCount", 0) or 0)

    if status in (
        str(ContributionStatus.SCHEDULED),
        str(ContributionStatus.RETRY_PENDING),
    ):
        # A fresh processing cycle: a per-cycle deterministic key. Two concurrent
        # deliveries of the same due item read the same attemptCount and derive
        # the SAME key, so idempotency's create-precondition fences them to one
        # charge; a later cycle (post-retry) has a higher attemptCount → new key.
        idem_key = f"proc_{contribution_id}_att_{attempt_count + 1:03d}"
    elif status == str(ContributionStatus.PROCESSING) and contribution.get(
        "currentAttemptId"
    ):
        # A redelivery/recovery of an in-flight attempt: reuse its stored command
        # key so Phase 1 reclaims (get_status → finalize) instead of charging
        # again (specs/08 §8.3, §8.4).
        attempt = attempts.get(client, contribution_id, attempt_count)
        idem_key = (attempt or {}).get("commandIdempotencyKey") or (
            f"proc_{contribution_id}_att_{attempt_count:03d}"
        )
    else:
        # Terminal (POSTED/CANCELED/FAILED) or otherwise not processable — an
        # idempotent no-op, never a re-charge and never a dead-letter.
        return {
            "contributionId": contribution_id,
            "status": status,
            "processed": False,
            "reason": f"not in a processable state ({status})",
        }

    proc_ctx = replace(
        ctx,
        idempotency_key=idem_key,
        request_hash=request_hash(
            "POST", f"/contributions/{contribution_id}/process", None
        ),
    )

    try:
        return process_contribution(contribution_id, proc_ctx, client=client)
    except CommandError:
        # If Phase 1 already committed (contribution now PROCESSING), a charge may
        # be in flight: do NOT dead-letter — the reconcile-stuck-payments sweeper
        # recovers it by the attempt's deterministic key (specs/09 §9.4). Only a
        # pre-charge failure (still SCHEDULED/RETRY_PENDING) is re-raised so Cloud
        # Tasks retries it.
        current = contributions.get(client, contribution_id)
        if current and current.get("status") == str(ContributionStatus.PROCESSING):
            logger.info(
                "process-contribution %s left in-flight; deferring to sweeper",
                contribution_id,
            )
            return {
                "contributionId": contribution_id,
                "status": str(ContributionStatus.PROCESSING),
                "processed": False,
                "deferredToSweeper": True,
                "reason": "left in-flight; reconciliation sweeper will finalize",
            }
        raise


# --------------------------------------------------------------------------- #
# reconcile-contribution
# --------------------------------------------------------------------------- #
def reconcile_contribution_task(payload: dict, ctx) -> dict:
    """Reconcile one in-flight attempt against the processor (specs/09 §9.4).

    Scheduler-driven, so it passes no ``idempotency_key`` — ``reconcile_contribution``
    completes no command key; it queries ``get_status`` (never ``charge``) and
    finalizes idempotently.
    """
    from common.firestore import get_client
    from contributions.reconcile import reconcile_contribution

    contribution_id = payload["contributionId"]
    return reconcile_contribution(contribution_id, ctx=ctx, client=get_client())


# --------------------------------------------------------------------------- #
# cancel-future-contributions
# --------------------------------------------------------------------------- #
def cancel_future_contributions_task(payload: dict, ctx) -> dict:
    """Cancel every future contribution of an agreement, completing the key.

    The tail is idempotent (status-guarded per-item transitions). The adapter
    completes the terminate/employment idempotency key with the enqueuing COMMAND's
    response body (``commandResult`` in the payload) — the body the first caller
    received — NOT the tail's ``{canceled, skipped_processing}`` summary, so a
    same-key replay / cloud poll returns the identical body and inline mirrors cloud
    (specs/08 §8.2). A reaper re-drive carries no ``commandResult`` (the original
    body is unavailable to it), so it falls back to the tail summary. Returns
    whatever it completed the key with, so ``enqueue()``'s inline return equals the
    command body.
    """
    from common.firestore import get_client
    from contributions.lifecycle import cancel_future_contributions

    agreement_id = payload["agreementId"]
    reason = payload.get("reason") or "benefit terminated"
    idem_key = payload.get("idempotencyKey")
    client = get_client()

    tail_result = cancel_future_contributions(
        client, agreement_id=agreement_id, ctx=ctx, reason=reason
    )
    command_result = payload.get("commandResult")
    completion = command_result if command_result is not None else tail_result
    _complete_key(client, idem_key, completion)
    return completion


# --------------------------------------------------------------------------- #
# shift-schedule
# --------------------------------------------------------------------------- #
def shift_schedule_task(payload: dict, ctx) -> dict:
    """Re-date the remaining schedule of a resumed benefit, completing the key.

    ``suspendedFrom`` / ``resumedAt`` are optional (only the ``SCHEDULE_SHIFTED``
    event's window metadata); the shift amount itself comes from the agreement's
    cumulative ``scheduleShiftMonths``, so a reaper re-drive without the dates is
    still correct. The adapter completes the resume/employment idempotency key with
    the enqueuing COMMAND's response body (``commandResult`` in the payload) — the
    body the first caller received — NOT the tail's ``{shiftMonths,
    installmentsShifted, ...}`` summary, so a same-key replay / cloud poll returns
    the identical body and inline mirrors cloud (specs/08 §8.2). A reaper re-drive
    carries no ``commandResult``, so it falls back to the tail summary. Returns
    whatever it completed the key with, so ``enqueue()``'s inline return equals the
    command body.
    """
    from common.firestore import get_client
    from benefits.shift import shift_schedule

    agreement_id = payload["agreementId"]
    idem_key = payload.get("idempotencyKey")
    client = get_client()

    tail_result = shift_schedule(
        client,
        agreement_id=agreement_id,
        ctx=ctx,
        suspended_from=payload.get("suspendedFrom"),
        resumed_at=payload.get("resumedAt"),
    )
    command_result = payload.get("commandResult")
    completion = command_result if command_result is not None else tail_result
    _complete_key(client, idem_key, completion)
    return completion
