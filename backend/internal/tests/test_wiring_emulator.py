"""Emulator integration tests for the Phase-3 /internal wiring (unit B).

Exercises the registered task/job callables against real Firestore transactions
(``@tag('emulator')``, skipped without ``FIRESTORE_EMULATOR_HOST``):

* the ``process-contribution`` task charges **once** and a redelivery is an
  idempotent no-op; two concurrent posts of the same contribution are fenced to a
  single charge;
* ``enqueue-due-contributions`` enqueues a ``process-contribution`` only for a
  due contribution whose agreement is ``acceptingPayments`` — a non-accepting
  agreement's due row is skipped (asserted against the specific seeded ids, so it
  is robust to other rows living in the shared emulator);
* ``reconcile-stuck-payments`` enqueues a ``reconcile-contribution`` only for a
  contribution stuck ``PROCESSING`` past ``STUCK_THRESHOLD`` — a freshly-attempted
  one is left alone;
* the ``cancel-future-contributions`` task runs the tail **and** completes the
  terminate idempotency key (the shared step-4 completion path).

Job-level tests scan the whole collection, so they mock ``internal.enqueue.enqueue``
to a spy and assert only on the presence/absence of the specific seeded ids.
"""

from __future__ import annotations

import os
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from django.test import SimpleTestCase, tag

from common.enums import (
    BenefitStatus,
    ContributionStatus,
    IdempotencyStatus,
)
from common.firestore import get_client
from internal import jobs, tasks
from internal.system_context import system_ctx
from payments.tests import fixtures
from repositories import agreements, contributions, idempotency_keys, stamp_create

EMULATOR = bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))


def _ctx(job: str):
    return system_ctx(job)


def _seed_pending_key(client, key: str, *, operation: str, entity_id: str) -> None:
    """Seed a live ``PENDING`` idempotency record (as a command's core txn would)."""
    now = datetime.now(timezone.utc)
    idempotency_keys.ref(client, key).set(
        {
            "operation": operation,
            "requestHash": "h",
            "status": str(IdempotencyStatus.PENDING),
            "entityType": "BENEFIT_AGREEMENT",
            "entityId": entity_id,
            "leaseOwner": "seed-owner",
            "leaseExpiresAt": now + timedelta(minutes=10),
            "result": None,
            "completedAt": None,
            "createdAt": now,
            "updatedAt": now,
            "expiresAt": now + timedelta(days=7),
        }
    )


def _seed_processing_contribution(
    client, cid: str, *, agreement_id: str, last_attempt_at: datetime
) -> None:
    """Seed a bare ``PROCESSING`` contribution with a given ``lastAttemptAt``."""
    doc = {
        "benefitAgreementId": agreement_id,
        "installmentNumber": 1,
        "loanId": f"loan_{cid}",
        "borrowerId": f"bor_{cid}",
        "employerId": f"emp_{cid}",
        "currency": "USD",
        "status": str(ContributionStatus.PROCESSING),
        "attemptCount": 1,
        "currentAttemptId": f"{cid}__att_001",
        "lastAttemptAt": last_attempt_at,
        "scheduledAmountCents": 100_000,
    }
    stamp_create(doc, "system:test")
    contributions.ref(client, cid).set(doc)


