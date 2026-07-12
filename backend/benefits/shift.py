"""benefits.shift — the schedule-shift-on-resume task (specs/07 §7.8, specs/10 §10.2).

When a suspended benefit is **resumed**, the installments whose ``scheduledDate``
passed during the suspension are *not* fired as an immediate catch-up lump.
Instead the remaining schedule **shifts**: every remaining ``SCHEDULED``
installment is re-dated forward by the suspension duration (whole months,
rounded up, preserving day-of-month + the noon rule) and ``agreement.endDate``
extends by the same amount. Amounts and ``installmentNumber``s are untouched, so
invariant I5 (``Σ(scheduledAmountCents) == totalCommitmentCents``) and the
deterministic contribution IDs are preserved and the benefit still reaches
``COMPLETED``. ``RETRY_PENDING``/``FAILED`` installments are *past obligations*,
not future schedule — they are **not** re-dated; they become processable again
the moment ``acceptingPayments`` flips true.

**Phase 2 execution (specs/19 §19.2).** There is no Cloud Task yet, so
:func:`shift_schedule` is a bounded in-process function the resume command calls
*synchronously* after its transaction commits. It runs the re-dating in bounded
**transaction** passes (:data:`_BATCH_SIZE` installments each, well under
Firestore's 500-writes/transaction cap); a final transaction extends
``agreement.endDate``, refreshes ``loan.nextContributionDate`` /
``nextContributionAmountCents``, and writes **one** ``SCHEDULE_SHIFTED`` event.

**Concurrency-safe re-dating.** Each installment is **re-read inside its batch
transaction** and re-dated only if it is *still* ``SCHEDULED`` (status-guarded,
mirroring :mod:`contributions.lifecycle`'s cancel-batch). A concurrent payment
that moved an installment ``SCHEDULED`` → ``POSTED`` between the up-front
work-list query and the write is therefore **skipped, never overwritten** — the
plain query is only a work list, so staleness can cause a skip but never a bad
write.

**Idempotency (specs/07 §7.8).** The target date of installment *n* is anchored
to the *immutable* ``agreement.startDate`` shifted by the agreement's cumulative
``scheduleShiftMonths`` — ``scheduled_datetime(startDate + M months, n)`` where
``M`` is the running TOTAL shift ``resume_benefit`` persists across *every* prior
resume — so it is a pure function of ``(startDate, M, n)`` and does not depend on
the contribution's current (possibly already-shifted) date. Re-running the task
therefore recomputes the identical target; the per-installment guard ``target >
current`` makes an already-shifted installment a no-op, and the whole task is a
no-op (no event written) when nothing needs to move. A *later* suspension bumps
``scheduleShiftMonths`` again (in resume's core txn), raising ``M`` so the
schedule shifts *cumulatively* further forward. The guard is also strictly
forward-only: a target that is not *after* the current date is never applied, so
the task can never move an installment backwards.

**Read-before-write.** Every transaction here reads before it writes: the batch
passes re-read each contribution before re-dating it; the final pass re-reads the
agreement (endDate guard) and the earliest still-``SCHEDULED`` installment (the
loan look-ahead) before its writes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import Any, Optional

from commands.base import CommandContext, NotFound, transactional
from common.enums import ContributionStatus
from common.periods import (
    SCHEDULED_TIME,
    SYSTEM_TIMEZONE,
    period_label,
    scheduled_datetime,
    shift_months,
)
from repositories import agreements, contributions, loans, refs, stamp_update
from servicing import events as servicing_events

ENTITY_TYPE = "BENEFIT_AGREEMENT"

# Bounded-batch size for the re-dating passes. Each re-dated installment costs a
# single contribution update, so a full batch stays far under Firestore's
# 500-writes/transaction cap; the final agreement/loan/event pass is separate.
_BATCH_SIZE = 400


# --------------------------------------------------------------------------- #
# datetime coercion (Firestore Timestamp / date / ISO string -> local values)
# --------------------------------------------------------------------------- #
def _as_aware(value: Any) -> Optional[datetime]:
    """Coerce a stored date/timestamp/ISO value to an aware ``datetime``, or None.

    A naive datetime is read as wall-clock in ``SYSTEM_TIMEZONE``; a tz-aware one
    (a Firestore Timestamp, UTC) is kept as-is; a plain ``date`` becomes noon
    local; an ISO string is parsed. Used only for *comparisons* (instants), so
    the exact wall-clock of a bare date does not matter as long as it is stable.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=SYSTEM_TIMEZONE)
        return value
    if isinstance(value, date):
        return datetime.combine(value, SCHEDULED_TIME, tzinfo=SYSTEM_TIMEZONE)
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SYSTEM_TIMEZONE)
        return parsed
    return None


