"""Read-model recompute engine (specs/05 §5.2 mechanism 2/3).

The single source-derivation layer behind **both** the event-driven
``update-projection`` task and the scheduled ``rebuild-summaries`` job. Each
``recompute_*`` reads the **source** collections (``benefitAgreements`` /
``scheduledContributions`` / ``loans`` / ``borrowers`` / ``operationalExceptions``)
with bounded queries and returns the **fully-derived** doc value — it never folds
an event's delta into a stored value.

Why recompute-from-source (not fold-a-delta): Cloud Tasks deliver at least once. A
redelivered *increment* double-counts; a redelivered *recompute* converges to the
same source-derived value, so redelivery is byte-identical, N coalesced tasks for
one key all write the same value, and the event-driven task and the scheduled
rebuild agree by construction (they run the same function). A projection is
**never** read to make a financial decision — commands read source entities in
their own transaction (specs/05 §5.1).

Normative derivation rules encoded here:
- **Commitment rollups** (``remainingEmployerCommitmentCents`` /
  ``remainingCommitmentCents``) sum only **non-terminal** agreements
  (``ACTIVE``/``SUSPENDED``/``ACTIVATING``) — a terminal agreement's residual
  commitment is money that will never move (specs/05 §5.3, specs/07 §7.7).
- **All period metrics bucket by the contribution's ``periodLabel``**, never the
  wall-clock month of the posting event: a July installment that posts on Aug 2
  counts in July's ``postedCents`` (specs/05 §5.3).
"""

from __future__ import annotations

from typing import Any, Optional

from common.enums import (
    BenefitStatus,
    ContributionStatus,
    EmploymentStatus,
    ExceptionStatus,
    LoanStatus,
    Severity,
)
from repositories import (
    agreements as agreements_repo,
    borrowers as borrowers_repo,
    contributions as contributions_repo,
    employer_summaries as employer_summaries_repo,
    employers as employers_repo,
    loan_workbenches as loan_workbenches_repo,
    loans as loans_repo,
    portfolio_summaries as portfolio_summaries_repo,
    refs,
)

# --------------------------------------------------------------------------- #
# Key kinds (JSON-serializable; flow through the update-projection payload)
# --------------------------------------------------------------------------- #
# A projection Key is ``{"kind": <KIND>, "id": <employerId|loanId|None>,
# "period": <"YYYY-MM"|None>}``. The fan-out (:mod:`projections.fanout`) builds
# them from an event; the task/rebuild dispatch each through :func:`apply_key`.
KIND_PORTFOLIO_CURRENT = "portfolio_current"
KIND_PORTFOLIO_PERIOD = "portfolio_period"
KIND_EMPLOYER = "employer"
KIND_EMPLOYER_PERIOD = "employer_period"
KIND_LOAN_WORKBENCH = "loan_workbench"

KEY_KINDS = frozenset(
    {
        KIND_PORTFOLIO_CURRENT,
        KIND_PORTFOLIO_PERIOD,
        KIND_EMPLOYER,
        KIND_EMPLOYER_PERIOD,
        KIND_LOAN_WORKBENCH,
    }
)

# Commitment rollups count only these (non-terminal) agreement statuses
# (specs/05 §5.3, specs/07 §7.7).
NON_TERMINAL_AGREEMENT_STATUSES = frozenset(
    {
        str(BenefitStatus.ACTIVE),
        str(BenefitStatus.SUSPENDED),
        str(BenefitStatus.ACTIVATING),
    }
)

# "Open" = not yet resolved/dismissed (specs/05 §5.3 open-exception tiles).
OPEN_EXCEPTION_STATUSES = frozenset(
    {str(ExceptionStatus.OPEN), str(ExceptionStatus.IN_REVIEW)}
)


# --------------------------------------------------------------------------- #
# Key constructors (convenience for fan-out / rebuild / tests)
# --------------------------------------------------------------------------- #
def portfolio_current_key() -> dict[str, Any]:
    return {"kind": KIND_PORTFOLIO_CURRENT, "id": None, "period": None}


def portfolio_period_key(period: str) -> dict[str, Any]:
    return {"kind": KIND_PORTFOLIO_PERIOD, "id": None, "period": period}


def employer_key(employer_id: str) -> dict[str, Any]:
    return {"kind": KIND_EMPLOYER, "id": employer_id, "period": None}


def employer_period_key(employer_id: str, period: str) -> dict[str, Any]:
    return {"kind": KIND_EMPLOYER_PERIOD, "id": employer_id, "period": period}


