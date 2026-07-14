"""Money math — integer US cents only (specs/07). No float anywhere.

Pure stdlib; safe to import with zero third-party dependencies.
"""

from typing import List

# Upper bound on a benefit's schedule length (installments == term_months).
#
# This is a WRITE-BUDGET bound, not a product rule: the loan-payoff path cancels
# every still-SCHEDULED installment inside ONE finalize transaction (payments/
# service.py), at ~3 writes per installment (1 contribution update + 2 event
# writes — global + mirror). Firestore caps a transaction at 500 writes, so with
# the finalize's own ~10-write overhead the inline cancel stays safe only while
# 3·N + 10 <= 500, i.e. N <= ~163. 120 months (10 years) sits comfortably under
# that and covers any realistic employer student-loan repayment term (the demo
# seeds 36). Enforced at activation (benefits.services) as a clean 422 before any
# money moves; re-asserted here as defense-in-depth so no schedule can be solved
# above the bound. If a longer term is ever required, the inline cancel must move
# to the async cancel-future-contributions task (specs/14) first.
MAX_TERM_MONTHS = 120


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
    if term_months > MAX_TERM_MONTHS:
        # Defense-in-depth; activation rejects this first as a 422 (see the
        # MAX_TERM_MONTHS note — it bounds the inline payoff-cancel write budget).
        raise ValueError(f"term_months must be <= {MAX_TERM_MONTHS}")
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
