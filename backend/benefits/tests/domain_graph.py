"""Shared emulator-test fixtures for the Phase-2-part-2 domain commands.

Seeds a *minimal* real Firestore graph (employer / borrower / loan / benefit
agreement / a multi-installment schedule) using the exact document field names
from specs/04 — mirroring :mod:`payments.tests.fixtures` but for the
suspend / resume / terminate / employment-cascade / exception / note commands,
which need a *full* ACTIVE schedule rather than the single seeded contribution
the payment fixtures provide.

Every fixture is namespaced by a caller-supplied unique ``key`` so tests never
collide on the emulator (deterministic contribution ids derive from the
agreement id; idempotency keys are per-``CommandContext``). These helpers seed
the SERVICE layer directly (no HTTP), producing the same shapes the real
activate-benefit command would have produced.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from commands.base import CommandContext, request_hash
from common.enums import (
    BenefitStatus,
    ContributionStatus,
    EmployerStatus,
    EmploymentStatus,
    ExceptionStatus,
    LoanStatus,
    Severity,
)
from common.ids import contribution_id as _contribution_id
from common.periods import SYSTEM_TIMEZONE, period_label, scheduled_datetime
from repositories import (
    agreements,
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


def make_ctx(
    operation_path: str = "/domain/x",
    *,
    method: str = "POST",
) -> CommandContext:
    """A SERVICING_MANAGER command context with a fresh unique idempotency key."""
    return CommandContext(
        actor_id=ACTOR_ID,
        actor_role=ACTOR_ROLE,
        actor_name=ACTOR_NAME,
        idempotency_key=f"key_{uuid.uuid4().hex}",
        request_hash=request_hash(method, operation_path, None),
    )


class Graph:
    """Handles to a seeded fixture graph (ids + convenient re-reads)."""

    def __init__(self, client, key: str, *, start_date) -> None:
        self.client = client
        self.key = key
        self.start_date = start_date
        self.employer_id = f"emp_{key}"
        self.borrower_id = f"bor_{key}"
        self.loan_id = f"loan_{key}"
        self.agreement_id = f"ben_{key}"
        self.borrower_name = "Jordan Fixture"
        self.employer_name = "Fixture Corp"

    def contribution_id(self, installment: int) -> str:
        return _contribution_id(self.agreement_id, installment)

    # re-reads -------------------------------------------------------------
    def loan(self) -> Optional[dict]:
        return loans.get(self.client, self.loan_id)

    def borrower(self) -> Optional[dict]:
        return borrowers.get(self.client, self.borrower_id)

    def agreement(self) -> Optional[dict]:
        return agreements.get(self.client, self.agreement_id)

    def contribution(self, installment: int) -> Optional[dict]:
        return contributions.get(self.client, self.contribution_id(installment))

    def schedule(self) -> list[dict]:
        return contributions.list_for_agreement(self.client, self.agreement_id)

    def exception(self, exception_id: str) -> Optional[dict]:
        return operational_exceptions.get(self.client, exception_id)


def seed_active_graph(
    client,
    key: str,
    *,
    term_months: int = 4,
    scheduled_amount_cents: int = 100_000,
    benefit_status: BenefitStatus = BenefitStatus.ACTIVE,
    accepting_payments: bool = True,
    suspended_reason: Optional[str] = None,
    suspended_at: Any = None,
    employment_status: EmploymentStatus = EmploymentStatus.ACTIVE,
    loan_open_exception_count: int = 0,
    statuses: Optional[dict[int, ContributionStatus]] = None,
    exception_on: Optional[int] = None,
) -> Graph:
    """Seed one ACTIVE, fully-scheduled graph and return a :class:`Graph`.

    ``statuses`` maps ``installmentNumber -> ContributionStatus`` to override the
    default ``SCHEDULED`` for specific installments (e.g. seed a POSTED / FAILED /
    PROCESSING row). ``exception_on`` seeds an OPEN ``PAYMENT_FAILED`` exception
    pointed at that installment (used with a FAILED status) and links it as the
    contribution's ``currentExceptionId``.
    """
    statuses = statuses or {}
    start_date = datetime.now(SYSTEM_TIMEZONE).replace(
        hour=12, minute=0, second=0, microsecond=0
    ).date()
    g = Graph(client, key, start_date=start_date)

    total = scheduled_amount_cents * term_months
    end_dt = scheduled_datetime(start_date, term_months)

    _set(
        client,
        employers.ref(client, g.employer_id),
        {
            "name": g.employer_name,
            "industry": "Testing",
            "status": str(EmployerStatus.ACTIVE),
            "programName": "Fixture Program",
            "currency": CURRENCY,
            "totalCommitmentCents": total,
            "activeBorrowerCount": 1,
            "amountPaidCents": 0,
            "remainingCommitmentCents": total,
        },
    )
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
            "employmentEndDate": None,
            "primaryLoanId": g.loan_id,
            "primaryBenefitAgreementId": g.agreement_id,
        },
    )
    _set(
        client,
        loans.ref(client, g.loan_id),
        {
            "borrowerId": g.borrower_id,
            "borrowerName": g.borrower_name,
            "employerId": g.employer_id,
            "employerName": g.employer_name,
            "currency": CURRENCY,
            "originalPrincipalCents": total + 500_000,
            "currentBalanceCents": total + 500_000,
            "loanStatus": str(LoanStatus.ACTIVE),
            "benefitAgreementId": g.agreement_id,
            "benefitStatus": str(benefit_status),
            "openExceptionCount": loan_open_exception_count,
            "nextContributionDate": scheduled_datetime(start_date, 1),
            "nextContributionAmountCents": scheduled_amount_cents,
        },
    )
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
            "totalCommitmentCents": total,
            "baseMonthlyContributionCents": scheduled_amount_cents,
            "termMonths": term_months,
            "startDate": scheduled_datetime(start_date, 1),
            "endDate": end_dt,
            "amountPaidCents": 0,
            "remainingCommitmentCents": total,
            "status": str(benefit_status),
            "acceptingPayments": accepting_payments,
            "suspendedReason": suspended_reason,
            "suspendedAt": suspended_at,
            "scheduleGenerated": True,
            "plannedInstallmentCount": term_months,
            "installmentsGenerated": term_months,
        },
    )

    for n in range(1, term_months + 1):
        sched_dt = scheduled_datetime(start_date, n)
        status = statuses.get(n, ContributionStatus.SCHEDULED)
        exc_id = None
        if exception_on == n:
            exc_id = f"{g.contribution_id(n)}__PAYMENT_FAILED"
            _seed_open_exception(client, g, exc_id, n)
        contribution: dict[str, Any] = {
            "benefitAgreementId": g.agreement_id,
            "installmentNumber": n,
            "borrowerId": g.borrower_id,
            "borrowerName": g.borrower_name,
            "employerId": g.employer_id,
            "employerName": g.employer_name,
            "loanId": g.loan_id,
            "currency": CURRENCY,
            "scheduledDate": sched_dt,
            "periodLabel": period_label(sched_dt),
            "scheduledAmountCents": scheduled_amount_cents,
            "status": str(status),
            "attemptCount": 0,
            "currentAttemptId": None,
            "currentExceptionId": exc_id,
            "lastAttemptAt": None,
            "postedAt": None,
            "postedAmountCents": None,
            "failureCode": None,
            "failureReason": None,
        }
        _set(client, contributions.ref(client, g.contribution_id(n)), contribution)

    return g


def _seed_open_exception(client, g: Graph, exception_id: str, installment: int) -> None:
    """Seed an OPEN operationalExceptions row a terminate/cancel should dismiss."""
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


def count_events(client, *, event_type: str, agreement_id: str = None, loan_id: str = None) -> int:
    """Count servicingEvents of ``event_type`` scoped to an agreement or loan."""
    from repositories import refs

    query = client.collection(refs.SERVICING_EVENTS).where(
        filter=refs.field_filter("eventType", "==", event_type)
    )
    if agreement_id is not None:
        query = query.where(
            filter=refs.field_filter("benefitAgreementId", "==", agreement_id)
        )
    if loan_id is not None:
        query = query.where(filter=refs.field_filter("loanId", "==", loan_id))
    return len(refs.stream_to_dicts(query))
