"""contributions.generate — the resumable generate-schedule task (specs/10 §10.1,
specs/14 §14.4).

Phase 2 (specs/19 §19.2) generated the whole contribution schedule **inline**,
inside ``activate_benefit``'s core transaction (see
:mod:`benefits.services`). Phase 3 (specs/14) moves generation onto an async
task: ``activate_benefit`` transitions ``PENDING → ACTIVATING`` (writing the
schedule parameters — ``plannedInstallmentCount``, ``installmentsGenerated = 0``)
and enqueues ``generate-schedule``; this module is the (idempotent, SYSTEM-run)
body that task invokes.

:func:`generate_schedule` is a **pure callable** — it owns neither the idempotency
key nor the enqueue decision (those are the ``/internal`` task wrapper's and the
activate command's, respectively; see the completion protocol notes on the unit).
It assumes the agreement is already ``ACTIVATING`` and:

* **Resumable / crash-safe.** Generation runs in bounded transactions; each
  re-reads ``installmentsGenerated`` (the persisted witness) and only creates the
  installments beyond it, so a Cloud Tasks redelivery / crash-restart resumes
  exactly where it left off. Creates use ``txn.create`` (a document-existence
  precondition), so a redelivery can never double-create an installment.
* **Byte-identical to the inline path.** Amounts come from
  :func:`common.money.solve_schedule` (``Σ == totalCommitmentCents`` exactly, the
  residual on the final installment — invariant I5, specs/07 §7.3) and IDs from
  the deterministic ``{agreementId}__{NNN:03d}`` scheme, so the produced
  contribution documents, final agreement/loan state, and ``endDate`` match what
  Phase 2's single-transaction generation produced.
* **Fast path preserved.** For ``plannedInstallmentCount ≤``
  :data:`SYNC_GENERATION_MAX` the whole schedule + finalize is created in a
  *single* atomic transaction (the common case); only larger schedules take the
  multi-batch (:data:`BATCH_SIZE`) path. Either way the outcome is identical.
* **Halts on mid-generation termination.** Every batch re-asserts the agreement
  is still ``ACTIVATING``; if a concurrent terminate moved it to ``TERMINATED``
  (or any non-``ACTIVATING`` state) generation stops and returns, *retaining*
  whatever was already created (the terminate cascade cancels those separately).

The finalize step (``ACTIVATING → ACTIVE``; ``scheduleGenerated = true``,
``acceptingPayments = true``; loan look-ahead; one ``BENEFIT_ACTIVATED`` event)
runs in the same transaction as the last create batch, so completion is atomic.
The ``BENEFIT_ACTIVATION_STARTED`` event (sequence 1) is the activate command's;
this task's ``BENEFIT_ACTIVATED`` uses a task-scoped ``:generate`` correlationId
so it can never collide on ``(correlationId, sequence)`` with the command's own
events (specs/04 §4.9), mirroring :mod:`contributions.lifecycle` /
:mod:`benefits.shift`.

Returns a summary dict (``finalized``/``halted`` flag + the agreement snapshot the
task wrapper completes the ACTIVATE idempotency key with).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import Any, Optional

from commands.base import (
    CommandContext,
    CommandError,
    NotFound,
    Unprocessable,
    from_domain_error,
    transactional,
)
from common.enums import BenefitStatus, ContributionStatus
from common.errors import DomainError
from common.ids import contribution_id as _make_contribution_id
from common.money import solve_schedule
from common.periods import SYSTEM_TIMEZONE, period_label, scheduled_datetime
from repositories import (
    agreements,
    contributions,
    loans,
    stamp_create,
    stamp_update,
)
from servicing import events as servicing_events

ENTITY_TYPE = "BENEFIT_AGREEMENT"

# Fast path: at/below this planned-installment count the whole schedule + the
# finalize is created in ONE atomic transaction (specs/10 §10.1 note — a
# 36-installment schedule + 2 events is far under Firestore's 500-writes/txn
# cap). Above it, generation uses the resumable multi-batch path. Pinned.
SYNC_GENERATION_MAX = 120

# Resumable path: installments created per bounded transaction. Each create is a
# single write; a full batch (BATCH_SIZE creates + the finalize's ~4 writes on
# the terminal batch) stays well under Firestore's 500-writes/transaction cap and
# the unit's ≤450 budget.
BATCH_SIZE = 100


# --------------------------------------------------------------------------- #
# transactional read helper
# --------------------------------------------------------------------------- #
def _get_in_txn(txn: Any, ref: Any) -> Optional[dict]:
    """Read a single ``DocumentReference`` inside ``txn`` as dict-with-id/None."""
    got = txn.get(ref)
    snap = got if hasattr(got, "exists") else next(iter(got), None)
    if snap is None or not getattr(snap, "exists", False):
        return None
    data = snap.to_dict() or {}
    data["id"] = snap.id
    return data


# --------------------------------------------------------------------------- #
# startDate normalisation — identical coercion to benefits.services so the
# scheduledDate/periodLabel of every installment is byte-identical to the inline
# path (specs/07, SYSTEM_TIMEZONE noon rule).
# --------------------------------------------------------------------------- #
def _as_local_date(value: Any) -> date:
    """Coerce a stored ``startDate`` (timestamp/date/ISO string) to a local date."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=SYSTEM_TIMEZONE).date()
        return value.astimezone(SYSTEM_TIMEZONE).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    raise Unprocessable("agreement.startDate is missing or unreadable")


