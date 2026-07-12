"""Emulator integration tests for the reconciliation sweeper's stuck-payment
scans — ``contributions.stuck_processing`` and ``contributions.stale_started_attempts``
(specs/09 §9.4).

These are the crash-recovery drivers of the two-phase payment: a driver that
dies between Phase 2 (charge persisted at the processor) and Phase 3 (finalize)
strands a contribution ``PROCESSING`` with a ``STARTED`` attempt. Two scans find
the wreckage:

* ``stuck_processing`` — scan (a): ``scheduledContributions`` still ``PROCESSING``
  whose ``lastAttemptAt`` predates the stuck threshold. Must return *only* those
  (a freshly-attempted PROCESSING row and a same-instant non-PROCESSING row are
  both skipped) and page a large set with a stable ``(lastAttemptAt, id)`` cursor.
* ``stale_started_attempts`` — scan (b): a **collection-group** query over every
  ``attempts`` subcollection for ``STARTED`` attempts older than the threshold,
  regardless of the parent contribution's status. This is the safety net that
  catches a ``STARTED`` attempt whose contribution has already *moved* off
  ``PROCESSING`` (a mis-finalized stale driver) — the case scan (a) cannot see.

Both share ``due``'s hard case: many rows share one noon-in-SYSTEM_TIMEZONE
instant (specs/07 §7.3), so the inequality field alone is a non-deterministic
cursor — the ``__name__`` tiebreak pins each page boundary to an exact position.
Seeds use far-past instants so the collection-wide / collection-group scans are
isolated from present-dated fixtures other suites leave in the emulator; every
seeded doc is deleted on cleanup. Exercises the repository against real Firestore
(no HTTP / auth).
"""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime

from django.test import SimpleTestCase, tag

from common.enums import ContributionStatus, PaymentAttemptStatus
from common.firestore import get_client
from common.ids import attempt_id as _attempt_id
from common.ids import contribution_id as _contribution_id
from common.periods import SYSTEM_TIMEZONE
from repositories import attempts, contributions, stamp_create

EMULATOR = bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))

ACTOR_ID = "user_test_worker"
CURRENCY = "USD"

# A far-past "stale" instant shared by every stranded row (stresses the __name__
# tiebreak, and keeps the scans clear of present-dated fixtures from other suites)
# and a threshold strictly after it. "Fresh" is far-future so it is never < the
# threshold and must be skipped by the ``< older_than`` predicate.
STALE_INSTANT = datetime(1987, 3, 1, 12, 0, 0, tzinfo=SYSTEM_TIMEZONE)
THRESHOLD = datetime(1990, 1, 1, 12, 0, 0, tzinfo=SYSTEM_TIMEZONE)
FRESH_INSTANT = datetime(2100, 1, 1, 12, 0, 0, tzinfo=SYSTEM_TIMEZONE)


def _seed_contribution(
    client,
    agreement_id: str,
    installment_number: int,
    *,
    status: str,
    last_attempt_at,
) -> str:
    """Write one contribution (specs/04 §4.7) in ``status`` with ``last_attempt_at``.

    Returns the deterministic contribution id.
    """
    cid = _contribution_id(agreement_id, installment_number)
    doc = {
        "benefitAgreementId": agreement_id,
        "installmentNumber": installment_number,
        "borrowerId": f"bor_{agreement_id}",
        "borrowerName": "Scan Fixture",
        "employerId": f"emp_{agreement_id}",
        "employerName": "Scan Fixture Corp",
        "loanId": f"loan_{agreement_id}",
        "currency": CURRENCY,
        "scheduledDate": STALE_INSTANT,
        "periodLabel": f"{STALE_INSTANT:%Y-%m}",
        "scheduledAmountCents": 50_000,  # integer cents (money convention)
        "status": status,
        "attemptCount": 1,
        "currentAttemptId": _attempt_id(cid, 1),
        "currentExceptionId": None,
        "lastAttemptAt": last_attempt_at,
        "postedAt": None,
        "postedAmountCents": None,
        "failureCode": None,
        "failureReason": None,
    }
    stamp_create(doc, ACTOR_ID)
    contributions.ref(client, cid).set(doc)
    return cid


