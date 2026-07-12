"""Lease-reaper reclamation emulator tests (specs/08 §8.3).

Must-pass reclamation path:

* A ``PENDING`` ``PROCESS_CONTRIBUTION`` key whose lease has expired, with a live
  ``STARTED`` attempt (a driver that crashed after Phase 2 but before finalize),
  is reconciled to completion by ``reap-expired-leases`` — the contribution posts
  **exactly once** (no double charge) and the idempotency record ends
  ``COMPLETED``.

Must-not-touch guard:

* A ``PENDING`` record whose lease is still valid, and a healthy in-flight async
  key (``leaseExpiresAt`` well in the future, i.e. within ``ASYNC_LEASE_TTL``),
  are left strictly untouched — the ``leaseExpiresAt < now`` query excludes them.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase, tag

from common.enums import ContributionStatus, IdempotencyStatus, PaymentAttemptStatus
from common.firestore import get_client
from idempotency.reaper import reap_expired_leases
from internal.system_context import system_ctx
from payments import service
from payments.adapter import (
    SimulatedCrash,
    SimulatedPaymentAdapter,
    crash_after_charge,
)
from payments.tests import fixtures
from repositories import refs

EMULATOR = bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class ReapProcessContributionTests(SimpleTestCase):
    databases: list[str] = []

    def test_expired_process_key_reconciled_to_completion_no_double_charge(self):
        client = get_client()
        key = fixtures.unique_key("reap")
        g = fixtures.seed_payment_graph(
            client,
            key,
            total_commitment_cents=1_000_000,
            scheduled_amount_cents=100_000,
            loan_balance_cents=2_000_000,
        )
        cid = g.contribution_id(1)
        proc_key = g.processor_key(1, 1)
        idem_key = f"proc_{key}"
        ctx = fixtures.make_ctx(idem_key, path=f"/contributions/{cid}/process")
        adapter = SimulatedPaymentAdapter(client)

        # --- Phase 2 crash: charge persists, driver dies before finalize ----
        with crash_after_charge():
            with self.assertRaises(SimulatedCrash):
                service.process_contribution(cid, ctx, client=client, adapter=adapter)

        # Left stuck: PROCESSING + a STARTED attempt + a PENDING idempotency key.
        self.assertEqual(g.contribution(1)["status"], str(ContributionStatus.PROCESSING))
        self.assertEqual(
            g.attempts_for(1)[0]["status"], str(PaymentAttemptStatus.STARTED)
        )
        idem_ref = client.collection(refs.IDEMPOTENCY_KEYS).document(idem_key)
        rec = idem_ref.get().to_dict()
        self.assertEqual(rec["status"], IdempotencyStatus.PENDING.value)
        self.assertEqual(rec["operation"], "PROCESS_CONTRIBUTION")
        charges_before = fixtures_charges(client, proc_key)
        self.assertEqual(len(charges_before), 1)

        # Force the lease to have expired (simulate a long-dead driver).
        idem_ref.update({"leaseExpiresAt": _now() - timedelta(hours=1)})

        # --- reap: reconcile the in-flight attempt, complete the key --------
        summary = reap_expired_leases(client, system_ctx("reap-expired-leases"))
        self.assertGreaterEqual(summary["reclaimed"], 1)
        mine = [r for r in summary["results"] if r.get("key") == idem_key]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["action"], "reconciled")

        # Contribution posted exactly once; balance moved once.
        contribution = g.contribution(1)
        self.assertEqual(contribution["status"], str(ContributionStatus.POSTED))
        self.assertEqual(contribution["postedAmountCents"], 100_000)
        self.assertEqual(g.loan()["currentBalanceCents"], 1_900_000)
        atts = g.attempts_for(1)
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["status"], str(PaymentAttemptStatus.SUCCEEDED))

        # No double charge: still exactly one ledger row for the processor key.
        self.assertEqual(len(fixtures_charges(client, proc_key)), 1)

        # The idempotency record is now COMPLETED (key no longer wedged PENDING).
        done = idem_ref.get().to_dict()
        self.assertEqual(done["status"], IdempotencyStatus.COMPLETED.value)


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class ReapLeavesHealthyKeysAloneTests(SimpleTestCase):
    databases: list[str] = []

    def test_valid_lease_and_healthy_async_key_untouched(self):
        client = get_client()
        suffix = fixtures.unique_key("healthy")
        now = _now()

        valid_key = f"sync_{suffix}"
        async_key = f"async_{suffix}"
        # A short sync lease still in the future.
        _seed_pending(client, valid_key, "resume-benefit", "agr_valid",
                      lease_expires=now + timedelta(minutes=1))
        # A healthy async key mid-flight (well within ASYNC_LEASE_TTL).
        _seed_pending(client, async_key, "activate-benefit", "agr_async",
                      lease_expires=now + timedelta(minutes=25))

        summary = reap_expired_leases(client, system_ctx("reap-expired-leases"))

        # Neither appears in the reap results, and both stay PENDING with their
        # original lease owner (untouched — the query excludes future leases).
        touched = {r.get("key") for r in summary["results"]}
        self.assertNotIn(valid_key, touched)
        self.assertNotIn(async_key, touched)
        for k in (valid_key, async_key):
            rec = client.collection(refs.IDEMPOTENCY_KEYS).document(k).get().to_dict()
            self.assertEqual(rec["status"], IdempotencyStatus.PENDING.value)
            self.assertEqual(rec["leaseOwner"], "driver_original")


def fixtures_charges(client, proc_key):
    return refs.stream_to_dicts(
        client.collection(refs.SIMULATED_CHARGES).where(
            filter=refs.field_filter("processorIdempotencyKey", "==", proc_key)
        )
    )


def _seed_pending(client, key, operation, entity_id, *, lease_expires):
    client.collection(refs.IDEMPOTENCY_KEYS).document(key).set(
        {
            "operation": operation,
            "status": IdempotencyStatus.PENDING.value,
            "requestHash": "h",
            "entityId": entity_id,
            "entityType": "benefitAgreement",
            "leaseOwner": "driver_original",
            "leaseExpiresAt": lease_expires,
            "result": None,
            "completedAt": None,
            "createdAt": _now(),
            "updatedAt": _now(),
        }
    )
