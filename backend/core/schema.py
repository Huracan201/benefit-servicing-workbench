"""TypedDict shapes for every Firestore document — specs/04 (+ read models, specs/05).

These are **documentation / type-hint helpers**, not runtime validators or
serializers. They give command handlers, projection tasks, and the seed script a
single, checkable source for field names, money units, and enum-valued fields so
copies of the schema don't drift across modules.

Conventions (specs/README "Global conventions"):
- Money is **integer US cents** (`int`, fields end ``Cents``); ``currency`` is
  fixed ``"USD"``.
- Timestamps are Firestore ``Timestamp`` (UTC instants) — modelled here as
  ``datetime``.
- Enum-valued fields use the shared enums from ``common.enums`` (the single
  source of the allowed string values). Because this module uses
  ``from __future__ import annotations``, every annotation is a *string* and is
  never evaluated at import time, so importing ``core.schema`` never requires
  ``common.enums`` at runtime — the enum names are for readers and type checkers.
- ``Required``/``NotRequired`` (PEP 655) distinguish nullable-but-present fields
  (typed ``Optional[...]``) from fields that may be absent from the document
  (``NotRequired[...]`` — e.g. the seed-only ``simulatedOutcome``).

Common-field exemptions and the seed-only field follow specs/04 §4.12a.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, NotRequired, Optional, TypedDict

if TYPE_CHECKING:  # pragma: no cover - type-checking only; not imported at runtime
    from common.enums import (
        BenefitStatus,
        ContributionStatus,
        EmployerStatus,
        EmploymentStatus,
        ExceptionStatus,
        ExceptionType,
        IdempotencyStatus,
        LoanStatus,
        PaymentAttemptStatus,
        PaymentFailureCode,
        Role,
        Severity,
    )

# Semantic aliases (documentation only).
Timestamp = datetime
Cents = int

__all__ = [
    "CommonFields",
    "Employer",
    "Borrower",
    "Loan",
    "BenefitAgreement",
    "ScheduledContribution",
    "PaymentAttempt",
    "ExceptionResolution",
    "OperationalException",
    "ServicingEvent",
    "IdempotencyKey",
    "User",
    "PortfolioSummaryCurrent",
    "PortfolioSummaryPeriod",
    "EmployerSummary",
    "EmployerSummaryPeriod",
    "LoanWorkbench",
]


# --------------------------------------------------------------------------- #
# Common fields (command-owned entity collections — specs/04 §4.12a)
# --------------------------------------------------------------------------- #


class CommonFields(TypedDict):
    """Fields present on every command-owned top-level document (specs/README).

    ``revision`` is a monotonic **audit counter**, not an optimistic-concurrency
    token (specs/README; optimistic concurrency is opt-in via ``expectedRevision``).
    """

    createdAt: Timestamp
    updatedAt: Timestamp
    createdBy: str
    updatedBy: str
    revision: int
    schemaVersion: int


# --------------------------------------------------------------------------- #
# Entity collections
# --------------------------------------------------------------------------- #


class Employer(CommonFields):
    """``employers/{employerId}`` — specs/04 §4.3.

    Denormalized rollups (``activeBorrowerCount``/``amountPaidCents``/
    ``remainingCommitmentCents``) are **projection-owned**, updated out of band —
    never written inside a payment transaction.
    """

    name: str
    industry: str
    status: "EmployerStatus"
    programName: str
    currency: Literal["USD"]
    totalCommitmentCents: Cents
    activeBorrowerCount: int
    amountPaidCents: Cents
    remainingCommitmentCents: Cents


class Borrower(CommonFields):
    """``borrowers/{borrowerId}`` — specs/04 §4.4.

    ``primaryLoanId``/``primaryBenefitAgreementId`` are non-authoritative
    convenience pointers; the canonical borrower→loan link is ``loan.borrowerId``.
    """

    firstName: str
    lastName: str
    displayName: str
    email: str
    employerId: str
    employerName: str  # live mirror
    employmentStatus: "EmploymentStatus"
    employmentStartDate: Timestamp
    employmentEndDate: Optional[Timestamp]
    primaryLoanId: Optional[str]
    primaryBenefitAgreementId: Optional[str]


class Loan(CommonFields):
    """``loans/{loanId}`` — specs/04 §4.5.

    ``benefitStatus``/``nextContributionDate``/``nextContributionAmountCents`` are
    live mirrors updated **in the same transaction** as the driving command.
    """

    borrowerId: str
    borrowerName: str  # live mirror
    employerId: str
    employerName: str  # live mirror
    externalLoanReference: str
    servicerName: str
    currency: Literal["USD"]
    originalPrincipalCents: Cents
    currentBalanceCents: Cents
    interestRateBasisPoints: int  # display only; no accrual in MVP
    loanStatus: "LoanStatus"
    benefitAgreementId: str
    benefitStatus: "BenefitStatus"  # live mirror of agreement.status (txn-synced)
    openExceptionCount: int
    nextContributionDate: Optional[Timestamp]
    nextContributionAmountCents: Optional[Cents]


class BenefitAgreement(CommonFields):
    """``benefitAgreements/{agreementId}`` — specs/04 §4.6.

    ``acceptingPayments`` is THE cancel-wins gate (specs/06 §6.7): ``false`` until
    activation finalize, ``false`` under SUSPENDED/TERMINATED, ``true`` on resume;
    checked by every process/retry precondition. Per-installment amounts are
    solved at generation so ``Σ == totalCommitmentCents`` (specs/07 §7.3).
    """

    borrowerId: str
    borrowerName: str
    employerId: str
    employerName: str
    loanId: str
    currency: Literal["USD"]
    totalCommitmentCents: Cents
    baseMonthlyContributionCents: Cents  # nominal; actual per-installment may differ
    termMonths: int
    startDate: Timestamp
    endDate: Timestamp
    amountPaidCents: Cents
    remainingCommitmentCents: Cents  # = total - paid, co-updated in txn
    status: "BenefitStatus"
    acceptingPayments: bool
    suspendedReason: Optional[Literal["LEAVE", "MANUAL"]]  # governs auto-resume
    scheduleGenerated: bool
    plannedInstallmentCount: int  # set at activation accept; generation target
    installmentsGenerated: int  # progress marker for resumable generation


class ScheduledContribution(CommonFields):
    """``scheduledContributions/{contributionId}`` — specs/04 §4.7.

    Deterministic ID: ``{agreementId}__{installmentNumber:03d}``.
    ``postedAmountCents`` may be < ``scheduledAmountCents`` on a balance-capped
    final installment. ``simulatedOutcome`` is **seed-only** (specs/04 §4.12a,
    specs/09 §9.5) and is never read by domain logic.
    """

    benefitAgreementId: str
    installmentNumber: int  # 1..termMonths
    borrowerId: str
    borrowerName: str  # live mirror
    employerId: str
    employerName: str
    loanId: str
    currency: Literal["USD"]
    scheduledDate: Timestamp  # 12:00 SYSTEM_TIMEZONE on due day
    periodLabel: str  # YYYY-MM in SYSTEM_TIMEZONE
    scheduledAmountCents: Cents
    status: "ContributionStatus"
    attemptCount: int
    currentAttemptId: Optional[str]
    currentExceptionId: Optional[str]  # deterministic exception id when failed
    lastAttemptAt: Optional[Timestamp]
    postedAt: Optional[Timestamp]
    postedAmountCents: Optional[Cents]
    failureCode: Optional["PaymentFailureCode"]
    failureReason: Optional[str]
    simulatedOutcome: NotRequired[str]  # seed-only; drives the payment simulator


class PaymentAttempt(TypedDict):
    """``scheduledContributions/{contributionId}/attempts/{attemptId}`` — specs/04 §4.8.

    Append-only, own lifecycle fields (no common fields — specs/04 §4.12a).
    Deterministic ID: ``{contributionId}__att_{attemptNumber:03d}``. Carries both
    the ``processorIdempotencyKey`` (sent to the adapter; deterministic
    ``pay_{contributionId}_att_{n:03d}``) and the ``commandIdempotencyKey`` that
    drove it — the distinction that makes crash-recovery safe (specs/08, specs/09).
    """

    contributionId: str
    loanId: str
    attemptNumber: int
    processorIdempotencyKey: str
    commandIdempotencyKey: str
    status: "PaymentAttemptStatus"
    reconcileAttempts: int  # indeterminate-sweep counter; STUCK at MAX_SWEEPS
    requestedAmountCents: Cents
    processorReference: Optional[str]
    failureCode: Optional["PaymentFailureCode"]
    failureReason: Optional[str]
    startedAt: Timestamp
    completedAt: Optional[Timestamp]


class ServicingEvent(TypedDict):
    """``servicingEvents/{eventId}`` — specs/04 §4.9. Immutable, append-only.

    Has ``createdAt`` only (specs/04 §4.12a). Mirrored, in the same transaction,
    to the most specific entity subcollection (loan → borrower → global-only).
    ``sequence`` is the monotonic-within-``correlationId`` ordering tiebreaker.
    Denormalized values here are **frozen snapshots** — never updated.
    """

    eventType: str  # canonical closed enum — specs/04 §4.9
    entityType: str
    entityId: str
    loanId: Optional[str]
    borrowerId: Optional[str]
    employerId: Optional[str]
    benefitAgreementId: Optional[str]
    actorType: Literal["USER", "SYSTEM"]
    actorId: str
    actorRole: Optional["Role"]
    actorName: str  # frozen snapshot
    correlationId: str
    sequence: int  # monotonic within a correlationId
    metadata: dict[str, Any]
    createdAt: Timestamp


class ExceptionResolution(TypedDict):
    """The ``resolution`` sub-object on a resolved exception — specs/04 §4.10."""

    resolvedBy: str
    note: str
    resolvedByEvent: str


class OperationalException(TypedDict):
    """``operationalExceptions/{exceptionId}`` — specs/04 §4.10.

    Own lifecycle timestamps, **no** ``revision`` (specs/04 §4.12a). Auto-created
    exceptions use the deterministic ID ``{entityId}__{exceptionType}`` and are
    upserted (``occurrenceCount``/``lastSeenAt`` bump on repeat). ``severityRank``
    is the numeric, sortable companion to ``severity`` (LOW=10…CRITICAL=40).
    """

    exceptionType: "ExceptionType"
    severity: "Severity"
    severityRank: int
    entityType: str
    entityId: str
    loanId: Optional[str]
    borrowerId: Optional[str]
    borrowerName: Optional[str]  # live mirror
    employerId: Optional[str]
    employerName: Optional[str]  # live mirror
    status: "ExceptionStatus"
    assignedTo: Optional[str]  # firebase uid
    occurrenceCount: int
    firstSeenAt: Timestamp
    lastSeenAt: Timestamp
    summary: str
    details: Optional[str]
    resolution: Optional[ExceptionResolution]
    createdAt: Timestamp
    updatedAt: Timestamp
    resolvedAt: Optional[Timestamp]


class IdempotencyKey(TypedDict):
    """``idempotencyKeys/{idempotencyKey}`` — specs/04 §4.11.

    ID is the client ``Idempotency-Key`` header verbatim. Created **inside** the
    state-transition transaction with a create-precondition; ``leaseOwner`` /
    ``leaseExpiresAt`` let a dead in-progress request be reclaimed (specs/08).
    """

    operation: str
    requestHash: str  # sha256 of normalized request body
    status: "IdempotencyStatus"
    entityType: str
    entityId: str
    leaseOwner: Optional[str]
    leaseExpiresAt: Optional[Timestamp]  # PENDING records only
    result: Optional[Any]  # prior response to replay, set on COMPLETED
    createdAt: Timestamp
    updatedAt: Timestamp
    completedAt: Optional[Timestamp]
    expiresAt: Timestamp  # retention TTL (Firestore TTL policy)


class User(CommonFields):
    """``users/{uid}`` — specs/04 §4.12.

    ``role`` mirrors the Firebase custom claim (claim is authoritative). Security
    rules deny all client writes to this doc, including self-write.
    """

    uid: str
    email: str
    displayName: str
    role: "Role"
    status: Literal["ACTIVE", "DISABLED"]


# --------------------------------------------------------------------------- #
# Read models / projections (specs/05) — derived, eventually consistent.
# Exempt from common fields; carry ``updatedAt`` only (specs/04 §4.12a).
# --------------------------------------------------------------------------- #


class PortfolioSummaryCurrent(TypedDict):
    """``portfolioSummaries/current`` — point-in-time totals (specs/05 §5.3)."""

    activeLoans: int
    activeBenefitAgreements: int
    benefitStatusCounts: dict[str, int]
    contributionStatusCounts: dict[str, int]
    openExceptionCount: int
    openExceptionSeverityCounts: dict[str, int]
    openExceptionTypeCounts: dict[str, int]
    remainingEmployerCommitmentCents: Cents  # NON-TERMINAL agreements only
    updatedAt: Timestamp


class PortfolioSummaryPeriod(TypedDict):
    """``portfolioSummaries/{YYYY-MM}`` — per-period flow metrics (specs/05 §5.3)."""

    periodLabel: str
    scheduledCents: Cents
    postedCents: Cents
    failedContributionCount: int
    updatedAt: Timestamp


class EmployerSummary(TypedDict):
    """``employerSummaries/{employerId}`` — point-in-time per employer (specs/05 §5.4)."""

    employerId: str
    employerName: str
    activeBorrowers: int
    activeBenefits: int
    monthlyObligationCents: Cents
    openExceptionCount: int
    totalCommitmentCents: Cents
    amountPaidCents: Cents
    remainingCommitmentCents: Cents  # non-terminal agreements only
    updatedAt: Timestamp


class EmployerSummaryPeriod(TypedDict):
    """``employerSummaries/{employerId}/periods/{YYYY-MM}`` (specs/05 §5.4)."""

    periodLabel: str
    postedCents: Cents
    failedCount: int
    updatedAt: Timestamp


class LoanWorkbench(TypedDict):
    """``loanWorkbenches/{loanId}`` — the widest live mirror (specs/05 §5.5).

    Everything to render a portfolio row and the account header without joins;
    updated by event-driven projection keyed on ``loanId`` and corrected by the
    scheduled rebuild.
    """

    loanId: str
    borrowerId: str
    borrowerName: str
    borrowerEmail: str
    employerId: str
    employerName: str
    employmentStatus: "EmploymentStatus"
    servicerName: str
    currentBalanceCents: Cents
    loanStatus: "LoanStatus"
    benefitAgreementId: str
    benefitStatus: "BenefitStatus"
    baseMonthlyContributionCents: Cents
    nextContributionDate: Optional[Timestamp]
    nextContributionAmountCents: Optional[Cents]
    openExceptionCount: int
    lastActivityAt: Optional[Timestamp]
    lastActivityType: Optional[str]
    updatedAt: Timestamp
