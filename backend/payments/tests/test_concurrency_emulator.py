"""CROWN-JEWEL concurrency gate (specs/17 §17.2, specs/08 §8.2, specs/11 §11.6).

Fire **two simultaneous** ``process_contribution`` calls with the **same**
idempotency key at the same ``SCHEDULED`` contribution and assert the design's
central safety property: **exactly one** attempt is created, **exactly one**
posting occurs, the loan balance moves **exactly once**, one caller gets the
POSTED result, and the other is safely rejected (a replay of the same result, a
``202 IN_PROGRESS``, or a ``409`` conflict) — never a second charge.

This is a must-pass gate: it is the clearest single proof the idempotency +
optimistic-concurrency design is sound.
"""

from __future__ import annotations

import os
import threading
import unittest

from django.test import SimpleTestCase, tag

from commands.base import CommandError
from common.enums import ContributionStatus, PaymentAttemptStatus
from common.firestore import get_client
from payments import service
from payments.adapter import SimulatedPaymentAdapter
from payments.tests import fixtures
from repositories import refs

EMULATOR = bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class ConcurrencyGateTests(SimpleTestCase):
    databases: list[str] = []

    def test_two_concurrent_same_key_process_post_exactly_once(self):
        client = get_client()
        key = fixtures.unique_key("cc")
        g = fixtures.seed_payment_graph(
            client,
            key,
            total_commitment_cents=1_000_000,
            scheduled_amount_cents=100_000,
            loan_balance_cents=2_000_000,
        )
        cid = g.contribution_id(1)

        # Both requests carry the SAME idempotency key + request hash (the gate);
        # distinct lease owners model two independent callers.
        shared_key = f"proc_shared_{key}"
        path = f"/contributions/{cid}/process"

        results: list[dict] = []
        errors: list[BaseException] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def worker(owner: str) -> None:
            ctx = fixtures.make_ctx(shared_key, path=path, lease_owner=owner)
            adapter = SimulatedPaymentAdapter(client)
            barrier.wait()  # release both threads as close to simultaneously as possible
            try:
                res = service.process_contribution(cid, ctx, client=client, adapter=adapter)
                with lock:
                    results.append(res)
            except BaseException as exc:  # noqa: BLE001 — capture for assertion
                with lock:
                    errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(f"owner_a_{key}",)),
            threading.Thread(target=worker, args=(f"owner_b_{key}",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        # --- every non-error result must describe the SAME single posting --
        posted_results = [r for r in results if r.get("status") == str(ContributionStatus.POSTED)]
        # At least one caller succeeded; any second success is a replay of it.
        self.assertGreaterEqual(len(posted_results), 1, f"errors={errors}")
        for r in posted_results:
            self.assertEqual(r["postedAmountCents"], 100_000)
            self.assertEqual(r["attemptId"], g.attempt_id(1, 1))

        # --- any error is a benign conflict, never a second money movement -
        for exc in errors:
            self.assertIsInstance(exc, CommandError, f"unexpected error: {exc!r}")
            self.assertIn(exc.http_status, (202, 409))

        # --- HARD INVARIANTS: exactly once --------------------------------
        atts = g.attempts_for(1)
        self.assertEqual(len(atts), 1, "exactly one attempt must exist")
        self.assertEqual(atts[0]["status"], str(PaymentAttemptStatus.SUCCEEDED))

        contribution = g.contribution(1)
        self.assertEqual(contribution["status"], str(ContributionStatus.POSTED))
        self.assertEqual(contribution["attemptCount"], 1)

        # Loan balance moved exactly once (2_000_000 - 100_000).
        self.assertEqual(g.loan()["currentBalanceCents"], 1_900_000)
        agreement = g.agreement()
        self.assertEqual(agreement["amountPaidCents"], 100_000)

        # Exactly one PAYMENT_POSTED event.
        posted_events = refs.stream_to_dicts(
            client.collection(refs.SERVICING_EVENTS)
            .where(filter=refs.field_filter("benefitAgreementId", "==", g.agreement_id))
            .where(filter=refs.field_filter("eventType", "==", "PAYMENT_POSTED"))
        )
        self.assertEqual(len(posted_events), 1)

        # Exactly one processor charge in the simulator ledger.
        charges = refs.stream_to_dicts(
            client.collection(refs.SIMULATED_CHARGES).where(
                filter=refs.field_filter("processorIdempotencyKey", "==", g.processor_key(1, 1))
            )
        )
        self.assertEqual(len(charges), 1, "the charge must have happened exactly once")
