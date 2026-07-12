"""Emulator integration tests for the employment-status cascade (specs/10 §10.4).

Exercises :func:`employment.services.change_employment_status` against real
Firestore transactions (no HTTP), covering the §10.4 benefit cascade:

* **TERMINATED** — borrower → ``TERMINATED``, the active benefit → ``TERMINATED``,
  and every future contribution is cancelled (inline follow-up).
* **LEAVE** — an ``ACTIVE`` benefit → ``SUSPENDED`` with ``suspendedReason ==
  LEAVE``.
* **return to ACTIVE** — auto-resumes **only** a LEAVE-suspended benefit; a
  MANUAL-suspended benefit stays suspended (the manager must resume it).
"""

from __future__ import annotations

import os
import unittest

from django.test import SimpleTestCase, tag

from benefits.tests.domain_graph import make_ctx, seed_active_graph, unique_key
from common.enums import BenefitStatus, ContributionStatus, EmploymentStatus
from common.firestore import get_client
from employment.services import change_employment_status


EMULATOR = bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class EmploymentCascadeTests(SimpleTestCase):
    databases: list[str] = []

    def test_terminated_cascades_to_benefit_and_cancels_future(self):
        client = get_client()
        key = unique_key("empterm")
        g = seed_active_graph(client, key, term_months=3)

        result = change_employment_status(
            borrower_id=g.borrower_id,
            ctx=make_ctx(),
            status=str(EmploymentStatus.TERMINATED),
            client=client,
        )
        self.assertEqual(result["employmentStatus"], str(EmploymentStatus.TERMINATED))
        self.assertEqual(result["benefitCascade"]["action"], "TERMINATED")

        # borrower TERMINATED
        self.assertEqual(
            g.borrower()["employmentStatus"], str(EmploymentStatus.TERMINATED)
        )
        # benefit TERMINATED
        self.assertEqual(g.agreement()["status"], str(BenefitStatus.TERMINATED))
        self.assertEqual(g.loan()["benefitStatus"], str(BenefitStatus.TERMINATED))
        # every (future) contribution cancelled
        for n in (1, 2, 3):
            self.assertEqual(
                g.contribution(n)["status"], str(ContributionStatus.CANCELED)
            )

    def test_leave_suspends_active_benefit_with_reason_leave(self):
        client = get_client()
        key = unique_key("empleave")
        g = seed_active_graph(client, key, term_months=3)

        result = change_employment_status(
            borrower_id=g.borrower_id,
            ctx=make_ctx(),
            status=str(EmploymentStatus.LEAVE),
            client=client,
        )
        self.assertEqual(result["employmentStatus"], str(EmploymentStatus.LEAVE))
        self.assertEqual(result["benefitCascade"]["action"], "SUSPENDED")
        self.assertEqual(result["benefitCascade"]["suspendedReason"], "LEAVE")

        agreement = g.agreement()
        self.assertEqual(agreement["status"], str(BenefitStatus.SUSPENDED))
        self.assertFalse(agreement["acceptingPayments"])
        self.assertEqual(agreement["suspendedReason"], "LEAVE")
        self.assertEqual(g.loan()["benefitStatus"], str(BenefitStatus.SUSPENDED))

    def test_return_active_auto_resumes_only_leave_suspended(self):
        client = get_client()
        key = unique_key("empret")
        # borrower on LEAVE, benefit SUSPENDED for reason LEAVE
        g = seed_active_graph(
            client,
            key,
            term_months=3,
            benefit_status=BenefitStatus.SUSPENDED,
            accepting_payments=False,
            suspended_reason="LEAVE",
            employment_status=EmploymentStatus.LEAVE,
        )

        result = change_employment_status(
            borrower_id=g.borrower_id,
            ctx=make_ctx(),
            status=str(EmploymentStatus.ACTIVE),
            client=client,
        )
        self.assertEqual(result["employmentStatus"], str(EmploymentStatus.ACTIVE))
        self.assertEqual(result["benefitCascade"]["action"], "RESUMED")

        agreement = g.agreement()
        self.assertEqual(agreement["status"], str(BenefitStatus.ACTIVE))
        self.assertTrue(agreement["acceptingPayments"])
        self.assertIsNone(agreement.get("suspendedReason"))
        self.assertEqual(g.borrower()["employmentStatus"], str(EmploymentStatus.ACTIVE))

    def test_return_active_does_not_resume_manual_suspended(self):
        client = get_client()
        key = unique_key("empman")
        # borrower on LEAVE, but benefit SUSPENDED for reason MANUAL -> stays put
        g = seed_active_graph(
            client,
            key,
            term_months=3,
            benefit_status=BenefitStatus.SUSPENDED,
            accepting_payments=False,
            suspended_reason="MANUAL",
            employment_status=EmploymentStatus.LEAVE,
        )

        result = change_employment_status(
            borrower_id=g.borrower_id,
            ctx=make_ctx(),
            status=str(EmploymentStatus.ACTIVE),
            client=client,
        )
        self.assertEqual(result["employmentStatus"], str(EmploymentStatus.ACTIVE))
        self.assertFalse(result["benefitCascade"]["applied"])

        agreement = g.agreement()
        # benefit stays SUSPENDED / MANUAL — manager must resume explicitly
        self.assertEqual(agreement["status"], str(BenefitStatus.SUSPENDED))
        self.assertFalse(agreement["acceptingPayments"])
        self.assertEqual(agreement["suspendedReason"], "MANUAL")
        # borrower still returned to ACTIVE
        self.assertEqual(g.borrower()["employmentStatus"], str(EmploymentStatus.ACTIVE))
