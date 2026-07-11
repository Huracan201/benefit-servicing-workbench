"""Domain enumerations (exact string values from specs/README + specs/06).

Pure stdlib. All enums subclass StrEnum (Python 3.11+): a member IS its canonical
Firestore string, so `.value`, `==` and `f"{member}"` all yield that string.
"""

from enum import StrEnum


class ContributionStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    PROCESSING = "PROCESSING"
    POSTED = "POSTED"
    FAILED = "FAILED"
    RETRY_PENDING = "RETRY_PENDING"
    CANCELED = "CANCELED"


class PaymentAttemptStatus(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class BenefitStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    ACTIVATING = "ACTIVATING"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    COMPLETED = "COMPLETED"
    TERMINATED = "TERMINATED"


class EmploymentStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    LEAVE = "LEAVE"
    TERMINATED = "TERMINATED"


class LoanStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAID_OFF = "PAID_OFF"
    DELINQUENT = "DELINQUENT"
    CLOSED = "CLOSED"


class EmployerStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class ExceptionStatus(StrEnum):
    OPEN = "OPEN"
    IN_REVIEW = "IN_REVIEW"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class ExceptionType(StrEnum):
    PAYMENT_FAILED = "PAYMENT_FAILED"
    EMPLOYMENT_VERIFICATION_REQUIRED = "EMPLOYMENT_VERIFICATION_REQUIRED"
    LOAN_BALANCE_MISMATCH = "LOAN_BALANCE_MISMATCH"
    BENEFIT_CONFIGURATION_ERROR = "BENEFIT_CONFIGURATION_ERROR"
    SERVICER_SYNC_FAILURE = "SERVICER_SYNC_FAILURE"
    PAYMENT_STUCK_PROCESSING = "PAYMENT_STUCK_PROCESSING"
    TASK_FAILED = "TASK_FAILED"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PaymentFailureCode(StrEnum):
    SERVICER_UNAVAILABLE = "SERVICER_UNAVAILABLE"
    SERVICER_TIMEOUT = "SERVICER_TIMEOUT"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    ACCOUNT_FROZEN = "ACCOUNT_FROZEN"
    INVALID_ACCOUNT = "INVALID_ACCOUNT"
    NOT_SUBMITTED = "NOT_SUBMITTED"


class IdempotencyStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Role(StrEnum):
    OPERATIONS_USER = "OPERATIONS_USER"
    SERVICING_MANAGER = "SERVICING_MANAGER"
    ADMINISTRATOR = "ADMINISTRATOR"


# severityRank values (specs/README ENUMS). Keyed by both the enum and its string.
SEVERITY_RANK = {
    Severity.LOW: 10,
    Severity.MEDIUM: 20,
    Severity.HIGH: 30,
    Severity.CRITICAL: 40,
}


def severity_rank(severity) -> int:
    """Return the numeric severityRank for a Severity or its string value."""
    return SEVERITY_RANK[Severity(severity)]


# Role hierarchy, lowest privilege first: OPERATIONS_USER < SERVICING_MANAGER < ADMINISTRATOR.
ROLE_ORDER = [
    Role.OPERATIONS_USER,
    Role.SERVICING_MANAGER,
    Role.ADMINISTRATOR,
]


def role_at_least(actual, required) -> bool:
    """True if `actual` role is at or above `required` in the hierarchy."""
    return ROLE_ORDER.index(Role(actual)) >= ROLE_ORDER.index(Role(required))
