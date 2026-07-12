"""Emulator integration tests for ``contributions.generate.generate_schedule``
(specs/10 §10.1, specs/14 §14.4).

The Phase-3 async replacement for ``activate_benefit``'s inline schedule
generation. Verifies against real Firestore transactions that:

* a full generate reproduces the inline outcome — deterministic ids,
  ``Σ(scheduledAmountCents) == totalCommitment`` (residual on the final
  installment), the agreement ``ACTIVE`` + payments-accepting, the correct
  ``endDate`` + loan look-ahead, and exactly one ``BENEFIT_ACTIVATED`` event;
* the resumable multi-batch path continues from a partial
  ``installmentsGenerated`` witness with no gaps or duplicates;
* a redelivery (a second full call) is an idempotent no-op — no duplicate
  contributions, no second ``BENEFIT_ACTIVATED`` event;
* generation HALTS (retaining what exists, never finalizing) when the agreement
  has left ``ACTIVATING`` (terminated mid-generation).

The multi-batch tests shrink :data:`SYNC_GENERATION_MAX` / :data:`BATCH_SIZE` so a
small term exercises the batched path. Exercises the callable directly (no HTTP /
auth / Cloud Task).
"""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime
from unittest import mock

from django.test import SimpleTestCase, tag

from common.enums import (
    BenefitStatus,
    ContributionStatus,
    EmployerStatus,
    EmploymentStatus,
    LoanStatus,
)
from common.firestore import get_client
from common.ids import contribution_id as _contribution_id
from common.money import solve_schedule
from common.periods import SYSTEM_TIMEZONE, period_label, scheduled_datetime
from contributions import generate
from contributions.generate import generate_schedule
from repositories import (
    agreements,
    borrowers,
    contributions,
    employers,
    loans,
    refs,
    stamp_create,
)

EMULATOR = bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))

ACTOR_ID = "system:generate-schedule"
ACTOR_NAME = "System (generate-schedule)"
CURRENCY = "USD"


def _set(client, ref, data: dict) -> None:
    stamp_create(data, ACTOR_ID)
    ref.set(data)


def _ctx(agreement_id: str):
    """A SYSTEM-actor context, as the enqueue seam mints for the inline task."""
    from internal.system_context import system_ctx

    return system_ctx("generate-schedule")


