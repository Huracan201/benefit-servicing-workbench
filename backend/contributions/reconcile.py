"""Reconciliation sweeper — stuck-PROCESSING / stale-STARTED recovery.

The crash-recovery half of the two-phase payment contract (specs/08 §8.4,
specs/09 §9.4). If a driver dies after Phase 2 (the charge is persisted at the
processor) but before Phase 3 (finalize) commits, the contribution is stranded
``PROCESSING`` with a ``STARTED`` attempt and money that moved but was never
applied. This module re-queries the adapter with the attempt's **deterministic**
``processorIdempotencyKey`` — reconstructed from ``(contributionId,
attemptNumber)``, needing nothing the dead process held — and finalizes
idempotently:

    SUCCEEDED       -> Phase-3 success  (→ POSTED)
    FAILED          -> Phase-3 failure  (→ FAILED, or settle-then-cancel)
    NOT_FOUND       -> fenced verdict    (→ CANCELED if TERMINATED, else revert)
    INDETERMINATE   -> get_status itself raised: never guess about money —
                       bump reconcileAttempts; at MAX_SWEEPS raise
                       PAYMENT_STUCK_PROCESSING (CRITICAL) for a human

All finalize paths reuse the guarded, idempotent transactions in
:mod:`payments.service`, so a live driver and the sweeper can never both mutate
the same attempt (the guard keys on ``currentAttemptId`` + attempt ``STARTED``).

Phase 3 (Cloud Tasks / Scheduler) will schedule this; it is exposed as a plain
callable now so tests can exercise the crash-recovery path directly.
"""

from __future__ import annotations

from typing import Optional

from commands.base import (
    CommandContext,
    CommandError,
    NotFound,
    from_domain_error,
    transactional,
)
from common.enums import ContributionStatus, ExceptionType, PaymentAttemptStatus
from common.errors import DomainError

# Pinned constant (specs/21 §21.1): indeterminate sweeps before escalating.
MAX_SWEEPS = 6

_ENTITY_CONTRIBUTION = "scheduledContribution"


def _system_ctx(correlation_id: Optional[str] = None) -> CommandContext:
    """A SYSTEM actor context for sweeper-driven writes (specs/12 §12.5)."""
    kwargs = {
        "actor_id": "system:reconciliation",
        "actor_role": None,
        "actor_name": "Reconciliation Sweeper",
    }
    if correlation_id is not None:
        kwargs["correlation_id"] = correlation_id
    return CommandContext(**kwargs)


def _inflight_attempt_number(contribution: dict) -> Optional[int]:
    """The attempt number of the in-flight attempt, or ``None``.

    ``currentAttemptId`` points at the latest attempt (``attemptCount``); we only
    reconcile when there is a live pointer.
    """
    if not contribution.get("currentAttemptId"):
        return None
    count = contribution.get("attemptCount")
    if not isinstance(count, int) or count < 1:
        return None
    return count


