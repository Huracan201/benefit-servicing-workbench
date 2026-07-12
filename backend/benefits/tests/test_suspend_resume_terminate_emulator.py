"""Emulator integration tests for suspend / resume / terminate (specs/10 §10.2-§10.4).

Exercises the SERVICE layer directly (no HTTP):

* **suspend then resume** — ``ACTIVE → SUSPENDED`` (``acceptingPayments`` false,
  ``suspendedReason`` MANUAL) → ``ACTIVE``; the resume's inline schedule-shift
  re-dates the remaining ``SCHEDULED`` installments forward + extends
  ``endDate`` and writes a ``SCHEDULE_SHIFTED`` event, while a ``RETRY_PENDING``
  installment (a past obligation) is left in place.
* **terminate** — ``ACTIVE → TERMINATED``; the inline cancel-future task cancels
  every ``SCHEDULED`` / ``RETRY_PENDING`` / ``FAILED`` contribution (dismissing
  the FAILED row's open exception), skips ``PROCESSING``, writes a
  ``FUTURE_CONTRIBUTIONS_CANCELED`` event, and leaves a past ``POSTED`` row
  untouched.
"""

from __future__ import annotations

import os
import unittest

from django.test import SimpleTestCase, tag

from benefits.services import resume_benefit, suspend_benefit, terminate_benefit
from benefits.tests.domain_graph import count_events, make_ctx, seed_active_graph, unique_key
from common.enums import BenefitStatus, ContributionStatus, ExceptionStatus
from common.firestore import get_client
from common.periods import scheduled_datetime, shift_months
from repositories import agreements

