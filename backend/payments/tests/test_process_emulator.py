"""Emulator integration tests for ``process_contribution`` (specs/09 §9.1, 17 §17.2).

Covers the multi-document atomicity of a posting (contribution + attempt + loan +
agreement + events commit together), the exception coupling on failure
(deterministic single exception, ``occurrenceCount`` on repeat), and the
resolve-on-post pointer behaviour. Exercises the SERVICE layer directly.
"""

from __future__ import annotations

import os
import unittest

from django.test import SimpleTestCase, tag

from common.enums import ContributionStatus, ExceptionStatus, PaymentAttemptStatus
from common.firestore import get_client
from payments import service
from payments.tests import fixtures
from repositories import refs

EMULATOR = bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))


def _events(client, agreement_id: str, event_type: str) -> list[dict]:
    query = (
        client.collection(refs.SERVICING_EVENTS)
        .where(filter=refs.field_filter("benefitAgreementId", "==", agreement_id))
        .where(filter=refs.field_filter("eventType", "==", event_type))
    )
    return refs.stream_to_dicts(query)


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class ProcessContributionSuccessTests(SimpleTestCase):
    databases: list[str] = []

    def test_success_posts_atomically_and_resolves_exception(self):
        client = get_client()
        key = fixtures.unique_key("succ")
        g = fixtures.seed_payment_graph(
            client,
            key,
            total_commitment_cents=1_000_000,
            scheduled_amount_cents=100_000,
            amount_paid_cents=0,
            loan_balance_cents=2_000_000,
            open_exception_count=1,
            with_open_exception=True,
        )
        exc_id = f"{g.contribution_id(1)}__PAYMENT_FAILED"
        ctx = fixtures.make_ctx(f"proc_{key}")

        result = service.process_contribution(g.contribution_id(1), ctx, client=client)

        # --- command result ------------------------------------------------
        self.assertEqual(result["status"], str(ContributionStatus.POSTED))
        self.assertEqual(result["postedAmountCents"], 100_000)

        # --- contribution POSTED ------------------------------------------
        contribution = g.contribution(1)
        self.assertEqual(contribution["status"], str(ContributionStatus.POSTED))
        self.assertEqual(contribution["postedAmountCents"], 100_000)
        self.assertIsNone(contribution["currentExceptionId"])

        # --- loan balance decreased by exactly the posted amount ----------
        loan = g.loan()
        self.assertEqual(loan["currentBalanceCents"], 1_900_000)
        # openExceptionCount decremented on resolve.
        self.assertEqual(loan["openExceptionCount"], 0)

        # --- agreement amountPaid increased -------------------------------
        agreement = g.agreement()
        self.assertEqual(agreement["amountPaidCents"], 100_000)
        self.assertEqual(agreement["remainingCommitmentCents"], 900_000)
        # not fully funded -> still ACTIVE.
        self.assertEqual(agreement["status"], "ACTIVE")

        # --- exactly one attempt, SUCCEEDED -------------------------------
        atts = g.attempts_for(1)
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["status"], str(PaymentAttemptStatus.SUCCEEDED))

        # --- exactly one PAYMENT_POSTED event -----------------------------
        posted_events = _events(client, g.agreement_id, "PAYMENT_POSTED")
        self.assertEqual(len(posted_events), 1)

        # --- the coupled exception is RESOLVED ----------------------------
        exc = g.exception(exc_id)
        self.assertIsNotNone(exc)
        self.assertEqual(exc["status"], str(ExceptionStatus.RESOLVED))


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class ProcessContributionFailureTests(SimpleTestCase):
    databases: list[str] = []

    def test_failure_leaves_balances_untouched_and_couples_one_exception(self):
        client = get_client()
        key = fixtures.unique_key("fail")
        g = fixtures.seed_payment_graph(
            client,
            key,
            total_commitment_cents=1_000_000,
            scheduled_amount_cents=100_000,
            loan_balance_cents=2_000_000,
            open_exception_count=0,
            simulated_outcome="SERVICER_UNAVAILABLE",
        )
        ctx = fixtures.make_ctx(f"proc_{key}")

        result = service.process_contribution(g.contribution_id(1), ctx, client=client)

        # A declined payment is a *successful command* with status FAILED.
        self.assertEqual(result["status"], str(ContributionStatus.FAILED))
        self.assertEqual(result["failureCode"], "SERVICER_UNAVAILABLE")

        # --- contribution FAILED, exception pointer set -------------------
        contribution = g.contribution(1)
        self.assertEqual(contribution["status"], str(ContributionStatus.FAILED))
        exc_id = f"{g.contribution_id(1)}__PAYMENT_FAILED"
        self.assertEqual(contribution["currentExceptionId"], exc_id)
        self.assertEqual(contribution["failureCode"], "SERVICER_UNAVAILABLE")

        # --- balances UNCHANGED (money never moved) -----------------------
        loan = g.loan()
        self.assertEqual(loan["currentBalanceCents"], 2_000_000)
        self.assertEqual(loan["openExceptionCount"], 1)  # new open exception
        agreement = g.agreement()
        self.assertEqual(agreement["amountPaidCents"], 0)
        self.assertEqual(agreement["remainingCommitmentCents"], 1_000_000)

        # --- a single deterministic PAYMENT_FAILED exception, count 1 -----
        exc = g.exception(exc_id)
        self.assertIsNotNone(exc)
        self.assertEqual(exc["status"], str(ExceptionStatus.OPEN))
        self.assertEqual(exc["occurrenceCount"], 1)
        self.assertEqual(exc["exceptionType"], "PAYMENT_FAILED")

        # --- exactly one PAYMENT_FAILED event -----------------------------
        failed_events = _events(client, g.agreement_id, "PAYMENT_FAILED")
        self.assertEqual(len(failed_events), 1)

    def test_repeated_failure_bumps_occurrence_on_one_exception(self):
        """A retry that fails again upserts the SAME exception (occurrenceCount++),
        never a second row, and does not double-bump openExceptionCount."""
        client = get_client()
        key = fixtures.unique_key("occ")
        g = fixtures.seed_payment_graph(
            client,
            key,
            scheduled_amount_cents=100_000,
            loan_balance_cents=2_000_000,
            simulated_outcome="SERVICER_UNAVAILABLE",
        )
        exc_id = f"{g.contribution_id(1)}__PAYMENT_FAILED"

        # First failure.
        service.process_contribution(
            g.contribution_id(1), fixtures.make_ctx(f"proc1_{key}"), client=client
        )
        self.assertEqual(g.exception(exc_id)["occurrenceCount"], 1)
        self.assertEqual(g.loan()["openExceptionCount"], 1)

        # Retry (FAILED -> RETRY_PENDING -> PROCESSING) fails again.
        retry_result = service.retry_contribution(
            g.contribution_id(1),
            fixtures.make_ctx(
                f"retry_{key}", path=f"/contributions/{g.contribution_id(1)}/retry"
            ),
            client=client,
        )
        self.assertEqual(retry_result["status"], str(ContributionStatus.FAILED))

        # Same single exception, occurrenceCount bumped to 2.
        exc = g.exception(exc_id)
        self.assertEqual(exc["occurrenceCount"], 2)
        self.assertEqual(exc["status"], str(ExceptionStatus.OPEN))
        # openExceptionCount NOT bumped again (still one open exception).
        self.assertEqual(g.loan()["openExceptionCount"], 1)

        # Exactly one exception doc exists for this contribution entity.
        rows = refs.stream_to_dicts(
            client.collection(refs.OPERATIONAL_EXCEPTIONS).where(
                filter=refs.field_filter("entityId", "==", g.contribution_id(1))
            )
        )
        self.assertEqual(len(rows), 1)