def loan_workbench_key(loan_id: str) -> dict[str, Any]:
    return {"kind": KIND_LOAN_WORKBENCH, "id": loan_id, "period": None}


# --------------------------------------------------------------------------- #
# Bounded source-stream helpers
# --------------------------------------------------------------------------- #
def _stream(query) -> list[dict[str, Any]]:
    """Materialize a firestore query into dict-with-id documents."""
    return [refs.snapshot_to_dict(snap) for snap in query.stream()]


def _stream_collection(client, collection: str) -> list[dict[str, Any]]:
    """Stream a whole top-level collection as dict-with-id documents."""
    return _stream(client.collection(collection))


def _where(client, collection: str, field: str, value) -> list[dict[str, Any]]:
    """Stream ``collection where field == value`` (single-field, auto-indexed)."""
    return _stream(
        client.collection(collection).where(
            filter=refs.field_filter(field, "==", value)
        )
    )


# --------------------------------------------------------------------------- #
# portfolioSummaries/current — point-in-time totals (specs/05 §5.3)
# --------------------------------------------------------------------------- #
def recompute_portfolio_current(client) -> dict[str, Any]:
    """Derive ``portfolioSummaries/current`` from source (no ``updatedAt`` —
    the gateway stamps server time)."""
    loans = _stream_collection(client, refs.LOANS)
    active_loans = sum(
        1 for loan in loans if loan.get("loanStatus") == str(LoanStatus.ACTIVE)
    )

    agreements = _stream_collection(client, refs.BENEFIT_AGREEMENTS)
    benefit_status_counts: dict[str, int] = {}
    active_benefit_agreements = 0
    remaining_commitment_cents = 0
    for agreement in agreements:
        status = agreement.get("status")
        benefit_status_counts[status] = benefit_status_counts.get(status, 0) + 1
        if status == str(BenefitStatus.ACTIVE):
            active_benefit_agreements += 1
        if status in NON_TERMINAL_AGREEMENT_STATUSES:
            remaining_commitment_cents += int(
                agreement.get("remainingCommitmentCents") or 0
            )

    contribution_status_counts: dict[str, int] = {}
    for contribution in _stream_collection(client, refs.SCHEDULED_CONTRIBUTIONS):
        status = contribution.get("status")
        contribution_status_counts[status] = (
            contribution_status_counts.get(status, 0) + 1
        )

    # Open exceptions: count + severity/type breakdowns. Severity is zero-inited
    # over the enum (the dashboard renders every tile — specs/05 §5.3); type is
    # observed-only (open-ended set of relevant types).
    open_exception_count = 0
    severity_counts: dict[str, int] = {str(s): 0 for s in Severity}
    type_counts: dict[str, int] = {}
    for exc in _stream_collection(client, refs.OPERATIONAL_EXCEPTIONS):
        if exc.get("status") not in OPEN_EXCEPTION_STATUSES:
            continue
        open_exception_count += 1
        sev = exc.get("severity")
        if sev in severity_counts:
            severity_counts[sev] += 1
        exc_type = exc.get("exceptionType")
        type_counts[exc_type] = type_counts.get(exc_type, 0) + 1

    return {
        "activeLoans": active_loans,
        "activeBenefitAgreements": active_benefit_agreements,
        "benefitStatusCounts": benefit_status_counts,
        "contributionStatusCounts": contribution_status_counts,
        "openExceptionCount": open_exception_count,
        "openExceptionSeverityCounts": severity_counts,
        "openExceptionTypeCounts": type_counts,
        "remainingEmployerCommitmentCents": remaining_commitment_cents,
    }


# --------------------------------------------------------------------------- #
# portfolioSummaries/{YYYY-MM} — per-period flow metrics (specs/05 §5.3)
# --------------------------------------------------------------------------- #
def recompute_portfolio_period(client, period: str) -> dict[str, Any]:
    """Derive ``portfolioSummaries/{period}`` from source contributions bucketed
    by ``periodLabel`` (never wall-clock posting month)."""
    contributions = _where(
        client, refs.SCHEDULED_CONTRIBUTIONS, "periodLabel", period
    )
    scheduled_cents = 0
    posted_cents = 0
    failed_count = 0
    for c in contributions:
        status = c.get("status")
        # scheduledCents = the period's live scheduled obligation (canceled
        # installments are no longer owed). NB (specs/05 §5.3, finding 3): this field
        # is refreshed by the scheduled rebuild-summaries, NOT by per-event fanout —
        # a single BENEFIT_ACTIVATED / FUTURE_CONTRIBUTIONS_CANCELED spans many
        # periods the event can't enumerate, so projections.fanout deliberately omits
        # the portfolio_period / employer_period key for those events (see the NB in
        # fanout._KINDS_BY_EVENT). postedCents / failedCount below stay event-driven.
        if status != str(ContributionStatus.CANCELED):
            scheduled_cents += int(c.get("scheduledAmountCents") or 0)
        if status == str(ContributionStatus.POSTED):
            posted_cents += int(c.get("postedAmountCents") or 0)
        elif status == str(ContributionStatus.FAILED):
            failed_count += 1

    return {
        "periodLabel": period,
        "scheduledCents": scheduled_cents,
        "postedCents": posted_cents,
        "failedContributionCount": failed_count,
    }


