"""projections.fanout — event→key fanout + the post-commit projection nudge.

Read models are updated **off** every payment/command transaction (specs/05 §5.1
hot-doc rule): a summary write inside a payment transaction would put the hottest
document (``portfolioSummaries/current``) into the transaction's read set and let
its contention abort/retry the *authoritative* payment. So this module owns two
things, and neither ever runs inside a transaction:

* :func:`affected_keys` — a **pure** mapper from a servicing-event-shaped dict
  (its ``eventType`` + ``loanId``/``employerId`` + ``metadata.periodLabel``) to
  the set of read-model keys that event could have made stale. It reads no
  Firestore and writes nothing — just table lookup + id/period materialisation.
* :func:`enqueue_projection_update` — called by a command **after** its
  transaction commits to schedule the ``update-projection`` task, which
  recomputes each named key from source (specs/05 §5.2 mechanism 2). Recompute
  (never a folded delta) is what makes redelivery idempotent and coalescing
  trivial: N nudges for the same key all recompute the same value.

**Coalescing.** A burst of payments all touch the same ``portfolio_current`` (and
per-employer/per-period) key. We enqueue **one Cloud Task per key** with a
deterministic, time-bucketed name so duplicate nudges for the same key within the
bucket are de-duplicated by Cloud Tasks (specs/05 §5.2) — the same
name+time-window pattern the reconcile-stuck-payments job uses. The scheduled
``rebuild-summaries`` job is the backstop for anything a collapsed/failed nudge
misses, so a projection nudge is strictly **best-effort**: it must never fail
(or roll back) the authoritative command that already committed. Hence every
enqueue is wrapped so an error is logged and swallowed.

The ``google.cloud``/settings imports live behind :mod:`internal.enqueue`, which
is imported lazily, so this module ``py_compile``s in the offline sandbox.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger("bsw.projections")

# --------------------------------------------------------------------------- #
# The shared projection KEY (JSON-serialisable; flows through the task payload).
#   {"kind": <one of KIND_*>, "id": <employerId|loanId|None>, "period": <YYYY-MM|None>}
# `id`/`period` are populated per kind (see _KEY_REQUIREMENTS); the recompute
# engine + gateways (agent A1) consume these verbatim. `period` carries the
# contribution's periodLabel ("YYYY-MM", the source docs' + summary docId format),
# NOT a wall-clock month (specs/05 §5.3 period attribution).
# --------------------------------------------------------------------------- #
KIND_PORTFOLIO_CURRENT = "portfolio_current"
KIND_PORTFOLIO_PERIOD = "portfolio_period"
KIND_EMPLOYER = "employer"
KIND_EMPLOYER_PERIOD = "employer_period"
KIND_LOAN_WORKBENCH = "loan_workbench"

Key = dict  # {"kind": str, "id": Optional[str], "period": Optional[str]}

UPDATE_PROJECTION_TASK = "update-projection"

# Coalescing window (seconds). A tight burst collapses onto one task name; a later
# nudge in a fresh window mints a new name so a genuinely-changed key is not
# permanently suppressed by Cloud Tasks' completed-name reuse gap. Mirrors the
# reconcile-stuck-payments job's window bucketing.
COALESCE_WINDOW_SECONDS = 10


# --------------------------------------------------------------------------- #
# eventType → which read-model kinds it can make stale (specs/05 §5.3-§5.5).
# A kind is only materialised into a concrete Key when its required id/period is
# present on the event (see _KEY_REQUIREMENTS), so e.g. an EMPLOYER-scoped
# exception (no loanId) yields no loan_workbench key, and a benefit event with no
# periodLabel yields no period buckets.
#
# portfolio_current  — benefitStatusCounts / contributionStatusCounts /
#                      openException* / remainingEmployerCommitment (§5.3)
# portfolio_period   — postedCents / failedContributionCount for periodLabel (§5.3)
# employer           — activeBenefits / *CommitmentCents / openExceptionCount (§5.4)
# employer_period    — postedCents / failedCount for periodLabel (§5.4)
# loan_workbench     — the per-loan live mirror (§5.5)
# --------------------------------------------------------------------------- #
_FULL_PAYMENT = (
    KIND_PORTFOLIO_CURRENT,
    KIND_PORTFOLIO_PERIOD,
    KIND_EMPLOYER,
    KIND_EMPLOYER_PERIOD,
    KIND_LOAN_WORKBENCH,
)
_STATUS_AND_LOAN = (KIND_PORTFOLIO_CURRENT, KIND_LOAN_WORKBENCH)
_ROLLUP_AND_LOAN = (KIND_PORTFOLIO_CURRENT, KIND_EMPLOYER, KIND_LOAN_WORKBENCH)

_KINDS_BY_EVENT: dict[str, tuple[str, ...]] = {
    # -- money movement: full period-flow fanout ---------------------------- #
    # A posting/failure moves postedCents/failedCount (period + employer_period),
    # amountPaid/remaining (employer + portfolio_current) and the loan mirror.
    "PAYMENT_POSTED": _FULL_PAYMENT,
    "PAYMENT_FAILED": _FULL_PAYMENT,
    # Reconcile can end POSTED / FAILED / reverted, so fan out the full set.
    "PAYMENT_RECONCILED": _FULL_PAYMENT,
    # -- contribution-status-only transitions (+ loan mirror) --------------- #
    "PAYMENT_PROCESSING": _STATUS_AND_LOAN,
    "PAYMENT_RETRY_SCHEDULED": _STATUS_AND_LOAN,
    "PAYMENT_CANCELED": _STATUS_AND_LOAN,
    "LOAN_BALANCE_UPDATED": (KIND_LOAN_WORKBENCH,),
    # NB — scheduledCents deferral (specs/05 §5.3; LOW). A cancel-future or an
    # activation restates the WHOLE remaining schedule at once — many distinct
    # periodLabels — and the servicing event carries none of them, so these events
    # cannot materialise the portfolio_period / employer_period keys whose
    # ``scheduledCents`` they stale. Per-event period fanout is therefore
    # intentionally omitted for them: they map only to _ROLLUP_AND_LOAN (refreshing
    # benefitStatusCounts / contributionStatusCounts / employer rollups / the loan
    # mirror), NEVER scheduledCents. The scheduled ``rebuild-summaries`` (*/15) owns
    # scheduledCents refresh across every spanned period. (A single posting/failure
    # DOES carry its own periodLabel, so postedCents / failedCount stay event-driven
    # via _FULL_PAYMENT — only the multi-period scheduledCents defers to rebuild.)
    "FUTURE_CONTRIBUTIONS_CANCELED": _ROLLUP_AND_LOAN,
    "SCHEDULE_SHIFTED": (KIND_LOAN_WORKBENCH,),
    # -- benefit lifecycle: benefitStatusCounts + employer rollups + loan --- #
    "BENEFIT_ACTIVATION_STARTED": _ROLLUP_AND_LOAN,
    # BENEFIT_ACTIVATED also spans many periods' scheduledCents — see the NB above.
    "BENEFIT_ACTIVATED": _ROLLUP_AND_LOAN,
    "BENEFIT_SUSPENDED": _ROLLUP_AND_LOAN,
    "BENEFIT_RESUMED": _ROLLUP_AND_LOAN,
    "BENEFIT_TERMINATED": _ROLLUP_AND_LOAN,
    "BENEFIT_COMPLETED": _ROLLUP_AND_LOAN,
    # -- employment: loan mirror + (cascade) benefit/employer rollups ------- #
    "EMPLOYMENT_STATUS_CHANGED": _ROLLUP_AND_LOAN,
    # -- exceptions: openException* on current + employer (+ loan if scoped) - #
    "EXCEPTION_CREATED": _ROLLUP_AND_LOAN,
    "EXCEPTION_RESOLVED": _ROLLUP_AND_LOAN,
    "EXCEPTION_DISMISSED": _ROLLUP_AND_LOAN,
    "EMPLOYER_STATUS_CHANGED": (KIND_PORTFOLIO_CURRENT, KIND_EMPLOYER),
    # -- no read-model impact ---------------------------------------------- #
    "MANUAL_NOTE_ADDED": (),
    "USER_ROLE_CHANGED": (),
}

# Per kind: does materialising a concrete Key require an id and/or a period?
_KEY_REQUIREMENTS: dict[str, tuple[bool, bool]] = {
    # kind: (needs_id, needs_period)
    KIND_PORTFOLIO_CURRENT: (False, False),
    KIND_PORTFOLIO_PERIOD: (False, True),
    KIND_EMPLOYER: (True, False),
    KIND_EMPLOYER_PERIOD: (True, True),
    KIND_LOAN_WORKBENCH: (True, False),
}


def _make_key(kind: str, *, employer_id: Optional[str], loan_id: Optional[str],
              period: Optional[str]) -> Optional[Key]:
    """Materialise a concrete Key for ``kind`` if its required id/period exist.

    ``portfolio_current``/``portfolio_period`` are portfolio-wide (``id`` None);
    ``employer``/``employer_period`` carry the ``employerId``; ``loan_workbench``
    carries the ``loanId``. Returns ``None`` when a required piece is missing (so a
    partially-scoped event simply omits the keys it cannot address).
    """
    needs_id, needs_period = _KEY_REQUIREMENTS[kind]
    ident: Optional[str] = None
    if needs_id:
        ident = employer_id if kind in (KIND_EMPLOYER, KIND_EMPLOYER_PERIOD) else loan_id
        if not ident:
            return None
    if needs_period and not period:
        return None
    return {"kind": kind, "id": ident, "period": period if needs_period else None}


def affected_keys(event: dict) -> list[Key]:
    """Map a servicing-event-shaped dict to the read-model keys it may have staled.

    Pure: reads ``event["eventType"]``, ``event.get("loanId")``,
    ``event.get("employerId")`` and ``event.get("metadata", {}).get("periodLabel")``
    — the shape :mod:`servicing.events` writes — and returns a de-duplicated,
    order-stable list of :data:`Key` dicts. No Firestore access, no writes. An
    unknown/absent eventType maps to no keys.
    """
    event_type = event.get("eventType")
    kinds = _KINDS_BY_EVENT.get(event_type, ())
    if not kinds:
        return []
    loan_id = event.get("loanId")
    employer_id = event.get("employerId")
    period = (event.get("metadata") or {}).get("periodLabel")

    keys: list[Key] = []
    seen: set[tuple] = set()
    for kind in kinds:
        key = _make_key(
            kind, employer_id=employer_id, loan_id=loan_id, period=period
        )
        if key is None:
            continue
        dedup = (key["kind"], key["id"], key["period"])
        if dedup in seen:
            continue
        seen.add(dedup)
        keys.append(key)
    return keys


def _coalesce_name(key: Key, window: int) -> str:
    """Deterministic, Cloud-Tasks-safe task name for coalescing one key per window.

    Same-key nudges within ``window`` collapse onto this one name (deduped by
    Cloud Tasks); a new window mints a fresh name so a later change is not
    permanently suppressed. ``id``/``period`` are ``[A-Za-z0-9_-]`` (entity ids /
    "YYYY-MM"), so the joined name is name-safe.
    """
    ident = key.get("id") or "none"
    period = key.get("period") or "none"
    return f"proj-{key['kind']}-{ident}-{period}-{window}"


def enqueue_projection_update(keys: list[Key], *, ctx) -> int:
    """Schedule ``update-projection`` recompute(s) for ``keys`` — POST-COMMIT ONLY.

    Call this from a command **after** its transaction commits (never inside one —
    specs/05 §5.1). One Cloud Task is enqueued **per unique key** with a
    coalescing name, so the hot ``portfolio_current`` key collapses across a burst
    (the task recomputes from source, so a collapsed/redelivered nudge is
    harmless). Returns the number of tasks enqueued.

    Best-effort: the authoritative write already committed, and the scheduled
    ``rebuild-summaries`` job reconciles any drift, so a failed enqueue is logged
    and swallowed — a projection nudge must never fail the command.
    """
    if not keys:
        return 0

    from internal.enqueue import enqueue  # lazy: pulls settings/google.cloud

    window = int(time.time() // COALESCE_WINDOW_SECONDS)
    enqueued = 0
    seen: set[tuple] = set()
    for key in keys:
        dedup = (key.get("kind"), key.get("id"), key.get("period"))
        if dedup in seen:
            continue
        seen.add(dedup)
        try:
            enqueue(
                UPDATE_PROJECTION_TASK,
                {"keys": [key]},
                ctx=ctx,
                name=_coalesce_name(key, window),
            )
            enqueued += 1
        except Exception:  # noqa: BLE001 — best-effort; rebuild job is the backstop
            logger.warning(
                "projection nudge failed key=%s (rebuild will reconcile)",
                key, exc_info=True,
            )
    return enqueued


def enqueue_for_event(event: dict, *, ctx) -> int:
    """Convenience: :func:`enqueue_projection_update` for one event's affected keys.

    POST-COMMIT ONLY. Lets a command fan out directly from an event-shaped dict it
    already has, without threading :func:`affected_keys` at the call site.
    """
    return enqueue_projection_update(affected_keys(event), ctx=ctx)