def reconcile_contribution(
    contribution_id: str,
    *,
    ctx: Optional[CommandContext] = None,
    idempotency_key: Optional[str] = None,
    client=None,
    adapter=None,
) -> dict:
    """Reconcile one in-flight attempt against the processor (specs/09 §9.4).

    ``idempotency_key`` (optional) is completed on the finalize when the sweep is
    driven by a reclaimed command key (specs/08 §8.3); the scheduled sweeper
    passes none. Returns a result body describing what was found and done.
    """
    from payments import service
    from payments.adapter import SimulatedPaymentAdapter
    from repositories import attempts, contributions

    client = service._client_default(client)
    if adapter is None:
        adapter = SimulatedPaymentAdapter(client)
    if ctx is None:
        ctx = _system_ctx()

    try:
        contribution = contributions.get(client, contribution_id)
        if contribution is None:
            raise NotFound(f"contribution {contribution_id} not found")

        attempt_number = _inflight_attempt_number(contribution)
        attempt = (
            attempts.get(client, contribution_id, attempt_number)
            if attempt_number
            else None
        )
        if attempt is None or attempt.get("status") != str(PaymentAttemptStatus.STARTED):
            # Nothing in flight — already finalized (idempotent no-op). If we were
            # driven by a reclaimed command key (RECLAIM path, specs/08 §8.3), that
            # key is still PENDING and no finalize ran here to complete it — do so
            # from current state, or it wedges PENDING forever. The scheduled
            # sweeper passes no key and takes the plain no-op return below.
            if idempotency_key:
                return _complete_reclaimed_key(
                    client, contribution_id, idempotency_key,
                    reason="no in-flight STARTED attempt",
                )
            return {
                "contributionId": contribution_id,
                "status": contribution.get("status"),
                "reconciled": False,
                "reason": "no in-flight STARTED attempt",
            }

        processor_key = attempt["processorIdempotencyKey"]

        # -- Phase 2 (query, NO transaction) --------------------------------
        try:
            status_result = adapter.get_status(processor_idempotency_key=processor_key)
        except Exception as exc:  # noqa: BLE001 — any raise == INDETERMINATE (§9.4)
            return _handle_indeterminate(
                client, ctx, contribution, attempt_number, str(exc)
            )

        # -- Phase 3 (finalize, transaction) --------------------------------
        if status_result.status == "SUCCEEDED":
            run = transactional(client)(service.finalize_success)
            result = run(
                client=client,
                ctx=ctx,
                contribution_id=contribution_id,
                attempt_number=attempt_number,
                processor_reference=status_result.processor_reference,
                idempotency_key=idempotency_key,
                reconciled=True,
                seq_start=1,
            )
        elif status_result.status == "FAILED":
            run = transactional(client)(service.finalize_failure)
            result = run(
                client=client,
                ctx=ctx,
                contribution_id=contribution_id,
                attempt_number=attempt_number,
                failure_code=status_result.failure_code,
                failure_reason=status_result.failure_reason,
                idempotency_key=idempotency_key,
                reconciled=True,
                seq_start=1,
            )
        else:  # NOT_FOUND (fenced)
            run = transactional(client)(service.finalize_not_submitted)
            result = run(
                client=client,
                ctx=ctx,
                contribution_id=contribution_id,
                attempt_number=attempt_number,
                idempotency_key=idempotency_key,
                seq_start=1,
            )

        if result is None:
            # A concurrent finalizer already resolved it — our finalize returned
            # None (guard superseded) WITHOUT completing the key. If we hold a
            # reclaimed command key, complete it from current state so it doesn't
            # wedge PENDING (specs/08 §8.3); the superseded finalize never reached
            # its idempotency.complete, so this cannot double-complete.
            if idempotency_key:
                return _complete_reclaimed_key(
                    client, contribution_id, idempotency_key,
                    reason="finalize superseded by concurrent sweeper",
                )
            current = contributions.get(client, contribution_id) or {}
            result = {
                "contributionId": contribution_id,
                "status": current.get("status"),
                "reconciled": True,
                "superseded": True,
            }
        return result
    except CommandError:
        raise
    except DomainError as exc:
        raise from_domain_error(exc)


def _complete_reclaimed_key(
    client, contribution_id: str, idempotency_key: str, *, reason: str
) -> dict:
    """Complete a RECLAIM-driven command idempotency key from the contribution's
    *current* state, for the reconcile paths that skip the finalize (no in-flight
    STARTED attempt, or a finalize superseded by a concurrent sweeper).

    Without this, a key reclaimed to PENDING in Phase 1 (specs/08 §8.3) that never
    reaches a completing finalize would stay PENDING forever. Mirrors
    ``payments.service._complete_with_current_state``: read the status in a small
    transaction, then ``idempotency.complete``. Only invoked on the skip branches
    — where no finalize completed the key — so it never double-completes.
    """
    from payments import service
    from repositories import contributions

    def _run(txn):
        current = service._get_in_txn(txn, contributions.ref(client, contribution_id))
        result = {
            "contributionId": contribution_id,
            "status": (current or {}).get("status"),
            "attemptId": (current or {}).get("currentAttemptId"),
            "reconciled": True,
            "reason": reason,
        }
        service._complete_idempotency(txn, idempotency_key, result, client)
        return result

    return transactional(client)(_run)()


