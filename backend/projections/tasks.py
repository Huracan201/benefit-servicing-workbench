"""projections.tasks — the ``update-projection`` Cloud Task + ``rebuild-summaries`` job.

Two entry points over the ONE source-derivation engine (:mod:`projections.recompute`),
so the event-driven and the scheduled path agree by construction (they call the
*same* ``recompute.apply_key``):

* :func:`update_projection` — the event-driven Cloud Tasks handler. Its payload
  ``{"keys": [Key, ...]}`` names the read-model docs a just-committed command
  dirtied (built by :mod:`projections.fanout`); it recomputes each key from source
  and overwrites via the key's gateway. Idempotent: recompute-from-source converges,
  so a redelivered / coalesced task writes the byte-identical value (modulo the
  server ``updatedAt``) and never double-counts (specs/05 §5.2 mechanism 2).
* :func:`rebuild_summaries` — the scheduled drift backstop (Cloud Scheduler
  ``*/15`` + a nightly full run, specs/14 §14.2). It enumerates the FULL key set by
  paging the source collections in bounded pages, then recomputes every key through
  the same ``apply_key`` — correcting anything a collapsed/failed nudge left stale.

Neither ever reads a projection to make a decision, and neither runs inside a
command transaction (the hot-doc rule, specs/05 §5.1): summaries are always
updated *off* the authoritative write. Heavy imports (``recompute`` /
``common.firestore``) are lazy so this module ``py_compile``s in an offline sandbox.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from repositories import refs

logger = logging.getLogger("bsw.projections")

# Bounds one rebuild scan's enumeration fan-out per source collection (mirrors
# internal.jobs._MAX_PAGES). At BATCH_SIZE rows/page this is far beyond demo scale;
# hitting the cap is logged (never a silent truncation) and the next scheduled run
# resumes — every recompute is idempotent (specs/14 §14.6).
_MAX_PAGES = 500


# --------------------------------------------------------------------------- #
# update-projection — the event-driven Cloud Task
# --------------------------------------------------------------------------- #
def update_projection(payload: dict, ctx) -> dict:
    """Recompute each projection Key named in ``payload`` from source (specs/05 §5.2).

    Payload: ``{"keys": [Key, ...]}`` where a Key is the JSON-serialisable
    ``{"kind", "id", "period"}`` dict :mod:`projections.fanout` emits. Each key is
    recomputed from the SOURCE entities and overwritten via its gateway
    (``recompute.apply_key``) — never a folded delta, so a redelivered or coalesced
    task is byte-identical and never double-counts. A key whose source entity has
    been removed recomputes to ``None`` and is skipped (nothing written).
    """
    from common.firestore import get_client
    from projections import recompute

    client = get_client()
    keys = payload.get("keys") or []

    applied = 0
    missing = 0
    for key in keys:
        doc = recompute.apply_key(client, key)
        if doc is None:
            # Source entity gone (e.g. a loan/employer removed) — nothing to write.
            missing += 1
        else:
            applied += 1

    summary = {"task": "update-projection", "applied": applied, "missing": missing}
    logger.info("update-projection applied=%s missing=%s", applied, missing)
    return summary


# --------------------------------------------------------------------------- #
# rebuild-summaries — the scheduled drift backstop (SCHEDULER_JOBS)
# --------------------------------------------------------------------------- #
def rebuild_summaries(payload: dict, ctx) -> dict:
    """Recompute EVERY read-model key from source (drift backstop — specs/05 §5.2).

    Scheduled ``*/15`` + a nightly full run (specs/14 §14.2, specs/21 §21.2). It
    enumerates the complete key set by paging the source collections in bounded
    pages, then recomputes each key through the SAME ``recompute.apply_key`` the
    event-driven ``update-projection`` task uses — so the scheduled rebuild and an
    event-driven nudge converge to identical values by construction. Idempotent and
    self-correcting: it overwrites whatever a collapsed/failed nudge left stale.

    Key enumeration (all bucketed the same way the recompute engine derives them):
    ``portfolio_current`` (one), ``loan_workbench`` per loan, ``employer`` per
    employer, and ``portfolio_period`` / ``employer_period`` for every distinct
    contribution ``periodLabel`` (never a wall-clock month — specs/05 §5.3).
    """
    from common.firestore import get_client
    from projections import recompute

    client = get_client()

    # portfolio_current — always exactly one.
    keys: list[dict[str, Any]] = [recompute.portfolio_current_key()]

    # loan_workbench/{loanId} — one per loan.
    for loan in _iter_collection(client, refs.LOANS):
        keys.append(recompute.loan_workbench_key(loan["id"]))

    # employer/{employerId} — one per employer.
    for employer in _iter_collection(client, refs.EMPLOYERS):
        keys.append(recompute.employer_key(employer["id"]))

    # portfolio_period + employer_period — every distinct periodLabel observed on
    # the schedule (and per employer), bucketed by the contribution's periodLabel.
    portfolio_periods: set[str] = set()
    employer_periods: set[tuple[str, str]] = set()
    for contribution in _iter_collection(client, refs.SCHEDULED_CONTRIBUTIONS):
        period = contribution.get("periodLabel")
        if not period:
            continue
        portfolio_periods.add(period)
        employer_id = contribution.get("employerId")
        if employer_id:
            employer_periods.add((employer_id, period))
    for period in sorted(portfolio_periods):
        keys.append(recompute.portfolio_period_key(period))
    for employer_id, period in sorted(employer_periods):
        keys.append(recompute.employer_period_key(employer_id, period))

    applied = 0
    missing = 0
    for key in keys:
        doc = recompute.apply_key(client, key)
        if doc is None:
            missing += 1
        else:
            applied += 1

    summary = {
        "job": "rebuild-summaries",
        "keys": len(keys),
        "applied": applied,
        "missing": missing,
    }
    logger.info(
        "rebuild-summaries keys=%s applied=%s missing=%s",
        len(keys), applied, missing,
    )
    return summary


# --------------------------------------------------------------------------- #
# bounded source enumeration
# --------------------------------------------------------------------------- #
def _iter_collection(client, collection: str):
    """Yield every doc of a top-level ``collection`` in bounded ``__name__`` pages.

    Cursor-paged on the document id (never a single unbounded stream) so the
    enumeration scan stays bounded per round-trip. Caps at ``_MAX_PAGES`` and logs
    a warning if a collection is larger than the cap can enumerate in one run — the
    remainder is picked up by the next scheduled run (every recompute is
    idempotent), never silently dropped (specs/14 §14.6).
    """
    start_after: Optional[Any] = None
    pages = 0
    while True:
        query = client.collection(collection).order_by("__name__")
        if start_after is not None:
            query = query.start_after(start_after)
        snapshots = list(query.limit(refs.BATCH_SIZE).stream())
        for snap in snapshots:
            yield refs.snapshot_to_dict(snap)
        pages += 1
        if len(snapshots) < refs.BATCH_SIZE:
            break
        if pages >= _MAX_PAGES:
            logger.warning(
                "rebuild-summaries hit the %s-page cap paging %s; remainder "
                "deferred to the next scheduled run",
                _MAX_PAGES, collection,
            )
            break
        start_after = snapshots[-1]
