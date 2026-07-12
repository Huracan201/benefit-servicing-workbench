"""Shared emulator-test fixtures for the payment-processing command tests.

Seeds the *minimal* real Firestore graph a payment command needs — one
employer / borrower / loan / benefit agreement / scheduled contribution — using
the exact document field names from specs/04 (mirrored from ``seed.builder``).

Every fixture is namespaced by a caller-supplied unique ``key`` so tests never
collide on the emulator: the deterministic contribution / attempt / processor /
exception ids all derive from the agreement id, and the idempotency keys are
caller-chosen, so a per-test ``uuid`` suffix isolates each run completely — no
cross-test teardown of the emulator is required.

These helpers exercise the SERVICE layer directly (no HTTP), so they seed the
same shapes the real activate-benefit command would have produced.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from common.enums import (
    BenefitStatus,
    ContributionStatus,
    EmployerStatus,
    EmploymentStatus,
    ExceptionStatus,
    LoanStatus,
    PaymentAttemptStatus,
    Severity,
)
from common.ids import attempt_id as _attempt_id
from common.ids import contribution_id as _contribution_id
from common.ids import processor_key as _processor_key
from common.periods import SYSTEM_TIMEZONE, period_label, scheduled_datetime
from commands.base import CommandContext, request_hash
from repositories import (
    agreements,
    attempts,
    borrowers,
    contributions,
    employers,
    loans,
    operational_exceptions,
    stamp_create,
)

ACTOR_ID = "user_test_manager"
ACTOR_ROLE = "SERVICING_MANAGER"
ACTOR_NAME = "Test Servicing Manager"
CURRENCY = "USD"


def unique_key(prefix: str = "t") -> str:
    """A short unique namespace so deterministic ids never collide on the emulator."""
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _set(client, ref, data: dict[str, Any]) -> None:
    stamp_create(data, ACTOR_ID)
    ref.set(data)


class Graph:
    """Handles to a seeded fixture graph (ids + convenient re-reads)."""

    def __init__(self, client, key: str) -> None:
        self.client = client
        self.key = key
        self.employer_id = f"emp_{key}"
        self.borrower_id = f"bor_{key}"
        self.loan_id = f"loan_{key}"
        self.agreement_id = f"ben_{key}"
        self.borrower_name = "Jordan Fixture"
        self.employer_name = "Fixture Corp"

    def contribution_id(self, installment: int = 1) -> str:
        return _contribution_id(self.agreement_id, installment)

    def attempt_id(self, installment: int, attempt_number: int) -> str:
        return _attempt_id(self.contribution_id(installment), attempt_number)

    def processor_key(self, installment: int, attempt_number: int) -> str:
        return _processor_key(self.contribution_id(installment), attempt_number)

    # re-reads -------------------------------------------------------------
    def loan(self) -> Optional[dict]:
        return loans.get(self.client, self.loan_id)

    def agreement(self) -> Optional[dict]:
        return agreements.get(self.client, self.agreement_id)

    def contribution(self, installment: int = 1) -> Optional[dict]:
        return contributions.get(self.client, self.contribution_id(installment))

    def attempts_for(self, installment: int = 1) -> list[dict]:
        return attempts.list_for_contribution(self.client, self.contribution_id(installment))

    def exception(self, exception_id: str) -> Optional[dict]:
        return operational_exceptions.get(self.client, exception_id)


def seed_payment_graph(
    client,
    key: str,
    *,
    total_commitment_cents: int = 1_000_000,
    scheduled_amount_cents: int = 100_000,
    amount_paid_cents: int = 0,
    loan_balance_cents: int = 2_000_000,
    open_exception_count: int = 0,
    accepting_payments: bool = True,
    benefit_status: BenefitStatus = BenefitStatus.ACTIVE,
    loan_status: LoanStatus = LoanStatus.ACTIVE,
    employment_status: EmploymentStatus = EmploymentStatus.ACTIVE,
    simulated_outcome: Optional[str] = None,
    contribution_status: ContributionStatus = ContributionStatus.SCHEDULED,
    current_exception_id: Optional[str] = None,
    installment: int = 1,
    with_open_exception: bool = False,
) -> Graph:
    """Seed one contribution-ready graph and return a :class:`Graph` of handles.

    Defaults describe the canonical happy path: an ACTIVE, payments-accepting
    agreement whose loan has ample balance and a single SCHEDULED contribution.
    Keyword overrides script the failure / recovery / exception variants.
    """
    g = Graph(client, key)
    remaining = total_commitment_cents - amount_paid_cents

    # employer --------------------------------------------------------------
    _set(
        client,
        employers.ref(client, g.employer_id),
        {
            "name": g.employer_name,
            "industry": "Testing",
            "status": str(EmployerStatus.ACTIVE),
            "programName": "Fixture Program",
            "currency": CURRENCY,
            "totalCommitmentCents": total_commitment_cents,
            "activeBorrowerCount": 1,
            "amountPaidCents": amount_paid_cents,
            "remainingCommitmentCents": remaining,
        },
    )

    # borrower --------------------------------------------------------------
    _set(
        client,
        borrowers.ref(client, g.borrower_id),
        {
            "firstName": "Jordan",
            "lastName": "Fixture",
            "displayName": g.borrower_name,
            "email": f"{key}@example.com",
            "employerId": g.employer_id,
            "employerName": g.employer_name,
            "employmentStatus": str(employment_status),
            "primaryLoanId": g.loan_id,
            "primaryBenefitAgreementId": g.agreement_id,
        },
    )

    # loan ------------------------------------------------------------------
    _set(
        client,
        loans.ref(client, g.loan_id),
        {
            "borrowerId": g.borrower_id,
            "borrowerName": g.borrower_name,
            "employerId": g.employer_id,
            "employerName": g.employer_name,
            "currency": CURRENCY,
            "originalPrincipalCents": loan_balance_cents + amount_paid_cents,
            "currentBalanceCents": loan_balance_cents,
            "loanStatus": str(loan_status),
            "benefitAgreementId": g.agreement_id,
            "benefitStatus": str(benefit_status),
            "openExceptionCount": open_exception_count,
            "nextContributionDate": None,
            "nextContributionAmountCents": scheduled_amount_cents,
        },
    )

    # agreement -------------------------------------------------------------
    start_dt = scheduled_datetime(datetime.now(SYSTEM_TIMEZONE).date(), 1)
    _set(
        client,
        agreements.ref(client, g.agreement_id),
        {
            "borrowerId": g.borrower_id,
            "borrowerName": g.borrower_name,
            "employerId": g.employer_id,
            "employerName": g.employer_name,
            "loanId": g.loan_id,
            "currency": CURRENCY,
            "totalCommitmentCents": total_commitment_cents,
            "termMonths": 10,
            "amountPaidCents": amount_paid_cents,
            "remainingCommitmentCents": remaining,
            "status": str(benefit_status),
            "acceptingPayments": accepting_payments,
            "suspendedReason": None,
            "scheduleGenerated": True,
            "plannedInstallmentCount": 10,
            "installmentsGenerated": 10,
        },
    )

    # optional pre-existing open exception (to test resolve-on-post) --------
    if with_open_exception and current_exception_id is None:
        current_exception_id = f"{g.contribution_id(installment)}__PAYMENT_FAILED"
    if with_open_exception:
        _seed_open_exception(client, g, current_exception_id, installment)

    # scheduled contribution ------------------------------------------------
    sched_dt = scheduled_datetime(datetime.now(SYSTEM_TIMEZONE).date(), installment)
    contribution: dict[str, Any] = {
        "benefitAgreementId": g.agreement_id,
        "installmentNumber": installment,
        "borrowerId": g.borrower_id,
        "borrowerName": g.borrower_name,
        "employerId": g.employer_id,
        "employerName": g.employer_name,
        "loanId": g.loan_id,
        "currency": CURRENCY,
        "scheduledDate": sched_dt,
        "periodLabel": period_label(sched_dt),
        "scheduledAmountCents": scheduled_amount_cents,
        "status": str(contribution_status),
        "attemptCount": 0,
        "currentAttemptId": None,
        "currentExceptionId": current_exception_id,
        "lastAttemptAt": None,
        "postedAt": None,
        "postedAmountCents": None,
        "failureCode": None,
        "failureReason": None,
    }
    if simulated_outcome is not None:
        contribution["simulatedOutcome"] = simulated_outcome
    _set(client, contributions.ref(client, g.contribution_id(installment)), contribution)

    return g


def _seed_open_exception(client, g: Graph, exception_id: str, installment: int) -> None:
    """Seed an OPEN operationalExceptions row that a successful post should resolve."""
    from datetime import timezone

    now = datetime.now(timezone.utc)
    operational_exceptions.ref(client, exception_id).set(
        {
            "exceptionType": "PAYMENT_FAILED",
            "severity": str(Severity.HIGH),
            "severityRank": 2,
            "entityType": "scheduledContribution",
            "entityId": g.contribution_id(installment),
            "loanId": g.loan_id,
            "borrowerId": g.borrower_id,
            "borrowerName": g.borrower_name,
            "employerId": g.employer_id,
            "employerName": g.employer_name,
            "summary": "Seeded open exception",
            "details": "prior failure",
            "status": str(ExceptionStatus.OPEN),
            "assignedTo": None,
            "occurrenceCount": 1,
            "firstSeenAt": now,
            "lastSeenAt": now,
            "resolution": None,
            "resolvedAt": None,
            "createdAt": now,
            "updatedAt": now,
        }
    )


def seed_inflight_attempt(
    client,
    g: Graph,
    *,
    installment: int = 1,
    attempt_number: int = 1,
) -> None:
    """Mutate a seeded contribution into the mid-flight PROCESSING/STARTED shape.

    Models a driver that opened Phase 1 (attempt STARTED, contribution
    PROCESSING) and then crashed before Phase 3 — exactly the state the
    reconciliation sweeper must recover.
    """
    cid = g.contribution_id(installment)
    att_id = g.attempt_id(installment, attempt_number)
    proc_key = g.processor_key(installment, attempt_number)
    scheduled = int(g.contribution(installment)["scheduledAmountCents"])

    attempts.ref(client, cid, attempt_number).set(
        {
            "contributionId": cid,
            "loanId": g.loan_id,
            "attemptNumber": attempt_number,
            "processorIdempotencyKey": proc_key,
            "commandIdempotencyKey": f"cmd_{g.key}",
            "status": str(PaymentAttemptStatus.STARTED),
            "reconcileAttempts": 0,
            "requestedAmountCents": scheduled,
            "processorReference": None,
            "failureCode": None,
            "failureReason": None,
            "startedAt": datetime.now(SYSTEM_TIMEZONE),
            "completedAt": None,
        }
    )
    contributions.ref(client, cid).update(
        {
            "status": str(ContributionStatus.PROCESSING),
            "attemptCount": attempt_number,
            "currentAttemptId": att_id,
        }
    )


def make_ctx(
    idempotency_key: str,
    *,
    method: str = "POST",
    path: str = "/contributions/x/process",
    lease_owner: Optional[str] = None,
) -> CommandContext:
    """A SERVICING_MANAGER command context with a deterministic request hash."""
    return CommandContext(
        actor_id=ACTOR_ID,
        actor_role=ACTOR_ROLE,
        actor_name=ACTOR_NAME,
        idempotency_key=idempotency_key,
        request_hash=request_hash(method, path, None),
        lease_owner=lease_owner or f"run_{uuid.uuid4().hex}",
    )
