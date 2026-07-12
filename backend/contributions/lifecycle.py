"""contributions.lifecycle — the cancel-future-contributions task (specs/10 §10.4).

When a benefit agreement is terminated (the employment-status-change cascade,
specs/10 §10.4), every *future* contribution of that agreement must be
cancelled. For Phase 2 (specs/19 §19.2) this "task" runs **inline** — a bounded
in-process function called synchronously *after* the terminate command's
transaction has committed (no Cloud Task yet; Phase 3).

Contract (specs/10 §10.4, specs/14 §14.4):

* **Which contributions** — those in ``SCHEDULED``, ``RETRY_PENDING`` **or**
  ``FAILED``. Each is transitioned to ``CANCELED`` (guarded on its *current*
  status, read inside the transaction). ``PROCESSING`` contributions are **not
  touched** (the money may already be moving — they settle through their normal
  Phase-3, and a ``TERMINATED`` agreement routes a late failure to
  settle-then-cancel; specs/10 §10.4 in-flight resolution).
* **FAILED rows** additionally have their ``currentExceptionId`` **dismissed**
  ("benefit terminated") and ``loan.openExceptionCount`` decremented — a
  termination must never leave a permanently open, un-retryable exception
  (specs/09 §9.3).
* **Events** — one ``PAYMENT_CANCELED`` servicing event per cancelled
  contribution (every material change is evented), then a single final
  ``FUTURE_CONTRIBUTIONS_CANCELED`` event, all sharing the command
  ``correlationId``.
* **Look-ahead** — on completion ``loan.nextContributionDate`` /
  ``nextContributionAmountCents`` are nulled (the schedule is gone).
* **Bounded batches** (specs/14 §14.4) — cancellation is applied in transactions
  of at most :data:`BATCH_SIZE` contributions. Each cancelled item costs ~3–4
  writes (update + event + mirror, plus a dismiss for a FAILED row), keeping
  every batch well under Firestore's 500-writes/transaction cap.
* **Idempotent** — re-running skips contributions already ``CANCELED`` (the
  per-item transition is status-guarded), so a redelivery / continuation
  resumes without redoing or double-counting work.

Returns a summary ``{"canceled": n, "skipped_processing": m}``.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Optional

from commands.base import CommandContext, NotFound
from common.enums import ContributionStatus, ExceptionStatus
from common.state_machines import assert_transition
from repositories import (
    agreements,
    contributions,
    loans,
    stamp_update,
)
from servicing import events as servicing_events

logger = logging.getLogger("bsw.projections")

# Entity-type tags on servicing events (mirror payments.service spelling).
_ENTITY_CONTRIBUTION = "scheduledContribution"
_ENTITY_LOAN = "loan"

# Contribution statuses that are cancellable on termination (specs/10 §10.4).
_CANCELABLE = frozenset(
    {
        str(ContributionStatus.SCHEDULED),
        str(ContributionStatus.RETRY_PENDING),
        str(ContributionStatus.FAILED),
    }
)

# Bounded-batch size (specs/14 §14.4). A cancelled item costs up to ~4 writes
# (contribution update + event global + event mirror + a FAILED-row exception
# dismiss); 100 items ≤ ~400 writes + one loan update — well under the 500 cap.
BATCH_SIZE = 100


class _Seq:
    """Monotonic (within a correlationId) event-sequence counter, retry-safe.

    Instantiated fresh inside every transaction handler so a Firestore contention
    retry re-runs the handler and re-derives identical sequence numbers
    (specs/04 §4.9, specs/08 §8.5).
    """

    def __init__(self, start: int = 1) -> None:
        self._n = start

    def __call__(self) -> int:
        n = self._n
        self._n += 1
        return n


def _get_in_txn(txn: Any, ref: Any) -> Optional[dict]:
    """Read a single ``DocumentReference`` inside ``txn`` as dict-with-id/None."""
    got = txn.get(ref)
    snap = got if hasattr(got, "exists") else next(iter(got), None)
    if snap is None or not getattr(snap, "exists", False):
        return None
    data = snap.to_dict() or {}
    data["id"] = snap.id
    return data


def _event_common(contribution: dict) -> dict:
    """The denormalized entity pointers every contribution event carries."""
    return {
        "loan_id": contribution.get("loanId"),
        "borrower_id": contribution.get("borrowerId"),
        "employer_id": contribution.get("employerId"),
        "benefit_agreement_id": contribution.get("benefitAgreementId"),
    }


def _nudge_cancel_projections(
    ctx: CommandContext, *, loan_id: Optional[str], employer_id: Optional[str]
) -> None:
    """POST-COMMIT: fan out the read-model recompute after cancel-future-contributions.

    Call only AFTER the cancel batches + the ``_finalize`` completion commit — never
    inside a transaction (specs/05 §5.1 hot-doc rule). Cancelling every future
    contribution restates the portfolio ``contributionStatusCounts``, the employer
    rollups, and the loan mirror (whose ``nextContribution*`` look-ahead was nulled);
    the ``FUTURE_CONTRIBUTIONS_CANCELED`` fanout maps to those keys and enqueues an
    idempotent recompute-from-source. (``portfolioSummaries/{period}.scheduledCents``
    spans many periods the event can't enumerate — see the NB in
    :func:`projections.fanout.affected_keys`; ``rebuild-summaries`` owns it.)

    Guarded + best-effort: a fanout failure is logged and swallowed so it can never
    break the already-committed cancel tail; ``rebuild-summaries`` is the backstop.
    Runs both inline (from terminate/employment) and via the
    ``cancel-future-contributions`` task, so nudging here covers both modes.
    """
    try:
        from projections.fanout import enqueue_for_event

        enqueue_for_event(
            {
                "eventType": "FUTURE_CONTRIBUTIONS_CANCELED",
                "loanId": loan_id,
                "employerId": employer_id,
                "metadata": {},
            },
            ctx=ctx,
        )
    except Exception:  # noqa: BLE001 — best-effort; rebuild-summaries is the backstop
        logger.warning(
            "projection nudge failed after cancel-future for loan %s "
            "(rebuild will reconcile)",
            loan_id,
            exc_info=True,
        )


def cancel_future_contributions(
    client: Any = None,
    *,
    agreement_id: str,
    ctx: CommandContext,
    reason: str,
) -> dict:
    """Cancel every future contribution of ``agreement_id`` (specs/10 §10.4).

    Runs **inline** in bounded batches (see module docstring). Idempotent:
    re-running skips contributions already ``CANCELED``. Returns
    ``{"canceled": n, "skipped_processing": m}``.
    """
    if client is None:
        from common.firestore import get_client

        client = get_client()

    # This post-commit inline task owns an INDEPENDENT event-sequence space:
    # derive a task-scoped correlationId (":cancel-future") threaded through every
    # batch + the finalize, so its events (each batch/finalize restarts sequence at
    # 1) never collide on (correlationId, sequence) with the terminate/employment
    # command's own events under the base correlationId (specs/04 §4.9).
    ctx = replace(ctx, correlation_id=f"{ctx.correlation_id}:cancel-future")

    # Locate the agreement (for the loan look-ahead + event denorm). Called after
    # the terminate command committed, so a missing agreement is a hard bug —
    # surface it rather than silently no-op.
    agreement = agreements.get(client, agreement_id)
    if agreement is None:
        raise NotFound(f"benefit agreement {agreement_id!r} not found")
    loan_id = agreement.get("loanId")
    borrower_id = agreement.get("borrowerId")
    employer_id = agreement.get("employerId")

    # Read the full schedule once (bounded by the agreement's term length) and
    # partition it. PROCESSING rows are counted as skipped and left untouched;
    # cancellable rows are chunked into bounded batches. The per-item transition
    # is re-read + status-guarded inside each transaction, so this pre-read is
    # only a work list — staleness cannot cause a bad write.
    schedule = contributions.list_for_agreement(client, agreement_id)
    skipped_processing = sum(
        1 for c in schedule if c.get("status") == str(ContributionStatus.PROCESSING)
    )
    candidate_ids = [
        c["id"] for c in schedule if c.get("status") in _CANCELABLE
    ]

    canceled = 0
    for start in range(0, len(candidate_ids), BATCH_SIZE):
        batch_ids = candidate_ids[start : start + BATCH_SIZE]
        summary = _cancel_batch(
            client,
            ctx=ctx,
            reason=reason,
            loan_id=loan_id,
            contribution_ids=batch_ids,
        )
        canceled += summary["canceled"]
        skipped_processing += summary["skipped_processing"]

    # Completion: null the loan look-ahead + write the final event (specs/10
    # §10.4). One small transaction, sharing the command correlationId.
    _finalize(
        client,
        ctx=ctx,
        reason=reason,
        agreement_id=agreement_id,
        loan_id=loan_id,
        borrower_id=borrower_id,
        employer_id=employer_id,
        canceled=canceled,
        skipped_processing=skipped_processing,
    )

    # POST-COMMIT: nudge the read-model recompute off the txn (§5.1). Gated on real
    # work (canceled > 0) to mirror the FUTURE_CONTRIBUTIONS_CANCELED event, which
    # `_finalize` only emits when something was cancelled — a redelivery/continuation
    # that finds everything already CANCELED changed nothing and needs no nudge.
    if canceled > 0:
        _nudge_cancel_projections(ctx, loan_id=loan_id, employer_id=employer_id)

    return {"canceled": canceled, "skipped_processing": skipped_processing}


def _cancel_batch(
    client: Any,
    *,
    ctx: CommandContext,
    reason: str,
    loan_id: Optional[str],
    contribution_ids: list[str],
) -> dict:
    """Cancel one bounded batch of contributions in a single transaction."""
    from commands.base import transactional
    from exceptions import service as exceptions_service

    @transactional(client)
    def _run(txn: Any) -> dict:
        # -- reads (all before any write — Firestore ordering rule) -----------
        docs: list[dict] = []
        for cid in contribution_ids:
            doc = _get_in_txn(txn, contributions.ref(client, cid))
            if doc is not None:
                docs.append(doc)

        # Rows we will actually cancel this batch (status-guarded → idempotent).
        to_cancel = [d for d in docs if d.get("status") in _CANCELABLE]
        skipped = sum(
            1 for d in docs if d.get("status") == str(ContributionStatus.PROCESSING)
        )

        # FAILED rows whose exception is STILL OPEN/IN_REVIEW each decrement
        # openExceptionCount. Read each exception in-txn (before any write) so an
        # exception an operator already resolved is neither re-dismissed (which
        # would overwrite their resolution) nor double-counted (undercounting the
        # loan's true open count) — parity with the payments finalize gate.
        from repositories import operational_exceptions

        open_exc_ids: set[str] = set()
        for d in to_cancel:
            exc_id = d.get("currentExceptionId")
            if d.get("status") == str(ContributionStatus.FAILED) and exc_id:
                exc = _get_in_txn(txn, operational_exceptions.ref(client, exc_id))
                if exc is not None and exc.get("status") in (
                    str(ExceptionStatus.OPEN),
                    str(ExceptionStatus.IN_REVIEW),
                ):
                    open_exc_ids.add(exc_id)
        exception_decrements = len(open_exc_ids)
        loan = None
        if exception_decrements and loan_id:
            loan = _get_in_txn(txn, loans.ref(client, loan_id))

        # -- writes -----------------------------------------------------------
        seq = _Seq()
        canceled = 0
        for doc in to_cancel:
            cid = doc["id"]
            previous_status = doc["status"]
            assert_transition(
                "contribution", previous_status, ContributionStatus.CANCELED
            )
            contrib_update: dict[str, Any] = {
                "status": str(ContributionStatus.CANCELED),
            }
            exc_id = doc.get("currentExceptionId")
            if previous_status == str(ContributionStatus.FAILED) and exc_id:
                # Never orphan a permanently open, un-retryable exception on a
                # terminated agreement (specs/09 §9.3, specs/10 §10.4). Dismiss
                # ONLY a still-open exception (don't overwrite an operator's prior
                # resolution); always clear the now-stale pointer on the canceled row.
                if exc_id in open_exc_ids:
                    exceptions_service.dismiss(txn, client, exc_id, reason=reason)
                contrib_update["currentExceptionId"] = None
            stamp_update(contrib_update, ctx.actor_id)
            txn.update(contributions.ref(client, cid), contrib_update)

            servicing_events.append(
                txn,
                event_type="PAYMENT_CANCELED",
                entity_type=_ENTITY_CONTRIBUTION,
                entity_id=cid,
                actor_id=ctx.actor_id,
                actor_role=ctx.actor_role,
                actor_name=ctx.actor_name,
                correlation_id=ctx.correlation_id,
                sequence=seq(),
                metadata={
                    "reason": reason,
                    "previousStatus": previous_status,
                    "periodLabel": doc.get("periodLabel"),
                    "installmentNumber": doc.get("installmentNumber"),
                },
                **_event_common(doc),
            )
            canceled += 1

        # Decrement openExceptionCount once for the whole batch (single loan
        # write; read-before-write already satisfied above).
        if exception_decrements and loan is not None:
            loan_update = {
                "openExceptionCount": max(
                    0, int(loan.get("openExceptionCount", 0)) - exception_decrements
                )
            }
            stamp_update(loan_update, ctx.actor_id)
            txn.update(loans.ref(client, loan_id), loan_update)

        return {"canceled": canceled, "skipped_processing": skipped}

    return _run()


def _finalize(
    client: Any,
    *,
    ctx: CommandContext,
    reason: str,
    agreement_id: str,
    loan_id: Optional[str],
    borrower_id: Optional[str],
    employer_id: Optional[str],
    canceled: int,
    skipped_processing: int,
) -> None:
    """Null the loan look-ahead and write the final completion event."""
    from commands.base import transactional

    @transactional(client)
    def _run(txn: Any) -> None:
        seq = _Seq()
        # Null the loan look-ahead — the future schedule is gone (specs/10 §10.4).
        if loan_id:
            loan = _get_in_txn(txn, loans.ref(client, loan_id))
            if loan is not None:
                loan_update = {
                    "nextContributionDate": None,
                    "nextContributionAmountCents": None,
                }
                stamp_update(loan_update, ctx.actor_id)
                txn.update(loans.ref(client, loan_id), loan_update)

        # Only append the summary event when this run actually canceled something.
        # A re-drive (continuation / redelivery) after every future contribution is
        # already CANCELED does no work — emitting again would append a duplicate
        # FUTURE_CONTRIBUTIONS_CANCELED. The look-ahead null above stays
        # unconditional (idempotent in value; the schedule is gone on terminate).
        if canceled > 0:
            servicing_events.append(
                txn,
                event_type="FUTURE_CONTRIBUTIONS_CANCELED",
                entity_type=_ENTITY_LOAN if loan_id else "benefitAgreement",
                entity_id=loan_id or agreement_id,
                actor_id=ctx.actor_id,
                actor_role=ctx.actor_role,
                actor_name=ctx.actor_name,
                correlation_id=ctx.correlation_id,
                sequence=seq(),
                metadata={
                    "reason": reason,
                    "canceledCount": canceled,
                    "skippedProcessing": skipped_processing,
                },
                loan_id=loan_id,
                borrower_id=borrower_id,
                employer_id=employer_id,
                benefit_agreement_id=agreement_id,
            )

    _run()
