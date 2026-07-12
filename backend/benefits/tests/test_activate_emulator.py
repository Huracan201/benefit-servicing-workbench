"""Emulator integration tests for ``activate_benefit`` (specs/10 §10.1, 17 §17.2).

Verifies the inline activation path end-to-end against real Firestore
transactions: the schedule is generated with ``Σ(scheduledAmountCents) ==
totalCommitment`` at deterministic ids, the agreement becomes ``ACTIVE`` and
payments-accepting, a ``BENEFIT_ACTIVATED`` event is appended, and activation is
refused when the borrower's employment is not ``ACTIVE``. Exercises the SERVICE
layer directly (no HTTP / auth mocking).
"""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime

from django.test import SimpleTestCase, tag

from benefits.services import activate_benefit
from commands.base import CommandContext, Unprocessable, request_hash
from common.enums import (
    BenefitStatus,
    EmployerStatus,
    EmploymentStatus,
    LoanStatus,
)
from common.firestore import get_client
from common.ids import contribution_id as _contribution_id
from common.periods import SYSTEM_TIMEZONE
from projections import recompute
from repositories import (
    agreements,
    borrowers,
    contributions,
    employer_summaries,
    employers,
    loan_workbenches,
    loans,
    refs,
    stamp_create,
)

EMULATOR = bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))

ACTOR_ID = "user_test_manager"
ACTOR_ROLE = "SERVICING_MANAGER"
ACTOR_NAME = "Test Servicing Manager"
CURRENCY = "USD"


def _set(client, ref, data: dict) -> None:
    stamp_create(data, ACTOR_ID)
    ref.set(data)


def _seed_pending_agreement(
    client,
    key: str,
    *,
    total_commitment_cents: int = 600_000,
    term_months: int = 6,
    employment_status: EmploymentStatus = EmploymentStatus.ACTIVE,
) -> dict:
    """Seed a PENDING agreement (no schedule yet) ready to activate.

    Returns a dict of the ids so the test can drive/read them.
    """
    employer_id = f"emp_{key}"
    borrower_id = f"bor_{key}"
    loan_id = f"loan_{key}"
    agreement_id = f"ben_{key}"
    borrower_name = "Jordan Fixture"
    employer_name = "Fixture Corp"
    # startDate = today at noon (local): not before today, so activation is
    # permitted (specs/10 §10.1 refuses a past startDate).
    start_dt = datetime.now(SYSTEM_TIMEZONE).replace(
        hour=12, minute=0, second=0, microsecond=0
    )

    _set(
        client,
        employers.ref(client, employer_id),
        {
            "name": employer_name,
            "industry": "Testing",
            "status": str(EmployerStatus.ACTIVE),
            "programName": "Fixture Program",
            "currency": CURRENCY,
            "totalCommitmentCents": total_commitment_cents,
            "activeBorrowerCount": 1,
            "amountPaidCents": 0,
            "remainingCommitmentCents": total_commitment_cents,
        },
    )
    _set(
        client,
        borrowers.ref(client, borrower_id),
        {
            "firstName": "Jordan",
            "lastName": "Fixture",
            "displayName": borrower_name,
            "email": f"{key}@example.com",
            "employerId": employer_id,
            "employerName": employer_name,
            "employmentStatus": str(employment_status),
            "primaryLoanId": loan_id,
            "primaryBenefitAgreementId": agreement_id,
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
            "originalPrincipalCents": total_commitment_cents + 500_000,
            "currentBalanceCents": total_commitment_cents + 500_000,
            "loanStatus": str(LoanStatus.ACTIVE),
            # loan points at THIS agreement, still PENDING -> not "occupied".
            "benefitAgreementId": agreement_id,
            "benefitStatus": str(BenefitStatus.PENDING),
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
            "baseMonthlyContributionCents": total_commitment_cents // term_months,
            "termMonths": term_months,
            "startDate": start_dt,
            "endDate": None,
            "amountPaidCents": 0,
            "remainingCommitmentCents": total_commitment_cents,
            "status": str(BenefitStatus.PENDING),
            "acceptingPayments": False,
            "suspendedReason": None,
            "scheduleGenerated": False,
            "plannedInstallmentCount": term_months,
            "installmentsGenerated": 0,
        },
    )
    return {
        "employer_id": employer_id,
        "borrower_id": borrower_id,
        "loan_id": loan_id,
        "agreement_id": agreement_id,
        "total": total_commitment_cents,
        "term": term_months,
    }


