"""internal.jobs — Cloud Scheduler job bodies (specs/14 §14.2).

``fn(payload: dict, ctx) -> dict`` callables registered in :mod:`internal.enqueue`
and invoked by ``/internal/jobs/<name>`` (or ``manage.py run_job <name>``). Each
job is a **fan-out**: it scans a paginated source query and enqueues one per-item
Cloud Task, so the heavy, retryable work is the task, not the scan. Jobs are
idempotent — re-running enqueues the same (idempotent) tasks, and the tasks
themselves de-dup on deterministic keys.

* :func:`enqueue_due_contributions` — pages ``due()`` for ``SCHEDULED`` and
  ``RETRY_PENDING`` with ``scheduledDate ≤ now``; re-checks each candidate's
  agreement ``acceptingPayments`` (a cross-document predicate, cached per scan and
  re-checked authoritatively in the process Phase-1 transaction); enqueues one
  ``process-contribution`` per eligible item. Bounded pages: if the per-run page
  cap is hit it **logs** that work was deferred — never a silent cap (specs/14 §14.6).
* :func:`reconcile_stuck_payments` — pages the two §9.4 scans (contributions stuck
  ``PROCESSING`` past ``STUCK_THRESHOLD`` + the collection-group ``STARTED``-attempt
  safety net), de-dupes by ``contributionId``, and enqueues one
  ``reconcile-contribution`` per item with a deterministic Cloud Tasks name so two
  overlapping runs never double-enqueue the same contribution.
* :func:`reap_expired_leases_job` — drives the lease reaper (specs/08 §8.3).
* :func:`reset_demo` — re-runs the deterministic seed so shared demo credentials
  always find a clean portfolio (specs/18 §18.1, specs/14 §14.2).
* :func:`expire_idempotency_keys` — a thin daily TTL-lag *metric emitter*; it
  DELETES NOTHING (Firestore TTL performs the actual deletion — specs/21 §21.1).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("bsw.internal")

# specs/21 §21.1: a contribution left PROCESSING longer than this began its
# two-phase payment but never finalized — the sweeper recovers it.
STUCK_THRESHOLD_SECONDS = 600  # 10 minutes

# Bounds a single scan's fan-out. At BATCH_SIZE=200 rows/page this is far beyond
# demo scale; the cron re-fires for any remainder (which is then logged, not
# silently dropped — specs/14 §14.6).
_MAX_PAGES = 500


# --------------------------------------------------------------------------- #
# enqueue-due-contributions
# --------------------------------------------------------------------------- #
def enqueue_due_contributions(payload: dict, ctx) -> dict:
    """Enqueue ``process-contribution`` for every eligible due contribution."""
    from datetime import datetime, timezone

    from common.enums import ContributionStatus
    from common.firestore import get_client
    from internal.enqueue import enqueue
    from repositories import agreements, contributions

    client = get_client()
    now = datetime.now(timezone.utc)
    statuses = (
        str(ContributionStatus.SCHEDULED),
        str(ContributionStatus.RETRY_PENDING),
    )

    accepting_cache: dict[str, bool] = {}

    def _accepting(agreement_id: Optional[str]) -> bool:
        if not agreement_id:
            return False
        if agreement_id not in accepting_cache:
            agreement = agreements.get(client, agreement_id)
            accepting_cache[agreement_id] = bool(
                agreement and agreement.get("acceptingPayments")
            )
        return accepting_cache[agreement_id]

    enqueued = 0
    skipped = 0
    capped = False

    for status in statuses:
        cursor: Optional[Any] = None
        pages = 0
        while True:
            page, cursor = contributions.due(
                client, now, status=status, start_after=cursor
            )
            for contribution in page:
                agreement_id = contribution.get("benefitAgreementId")
                # Pre-filter on acceptingPayments (Phase 1 re-checks authoritatively);
                # a suspended/terminated agreement's due rows are skipped here.
                if _accepting(agreement_id):
                    enqueue(
                        "process-contribution",
                        {"contributionId": contribution["id"]},
                        ctx=ctx,
                    )
                    enqueued += 1
                else:
                    skipped += 1
            pages += 1
            if cursor is None:
                break
            if pages >= _MAX_PAGES:
                capped = True
                break

    if capped:
        # A full page cap was reached with more rows behind the cursor — never
        # report "everything processed" (specs/14 §14.6). The next scheduled run
        # picks up the remainder (every task enqueued is idempotent).
        logger.warning(
            "enqueue-due-contributions hit the %s-page cap; remaining due "
            "contributions deferred to the next scheduled run",
            _MAX_PAGES,
        )

    summary = {
        "job": "enqueue-due-contributions",
        "enqueued": enqueued,
        "skipped": skipped,
        "deferred": capped,
    }
    logger.info(
        "enqueue-due-contributions enqueued=%s skipped=%s deferred=%s",
        enqueued, skipped, capped,
    )
    return summary


# --------------------------------------------------------------------------- #
# reconcile-stuck-payments
# --------------------------------------------------------------------------- #
def reconcile_stuck_payments(payload: dict, ctx) -> dict:
    """Enqueue ``reconcile-contribution`` for every stuck/stale in-flight payment."""
    from datetime import datetime, timedelta, timezone

    from common.firestore import get_client
    from internal.enqueue import enqueue
    from repositories import contributions

    client = get_client()
    now = datetime.now(timezone.utc)
    older_than = now - timedelta(seconds=STUCK_THRESHOLD_SECONDS)
    # De-dup window bucket: two overlapping runs (the job fires every 10 min) that
    # both scan the same still-stuck contribution enqueue the SAME Cloud Tasks
    # name and are de-duplicated; the next window uses a new bucket so a genuinely
    # still-stuck contribution is re-reconciled.
    window = int(now.timestamp() // STUCK_THRESHOLD_SECONDS)

    seen: set[str] = set()

    def _enqueue_reconcile(contribution_id: Optional[str]) -> None:
        if not contribution_id or contribution_id in seen:
            return
        seen.add(contribution_id)
        enqueue(
            "reconcile-contribution",
            {"contributionId": contribution_id},
            ctx=ctx,
            name=f"reconcile-{contribution_id}-{window}",
        )

    # (a) contributions stuck PROCESSING past STUCK_THRESHOLD (specs/09 §9.4 scan a).
    cursor: Optional[Any] = None
    pages = 0
    while True:
        page, cursor = contributions.stuck_processing(
            client, older_than=older_than, start_after=cursor
        )
        for contribution in page:
            _enqueue_reconcile(contribution.get("id"))
        pages += 1
        if cursor is None or pages >= _MAX_PAGES:
            break

    # (b) collection-group STARTED-attempt safety net (specs/09 §9.4 scan b) — a
    # stale attempt whose contribution already moved off PROCESSING.
    cursor = None
    pages = 0
    while True:
        page, cursor = contributions.stale_started_attempts(
            client, older_than=older_than, start_after=cursor
        )
        for attempt in page:
            _enqueue_reconcile(attempt.get("contributionId"))
        pages += 1
        if cursor is None or pages >= _MAX_PAGES:
            break

    summary = {"job": "reconcile-stuck-payments", "enqueued": len(seen)}
    logger.info("reconcile-stuck-payments enqueued=%s", len(seen))
    return summary


# --------------------------------------------------------------------------- #
# reap-expired-leases
# --------------------------------------------------------------------------- #
def reap_expired_leases_job(payload: dict, ctx) -> dict:
    """Reclaim abandoned ``PENDING`` idempotency leases (specs/08 §8.3)."""
    from common.firestore import get_client
    from idempotency.reaper import reap_expired_leases

    return reap_expired_leases(get_client(), ctx)


# --------------------------------------------------------------------------- #
# reset-demo
# --------------------------------------------------------------------------- #
def reset_demo(payload: dict, ctx) -> dict:
    """Re-seed the deterministic demo dataset so the public demo self-heals nightly.

    Re-runs the SAME builder the ``seed_demo`` management command uses (specs/18
    §18.1, specs/14 §14.2). Idempotent: the seed writes deterministic ids with
    overwriting ``set``s, so a nightly re-run resets any drift the day's demo use
    introduced — shared demo credentials always find a clean portfolio.

    The Firestore dataset reseed is the core (it is what drifts). Demo-user
    provisioning is attempted best-effort afterwards — it needs Firebase Auth admin
    (or the Auth emulator), so a failure there is logged and swallowed rather than
    failing the whole reseed job.
    """
    from common.firestore import get_client
    from seed.builder import SeedRunner

    client = get_client()
    stats = SeedRunner(client).run()

    users = 0
    try:
        from seed import users as seed_users

        provisioned = seed_users.provision_demo_users(
            client, password=seed_users.DEFAULT_PASSWORD
        )
        users = len(provisioned)
    except Exception:  # noqa: BLE001 — dataset reseed is the core; user provisioning is best-effort
        logger.warning(
            "reset-demo: demo-user provisioning skipped/failed (dataset reseeded ok)",
            exc_info=True,
        )

    logger.info("reset-demo reseeded dataset stats=%s users=%s", stats, users)
    return {"job": "reset-demo", "reseeded": True, "stats": stats, "users": users}


# --------------------------------------------------------------------------- #
# expire-idempotency-keys — TTL-lag METRIC EMITTER (deletes nothing)
# --------------------------------------------------------------------------- #
def expire_idempotency_keys(payload: dict, ctx) -> dict:
    """Emit a TTL-lag metric for idempotency-key retention (specs/14 §14.2).

    **This job DELETES NOTHING.** Firestore TTL on ``idempotencyKeys.expiresAt`` is
    what physically deletes expired records (specs/21 §21.1); TTL sweeps can lag up
    to ~72h. This daily job merely counts records whose ``expiresAt`` is already in
    the past but which TTL has not yet swept, and logs the count, so an operator can
    alarm if that backlog grows abnormally — a pure observability backstop.

    Spec reconciliation: this job is defined in specs/14 §14.2 but omitted from the
    specs/21 §21.2 Cloud Scheduler list. We keep the thin daily metric-emitter per
    §14.2 (no deletion, no dependency on any queue) and treat the §21.2 omission as
    an oversight — deleting here would race Firestore TTL and buy nothing.
    """
    from datetime import datetime, timezone

    from common.firestore import get_client
    from repositories import refs

    client = get_client()
    now = datetime.now(timezone.utc)

    lagging = 0
    cursor: Optional[Any] = None
    pages = 0
    capped = False
    while True:
        query = (
            client.collection(refs.IDEMPOTENCY_KEYS)
            .where(filter=refs.field_filter("expiresAt", "<", now))
            .order_by("expiresAt")
            .order_by("__name__")
        )
        if cursor is not None:
            query = query.start_after(cursor)
        snapshots = list(query.limit(refs.BATCH_SIZE).stream())
        lagging += len(snapshots)
        pages += 1
        if len(snapshots) < refs.BATCH_SIZE:
            break
        if pages >= _MAX_PAGES:
            capped = True
            break
        cursor = snapshots[-1]

    if capped:
        logger.warning(
            "expire-idempotency-keys hit the %s-page cap; ttl_lagging=%s is a lower "
            "bound (a large TTL backlog — investigate)",
            _MAX_PAGES, lagging,
        )
    logger.info(
        "expire-idempotency-keys ttl_lagging=%s (Firestore TTL performs deletion; "
        "this job deleted nothing)",
        lagging,
    )
    return {"job": "expire-idempotency-keys", "ttlLagging": lagging, "deleted": 0}
