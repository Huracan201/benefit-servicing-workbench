"""employment.services — the change-employment-status command (specs/10 §10.4).

Changes a borrower's ``employmentStatus`` (validated against the employment
state machine, specs/06 §6.5) and **cascades** to that borrower's active benefit
agreement per the §10.4 mapping:

======================  ==================================================
New employment status   Benefit cascade
======================  ==================================================
``LEAVE``               ``ACTIVE`` benefit → ``SUSPENDED`` (``suspendedReason
                        = LEAVE``). Already ``SUSPENDED`` (manual **or** leave):
                        no-op — the existing suspension + its reason stand.
``TERMINATED``          ``ACTIVE``/``SUSPENDED``/``ACTIVATING`` benefit →
                        ``TERMINATED`` + cancel every future contribution.
``ACTIVE`` (return      ``SUSPENDED`` benefit → ``ACTIVE`` **only when
from leave)             ``suspendedReason == LEAVE``** + schedule shift. A
                        *manually* suspended benefit stays suspended.
======================  ==================================================

**Cascade idempotency (specs/06 §6.7).** The cascade is an idempotent **no-op
when the benefit is already at or past the target state** — it is guarded on the
benefit's *current* status (read inside the transaction), never on
``assert_transition``, so a benefit that has run ahead of the employment change
never fails the command with ``409 INVALID_TRANSITION``. The manual-vs-leave
distinction is honoured: only a ``suspendedReason == LEAVE`` benefit is
auto-resumed.

**One coherent command (specs/10 §10.4 step 1).** The borrower status change,
the benefit-status cascade write, and **both** servicing events
(``EMPLOYMENT_STATUS_CHANGED`` sequence 1, then the cascade event sequence 2)
share **one** ``correlationId`` and commit in a **single** transaction under
**one** idempotency key (operation ``change-employment-status``). The bounded
follow-up work — cancel-future-contributions on terminate, schedule-shift on
resume — is handed onto its async task via :func:`internal.enqueue.enqueue`
**after** that transaction commits (the COMPLETION PROTOCOL, Decision A;
specs/14): inline mode runs it synchronously (``200``), cloud mode defers it
(``202`` + ``Retry-After``). The task wrapper runs the idempotent tail THEN
completes the idempotency key; a cascade with **no** tail (a LEAVE-suspend, a
no-op, or no active agreement) completes the key inside the core transaction
instead. Those tasks are idempotent, so a redelivery/replay re-drives without
duplicate side effects.

**Known reaper limitation (deferred).** This command's idempotency record is
BORROWER-scoped — its ``entityId`` is the ``borrowerId`` — but the cascade tail it
hands off (cancel-future-contributions / shift-schedule) is AGREEMENT-scoped. The
enqueue payload carries the ``agreementId`` (and ``commandResult``) so the normal
inline/cloud handoff completes the key correctly; and a same-key CLIENT retry after
the lease expires reclaims via the ``reclaimed`` path below and re-derives + re-drives
the tail. What is NOT covered is the SERVER-driven lease reaper: it reads only the
record (whose ``entityId`` is a borrowerId, not the agreementId the tail needs) and
so **defers** an orphaned employment-cascade key rather than re-driving it (see
``idempotency.reaper._reclaim_one``'s ``OP_EMPLOYMENT`` branch). Closing this would
require persisting the agreementId onto the idempotency record (a change to the
``idempotency.service`` write path) AND teaching the reaper to infer which tail to
re-drive from the agreement's committed status — a broader, correctness-sensitive
change deliberately left for a later slice. An orphaned key here is unwedged either
by a same-key client retry or, failing that, by the ``PENDING`` record's TTL.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from commands.base import (
    ASYNC_LEASE_TTL_SECONDS,
    RETRY_AFTER_IN_PROGRESS,
    CommandContext,
    CommandError,
    IdempotencyKeyReused,
    NotFound,
    OperationInProgress,
    ValidationError,
    from_domain_error,
    transactional,
)
from common import errors as domain_errors
from common import state_machines
from common.enums import BenefitStatus, EmploymentStatus
from common.periods import SYSTEM_TIMEZONE
from idempotency import service as idempotency
from repositories import (
    agreements,
    borrowers,
    loans,
    stamp_update,
)
from servicing import events as servicing_events

OPERATION = "change-employment-status"
ENTITY_TYPE = "BORROWER"


def _nudge_projections(ctx: CommandContext, event_type: str, *, loan_id, employer_id) -> None:
    """POST-COMMIT: fan out the read-model recompute for an employment change.

    Call only AFTER the core transaction commits, on the real (non-replay) path —
    never inside a transaction (specs/05 §5.1 hot-doc rule). The change mirrors to
    the loan (employmentStatus) and any benefit cascade restates the portfolio /
    employer rollups; the fanout maps the eventType to those keys and enqueues an
    idempotent recompute. Best-effort (the fanout swallows its own errors; the
    scheduled rebuild is the backstop).
    """
    from projections.fanout import enqueue_for_event

    enqueue_for_event(
        {
            "eventType": event_type,
            "loanId": loan_id,
            "employerId": employer_id,
            "metadata": {},
        },
        ctx=ctx,
    )

# suspendedReason values (specs/04 §4.6, specs/10 §10.2/§10.4). MANUAL from the
# suspend command; LEAVE from this employment cascade.
_SUSPEND_REASON_LEAVE = "LEAVE"

# Reason stamped on the cancel-future-contributions task events (specs/10 §10.4).
_TERMINATE_REASON = "benefit terminated"

# Benefit statuses a TERMINATED cascade acts on (specs/10 §10.3/§10.4). Anything
# else (TERMINATED/COMPLETED/PENDING) is a benefit no-op.
_TERMINATABLE = frozenset(
    {
        BenefitStatus.ACTIVE.value,
        BenefitStatus.SUSPENDED.value,
        BenefitStatus.ACTIVATING.value,
    }
)


class _Seq:
    """Monotonic (within a correlationId) event-sequence counter, retry-safe.

    Instantiated fresh inside the transaction handler so a Firestore contention
    retry re-runs the handler and re-derives identical sequence numbers
    (specs/04 §4.9, specs/08 §8.5).
    """

    def __init__(self, start: int = 1) -> None:
        self._n = start

    def __call__(self) -> int:
        n = self._n
        self._n += 1
        return n


def _txn_get(txn: Any, ref: Any) -> Optional[dict]:
    """Read a single document inside the transaction, as dict-with-id or None."""
    got = txn.get(ref)
    snap = got if hasattr(got, "exists") else next(iter(got), None)
    if snap is None or not getattr(snap, "exists", False):
        return None
    data = snap.to_dict() or {}
    data["id"] = snap.id
    return data


def _now_local() -> datetime:
    """Current instant as a ``SYSTEM_TIMEZONE``-aware ``datetime``.

    Computed once at command entry (outside the transaction) so a Firestore
    contention retry of the handler reuses the same instant — the recorded
    ``suspendedAt`` / resume instant is stable across retries.
    """
    return datetime.now(SYSTEM_TIMEZONE)


def _validate_status(status: Any) -> str:
    """Coerce/validate the requested employment status (specs/06 §6.5)."""
    if not isinstance(status, str) or not status.strip():
        raise ValidationError(
            "employment status is required", code="EMPLOYMENT_STATUS_REQUIRED"
        )
    value = status.strip()
    valid = {s.value for s in EmploymentStatus}
    if value not in valid:
        raise ValidationError(
            f"unknown employment status {value!r}",
            code="EMPLOYMENT_STATUS_INVALID",
        )
    return value


def _discover_agreement(client: Any, borrower_id: str) -> tuple[Optional[str], Optional[str]]:
    """Find the borrower's loan + active benefit agreement (specs/04 §4.4).

    The authoritative borrower→loan link is ``loan.borrowerId``; the loan's
    synced ``benefitAgreementId`` points at the active agreement. This runs
    *outside* the transaction as a discovery step — the authoritative reads
    happen by ref inside the transaction, so staleness here cannot cause a bad
    write (the in-txn status guards decide the cascade). Returns
    ``(loan_id, agreement_id)`` (either may be ``None``).
    """
    borrower_loans = loans.list_for_borrower(client, borrower_id)
    seed = None
    for loan in borrower_loans:
        if loan.get("benefitAgreementId"):
            seed = loan
            break
    if seed is None and borrower_loans:
        seed = borrower_loans[0]
    if seed is None:
        return None, None
    return seed.get("id"), seed.get("benefitAgreementId")


def change_employment_status(
    *,
    borrower_id: str,
    ctx: CommandContext,
    status: str,
    effective_date: Any = None,
    reason: Optional[str] = None,
    client: Any = None,
) -> dict:
    """Change a borrower's employment status + cascade to the benefit (§10.4).

    Returns the response body (also stored for idempotent replay). Raises a
    :class:`commands.base.CommandError` subclass on any validation/idempotency/
    transition failure, which the view maps to the specs/11 §11.3 response.
    """
    if client is None:
        from common.firestore import get_client

        client = get_client()

    new_status = _validate_status(status)
    # effective_date is stamped as employmentEndDate on TERMINATED — validate it is
    # a date-ish value (None / ISO string / date / datetime) before it's persisted,
    # so a malformed body can't write junk into the audit field.
    if effective_date is not None and not isinstance(effective_date, (str, date, datetime)):
        raise ValidationError(
            "effective_date must be an ISO date string or omitted",
            code="INVALID_EFFECTIVE_DATE",
        )
    now = _now_local()

    # Discover the loan + active agreement outside the txn (work list only; the
    # authoritative reads + status guards are inside the txn).
    loan_id, agreement_id = _discover_agreement(client, borrower_id)

    # Post-commit signals — set only on the real (new/reclaimed) path, never on a
    # replay/in-progress/reuse. `ran` gates the tail + idempotency completion (a
    # LEAVE-suspend has no tail, so followup-presence alone cannot signal "ran");
    # `followup` carries which bounded tail to run.
    ran = {"done": False}
    followup: dict[str, Any] = {}
    emitted: dict[str, Any] = {}  # entity pointers for the post-commit nudge

    @transactional(client)
    def _run(txn: Any) -> dict:
        # --- reads (all before any write — Firestore ordering rule) ----------
        borrower = _txn_get(txn, borrowers.ref(client, borrower_id))
        if borrower is None:
            raise NotFound(f"borrower {borrower_id!r} not found")
        loan = _txn_get(txn, loans.ref(client, loan_id)) if loan_id else None
        # The loan's synced benefitAgreementId is authoritative for *which*
        # agreement to cascade to (specs/04 §4.4); fall back to the discovery
        # hint if the loan doc is unreadable.
        active_agreement_id = (
            (loan.get("benefitAgreementId") if loan else None) or agreement_id
        )
        agreement = (
            _txn_get(txn, agreements.ref(client, active_agreement_id))
            if active_agreement_id
            else None
        )

        # --- idempotency: begin inside the txn (reads then writes PENDING) ----
        outcome = idempotency.begin(
            txn,
            key=ctx.idempotency_key,
            operation=OPERATION,
            request_hash=ctx.request_hash,
            entity_id=borrower_id,
            entity_type=ENTITY_TYPE,
            lease_ttl_seconds=ASYNC_LEASE_TTL_SECONDS,
            lease_owner=ctx.lease_owner,
            client=client,
        )
        if outcome.is_replay:
            return outcome.result or {}
        if outcome.is_in_progress:
            raise OperationInProgress(
                "employment status change already in progress",
                retry_after=RETRY_AFTER_IN_PROGRESS,
                state={
                    "borrowerId": borrower_id,
                    "employmentStatus": borrower.get("employmentStatus"),
                },
            )
        if outcome.is_reuse:
            raise IdempotencyKeyReused(
                "idempotency key reused with a different request"
            )

        # --- borrower employment transition + benefit cascade ----------------
        # Reclaim-aware: on a same-key reclaim of an abandoned lease where the
        # borrower is ALREADY in `new_status`, the original call's core txn
        # committed the borrower change AND the whole cascade (one txn) but
        # crashed before the post-commit tail + completion. Skip the writes
        # (already applied), re-derive the tail from the agreement's committed
        # state, and RE-DRIVE it below. A genuine fresh key (reclaimed is False)
        # still runs assert_transition, so an illegal employment edge is a 409.
        previous_status = borrower.get("employmentStatus")
        already_target = previous_status == new_status
        if not (outcome.reclaimed and already_target):
            state_machines.assert_transition("employment", previous_status, new_status)

            seq = _Seq()

            borrower_update: dict[str, Any] = {"employmentStatus": new_status}
            if new_status == EmploymentStatus.TERMINATED.value:
                borrower_update["employmentEndDate"] = effective_date
            stamp_update(borrower_update, ctx.actor_id)
            txn.update(borrowers.ref(client, borrower_id), borrower_update)

            servicing_events.append(
                txn,
                event_type="EMPLOYMENT_STATUS_CHANGED",
                entity_type=ENTITY_TYPE,
                entity_id=borrower_id,
                actor_id=ctx.actor_id,
                actor_role=ctx.actor_role,
                actor_name=ctx.actor_name,
                correlation_id=ctx.correlation_id,
                sequence=seq(),
                metadata={
                    "previousStatus": previous_status,
                    "newStatus": new_status,
                    "effectiveDate": _iso_or_value(effective_date),
                    "reason": reason,
                },
                loan_id=loan_id,
                borrower_id=borrower_id,
                employer_id=borrower.get("employerId"),
                benefit_agreement_id=active_agreement_id,
            )

            # --- benefit-status cascade (guarded on the benefit's CURRENT
            #     status; an already-at/past-target benefit is an idempotent
            #     no-op) ------------------------------------------------------
            cascade = _apply_cascade(
                txn,
                client=client,
                ctx=ctx,
                seq=seq,
                new_status=new_status,
                loan_id=loan_id,
                loan=loan,
                agreement=agreement,
                now=now,
            )
            cascade_summary = cascade["summary"]
            cascade_followup = cascade.get("followup")
        else:
            # Reclaim: the cascade already committed on the original call; the
            # agreement's committed status determines both what the cascade DID
            # (for the response/audit) and which idempotent tail to re-drive.
            cascade_summary = _reclaim_cascade_summary(new_status, agreement)
            cascade_followup = _reclaim_cascade_followup(new_status, agreement)

        result = {
            "borrowerId": borrower_id,
            "employmentStatus": new_status,
            "previousStatus": previous_status,
            "employmentEndDate": (
                _iso_or_value(effective_date)
                if new_status == EmploymentStatus.TERMINATED.value
                else _iso_or_value(borrower.get("employmentEndDate"))
            ),
            "benefitAgreementId": active_agreement_id,
            "benefitCascade": cascade_summary,
            "correlationId": ctx.correlation_id,
        }

        # COMPLETION PROTOCOL (Decision A). Two cases:
        #  * A cascade WITH an async tail (terminate → cancel-future, resume →
        #    schedule-shift): keep the idempotency key PENDING across the commit →
        #    task boundary; the task wrapper runs the idempotent tail THEN completes
        #    the key (closing the crash gap where a completed key would replay past
        #    an un-run tail).
        #  * A cascade with NO tail (a LEAVE-suspend, a no-op cascade, or no active
        #    agreement): there is no task to complete the key, so complete it HERE,
        #    atomically with the transition — exactly like the sync suspend command.
        if cascade_followup:
            followup.update(cascade_followup)
        else:
            idempotency.complete(txn, ctx.idempotency_key, result, client=client)
        emitted.update({"loan_id": loan_id, "employer_id": borrower.get("employerId")})
        ran["done"] = True
        return result

    try:
        result = _run()
    except CommandError:
        raise
    except domain_errors.DomainError as exc:
        raise from_domain_error(exc) from exc

    # --- POST-COMMIT: nudge the read-model recompute (off the txn, §5.1) ------
    # An employment change mirrors to the loan (employmentStatus) and its cascade
    # may re-status the benefit — so nudge on EVERY real path, independent of
    # whether a bounded tail follows. Placed before the tail handoff so it runs
    # even in cloud mode (where the handoff raises 202). Only the real path
    # populates `emitted` (a replay/in-progress/reuse returns before it).
    if ran["done"] and emitted:
        _nudge_projections(
            ctx, "EMPLOYMENT_STATUS_CHANGED",
            loan_id=emitted.get("loan_id"), employer_id=emitted.get("employer_id"),
        )

    # --- follow-up handoff (AFTER the core txn commits) ----------------------
    # Hand the bounded cascade tail onto the async task via enqueue() (COMPLETION
    # PROTOCOL, Decision A): cancel-future-contributions on terminate,
    # shift-schedule on resume. The task runs the idempotent tail body THEN
    # completes the idempotency key; a crash before that leaves the key
    # reclaimable so a same-key retry re-drives the idempotent tail (zero-duration
    # shift / all-canceled cancel = no-op). Inline mode runs it synchronously
    # (-> 200 with the cascade result); cloud mode returns None (-> 202 +
    # Retry-After). Skipped on a replay (`ran` stays False) and on a no-tail
    # cascade (the key was already completed inside the core txn).
    if ran["done"] and followup:
        kind = followup.get("kind")
        # The idempotency key must store the COMMAND's response body (`result`
        # below — the employment change + benefitCascade summary the first caller
        # received), NOT the tail's side-effect summary (cancel-future's {canceled,
        # skipped_processing} / shift's {shiftMonths, installmentsShifted,...}), so a
        # same-key replay / cloud poll returns the identical body (specs/08 §8.2).
        # The tail adapter completes the key with this `commandResult`.
        if kind == "terminate":
            task, payload = "cancel-future-contributions", {
                "agreementId": followup["agreement_id"],
                "reason": _TERMINATE_REASON,
                "idempotencyKey": ctx.idempotency_key,
                "commandResult": result,
            }
        elif kind == "resume":
            task, payload = "shift-schedule", {
                "agreementId": followup["agreement_id"],
                "idempotencyKey": ctx.idempotency_key,
                "commandResult": result,
            }
        else:  # pragma: no cover - defensive; _apply_cascade sets only the above
            task = None

        if task is not None:
            from internal.enqueue import enqueue

            task_result = enqueue(task, payload, ctx=ctx)
            if task_result is None:
                raise OperationInProgress(
                    "employment status change cascade in progress",
                    retry_after=RETRY_AFTER_IN_PROGRESS,
                    state={"borrowerId": borrower_id, "employmentStatus": new_status},
                )
    return result


def _apply_cascade(
    txn: Any,
    *,
    client: Any,
    ctx: CommandContext,
    seq: _Seq,
    new_status: str,
    loan_id: Optional[str],
    loan: Optional[dict],
    agreement: Optional[dict],
    now: datetime,
) -> dict:
    """Apply the §10.4 benefit-status cascade inside the command transaction.

    Returns ``{"summary": <dict>, "followup": <dict|None>}``. ``summary`` records
    what happened (for the response); ``followup`` (when set) tells the caller to
    run a bounded inline task after commit. Every branch is a guarded no-op when
    the benefit is already at/past the target state (specs/06 §6.7).
    """
    if agreement is None:
        return {"summary": {"applied": False, "reason": "no active agreement"}, "followup": None}

    agreement_id = agreement["id"]
    current = agreement.get("status")

    if new_status == EmploymentStatus.LEAVE.value:
        return _cascade_suspend(
            txn, client=client, ctx=ctx, seq=seq, agreement=agreement,
            agreement_id=agreement_id, current=current, loan_id=loan_id, loan=loan,
            now=now,
        )
    if new_status == EmploymentStatus.TERMINATED.value:
        return _cascade_terminate(
            txn, client=client, ctx=ctx, seq=seq, agreement=agreement,
            agreement_id=agreement_id, current=current, loan_id=loan_id, loan=loan,
        )
    if new_status == EmploymentStatus.ACTIVE.value:
        return _cascade_resume(
            txn, client=client, ctx=ctx, seq=seq, agreement=agreement,
            agreement_id=agreement_id, current=current, loan_id=loan_id, loan=loan,
            now=now,
        )
    return {"summary": {"applied": False, "reason": "no cascade for status"}, "followup": None}


def _reclaim_cascade_summary(
    new_status: str, agreement: Optional[dict]
) -> dict:
    """Reconstruct the benefit-cascade summary on a reclaim from committed state.

    The original ``_apply_cascade`` summary was never persisted (the key is kept
    PENDING across the commit->tail boundary), so on a same-key reclaim we rebuild
    it from the agreement's *committed* status — which reflects what the cascade
    actually did — rather than reporting a hardcoded no-op that would make the
    API/audit trail lie about a benefit that really was suspended/terminated/resumed.
    """
    if agreement is None:
        return {"applied": False, "reason": "no active benefit agreement"}
    current = agreement.get("status")
    if (
        new_status == EmploymentStatus.TERMINATED.value
        and current == BenefitStatus.TERMINATED.value
    ):
        return {"applied": True, "action": "TERMINATED"}
    if (
        new_status == EmploymentStatus.LEAVE.value
        and current == BenefitStatus.SUSPENDED.value
    ):
        return {
            "applied": True,
            "action": "SUSPENDED",
            "suspendedReason": agreement.get("suspendedReason"),
        }
    if (
        new_status == EmploymentStatus.ACTIVE.value
        and current == BenefitStatus.ACTIVE.value
    ):
        return {"applied": True, "action": "RESUMED"}
    return {"applied": False, "reason": "cascade was a no-op (benefit already at/past target)"}


def _reclaim_cascade_followup(
    new_status: str, agreement: Optional[dict]
) -> Optional[dict]:
    """Re-derive the post-commit cascade tail on a reclaim (specs/08 §8.3).

    On a same-key reclaim the borrower change + cascade already committed (one
    txn), so ``_apply_cascade`` — and its ``followup`` — never re-run. The
    agreement's *committed* status tells us what the cascade did and hence which
    idempotent tail to re-drive: a TERMINATED benefit needs cancel-future re-run;
    an ACTIVE benefit (a return-from-leave resume) needs the schedule shift
    re-run; every other committed state had a no-op cascade and thus no tail.
    Returns a followup dict (matching ``_apply_cascade``'s shape) or ``None``.
    """
    if agreement is None:
        return None
    current = agreement.get("status")
    if (
        new_status == EmploymentStatus.TERMINATED.value
        and current == BenefitStatus.TERMINATED.value
    ):
        return {"kind": "terminate", "agreement_id": agreement["id"]}
    if (
        new_status == EmploymentStatus.ACTIVE.value
        and current == BenefitStatus.ACTIVE.value
    ):
        # suspendedAt was cleared when the benefit resumed; the shift is driven by
        # the agreement's persisted scheduleShiftMonths, so a None here only omits
        # the SCHEDULE_SHIFTED event's window metadata on a re-drive.
        return {
            "kind": "resume",
            "agreement_id": agreement["id"],
            "suspended_from": agreement.get("suspendedAt"),
        }
    return None


def _cascade_suspend(
    txn: Any, *, client: Any, ctx: CommandContext, seq: _Seq, agreement: dict,
    agreement_id: str, current: Optional[str], loan_id: Optional[str],
    loan: Optional[dict], now: datetime,
) -> dict:
    """LEAVE → suspend an ACTIVE benefit (reason LEAVE). Else no-op (§10.4)."""
    if current != BenefitStatus.ACTIVE.value:
        # Already SUSPENDED (manual or leave), or terminal/pending — no-op; the
        # existing suspension + its reason stand (specs/10 §10.4, specs/06 §6.7).
        return {"summary": {"applied": False, "reason": f"benefit is {current}"}, "followup": None}

    state_machines.assert_transition("benefit", current, BenefitStatus.SUSPENDED.value)
    agreement_update = {
        "status": BenefitStatus.SUSPENDED.value,
        "acceptingPayments": False,
        "suspendedReason": _SUSPEND_REASON_LEAVE,
        "suspendedAt": now,
    }
    stamp_update(agreement_update, ctx.actor_id)
    txn.update(agreements.ref(client, agreement_id), agreement_update)
    _sync_loan_status(txn, client, ctx, loan_id, loan, BenefitStatus.SUSPENDED.value)

    servicing_events.append(
        txn,
        event_type="BENEFIT_SUSPENDED",
        entity_type="BENEFIT_AGREEMENT",
        entity_id=agreement_id,
        actor_id=ctx.actor_id,
        actor_role=ctx.actor_role,
        actor_name=ctx.actor_name,
        correlation_id=ctx.correlation_id,
        sequence=seq(),
        metadata={
            "previousStatus": current,
            "newStatus": BenefitStatus.SUSPENDED.value,
            "suspendedReason": _SUSPEND_REASON_LEAVE,
            "suspendedAt": now.isoformat(),
            "cascadedFrom": "EMPLOYMENT_LEAVE",
        },
        loan_id=loan_id,
        borrower_id=agreement.get("borrowerId"),
        employer_id=agreement.get("employerId"),
        benefit_agreement_id=agreement_id,
    )
    return {
        "summary": {"applied": True, "action": "SUSPENDED", "suspendedReason": _SUSPEND_REASON_LEAVE},
        "followup": None,
    }


def _cascade_terminate(
    txn: Any, *, client: Any, ctx: CommandContext, seq: _Seq, agreement: dict,
    agreement_id: str, current: Optional[str], loan_id: Optional[str],
    loan: Optional[dict],
) -> dict:
    """TERMINATED → terminate an ACTIVE/SUSPENDED/ACTIVATING benefit. Else no-op."""
    if current not in _TERMINATABLE:
        # Already TERMINATED/COMPLETED (or PENDING) — no-op (specs/06 §6.7).
        return {"summary": {"applied": False, "reason": f"benefit is {current}"}, "followup": None}

    state_machines.assert_transition("benefit", current, BenefitStatus.TERMINATED.value)
    agreement_update = {
        "status": BenefitStatus.TERMINATED.value,
        "acceptingPayments": False,
    }
    stamp_update(agreement_update, ctx.actor_id)
    txn.update(agreements.ref(client, agreement_id), agreement_update)
    _sync_loan_status(txn, client, ctx, loan_id, loan, BenefitStatus.TERMINATED.value)

    servicing_events.append(
        txn,
        event_type="BENEFIT_TERMINATED",
        entity_type="BENEFIT_AGREEMENT",
        entity_id=agreement_id,
        actor_id=ctx.actor_id,
        actor_role=ctx.actor_role,
        actor_name=ctx.actor_name,
        correlation_id=ctx.correlation_id,
        sequence=seq(),
        metadata={
            "previousStatus": current,
            "newStatus": BenefitStatus.TERMINATED.value,
            "cascadedFrom": "EMPLOYMENT_TERMINATED",
        },
        loan_id=loan_id,
        borrower_id=agreement.get("borrowerId"),
        employer_id=agreement.get("employerId"),
        benefit_agreement_id=agreement_id,
    )
    return {
        "summary": {"applied": True, "action": "TERMINATED"},
        "followup": {"kind": "terminate", "agreement_id": agreement_id},
    }


def _cascade_resume(
    txn: Any, *, client: Any, ctx: CommandContext, seq: _Seq, agreement: dict,
    agreement_id: str, current: Optional[str], loan_id: Optional[str],
    loan: Optional[dict], now: datetime,
) -> dict:
    """Return-from-LEAVE → resume a SUSPENDED benefit ONLY when reason == LEAVE.

    A manually suspended benefit (``suspendedReason != LEAVE``) stays suspended —
    the manager resumes it explicitly (specs/10 §10.4). An already-ACTIVE benefit
    is a no-op.
    """
    if current != BenefitStatus.SUSPENDED.value:
        return {"summary": {"applied": False, "reason": f"benefit is {current}"}, "followup": None}
    if agreement.get("suspendedReason") != _SUSPEND_REASON_LEAVE:
        # Manually suspended — the manager's suspension stands (specs/10 §10.4).
        return {
            "summary": {"applied": False, "reason": "benefit suspended MANUAL, not auto-resumed"},
            "followup": None,
        }

    state_machines.assert_transition("benefit", current, BenefitStatus.ACTIVE.value)
    suspended_from = agreement.get("suspendedAt")
    # Cumulative schedule-shift witness (specs/07 §7.8): mirror resume_benefit —
    # accumulate THIS leave's whole-month duration onto any prior shift so the
    # post-commit shift task (which now anchors to the TOTAL scheduleShiftMonths)
    # shifts cumulatively. Required because shift_schedule no longer recomputes the
    # duration from suspended_from/resumed_at. `scheduleShiftMonths` is a new
    # agreement field, absent from the frozen core/schema.py (Firestore schemaless).
    from benefits.shift import _months_between

    this_shift_months = _months_between(suspended_from, now)
    total_shift_months = (
        int(agreement.get("scheduleShiftMonths", 0) or 0) + this_shift_months
    )
    agreement_update = {
        "status": BenefitStatus.ACTIVE.value,
        "acceptingPayments": True,
        "suspendedReason": None,
        "suspendedAt": None,
        "scheduleShiftMonths": total_shift_months,
    }
    stamp_update(agreement_update, ctx.actor_id)
    txn.update(agreements.ref(client, agreement_id), agreement_update)
    _sync_loan_status(txn, client, ctx, loan_id, loan, BenefitStatus.ACTIVE.value)

    servicing_events.append(
        txn,
        event_type="BENEFIT_RESUMED",
        entity_type="BENEFIT_AGREEMENT",
        entity_id=agreement_id,
        actor_id=ctx.actor_id,
        actor_role=ctx.actor_role,
        actor_name=ctx.actor_name,
        correlation_id=ctx.correlation_id,
        sequence=seq(),
        metadata={
            "previousStatus": current,
            "newStatus": BenefitStatus.ACTIVE.value,
            "suspendedFrom": _iso_or_value(suspended_from),
            "cascadedFrom": "EMPLOYMENT_RETURN_FROM_LEAVE",
        },
        loan_id=loan_id,
        borrower_id=agreement.get("borrowerId"),
        employer_id=agreement.get("employerId"),
        benefit_agreement_id=agreement_id,
    )
    return {
        "summary": {"applied": True, "action": "RESUMED"},
        "followup": {"kind": "resume", "agreement_id": agreement_id, "suspended_from": suspended_from},
    }


def _sync_loan_status(
    txn: Any, client: Any, ctx: CommandContext, loan_id: Optional[str],
    loan: Optional[dict], benefit_status: str,
) -> None:
    """Sync ``loan.benefitStatus`` in the same txn (specs/04 §4.5)."""
    if loan_id and loan is not None:
        loan_update = {"benefitStatus": benefit_status}
        stamp_update(loan_update, ctx.actor_id)
        txn.update(loans.ref(client, loan_id), loan_update)


def _iso_or_value(value: Any) -> Any:
    """ISO-format a datetime; pass strings/None/date-likes through unchanged."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value
