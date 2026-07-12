"""Emulator integration tests for ``contributions.due`` cursor pagination.

``due`` feeds the enqueue-due run (specs/14): it must page a large due set with a
**stable total order** so a fan-out never skips or double-enqueues an installment.
The hard case is that every installment's ``scheduledDate`` is noon in
SYSTEM_TIMEZONE (specs/07 §7.3), so a whole month's contributions share one
timestamp — ordering on ``scheduledDate`` alone is a non-deterministic cursor that
skips/dups at a page boundary landing inside the shared instant. ``due`` breaks the
tie on ``__name__`` (the document id) so each cursor pins an exact position.

These tests seed N contributions that all share ONE noon ``scheduledDate`` and
verify: a page is exactly ``limit`` long, rows come back in ``(scheduledDate, id)``
order, walking the returned cursor visits every row exactly once across the
shared-timestamp boundary, and a short/empty terminal page returns cursor ``None``.
Exercises the repository against real Firestore transactions (no HTTP / auth).

The seed uses a far-past ``scheduledDate`` so the collection-wide ``due`` query is
isolated from any present-dated contributions other suites leave in the emulator;
each seeded doc is deleted on cleanup.
"""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime

from django.test import SimpleTestCase, tag

from common.enums import ContributionStatus
from common.firestore import get_client
from common.ids import contribution_id as _contribution_id
from common.periods import SYSTEM_TIMEZONE
from repositories import contributions, stamp_create

EMULATOR = bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))

ACTOR_ID = "user_test_worker"
CURRENCY = "USD"

# Six installments (> the small `limit`s used below) all sharing ONE noon instant
# so pagination boundaries fall *inside* a shared ``scheduledDate``. Far-past so
# `scheduledDate <= as_of` never sweeps in present-dated fixtures from other suites.
N = 6
DUE_INSTANT = datetime(1987, 6, 15, 12, 0, 0, tzinfo=SYSTEM_TIMEZONE)


def _seed_due_contribution(client, agreement_id: str, installment_number: int) -> None:
    """Write one SCHEDULED contribution at the shared ``DUE_INSTANT`` (specs/04 §4.7)."""
    cid = _contribution_id(agreement_id, installment_number)
    doc = {
        "benefitAgreementId": agreement_id,
        "installmentNumber": installment_number,
        "borrowerId": f"bor_{agreement_id}",
        "borrowerName": "Due Fixture",
        "employerId": f"emp_{agreement_id}",
        "employerName": "Due Fixture Corp",
        "loanId": f"loan_{agreement_id}",
        "currency": CURRENCY,
        "scheduledDate": DUE_INSTANT,  # shared noon instant — the tiebreak stressor
        "periodLabel": f"{DUE_INSTANT:%Y-%m}",
        "scheduledAmountCents": 50_000,  # integer cents (money convention)
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
    stamp_create(doc, ACTOR_ID)
    contributions.ref(client, cid).set(doc)


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class ContributionsDuePaginationTests(SimpleTestCase):
    databases: list[str] = []

    def setUp(self) -> None:
        self.client = get_client()
        # Unique agreement id per method: deterministic contribution ids
        # ``{agreement}__001..00N`` sort ascending == installment order.
        self.agreement_id = f"ben_due_{uuid.uuid4().hex[:10]}"
        self.expected_ids = [
            _contribution_id(self.agreement_id, n) for n in range(1, N + 1)
        ]
        for n in range(1, N + 1):
            _seed_due_contribution(self.client, self.agreement_id, n)
        self.addCleanup(self._delete_seeded)

    def _delete_seeded(self) -> None:
        for cid in self.expected_ids:
            contributions.ref(self.client, cid).delete()

    def test_cursor_pagination_walks_all_across_shared_timestamp(self):
        """limit=3 over N=6 (one shared instant): pages 3,3, then empty terminal."""
        as_of = DUE_INSTANT

        collected: list[str] = []
        seen: set[str] = set()
        cursor = None
        pages = 0
        while True:
            page, cursor = contributions.due(
                self.client, as_of, limit=3, start_after=cursor
            )
            pages += 1
            self.assertLess(pages, 10, "pagination failed to terminate")
            for row in page:
                # Query predicate holds and money stays integer cents on read-back.
                self.assertEqual(row["status"], str(ContributionStatus.SCHEDULED))
                self.assertIsInstance(row["scheduledAmountCents"], int)
                self.assertNotIn(
                    row["id"],
                    seen,
                    "cursor duplicated a row across the shared-timestamp boundary",
                )
                seen.add(row["id"])
                collected.append(row["id"])
            if cursor is None:
                break
            # A page that handed back a cursor must have been exactly `limit` long.
            self.assertEqual(len(page), 3)

        # Every installment, exactly once, in (scheduledDate, id) order — the ids
        # are already listed in that order, so equality proves no skip / no dup.
        self.assertEqual(collected, self.expected_ids)
        self.assertEqual(len(seen), N)
        # 6 / 3 -> two full pages, then a third call that reveals termination with
        # an empty page and cursor None (the exact-multiple terminal case).
        self.assertEqual(pages, 3)

    def test_partial_and_oversized_pages_return_no_cursor(self):
        """A short final page and a limit>=N page both signal terminal (cursor None)."""
        as_of = DUE_INSTANT

        # limit=4 over N=6: one full page (with cursor), then a 2-row page (< limit).
        page1, c1 = contributions.due(self.client, as_of, limit=4)
        self.assertEqual(len(page1), 4)
        self.assertIsNotNone(c1)
        self.assertEqual([r["id"] for r in page1], self.expected_ids[:4])

        page2, c2 = contributions.due(self.client, as_of, limit=4, start_after=c1)
        self.assertEqual(len(page2), 2)
        self.assertIsNone(c2, "a partial (< limit) page must return no cursor")
        self.assertEqual([r["id"] for r in page2], self.expected_ids[4:])

        # No gap and no overlap at the boundary that split the shared instant.
        self.assertEqual(
            [r["id"] for r in page1] + [r["id"] for r in page2], self.expected_ids
        )

        # limit >= N: the whole set comes back in one terminal page (cursor None).
        whole, cw = contributions.due(self.client, as_of, limit=N + 5)
        self.assertEqual(len(whole), N)
        self.assertIsNone(cw)
        self.assertEqual([r["id"] for r in whole], self.expected_ids)
