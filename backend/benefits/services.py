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
    LEASE_TTL_SECONDS,
    RETRY_AFTER_ACTIVATION,
    RETRY_AFTER_IN_PROGRESS,
    CommandContext,
    CommandError,
    IdempotencyKeyReused,
    InvalidTransition,
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
        raise from_domain_error(exc) from exc


# --------------------------------------------------------------------------- #
# suspend / resume / terminate (specs/10 §10.2, §10.3)
#
# Each is idempotency-first and mirrors activate_benefit's transactional
# ordering (reads → idempotency.begin → replay/in-progress/reuse → transition +
# preconditions → writes → event → idempotency.complete). The core transaction
# commits the *decision* (the status change + its servicing event + the
# idempotency completion); resume and terminate then run their bounded
# **inline** follow-up task (schedule shift / cancel-future-contributions)
# AFTER that transaction commits — the Phase-2 (specs/19 §19.2) stand-in for the
# Phase-3 Cloud Task. Those tasks are themselves idempotent, so a replay simply
# returns the stored result and skips re-running them.
# --------------------------------------------------------------------------- #
OPERATION_SUSPEND = "suspend-benefit"
OPERATION_RESUME = "resume-benefit"
OPERATION_TERMINATE = "terminate-benefit"

# suspendedReason values (specs/04 §4.6, specs/10 §10.2). MANUAL from this
# command; LEAVE from the employment cascade (§10.4).
_SUSPEND_REASON_MANUAL = "MANUAL"

# Reason string stamped on the cancel-future-contributions task events (§10.4).
_TERMINATE_REASON = "benefit terminated"


def _now_local() -> datetime:
    """Current instant as a ``SYSTEM_TIMEZONE``-aware ``datetime``.

    Computed once at command entry (outside the transaction) so a Firestore
    contention retry of the handler reuses the same instant — the recorded
    ``suspendedAt`` / resume instant is stable across retries.
    """
    return datetime.now(SYSTEM_TIMEZONE)


