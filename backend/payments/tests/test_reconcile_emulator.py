"""Crash-recovery & fencing emulator tests (specs/08 §8.4, specs/09 §9.4, 17 §17.2).

Two must-pass recovery paths:

* **FENCING / recovery — charge succeeded, finalize never ran.** The crash
  toggle persists the processor charge and then raises before Phase 3; the
  contribution is left ``PROCESSING``. The reconciliation sweeper re-queries the
  processor and posts **exactly once**, with **no double charge**.
* **NOT_FOUND -> fenced (the F1 double-charge gate).** The sweeper queries an
  unsubmitted key: the simulator fences it and returns ``NOT_FOUND``; the sweeper
  reverts the contribution for a clean retry. A *delayed original* ``charge`` on
  that fenced key then arrives — and is REJECTED (``NOT_SUBMITTED``).
"""

from __future__ import annotations

import os
import unittest

from django.test import SimpleTestCase, tag

from common.enums import ContributionStatus, PaymentAttemptStatus, PaymentFailureCode
from common.firestore import get_client
from contributions.reconcile import reconcile_contribution
from payments import service
from payments.adapter import (
    SimulatedCrash,
    SimulatedPaymentAdapter,
    crash_after_charge,
)
from payments.tests import fixtures
from repositories import refs

EMULATOR = bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class ReconcileAfterCrashTests(SimpleTestCase):
    databases: list[str] = []

    def test_charge_succeeded_finalize_skipped_reconcile_posts_once(self):
        client = get_client()
        key = fixtures.unique_key("crash")
        g = fixtures.seed_payment_graph(
            client,
            key,
            total_commitment_cents=1_000_000,
            scheduled_amount_cents=100_000,
            loan_balance_cents=2_000_000,
        )
        cid = g.contribution_id(1)
        proc_key = g.processor_key(1, 1)
        ctx = fixtures.make_ctx(f"proc_{key}", path=f"/contributions/{cid}/process")
        adapter = SimulatedPaymentAdapter(client)

        # --- Phase 2 crash: charge persists, process dies before finalize --
        with crash_after_charge():
            with self.assertRaises(SimulatedCrash):
                service.process_contribution(cid, ctx, client=client, adapter=adapter)

        # Stuck PROCESSING with a STARTED attempt; the charge row survives.
        stuck = g.contribution(1)
        self.assertEqual(stuck["status"], str(ContributionStatus.PROCESSING))
        self.assertEqual(g.attempts_for(1)[0]["status"], str(PaymentAttemptStatus.STARTED))
        charges_before = refs.stream_to_dicts(
            client.collection(refs.SIMULATED_CHARGES).where(
                filter=refs.field_filter("processorIdempotencyKey", "==", proc_key)
            )
        )
        self.assertEqual(len(charges_before), 1)

        # --- sweeper reconciles: get_status == SUCCEEDED -> POSTED ---------
        result = reconcile_contribution(cid, client=client, adapter=adapter)
        self.assertEqual(result["status"], str(ContributionStatus.POSTED))

        contribution = g.contribution(1)
        self.assertEqual(contribution["status"], str(ContributionStatus.POSTED))
        self.assertEqual(contribution["postedAmountCents"], 100_000)

        # Posted exactly once: balance moved once, one attempt (SUCCEEDED).
        self.assertEqual(g.loan()["currentBalanceCents"], 1_900_000)
        self.assertEqual(g.agreement()["amountPaidCents"], 100_000)
        atts = g.attempts_for(1)
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["status"], str(PaymentAttemptStatus.SUCCEEDED))

        # NO double charge: still exactly one ledger row for the key.
        charges_after = refs.stream_to_dicts(
            client.collection(refs.SIMULATED_CHARGES).where(
                filter=refs.field_filter("processorIdempotencyKey", "==", proc_key)
            )
        )
        self.assertEqual(len(charges_after), 1)

        # A PAYMENT_RECONCILED event records the recovery.
        reconciled_events = refs.stream_to_dicts(
            client.collection(refs.SERVICING_EVENTS)
            .where(filter=refs.field_filter("benefitAgreementId", "==", g.agreement_id))
            .where(filter=refs.field_filter("eventType", "==", "PAYMENT_RECONCILED"))
        )
        self.assertEqual(len(reconciled_events), 1)


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class FencingGateTests(SimpleTestCase):
    databases: list[str] = []

    def test_not_found_fences_key_and_rejects_delayed_original_charge(self):
        client = get_client()
        key = fixtures.unique_key("fence")
        g = fixtures.seed_payment_graph(
            client,
            key,
            scheduled_amount_cents=100_000,
            loan_balance_cents=2_000_000,
        )
        cid = g.contribution_id(1)
        proc_key = g.processor_key(1, 1)

        # Driver opened Phase 1 (STARTED attempt) but the charge was never
        # submitted, then died. The sweeper must recover safely.
        fixtures.seed_inflight_attempt(client, g, installment=1, attempt_number=1)

        adapter = SimulatedPaymentAdapter(client)

        # --- sweeper: get_status on the unsubmitted key -> NOT_FOUND (fenced)
        result = reconcile_contribution(cid, client=client, adapter=adapter)
        self.assertEqual(result["finding"], "NOT_FOUND")

        # Contribution reverted to SCHEDULED for a clean retry; attempt FAILED.
        contribution = g.contribution(1)
        self.assertEqual(contribution["status"], str(ContributionStatus.SCHEDULED))
        self.assertIsNone(contribution["currentAttemptId"])
        att = g.attempts_for(1)[0]
        self.assertEqual(att["status"], str(PaymentAttemptStatus.FAILED))
        self.assertEqual(att["failureCode"], str(PaymentFailureCode.NOT_SUBMITTED))

        # Balances untouched (money never moved).
        self.assertEqual(g.loan()["currentBalanceCents"], 2_000_000)
        self.assertEqual(g.agreement()["amountPaidCents"], 0)

        # --- the DELAYED ORIGINAL charge finally arrives on the fenced key -
        late = adapter.charge(
            processor_idempotency_key=proc_key,
            amount_cents=100_000,
            currency=fixtures.CURRENCY,
            metadata={"contributionId": cid},
        )
        # It MUST be rejected — no double charge (the F1 regression gate).
        self.assertEqual(late.status, "FAILED")
        self.assertEqual(late.failure_code, PaymentFailureCode.NOT_SUBMITTED)

        # Ledger holds only the fenced tombstone — no SUCCEEDED charge row.
        rows = refs.stream_to_dicts(
            client.collection(refs.SIMULATED_CHARGES).where(
                filter=refs.field_filter("processorIdempotencyKey", "==", proc_key)
            )
        )
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["status"], "SUCCEEDED")
