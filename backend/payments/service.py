"""Payment-processing domain commands — the two-phase contribution workflow.

This module is the heart of the Phase-2 domain command layer (specs/19 §19.2)
for money movement. It implements:

* :func:`process_contribution` — the specs/09 §9.1 two-phase process:
  **Phase 1** (transaction) opens the idempotency record, transitions the
  contribution ``SCHEDULED``/``RETRY_PENDING`` → ``PROCESSING``, creates the
  ``STARTED`` attempt and writes the ``PAYMENT_PROCESSING`` event; **Phase 2**
  calls the payment adapter's ``charge`` **outside** any transaction; **Phase 3**
  (transaction) finalizes success → ``POSTED`` (applying balances) or failure →
  ``FAILED`` (or settle-then-cancel on a ``TERMINATED`` agreement).
* :func:`retry_contribution` — the specs/09 §9.2 retry: ``FAILED`` →
  ``RETRY_PENDING`` then an inline re-process (Phase-3 ``TASK_EXECUTION_MODE``).

It also exposes the two shared **finalize** transactions
(:func:`finalize_success`, :func:`finalize_failure`) and the
``NOT_SUBMITTED`` finalize (:func:`finalize_not_submitted`) so the
reconciliation sweeper (:mod:`contributions.reconcile`) can drive the *same*
guarded, idempotent Phase-3 logic when it recovers a crashed in-flight attempt
(specs/08 §8.4, specs/09 §9.4).

**Transaction discipline (specs/08 §8.1).** Firestore requires all reads before
all writes; every handler here reads contribution/attempt/benefit/loan (and, for
the success look-ahead, the next scheduled installment) *before* mutating, and
the payment adapter call is never inside a transaction. ``google.cloud`` is
imported lazily so the module ``py_compile``s in the offline sandbox.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Optional

from commands.base import (
    BenefitNotAcceptingPayments,
    CommandContext,
    CommandError,
    IdempotencyKeyReused,
    LEASE_TTL_SECONDS,
    NotFound,
    OperationInProgress,
    RETRY_AFTER_IN_PROGRESS,
    Unprocessable,
    from_domain_error,
    request_hash,
    transactional,
)
from common.enums import (
    BenefitStatus,
    ContributionStatus,
    EmploymentStatus,
    LoanStatus,
    PaymentAttemptStatus,
    PaymentFailureCode,
)
from common.errors import DomainError
from common.ids import attempt_id as _attempt_id
from common.ids import processor_key as _processor_key
from common.money import cap_posted
from common.periods import period_label
from common.state_machines import assert_transition
from common import invariants

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
CURRENCY = "USD"
OPERATION_PROCESS = "PROCESS_CONTRIBUTION"
OPERATION_RETRY = "RETRY_CONTRIBUTION"

# Entity-type tags used on servicing events / exceptions.
_ENTITY_CONTRIBUTION = "scheduledContribution"
_ENTITY_LOAN = "loan"
_ENTITY_AGREEMENT = "benefitAgreement"

# Loan statuses that permit processing (specs/09 §9.1). PAID_OFF / CLOSED block;
# DELINQUENT does NOT block (paying down a delinquent loan is desirable).
_PROCESSABLE_LOAN_STATUSES = frozenset(
    {str(LoanStatus.ACTIVE), str(LoanStatus.DELINQUENT)}
)


# --------------------------------------------------------------------------- #
# Lazy firestore helpers
# --------------------------------------------------------------------------- #
def _server_ts():
    from google.cloud import firestore  # lazy — package optional at import time

    return firestore.SERVER_TIMESTAMP


def _client_default(client):
    if client is not None:
        return client
    from common.firestore import get_client

    return get_client()


def _adapter_default(adapter, client):
    if adapter is not None:
        return adapter
    from payments.adapter import SimulatedPaymentAdapter

    return SimulatedPaymentAdapter(client)


def _get_in_txn(txn, ref) -> Optional[dict]:
    """Read a single ``DocumentReference`` inside ``txn`` as dict-with-id/None."""
    got = txn.get(ref)
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


def _next_scheduled_in_txn(txn, client, agreement_id: str) -> Optional[dict]:
    """Lowest-``installmentNumber`` still-``SCHEDULED`` contribution, in the txn
    read set (specs/09 §9.1 look-ahead must be read before writes)."""
    from repositories import refs

    query = (
        client.collection(refs.SCHEDULED_CONTRIBUTIONS)
        .where(filter=refs.field_filter("benefitAgreementId", "==", agreement_id))
        .where(filter=refs.field_filter("status", "==", str(ContributionStatus.SCHEDULED)))
        .order_by("installmentNumber")
        .limit(1)
    )
    for snap in query.get(transaction=txn):
        data = snap.to_dict() or {}
        data["id"] = snap.id
        return data
    return None


class _Seq:
    """Monotonic (within a correlationId) event-sequence counter, retry-safe.

    Instantiated fresh at the top of every transaction handler so a Firestore
    contention retry re-runs the handler and re-derives identical sequence
    numbers (specs/04 §4.9, specs/08 §8.5).
    """

    def __init__(self, start: int = 1) -> None:
        self._n = start

    def __call__(self) -> int:
        n = self._n
        self._n += 1
        return n


def _guard_inflight(contribution, attempt, contribution_id, attempt_number) -> bool:
    """The specs/09 §9.1 finalize guard (identical on success and failure).

    True iff the contribution is still ``PROCESSING`` **with this
    ``currentAttemptId``** and the attempt is still ``STARTED``. A stale driver
    whose attempt was superseded fails this guard and must abort without writing.
    """
    if contribution is None or attempt is None:
        return False
    if contribution.get("status") != str(ContributionStatus.PROCESSING):
        return False
    if contribution.get("currentAttemptId") != _attempt_id(contribution_id, attempt_number):
        return False
    if attempt.get("status") != str(PaymentAttemptStatus.STARTED):
        return False
    return True


def _event_common(contribution: dict) -> dict:
    """The denormalized entity pointers every payment event carries."""
    return {
        "loan_id": contribution.get("loanId"),
        "borrower_id": contribution.get("borrowerId"),
        "employer_id": contribution.get("employerId"),
        "benefit_agreement_id": contribution.get("benefitAgreementId"),
    }


# --------------------------------------------------------------------------- #
# Phase 3 — shared finalize transactions (used by process AND reconcile)
# --------------------------------------------------------------------------- #
def finalize_success(
    txn,
    *,
    client,
    ctx: CommandContext,
    contribution_id: str,
    attempt_number: int,
    processor_reference: Optional[str],
    idempotency_key: Optional[str] = None,
    reconciled: bool = False,
    seq_start: int = 1,
) -> Optional[dict]:
    """Finalize a successful charge → ``POSTED`` (specs/09 §9.1 success branch).

    Returns the result body, or ``None`` when the finalize guard fails (a
    superseded stale driver — writes nothing, per specs/08 §8.4).
    """
    from servicing import events
    from exceptions import service as exceptions_service
    from repositories import agreements, attempts, contributions, loans, refs, stamp_update

    # -- reads (all before any write) ---------------------------------------
    contribution = _get_in_txn(txn, contributions.ref(client, contribution_id))
    attempt = _get_in_txn(txn, attempts.ref(client, contribution_id, attempt_number))
    if not _guard_inflight(contribution, attempt, contribution_id, attempt_number):
        return None
    agreement_id = contribution["benefitAgreementId"]
    loan_id = contribution["loanId"]
    benefit = _get_in_txn(txn, agreements.ref(client, agreement_id))
    loan = _get_in_txn(txn, loans.ref(client, loan_id))
    if benefit is None or loan is None:
        raise NotFound("loan or benefit agreement missing for contribution")
    next_scheduled = _next_scheduled_in_txn(txn, client, agreement_id)

    # -- compute + assert invariants (specs/07 §7.2, §7.4) ------------------
    scheduled = int(contribution["scheduledAmountCents"])
    loan_balance = int(loan["currentBalanceCents"])
    remaining = int(benefit["remainingCommitmentCents"])
    total = int(benefit["totalCommitmentCents"])
    posted = cap_posted(scheduled, loan_balance, remaining)
    new_balance = loan_balance - posted
    new_paid = int(benefit["amountPaidCents"]) + posted
    new_remaining = total - new_paid

    invariants.check_posted_within_caps(posted, scheduled, loan_balance, remaining)
    invariants.check_loan_balance_non_negative(new_balance)
    invariants.check_amount_paid_within_commitment(new_paid, total)
    invariants.check_remaining_commitment_consistent(new_remaining, total, new_paid)

    loan_paid_off = new_balance == 0
    benefit_completed = new_remaining == 0 or loan_paid_off
    # Only *complete* the benefit from a legal source state (specs/06): there is
    # no TERMINATED -> COMPLETED edge, so a reconciled payoff on a terminated
    # agreement must still move the loan balance / mark the loan PAID_OFF but
    # leave the agreement TERMINATED (never assert an illegal benefit transition).
    complete_benefit = benefit_completed and benefit.get("status") in (
        str(BenefitStatus.ACTIVE),
        str(BenefitStatus.SUSPENDED),
    )

    # Loan payoff / full funding satisfies the commitment, so any remaining
    # SCHEDULED installments will never fire and must be CANCELED (specs/07 §7.4).
    # Read them now — the LAST read, still before any write (specs/08 §8.1) —
    # mirroring _next_scheduled_in_txn's txn-query but WITHOUT .limit(1). Excludes
    # the current contribution (being POSTED here; it is PROCESSING, not
    # SCHEDULED, so the status filter already skips it — belt-and-suspenders).
    to_cancel: list[dict] = []
    if benefit_completed:
        cancel_query = (
            client.collection(refs.SCHEDULED_CONTRIBUTIONS)
            .where(filter=refs.field_filter("benefitAgreementId", "==", agreement_id))
            .where(filter=refs.field_filter("status", "==", str(ContributionStatus.SCHEDULED)))
            .order_by("installmentNumber")
        )
        for snap in cancel_query.get(transaction=txn):
            data = snap.to_dict() or {}
            data["id"] = snap.id
            if data["id"] != contribution_id:
                to_cancel.append(data)

    now = _server_ts()
    seq = _Seq(seq_start)
    ev = _event_common(contribution)
    period = contribution.get("periodLabel") or period_label(_now_dt())
    had_exception = contribution.get("currentExceptionId")

    # -- writes -------------------------------------------------------------
    # Attempt STARTED -> SUCCEEDED (terminal; specs/06 §6.2).
    assert_transition("attempt", attempt["status"], PaymentAttemptStatus.SUCCEEDED)
    txn.update(
        attempts.ref(client, contribution_id, attempt_number),
        {
            "status": str(PaymentAttemptStatus.SUCCEEDED),
            "processorReference": processor_reference,
            "completedAt": now,
        },
    )

    # Contribution PROCESSING -> POSTED.
    assert_transition(
        "contribution", contribution["status"], ContributionStatus.POSTED
    )
    contrib_update = {
        "status": str(ContributionStatus.POSTED),
        "postedAt": now,
        "postedAmountCents": posted,
        "currentExceptionId": None,
        "failureCode": None,
        "failureReason": None,
    }
    stamp_update(contrib_update, ctx.actor_id)
    txn.update(contributions.ref(client, contribution_id), contrib_update)

    # Loan balance + look-ahead (+ exception count decrement on resolve). A
    # completed benefit / paid-off loan has NO next contribution — its remaining
    # schedule is canceled below, so null the look-ahead rather than advertise a
    # date that can never fire (specs/07 §7.4). Otherwise point at next_scheduled.
    look_ahead = {} if benefit_completed else (next_scheduled or {})
    loan_update: dict[str, Any] = {
        "currentBalanceCents": new_balance,
        "nextContributionDate": look_ahead.get("scheduledDate"),
        "nextContributionAmountCents": look_ahead.get("scheduledAmountCents"),
    }
    if loan_paid_off and loan["loanStatus"] != str(LoanStatus.PAID_OFF):
        # Guard the payoff self-loop: if a concurrent installment of the same
        # agreement already drove the loan PAID_OFF, PAID_OFF -> PAID_OFF is an
        # illegal transition that would crash the finalize. Skip it idempotently
        # (FIX 1's inline cancel largely closes this window; keep the guard).
        assert_transition("loan", loan["loanStatus"], LoanStatus.PAID_OFF)
        loan_update["loanStatus"] = str(LoanStatus.PAID_OFF)
    if complete_benefit:
        loan_update["benefitStatus"] = str(BenefitStatus.COMPLETED)
    if had_exception:
        loan_update["openExceptionCount"] = max(0, int(loan.get("openExceptionCount", 0)) - 1)
    stamp_update(loan_update, ctx.actor_id)
    txn.update(loans.ref(client, loan_id), loan_update)

    # Agreement amountPaid / remaining (+ COMPLETED on full pay or payoff).
    agreement_update: dict[str, Any] = {
        "amountPaidCents": new_paid,
        "remainingCommitmentCents": new_remaining,
    }
    if complete_benefit:
        assert_transition("benefit", benefit["status"], BenefitStatus.COMPLETED)
        agreement_update["status"] = str(BenefitStatus.COMPLETED)
        agreement_update["acceptingPayments"] = False
    stamp_update(agreement_update, ctx.actor_id)
    txn.update(agreements.ref(client, agreement_id), agreement_update)

    # Events (PAYMENT_POSTED first so its id can resolve the exception).
    posted_event_id = events.append(
        txn,
        event_type="PAYMENT_POSTED",
        entity_type=_ENTITY_CONTRIBUTION,
        entity_id=contribution_id,
        actor_id=ctx.actor_id,
        actor_role=ctx.actor_role,
        actor_name=ctx.actor_name,
        correlation_id=ctx.correlation_id,
        sequence=seq(),
        metadata={
            "periodLabel": period,
            "postedAmountCents": posted,
            "attemptNumber": attempt_number,
            "processorReference": processor_reference,
        },
        **ev,
    )
    events.append(
        txn,
        event_type="LOAN_BALANCE_UPDATED",
        entity_type=_ENTITY_LOAN,
        entity_id=loan_id,
        actor_id=ctx.actor_id,
        actor_role=ctx.actor_role,
        actor_name=ctx.actor_name,
        correlation_id=ctx.correlation_id,
        sequence=seq(),
        metadata={
            "periodLabel": period,
            "currentBalanceCents": new_balance,
            "deltaCents": -posted,
            "loanPaidOff": loan_paid_off,
        },
        **ev,
    )
    if complete_benefit:
        events.append(
            txn,
            event_type="BENEFIT_COMPLETED",
            entity_type=_ENTITY_AGREEMENT,
            entity_id=agreement_id,
            actor_id=ctx.actor_id,
            actor_role=ctx.actor_role,
            actor_name=ctx.actor_name,
            correlation_id=ctx.correlation_id,
            sequence=seq(),
            metadata={"periodLabel": period, "reason": "PAID_OFF" if loan_paid_off else "FULLY_FUNDED"},
            **ev,
        )
    if reconciled:
        events.append(
            txn,
            event_type="PAYMENT_RECONCILED",
            entity_type=_ENTITY_CONTRIBUTION,
            entity_id=contribution_id,
            actor_id=ctx.actor_id,
            actor_role=ctx.actor_role,
            actor_name=ctx.actor_name,
            correlation_id=ctx.correlation_id,
            sequence=seq(),
            metadata={"finding": "SUCCEEDED", "attemptNumber": attempt_number},
            **ev,
        )

    # Cancel the remaining schedule on payoff / full funding (specs/07 §7.4).
    # The installments were read (before any write) into `to_cancel`; each is a
    # SCHEDULED -> CANCELED terminal transition with its own PAYMENT_CANCELED
    # event. Only reachable via loan payoff (a still-SCHEDULED installment implies
    # remaining commitment > 0, so benefit_completed came from loan_paid_off),
    # hence reason "LOAN_PAID_OFF". This inline cancellation is bounded by the
    # term length (well under Firestore's 500-write/txn cap — safe for demo
    # scale); at production scale it defers to the async
    # cancel-future-contributions task (Phase 3).
    for inst in to_cancel:
        if inst["id"] == contribution_id:
            continue  # never cancel the installment we just POSTED
        assert_transition("contribution", inst["status"], ContributionStatus.CANCELED)
        cancel_update = {"status": str(ContributionStatus.CANCELED)}
        stamp_update(cancel_update, ctx.actor_id)
        txn.update(contributions.ref(client, inst["id"]), cancel_update)
        events.append(
            txn,
            event_type="PAYMENT_CANCELED",
            entity_type=_ENTITY_CONTRIBUTION,
            entity_id=inst["id"],
            actor_id=ctx.actor_id,
            actor_role=ctx.actor_role,
            actor_name=ctx.actor_name,
            correlation_id=ctx.correlation_id,
            sequence=seq(),
            metadata={"reason": "LOAN_PAID_OFF", "periodLabel": inst.get("periodLabel")},
            **_event_common(inst),
        )

    # Resolve the coupled exception (pointer-based; specs/09 §9.3).
    if had_exception:
        exceptions_service.resolve(txn, client, had_exception, resolved_by_event_id=posted_event_id)

    result = {
        "contributionId": contribution_id,
        "status": str(ContributionStatus.POSTED),
        "attemptId": _attempt_id(contribution_id, attempt_number),
        "attemptNumber": attempt_number,
        "postedAmountCents": posted,
        "loanPaidOff": loan_paid_off,
        "benefitCompleted": complete_benefit,
    }
    if idempotency_key:
        _complete_idempotency(txn, idempotency_key, result, client)
    return result


def finalize_failure(
    txn,
    *,
    client,
    ctx: CommandContext,
    contribution_id: str,
    attempt_number: int,
    failure_code,
    failure_reason: Optional[str],
    idempotency_key: Optional[str] = None,
    reconciled: bool = False,
    seq_start: int = 1,
) -> Optional[dict]:
    """Finalize a declined/failed charge (specs/09 §9.1 failure branch).

    Normal path → ``FAILED`` + upsert the coupled exception. If the agreement is
    ``TERMINATED``, route to **settle-then-cancel** → ``CANCELED`` (suppress a new
    exception, dismiss any open one). Returns the result body, or ``None`` when
    the finalize guard fails.
    """
    from servicing import events
    from exceptions import service as exceptions_service
    from repositories import attempts, contributions, loans, stamp_update

    # -- reads (all before any write) ---------------------------------------
    contribution = _get_in_txn(txn, contributions.ref(client, contribution_id))
    attempt = _get_in_txn(txn, attempts.ref(client, contribution_id, attempt_number))
    if not _guard_inflight(contribution, attempt, contribution_id, attempt_number):
        return None
    loan_id = contribution["loanId"]
    from repositories import agreements

    benefit = _get_in_txn(txn, agreements.ref(client, contribution["benefitAgreementId"]))
    loan = _get_in_txn(txn, loans.ref(client, loan_id))
    if benefit is None or loan is None:
        raise NotFound("loan or benefit agreement missing for contribution")

    terminated = benefit.get("status") == str(BenefitStatus.TERMINATED)
    failure_code_str = str(failure_code) if failure_code is not None else None
    had_exception = contribution.get("currentExceptionId")
    now = _server_ts()
    seq = _Seq(seq_start)
    ev = _event_common(contribution)
    period = contribution.get("periodLabel") or period_label(_now_dt())

    if terminated:
        # settle-then-cancel (specs/06 §6.1, specs/09 §9.1, specs/10 §10.4).
        assert_transition(
            "contribution", contribution["status"], ContributionStatus.CANCELED
        )
        # Attempt STARTED -> FAILED (balances untouched: money never moved).
        assert_transition("attempt", attempt["status"], PaymentAttemptStatus.FAILED)
        txn.update(
            attempts.ref(client, contribution_id, attempt_number),
            {
                "status": str(PaymentAttemptStatus.FAILED),
                "failureCode": failure_code_str,
                "failureReason": failure_reason,
                "completedAt": now,
            },
        )
        contrib_update = {
            "status": str(ContributionStatus.CANCELED),
            "failureCode": failure_code_str,
            "failureReason": failure_reason,
        }
        if had_exception:
            exceptions_service.dismiss(txn, client, had_exception, reason="benefit terminated")
            contrib_update["currentExceptionId"] = None
        stamp_update(contrib_update, ctx.actor_id)
        txn.update(contributions.ref(client, contribution_id), contrib_update)
        if had_exception:
            loan_update = {
                "openExceptionCount": max(0, int(loan.get("openExceptionCount", 0)) - 1)
            }
            stamp_update(loan_update, ctx.actor_id)
            txn.update(loans.ref(client, loan_id), loan_update)
        events.append(
            txn,
            event_type="PAYMENT_CANCELED",
            entity_type=_ENTITY_CONTRIBUTION,
            entity_id=contribution_id,
            actor_id=ctx.actor_id,
            actor_role=ctx.actor_role,
            actor_name=ctx.actor_name,
            correlation_id=ctx.correlation_id,
            sequence=seq(),
            metadata={
                "periodLabel": period,
                "reason": "BENEFIT_TERMINATED",
                "failureCode": failure_code_str,
                "attemptNumber": attempt_number,
            },
            **ev,
        )
        if reconciled:
            events.append(
                txn,
                event_type="PAYMENT_RECONCILED",
                entity_type=_ENTITY_CONTRIBUTION,
                entity_id=contribution_id,
                actor_id=ctx.actor_id,
                actor_role=ctx.actor_role,
                actor_name=ctx.actor_name,
                correlation_id=ctx.correlation_id,
                sequence=seq(),
                metadata={"finding": "FAILED", "settled": "CANCELED", "attemptNumber": attempt_number},
                **ev,
            )
        result = {
            "contributionId": contribution_id,
            "status": str(ContributionStatus.CANCELED),
            "attemptId": _attempt_id(contribution_id, attempt_number),
            "attemptNumber": attempt_number,
            "failureCode": failure_code_str,
            "failureReason": failure_reason,
        }
        if idempotency_key:
            _complete_idempotency(txn, idempotency_key, result, client)
        return result

    # -- normal failure path ------------------------------------------------
    assert_transition("contribution", contribution["status"], ContributionStatus.FAILED)
    assert_transition("attempt", attempt["status"], PaymentAttemptStatus.FAILED)

    # specs/09 §9.5: INVALID_ACCOUNT is a terminal *configuration* fault, not a
    # transient payment failure — raise a BENEFIT_CONFIGURATION_ERROR instead of
    # a PAYMENT_FAILED exception so it routes to config remediation, not retry.
    if failure_code_str == str(PaymentFailureCode.INVALID_ACCOUNT):
        exc_type = "BENEFIT_CONFIGURATION_ERROR"
        exc_summary = f"Invalid account configuration for contribution {contribution_id}"
    else:
        exc_type = "PAYMENT_FAILED"
        exc_summary = f"Payment failed for contribution {contribution_id}"

    # Upsert the deterministic exception FIRST (read+write) so all reads precede
    # the remaining writes (specs/08 §8.1). specs/09 §9.3: {cid}__{exceptionType}.
    exc_id = exceptions_service.upsert(
        txn,
        client,
        exception_type=exc_type,
        entity_type=_ENTITY_CONTRIBUTION,
        entity_id=contribution_id,
        summary=exc_summary,
        details=failure_reason,
        loan_id=loan_id,
        borrower_id=contribution.get("borrowerId"),
        borrower_name=contribution.get("borrowerName"),
        employer_id=contribution.get("employerId"),
        employer_name=contribution.get("employerName"),
    )

    txn.update(
        attempts.ref(client, contribution_id, attempt_number),
        {
            "status": str(PaymentAttemptStatus.FAILED),
            "failureCode": failure_code_str,
            "failureReason": failure_reason,
            "completedAt": now,
        },
    )
    contrib_update = {
        "status": str(ContributionStatus.FAILED),
        "failureCode": failure_code_str,
        "failureReason": failure_reason,
        "currentExceptionId": exc_id,
    }
    stamp_update(contrib_update, ctx.actor_id)
    txn.update(contributions.ref(client, contribution_id), contrib_update)

    # Only bump openExceptionCount when a *new* open exception appears (first
    # failure / re-open after resolve), never on an occurrenceCount repeat.
    if not had_exception:
        loan_update = {"openExceptionCount": int(loan.get("openExceptionCount", 0)) + 1}
        stamp_update(loan_update, ctx.actor_id)
        txn.update(loans.ref(client, loan_id), loan_update)

    events.append(
        txn,
        event_type="PAYMENT_FAILED",
        entity_type=_ENTITY_CONTRIBUTION,
        entity_id=contribution_id,
        actor_id=ctx.actor_id,
        actor_role=ctx.actor_role,
        actor_name=ctx.actor_name,
        correlation_id=ctx.correlation_id,
        sequence=seq(),
        metadata={
            "periodLabel": period,
            "failureCode": failure_code_str,
            "failureReason": failure_reason,
            "attemptNumber": attempt_number,
            "exceptionId": exc_id,
        },
        **ev,
    )
    if reconciled:
        events.append(
            txn,
            event_type="PAYMENT_RECONCILED",
            entity_type=_ENTITY_CONTRIBUTION,
            entity_id=contribution_id,
            actor_id=ctx.actor_id,
            actor_role=ctx.actor_role,
            actor_name=ctx.actor_name,
            correlation_id=ctx.correlation_id,
            sequence=seq(),
            metadata={"finding": "FAILED", "attemptNumber": attempt_number},
            **ev,
        )

    result = {
        "contributionId": contribution_id,
        "status": str(ContributionStatus.FAILED),
        "attemptId": _attempt_id(contribution_id, attempt_number),
        "attemptNumber": attempt_number,
        "failureCode": failure_code_str,
        "failureReason": failure_reason,
        "exceptionId": exc_id,
    }
    if idempotency_key:
        _complete_idempotency(txn, idempotency_key, result, client)
    return result


def finalize_not_submitted(
    txn,
    *,
    client,
    ctx: CommandContext,
    contribution_id: str,
    attempt_number: int,
    idempotency_key: Optional[str] = None,
    seq_start: int = 1,
) -> Optional[dict]:
    """Finalize a fenced ``NOT_FOUND`` verdict (specs/09 §9.4 NOT_FOUND branch).

    The charge never reached the processor and — the key now fenced — never can.
    Attempt → ``FAILED(NOT_SUBMITTED)``. If the agreement is ``TERMINATED`` the
    contribution is ``CANCELED`` (dismiss exception); otherwise it is **reverted**
    to its pre-processing state (``SCHEDULED``/``RETRY_PENDING``) for a clean
    retry. The revert is a compensating rollback, not a forward transition, so it
    is applied directly (``PROCESSING → SCHEDULED`` is not in the state machine).
    Returns the result body, or ``None`` when the finalize guard fails.
    """
    from servicing import events
    from exceptions import service as exceptions_service
    from repositories import agreements, attempts, contributions, loans, stamp_update

    contribution = _get_in_txn(txn, contributions.ref(client, contribution_id))
    attempt = _get_in_txn(txn, attempts.ref(client, contribution_id, attempt_number))
    if not _guard_inflight(contribution, attempt, contribution_id, attempt_number):
        return None
    loan_id = contribution["loanId"]
    benefit = _get_in_txn(txn, agreements.ref(client, contribution["benefitAgreementId"]))
    loan = _get_in_txn(txn, loans.ref(client, loan_id))
    if benefit is None or loan is None:
        raise NotFound("loan or benefit agreement missing for contribution")

    terminated = benefit.get("status") == str(BenefitStatus.TERMINATED)
    had_exception = contribution.get("currentExceptionId")
    now = _server_ts()
    seq = _Seq(seq_start)
    ev = _event_common(contribution)
    period = contribution.get("periodLabel") or period_label(_now_dt())

    # Attempt STARTED -> FAILED(NOT_SUBMITTED) (balances never moved).
    assert_transition("attempt", attempt["status"], PaymentAttemptStatus.FAILED)
    txn.update(
        attempts.ref(client, contribution_id, attempt_number),
        {
            "status": str(PaymentAttemptStatus.FAILED),
            "failureCode": str(PaymentFailureCode.NOT_SUBMITTED),
            "failureReason": "Charge never reached the processor; key fenced",
            "completedAt": now,
        },
    )

    if terminated:
        assert_transition(
            "contribution", contribution["status"], ContributionStatus.CANCELED
        )
        contrib_update = {
            "status": str(ContributionStatus.CANCELED),
            "failureCode": str(PaymentFailureCode.NOT_SUBMITTED),
            "failureReason": "Benefit terminated; unsubmitted charge canceled",
        }
        if had_exception:
            exceptions_service.dismiss(txn, client, had_exception, reason="benefit terminated")
            contrib_update["currentExceptionId"] = None
        stamp_update(contrib_update, ctx.actor_id)
        txn.update(contributions.ref(client, contribution_id), contrib_update)
        if had_exception:
            loan_update = {
                "openExceptionCount": max(0, int(loan.get("openExceptionCount", 0)) - 1)
            }
            stamp_update(loan_update, ctx.actor_id)
            txn.update(loans.ref(client, loan_id), loan_update)
        final_status = str(ContributionStatus.CANCELED)
    else:
        # Revert to pre-processing state for a clean retry (compensating).
        revert_status = (
            str(ContributionStatus.SCHEDULED)
            if int(attempt_number) <= 1
            else str(ContributionStatus.RETRY_PENDING)
        )
        contrib_update = {
            "status": revert_status,
            "currentAttemptId": None,
            "failureCode": None,
            "failureReason": None,
        }
        stamp_update(contrib_update, ctx.actor_id)
        txn.update(contributions.ref(client, contribution_id), contrib_update)
        final_status = revert_status

    events.append(
        txn,
        event_type="PAYMENT_RECONCILED",
        entity_type=_ENTITY_CONTRIBUTION,
        entity_id=contribution_id,
        actor_id=ctx.actor_id,
        actor_role=ctx.actor_role,
        actor_name=ctx.actor_name,
        correlation_id=ctx.correlation_id,
        sequence=seq(),
        metadata={
            "periodLabel": period,
            "finding": "NOT_FOUND",
            "resultStatus": final_status,
            "attemptNumber": attempt_number,
        },
        **ev,
    )

    result = {
        "contributionId": contribution_id,
        "status": final_status,
        "attemptId": _attempt_id(contribution_id, attempt_number),
        "attemptNumber": attempt_number,
        "reconciled": True,
        "finding": "NOT_FOUND",
    }
    if idempotency_key:
        _complete_idempotency(txn, idempotency_key, result, client)
    return result


def _complete_idempotency(txn, key: str, result: dict, client) -> None:
    from idempotency import service as idempotency

    idempotency.complete(txn, key, result, client=client)


def _now_dt():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# process_contribution — the specs/09 §9.1 two-phase command
# --------------------------------------------------------------------------- #
def process_contribution(
    contribution_id: str,
    ctx: CommandContext,
    *,
    client=None,
    adapter=None,
) -> dict:
    """Process a scheduled contribution end-to-end (specs/09 §9.1).

    Returns the result body (also stored on the idempotency record for replay).
    A **declined payment is a successful command** whose body carries
    ``status == FAILED`` — the view renders it ``200``, not an HTTP error.
    """
    client = _client_default(client)
    adapter = _adapter_default(adapter, client)

    try:
        # ---- Phase 1: begin (transaction) ---------------------------------
        phase1 = transactional(client)(_process_phase1)(
            client=client, ctx=ctx, contribution_id=contribution_id
        )
        kind, payload = phase1
        if kind == "REPLAY":
            return payload
        if kind == "RECLAIM":
            # A prior attempt may be in-flight (crash between phases). Do NOT
            # create a new attempt/charge — reconcile the existing one and
            # complete this idempotency key (specs/08 §8.3).
            from contributions.reconcile import reconcile_contribution

            return reconcile_contribution(
                contribution_id,
                ctx=ctx,
                idempotency_key=ctx.idempotency_key,
                client=client,
                adapter=adapter,
            )

        attempt_number = payload["attemptNumber"]
        processor_idempotency_key = payload["processorIdempotencyKey"]

        # ---- Phase 2: charge (NO transaction) -----------------------------
        charge_result = adapter.charge(
            processor_idempotency_key=processor_idempotency_key,
            amount_cents=payload["requestedAmountCents"],
            currency=CURRENCY,
            metadata={
                "simulatedOutcome": payload.get("simulatedOutcome"),
                "contributionId": contribution_id,
                "periodLabel": payload.get("periodLabel"),
                "attemptNumber": attempt_number,
            },
        )

        # ---- Phase 3: finalize (transaction) ------------------------------
        if charge_result.status == "SUCCEEDED":
            run = transactional(client)(finalize_success)
            result = run(
                client=client,
                ctx=ctx,
                contribution_id=contribution_id,
                attempt_number=attempt_number,
                processor_reference=charge_result.processor_reference,
                idempotency_key=ctx.idempotency_key,
                seq_start=2,
            )
        else:
            run = transactional(client)(finalize_failure)
            result = run(
                client=client,
                ctx=ctx,
                contribution_id=contribution_id,
                attempt_number=attempt_number,
                failure_code=charge_result.failure_code,
                failure_reason=charge_result.failure_reason,
                idempotency_key=ctx.idempotency_key,
                seq_start=2,
            )

        if result is None:
            # Guard failed — a concurrent finalizer (sweeper) already resolved
            # this attempt. Complete the idempotency key with the current state.
            result = _complete_with_current_state(client, ctx, contribution_id)
        return result
    except CommandError:
        raise
    except DomainError as exc:
        raise from_domain_error(exc)


def _process_phase1(txn, *, client, ctx: CommandContext, contribution_id: str):
    """Phase 1: idempotency + SCHEDULED/RETRY_PENDING → PROCESSING + STARTED attempt."""
    from idempotency import service as idempotency
    from servicing import events
    from repositories import agreements, attempts, contributions, loans, stamp_update

    # -- reads (before begin writes the PENDING record) ---------------------
    contribution = _get_in_txn(txn, contributions.ref(client, contribution_id))
    if contribution is None:
        raise NotFound(f"contribution {contribution_id} not found")
    benefit = _get_in_txn(txn, agreements.ref(client, contribution["benefitAgreementId"]))
    loan = _get_in_txn(txn, loans.ref(client, contribution["loanId"]))
    if benefit is None or loan is None:
        raise NotFound("loan or benefit agreement missing for contribution")

    # -- idempotency begin FIRST (specs/08 §8.2): handle the Outcome before any
    #    entity precondition — a replay returns the stored result and an
    #    in-progress/reused key short-circuits without re-validating. begin reads
    #    the idem doc (part of the read set) and writes PENDING only on NEW. This
    #    mirrors benefits.activate_benefit's ordering (idempotency-first).
    outcome = idempotency.begin(
        txn,
        key=ctx.idempotency_key,
        operation=OPERATION_PROCESS,
        request_hash=ctx.request_hash,
        entity_id=contribution_id,
        entity_type=_ENTITY_CONTRIBUTION,
        lease_ttl_seconds=LEASE_TTL_SECONDS,
        lease_owner=ctx.lease_owner,
        client=client,
    )
    if outcome.is_replay:
        return ("REPLAY", outcome.result)
    if outcome.is_in_progress:
        raise OperationInProgress(retry_after=RETRY_AFTER_IN_PROGRESS)
    if outcome.is_reuse:
        raise IdempotencyKeyReused()
    if outcome.reclaimed:
        # Fresh PENDING lease taken over an abandoned in-flight attempt: do not
        # start a new charge here — signal the caller to reconcile it.
        return ("RECLAIM", None)

    # -- NEW: entity precondition + eligibility (specs/09 §9.1). A raise here
    #    aborts the txn, discarding the PENDING idempotency write above (the
    #    same abort-discards-PENDING contract activate_benefit relies on).
    assert_transition("contribution", contribution["status"], ContributionStatus.PROCESSING)
    if not benefit.get("acceptingPayments"):
        raise BenefitNotAcceptingPayments()
    if loan.get("loanStatus") not in _PROCESSABLE_LOAN_STATUSES:
        raise Unprocessable(
            f"loan status {loan.get('loanStatus')!r} does not accept payments "
            f"(PAID_OFF/CLOSED block)"
        )

    # -- writes -------------------------------------------------------------
    attempt_number = int(contribution.get("attemptCount", 0)) + 1
    attempt_id_val = _attempt_id(contribution_id, attempt_number)
    proc_key = _processor_key(contribution_id, attempt_number)
    now = _server_ts()

    txn.set(
        attempts.ref(client, contribution_id, attempt_number),
        {
            "contributionId": contribution_id,
            "loanId": contribution["loanId"],
            "attemptNumber": attempt_number,
            "processorIdempotencyKey": proc_key,
            "commandIdempotencyKey": ctx.idempotency_key,
            "status": str(PaymentAttemptStatus.STARTED),
            "reconcileAttempts": 0,
            "requestedAmountCents": int(contribution["scheduledAmountCents"]),
            "processorReference": None,
            "failureCode": None,
            "failureReason": None,
            "startedAt": now,
            "completedAt": None,
        },
    )

    contrib_update = {
        "status": str(ContributionStatus.PROCESSING),
        "attemptCount": attempt_number,
        "currentAttemptId": attempt_id_val,
        "lastAttemptAt": now,
    }
    stamp_update(contrib_update, ctx.actor_id)
    txn.update(contributions.ref(client, contribution_id), contrib_update)

    events.append(
        txn,
        event_type="PAYMENT_PROCESSING",
        entity_type=_ENTITY_CONTRIBUTION,
        entity_id=contribution_id,
        actor_id=ctx.actor_id,
        actor_role=ctx.actor_role,
        actor_name=ctx.actor_name,
        correlation_id=ctx.correlation_id,
        sequence=1,
        metadata={
            "periodLabel": contribution.get("periodLabel"),
            "attemptNumber": attempt_number,
            "processorIdempotencyKey": proc_key,
            "requestedAmountCents": int(contribution["scheduledAmountCents"]),
        },
        **_event_common(contribution),
    )

    return (
        "PROCEED",
        {
            "attemptNumber": attempt_number,
            "processorIdempotencyKey": proc_key,
            "requestedAmountCents": int(contribution["scheduledAmountCents"]),
            "simulatedOutcome": contribution.get("simulatedOutcome"),
            "periodLabel": contribution.get("periodLabel"),
        },
    )


def _complete_with_current_state(client, ctx: CommandContext, contribution_id: str) -> dict:
    """Complete the idempotency key from the current contribution state.

    Used when the Phase-3 guard aborts because a concurrent finalizer already
    resolved the attempt — we still owe the caller a stored, replayable result.
    """
    from repositories import contributions

    def _run(txn):
        contribution = _get_in_txn(txn, contributions.ref(client, contribution_id))
        status = (contribution or {}).get("status")
        result = {
            "contributionId": contribution_id,
            "status": status,
            "attemptId": (contribution or {}).get("currentAttemptId"),
            "note": "finalized concurrently; state read post-hoc",
        }
        if ctx.idempotency_key:
            _complete_idempotency(txn, ctx.idempotency_key, result, client)
        return result

    return transactional(client)(_run)()


# --------------------------------------------------------------------------- #
# retry_contribution — the specs/09 §9.2 command
# --------------------------------------------------------------------------- #
def retry_contribution(
    contribution_id: str,
    ctx: CommandContext,
    *,
    client=None,
    adapter=None,
) -> dict:
    """Schedule a retry of a ``FAILED`` contribution, then process it inline.

    Step 1 (transaction): assert preconditions; ``FAILED`` → ``RETRY_PENDING``;
    ``PAYMENT_RETRY_SCHEDULED`` event; idempotency ``COMPLETED``. Step 2: reuse
    the :func:`process_contribution` workflow (inline under
    ``TASK_EXECUTION_MODE=inline``) with a *derived* idempotency key so the two
    commands stay independently idempotent (specs/09 §9.2).
    """
    client = _client_default(client)
    adapter = _adapter_default(adapter, client)

    try:
        kind, _ = transactional(client)(_retry_phase1)(
            client=client, ctx=ctx, contribution_id=contribution_id
        )
        # Whether the retry transition ran now or replayed, re-process: the
        # derived process key makes it idempotent (replays if already done).
        process_ctx = replace(
            ctx,
            idempotency_key=f"{ctx.idempotency_key}__retry_process",
            request_hash=request_hash(
                "POST", f"/contributions/{contribution_id}/process", None
            ),
            lease_owner=f"{ctx.lease_owner}__proc",
        )
        return process_contribution(
            contribution_id, process_ctx, client=client, adapter=adapter
        )
    except CommandError:
        raise
    except DomainError as exc:
        raise from_domain_error(exc)


def _retry_phase1(txn, *, client, ctx: CommandContext, contribution_id: str):
    """Step 1: FAILED → RETRY_PENDING (with cancel-wins preconditions)."""
    from idempotency import service as idempotency
    from servicing import events
    from repositories import agreements, borrowers, contributions, loans, stamp_update

    # -- reads --------------------------------------------------------------
    contribution = _get_in_txn(txn, contributions.ref(client, contribution_id))
    if contribution is None:
        raise NotFound(f"contribution {contribution_id} not found")
    benefit = _get_in_txn(txn, agreements.ref(client, contribution["benefitAgreementId"]))
    loan = _get_in_txn(txn, loans.ref(client, contribution["loanId"]))
    borrower = _get_in_txn(txn, borrowers.ref(client, contribution["borrowerId"]))
    if benefit is None or loan is None or borrower is None:
        raise NotFound("loan, benefit agreement or borrower missing for contribution")

    # -- idempotency begin FIRST (specs/08 §8.2): handle the Outcome before the
    #    entity preconditions — a replay returns the stored result and an
    #    in-progress/reused key short-circuits without re-validating. A reclaimed
    #    (expired-lease / retryable-FAILED) key falls through to re-drive the
    #    transition idempotently. Mirrors benefits.activate_benefit's ordering.
    outcome = idempotency.begin(
        txn,
        key=ctx.idempotency_key,
        operation=OPERATION_RETRY,
        request_hash=ctx.request_hash,
        entity_id=contribution_id,
        entity_type=_ENTITY_CONTRIBUTION,
        lease_ttl_seconds=LEASE_TTL_SECONDS,
        lease_owner=ctx.lease_owner,
        client=client,
    )
    if outcome.is_replay:
        return ("REPLAY", outcome.result)
    if outcome.is_in_progress:
        raise OperationInProgress(retry_after=RETRY_AFTER_IN_PROGRESS)
    if outcome.is_reuse:
        raise IdempotencyKeyReused()

    # -- NEW: transition + preconditions (specs/09 §9.2). A raise here aborts
    #    the txn, discarding the PENDING idempotency write above.
    assert_transition(
        "contribution", contribution["status"], ContributionStatus.RETRY_PENDING
    )
    if not benefit.get("acceptingPayments"):
        raise BenefitNotAcceptingPayments()
    if borrower.get("employmentStatus") != str(EmploymentStatus.ACTIVE):
        raise Unprocessable("employment is not ACTIVE; retry not permitted")
    if loan.get("loanStatus") != str(LoanStatus.ACTIVE):
        raise Unprocessable(f"loan status {loan.get('loanStatus')!r} is not ACTIVE")

    # -- writes -------------------------------------------------------------
    contrib_update = {"status": str(ContributionStatus.RETRY_PENDING)}
    stamp_update(contrib_update, ctx.actor_id)
    txn.update(contributions.ref(client, contribution_id), contrib_update)

    events.append(
        txn,
        event_type="PAYMENT_RETRY_SCHEDULED",
        entity_type=_ENTITY_CONTRIBUTION,
        entity_id=contribution_id,
        actor_id=ctx.actor_id,
        actor_role=ctx.actor_role,
        actor_name=ctx.actor_name,
        correlation_id=ctx.correlation_id,
        sequence=1,
        metadata={
            "periodLabel": contribution.get("periodLabel"),
            "priorAttemptCount": int(contribution.get("attemptCount", 0)),
        },
        **_event_common(contribution),
    )

    result = {
        "contributionId": contribution_id,
        "status": str(ContributionStatus.RETRY_PENDING),
    }
    _complete_idempotency(txn, ctx.idempotency_key, result, client)
    return ("DONE", result)
