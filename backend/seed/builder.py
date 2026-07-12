"""Deterministic Firestore seed builder (specs/18).

``SeedRunner`` writes the fixed demo dataset — 4 employers, 20 borrowers /
loans / benefit agreements, their full solved contribution schedules with
~12 months of elapsed history, and the eight scripted demo scenarios
(specs/18 §18.2) — to Firestore. Every write is an overwrite ``set`` keyed by a
deterministic id, so re-running is idempotent (the nightly ``reset-demo`` job
self-heals the public demo, specs/18 §18.1).

Design choices for a *seed* (vs. the live command layer):

* **Direct, overwriting ``set`` writes** rather than the increment/upsert
  helpers — re-runs must reproduce the *same* ``occurrenceCount`` / ``revision``
  values, so scenario 2 (``occurrenceCount: 1``) and scenario 4
  (``occurrenceCount: 4``) stay deterministic.
* **Deterministic event ids** (``{scope}__evtNNNN``) so re-running overwrites the
  same timeline rows instead of appending duplicates. The record shape matches
  ``servicing.events`` (§4.9) and ``eventType`` is validated against its closed
  ``EVENT_TYPES`` enum, which we import rather than re-declare.
* Money is solved with :func:`common.money.solve_schedule` so every schedule
  sums exactly to its commitment (invariant I5); ``scheduledDate`` /
  ``periodLabel`` come from :mod:`common.periods`.

Writes are buffered through a :class:`_Batcher` (auto-flush at 450 ops) for
throughput against the emulator. ``google.cloud.firestore`` is imported lazily so
this module ``py_compile``s in the offline sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any, Optional

from common.enums import (
    BenefitStatus,
    ContributionStatus,
    EmployerStatus,
    EmploymentStatus,
    ExceptionStatus,
    ExceptionType,
    LoanStatus,
    PaymentAttemptStatus,
    PaymentFailureCode,
    severity_rank,
)
from common.ids import (
    attempt_id,
    contribution_id,
    exception_id,
    processor_key,
)
from common.periods import (
    SYSTEM_TIMEZONE,
    period_label,
    scheduled_datetime,
    shift_months,
)
from common.money import solve_schedule
from exceptions.service import TYPE_DEFAULT_SEVERITY
from repositories import refs
from repositories import (
    agreements as agreements_repo,
    attempts as attempts_repo,
    borrowers as borrowers_repo,
    contributions as contributions_repo,
    employers as employers_repo,
    loans as loans_repo,
    servicing_events as events_repo,
)
from servicing.events import EVENT_TYPES

# --------------------------------------------------------------------------- #
# Fixed parameters
# --------------------------------------------------------------------------- #
CURRENCY = "USD"
SERVICER_NAME = "Demo Student Loan Servicer"
INTEREST_RATE_BPS = 625
DEFAULT_TERM = 36
DEFAULT_TOTAL_CENTS = 3_000_000  # solve_schedule(3_000_000, 36) -> [83333]*35 + [83345]
LOAN_PRINCIPAL_MARGIN_CENTS = 1_500_000  # loan larger than the benefit commitment
ACTOR_ID = "system:seed_demo"
ACTOR_NAME = "Seed Script"
# Emit PAYMENT_POSTED timeline events only for the most recent N posted
# installments per account — keeps the audit stream rich but bounded.
RECENT_POSTED_EVENTS = 6
_NOON = time(hour=12, minute=0, second=0, microsecond=0)


# --------------------------------------------------------------------------- #
# Employers (specs/18 §18.1 — 4 employers)
# --------------------------------------------------------------------------- #
EMPLOYERS: list[dict[str, str]] = [
    {
        "id": "emp_memorial",
        "name": "Memorial Health",
        "industry": "Healthcare",
        "programName": "Clinical Talent Loan Benefit",
    },
    {
        "id": "emp_northwind",
        "name": "Northwind Traders",
        "industry": "Retail",
        "programName": "Retail Associate Loan Benefit",
    },
    {
        "id": "emp_globex",
        "name": "Globex Manufacturing",
        "industry": "Manufacturing",
        "programName": "Skilled Trades Loan Benefit",
    },
    {
        "id": "emp_initech",
        "name": "Initech Software",
        "industry": "Technology",
        "programName": "Engineering Retention Benefit",
    },
]


# --------------------------------------------------------------------------- #
# Account specification (one borrower + loan + benefit agreement, 1:1:1 per MVP)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AccountSpec:
    key: str  # deterministic id stem: bor_{key}/loan_{key}/ben_{key}
    first: str
    last: str
    employer_id: str
    posted: int  # number of leading POSTED installments (elapsed history)
    scenario: str = ""  # human label (which specs/18 §18.2 scenario, if any)
    term: int = DEFAULT_TERM
    total_cents: int = DEFAULT_TOTAL_CENTS
    employment: EmploymentStatus = EmploymentStatus.ACTIVE
    benefit: BenefitStatus = BenefitStatus.ACTIVE
    accepting: bool = True
    # A single FAILED contribution (installment number) + its open exception.
    failed_installment: Optional[int] = None
    failed_code: Optional[PaymentFailureCode] = None
    failed_occurrences: int = 1
    failed_sim_outcome: Optional[str] = None  # seed-only simulatedOutcome passthrough
    # A single RETRY_PENDING contribution (prior failed attempt, ready to reprocess).
    retry_installment: Optional[int] = None
    retry_code: Optional[PaymentFailureCode] = None
    # Termination: installments >= this are CANCELED (future benefits stopped).
    canceled_from: Optional[int] = None
    # Extra standalone SERVICER_SYNC_FAILURE exception on the loan.
    sync_failure: bool = False
    # Scenario 8: force loan balance below the next scheduled contribution.
    balance_cap_cents: Optional[int] = None


# 20 accounts. Memorial carries a cluster of 8 (scenario 7 — employer rollups).
ACCOUNTS: list[AccountSpec] = [
    # --- Memorial Health (8) -------------------------------------------------
    AccountSpec("jordan_lee", "Jordan", "Lee", "emp_memorial", posted=12,
                scenario="1 healthy active"),
    AccountSpec("maria_santos", "Maria", "Santos", "emp_memorial", posted=6,
                scenario="2 failed awaiting retry",
                failed_installment=7, failed_code=PaymentFailureCode.SERVICER_TIMEOUT,
                failed_occurrences=1),
    AccountSpec("sophia_rossi", "Sophia", "Rossi", "emp_memorial", posted=35,
                scenario="5 approaching completion"),
    AccountSpec("ethan_brown", "Ethan", "Brown", "emp_memorial", posted=5,
                scenario="6 idempotency-protected process"),
    AccountSpec("olivia_martin", "Olivia", "Martin", "emp_memorial", posted=6,
                scenario="8 balance-capped final", balance_cap_cents=40_000),
    AccountSpec("ava_thompson", "Ava", "Thompson", "emp_memorial", posted=9,
                scenario="7 employer cluster"),
    AccountSpec("william_clark", "William", "Clark", "emp_memorial", posted=4,
                scenario="retry-pending",
                retry_installment=5, retry_code=PaymentFailureCode.SERVICER_UNAVAILABLE),
    AccountSpec("isabella_lopez", "Isabella", "Lopez", "emp_memorial", posted=7,
                scenario="7 employer cluster"),
    # --- Northwind Traders (4) ----------------------------------------------
    AccountSpec("david_kim", "David", "Kim", "emp_northwind", posted=8,
                scenario="3 terminated, future canceled",
                employment=EmploymentStatus.TERMINATED, benefit=BenefitStatus.TERMINATED,
                accepting=False, canceled_from=9),
    AccountSpec("noah_wilson", "Noah", "Wilson", "emp_northwind", posted=36,
                scenario="completed benefit agreement",
                benefit=BenefitStatus.COMPLETED, accepting=False),
    AccountSpec("emma_davis", "Emma", "Davis", "emp_northwind", posted=5,
                scenario="failed contribution",
                failed_installment=6, failed_code=PaymentFailureCode.INSUFFICIENT_FUNDS,
                failed_occurrences=2),
    AccountSpec("mason_garcia", "Mason", "Garcia", "emp_northwind", posted=6,
                scenario="retry-pending",
                retry_installment=7, retry_code=PaymentFailureCode.SERVICER_TIMEOUT),
    # --- Globex Manufacturing (4) -------------------------------------------
    AccountSpec("liam_walsh", "Liam", "Walsh", "emp_globex", posted=9,
                scenario="4 repeated failure + sync failure",
                failed_installment=10, failed_code=PaymentFailureCode.SERVICER_UNAVAILABLE,
                failed_occurrences=4, failed_sim_outcome="SERVICER_UNAVAILABLE",
                sync_failure=True),
    AccountSpec("charlotte_white", "Charlotte", "White", "emp_globex", posted=10,
                scenario="terminated (2nd)",
                employment=EmploymentStatus.TERMINATED, benefit=BenefitStatus.TERMINATED,
                accepting=False, canceled_from=11),
    AccountSpec("benjamin_hall", "Benjamin", "Hall", "emp_globex", posted=7,
                scenario="failed contribution",
                failed_installment=8, failed_code=PaymentFailureCode.ACCOUNT_FROZEN,
                failed_occurrences=1),
    AccountSpec("amelia_young", "Amelia", "Young", "emp_globex", posted=8,
                scenario="retry-pending",
                retry_installment=9, retry_code=PaymentFailureCode.INSUFFICIENT_FUNDS),
    # --- Initech Software (4) -----------------------------------------------
    AccountSpec("lucas_scott", "Lucas", "Scott", "emp_initech", posted=10),
    AccountSpec("mia_adams", "Mia", "Adams", "emp_initech", posted=8),
    AccountSpec("henry_baker", "Henry", "Baker", "emp_initech", posted=11),
    AccountSpec("ella_nelson", "Ella", "Nelson", "emp_initech", posted=6),
]

_FAILURE_REASONS = {
    PaymentFailureCode.SERVICER_UNAVAILABLE: "Downstream servicer unavailable",
    PaymentFailureCode.SERVICER_TIMEOUT: "Downstream servicer timed out",
    PaymentFailureCode.INSUFFICIENT_FUNDS: "Funding account has insufficient funds",
    PaymentFailureCode.ACCOUNT_FROZEN: "Account is frozen at the servicer",
    PaymentFailureCode.INVALID_ACCOUNT: "Invalid loan/account reference",
    PaymentFailureCode.NOT_SUBMITTED: "Charge never reached the processor",
}


def _failure_reason(code: PaymentFailureCode) -> str:
    return _FAILURE_REASONS.get(code, "Payment failed")


# --------------------------------------------------------------------------- #
# Write batching
# --------------------------------------------------------------------------- #
class _Batcher:
    """Buffer writes into WriteBatches, auto-flushing before the 500-op limit."""

    _LIMIT = 450

    def __init__(self, client) -> None:
        self._client = client
        self._batch = None
        self._ops = 0

    def set(self, ref, data: dict[str, Any]) -> None:
        if self._batch is None:
            self._batch = self._client.batch()
            self._ops = 0
        self._batch.set(ref, data)
        self._ops += 1
        if self._ops >= self._LIMIT:
            self.flush()

    def flush(self) -> None:
        if self._batch is not None and self._ops:
            self._batch.commit()
        self._batch = None
        self._ops = 0


# --------------------------------------------------------------------------- #
# Seed runner
# --------------------------------------------------------------------------- #
class SeedRunner:
    """Generate and write the full deterministic demo dataset."""

    def __init__(self, client) -> None:
        self._client = client
        self._batch = _Batcher(client)
        self._today = datetime.now(SYSTEM_TIMEZONE).date()
        # employer rollups accumulated across accounts (projection-owned in prod,
        # seeded here so the dashboard has data without running projections).
        self._emp_rollup: dict[str, dict[str, int]] = {
            e["id"]: {"total": 0, "paid": 0, "active": 0} for e in EMPLOYERS
        }
        self.stats: dict[str, int] = {
            "employers": 0,
            "borrowers": 0,
            "loans": 0,
            "agreements": 0,
            "contributions": 0,
            "posted": 0,
            "failed": 0,
            "retryPending": 0,
            "canceled": 0,
            "attempts": 0,
            "events": 0,
            "exceptions": 0,
            "terminated": 0,
            "completed": 0,
        }

    # -- helpers ---------------------------------------------------------- #
    @staticmethod
    def _fs():
        from google.cloud import firestore  # lazy — offline py_compile friendly

        return firestore

    def _server_ts(self):
        return self._fs().SERVER_TIMESTAMP

    def _at_noon(self, months_from_today: int) -> datetime:
        d = shift_months(self._today, months_from_today)
        return datetime.combine(d, _NOON, tzinfo=SYSTEM_TIMEZONE)

    def _start_month(self, posted: int):
        """Date whose installment (posted+1) falls due in the current month."""
        return shift_months(self._today, -posted)

    # -- event writer (deterministic id; shape per specs/04 §4.9) --------- #
    def _event(
        self,
        *,
        event_type: str,
        entity_type: str,
        entity_id: str,
        scope_id: str,
        sequence: int,
        metadata: dict[str, Any],
        when: datetime,
        loan_id: Optional[str] = None,
        borrower_id: Optional[str] = None,
        employer_id: Optional[str] = None,
        benefit_agreement_id: Optional[str] = None,
    ) -> None:
        if event_type not in EVENT_TYPES:  # reuse the closed enum (do not reinvent)
            raise ValueError(f"unknown eventType {event_type!r}")
        event_id = f"{scope_id}__evt{sequence:04d}"
        record = {
            "eventType": event_type,
            "entityType": entity_type,
            "entityId": entity_id,
            "loanId": loan_id,
            "borrowerId": borrower_id,
            "employerId": employer_id,
            "benefitAgreementId": benefit_agreement_id,
            "actorType": "SYSTEM",
            "actorId": ACTOR_ID,
            "actorRole": None,
            "actorName": ACTOR_NAME,
            "correlationId": f"seed:{scope_id}",
            "sequence": sequence,
            "metadata": dict(metadata),
            "createdAt": when,
        }
        self._batch.set(events_repo.ref(self._client, event_id), record)
        # Most-specific mirror (loan -> borrower -> global-only), same as §4.9.
        if loan_id:
            self._batch.set(
                events_repo.loan_mirror_ref(self._client, loan_id, event_id), record
            )
        elif borrower_id:
            self._batch.set(
                events_repo.borrower_mirror_ref(self._client, borrower_id, event_id),
                record,
            )
        self.stats["events"] += 1

    # -- exception writer (deterministic id; shape per specs/04 §4.10) ---- #
    def _exception(
        self,
        *,
        exception_type: ExceptionType,
        entity_type: str,
        entity_id: str,
        loan_id: str,
        borrower_id: str,
        borrower_name: str,
        employer_id: str,
        employer_name: str,
        summary: str,
        details: str,
        occurrence_count: int,
        first_seen: datetime,
        last_seen: datetime,
    ) -> str:
        exc_id = exception_id(entity_id, exception_type)
        severity = TYPE_DEFAULT_SEVERITY.get(exception_type)
        record = {
            "exceptionType": str(exception_type),
            "severity": str(severity),
            "severityRank": severity_rank(severity),
            "entityType": entity_type,
            "entityId": entity_id,
            "loanId": loan_id,
            "borrowerId": borrower_id,
            "borrowerName": borrower_name,
            "employerId": employer_id,
            "employerName": employer_name,
            "status": str(ExceptionStatus.OPEN),
            "assignedTo": None,
            "occurrenceCount": occurrence_count,
            "firstSeenAt": first_seen,
            "lastSeenAt": last_seen,
            "summary": summary,
            "details": details,
            "resolution": None,
            "createdAt": first_seen,
            "updatedAt": last_seen,
            "resolvedAt": None,
        }
        self._batch.set(
            refs.doc(self._client, refs.OPERATIONAL_EXCEPTIONS, exc_id), record
        )
        self.stats["exceptions"] += 1
        return exc_id

    # -- attempt writer (shape per specs/04 §4.8) ------------------------- #
    def _attempt(
        self,
        *,
        cid: str,
        loan_id: str,
        attempt_number: int,
        amount_cents: int,
        status: PaymentAttemptStatus,
        when: datetime,
        failure_code: Optional[PaymentFailureCode] = None,
    ) -> str:
        att_id = attempt_id(cid, attempt_number)
        pkey = processor_key(cid, attempt_number)
        record = {
            "contributionId": cid,
            "loanId": loan_id,
            "attemptNumber": attempt_number,
            "processorIdempotencyKey": pkey,
            "commandIdempotencyKey": f"seed-{cid}-att{attempt_number:03d}",
            "status": str(status),
            "reconcileAttempts": 0,
            "requestedAmountCents": amount_cents,
            "processorReference": (
                f"sim_ref_{pkey}" if status == PaymentAttemptStatus.SUCCEEDED else None
            ),
            "failureCode": str(failure_code) if failure_code else None,
            "failureReason": _failure_reason(failure_code) if failure_code else None,
            "startedAt": when,
            "completedAt": when,
        }
        self._batch.set(
            attempts_repo.ref(self._client, cid, attempt_number), record
        )
        self.stats["attempts"] += 1
        return att_id

    # -- top level -------------------------------------------------------- #
    def run(self) -> dict[str, int]:
        for spec in ACCOUNTS:
            self._build_account(spec)
        self._write_employers()
        self._batch.flush()
        return self.stats

    def _write_employers(self) -> None:
        for emp in EMPLOYERS:
            roll = self._emp_rollup[emp["id"]]
            data = {
                "name": emp["name"],
                "industry": emp["industry"],
                "status": str(EmployerStatus.ACTIVE),
                "programName": emp["programName"],
                "currency": CURRENCY,
                "totalCommitmentCents": roll["total"],
                "activeBorrowerCount": roll["active"],
                "amountPaidCents": roll["paid"],
                "remainingCommitmentCents": roll["total"] - roll["paid"],
            }
            refs.stamp_create(data, ACTOR_ID)
            self._batch.set(employers_repo.ref(self._client, emp["id"]), data)
            self.stats["employers"] += 1

    # -- one account ------------------------------------------------------ #
    def _build_account(self, spec: AccountSpec) -> None:
        client = self._client
        emp = next(e for e in EMPLOYERS if e["id"] == spec.employer_id)
        employer_id = spec.employer_id
        employer_name = emp["name"]
        borrower_id = f"bor_{spec.key}"
        loan_id = f"loan_{spec.key}"
        agreement_id = f"ben_{spec.key}"
        borrower_name = f"{spec.first} {spec.last}"

        schedule = solve_schedule(spec.total_cents, spec.term)
        start_month = self._start_month(spec.posted)
        start_dt = scheduled_datetime(start_month, 1)
        end_dt = scheduled_datetime(start_month, spec.term)
        base_monthly = spec.total_cents // spec.term

        canceled = (
            set(range(spec.canceled_from, spec.term + 1))
            if spec.canceled_from
            else set()
        )

        seq = 1  # per-account monotonic event sequence

        # BENEFIT_ACTIVATED first, so the timeline opens with activation.
        self._event(
            event_type="BENEFIT_ACTIVATED",
            entity_type="BENEFIT_AGREEMENT",
            entity_id=agreement_id,
            scope_id=loan_id,
            sequence=seq,
            metadata={"termMonths": spec.term, "totalCommitmentCents": spec.total_cents},
            when=start_dt,
            loan_id=loan_id,
            borrower_id=borrower_id,
            employer_id=employer_id,
            benefit_agreement_id=agreement_id,
        )
        seq += 1

        amount_paid = 0
        next_sched_dt: Optional[datetime] = None
        next_sched_amt: Optional[int] = None
        open_exception_count = 0

        for n in range(1, spec.term + 1):
            cid = contribution_id(agreement_id, n)
            amt = schedule[n - 1]
            sched_dt = scheduled_datetime(start_month, n)
            plabel = period_label(sched_dt)

            doc: dict[str, Any] = {
                "benefitAgreementId": agreement_id,
                "installmentNumber": n,
                "borrowerId": borrower_id,
                "borrowerName": borrower_name,
                "employerId": employer_id,
                "employerName": employer_name,
                "loanId": loan_id,
                "currency": CURRENCY,
                "scheduledDate": sched_dt,
                "periodLabel": plabel,
                "scheduledAmountCents": amt,
                "status": str(ContributionStatus.SCHEDULED),
                "attemptCount": 0,
                "currentAttemptId": None,
                "currentExceptionId": None,
                "lastAttemptAt": None,
                "postedAt": None,
                "postedAmountCents": None,
                "failureCode": None,
                "failureReason": None,
            }
            if spec.failed_sim_outcome and n == spec.failed_installment:
                doc["simulatedOutcome"] = spec.failed_sim_outcome

            if n in canceled:
                doc["status"] = str(ContributionStatus.CANCELED)
                self.stats["canceled"] += 1

            elif n == spec.failed_installment:
                code = spec.failed_code or PaymentFailureCode.SERVICER_UNAVAILABLE
                att = self._attempt(
                    cid=cid, loan_id=loan_id, attempt_number=1, amount_cents=amt,
                    status=PaymentAttemptStatus.FAILED, when=sched_dt, failure_code=code,
                )
                exc_id = self._exception(
                    exception_type=ExceptionType.PAYMENT_FAILED,
                    entity_type="SCHEDULED_CONTRIBUTION",
                    entity_id=cid, loan_id=loan_id,
                    borrower_id=borrower_id, borrower_name=borrower_name,
                    employer_id=employer_id, employer_name=employer_name,
                    summary="Employer contribution failed",
                    details=_failure_reason(code),
                    occurrence_count=spec.failed_occurrences,
                    first_seen=sched_dt, last_seen=sched_dt,
                )
                open_exception_count += 1
                doc.update({
                    "status": str(ContributionStatus.FAILED),
                    "attemptCount": 1,
                    "currentAttemptId": att,
                    "currentExceptionId": exc_id,
                    "lastAttemptAt": sched_dt,
                    "failureCode": str(code),
                    "failureReason": _failure_reason(code),
                })
                self.stats["failed"] += 1
                self._event(
                    event_type="PAYMENT_FAILED", entity_type="SCHEDULED_CONTRIBUTION",
                    entity_id=cid, scope_id=loan_id, sequence=seq,
                    metadata={"amountCents": amt, "periodLabel": plabel,
                              "failureCode": str(code),
                              "occurrenceCount": spec.failed_occurrences},
                    when=sched_dt, loan_id=loan_id, borrower_id=borrower_id,
                    employer_id=employer_id, benefit_agreement_id=agreement_id,
                )
                seq += 1

            elif n == spec.retry_installment:
                code = spec.retry_code or PaymentFailureCode.SERVICER_UNAVAILABLE
                att = self._attempt(
                    cid=cid, loan_id=loan_id, attempt_number=1, amount_cents=amt,
                    status=PaymentAttemptStatus.FAILED, when=sched_dt, failure_code=code,
                )
                doc.update({
                    "status": str(ContributionStatus.RETRY_PENDING),
                    "attemptCount": 1,
                    "currentAttemptId": att,
                    "lastAttemptAt": sched_dt,
                    "failureCode": str(code),
                    "failureReason": _failure_reason(code),
                })
                self.stats["retryPending"] += 1
                self._event(
                    event_type="PAYMENT_FAILED", entity_type="SCHEDULED_CONTRIBUTION",
                    entity_id=cid, scope_id=loan_id, sequence=seq,
                    metadata={"amountCents": amt, "periodLabel": plabel,
                              "failureCode": str(code)},
                    when=sched_dt, loan_id=loan_id, borrower_id=borrower_id,
                    employer_id=employer_id, benefit_agreement_id=agreement_id,
                )
                seq += 1
                self._event(
                    event_type="PAYMENT_RETRY_SCHEDULED",
                    entity_type="SCHEDULED_CONTRIBUTION",
                    entity_id=cid, scope_id=loan_id, sequence=seq,
                    metadata={"amountCents": amt, "periodLabel": plabel},
                    when=sched_dt, loan_id=loan_id, borrower_id=borrower_id,
                    employer_id=employer_id, benefit_agreement_id=agreement_id,
                )
                seq += 1

            elif n <= spec.posted:
                att = self._attempt(
                    cid=cid, loan_id=loan_id, attempt_number=1, amount_cents=amt,
                    status=PaymentAttemptStatus.SUCCEEDED, when=sched_dt,
                )
                doc.update({
                    "status": str(ContributionStatus.POSTED),
                    "attemptCount": 1,
                    "currentAttemptId": att,
                    "lastAttemptAt": sched_dt,
                    "postedAt": sched_dt,
                    "postedAmountCents": amt,
                })
                amount_paid += amt
                self.stats["posted"] += 1
                if n > spec.posted - RECENT_POSTED_EVENTS:
                    self._event(
                        event_type="PAYMENT_POSTED",
                        entity_type="SCHEDULED_CONTRIBUTION",
                        entity_id=cid, scope_id=loan_id, sequence=seq,
                        metadata={"amountCents": amt, "periodLabel": plabel,
                                  "previousStatus": "PROCESSING", "newStatus": "POSTED"},
                        when=sched_dt, loan_id=loan_id, borrower_id=borrower_id,
                        employer_id=employer_id, benefit_agreement_id=agreement_id,
                    )
                    seq += 1
            # else: plain SCHEDULED (future) — nothing extra.

            # Track the earliest still-SCHEDULED installment for the look-ahead.
            if doc["status"] == str(ContributionStatus.SCHEDULED) and next_sched_dt is None:
                next_sched_dt = sched_dt
                next_sched_amt = amt

            refs.stamp_create(doc, ACTOR_ID)
            self._batch.set(contributions_repo.ref(client, cid), doc)
            self.stats["contributions"] += 1

        remaining = spec.total_cents - amount_paid

        # Standalone SERVICER_SYNC_FAILURE exception (scenario 4 second exception).
        if spec.sync_failure:
            self._exception(
                exception_type=ExceptionType.SERVICER_SYNC_FAILURE,
                entity_type="LOAN", entity_id=loan_id, loan_id=loan_id,
                borrower_id=borrower_id, borrower_name=borrower_name,
                employer_id=employer_id, employer_name=employer_name,
                summary="Loan servicer sync failed",
                details="Simulated servicer sync job could not reconcile the loan balance",
                occurrence_count=1,
                first_seen=self._at_noon(-1), last_seen=self._at_noon(0),
            )
            open_exception_count += 1
            self._event(
                event_type="EXCEPTION_CREATED", entity_type="LOAN", entity_id=loan_id,
                scope_id=loan_id, sequence=seq,
                metadata={"exceptionType": str(ExceptionType.SERVICER_SYNC_FAILURE)},
                when=self._at_noon(0), loan_id=loan_id, borrower_id=borrower_id,
                employer_id=employer_id,
            )
            seq += 1

        # Termination story (scenario 3): employment + benefit terminated,
        # future contributions canceled.
        if spec.employment == EmploymentStatus.TERMINATED:
            self.stats["terminated"] += 1
            self._event(
                event_type="EMPLOYMENT_STATUS_CHANGED", entity_type="BORROWER",
                entity_id=borrower_id, scope_id=borrower_id, sequence=seq,
                metadata={"previousStatus": "ACTIVE", "newStatus": "TERMINATED"},
                when=self._at_noon(0), borrower_id=borrower_id, employer_id=employer_id,
            )
            seq += 1
            self._event(
                event_type="BENEFIT_TERMINATED", entity_type="BENEFIT_AGREEMENT",
                entity_id=agreement_id, scope_id=loan_id, sequence=seq,
                metadata={"reason": "EMPLOYMENT_TERMINATED"}, when=self._at_noon(0),
                loan_id=loan_id, borrower_id=borrower_id, employer_id=employer_id,
                benefit_agreement_id=agreement_id,
            )
            seq += 1
            if canceled:
                self._event(
                    event_type="FUTURE_CONTRIBUTIONS_CANCELED",
                    entity_type="BENEFIT_AGREEMENT", entity_id=agreement_id,
                    scope_id=loan_id, sequence=seq,
                    metadata={"canceledCount": len(canceled),
                              "fromInstallment": spec.canceled_from},
                    when=self._at_noon(0), loan_id=loan_id, borrower_id=borrower_id,
                    employer_id=employer_id, benefit_agreement_id=agreement_id,
                )
                seq += 1

        if spec.benefit == BenefitStatus.COMPLETED:
            self.stats["completed"] += 1
            self._event(
                event_type="BENEFIT_COMPLETED", entity_type="BENEFIT_AGREEMENT",
                entity_id=agreement_id, scope_id=loan_id, sequence=seq,
                metadata={"totalCommitmentCents": spec.total_cents}, when=self._at_noon(0),
                loan_id=loan_id, borrower_id=borrower_id, employer_id=employer_id,
                benefit_agreement_id=agreement_id,
            )
            seq += 1

        # --- Loan document (specs/04 §4.5) --------------------------------
        principal = spec.total_cents + LOAN_PRINCIPAL_MARGIN_CENTS
        current_balance = principal - amount_paid
        loan_status = LoanStatus.ACTIVE
        if spec.balance_cap_cents is not None:
            # Scenario 8: shrink the loan so balance < next scheduled contribution,
            # keeping balance == principal - paid (invariant-consistent).
            principal = amount_paid + spec.balance_cap_cents
            current_balance = spec.balance_cap_cents

        loan = {
            "borrowerId": borrower_id,
            "borrowerName": borrower_name,
            "employerId": employer_id,
            "employerName": employer_name,
            "externalLoanReference": f"LN-{spec.key.upper().replace('_', '')}",
            "servicerName": SERVICER_NAME,
            "currency": CURRENCY,
            "originalPrincipalCents": principal,
            "currentBalanceCents": current_balance,
            "interestRateBasisPoints": INTEREST_RATE_BPS,
            "loanStatus": str(loan_status),
            "benefitAgreementId": agreement_id,
            "benefitStatus": str(spec.benefit),
            "openExceptionCount": open_exception_count,
            "nextContributionDate": next_sched_dt,
            "nextContributionAmountCents": next_sched_amt,
        }
        refs.stamp_create(loan, ACTOR_ID)
        self._batch.set(loans_repo.ref(client, loan_id), loan)
        self.stats["loans"] += 1

        # --- Benefit agreement (specs/04 §4.6) ----------------------------
        agreement = {
            "borrowerId": borrower_id,
            "borrowerName": borrower_name,
            "employerId": employer_id,
            "employerName": employer_name,
            "loanId": loan_id,
            "currency": CURRENCY,
            "totalCommitmentCents": spec.total_cents,
            "baseMonthlyContributionCents": base_monthly,
            "termMonths": spec.term,
            "startDate": start_dt,
            "endDate": end_dt,
            "amountPaidCents": amount_paid,
            "remainingCommitmentCents": remaining,
            "status": str(spec.benefit),
            "acceptingPayments": spec.accepting,
            "suspendedReason": None,
            "scheduleGenerated": True,
            "plannedInstallmentCount": spec.term,
            "installmentsGenerated": spec.term,
        }
        refs.stamp_create(agreement, ACTOR_ID)
        self._batch.set(agreements_repo.ref(client, agreement_id), agreement)
        self.stats["agreements"] += 1

        # --- Borrower (specs/04 §4.4) -------------------------------------
        employment_end = (
            self._at_noon(0) if spec.employment == EmploymentStatus.TERMINATED else None
        )
        borrower = {
            "firstName": spec.first,
            "lastName": spec.last,
            "displayName": borrower_name,
            "email": f"{spec.key.replace('_', '.')}@example.com",
            "employerId": employer_id,
            "employerName": employer_name,
            "employmentStatus": str(spec.employment),
            "employmentStartDate": self._at_noon(-30),
            "employmentEndDate": employment_end,
            "primaryLoanId": loan_id,
            "primaryBenefitAgreementId": agreement_id,
        }
        refs.stamp_create(borrower, ACTOR_ID)
        self._batch.set(borrowers_repo.ref(client, borrower_id), borrower)
        self.stats["borrowers"] += 1

        # --- accumulate employer rollups ----------------------------------
        roll = self._emp_rollup[employer_id]
        roll["total"] += spec.total_cents
        roll["paid"] += amount_paid
        if spec.employment == EmploymentStatus.ACTIVE:
            roll["active"] += 1
