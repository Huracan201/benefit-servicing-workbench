"""Emulator integration tests for the read-model recompute engine (specs/05).

Seed a small, self-contained book under a unique employer id and assert each
``recompute_*`` derives the doc a hand count would — plus the three normative
properties that make projections trustworthy:

* **periodLabel bucketing, not wall-clock.** A July installment (``periodLabel
  2026-07``) that *posts* on Aug 2 counts in July's ``postedCents`` — never August.
* **terminal exclusion.** A ``TERMINATED`` agreement's residual commitment is
  excluded from ``remainingCommitmentCents`` (money that will never move), while
  its ``totalCommitmentCents`` / ``amountPaidCents`` still roll up.
* **byte-identical redelivery.** Recompute is idempotent — a second call returns
  the identical derived value (recompute-from-source, no folded delta).

Portfolio-wide recomputes scan the whole (shared-emulator) collections, so they
are asserted against an independent source count computed in-test rather than
against fixed numbers. Employer-/loan-scoped recomputes use unique ids and are
asserted exactly. Each seeded doc is deleted on cleanup.
"""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime

from django.test import SimpleTestCase, tag

from common.enums import (
    BenefitStatus,
    ContributionStatus,
    EmploymentStatus,
    ExceptionStatus,
    LoanStatus,
    PaymentFailureCode,
    Severity,
    severity_rank,
)
from common.firestore import get_client
from common.periods import SYSTEM_TIMEZONE
from projections import recompute
from repositories import (
    agreements as agreements_repo,
    borrowers as borrowers_repo,
    contributions as contributions_repo,
    employers as employers_repo,
    loans as loans_repo,
    operational_exceptions as exceptions_repo,
    refs,
    servicing_events as events_repo,
)

EMULATOR = bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))

CURRENCY = "USD"
MONTHLY = 83_333
TOTAL = 3_000_000


