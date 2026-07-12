"""Emulator test: the demo seed populates the read-model summary docs (specs/05, 18).

After ``SeedRunner.run()`` writes the deterministic demo dataset, every dashboard
read model must be populated so a freshly-seeded public demo is **not empty**: the
portfolio point-in-time doc + the current-period flow doc, one
``employerSummaries/{id}`` per seeded employer, and one ``loanWorkbenches/{loanId}``
per seeded loan.

Each doc is asserted to (a) exist and (b) be *source-consistent* — the stored value
equals what :mod:`projections.recompute` derives from the seeded source right now
(the seed drives that same engine, so this proves the seed persisted the engine's
output, not a divergent inline derivation), cross-checked against a couple of
independent hand counts. The seed writes fixed ids and only reads occur after it,
so the source is stable across the assertions; the shared demo data is intentionally
left in place (it *is* the demo, and the seed is idempotent).
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime

from django.test import SimpleTestCase, tag

from common.enums import EmploymentStatus, LoanStatus
from common.firestore import get_client
from common.periods import SYSTEM_TIMEZONE, period_label
from projections import recompute
from repositories import (
    employer_summaries as employer_summaries_repo,
    loan_workbenches as loan_workbenches_repo,
    portfolio_summaries as portfolio_summaries_repo,
    refs,
)
from seed.builder import ACCOUNTS, EMPLOYERS, SeedRunner

EMULATOR = bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class SeedSummariesTests(SimpleTestCase):
    databases: list[str] = []

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # NB: use ``fs`` not ``client`` — SimpleTestCase reserves ``self.client``
        # for the Django test HTTP client (a ``Client`` with no ``.collection``),
        # which would otherwise shadow the Firestore client in every test method.
        cls.fs = get_client()
        # Full deterministic seed once for the whole class (data only; run() does not
        # touch Firebase Auth). run() also rebuilds every summary from source.
        cls.stats = SeedRunner(cls.fs).run()

    # -- helpers ---------------------------------------------------------- #
    @staticmethod
    def _derived(stored: dict) -> dict:
        """Strip the gateway/snapshot metadata (server ``updatedAt`` + the snapshot
        ``id``) so a stored read model can be compared to a fresh recompute, which
        carries neither."""
        return {k: v for k, v in stored.items() if k not in ("updatedAt", "id")}

    # -- tests ------------------------------------------------------------ #
    def test_seed_reports_summaries_built(self) -> None:
        # portfolio_current + periods + 4 employers + employer periods + 20 loans.
        self.assertGreater(self.stats.get("summaries", 0), len(ACCOUNTS))

    def test_portfolio_current_populated_and_source_consistent(self) -> None:
        stored = portfolio_summaries_repo.get_current(self.fs)
        self.assertIsNotNone(stored, "portfolioSummaries/current missing after seed")
        self.assertIn("updatedAt", stored)
        self.assertEqual(
            self._derived(stored),
            recompute.recompute_portfolio_current(self.fs),
        )
        # Independent hand count: activeLoans == number of ACTIVE loan docs, and the
        # seed activates all 20 loans (terminated employment leaves the LOAN active).
        active_loans = sum(
            1
            for snap in self.fs.collection(refs.LOANS).stream()
            if (snap.to_dict() or {}).get("loanStatus") == str(LoanStatus.ACTIVE)
        )
        self.assertEqual(stored["activeLoans"], active_loans)
        self.assertGreaterEqual(stored["activeLoans"], len(ACCOUNTS))

    def test_current_period_doc_populated_and_source_consistent(self) -> None:
        # Every account's installment (posted + 1) is due in the current month, so
        # the current period always has contributions and thus a period doc.
        period = period_label(datetime.now(SYSTEM_TIMEZONE))
        stored = portfolio_summaries_repo.get_period(self.fs, period)
        self.assertIsNotNone(
            stored, f"portfolioSummaries/{period} missing after seed"
        )
        self.assertEqual(stored["periodLabel"], period)
        self.assertEqual(
            self._derived(stored),
            recompute.recompute_portfolio_period(self.fs, period),
        )

    def test_each_employer_summary_populated_and_source_consistent(self) -> None:
        for emp in EMPLOYERS:
            stored = employer_summaries_repo.get(self.fs, emp["id"])
            self.assertIsNotNone(
                stored, f"employerSummaries/{emp['id']} missing after seed"
            )
            self.assertEqual(stored["employerName"], emp["name"])
            self.assertEqual(
                self._derived(stored),
                recompute.recompute_employer(self.fs, emp["id"]),
            )
            # The per-employer current-period bucket is written too.
            period = period_label(datetime.now(SYSTEM_TIMEZONE))
            self.assertIsNotNone(
                employer_summaries_repo.get_period(self.fs, emp["id"], period),
                f"employerSummaries/{emp['id']}/periods/{period} missing",
            )

        # Independent hand count for one employer's active-borrower rollup.
        memorial = "emp_memorial"
        active_borrowers = sum(
            1
            for snap in self.fs.collection(refs.BORROWERS)
            .where(filter=refs.field_filter("employerId", "==", memorial))
            .stream()
            if (snap.to_dict() or {}).get("employmentStatus")
            == str(EmploymentStatus.ACTIVE)
        )
        self.assertEqual(
            employer_summaries_repo.get(self.fs, memorial)["activeBorrowers"],
            active_borrowers,
        )

    def test_each_loan_workbench_populated_and_source_consistent(self) -> None:
        for spec in ACCOUNTS:
            loan_id = f"loan_{spec.key}"
            stored = loan_workbenches_repo.get(self.fs, loan_id)
            self.assertIsNotNone(
                stored, f"loanWorkbenches/{loan_id} missing after seed"
            )
            self.assertEqual(stored["loanId"], loan_id)
            self.assertEqual(stored["borrowerName"], f"{spec.first} {spec.last}")
            self.assertEqual(
                self._derived(stored),
                recompute.recompute_loan_workbench(self.fs, loan_id),
            )


if __name__ == "__main__":
    unittest.main()