# --------------------------------------------------------------------------- #
# employerSummaries/{employerId} — point-in-time per employer (specs/05 §5.4)
# --------------------------------------------------------------------------- #
def recompute_employer(client, employer_id: str) -> Optional[dict[str, Any]]:
    """Derive ``employerSummaries/{employer_id}`` from source, or ``None`` if the
    employer no longer exists."""
    employer = employers_repo.get(client, employer_id)
    if employer is None:
        return None

    borrowers = borrowers_repo.list_for_employer(client, employer_id)
    active_borrowers = sum(
        1
        for b in borrowers
        if b.get("employmentStatus") == str(EmploymentStatus.ACTIVE)
    )

    agreements = _where(client, refs.BENEFIT_AGREEMENTS, "employerId", employer_id)
    active_benefits = 0
    monthly_obligation_cents = 0
    total_commitment_cents = 0
    amount_paid_cents = 0
    remaining_commitment_cents = 0
    for a in agreements:
        status = a.get("status")
        total_commitment_cents += int(a.get("totalCommitmentCents") or 0)
        amount_paid_cents += int(a.get("amountPaidCents") or 0)
        if status == str(BenefitStatus.ACTIVE):
            active_benefits += 1
            monthly_obligation_cents += int(
                a.get("baseMonthlyContributionCents") or 0
            )
        if status in NON_TERMINAL_AGREEMENT_STATUSES:
            remaining_commitment_cents += int(a.get("remainingCommitmentCents") or 0)

    open_exception_count = sum(
        1
        for exc in _where(
            client, refs.OPERATIONAL_EXCEPTIONS, "employerId", employer_id
        )
        if exc.get("status") in OPEN_EXCEPTION_STATUSES
    )

    return {
        "employerId": employer_id,
        "employerName": employer.get("name"),
        "activeBorrowers": active_borrowers,
        "activeBenefits": active_benefits,
        "monthlyObligationCents": monthly_obligation_cents,
        "openExceptionCount": open_exception_count,
        "totalCommitmentCents": total_commitment_cents,
        "amountPaidCents": amount_paid_cents,
        "remainingCommitmentCents": remaining_commitment_cents,
    }


# --------------------------------------------------------------------------- #
# employerSummaries/{employerId}/periods/{YYYY-MM} (specs/05 §5.4)
# --------------------------------------------------------------------------- #
def recompute_employer_period(
    client, employer_id: str, period: str
) -> dict[str, Any]:
    """Derive an employer's per-period flow doc from source contributions bucketed
    by ``periodLabel`` (scoped by ``employerId``, then filtered on the label)."""
    contributions = _where(
        client, refs.SCHEDULED_CONTRIBUTIONS, "employerId", employer_id
    )
    posted_cents = 0
    failed_count = 0
    for c in contributions:
        if c.get("periodLabel") != period:
            continue
        status = c.get("status")
        if status == str(ContributionStatus.POSTED):
            posted_cents += int(c.get("postedAmountCents") or 0)
        elif status == str(ContributionStatus.FAILED):
            failed_count += 1

    return {
        "periodLabel": period,
        "postedCents": posted_cents,
        "failedCount": failed_count,
    }


# --------------------------------------------------------------------------- #
# loanWorkbenches/{loanId} — widest live mirror (specs/05 §5.5)
# --------------------------------------------------------------------------- #
def _latest_loan_activity(client, loan_id: str) -> tuple[Any, Optional[str]]:
    """Return ``(lastActivityAt, lastActivityType)`` from the loan's newest
    servicing-event mirror, or ``(None, None)`` when the loan has no events."""
    from google.cloud import firestore  # lazy — offline py_compile friendly

    query = (
        client.collection(refs.LOANS)
        .document(loan_id)
        .collection(refs.LOAN_EVENTS)
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
        .order_by("sequence", direction=firestore.Query.DESCENDING)
        .limit(1)
    )
    docs = _stream(query)
    if not docs:
        return None, None
    latest = docs[0]
    return latest.get("createdAt"), latest.get("eventType")


