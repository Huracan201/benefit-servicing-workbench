"""Financial invariants I1-I7 (specs/07 §7.2).

Pure functions over primitives (ints / strings), asserted inside the command
transaction before commit. A violation raises InvariantViolation, which the
command layer maps to 409 INVARIANT_VIOLATION.

Pure stdlib + common.errors — no django, no google.cloud, no float.
"""

from typing import Iterable

from common.errors import InvariantViolation


def check_loan_balance_non_negative(current_balance_cents: int) -> None:
    """I1: loan.currentBalanceCents >= 0 at all times."""
    if current_balance_cents < 0:
        raise InvariantViolation(
            "I1", f"loan balance negative: {current_balance_cents}"
        )


def check_amount_paid_within_commitment(
    amount_paid_cents: int, total_commitment_cents: int
) -> None:
    """I2: agreement.amountPaidCents <= agreement.totalCommitmentCents."""
    if amount_paid_cents > total_commitment_cents:
        raise InvariantViolation(
            "I2",
            f"amountPaid {amount_paid_cents} exceeds totalCommitment "
            f"{total_commitment_cents}",
        )


def check_remaining_commitment_consistent(
    remaining_commitment_cents: int,
    total_commitment_cents: int,
    amount_paid_cents: int,
) -> None:
    """I3: remaining == total - paid, always co-updated in the same write."""
    expected = total_commitment_cents - amount_paid_cents
    if remaining_commitment_cents != expected:
        raise InvariantViolation(
            "I3",
            f"remainingCommitment {remaining_commitment_cents} != "
            f"total-paid ({total_commitment_cents} - {amount_paid_cents} = {expected})",
        )


def check_posted_within_caps(
    posted_amount_cents: int,
    scheduled_amount_cents: int,
    loan_balance_cents: int,
    remaining_commitment_cents: int,
) -> None:
    """I4: postedAmount <= min(scheduled, loan balance, remaining commitment).

    Caps evaluated at post time. Posted must also be non-negative.
    """
    if posted_amount_cents < 0:
        raise InvariantViolation(
            "I4", f"postedAmount negative: {posted_amount_cents}"
        )
    cap = min(scheduled_amount_cents, loan_balance_cents, remaining_commitment_cents)
    if posted_amount_cents > cap:
        raise InvariantViolation(
            "I4",
            f"postedAmount {posted_amount_cents} exceeds cap min("
            f"scheduled={scheduled_amount_cents}, balance={loan_balance_cents}, "
            f"remaining={remaining_commitment_cents})={cap}",
        )


def check_schedule_sums_to_commitment(
    scheduled_amounts_cents: Iterable[int], total_commitment_cents: int
) -> None:
    """I5: Σ(scheduledAmountCents over all installments) == totalCommitmentCents.

    Established at generation (specs/07 §7.3).
    """
    total = sum(scheduled_amounts_cents)
    if total != total_commitment_cents:
        raise InvariantViolation(
            "I5",
            f"Σ(installments) {total} != totalCommitment {total_commitment_cents}",
        )


def check_posted_immutable(
    prior_status: str, prior_posted_amount_cents: int, new_posted_amount_cents: int
) -> None:
    """I6: a POSTED contribution is immutable — its postedAmount cannot change.

    Corrections are separate compensating events (specs/07 §7.5), never in-place
    edits. This guards an attempt to rewrite a posted contribution's amount.
    """
    if prior_status == "POSTED" and new_posted_amount_cents != prior_posted_amount_cents:
        raise InvariantViolation(
            "I6",
            f"POSTED contribution is immutable: postedAmount "
            f"{prior_posted_amount_cents} -> {new_posted_amount_cents}",
        )


def check_mutual_pointers(
    loan_benefit_agreement_id: str, agreement_loan_id: str,
    loan_id: str, agreement_id: str,
) -> None:
    """I7: referential integrity of mutual pointers (loan <-> agreement).

    loan.benefitAgreementId must point at the agreement and
    agreement.loanId must point back at the loan.
    """
    if loan_benefit_agreement_id != agreement_id:
        raise InvariantViolation(
            "I7",
            f"loan.benefitAgreementId {loan_benefit_agreement_id!r} != "
            f"agreementId {agreement_id!r}",
        )
    if agreement_loan_id != loan_id:
        raise InvariantViolation(
            "I7",
            f"agreement.loanId {agreement_loan_id!r} != loanId {loan_id!r}",
        )