def _seed_attempt(
    client,
    contribution_id: str,
    attempt_number: int,
    *,
    status: str,
    started_at,
) -> str:
    """Write one attempt subdoc (specs/04 §4.8) mirroring the real payment shape.

    Attempts carry their own lifecycle fields (no common fields) and store their
    ``contributionId`` so a collection-group hit resolves its parent. Returns the
    attempt document id.
    """
    doc = {
        "contributionId": contribution_id,
        "loanId": f"loan_{contribution_id}",
        "attemptNumber": attempt_number,
        "processorIdempotencyKey": f"{contribution_id}__att_{attempt_number:03d}",
        "commandIdempotencyKey": None,
        "status": status,
        "reconcileAttempts": 0,
        "requestedAmountCents": 50_000,
        "processorReference": None,
        "failureCode": None,
        "failureReason": None,
        "startedAt": started_at,
        "completedAt": None,
    }
    attempts.ref(client, contribution_id, attempt_number).set(doc)
    return _attempt_id(contribution_id, attempt_number)


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class StuckProcessingScanTests(SimpleTestCase):
    databases: list[str] = []

    # Four stranded PROCESSING rows share ONE stale instant (so page boundaries
    # fall inside a shared ``lastAttemptAt``); ids ``{agreement}__001..004`` sort
    # ascending == the expected cursor order.
    N_STALE = 4

    def setUp(self) -> None:
        self.client = get_client()
        self.agreement_id = f"ben_stuck_{uuid.uuid4().hex[:10]}"
        self.stale_ids = [
            _seed_contribution(
                self.client, self.agreement_id, n,
                status=str(ContributionStatus.PROCESSING),
                last_attempt_at=STALE_INSTANT,
            )
            for n in range(1, self.N_STALE + 1)
        ]
        # A PROCESSING row attempted just now — younger than the threshold, skip.
        self.fresh_id = _seed_contribution(
            self.client, self.agreement_id, self.N_STALE + 1,
            status=str(ContributionStatus.PROCESSING),
            last_attempt_at=FRESH_INSTANT,
        )
        # A SCHEDULED row at the same stale instant — wrong status, skip.
        self.wrong_status_id = _seed_contribution(
            self.client, self.agreement_id, self.N_STALE + 2,
            status=str(ContributionStatus.SCHEDULED),
            last_attempt_at=STALE_INSTANT,
        )
        self.all_ids = self.stale_ids + [self.fresh_id, self.wrong_status_id]
        self.addCleanup(self._delete_seeded)

    def _delete_seeded(self) -> None:
        for cid in self.all_ids:
            contributions.ref(self.client, cid).delete()

    def test_returns_only_stale_processing_and_paginates(self):
        """Only stale PROCESSING rows come back, in (lastAttemptAt, id) order, and
        walking the cursor over the shared instant visits each exactly once."""
        collected: list[str] = []
        seen: set[str] = set()
        cursor = None
        pages = 0
        while True:
            page, cursor = contributions.stuck_processing(
                self.client, older_than=THRESHOLD, limit=2, start_after=cursor,
            )
            pages += 1
            self.assertLess(pages, 50, "pagination failed to terminate")
            for row in page:
                # Query predicate holds on read-back: PROCESSING + stale.
                self.assertEqual(row["status"], str(ContributionStatus.PROCESSING))
                self.assertNotIn(
                    row["id"], seen,
                    "cursor duplicated a row across the shared-instant boundary",
                )
                seen.add(row["id"])
                collected.append(row["id"])
            if cursor is None:
                break
            # A page that handed back a cursor must have been exactly ``limit`` long.
            self.assertEqual(len(page), 2)

        # The fresh (too-young) and wrong-status rows are never returned.
        self.assertNotIn(self.fresh_id, seen)
        self.assertNotIn(self.wrong_status_id, seen)
        # My four stranded rows appear exactly once each, in ascending-id order —
        # the ids are already listed in (shared-instant, id) order, so equality of
        # the filtered projection proves no skip / no dup across the boundary.
        mine_in_order = [cid for cid in collected if cid in set(self.stale_ids)]
        self.assertEqual(mine_in_order, self.stale_ids)

    def test_full_page_yields_cursor_short_page_does_not(self):
        """limit>=N_STALE returns the stranded set in one terminal page (cursor None)."""
        page, cursor = contributions.stuck_processing(
            self.client, older_than=THRESHOLD, limit=self.N_STALE + 10,
        )
        returned = {r["id"] for r in page}
        self.assertTrue(set(self.stale_ids).issubset(returned))
        self.assertNotIn(self.fresh_id, returned)
        self.assertNotIn(self.wrong_status_id, returned)
        # A page shorter than ``limit`` signals the terminal page.
        self.assertLess(len(page), self.N_STALE + 10)
        self.assertIsNone(cursor)


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class StaleStartedAttemptsScanTests(SimpleTestCase):
    databases: list[str] = []

    def setUp(self) -> None:
        self.client = get_client()
        # Distinct agreement per parent so contribution ids don't collide.
        base = f"scan_stale_{uuid.uuid4().hex[:8]}"
        self.cid_moved = f"ben_{base}_moved__001"
        self.cid_processing = f"ben_{base}_proc__001"
        self.cid_fresh = f"ben_{base}_fresh__001"
        self.cid_succeeded = f"ben_{base}_ok__001"
        self.parent_cids = [
            self.cid_moved, self.cid_processing, self.cid_fresh, self.cid_succeeded,
        ]
        self.mine = set(self.parent_cids)

        # (a) MOVED contribution (already POSTED) with an orphaned STARTED attempt —
        #     the headline case scan (b) exists for: scan (a) can't see it.
        contributions.ref(self.client, self.cid_moved).set(
            {"status": str(ContributionStatus.POSTED)}
        )
        self.att_moved = _seed_attempt(
            self.client, self.cid_moved, 1,
            status=str(PaymentAttemptStatus.STARTED), started_at=STALE_INSTANT,
        )
        # (b) A still-PROCESSING contribution with a stale STARTED attempt — caught.
        contributions.ref(self.client, self.cid_processing).set(
            {"status": str(ContributionStatus.PROCESSING)}
        )
        self.att_processing = _seed_attempt(
            self.client, self.cid_processing, 1,
            status=str(PaymentAttemptStatus.STARTED), started_at=STALE_INSTANT,
        )
        # (c) A FRESH STARTED attempt (started just now) — younger than threshold, skip.
        contributions.ref(self.client, self.cid_fresh).set(
            {"status": str(ContributionStatus.PROCESSING)}
        )
        self.att_fresh = _seed_attempt(
            self.client, self.cid_fresh, 1,
            status=str(PaymentAttemptStatus.STARTED), started_at=FRESH_INSTANT,
        )
        # (d) An old but SUCCEEDED attempt — wrong status, skip.
        contributions.ref(self.client, self.cid_succeeded).set(
            {"status": str(ContributionStatus.POSTED)}
        )
        self.att_succeeded = _seed_attempt(
            self.client, self.cid_succeeded, 1,
            status=str(PaymentAttemptStatus.SUCCEEDED), started_at=STALE_INSTANT,
        )
        self.addCleanup(self._delete_seeded)

    def _delete_seeded(self) -> None:
        for cid in self.parent_cids:
            attempts.ref(self.client, cid, 1).delete()
            contributions.ref(self.client, cid).delete()

    def test_collection_group_catches_started_on_moved_contribution(self):
        """The collection-group scan returns stale STARTED attempts across parents —
        including one whose contribution has moved off PROCESSING — and skips a
        fresh STARTED and an old SUCCEEDED. limit=1 forces multi-page pagination."""
        collected: list[dict] = []
        seen: set[str] = set()
        cursor = None
        pages = 0
        while True:
            page, cursor = contributions.stale_started_attempts(
                self.client, older_than=THRESHOLD, limit=1, start_after=cursor,
            )
            pages += 1
            self.assertLess(pages, 200, "pagination failed to terminate")
            for row in page:
                self.assertNotIn(row["id"], seen, "cursor duplicated an attempt")
                seen.add(row["id"])
                # Every returned attempt satisfies the predicate.
                self.assertEqual(row["status"], str(PaymentAttemptStatus.STARTED))
                collected.append(row)
            if cursor is None:
                break
            self.assertEqual(len(page), 1)

        # Restrict to attempts under MY parents (the group scan is emulator-global).
        mine = [r for r in collected if r.get("contributionId") in self.mine]
        mine_ids = {r["id"] for r in mine}

        # The orphaned STARTED attempt on the MOVED (POSTED) contribution is caught,
        # and it carries its contributionId so the sweeper can reconcile the parent.
        self.assertIn(self.att_moved, mine_ids)
        moved_row = next(r for r in mine if r["id"] == self.att_moved)
        self.assertEqual(moved_row["contributionId"], self.cid_moved)
        # The stale STARTED attempt on the still-PROCESSING contribution too.
        self.assertIn(self.att_processing, mine_ids)
        # Fresh STARTED (too young) and old SUCCEEDED (wrong status) are excluded.
        self.assertNotIn(self.att_fresh, mine_ids)
        self.assertNotIn(self.att_succeeded, mine_ids)
        # Exactly the two stale STARTED attempts, no more, among my parents.
        self.assertEqual(mine_ids, {self.att_moved, self.att_processing})