def _as_local_date(value: Any) -> Optional[date]:
    """Coerce a stored date/timestamp/ISO value to a ``SYSTEM_TIMEZONE`` date."""
    aware = _as_aware(value)
    if aware is None:
        return None
    return aware.astimezone(SYSTEM_TIMEZONE).date()


# --------------------------------------------------------------------------- #
# suspension duration -> whole months, rounded up
# --------------------------------------------------------------------------- #
def _months_between(suspended_from: Any, resumed_at: Any) -> int:
    """Whole calendar months from ``suspended_from`` to ``resumed_at``, rounded up.

    Both endpoints are taken as dates in ``SYSTEM_TIMEZONE`` (specs/07 §7.8). The
    result is the smallest ``m >= 0`` such that ``suspended_from + m months`` is
    on or after ``resumed_at`` — i.e. any partial trailing month rounds up. A
    resume on/before the suspend instant yields ``0`` (nothing to shift).
    """
    start = _as_local_date(suspended_from)
    end = _as_local_date(resumed_at)
    if start is None or end is None or end <= start:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if months < 0:
        return 0
    # Round a partial trailing month up: if the whole-month anchor lands before
    # the resume date, one more whole month is needed to cover the remainder.
    if shift_months(start, months) < end:
        months += 1
    return months


# --------------------------------------------------------------------------- #
# transactional read helpers
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


def _earliest_scheduled_in_txn(
    txn: Any, client: Any, agreement_id: str
) -> Optional[dict]:
    """Lowest-``installmentNumber`` still-``SCHEDULED`` contribution, read in txn.

    Backs the loan look-ahead refresh; read inside the finalize transaction
    (before any write) so it reflects the post-shift schedule. Mirrors
    :func:`payments.service._next_scheduled_in_txn`.
    """
    query = (
        client.collection(refs.SCHEDULED_CONTRIBUTIONS)
        .where(filter=refs.field_filter("benefitAgreementId", "==", agreement_id))
        .where(filter=refs.field_filter("status", "==", ContributionStatus.SCHEDULED.value))
        .order_by("installmentNumber")
        .limit(1)
    )
    for snap in query.get(transaction=txn):
        data = snap.to_dict() or {}
        data["id"] = snap.id
        return data
    return None