def _contribution_doc(
    *,
    agreement_id: str,
    installment_number: int,
    agreement: dict,
    amount_cents: int,
    scheduled_dt: datetime,
) -> dict:
    """Build one contribution document, byte-identical to the inline path."""
    return {
        "benefitAgreementId": agreement_id,
        "installmentNumber": installment_number,
        "borrowerId": agreement.get("borrowerId"),
        "borrowerName": agreement.get("borrowerName"),
        "employerId": agreement.get("employerId"),
        "employerName": agreement.get("employerName"),
        "loanId": agreement.get("loanId"),
        "currency": agreement.get("currency", "USD"),
        "scheduledDate": scheduled_dt,
        "periodLabel": period_label(scheduled_dt),
        "scheduledAmountCents": amount_cents,
        "status": ContributionStatus.SCHEDULED.value,
        "attemptCount": 0,
        "currentAttemptId": None,
        "currentExceptionId": None,
        "lastAttemptAt": None,
        "postedAt": None,
        "postedAmountCents": None,
        "failureCode": None,
        "failureReason": None,
    }


# --------------------------------------------------------------------------- #
# the task
# --------------------------------------------------------------------------- #
def generate_schedule(
    agreement_id: str, ctx: CommandContext, *, client: Any = None
) -> dict:
    """Generate an ``ACTIVATING`` agreement's contribution schedule (resumable).

    Creates every remaining installment (from the persisted
    ``installmentsGenerated`` witness) in bounded, crash-safe transactions and,
    on the terminal batch, finalizes the agreement ``ACTIVATING → ACTIVE``.
    Idempotent: a redelivery after full generation returns the finalized summary
    without re-creating anything; a redelivery mid-generation resumes with no
    gaps/dups (deterministic ids + ``txn.create`` precondition). Halts and
    returns (retaining what exists) if the agreement is no longer ``ACTIVATING``.
    """
    if client is None:
        from common.firestore import get_client

        client = get_client()

    # Task-scoped correlationId: the finalize's BENEFIT_ACTIVATED event lives in an
    # INDEPENDENT sequence space (:generate) so it never collides on
    # (correlationId, sequence) with the activate command's own events (specs/04
    # §4.9), mirroring contributions.lifecycle / benefits.shift.
    event_ctx = replace(ctx, correlation_id=f"{ctx.correlation_id}:generate")

    try:
        # --- immutable schedule parameters (read once; these never change after
        #     activate committed the ACTIVATING transition) ----------------------
        agreement = agreements.get(client, agreement_id)
        if agreement is None:
            raise NotFound(f"benefit agreement {agreement_id!r} not found")

        total = int(agreement["totalCommitmentCents"])
        term = int(agreement["termMonths"])
        planned = int(agreement.get("plannedInstallmentCount") or term)
        start_date = _as_local_date(agreement.get("startDate"))

        # Amounts: Σ == total exactly, residual on the final installment (I5).
        schedule = solve_schedule(total, term)
        first_dt = scheduled_datetime(start_date, 1)
        end_dt = scheduled_datetime(start_date, planned)

        # Fast single-atomic path at/below SYNC_GENERATION_MAX; else bounded
        # batches. Either path yields byte-identical documents + final state.
        batch_size = planned if planned <= SYNC_GENERATION_MAX else BATCH_SIZE

        # --- generate in bounded transactions, resuming from the witness --------
        while True:
            kind, payload = _run_batch(
                client,
                agreement_id=agreement_id,
                event_ctx=event_ctx,
                schedule=schedule,
                planned=planned,
                start_date=start_date,
                batch_size=batch_size,
                first_dt=first_dt,
                end_dt=end_dt,
            )
            if kind == "halted":
                return _halt_result(agreement_id, planned, payload)
            if kind == "finalized":
                return _success_result(
                    agreement_id,
                    payload,
                    planned=planned,
                    term=term,
                    total=total,
                    start_date=start_date,
                    first_dt=first_dt,
                    end_dt=end_dt,
                    first_amount=schedule[0],
                    correlation_id=ctx.correlation_id,
                )
            # kind == "progress": more installments remain — continue the loop.
    except CommandError:
        raise
    except DomainError as exc:
        raise from_domain_error(exc) from exc