def recompute_loan_workbench(client, loan_id: str) -> Optional[dict[str, Any]]:
    """Derive ``loanWorkbenches/{loan_id}`` from source (loan + borrower +
    agreement + next contribution + open exceptions + latest event), or ``None``
    if the loan no longer exists."""
    loan = loans_repo.get(client, loan_id)
    if loan is None:
        return None

    borrower_id = loan.get("borrowerId")
    borrower = borrowers_repo.get(client, borrower_id) if borrower_id else None

    agreement_id = loan.get("benefitAgreementId")
    agreement = (
        agreements_repo.get(client, agreement_id) if agreement_id else None
    )

    # Next still-SCHEDULED installment (lowest installmentNumber) — recomputed
    # from source, not read off the loan's txn-synced mirror.
    next_contribution = (
        contributions_repo.next_scheduled(client, agreement_id)
        if agreement_id
        else None
    )
    next_date = next_contribution.get("scheduledDate") if next_contribution else None
    next_amount = (
        next_contribution.get("scheduledAmountCents") if next_contribution else None
    )

    open_exception_count = sum(
        1
        for exc in _where(client, refs.OPERATIONAL_EXCEPTIONS, "loanId", loan_id)
        if exc.get("status") in OPEN_EXCEPTION_STATUSES
    )

    last_activity_at, last_activity_type = _latest_loan_activity(client, loan_id)

    # benefitStatus mirrors the agreement (authoritative); fall back to the loan's
    # txn-synced mirror if the agreement read is unavailable.
    benefit_status = (
        agreement.get("status") if agreement else loan.get("benefitStatus")
    )

    return {
        "loanId": loan_id,
        "borrowerId": borrower_id,
        "borrowerName": (
            borrower.get("displayName") if borrower else loan.get("borrowerName")
        ),
        "borrowerEmail": borrower.get("email") if borrower else None,
        "employerId": loan.get("employerId"),
        "employerName": loan.get("employerName"),
        "employmentStatus": (
            borrower.get("employmentStatus") if borrower else None
        ),
        "servicerName": loan.get("servicerName"),
        "currentBalanceCents": loan.get("currentBalanceCents"),
        "loanStatus": loan.get("loanStatus"),
        "benefitAgreementId": agreement_id,
        "benefitStatus": benefit_status,
        "baseMonthlyContributionCents": (
            agreement.get("baseMonthlyContributionCents") if agreement else None
        ),
        "nextContributionDate": next_date,
        "nextContributionAmountCents": next_amount,
        "openExceptionCount": open_exception_count,
        "lastActivityAt": last_activity_at,
        "lastActivityType": last_activity_type,
    }


# --------------------------------------------------------------------------- #
# Dispatch — recompute a Key and write it via its gateway
# --------------------------------------------------------------------------- #
def apply_key(client, key: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Recompute the summary doc named by ``key`` and write it via its gateway.

    The single entry point shared by the ``update-projection`` task and the
    ``rebuild-summaries`` job. Idempotent: recompute converges to a source-derived
    value and the gateway overwrites, so a redelivery is byte-identical (modulo the
    server ``updatedAt``). Returns the derived doc, or ``None`` when the source
    entity has been removed (nothing is written).
    """
    kind = key.get("kind")
    if kind == KIND_PORTFOLIO_CURRENT:
        doc = recompute_portfolio_current(client)
        portfolio_summaries_repo.write_current(client, doc)
        return doc
    if kind == KIND_PORTFOLIO_PERIOD:
        period = key["period"]
        doc = recompute_portfolio_period(client, period)
        portfolio_summaries_repo.write_period(client, period, doc)
        return doc
    if kind == KIND_EMPLOYER:
        employer_id = key["id"]
        doc = recompute_employer(client, employer_id)
        if doc is not None:
            employer_summaries_repo.write(client, employer_id, doc)
        return doc
    if kind == KIND_EMPLOYER_PERIOD:
        employer_id = key["id"]
        period = key["period"]
        doc = recompute_employer_period(client, employer_id, period)
        employer_summaries_repo.write_period(client, employer_id, period, doc)
        return doc
    if kind == KIND_LOAN_WORKBENCH:
        loan_id = key["id"]
        doc = recompute_loan_workbench(client, loan_id)
        if doc is not None:
            loan_workbenches_repo.write(client, loan_id, doc)
        return doc
    raise ValueError(f"unknown projection key kind {kind!r}")
