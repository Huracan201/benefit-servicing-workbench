"""benefits.services — the activate-benefit command (specs/10 §10.1).

Turns a ``PENDING`` benefit agreement into an ``ACTIVE`` one with a fully
generated contribution schedule. For Phase 2 (specs/19 §19.2) the schedule is
generated **inline** (``TASK_EXECUTION_MODE=inline`` — no Cloud Task): the
accept, generate, and finalize steps of the §10.1 flow all run in a *single*
Firestore transaction, so the outcome is atomic (all-or-nothing) and the IDs are
identical to the eventual async path. A 36-installment schedule plus its two
events is far under Firestore's 500-writes/transaction limit (specs/10 §10.1
note), so the single-batch path is used here.

Contract highlights:

* **Preconditions** (specs/10 §10.1): agreement ``PENDING``; borrower employment
  ``ACTIVE``; loan ``ACTIVE``; employer ``ACTIVE``; ``startDate`` not in the past
  (else ``422``); the loan has no *other* active agreement.
* **Idempotency** (specs/08 §8.2): the ``idempotencyKeys/{key}`` record is
  created inside the same transaction as the state change and completed in it —
  a replay returns the stored result; a same-key/different-hash request is a
  ``409``; a live lease is ``202``.
* **Schedule** (specs/07 §7.3): amounts are solved so
  ``Σ(scheduledAmountCents) == totalCommitmentCents`` exactly (invariant I5),
  each contribution created at its deterministic id ``{agreementId}__{NNN}`` with
  a create-precondition.
* **Events** (specs/04 §4.9): ``BENEFIT_ACTIVATION_STARTED`` (sequence 1) and
  ``BENEFIT_ACTIVATED`` (sequence 2) share the command ``correlationId``.
* **Sync** (specs/04 §4.5): ``loan.benefitStatus``/``nextContributionDate``/
  ``nextContributionAmountCents`` are updated in the same transaction.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from commands.base import (
    ASYNC_LEASE_TTL_SECONDS,
    RETRY_AFTER_ACTIVATION,
    CommandContext,
    CommandError,
    IdempotencyKeyReused,
    NotFound,
    OperationInProgress,
    Unprocessable,
    from_domain_error,
    transactional,
)
from common import errors as domain_errors
from common import invariants
from common import state_machines
from common.enums import (
    BenefitStatus,
    ContributionStatus,
    EmployerStatus,
    EmploymentStatus,
    LoanStatus,
)
from common.ids import contribution_id as _make_contribution_id
from common.money import solve_schedule
from common.periods import SYSTEM_TIMEZONE, period_label, scheduled_datetime
from idempotency import service as idempotency
from repositories import (
    agreements,
    borrowers,
    contributions,
    employers,
    loans,
    stamp_create,
    stamp_update,
)
from servicing import events as servicing_events

OPERATION = "activate-benefit"
ENTITY_TYPE = "BENEFIT_AGREEMENT"

# Agreement statuses that count as an "active" agreement occupying the loan
# (specs/10 §10.1 "the loan has no other active agreement").
_OCCUPYING_STATUSES = frozenset(
    {
        BenefitStatus.ACTIVATING.value,
        BenefitStatus.ACTIVE.value,
        BenefitStatus.SUSPENDED.value,
    }
)


# --------------------------------------------------------------------------- #
# Transactional read helper
# --------------------------------------------------------------------------- #
def _txn_get(txn: Any, ref: Any) -> Optional[dict]:
    """Read a single document *inside* the transaction, as dict-with-id or None.

    Firestore's ``Transaction.get`` yields snapshots (a single snapshot or a
    one-element generator depending on client version); normalise both.
    """
    got = txn.get(ref)
    snap = got if hasattr(got, "exists") else next(iter(got), None)
    if snap is None or not getattr(snap, "exists", False):
        return None
    data = snap.to_dict() or {}
    data["id"] = snap.id
    return data


# --------------------------------------------------------------------------- #
# startDate normalisation
# --------------------------------------------------------------------------- #
def _as_local_date(value: Any) -> date:
    """Coerce a stored ``startDate`` (timestamp/date/ISO string) to a local date.

    A tz-aware datetime (Firestore Timestamp, UTC) is converted to
    ``SYSTEM_TIMEZONE`` before taking the calendar date; a naive datetime is
    read as wall-clock in ``SYSTEM_TIMEZONE``; an ISO string uses its date part.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=SYSTEM_TIMEZONE).date()
        return value.astimezone(SYSTEM_TIMEZONE).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    raise Unprocessable("agreement.startDate is missing or unreadable")


def _today_local() -> date:
    return datetime.now(SYSTEM_TIMEZONE).date()