def suspend_benefit(
    *, agreement_id: str, ctx: CommandContext, client: Any = None
) -> dict:
    """Suspend an ``ACTIVE`` benefit agreement (specs/10 §10.2).

    ``ACTIVE → SUSPENDED``; ``acceptingPayments = false``; ``suspendedReason =
    MANUAL``; ``suspendedAt`` recorded (so a later resume can compute the shift);
    ``loan.benefitStatus`` synced; one ``BENEFIT_SUSPENDED`` event. Future
    ``SCHEDULED`` installments are **left in place** — suspension is reversible;
    the ``acceptingPayments == false`` gate stops them processing meanwhile.
    """
    if client is None:
        from common.firestore import get_client

        client = get_client()

    suspended_at = _now_local()

    @transactional(client)
    def _run(txn: Any) -> dict:
        # --- reads (all before any write) ------------------------------------
        agreement = _txn_get(txn, agreements.ref(client, agreement_id))
        if agreement is None:
            raise NotFound(f"benefit agreement {agreement_id!r} not found")
        loan_id = agreement.get("loanId")
        borrower_id = agreement.get("borrowerId")
        employer_id = agreement.get("employerId")
        loan = _txn_get(txn, loans.ref(client, loan_id)) if loan_id else None

        # --- idempotency: begin inside the txn -------------------------------
        outcome = idempotency.begin(
            txn,
            key=ctx.idempotency_key,
            operation=OPERATION_SUSPEND,
            request_hash=ctx.request_hash,
            entity_id=agreement_id,
            entity_type=ENTITY_TYPE,
            lease_ttl_seconds=LEASE_TTL_SECONDS,
            lease_owner=ctx.lease_owner,
            client=client,
        )
        if outcome.is_replay:
            return outcome.result or {}
        if outcome.is_in_progress:
            raise OperationInProgress(
                "benefit suspend already in progress",
                retry_after=RETRY_AFTER_IN_PROGRESS,
                state={"agreementId": agreement_id, "status": agreement.get("status")},
            )
        if outcome.is_reuse:
            raise IdempotencyKeyReused(
                "idempotency key reused with a different request"
            )

        # --- transition ACTIVE -> SUSPENDED (a raise aborts the txn, discarding
        #     the PENDING idempotency write) -----------------------------------
        previous_status = agreement.get("status")
        state_machines.assert_transition(
            "benefit", previous_status, BenefitStatus.SUSPENDED.value
        )

        # --- writes ----------------------------------------------------------
        agreement_update = {
            "status": BenefitStatus.SUSPENDED.value,
            "acceptingPayments": False,
            "suspendedReason": _SUSPEND_REASON_MANUAL,
            "suspendedAt": suspended_at,
        }
        stamp_update(agreement_update, ctx.actor_id)
        txn.update(agreements.ref(client, agreement_id), agreement_update)

        if loan_id and loan is not None:
            loan_update = {"benefitStatus": BenefitStatus.SUSPENDED.value}
            stamp_update(loan_update, ctx.actor_id)
            txn.update(loans.ref(client, loan_id), loan_update)

        servicing_events.append(
            txn,
            event_type="BENEFIT_SUSPENDED",
            entity_type=ENTITY_TYPE,
            entity_id=agreement_id,
            actor_id=ctx.actor_id,
            actor_role=ctx.actor_role,
            actor_name=ctx.actor_name,
            correlation_id=ctx.correlation_id,
            sequence=1,
            metadata={
                "previousStatus": previous_status,
                "newStatus": BenefitStatus.SUSPENDED.value,
                "suspendedReason": _SUSPEND_REASON_MANUAL,
                "suspendedAt": suspended_at.isoformat(),
            },
            loan_id=loan_id,
            borrower_id=borrower_id,
            employer_id=employer_id,
            benefit_agreement_id=agreement_id,
        )

        result = {
            "agreementId": agreement_id,
            "status": BenefitStatus.SUSPENDED.value,
            "acceptingPayments": False,
            "suspendedReason": _SUSPEND_REASON_MANUAL,
            "suspendedAt": suspended_at.isoformat(),
            "correlationId": ctx.correlation_id,
        }
        idempotency.complete(txn, ctx.idempotency_key, result, client=client)
        return result

    try:
        return _run()
    except CommandError:
        raise
    except domain_errors.DomainError as exc:
        raise from_domain_error(exc) from exc