def _seed_activating_agreement(
    client,
    key: str,
    *,
    total_commitment_cents: int,
    term_months: int,
    installments_generated: int = 0,
    status: BenefitStatus = BenefitStatus.ACTIVATING,
) -> dict:
    """Seed an ``ACTIVATING`` agreement (schedule not yet generated) + its loan/
    borrower/employer. When ``installments_generated > 0`` the corresponding
    leading contributions are pre-created (mimicking a crashed mid-generation) so
    the resume path has consistent state to continue from.
    """
    employer_id = f"emp_{key}"
    borrower_id = f"bor_{key}"
    loan_id = f"loan_{key}"
    agreement_id = f"ben_{key}"
    borrower_name = "Jordan Fixture"
    employer_name = "Fixture Corp"
    start_dt = datetime.now(SYSTEM_TIMEZONE).replace(
        hour=12, minute=0, second=0, microsecond=0
    )

    _set(
        client,
        employers.ref(client, employer_id),
        {
            "name": employer_name,
            "status": str(EmployerStatus.ACTIVE),
            "currency": CURRENCY,
        },
    )
    _set(
        client,
        borrowers.ref(client, borrower_id),
        {
            "displayName": borrower_name,
            "employerId": employer_id,
            "employerName": employer_name,
            "employmentStatus": str(EmploymentStatus.ACTIVE),
            "primaryLoanId": loan_id,
        },
    )
    _set(
        client,
        loans.ref(client, loan_id),
        {
            "borrowerId": borrower_id,
            "borrowerName": borrower_name,
            "employerId": employer_id,
            "employerName": employer_name,
            "currency": CURRENCY,
            "currentBalanceCents": total_commitment_cents + 500_000,
            "loanStatus": str(LoanStatus.ACTIVE),
            "benefitAgreementId": agreement_id,
            "benefitStatus": str(BenefitStatus.ACTIVATING),
            "openExceptionCount": 0,
            "nextContributionDate": None,
            "nextContributionAmountCents": None,
        },
    )
    _set(
        client,
        agreements.ref(client, agreement_id),
        {
            "borrowerId": borrower_id,
            "borrowerName": borrower_name,
            "employerId": employer_id,
            "employerName": employer_name,
            "loanId": loan_id,
            "currency": CURRENCY,
            "totalCommitmentCents": total_commitment_cents,
            "termMonths": term_months,
            "startDate": start_dt,
            "endDate": None,
            "amountPaidCents": 0,
            "remainingCommitmentCents": total_commitment_cents,
            "status": str(status),
            "acceptingPayments": False,
            "suspendedReason": None,
            "scheduleGenerated": False,
            "plannedInstallmentCount": term_months,
            "installmentsGenerated": installments_generated,
        },
    )

    # Pre-create the leading contributions the witness claims already exist, with
    # the SAME solved amounts/dates the task would produce, so resume is consistent.
    schedule = solve_schedule(total_commitment_cents, term_months)
    start_date = start_dt.date()
    for n in range(1, installments_generated + 1):
        sched_dt = scheduled_datetime(start_date, n)
        cid = _contribution_id(agreement_id, n)
        _set(
            client,
            contributions.ref(client, cid),
            {
                "benefitAgreementId": agreement_id,
                "installmentNumber": n,
                "borrowerId": borrower_id,
                "borrowerName": borrower_name,
                "employerId": employer_id,
                "employerName": employer_name,
                "loanId": loan_id,
                "currency": CURRENCY,
                "scheduledDate": sched_dt,
                "periodLabel": period_label(sched_dt),
                "scheduledAmountCents": schedule[n - 1],
                "status": str(ContributionStatus.SCHEDULED),
                "attemptCount": 0,
                "currentAttemptId": None,
                "currentExceptionId": None,
                "lastAttemptAt": None,
                "postedAt": None,
                "postedAmountCents": None,
                "failureCode": None,
                "failureReason": None,
            },
        )

    return {
        "employer_id": employer_id,
        "borrower_id": borrower_id,
        "loan_id": loan_id,
        "agreement_id": agreement_id,
        "total": total_commitment_cents,
        "term": term_months,
        "start_date": start_date,
    }