# --------------------------------------------------------------------------- #
# Precondition validation (specs/10 §10.1)
# --------------------------------------------------------------------------- #
def _validate_preconditions(
    *,
    agreement: dict,
    loan: Optional[dict],
    borrower: Optional[dict],
    employer: Optional[dict],
    agreement_id: str,
) -> date:
    """Assert every §10.1 precondition; return the validated local ``startDate``.

    Raises the typed :class:`commands.base.CommandError` matching the failure:
    a non-``PENDING`` agreement is a ``409 INVALID_TRANSITION`` (via the benefit
    state machine); the business preconditions (employment/employer/loan/date)
    are ``422 UNPROCESSABLE`` per §10.1.
    """
    # Agreement must be PENDING — expressed as the PENDING -> ACTIVATING edge so
    # the rejection is a precise 409 INVALID_TRANSITION (specs/06).
    state_machines.assert_transition(
        "benefit", agreement.get("status"), BenefitStatus.ACTIVATING.value
    )
    # Sanity: the ACTIVATING -> ACTIVE edge we will also take must be legal.
    state_machines.assert_transition(
        "benefit", BenefitStatus.ACTIVATING.value, BenefitStatus.ACTIVE.value
    )

    if borrower is None:
        raise NotFound("borrower not found for agreement")
    if borrower.get("employmentStatus") != EmploymentStatus.ACTIVE.value:
        raise Unprocessable(
            "borrower employment is not ACTIVE; cannot activate benefit"
        )

    if employer is None:
        raise NotFound("employer not found for agreement")
    if employer.get("status") != EmployerStatus.ACTIVE.value:
        raise Unprocessable("employer is not ACTIVE; cannot activate benefit")

    if loan is None:
        raise NotFound("loan not found for agreement")
    if loan.get("loanStatus") != LoanStatus.ACTIVE.value:
        raise Unprocessable("loan is not ACTIVE; cannot activate benefit")

    # No *other* active agreement occupying the loan (specs/10 §10.1). The loan's
    # synced benefitStatus/benefitAgreementId is authoritative for this check.
    other_agreement_id = loan.get("benefitAgreementId")
    if (
        other_agreement_id
        and other_agreement_id != agreement_id
        and loan.get("benefitStatus") in _OCCUPYING_STATUSES
    ):
        raise Unprocessable("loan already has an active benefit agreement")

    start_date = _as_local_date(agreement.get("startDate"))
    if start_date < _today_local():
        raise Unprocessable("startDate is in the past; cannot activate benefit")
    return start_date