def _handle_indeterminate(
    client, ctx: CommandContext, contribution: dict, attempt_number: int, error: str
) -> dict:
    """INDETERMINATE branch (specs/09 §9.4): bump ``reconcileAttempts``; escalate
    to ``PAYMENT_STUCK_PROCESSING`` (CRITICAL) at ``MAX_SWEEPS``. Never guesses
    about money and never completes the idempotency key — the next sweep retries.
    """
    from payments import service
    from servicing import events
    from exceptions import service as exceptions_service
    from repositories import attempts, contributions, loans, stamp_update

    contribution_id = contribution.get("id") or contribution.get("contributionId")

    def _run(txn):
        # -- reads (all before any write — Firestore ordering rule) ---------
        # Re-read the contribution IN-TXN. The outer ``contribution`` is a
        # pre-transaction snapshot and may be stale: a concurrent finalize or a
        # prior escalation could have moved ``currentExceptionId``. The
        # already-counted / openExceptionCount decisions below must key off the
        # fresh in-txn state, not the param. Fall back to the snapshot if the
        # in-txn read comes back empty.
        fresh = service._get_in_txn(txn, contributions.ref(client, contribution_id))
        if fresh is None:
            fresh = contribution
        loan_id = fresh.get("loanId")

        attempt = service._get_in_txn(
            txn, attempts.ref(client, contribution_id, attempt_number)
        )
        if attempt is None or attempt.get("status") != str(PaymentAttemptStatus.STARTED):
            return {
                "contributionId": contribution_id,
                "status": fresh.get("status"),
                "reconciled": False,
                "indeterminate": True,
                "reason": "attempt no longer STARTED",
            }
        new_count = int(attempt.get("reconcileAttempts", 0)) + 1
        escalate = new_count >= MAX_SWEEPS

        loan = None
        if escalate:
            # Read the loan (before writes) so we can bump openExceptionCount.
            loan = (
                service._get_in_txn(txn, loans.ref(client, loan_id))
                if loan_id
                else None
            )
            exc_id = exceptions_service.upsert(
                txn,
                client,
                exception_type=ExceptionType.PAYMENT_STUCK_PROCESSING,
                entity_type=_ENTITY_CONTRIBUTION,
                entity_id=contribution_id,
                summary=f"Payment stuck PROCESSING for {contribution_id} after {new_count} sweeps",
                details=f"get_status indeterminate: {error}",
                loan_id=loan_id,
                borrower_id=fresh.get("borrowerId"),
                borrower_name=fresh.get("borrowerName"),
                employer_id=fresh.get("employerId"),
                employer_name=fresh.get("employerName"),
            )

        # Bump the sweep counter (only write on the attempt for the common case).
        txn.update(
            attempts.ref(client, contribution_id, attempt_number),
            {"reconcileAttempts": new_count},
        )

        if escalate:
            # The stuck exception has its OWN deterministic id
            # ({cid}__PAYMENT_STUCK_PROCESSING), distinct from any prior
            # {cid}__PAYMENT_FAILED. It is therefore a genuinely NEW open row and
            # must bump openExceptionCount — unless a PRIOR escalation already
            # created it and pointed currentExceptionId at it (idempotent re-sweep;
            # upsert only bumps occurrenceCount then). Counting it as already-open
            # whenever *any* currentExceptionId was set (the old bug) orphaned the
            # still-OPEN PAYMENT_FAILED and undercounted (2 open, count said 1).
            already_counted = fresh.get("currentExceptionId") == exc_id
            # Point at the stuck exception (most actionable); the prior
            # PAYMENT_FAILED row stays OPEN and is still reflected in the count.
            contrib_update = {"currentExceptionId": exc_id}
            stamp_update(contrib_update, ctx.actor_id)
            txn.update(contributions.ref(client, contribution_id), contrib_update)
            if loan is not None and not already_counted:
                loan_update = {
                    "openExceptionCount": int(loan.get("openExceptionCount", 0)) + 1
                }
                stamp_update(loan_update, ctx.actor_id)
                txn.update(loans.ref(client, loan_id), loan_update)

        events.append(
            txn,
            event_type="PAYMENT_RECONCILED",
            entity_type=_ENTITY_CONTRIBUTION,
            entity_id=contribution_id,
            actor_id=ctx.actor_id,
            actor_role=ctx.actor_role,
            actor_name=ctx.actor_name,
            correlation_id=ctx.correlation_id,
            sequence=1,
            metadata={
                "finding": "INDETERMINATE",
                "reconcileAttempts": new_count,
                "escalated": escalate,
                "attemptNumber": attempt_number,
            },
            loan_id=loan_id,
            borrower_id=fresh.get("borrowerId"),
            employer_id=fresh.get("employerId"),
            benefit_agreement_id=fresh.get("benefitAgreementId"),
        )

        return {
            "contributionId": contribution_id,
            "status": str(ContributionStatus.PROCESSING),
            "reconciled": False,
            "indeterminate": True,
            "reconcileAttempts": new_count,
            "escalated": escalate,
        }

    return transactional(client)(_run)()