def _noon(year: int, month: int, day: int = 15) -> datetime:
    return datetime(year, month, day, 12, 0, 0, tzinfo=SYSTEM_TIMEZONE)


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class RecomputeEngineTests(SimpleTestCase):
    databases: list[str] = []

    def setUp(self) -> None:
        self.client = get_client()
        uid = uuid.uuid4().hex[:10]
        self.employer_id = f"emp_projtest_{uid}"
        # Account A — active; Account B — terminated.
        self.bor_a, self.loan_a, self.ben_a = (
            f"bor_a_{uid}",
            f"loan_a_{uid}",
            f"ben_a_{uid}",
        )
        self.bor_b, self.loan_b, self.ben_b = (
            f"bor_b_{uid}",
            f"loan_b_{uid}",
            f"ben_b_{uid}",
        )
        self._refs: list = []
        self._seed()
        self.addCleanup(self._cleanup)

    # -- seed helpers ----------------------------------------------------- #
    def _set(self, ref, data: dict) -> None:
        ref.set(data)
        self._refs.append(ref)

    def _cleanup(self) -> None:
        for ref in self._refs:
            ref.delete()

    def _contribution(
        self,
        agreement_id: str,
        loan_id: str,
        borrower_id: str,
        n: int,
        period: str,
        status: ContributionStatus,
        *,
        scheduled_dt: datetime,
        posted_amount: int | None = None,
        posted_at: datetime | None = None,
    ) -> None:
        cid = f"{agreement_id}__{n:03d}"
        doc = {
            "benefitAgreementId": agreement_id,
            "installmentNumber": n,
            "borrowerId": borrower_id,
            "borrowerName": "Fixture",
            "employerId": self.employer_id,
            "employerName": "Projtest Corp",
            "loanId": loan_id,
            "currency": CURRENCY,
            "scheduledDate": scheduled_dt,
            "periodLabel": period,
            "scheduledAmountCents": MONTHLY,
            "status": str(status),
            "attemptCount": 1 if status != ContributionStatus.SCHEDULED else 0,
            "currentAttemptId": None,
            "currentExceptionId": None,
            "lastAttemptAt": posted_at,
            "postedAt": posted_at,
            "postedAmountCents": posted_amount,
            "failureCode": (
                str(PaymentFailureCode.SERVICER_TIMEOUT)
                if status == ContributionStatus.FAILED
                else None
            ),
            "failureReason": None,
        }
        self._set(contributions_repo.ref(self.client, cid), doc)

    def _seed(self) -> None:
        # --- Account A: ACTIVE agreement, active borrower ------------------
        self._set(
            borrowers_repo.ref(self.client, self.bor_a),
            {
                "displayName": "Active Ada",
                "email": "ada@example.com",
                "employerId": self.employer_id,
                "employmentStatus": str(EmploymentStatus.ACTIVE),
            },
        )
        self._set(
            loans_repo.ref(self.client, self.loan_a),
            {
                "borrowerId": self.bor_a,
                "borrowerName": "Active Ada",
                "employerId": self.employer_id,
                "employerName": "Projtest Corp",
                "servicerName": "Demo Servicer",
                "currentBalanceCents": 5_000_000,
                "loanStatus": str(LoanStatus.ACTIVE),
                "benefitAgreementId": self.ben_a,
                "benefitStatus": str(BenefitStatus.ACTIVE),
            },
        )
        self._set(
            agreements_repo.ref(self.client, self.ben_a),
            {
                "employerId": self.employer_id,
                "loanId": self.loan_a,
                "status": str(BenefitStatus.ACTIVE),
                "totalCommitmentCents": TOTAL,
                "baseMonthlyContributionCents": MONTHLY,
                "amountPaidCents": 1_000_000,
                "remainingCommitmentCents": 2_000_000,
            },
        )
        # inst1 posted in June (period 2026-06); inst2 period 2026-07 but POSTS in
        # August (the wall-clock-vs-periodLabel stressor); inst3 FAILED in July;
        # inst4 still SCHEDULED (the look-ahead target).
        self._contribution(
            self.ben_a, self.loan_a, self.bor_a, 1, "2026-06",
            ContributionStatus.POSTED, scheduled_dt=_noon(2026, 6),
            posted_amount=MONTHLY, posted_at=_noon(2026, 6),
        )
        self._contribution(
            self.ben_a, self.loan_a, self.bor_a, 2, "2026-07",
            ContributionStatus.POSTED, scheduled_dt=_noon(2026, 7),
            posted_amount=MONTHLY, posted_at=_noon(2026, 8, 2),  # posts in AUGUST
        )
        self._contribution(
            self.ben_a, self.loan_a, self.bor_a, 3, "2026-07",
            ContributionStatus.FAILED, scheduled_dt=_noon(2026, 7),
        )
        self._contribution(
            self.ben_a, self.loan_a, self.bor_a, 4, "2026-08",
            ContributionStatus.SCHEDULED, scheduled_dt=_noon(2026, 8),
        )

        # One OPEN exception on loan A.
        self._set(
            exceptions_repo.ref(self.client, f"{self.loan_a}__PAYMENT_FAILED"),
            {
                "exceptionType": "PAYMENT_FAILED",
                "severity": str(Severity.HIGH),
                "severityRank": severity_rank(Severity.HIGH),
                "entityType": "SCHEDULED_CONTRIBUTION",
                "entityId": f"{self.ben_a}__003",
                "loanId": self.loan_a,
                "employerId": self.employer_id,
                "status": str(ExceptionStatus.OPEN),
            },
        )

        # A loan-A servicing-event mirror → drives lastActivity on the workbench.
        self._set(
            events_repo.loan_mirror_ref(self.client, self.loan_a, f"{self.loan_a}__evt1"),
            {
                "eventType": "PAYMENT_POSTED",
                "loanId": self.loan_a,
                "sequence": 1,
                "createdAt": _noon(2026, 8, 2),
            },
        )

        # --- Account B: TERMINATED agreement, terminated borrower ----------
        self._set(
            borrowers_repo.ref(self.client, self.bor_b),
            {
                "displayName": "Gone Gary",
                "email": "gary@example.com",
                "employerId": self.employer_id,
                "employmentStatus": str(EmploymentStatus.TERMINATED),
            },
        )
        self._set(
            loans_repo.ref(self.client, self.loan_b),
            {
                "borrowerId": self.bor_b,
                "borrowerName": "Gone Gary",
                "employerId": self.employer_id,
                "employerName": "Projtest Corp",
                "servicerName": "Demo Servicer",
                "currentBalanceCents": 5_500_000,
                "loanStatus": str(LoanStatus.ACTIVE),
                "benefitAgreementId": self.ben_b,
                "benefitStatus": str(BenefitStatus.TERMINATED),
            },
        )
        self._set(
            agreements_repo.ref(self.client, self.ben_b),
            {
                "employerId": self.employer_id,
                "loanId": self.loan_b,
                "status": str(BenefitStatus.TERMINATED),
                "totalCommitmentCents": TOTAL,
                "baseMonthlyContributionCents": MONTHLY,
                "amountPaidCents": 500_000,
                "remainingCommitmentCents": 2_500_000,  # excluded from rollup
            },
        )
        self._contribution(
            self.ben_b, self.loan_b, self.bor_b, 1, "2026-06",
            ContributionStatus.POSTED, scheduled_dt=_noon(2026, 6),
            posted_amount=MONTHLY, posted_at=_noon(2026, 6),
        )
        self._contribution(
            self.ben_b, self.loan_b, self.bor_b, 2, "2026-07",
            ContributionStatus.CANCELED, scheduled_dt=_noon(2026, 7),
        )

        # Finally, the employer base doc.
        self._set(
            employers_repo.ref(self.client, self.employer_id),
            {
                "name": "Projtest Corp",
                "status": "ACTIVE",
                "currency": CURRENCY,
                "totalCommitmentCents": 6_000_000,
                "amountPaidCents": 1_500_000,
                "remainingCommitmentCents": 4_500_000,
            },
        )

    # -- tests ------------------------------------------------------------ #
    def test_recompute_employer_rolls_up_and_excludes_terminal_commitment(self):
        doc = recompute.recompute_employer(self.client, self.employer_id)
        self.assertEqual(doc["employerId"], self.employer_id)
        self.assertEqual(doc["employerName"], "Projtest Corp")
        self.assertEqual(doc["activeBorrowers"], 1)  # B is TERMINATED
        self.assertEqual(doc["activeBenefits"], 1)  # only A is ACTIVE
        self.assertEqual(doc["monthlyObligationCents"], MONTHLY)  # ACTIVE only
        self.assertEqual(doc["openExceptionCount"], 1)
        # total/paid roll up ALL agreements; remaining excludes the TERMINATED one.
        self.assertEqual(doc["totalCommitmentCents"], 2 * TOTAL)
        self.assertEqual(doc["amountPaidCents"], 1_500_000)
        self.assertEqual(doc["remainingCommitmentCents"], 2_000_000)

    def test_recompute_employer_period_buckets_by_period_label(self):
        # inst2 has periodLabel 2026-07 but posted in AUGUST — it counts in July.
        july = recompute.recompute_employer_period(
            self.client, self.employer_id, "2026-07"
        )
        self.assertEqual(july["periodLabel"], "2026-07")
        self.assertEqual(july["postedCents"], MONTHLY)  # the Aug-posted July install
        self.assertEqual(july["failedCount"], 1)  # inst3

        # August has no POSTED/FAILED (inst4 is still SCHEDULED); the Aug-posted
        # July installment must NOT leak here.
        august = recompute.recompute_employer_period(
            self.client, self.employer_id, "2026-08"
        )
        self.assertEqual(august["postedCents"], 0)
        self.assertEqual(august["failedCount"], 0)

    def test_recompute_loan_workbench_joins_source_without_stale_reads(self):
        doc = recompute.recompute_loan_workbench(self.client, self.loan_a)
        self.assertEqual(doc["loanId"], self.loan_a)
        self.assertEqual(doc["borrowerName"], "Active Ada")
        self.assertEqual(doc["borrowerEmail"], "ada@example.com")
        self.assertEqual(doc["employmentStatus"], str(EmploymentStatus.ACTIVE))
        self.assertEqual(doc["benefitStatus"], str(BenefitStatus.ACTIVE))
        self.assertEqual(doc["baseMonthlyContributionCents"], MONTHLY)
        # next_scheduled → inst4 (the only SCHEDULED installment).
        self.assertEqual(doc["nextContributionAmountCents"], MONTHLY)
        self.assertEqual(doc["nextContributionDate"], _noon(2026, 8))
        self.assertEqual(doc["openExceptionCount"], 1)
        self.assertEqual(doc["lastActivityType"], "PAYMENT_POSTED")
        self.assertEqual(doc["lastActivityAt"], _noon(2026, 8, 2))

    def test_recompute_loan_workbench_missing_loan_returns_none(self):
        self.assertIsNone(
            recompute.recompute_loan_workbench(self.client, "loan_does_not_exist")
        )

    def test_portfolio_period_matches_independent_source_count(self):
        # Independent count over ALL contributions with periodLabel 2026-07.
        rows = [
            refs.snapshot_to_dict(s)
            for s in self.client.collection(refs.SCHEDULED_CONTRIBUTIONS)
            .where(filter=refs.field_filter("periodLabel", "==", "2026-07"))
            .stream()
        ]
        expected_scheduled = sum(
            int(r["scheduledAmountCents"])
            for r in rows
            if r["status"] != str(ContributionStatus.CANCELED)
        )
        expected_posted = sum(
            int(r.get("postedAmountCents") or 0)
            for r in rows
            if r["status"] == str(ContributionStatus.POSTED)
        )
        expected_failed = sum(
            1 for r in rows if r["status"] == str(ContributionStatus.FAILED)
        )

        doc = recompute.recompute_portfolio_period(self.client, "2026-07")
        self.assertEqual(doc["periodLabel"], "2026-07")
        self.assertEqual(doc["scheduledCents"], expected_scheduled)
        self.assertEqual(doc["postedCents"], expected_posted)
        self.assertEqual(doc["failedContributionCount"], expected_failed)
        # Our seed contributes at least the A-inst2 posted July installment.
        self.assertGreaterEqual(doc["postedCents"], MONTHLY)

    def test_portfolio_current_is_internally_consistent_and_idempotent(self):
        doc = recompute.recompute_portfolio_current(self.client)
        # Independent non-terminal commitment sum over all agreements.
        agreements = [
            refs.snapshot_to_dict(s)
            for s in self.client.collection(refs.BENEFIT_AGREEMENTS).stream()
        ]
        expected_remaining = sum(
            int(a.get("remainingCommitmentCents") or 0)
            for a in agreements
            if a.get("status") in recompute.NON_TERMINAL_AGREEMENT_STATUSES
        )
        self.assertEqual(
            doc["remainingEmployerCommitmentCents"], expected_remaining
        )
        # The TERMINATED B agreement (2_500_000) is NOT in the rollup; the ACTIVE
        # A agreement (2_000_000) is.
        self.assertGreaterEqual(doc["remainingEmployerCommitmentCents"], 2_000_000)
        # Severity tiles are zero-initialised over the whole enum.
        for sev in Severity:
            self.assertIn(str(sev), doc["openExceptionSeverityCounts"])
        # Byte-identical on a second call (recompute-from-source idempotency).
        self.assertEqual(doc, recompute.recompute_portfolio_current(self.client))

    def test_apply_key_writes_then_gateway_reads_it_back(self):
        recompute.apply_key(self.client, recompute.employer_key(self.employer_id))
        from repositories import employer_summaries as es

        # gateway read-back path + updatedAt stamped by the gateway.
        stored = es.get(self.client, self.employer_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["remainingCommitmentCents"], 2_000_000)
        self.assertIn("updatedAt", stored)
        self.addCleanup(es.ref(self.client, self.employer_id).delete)


if __name__ == "__main__":
    unittest.main()