def _ctx(agreement_id: str) -> CommandContext:
    key = f"activate_{uuid.uuid4().hex[:12]}"
    return CommandContext(
        actor_id=ACTOR_ID,
        actor_role=ACTOR_ROLE,
        actor_name=ACTOR_NAME,
        idempotency_key=key,
        request_hash=request_hash("POST", f"/benefits/{agreement_id}/activate", None),
    )


def _strip_readmodel(doc: dict) -> dict:
    """Drop the gateway/get extras (``id``, ``updatedAt``) so a stored read-model
    doc can be compared field-for-field against the pure recompute output."""
    return {k: v for k, v in doc.items() if k not in ("id", "updatedAt")}


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class ActivateBenefitTests(SimpleTestCase):
    databases: list[str] = []

    def test_activation_generates_schedule_and_activates(self):
        client = get_client()
        key = f"act_{uuid.uuid4().hex[:10]}"
        # Non-divisible on purpose (1_000_000 / 12): base 83_333, final 83_337 —
        # so the residual installment is actually exercised (specs/07 §7.3, I5).
        ids = _seed_pending_agreement(
            client, key, total_commitment_cents=1_000_000, term_months=12
        )
        agreement_id = ids["agreement_id"]

        result = activate_benefit(agreement_id=agreement_id, ctx=_ctx(agreement_id), client=client)

        # --- command result -----------------------------------------------
        self.assertEqual(result["status"], str(BenefitStatus.ACTIVE))
        self.assertTrue(result["acceptingPayments"])
        self.assertEqual(result["installmentsGenerated"], 12)

        # --- agreement ACTIVE + acceptingPayments -------------------------
        agreement = agreements.get(client, agreement_id)
        self.assertEqual(agreement["status"], str(BenefitStatus.ACTIVE))
        self.assertTrue(agreement["acceptingPayments"])
        self.assertTrue(agreement["scheduleGenerated"])
        self.assertEqual(agreement["installmentsGenerated"], 12)

        # --- schedule generated: exactly `term` contributions -------------
        schedule = contributions.list_for_agreement(client, agreement_id)
        self.assertEqual(len(schedule), 12)

        # --- Σ(scheduledAmountCents) == totalCommitment (invariant I5) -----
        amounts = [c["scheduledAmountCents"] for c in schedule]
        self.assertEqual(sum(amounts), 1_000_000)
        # Residual lands on the FINAL installment: installments 1..11 are the
        # base; the last carries the remainder (specs/07 §7.3). This is the case
        # the divisible 600_000/6 seed never exercised.
        base = 1_000_000 // 12  # 83_333
        self.assertEqual(amounts[:-1], [base] * 11)
        self.assertEqual(amounts[-1], base + (1_000_000 - base * 12))  # 83_337
        self.assertNotEqual(
            amounts[-1], base, "final installment must carry the residual"
        )

        # --- deterministic ids: {agreementId}__{NNN}, contiguous 1..term ---
        expected_ids = [_contribution_id(agreement_id, n) for n in range(1, 13)]
        self.assertEqual([c["id"] for c in schedule], expected_ids)
        self.assertEqual(
            [c["installmentNumber"] for c in schedule], list(range(1, 13))
        )

        # --- loan look-ahead synced ---------------------------------------
        loan = loans.get(client, ids["loan_id"])
        self.assertEqual(loan["benefitStatus"], str(BenefitStatus.ACTIVE))
        self.assertEqual(loan["nextContributionAmountCents"], schedule[0]["scheduledAmountCents"])

        # --- BENEFIT_ACTIVATED event appended -----------------------------
        activated_events = refs.stream_to_dicts(
            client.collection(refs.SERVICING_EVENTS)
            .where(filter=refs.field_filter("benefitAgreementId", "==", agreement_id))
            .where(filter=refs.field_filter("eventType", "==", "BENEFIT_ACTIVATED"))
        )
        self.assertEqual(len(activated_events), 1)

    def test_inline_activation_updates_read_model_to_active_post_state(self):
        """The generate tail's terminal ``BENEFIT_ACTIVATED`` fanout nudge corrects
        the read models from the pre-generation ``ACTIVATING`` snapshot to the
        ``ACTIVE`` post-state (the projection-fanout gap in the async tail — specs/05
        §5.2, specs/14 §14.4).

        Under the emulator ``TASK_EXECUTION_MODE`` is inline, so ``activate_benefit``
        runs the generate-schedule tail AND every ``update-projection`` nudge
        synchronously. Without the tail nudge, only the command's *starting*
        ``BENEFIT_ACTIVATION_STARTED`` nudge fires — at ``ACTIVATING``, before any
        schedule exists — leaving the summaries stale (benefitStatus ``ACTIVATING``,
        activeBenefits 0, ``$0`` obligation, no look-ahead) until rebuild-summaries.
        Here the summaries must reflect the ``ACTIVE`` post-state and equal a fresh
        recompute-from-source.
        """
        client = get_client()
        key = f"actproj_{uuid.uuid4().hex[:10]}"
        ids = _seed_pending_agreement(
            client, key, total_commitment_cents=1_000_000, term_months=12
        )
        agreement_id = ids["agreement_id"]
        employer_id = ids["employer_id"]
        loan_id = ids["loan_id"]
        base_monthly = 1_000_000 // 12  # == the seed's baseMonthlyContributionCents

        # Clean up the (uniquely-scoped) read-model docs the fanout writes.
        self.addCleanup(employer_summaries.ref(client, employer_id).delete)
        self.addCleanup(loan_workbenches.ref(client, loan_id).delete)

        result = activate_benefit(
            agreement_id=agreement_id, ctx=_ctx(agreement_id), client=client
        )
        self.assertEqual(result["status"], str(BenefitStatus.ACTIVE))

        schedule = contributions.list_for_agreement(client, agreement_id)
        first_amount = schedule[0]["scheduledAmountCents"]

        # --- loanWorkbenches/{loan}: ACTIVE + look-ahead populated (would be
        #     ACTIVATING + None if stuck on the pre-generation starting nudge). ----
        lw = loan_workbenches.get(client, loan_id)
        self.assertIsNotNone(lw, "loan workbench must be written by the activation fanout")
        self.assertEqual(lw["benefitStatus"], str(BenefitStatus.ACTIVE))
        self.assertEqual(lw["nextContributionAmountCents"], first_amount)
        self.assertIsNotNone(lw["nextContributionDate"])

        # --- employerSummaries/{emp}: activeBenefits incremented, obligation set. -
        es = employer_summaries.get(client, employer_id)
        self.assertIsNotNone(es, "employer summary must be written by the activation fanout")
        self.assertEqual(es["activeBenefits"], 1)
        self.assertEqual(es["monthlyObligationCents"], base_monthly)

        # --- freshness: stored == a fresh recompute (the terminal nudge recomputed
        #     from the committed ACTIVE source, not a stale ACTIVATING snapshot). ---
        self.assertEqual(
            _strip_readmodel(lw), recompute.recompute_loan_workbench(client, loan_id)
        )
        self.assertEqual(
            _strip_readmodel(es), recompute.recompute_employer(client, employer_id)
        )

    def test_activation_fails_when_employment_not_active(self):
        client = get_client()
        key = f"actbad_{uuid.uuid4().hex[:10]}"
        ids = _seed_pending_agreement(
            client, key, employment_status=EmploymentStatus.TERMINATED
        )
        agreement_id = ids["agreement_id"]

        with self.assertRaises(Unprocessable):
            activate_benefit(agreement_id=agreement_id, ctx=_ctx(agreement_id), client=client)

        # Agreement untouched — still PENDING, no schedule generated.
        agreement = agreements.get(client, agreement_id)
        self.assertEqual(agreement["status"], str(BenefitStatus.PENDING))
        self.assertFalse(agreement["acceptingPayments"])
        self.assertEqual(contributions.list_for_agreement(client, agreement_id), [])