# --------------------------------------------------------------------------- #
# the task
# --------------------------------------------------------------------------- #
def shift_schedule(
    client: Any,
    *,
    agreement_id: str,
    ctx: CommandContext,
    suspended_from: Any,
    resumed_at: Any,
) -> dict:
    """Re-date the remaining schedule of a resumed benefit forward (specs/07 §7.8).

    The shift amount is the agreement's cumulative ``scheduleShiftMonths`` — the
    running total ``resume_benefit`` maintains across every suspension — so a 2nd
    suspend/resume shifts the schedule *cumulatively*. ``suspended_from`` /
    ``resumed_at`` are the current suspension's start/end instants (stored
    timestamps, local dates, or ISO strings), retained only for the
    ``SCHEDULE_SHIFTED`` event's window metadata. Returns a small summary
    ``{agreementId, shiftMonths, installmentsShifted, endDate, nextContributionDate}``.
    A zero total shift, an agreement with no remaining ``SCHEDULED`` installments,
    or a re-run where everything is already shifted is a no-op that writes nothing
    (and no ``SCHEDULE_SHIFTED`` event).
    """
    # This post-commit inline task owns an INDEPENDENT event-sequence space:
    # derive a task-scoped correlationId (":shift") so its SCHEDULE_SHIFTED event
    # (sequence 1) can never collide on (correlationId, sequence) with the resume
    # command's own events, which restart sequence at 1 under the base
    # correlationId (specs/04 §4.9).
    ctx = replace(ctx, correlation_id=f"{ctx.correlation_id}:shift")

    # --- reads: agreement + the work list ----------------------------------
    agreement = agreements.get(client, agreement_id)
    if agreement is None:
        raise NotFound(f"benefit agreement {agreement_id!r} not found")
    loan_id = agreement.get("loanId")

    # The TOTAL cumulative shift is the agreement's persisted witness (specs/07
    # §7.8): resume_benefit adds each suspension's whole-month duration to
    # scheduleShiftMonths in its core txn. Anchoring to this TOTAL (not just the
    # current suspension) makes a 2nd suspend/resume shift cumulatively, while a
    # re-run of the SAME resume recomputes identical targets — the forward-only
    # guard below then makes it an idempotent no-op.
    total_months = int(agreement.get("scheduleShiftMonths", 0) or 0)

    start_local = _as_local_date(agreement.get("startDate"))
    term = int(
        agreement.get("termMonths")
        or agreement.get("plannedInstallmentCount")
        or agreement.get("installmentsGenerated")
        or 0
    )
    if total_months <= 0 or start_local is None or term <= 0:
        return _noop_result(agreement_id, total_months, agreement)

    # The immutable anchor: reproduce the cumulatively-shifted schedule from the
    # immutable startDate so the target of installment n is a pure function of
    # (startDate, total_months, n) — independent of its current (already-shifted)
    # date, hence re-runnable.
    effective_start = shift_months(start_local, total_months)

    # Work list: currently-SCHEDULED installment ids (a stale snapshot — each is
    # re-read + status-guarded inside _shift_batch, so staleness can only cause a
    # skip, never a bad write).
    all_contribs = contributions.list_for_agreement(client, agreement_id)
    candidate_ids = [
        c["id"]
        for c in all_contribs
        if c.get("status") == ContributionStatus.SCHEDULED.value
    ]

    # --- re-dating passes (bounded transactions; forward-only, status-guarded) --
    installments_shifted = 0
    for start in range(0, len(candidate_ids), _BATCH_SIZE):
        batch_ids = candidate_ids[start : start + _BATCH_SIZE]
        installments_shifted += _shift_batch(
            client,
            ctx=ctx,
            effective_start=effective_start,
            contribution_ids=batch_ids,
        )

    # --- final pass: endDate + loan look-ahead + one SCHEDULE_SHIFTED event -----
    return _finalize_shift(
        client,
        ctx=ctx,
        agreement_id=agreement_id,
        loan_id=loan_id,
        effective_start=effective_start,
        term=term,
        total_months=total_months,
        installments_shifted=installments_shifted,
        suspended_from=suspended_from,
        resumed_at=resumed_at,
    )


def _shift_batch(
    client: Any,
    *,
    ctx: CommandContext,
    effective_start: date,
    contribution_ids: list[str],
) -> int:
    """Re-date one bounded batch of installments in a single transaction.

    Each contribution is RE-READ inside the transaction and re-dated only if it is
    still ``SCHEDULED`` and its anchored target is strictly forward of its current
    ``scheduledDate`` (forward-only + idempotent). A row a concurrent payment moved
    ``SCHEDULED`` → ``POSTED`` between the work-list read and here is skipped, never
    overwritten. Returns the number of installments actually re-dated.
    """

    @transactional(client)
    def _run(txn: Any) -> int:
        # -- reads (all before any write — Firestore ordering rule) -----------
        docs: list[dict] = []
        for cid in contribution_ids:
            doc = _get_in_txn(txn, contributions.ref(client, cid))
            if doc is not None:
                docs.append(doc)

        # -- writes: forward-only, status-guarded re-date ---------------------
        shifted = 0
        for doc in docs:
            if doc.get("status") != ContributionStatus.SCHEDULED.value:
                continue  # concurrently moved (e.g. POSTED) — never re-date it
            n = int(doc["installmentNumber"])
            target = scheduled_datetime(effective_start, n)
            current = _as_aware(doc.get("scheduledDate"))
            if current is not None and target <= current:
                continue  # already at/after the anchored target — idempotent no-op
            update = {
                "scheduledDate": target,
                "periodLabel": period_label(target),
            }
            stamp_update(update, ctx.actor_id)
            txn.update(contributions.ref(client, doc["id"]), update)
            shifted += 1
        return shifted

    return _run()