def _seed_generatable_agreement(client, key: str, *, status: BenefitStatus) -> str:
    """Seed a minimal agreement carrying the fields ``generate_schedule`` reads
    (total/term/startDate) at ``status`` — enough to drive
    ``generate_schedule_task`` to HALT (any non-ACTIVATING status), without a
    loan/borrower/employer (the halt path returns before the finalize reads them).
    """
    agreement_id = f"ben_{key}"
    start_dt = datetime.now(timezone.utc).replace(
        hour=16, minute=0, second=0, microsecond=0
    )
    doc = {
        "totalCommitmentCents": 600_000,
        "termMonths": 6,
        "plannedInstallmentCount": 6,
        "installmentsGenerated": 0,
        "startDate": start_dt,
        "status": str(status),
        "acceptingPayments": False,
        "scheduleGenerated": False,
        "currency": "USD",
        "borrowerId": f"bor_{key}",
        "employerId": f"emp_{key}",
        "loanId": f"loan_{key}",
    }
    stamp_create(doc, "system:test")
    agreements.ref(client, agreement_id).set(doc)
    return agreement_id


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class GenerateScheduleTaskTests(SimpleTestCase):
    databases: list[str] = []

    def test_halted_generate_fails_activate_key_not_left_pending(self):
        client = get_client()
        key = fixtures.unique_key("genhalt")
        # Agreement already TERMINATED (left ACTIVATING mid-run): generation halts.
        agreement_id = _seed_generatable_agreement(
            client, key, status=BenefitStatus.TERMINATED
        )
        idem_key = f"activate_{key}"
        _seed_pending_key(
            client, idem_key, operation="activate-benefit", entity_id=agreement_id,
        )

        result = tasks.generate_schedule_task(
            {"agreementId": agreement_id, "idempotencyKey": idem_key},
            _ctx("generate-schedule"),
        )
        # Halted, not finalized (whatever exists is retained; the cascade owns it).
        self.assertFalse(result.get("finalized"))
        self.assertTrue(result.get("halted"))
        # The ACTIVATE key is driven terminal (FAILED), NOT left wedged PENDING —
        # so the PENDING-only reaper stops re-driving and a same-key retry is
        # unblocked (specs/08 §8.3), instead of the key looping forever.
        record = idempotency_keys.get(client, idem_key)
        self.assertEqual(record["status"], str(IdempotencyStatus.FAILED))


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class ProcessContributionTaskTests(SimpleTestCase):
    databases: list[str] = []

    def test_task_posts_once_and_redelivery_is_noop(self):
        client = get_client()
        key = fixtures.unique_key("ptask")
        g = fixtures.seed_payment_graph(
            client, key,
            total_commitment_cents=1_000_000,
            scheduled_amount_cents=100_000,
            loan_balance_cents=2_000_000,
        )
        cid = g.contribution_id(1)

        result = tasks.process_contribution_task(
            {"contributionId": cid}, _ctx("process-contribution")
        )
        self.assertEqual(result["status"], str(ContributionStatus.POSTED))
        self.assertEqual(len(g.attempts_for(1)), 1)
        self.assertEqual(g.loan()["currentBalanceCents"], 1_900_000)

        # Redelivery: contribution is now POSTED → a no-op, never a second charge.
        again = tasks.process_contribution_task(
            {"contributionId": cid}, _ctx("process-contribution")
        )
        self.assertFalse(again.get("processed"))
        self.assertEqual(len(g.attempts_for(1)), 1)
        self.assertEqual(g.loan()["currentBalanceCents"], 1_900_000)

    def test_two_concurrent_posts_are_fenced_to_one_charge(self):
        client = get_client()
        key = fixtures.unique_key("pfence")
        g = fixtures.seed_payment_graph(
            client, key,
            total_commitment_cents=1_000_000,
            scheduled_amount_cents=100_000,
            loan_balance_cents=2_000_000,
        )
        cid = g.contribution_id(1)

        results: list[dict] = []
        errors: list[BaseException] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def worker() -> None:
            barrier.wait()
            try:
                res = tasks.process_contribution_task(
                    {"contributionId": cid}, _ctx("process-contribution")
                )
                with lock:
                    results.append(res)
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
            self.assertFalse(t.is_alive(), "worker hung")

        self.assertEqual(len(results) + len(errors), 2)
        # Exactly one charge: one attempt, balance moved once, exactly one POSTED.
        self.assertEqual(len(g.attempts_for(1)), 1)
        self.assertEqual(g.loan()["currentBalanceCents"], 1_900_000)
        posted = [r for r in results if r.get("status") == str(ContributionStatus.POSTED)]
        self.assertEqual(len(posted), 1)


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class EnqueueDueJobTests(SimpleTestCase):
    databases: list[str] = []

    def test_enqueues_only_eligible_accepting_agreements(self):
        client = get_client()
        past = datetime.now(timezone.utc) - timedelta(days=1)

        # Eligible: acceptingPayments True + a past-due SCHEDULED contribution.
        ok = fixtures.seed_payment_graph(client, fixtures.unique_key("due_ok"))
        contributions.ref(client, ok.contribution_id(1)).update({"scheduledDate": past})
        # Ineligible: agreement not accepting payments (suspended/terminated).
        no = fixtures.seed_payment_graph(
            client, fixtures.unique_key("due_no"), accepting_payments=False
        )
        contributions.ref(client, no.contribution_id(1)).update({"scheduledDate": past})

        with mock.patch("internal.enqueue.enqueue") as spy:
            jobs.enqueue_due_contributions({}, _ctx("enqueue-due-contributions"))

        enqueued_ids = {
            c.args[1]["contributionId"] for c in spy.call_args_list
        }
        self.assertIn(ok.contribution_id(1), enqueued_ids)
        self.assertNotIn(no.contribution_id(1), enqueued_ids)


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class ReconcileStuckJobTests(SimpleTestCase):
    databases: list[str] = []

    def test_enqueues_only_stale_processing_contributions(self):
        client = get_client()
        now = datetime.now(timezone.utc)
        stale_cid = f"ben_{fixtures.unique_key('stuck')}__001"
        fresh_cid = f"ben_{fixtures.unique_key('fresh')}__001"
        _seed_processing_contribution(
            client, stale_cid, agreement_id="a_stale",
            last_attempt_at=now - timedelta(minutes=20),  # older than STUCK_THRESHOLD
        )
        _seed_processing_contribution(
            client, fresh_cid, agreement_id="a_fresh",
            last_attempt_at=now,  # well within threshold — not stuck
        )

        with mock.patch("internal.enqueue.enqueue") as spy:
            jobs.reconcile_stuck_payments({}, _ctx("reconcile-stuck-payments"))

        enqueued_ids = {c.args[1]["contributionId"] for c in spy.call_args_list}
        self.assertIn(stale_cid, enqueued_ids)
        self.assertNotIn(fresh_cid, enqueued_ids)


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class TailCompletionTests(SimpleTestCase):
    databases: list[str] = []

    def test_cancel_future_task_runs_tail_and_completes_key_with_command_body(self):
        client = get_client()
        key = fixtures.unique_key("cf")
        g = fixtures.seed_payment_graph(
            client, key,
            benefit_status=BenefitStatus.TERMINATED,
            accepting_payments=False,
        )
        idem_key = f"terminate_{key}"
        _seed_pending_key(
            client, idem_key, operation="terminate-benefit", entity_id=g.agreement_id,
        )
        # The terminate command threads its own response body in as `commandResult`
        # so the key stores what the first caller received — not the tail summary.
        command_body = {
            "agreementId": g.agreement_id,
            "status": str(BenefitStatus.TERMINATED),
            "acceptingPayments": False,
            "correlationId": "corr-cf-test",
        }

        result = tasks.cancel_future_contributions_task(
            {
                "agreementId": g.agreement_id,
                "reason": "terminated",
                "idempotencyKey": idem_key,
                "commandResult": command_body,
            },
            _ctx("cancel-future-contributions"),
        )
        # The adapter returns the COMMAND body (== enqueue()'s inline return ==
        # first-call 200 body), NOT the tail's {canceled, ...} summary.
        self.assertEqual(result, command_body)
        # The tail still ran: the SCHEDULED contribution was canceled (side effect).
        self.assertEqual(g.contribution(1)["status"], str(ContributionStatus.CANCELED))
        # The key is COMPLETED and stores the COMMAND body, so a same-key replay /
        # cloud poll returns exactly what the first caller received (specs/08 §8.2).
        record = idempotency_keys.get(client, idem_key)
        self.assertEqual(record["status"], str(IdempotencyStatus.COMPLETED))
        self.assertEqual(record["result"], command_body)

    def test_cancel_future_task_without_command_result_falls_back_to_tail_summary(self):
        # A reaper re-drive carries no `commandResult` (the original command body is
        # unavailable to it), so the adapter completes the key with the tail summary.
        client = get_client()
        key = fixtures.unique_key("cffb")
        g = fixtures.seed_payment_graph(
            client, key,
            benefit_status=BenefitStatus.TERMINATED,
            accepting_payments=False,
        )
        idem_key = f"terminate_{key}"
        _seed_pending_key(
            client, idem_key, operation="terminate-benefit", entity_id=g.agreement_id,
        )

        result = tasks.cancel_future_contributions_task(
            {"agreementId": g.agreement_id, "reason": "terminated", "idempotencyKey": idem_key},
            _ctx("cancel-future-contributions"),
        )
        self.assertGreaterEqual(result["canceled"], 1)
        self.assertEqual(g.contribution(1)["status"], str(ContributionStatus.CANCELED))
        record = idempotency_keys.get(client, idem_key)
        self.assertEqual(record["status"], str(IdempotencyStatus.COMPLETED))
        self.assertEqual(record["result"]["canceled"], result["canceled"])

    def test_complete_key_is_idempotent_on_redelivery(self):
        client = get_client()
        key = fixtures.unique_key("ck")
        idem_key = f"resume_{key}"
        _seed_pending_key(
            client, idem_key, operation="resume-benefit", entity_id=f"ben_{key}",
        )

        tasks._complete_key(client, idem_key, {"shiftMonths": 2})
        first = idempotency_keys.get(client, idem_key)
        self.assertEqual(first["status"], str(IdempotencyStatus.COMPLETED))

        # A redelivery must not overwrite / re-complete — a no-op.
        tasks._complete_key(client, idem_key, {"shiftMonths": 999})
        second = idempotency_keys.get(client, idem_key)
        self.assertEqual(second["result"], {"shiftMonths": 2})