def _run_batch(
    client: Any,
    *,
    agreement_id: str,
    event_ctx: CommandContext,
    schedule: list[int],
    planned: int,
    start_date: date,
    batch_size: int,
    first_dt: datetime,
    end_dt: datetime,
) -> tuple[str, Any]:
    """Run one bounded generation batch in a single transaction.

    Returns ``(kind, payload)``:

    * ``("halted", status)``       — agreement no longer ``ACTIVATING``: stop,
      retaining whatever was already created.
    * ``("finalized", agreement)`` — this batch (or a prior redelivered run)
      reached ``plannedInstallmentCount`` and the agreement is ``ACTIVE``.
    * ``("progress", new_count)``  — a non-terminal batch advanced
      ``installmentsGenerated``; the caller loops for the next batch.
    """

    @transactional(client)
    def _run(txn: Any) -> tuple[str, Any]:
        # -- read (before any write — Firestore ordering rule) ----------------
        agreement = _get_in_txn(txn, agreements.ref(client, agreement_id))
        if agreement is None:
            raise NotFound(f"benefit agreement {agreement_id!r} not found")

        status = agreement.get("status")
        # Redelivery after completion: already ACTIVE → nothing to do, replay the
        # finalized summary.
        if status == BenefitStatus.ACTIVE.value:
            return ("finalized", agreement)
        # Terminated (or otherwise no longer generating) mid-run: HALT, keeping
        # whatever was already created (the terminate cascade handles those).
        if status != BenefitStatus.ACTIVATING.value:
            return ("halted", status)

        generated = int(agreement.get("installmentsGenerated", 0) or 0)

        # The installments to create this batch (resume strictly beyond the
        # persisted witness — deterministic ids + create-precondition mean a
        # redelivery of an already-created installment could never dup here).
        batch_end = min(generated + batch_size, planned)
        for n in range(generated + 1, batch_end + 1):
            scheduled_dt = scheduled_datetime(start_date, n)
            contribution = _contribution_doc(
                agreement_id=agreement_id,
                installment_number=n,
                agreement=agreement,
                amount_cents=schedule[n - 1],
                scheduled_dt=scheduled_dt,
            )
            stamp_create(contribution, event_ctx.actor_id)
            # create-precondition: a redelivered/duplicate create of an existing
            # id fails rather than silently overwriting (specs/10 §10.1).
            txn.create(
                contributions.ref(client, _make_contribution_id(agreement_id, n)),
                contribution,
            )

        if batch_end >= planned:
            # Terminal batch: finalize ACTIVATING → ACTIVE in this same txn so
            # completion is atomic with the last creates.
            _finalize_in_txn(
                txn,
                client,
                agreement=agreement,
                event_ctx=event_ctx,
                planned=planned,
                first_dt=first_dt,
                end_dt=end_dt,
                first_amount=schedule[0],
            )
            return ("finalized", agreement)

        # Non-terminal batch: advance the witness only.
        agreement_update = {"installmentsGenerated": batch_end}
        stamp_update(agreement_update, event_ctx.actor_id)
        txn.update(agreements.ref(client, agreement_id), agreement_update)
        return ("progress", batch_end)

    return _run()