def _finalize_shift(
    client: Any,
    *,
    ctx: CommandContext,
    agreement_id: str,
    loan_id: Optional[str],
    effective_start: date,
    term: int,
    total_months: int,
    installments_shifted: int,
    suspended_from: Any,
    resumed_at: Any,
) -> dict:
    """Extend ``endDate`` + refresh the loan look-ahead + write one event.

    Re-reads the agreement (endDate forward-only guard) and the earliest still-
    ``SCHEDULED`` installment (look-ahead) inside the transaction. Writes the
    ``SCHEDULE_SHIFTED`` event only when real work happened this run —
    installments were re-dated OR ``endDate`` still needed extending — so a
    re-drive of an already-shifted schedule appends no duplicate summary event.
    """

    @transactional(client)
    def _run(txn: Any) -> dict:
        # -- reads (all before any write) -------------------------------------
        agreement = _get_in_txn(txn, agreements.ref(client, agreement_id))
        if agreement is None:
            raise NotFound(f"benefit agreement {agreement_id!r} not found")

        # endDate corresponds to the last installment; anchor it the same way and
        # only extend it forward (never pull it back).
        anchored_end = scheduled_datetime(effective_start, term)
        current_end = _as_aware(agreement.get("endDate"))
        end_changed = current_end is None or anchored_end > current_end

        # Idempotent no-op: nothing moved this run and endDate is already extended.
        # Write NO event, so a re-drive never appends a duplicate SCHEDULE_SHIFTED.
        if installments_shifted == 0 and not end_changed:
            return _noop_result(agreement_id, total_months, agreement)

        # Look-ahead read (still before any write): the earliest remaining
        # SCHEDULED installment (lowest installmentNumber), re-dated.
        earliest = (
            _earliest_scheduled_in_txn(txn, client, agreement_id) if loan_id else None
        )

        # -- writes -----------------------------------------------------------
        if end_changed:
            agreement_update = {"endDate": anchored_end}
            stamp_update(agreement_update, ctx.actor_id)
            txn.update(agreements.ref(client, agreement_id), agreement_update)

        next_date: Optional[datetime] = None
        next_amount: Optional[int] = None
        if loan_id and earliest is not None:
            next_date = scheduled_datetime(
                effective_start, int(earliest["installmentNumber"])
            )
            next_amount = int(earliest["scheduledAmountCents"])
            loan_update = {
                "nextContributionDate": next_date,
                "nextContributionAmountCents": next_amount,
            }
            stamp_update(loan_update, ctx.actor_id)
            txn.update(loans.ref(client, loan_id), loan_update)

        servicing_events.append(
            txn,
            event_type="SCHEDULE_SHIFTED",
            entity_type=ENTITY_TYPE,
            entity_id=agreement_id,
            actor_id=ctx.actor_id,
            actor_role=ctx.actor_role,
            actor_name=ctx.actor_name,
            correlation_id=ctx.correlation_id,
            sequence=1,
            metadata={
                "shiftMonths": total_months,
                "installmentsShifted": installments_shifted,
                "suspendedFrom": _iso_or_none(suspended_from),
                "resumedAt": _iso_or_none(resumed_at),
                "endDate": anchored_end.isoformat(),
                "nextContributionDate": next_date.isoformat() if next_date else None,
            },
            loan_id=loan_id,
            borrower_id=agreement.get("borrowerId"),
            employer_id=agreement.get("employerId"),
            benefit_agreement_id=agreement_id,
        )

        return {
            "agreementId": agreement_id,
            "shiftMonths": total_months,
            "installmentsShifted": installments_shifted,
            "endDate": anchored_end.isoformat(),
            "nextContributionDate": next_date.isoformat() if next_date else None,
            "correlationId": ctx.correlation_id,
        }

    return _run()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _noop_result(agreement_id: str, months: int, agreement: dict) -> dict:
    """Summary body for a no-op shift (nothing re-dated, no event written)."""
    end = _as_aware(agreement.get("endDate"))
    return {
        "agreementId": agreement_id,
        "shiftMonths": months,
        "installmentsShifted": 0,
        "endDate": end.isoformat() if end else None,
        "nextContributionDate": None,
    }


def _iso_or_none(value: Any) -> Optional[str]:
    aware = _as_aware(value)
    return aware.isoformat() if aware else None