EMULATOR = bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class SuspendResumeTests(SimpleTestCase):
    databases: list[str] = []

    def test_suspend_then_resume_shifts_scheduled_but_not_retry_pending(self):
        client = get_client()
        key = unique_key("susp")
        # term=4: installment 1 is a past obligation (RETRY_PENDING, must NOT
        # be re-dated); 2..4 are future SCHEDULED (must shift forward).
        g = seed_active_graph(
            client,
            key,
            term_months=4,
            statuses={1: ContributionStatus.RETRY_PENDING},
        )
        start_date = g.start_date

        # --- suspend: ACTIVE -> SUSPENDED ---------------------------------
        susp = suspend_benefit(agreement_id=g.agreement_id, ctx=make_ctx(), client=client)
        self.assertEqual(susp["status"], str(BenefitStatus.SUSPENDED))
        self.assertFalse(susp["acceptingPayments"])
        self.assertEqual(susp["suspendedReason"], "MANUAL")

        agreement = g.agreement()
        self.assertEqual(agreement["status"], str(BenefitStatus.SUSPENDED))
        self.assertFalse(agreement["acceptingPayments"])
        self.assertEqual(agreement["suspendedReason"], "MANUAL")
        self.assertEqual(g.loan()["benefitStatus"], str(BenefitStatus.SUSPENDED))
        self.assertEqual(
            count_events(client, event_type="BENEFIT_SUSPENDED", agreement_id=g.agreement_id),
            1,
        )

        # Simulate a real elapsed suspension: back-date suspendedAt two months so
        # the resume computes a non-zero shift (a same-instant resume is 0 months).
        suspended_from = scheduled_datetime(shift_months(start_date, -2), 1)
        agreements.ref(client, g.agreement_id).update({"suspendedAt": suspended_from})

        # capture the RETRY_PENDING installment date BEFORE resume
        retry_date_before = g.contribution(1)["scheduledDate"]

        # --- resume: SUSPENDED -> ACTIVE + inline schedule shift ----------
        res = resume_benefit(agreement_id=g.agreement_id, ctx=make_ctx(), client=client)
        self.assertEqual(res["status"], str(BenefitStatus.ACTIVE))
        self.assertTrue(res["acceptingPayments"])
        self.assertIsNone(res["suspendedReason"])

        agreement = g.agreement()
        self.assertEqual(agreement["status"], str(BenefitStatus.ACTIVE))
        self.assertTrue(agreement["acceptingPayments"])
        self.assertIsNone(agreement.get("suspendedReason"))

        # --- SCHEDULED installments 2..4 re-dated forward ------------------
        # Derive the expected shift from what the command actually persisted
        # rather than hard-coding 2: production rounds a partial trailing month
        # UP, so back-dating suspendedAt by 2 months lands on a 3-month shift on
        # calendar edges (e.g. resume on Jan 31 / Apr 30 / Aug 31, where clamping
        # makes startDate+2mo precede the resume date). Assert it was still a real
        # (>= 2 month) shift, then anchor every date assertion to it — shift is
        # anchored to the immutable startDate: target(n) =
        # scheduled_datetime(startDate + shiftMonths, n).
        shift_applied = agreement["scheduleShiftMonths"]
        self.assertGreaterEqual(shift_applied, 2)
        effective_start = shift_months(start_date, shift_applied)
        for n in (2, 3, 4):
            expected = scheduled_datetime(effective_start, n)
            self.assertEqual(
                g.contribution(n)["scheduledDate"],
                expected,
                f"installment {n} must shift forward {shift_applied} months",
            )

        # --- RETRY_PENDING installment 1 is NOT re-dated ------------------
        self.assertEqual(g.contribution(1)["scheduledDate"], retry_date_before)
        self.assertEqual(g.contribution(1)["status"], str(ContributionStatus.RETRY_PENDING))

        # --- endDate extended to the last shifted installment -------------
        self.assertEqual(agreement["endDate"], scheduled_datetime(effective_start, 4))

        # --- exactly one SCHEDULE_SHIFTED + one BENEFIT_RESUMED event -----
        self.assertEqual(
            count_events(client, event_type="SCHEDULE_SHIFTED", agreement_id=g.agreement_id),
            1,
        )
        self.assertEqual(
            count_events(client, event_type="BENEFIT_RESUMED", agreement_id=g.agreement_id),
            1,
        )


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class TerminateTests(SimpleTestCase):
    databases: list[str] = []

    def test_terminate_cancels_future_dismisses_failed_and_skips_processing(self):
        client = get_client()
        key = unique_key("term")
        # term=5 mixed statuses: 1 POSTED (past, untouched), 2 SCHEDULED,
        # 3 RETRY_PENDING, 4 FAILED (+ open exception), 5 PROCESSING (skipped).
        g = seed_active_graph(
            client,
            key,
            term_months=5,
            loan_open_exception_count=1,
            statuses={
                1: ContributionStatus.POSTED,
                2: ContributionStatus.SCHEDULED,
                3: ContributionStatus.RETRY_PENDING,
                4: ContributionStatus.FAILED,
                5: ContributionStatus.PROCESSING,
            },
            exception_on=4,
        )
        exc_id = f"{g.contribution_id(4)}__PAYMENT_FAILED"

        # --- terminate: ACTIVE -> TERMINATED (+ inline cancel-future) ------
        result = terminate_benefit(agreement_id=g.agreement_id, ctx=make_ctx(), client=client)
        self.assertEqual(result["status"], str(BenefitStatus.TERMINATED))
        self.assertFalse(result["acceptingPayments"])

        agreement = g.agreement()
        self.assertEqual(agreement["status"], str(BenefitStatus.TERMINATED))
        self.assertFalse(agreement["acceptingPayments"])
        self.assertEqual(g.loan()["benefitStatus"], str(BenefitStatus.TERMINATED))

        # --- cancellations: SCHEDULED / RETRY_PENDING / FAILED -> CANCELED -
        self.assertEqual(g.contribution(2)["status"], str(ContributionStatus.CANCELED))
        self.assertEqual(g.contribution(3)["status"], str(ContributionStatus.CANCELED))
        self.assertEqual(g.contribution(4)["status"], str(ContributionStatus.CANCELED))

        # --- PROCESSING skipped; past POSTED untouched --------------------
        self.assertEqual(g.contribution(5)["status"], str(ContributionStatus.PROCESSING))
        self.assertEqual(g.contribution(1)["status"], str(ContributionStatus.POSTED))

        # --- FAILED row's open exception dismissed + count decremented ----
        self.assertEqual(g.exception(exc_id)["status"], str(ExceptionStatus.DISMISSED))
        self.assertIsNone(g.contribution(4)["currentExceptionId"])
        self.assertEqual(g.loan()["openExceptionCount"], 0)

        # --- loan look-ahead nulled + completion event --------------------
        self.assertIsNone(g.loan()["nextContributionDate"])
        self.assertIsNone(g.loan()["nextContributionAmountCents"])
        self.assertEqual(
            count_events(
                client, event_type="FUTURE_CONTRIBUTIONS_CANCELED", loan_id=g.loan_id
            ),
            1,
        )
        # one BENEFIT_TERMINATED + one PAYMENT_CANCELED per cancelled row (3)
        self.assertEqual(
            count_events(client, event_type="BENEFIT_TERMINATED", agreement_id=g.agreement_id),
            1,
        )