def _finalize_in_txn(
    txn: Any,
    client: Any,
    *,
    agreement: dict,
    event_ctx: CommandContext,
    planned: int,
    first_dt: datetime,
    end_dt: datetime,
    first_amount: int,
) -> None:
    """Finalize ``ACTIVATING → ACTIVE`` inside ``txn`` (specs/10 §10.1).

    Sets the agreement live + payments-accepting with ``endDate`` and the final
    ``installmentsGenerated``; syncs the loan look-ahead to installment 1 (nothing
    can have been paid while ``acceptingPayments`` was false, so installment 1 is
    always the earliest — matching the inline path exactly); appends one
    ``BENEFIT_ACTIVATED`` event.
    """
    agreement_id = agreement["id"]
    loan_id = agreement.get("loanId")

    agreement_update = {
        "status": BenefitStatus.ACTIVE.value,
        "acceptingPayments": True,
        "scheduleGenerated": True,
        "installmentsGenerated": planned,
        "endDate": end_dt,
        "suspendedReason": None,
    }
    stamp_update(agreement_update, event_ctx.actor_id)
    txn.update(agreements.ref(client, agreement_id), agreement_update)

    if loan_id:
        loan_update = {
            "benefitStatus": BenefitStatus.ACTIVE.value,
            "benefitAgreementId": agreement_id,
            "nextContributionDate": first_dt,
            "nextContributionAmountCents": first_amount,
        }
        stamp_update(loan_update, event_ctx.actor_id)
        txn.update(loans.ref(client, loan_id), loan_update)

    servicing_events.append(
        txn,
        event_type="BENEFIT_ACTIVATED",
        entity_type=ENTITY_TYPE,
        entity_id=agreement_id,
        actor_id=event_ctx.actor_id,
        actor_role=event_ctx.actor_role,
        actor_name=event_ctx.actor_name,
        correlation_id=event_ctx.correlation_id,
        sequence=1,
        metadata={
            "previousStatus": BenefitStatus.ACTIVATING.value,
            "newStatus": BenefitStatus.ACTIVE.value,
            "installmentsGenerated": planned,
            "firstContributionDate": first_dt.isoformat(),
            "endDate": end_dt.isoformat(),
        },
        loan_id=loan_id,
        borrower_id=agreement.get("borrowerId"),
        employer_id=agreement.get("employerId"),
        benefit_agreement_id=agreement_id,
    )


# --------------------------------------------------------------------------- #
# result bodies
# --------------------------------------------------------------------------- #
def _success_result(
    agreement_id: str,
    agreement: dict,
    *,
    planned: int,
    term: int,
    total: int,
    start_date: date,
    first_dt: datetime,
    end_dt: datetime,
    first_amount: int,
    correlation_id: str,
) -> dict:
    """The finalized summary — shaped like ``activate_benefit``'s result so the
    task wrapper can complete the ACTIVATE idempotency key with it."""
    return {
        "agreementId": agreement_id,
        "status": BenefitStatus.ACTIVE.value,
        "acceptingPayments": True,
        "scheduleGenerated": True,
        "plannedInstallmentCount": planned,
        "installmentsGenerated": planned,
        "termMonths": term,
        "totalCommitmentCents": total,
        "remainingCommitmentCents": int(
            agreement.get("remainingCommitmentCents", total)
        ),
        "amountPaidCents": int(agreement.get("amountPaidCents", 0)),
        "currency": agreement.get("currency", "USD"),
        "startDate": start_date.isoformat(),
        "endDate": end_dt.isoformat(),
        "firstContributionDate": first_dt.isoformat(),
        "nextContributionAmountCents": first_amount,
        "finalized": True,
        "correlationId": correlation_id,
    }


def _halt_result(agreement_id: str, planned: int, status: Any) -> dict:
    """Summary for a halted run — the agreement left ``ACTIVATING`` mid-generation
    (e.g. terminated). Whatever was already created is retained."""
    return {
        "agreementId": agreement_id,
        "status": status,
        "plannedInstallmentCount": planned,
        "finalized": False,
        "halted": True,
        "reason": "agreement no longer ACTIVATING; generation halted",
    }