def _activated_event_count(client, agreement_id: str) -> int:
    events = refs.stream_to_dicts(
        client.collection(refs.SERVICING_EVENTS)
        .where(filter=refs.field_filter("benefitAgreementId", "==", agreement_id))
        .where(filter=refs.field_filter("eventType", "==", "BENEFIT_ACTIVATED"))
    )
    return len(events)


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class GenerateScheduleTests(SimpleTestCase):
    databases: list[str] = []

    def test_full_generate_matches_inline_outcome(self):
        client = get_client()
        key = f"gen_{uuid.uuid4().hex[:10]}"
        # 1_000_000 / 12 is non-divisible: base 83_333, residual on the last.
        ids = _seed_activating_agreement(
            client, key, total_commitment_cents=1_000_000, term_months=12
        )
        agreement_id = ids["agreement_id"]

        result = generate_schedule(agreement_id, _ctx(agreement_id), client=client)

        self.assertTrue(result["finalized"])
        self.assertEqual(result["status"], str(BenefitStatus.ACTIVE))
        self.assertEqual(result["installmentsGenerated"], 12)

        agreement = agreements.get(client, agreement_id)
        self.assertEqual(agreement["status"], str(BenefitStatus.ACTIVE))
        self.assertTrue(agreement["acceptingPayments"])
        self.assertTrue(agreement["scheduleGenerated"])
        self.assertEqual(agreement["installmentsGenerated"], 12)

        # Schedule: exactly `term` contributions, contiguous deterministic ids.
        schedule = contributions.list_for_agreement(client, agreement_id)
        self.assertEqual(len(schedule), 12)
        self.assertEqual(
            [c["id"] for c in schedule],
            [_contribution_id(agreement_id, n) for n in range(1, 13)],
        )
        self.assertEqual(
            [c["installmentNumber"] for c in schedule], list(range(1, 13))
        )

        # Σ == totalCommitment, residual on the FINAL installment (I5, §7.3).
        amounts = [c["scheduledAmountCents"] for c in schedule]
        self.assertEqual(sum(amounts), 1_000_000)
        expected = solve_schedule(1_000_000, 12)
        self.assertEqual(amounts, expected)
        self.assertNotEqual(amounts[-1], amounts[0])

        # endDate == scheduled_datetime(startDate, term); look-ahead == installment 1.
        end_dt = scheduled_datetime(ids["start_date"], 12)
        self.assertEqual(result["endDate"], end_dt.isoformat())
        loan = loans.get(client, ids["loan_id"])
        self.assertEqual(loan["benefitStatus"], str(BenefitStatus.ACTIVE))
        self.assertEqual(loan["nextContributionAmountCents"], amounts[0])

        # Exactly one BENEFIT_ACTIVATED event.
        self.assertEqual(_activated_event_count(client, agreement_id), 1)

    def test_resume_from_partial_batch_no_gaps_or_dups(self):
        client = get_client()
        key = f"genres_{uuid.uuid4().hex[:10]}"
        # Force the multi-batch path: term 6 with BATCH_SIZE/SYNC_MAX = 2.
        ids = _seed_activating_agreement(
            client,
            key,
            total_commitment_cents=600_000,
            term_months=6,
            installments_generated=2,  # crashed after batch 1 (installments 1..2)
        )
        agreement_id = ids["agreement_id"]

        with mock.patch.object(generate, "SYNC_GENERATION_MAX", 2), mock.patch.object(
            generate, "BATCH_SIZE", 2
        ):
            result = generate_schedule(agreement_id, _ctx(agreement_id), client=client)

        self.assertTrue(result["finalized"])
        schedule = contributions.list_for_agreement(client, agreement_id)
        # No gaps, no dups: exactly installments 1..6 once each.
        self.assertEqual(
            [c["installmentNumber"] for c in schedule], list(range(1, 7))
        )
        amounts = [c["scheduledAmountCents"] for c in schedule]
        self.assertEqual(amounts, solve_schedule(600_000, 6))
        self.assertEqual(sum(amounts), 600_000)

        agreement = agreements.get(client, agreement_id)
        self.assertEqual(agreement["status"], str(BenefitStatus.ACTIVE))
        self.assertEqual(agreement["installmentsGenerated"], 6)
        self.assertEqual(_activated_event_count(client, agreement_id), 1)

    def test_redelivery_is_idempotent_no_dup(self):
        client = get_client()
        key = f"gendup_{uuid.uuid4().hex[:10]}"
        ids = _seed_activating_agreement(
            client, key, total_commitment_cents=600_000, term_months=6
        )
        agreement_id = ids["agreement_id"]

        first = generate_schedule(agreement_id, _ctx(agreement_id), client=client)
        second = generate_schedule(agreement_id, _ctx(agreement_id), client=client)

        self.assertTrue(first["finalized"])
        self.assertTrue(second["finalized"])
        # Redelivery created nothing new and emitted no second event.
        schedule = contributions.list_for_agreement(client, agreement_id)
        self.assertEqual(len(schedule), 6)
        self.assertEqual(
            [c["installmentNumber"] for c in schedule], list(range(1, 7))
        )
        self.assertEqual(_activated_event_count(client, agreement_id), 1)

    def test_halts_when_terminated_mid_generation(self):
        client = get_client()
        key = f"genhalt_{uuid.uuid4().hex[:10]}"
        # Two installments already created, then the agreement was TERMINATED
        # before generation finished.
        ids = _seed_activating_agreement(
            client,
            key,
            total_commitment_cents=600_000,
            term_months=6,
            installments_generated=2,
            status=BenefitStatus.TERMINATED,
        )
        agreement_id = ids["agreement_id"]

        result = generate_schedule(agreement_id, _ctx(agreement_id), client=client)

        self.assertFalse(result["finalized"])
        self.assertTrue(result["halted"])
        self.assertEqual(result["status"], str(BenefitStatus.TERMINATED))

        # No finalize: agreement stays TERMINATED, no new contributions, no event.
        agreement = agreements.get(client, agreement_id)
        self.assertEqual(agreement["status"], str(BenefitStatus.TERMINATED))
        self.assertFalse(agreement.get("scheduleGenerated"))
        schedule = contributions.list_for_agreement(client, agreement_id)
        self.assertEqual(len(schedule), 2)  # the pre-existing rows are retained
        self.assertEqual(_activated_event_count(client, agreement_id), 0)
