"""Money math — integer US cents only (specs/07). No float anywhere.

Pure stdlib; safe to import with zero third-party dependencies.
"""

from typing import List


def solve_schedule(total_commitment_cents: int, term_months: int) -> List[int]:
    """Solve installment amounts so they sum EXACTLY to the commitment.

    specs/07 §7.3 (the residual / rounding rule):

        base      = total // term
        remainder = total - base * term        # 0 <= remainder < term
        installments 1..(term-1) = base
        installment  term        = base + remainder

    The residual is placed on the FINAL installment so Σ == total (invariant I5).

    Worked example: solve_schedule(3_000_000, 36) -> [83333]*35 + [83345],
    which sums to 3_000_000.
    """
    if not isinstance(total_commitment_cents, int) or isinstance(total_commitment_cents, bool):
        raise TypeError("total_commitment_cents must be an int (cents)")
    if not isinstance(term_months, int) or isinstance(term_months, bool):
        raise TypeError("term_months must be an int")
    if term_months < 1:
        raise ValueError("term_months must be >= 1")
    if total_commitment_cents < 0:
        raise ValueError("total_commitment_cents must be >= 0")

    base = total_commitment_cents // term_months
    remainder = total_commitment_cents - base * term_months  # 0 <= remainder < term
    schedule = [base] * (term_months - 1) + [base + remainder]
    return schedule


def cap_posted(
    scheduled_amount_cents: int,
    loan_balance_cents: int,
    remaining_commitment_cents: int,
) -> int:
    """Balance/commitment-capped posted amount (specs/07 §7.4, invariant I4).

        postedAmountCents = min(scheduledAmountCents,
                                loan.currentBalanceCents,
                                agreement.remainingCommitmentCents)
    """
    for name, val in (
        ("scheduled_amount_cents", scheduled_amount_cents),
        ("loan_balance_cents", loan_balance_cents),
        ("remaining_commitment_cents", remaining_commitment_cents),
    ):
        if not isinstance(val, int) or isinstance(val, bool):
            raise TypeError(f"{name} must be an int (cents)")
        if val < 0:
            raise ValueError(f"{name} must be >= 0")
    return min(
        scheduled_amount_cents,
        loan_balance_cents,
        remaining_commitment_cents,
    )


def dollars(amount_cents: int) -> str:
    """Format integer cents as a USD string, e.g. dollars(83345) -> '$833.45'.

    Presentation only — never used in the money path. Handles negatives.
    """
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool):
        raise TypeError("amount_cents must be an int (cents)")
    sign = "-" if amount_cents < 0 else ""
    whole, frac = divmod(abs(amount_cents), 100)
    return f"{sign}${whole:,}.{frac:02d}"