# --------------------------------------------------------------------------- #
# The command
# --------------------------------------------------------------------------- #
def activate_benefit(
    *, agreement_id: str, ctx: CommandContext, client: Any = None
) -> dict:
    """Activate a ``PENDING`` benefit agreement, generating its schedule inline.

    Returns the response body (a serialisable summary of the now-``ACTIVE``
    agreement) — the same object stored for idempotent replay. Raises a
    :class:`commands.base.CommandError` subclass on any precondition/idempotency
    failure, which the view maps to the specs/11 §11.3 HTTP response.
    """
    if client is None:
        from common.firestore import get_client

        client = get_client()

    @transactional(client)
    def _run(txn: Any) -> dict:
        # --- reads (all before any write — Firestore ordering rule) ----------
        agreement = _txn_get(txn, agreements.ref(client, agreement_id))
        if agreement is None:
            raise NotFound(f"benefit agreement {agreement_id!r} not found")

        loan_id = agreement.get("loanId")
        borrower_id = agreement.get("borrowerId")
        employer_id = agreement.get("employerId")

        loan = _txn_get(txn, loans.ref(client, loan_id)) if loan_id else None
        borrower = (
            _txn_get(txn, borrowers.ref(client, borrower_id)) if borrower_id else None
        )
        employer = (
            _txn_get(txn, employers.ref(client, employer_id)) if employer_id else None
        )

        # --- idempotency: begin inside the txn (reads then writes PENDING) ----
        outcome = idempotency.begin(
            txn,
            key=ctx.idempotency_key,
            operation=OPERATION,
            request_hash=ctx.request_hash,
            entity_id=agreement_id,
            entity_type=ENTITY_TYPE,
            lease_ttl_seconds=ASYNC_LEASE_TTL_SECONDS,
            lease_owner=ctx.lease_owner,
            client=client,
        )
        if outcome.is_replay:
            # Prior success — replay the stored result, skip re-validation.
            return outcome.result or {}
        if outcome.is_in_progress:
            raise OperationInProgress(
                "benefit activation already in progress",
                retry_after=RETRY_AFTER_ACTIVATION,
                state={"agreementId": agreement_id, "status": agreement.get("status")},
            )
        if outcome.is_reuse:
            raise IdempotencyKeyReused(
                "idempotency key reused with a different request"
            )

        # --- NEW: validate preconditions (a raise aborts the whole txn,
        #     discarding the PENDING idempotency write we just made) -----------
        start_date = _validate_preconditions(
            agreement=agreement,
            loan=loan,
            borrower=borrower,
            employer=employer,
            agreement_id=agreement_id,
        )

        total = int(agreement.get("totalCommitmentCents"))
        term = int(agreement.get("termMonths"))
        currency = agreement.get("currency", "USD")
        borrower_name = agreement.get("borrowerName")
        employer_name = agreement.get("employerName")

        # --- solve the schedule; Σ == commitment exactly (I5, specs/07 §7.3) --
        schedule = solve_schedule(total, term)
        invariants.check_schedule_sums_to_commitment(schedule, total)

        first_dt = scheduled_datetime(start_date, 1)
        end_dt = scheduled_datetime(start_date, term)

        # --- generate contributions (deterministic id + create-precondition) --
        for installment_number in range(1, term + 1):
            sched_dt = scheduled_datetime(start_date, installment_number)
            cid = _make_contribution_id(agreement_id, installment_number)
            contribution = {
                "benefitAgreementId": agreement_id,
                "installmentNumber": installment_number,
                "borrowerId": borrower_id,
                "borrowerName": borrower_name,
                "employerId": employer_id,
                "employerName": employer_name,
                "loanId": loan_id,
                "currency": currency,
                "scheduledDate": sched_dt,
                "periodLabel": period_label(sched_dt),
                "scheduledAmountCents": schedule[installment_number - 1],
                "status": ContributionStatus.SCHEDULED.value,
                "attemptCount": 0,
                "currentAttemptId": None,
                "currentExceptionId": None,
                "lastAttemptAt": None,
                "postedAt": None,
                "postedAmountCents": None,
                "failureCode": None,
                "failureReason": None,
            }
            stamp_create(contribution, ctx.actor_id)
            # create-precondition: a redelivered/duplicate generation of an
            # existing id is rejected (specs/10 §10.1), never a silent overwrite.
            txn.create(contributions.ref(client, cid), contribution)

        # --- finalize the agreement: ACTIVATING -> ACTIVE --------------------
        agreement_update = {
            "status": BenefitStatus.ACTIVE.value,
            "acceptingPayments": True,
            "scheduleGenerated": True,
            "plannedInstallmentCount": term,
            "installmentsGenerated": term,
            "endDate": end_dt,
            "suspendedReason": None,
        }
        stamp_update(agreement_update, ctx.actor_id)
        txn.update(agreements.ref(client, agreement_id), agreement_update)

        # --- sync the loan look-ahead (specs/04 §4.5) ------------------------
        loan_update = {
            "benefitStatus": BenefitStatus.ACTIVE.value,
            "benefitAgreementId": agreement_id,
            "nextContributionDate": first_dt,
            "nextContributionAmountCents": schedule[0],
        }
        stamp_update(loan_update, ctx.actor_id)
        txn.update(loans.ref(client, loan_id), loan_update)

        # --- events (shared correlationId, sequence 1..2) --------------------
        servicing_events.append(
            txn,
            event_type="BENEFIT_ACTIVATION_STARTED",
            entity_type=ENTITY_TYPE,
            entity_id=agreement_id,
            actor_id=ctx.actor_id,
            actor_role=ctx.actor_role,
            actor_name=ctx.actor_name,
            correlation_id=ctx.correlation_id,
            sequence=1,
            metadata={
                "previousStatus": BenefitStatus.PENDING.value,
                "newStatus": BenefitStatus.ACTIVATING.value,
                "plannedInstallmentCount": term,
                "totalCommitmentCents": total,
                "termMonths": term,
            },
            loan_id=loan_id,
            borrower_id=borrower_id,
            employer_id=employer_id,
            benefit_agreement_id=agreement_id,
        )
        servicing_events.append(
            txn,
            event_type="BENEFIT_ACTIVATED",
            entity_type=ENTITY_TYPE,
            entity_id=agreement_id,
            actor_id=ctx.actor_id,
            actor_role=ctx.actor_role,
            actor_name=ctx.actor_name,
            correlation_id=ctx.correlation_id,
            sequence=2,
            metadata={
                "previousStatus": BenefitStatus.ACTIVATING.value,
                "newStatus": BenefitStatus.ACTIVE.value,
                "installmentsGenerated": term,
                "firstContributionDate": first_dt.isoformat(),
                "endDate": end_dt.isoformat(),
            },
            loan_id=loan_id,
            borrower_id=borrower_id,
            employer_id=employer_id,
            benefit_agreement_id=agreement_id,
        )

        result = {
            "agreementId": agreement_id,
            "status": BenefitStatus.ACTIVE.value,
            "acceptingPayments": True,
            "scheduleGenerated": True,
            "plannedInstallmentCount": term,
            "installmentsGenerated": term,
            "termMonths": term,
            "totalCommitmentCents": total,
            "remainingCommitmentCents": int(
                agreement.get("remainingCommitmentCents", total)
            ),
            "amountPaidCents": int(agreement.get("amountPaidCents", 0)),
            "currency": currency,
            "startDate": start_date.isoformat(),
            "endDate": end_dt.isoformat(),
            "firstContributionDate": first_dt.isoformat(),
            "nextContributionAmountCents": schedule[0],
            "correlationId": ctx.correlation_id,
        }

        # --- idempotency COMPLETED, in the same transaction ------------------
        idempotency.complete(txn, ctx.idempotency_key, result, client=client)
        return result

    try:
        return _run()
    except CommandError:
        raise
    except domain_errors.DomainError as exc:
        # state_machines / invariants raised a framework-free domain error;
        # re-raise the HTTP-aware equivalent (409) for the view.
        raise from_domain_error(exc)