def resume_benefit(
    *, agreement_id: str, ctx: CommandContext, client: Any = None
) -> dict:
    """Resume a ``SUSPENDED`` benefit agreement (specs/10 §10.2).

    ``SUSPENDED → ACTIVE`` **only** (an ``ACTIVATING`` agreement also has a legal
    ``-> ACTIVE`` edge, so the SUSPENDED-only rule is enforced explicitly);
    ``acceptingPayments = true``; ``suspendedReason``/``suspendedAt`` cleared;
    ``loan.benefitStatus`` synced; one ``BENEFIT_RESUMED`` event. After the
    transaction commits, the **schedule-shift** task (:func:`benefits.shift.
    shift_schedule`) runs inline to re-date the remaining schedule forward by the
    suspension duration (specs/07 §7.8) — no catch-up lump.
    """
    if client is None:
        from common.firestore import get_client

        client = get_client()

    resumed_at = _now_local()
    followup: dict[str, Any] = {}  # populated only on the real (non-replay) path

    @transactional(client)
    def _run(txn: Any) -> dict:
        # --- reads (all before any write) ------------------------------------
        agreement = _txn_get(txn, agreements.ref(client, agreement_id))
        if agreement is None:
            raise NotFound(f"benefit agreement {agreement_id!r} not found")
        loan_id = agreement.get("loanId")
        borrower_id = agreement.get("borrowerId")
        employer_id = agreement.get("employerId")
        loan = _txn_get(txn, loans.ref(client, loan_id)) if loan_id else None

        # --- idempotency: begin inside the txn -------------------------------
        outcome = idempotency.begin(
            txn,
            key=ctx.idempotency_key,
            operation=OPERATION_RESUME,
            request_hash=ctx.request_hash,
            entity_id=agreement_id,
            entity_type=ENTITY_TYPE,
            lease_ttl_seconds=LEASE_TTL_SECONDS,
            lease_owner=ctx.lease_owner,
            client=client,
        )
        if outcome.is_replay:
            return outcome.result or {}
        if outcome.is_in_progress:
            raise OperationInProgress(
                "benefit resume already in progress",
                retry_after=RETRY_AFTER_IN_PROGRESS,
                state={"agreementId": agreement_id, "status": agreement.get("status")},
            )
        if outcome.is_reuse:
            raise IdempotencyKeyReused(
                "idempotency key reused with a different request"
            )

        # --- transition SUSPENDED -> ACTIVE (SUSPENDED source only) ----------
        # `suspended_from` is read before the reclaim guard so the post-commit
        # shift can be signalled on both the fresh and the reclaim path (on a
        # reclaim the agreement is already ACTIVE, so this is None — the shift is
        # driven by the agreement's persisted scheduleShiftMonths, not by this
        # value, which only supplies the SCHEDULE_SHIFTED event's window metadata).
        suspended_from = agreement.get("suspendedAt")

        # Reclaim-aware: on a same-key reclaim of an abandoned lease where the
        # agreement is ALREADY ACTIVE, the original call's core txn committed the
        # SUSPENDED -> ACTIVE transition AND the scheduleShiftMonths bump but
        # crashed before the post-commit shift + completion. Skip the transition +
        # writes (already applied — re-running would double-increment the shift)
        # and fall through to RE-DRIVE shift_schedule below. A genuine fresh key
        # (reclaimed is False) on an already-ACTIVE benefit still hits
        # assert_transition -> 409.
        already_target = agreement.get("status") == BenefitStatus.ACTIVE.value
        if not (outcome.reclaimed and already_target):
            previous_status = agreement.get("status")
            # Assert the ->ACTIVE edge is legal (rejects PENDING/COMPLETED/
            # TERMINATED etc.), then enforce that resume's *only* legal source is
            # SUSPENDED — the ACTIVATING -> ACTIVE edge (schedule finalize) is not
            # a resume.
            state_machines.assert_transition(
                "benefit", previous_status, BenefitStatus.ACTIVE.value
            )
            if previous_status != BenefitStatus.SUSPENDED.value:
                raise InvalidTransition(
                    f"benefit can only be resumed from SUSPENDED, not "
                    f"{previous_status!r}"
                )

            # --- cumulative schedule-shift witness (specs/07 §7.8) -----------
            # Accumulate THIS suspension's whole-month duration onto any prior
            # shift so multiple suspend/resume cycles shift the remaining schedule
            # *cumulatively* — the post-commit shift task anchors installments to
            # this running TOTAL, not just the current suspension.
            # `scheduleShiftMonths` and `suspendedAt` are new agreement fields
            # absent from the frozen core/schema.py; Firestore is schemaless, so
            # writing them here is fine. `_months_between` (whole months, rounded
            # up) is reused from benefits.shift for one definition. This bump runs
            # exactly ONCE per resume — the reclaim path above skips it.
            from benefits.shift import _months_between

            this_shift_months = _months_between(suspended_from, resumed_at)
            total_shift_months = (
                int(agreement.get("scheduleShiftMonths", 0) or 0) + this_shift_months
            )

            # --- writes ------------------------------------------------------
            agreement_update = {
                "status": BenefitStatus.ACTIVE.value,
                "acceptingPayments": True,
                "suspendedReason": None,
                "suspendedAt": None,
                "scheduleShiftMonths": total_shift_months,
            }
            stamp_update(agreement_update, ctx.actor_id)
            txn.update(agreements.ref(client, agreement_id), agreement_update)

            if loan_id and loan is not None:
                loan_update = {"benefitStatus": BenefitStatus.ACTIVE.value}
                stamp_update(loan_update, ctx.actor_id)
                txn.update(loans.ref(client, loan_id), loan_update)

            servicing_events.append(
                txn,
                event_type="BENEFIT_RESUMED",
                entity_type=ENTITY_TYPE,
                entity_id=agreement_id,
                actor_id=ctx.actor_id,
                actor_role=ctx.actor_role,
                actor_name=ctx.actor_name,
                correlation_id=ctx.correlation_id,
                sequence=1,
                metadata={
                    "previousStatus": previous_status,
                    "newStatus": BenefitStatus.ACTIVE.value,
                    "suspendedFrom": (
                        suspended_from.isoformat()
                        if isinstance(suspended_from, datetime)
                        else suspended_from
                    ),
                    "resumedAt": resumed_at.isoformat(),
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
            "suspendedReason": None,
            "resumedAt": resumed_at.isoformat(),
            "correlationId": ctx.correlation_id,
        }
        # NB: idempotency.complete is deliberately NOT called inside this txn —
        # it runs only AFTER the post-commit shift succeeds (see below). Signal
        # the follow-up on both the fresh and reclaim path (not on a replay return).
        followup["suspended_from"] = suspended_from
        return result

    try:
        result = _run()
    except CommandError:
        raise
    except domain_errors.DomainError as exc:
        raise from_domain_error(exc) from exc

    # --- inline follow-up (AFTER the core txn commits) -----------------------
    # Re-date the remaining schedule forward, THEN complete the idempotency key.
    # Only when this call actually performed (or reclaimed) the resume — a replay
    # returns the stored result without populating `followup`. Order matters: the
    # key is completed only after the shift succeeds, so a crash/transient error
    # in the shift leaves the record PENDING and a same-key retry (after lease
    # expiry) reclaims and re-drives the shift — which is idempotent (it anchors
    # to the persisted scheduleShiftMonths; an already-shifted / zero-duration
    # schedule is a no-op).
    if "suspended_from" in followup:
        from benefits.shift import shift_schedule

        shift_schedule(
            client,
            agreement_id=agreement_id,
            ctx=ctx,
            suspended_from=followup["suspended_from"],
            resumed_at=resumed_at,
        )

        def _finalize_idem(txn: Any) -> None:
            idempotency.complete(txn, ctx.idempotency_key, result, client=client)

        transactional(client)(_finalize_idem)()
    return result


def terminate_benefit(
    *, agreement_id: str, ctx: CommandContext, client: Any = None
) -> dict:
    """Terminate a benefit agreement (specs/10 §10.3). Terminal; not reversible.

    ``ACTIVE``/``SUSPENDED``/``ACTIVATING → TERMINATED``; ``acceptingPayments =
    false``; ``loan.benefitStatus`` synced; one ``BENEFIT_TERMINATED`` event.
    After the transaction commits, the **cancel-future-contributions** task
    (:func:`contributions.lifecycle.cancel_future_contributions`) runs inline to
    cancel every future contribution and null the loan look-ahead (§10.4).
    """
    if client is None:
        from common.firestore import get_client

        client = get_client()

    ran_termination = {"done": False}  # True only on the real (non-replay) path

    @transactional(client)
    def _run(txn: Any) -> dict:
        # --- reads (all before any write) ------------------------------------
        agreement = _txn_get(txn, agreements.ref(client, agreement_id))
        if agreement is None:
            raise NotFound(f"benefit agreement {agreement_id!r} not found")
        loan_id = agreement.get("loanId")
        borrower_id = agreement.get("borrowerId")
        employer_id = agreement.get("employerId")
        loan = _txn_get(txn, loans.ref(client, loan_id)) if loan_id else None

        # --- idempotency: begin inside the txn -------------------------------
        outcome = idempotency.begin(
            txn,
            key=ctx.idempotency_key,
            operation=OPERATION_TERMINATE,
            request_hash=ctx.request_hash,
            entity_id=agreement_id,
            entity_type=ENTITY_TYPE,
            lease_ttl_seconds=ASYNC_LEASE_TTL_SECONDS,
            lease_owner=ctx.lease_owner,
            client=client,
        )
        if outcome.is_replay:
            return outcome.result or {}
        if outcome.is_in_progress:
            raise OperationInProgress(
                "benefit termination already in progress",
                retry_after=RETRY_AFTER_IN_PROGRESS,
                state={"agreementId": agreement_id, "status": agreement.get("status")},
            )
        if outcome.is_reuse:
            raise IdempotencyKeyReused(
                "idempotency key reused with a different request"
            )

        # --- transition {ACTIVE,SUSPENDED,ACTIVATING} -> TERMINATED ----------
        # All three source edges are legal (specs/06 §6.3); assert_transition
        # rejects PENDING/COMPLETED/TERMINATED with a precise 409.
        #
        # Reclaim-aware: on a same-key reclaim of an abandoned lease where the
        # agreement is ALREADY TERMINATED, the original call's core txn committed
        # this transition but crashed before the post-commit tail + completion.
        # Skip the transition + writes (already applied) and fall through to
        # RE-DRIVE the tail below. A genuine fresh key (reclaimed is False) on an
        # already-TERMINATED benefit still hits assert_transition -> 409.
        already_target = (
            agreement.get("status") == BenefitStatus.TERMINATED.value
        )
        if not (outcome.reclaimed and already_target):
            previous_status = agreement.get("status")
            state_machines.assert_transition(
                "benefit", previous_status, BenefitStatus.TERMINATED.value
            )

            # --- writes ------------------------------------------------------
            agreement_update = {
                "status": BenefitStatus.TERMINATED.value,
                "acceptingPayments": False,
            }
            stamp_update(agreement_update, ctx.actor_id)
            txn.update(agreements.ref(client, agreement_id), agreement_update)

            if loan_id and loan is not None:
                loan_update = {"benefitStatus": BenefitStatus.TERMINATED.value}
                stamp_update(loan_update, ctx.actor_id)
                txn.update(loans.ref(client, loan_id), loan_update)

            servicing_events.append(
                txn,
                event_type="BENEFIT_TERMINATED",
                entity_type=ENTITY_TYPE,
                entity_id=agreement_id,
                actor_id=ctx.actor_id,
                actor_role=ctx.actor_role,
                actor_name=ctx.actor_name,
                correlation_id=ctx.correlation_id,
                sequence=1,
                metadata={
                    "previousStatus": previous_status,
                    "newStatus": BenefitStatus.TERMINATED.value,
                },
                loan_id=loan_id,
                borrower_id=borrower_id,
                employer_id=employer_id,
                benefit_agreement_id=agreement_id,
            )

        result = {
            "agreementId": agreement_id,
            "status": BenefitStatus.TERMINATED.value,
            "acceptingPayments": False,
            "correlationId": ctx.correlation_id,
        }
        # NB: idempotency.complete is deliberately NOT called inside this txn. The
        # key is kept PENDING across the commit -> tail boundary and completed
        # only AFTER the post-commit tail succeeds (see below), closing the crash
        # gap where a completed key would replay past an un-run tail.
        ran_termination["done"] = True
        return result

    try:
        result = _run()
    except CommandError:
        raise
    except domain_errors.DomainError as exc:
        raise from_domain_error(exc) from exc

    # --- inline follow-up (AFTER the core txn commits) -----------------------
    # Cancel every future contribution + null the loan look-ahead (§10.4), THEN
    # complete the idempotency key. Order matters: the key is completed only after
    # the tail succeeds, so a crash/transient error in the tail leaves the record
    # PENDING and a same-key retry (after lease expiry) reclaims and re-drives the
    # tail — which is itself idempotent (an already-canceled schedule is a no-op).
    # Skipped on a replay (`done` stays False; the tail already ran originally).
    if ran_termination["done"]:
        from contributions.lifecycle import cancel_future_contributions

        cancel_future_contributions(
            client,
            agreement_id=agreement_id,
            ctx=ctx,
            reason=_TERMINATE_REASON,
        )

        def _finalize_idem(txn: Any) -> None:
            idempotency.complete(txn, ctx.idempotency_key, result, client=client)

        transactional(client)(_finalize_idem)()
    return result
