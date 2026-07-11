"""Deterministic ID formatters (specs/README — Identifiers).

"Create exactly once" IDs so a duplicate create fails on a document-existence
precondition rather than racing. Pure stdlib.

Canonical formats:
  contribution : {agreementId}__{installmentNumber:03d}
  attempt      : {contributionId}__att_{attemptNumber:03d}
  auto exception: {entityId}__{exceptionType}
  processor key : pay_{contributionId}_att_{attemptNumber:03d}
"""


def _require_positive(name: str, n: int) -> None:
    if not isinstance(n, int) or isinstance(n, bool):
        raise TypeError(f"{name} must be an int")
    if n < 1:
        raise ValueError(f"{name} must be >= 1")


def contribution_id(agreement_id: str, installment_number: int) -> str:
    """f'{agreementId}__{installmentNumber:03d}'  e.g. ben_jordan_001__004."""
    _require_positive("installment_number", installment_number)
    return f"{agreement_id}__{installment_number:03d}"


def attempt_id(contribution_id: str, attempt_number: int) -> str:
    """f'{contributionId}__att_{attemptNumber:03d}'."""
    _require_positive("attempt_number", attempt_number)
    return f"{contribution_id}__att_{attempt_number:03d}"


def exception_id(entity_id: str, exception_type) -> str:
    """f'{entityId}__{exceptionType}' — deterministic auto-exception dedupe key.

    `exception_type` may be an ExceptionType enum or its string value.
    """
    value = getattr(exception_type, "value", exception_type)
    return f"{entity_id}__{value}"


def processor_key(contribution_id: str, attempt_number: int) -> str:
    """f'pay_{contributionId}_att_{attemptNumber:03d}' — payment adapter idempotency key."""
    _require_positive("attempt_number", attempt_number)
    return f"pay_{contribution_id}_att_{attempt_number:03d}"
