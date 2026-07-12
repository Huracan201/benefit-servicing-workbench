# Engineering Report — Phase 3: Async Workflows & Read-Model Projections

**Project:** BenefitServicing Workbench (`Huracan201/benefit-servicing-workbench`)
**Phase:** 3 — Async workflows (Cloud Tasks + Scheduler) + read-model projections + the deferred security prerequisites ([specs/19 §19.2](../19-delivery-and-scope.md))
**Scope:** the `/internal` task/scheduler surface + `enqueue()` seam · resumable schedule generation · process-contribution + the scheduler fan-out · the reconciliation sweeper + lease reaper · the read-model projection layer · rate-limiting / revoke-on-demotion / input-validation / `due()` pagination
**Status:** ✅ Built & QA-verified across 3 slices — Slices A+B merged to `release/phase-3` (CI green), Slice C pending commit → CI. On draft **PR #5**.
**Date:** 2026-07-12

---

## 1. Summary

Phase 3 turns the Phase-2 **inline** command layer into a real **asynchronous** one: the post-commit tails that ran synchronously in-process now dispatch to Cloud Tasks through an OIDC-gated `/internal` surface, time-triggered work runs under Cloud Scheduler, and the eventually-consistent read models that power the workbench dashboards are built for the first time. It also lands the security prerequisites deferred from the Phase 1+2 review.

The design is dominated by one seam and two correctness properties:

- **The `enqueue()` seam** dispatches on `TASK_EXECUTION_MODE`: `inline` (emulator/CI) runs the **exact same callable** the cloud `/internal/tasks/<task>` view invokes; `cloud` enqueues a real Cloud Task with a minted OIDC token. Because inline runs the identical body, **the entire Phase-2 emulator suite keeps passing throughout** — CI stays representative of production while the async machinery is added underneath it.
- **The completion protocol (202-cloud / 200-inline)** keeps an idempotency key **PENDING across the commit→task boundary** and completes it — with the *command's* response body — only after the async tail runs. This is what makes **exactly-once survive the move to Cloud Tasks**: a crash between commit and task is reclaimed by the lease reaper and re-driven by the same key, never a fresh side effect.
- **Recompute-from-source projections.** Every summary is *recomputed* from source collections and written as a full-document overwrite — never a folded delta — so an at-least-once redelivery of a projection-update is byte-identical. Summary writes are strictly **off the payment transaction** (the ~1 write/sec/doc hot-doc rule); the event-driven path and the scheduled rebuild share one recompute engine, so they agree by construction.

**Headline outcomes**
- A new `backend/internal/` app (enqueue seam · SYSTEM context · dead-letter envelope · task/job base views · `run_job`), plus the `backend/projections/` app (recompute engine · fanout · tasks) and three read-model gateways.
- The full async handler set: resumable `generate-schedule`, `process-contribution` + `enqueue-due-contributions`, `reconcile-contribution` + `reconcile-stuck-payments`, `reap-expired-leases`, and the inline `cancel-future`/`shift` helpers wrapped as tasks.
- All five deferred security prerequisites (command-boundary SYSTEM authority guard, rate limiting, entity-id validation, revoke-on-demotion, `due()` pagination).
- **3 HIGH + 5 MEDIUM + several LOW** issues found by layered adversarial QA — **all fixed and independently verified** (2 LOW consciously deferred + documented) before merge.

---

## 2. Scope

Built in three dependency-ordered, individually-QA'd slices:

**Slice A — Foundation & security prerequisites.** The `internal/` app + `enqueue()`; SYSTEM `system_ctx`; the dead-letter envelope; `require_system_or_role` command-boundary guard; DRF rate limiting; entity-id validation; revoke-on-demotion; paginated `due()`.

**Slice B — Task handlers.** Resumable `generate-schedule` (fast single-atomic ≤120, multi-batch above); `process-contribution` + the `enqueue-due-contributions` scheduler fan-out; the reconciliation sweeper (`reconcile-contribution` + `reconcile-stuck-payments` + the stuck/stale-attempt scans); `reap-expired-leases` + the per-operation reclamation table; the `cancel-future`/`shift` inline helpers wrapped as Cloud Tasks; the **completion protocol** across activate/terminate/resume/employment.

**Slice C — Read-model projections.** The recompute engine + three gateways; the event→keys fanout with strictly post-commit hooks; the `update-projection` task; `rebuild-summaries` (drift backstop); `reset-demo` / `expire-idempotency-keys` jobs; seed population so a fresh demo has live dashboards.

**Deferred (by design):** `U12` Cloud Tasks/Scheduler provisioning + the readiness flip (deploy-only, cloud-verified) and `U13` `propagate-denormalized-field` (its producer — a borrower/employer rename command — does not exist yet; only the target fields are reserved).

---

## 3. What was delivered

| Area | Module(s) | Highlights |
|------|-----------|-----------|
| Async infra | `internal/enqueue.py`, `system_context.py`, `dead_letter.py`, `views.py`, `run_job.py` | `enqueue()` inline↔cloud seam; un-forgeable SYSTEM marker; retryable/terminal + `TASK_FAILED` dead-letter |
| Auth | `commands/authz.py`, `firebase_auth/middleware.py` | `internal_verified` ingress marker + `require_system_or_role` (fail-closed) |
| Task handlers | `internal/tasks.py`, `contributions/generate.py` | generate-schedule (resumable) · process/reconcile/cancel/shift adapters + completion protocol |
| Scheduler jobs | `internal/jobs.py` | enqueue-due · reconcile-stuck · reap-expired-leases · rebuild-summaries · reset-demo · expire-idempotency-keys |
| Reaper | `idempotency/reaper.py`, `repositories/idempotency_keys.py` | expired-lease query + per-operation reclamation (get_status re-drive, never a fresh charge) |
| Scans | `repositories/contributions.py` | paginated `due()` · `stuck_processing` · collection-group `stale_started_attempts` + indexes |
| Projections | `projections/recompute.py`, `fanout.py`, `tasks.py`; `repositories/{portfolio,employer}_summaries.py`, `loan_workbenches.py` | recompute-from-source engine · off-txn fanout · shared apply_key |
| Rate limiting | `config/settings.py` + all 15 mutating views | `ScopedRateThrottle`, 429 in the clean envelope |
| Seed | `seed/` | populates summaries via the shared recompute so a fresh demo dashboard is live |

---

## 4. Architecture highlights (the load-bearing ideas)

**The `enqueue()` seam is the CI↔prod mirror.** `inline` looks the task up in the same `TASK_HANDLERS` registry the cloud view dispatches through and runs it synchronously; `cloud` `CreateTask`s to `/internal/tasks/<task>` with an OIDC token for the invoker SA. One registry, one callable — a green inline CI genuinely exercises the production body.

**The completion protocol (Decision A).** activate/terminate/resume/employment commit their core transaction with the idempotency key **PENDING**, then `enqueue()` the tail with the **command's response body** (`commandResult`) in the payload. `enqueue()` returns the inline result (→ command renders `200`) or `None` (cloud → `202` + `Retry-After`, the client polls the same key). The task adapter runs the idempotent tail and completes the key **with the command body**, so first-call == same-key replay == cloud-poll. A crash after commit leaves the key PENDING → the **lease reaper** re-drives the tail by the same key.

**SYSTEM authority is derived, not asserted.** `InternalOIDCMiddleware` verifies the invoker and stamps `request.internal_verified`; the handler mints the SYSTEM context **only** when that marker is present and re-asserts `require_system_or_role` at the command boundary — so a bypassed/misconfigured middleware fails closed (403) rather than running SYSTEM for anyone. The marker can never be forged from request input.

**Crash-recovery is a first-class handler, not a hope.** The reconciliation sweeper scans stuck `PROCESSING` contributions and stale `STARTED` attempts (a collection-group query) and re-drives `reconcile-contribution`, which queries the processor by the deterministic key (`get_status`, never a fresh charge) and escalates `PAYMENT_STUCK_PROCESSING` at `MAX_SWEEPS`. The reaper applies the per-operation reclamation table to expired-lease PENDING keys and leaves healthy in-`ASYNC_LEASE_TTL` keys untouched.

**Projections are recompute-from-source, off-txn, and self-consistent.** Every `recompute_*` reads source with bounded queries and returns the full derived doc; the `update-projection` task and the `rebuild-summaries` drift-backstop dispatch through the **same** `apply_key`, so event-driven and scheduled results are identical by construction and any redelivery is byte-identical. Period metrics bucket by `contribution.periodLabel` (never wall-clock); commitment rollups exclude terminal agreements; no summary write ever enters a command transaction.

---

## 5. Process — understand → design → 3× (build → adversarial QA → fix → verify)

1. **Understand + design.** A workflow fanned out four readers over specs 05/09/14/21 + the existing seams and synthesized one dependency-ordered build plan (13 units, the interface contracts, the open decisions). Two consequential forks — the async completion contract and projection strategy — were taken to the user (→ 202-cloud/200-inline; per-event affected keys + current on the rebuild cadence).
2. **Per-slice build.** Each slice ran as a workflow with **collision-free file ownership** (disjoint domain-logic files built in parallel; the shared `/internal` wiring + command seams built after, on stable files) and an in-workflow integration lead that compiled, traced the load-bearing seam, and fixed cross-unit defects.
3. **Per-slice adversarial QA.** Four independent reviewers attacked the slice's dimensions and **every finding was independently verified** (CONFIRMED / PLAUSIBLE / REFUTED with a concrete reproduction) before it counted — refuting several plausible-but-wrong findings and catching the real ones.
4. **Consolidated fix + lead verification.** A single fixer applied the confirmed fixes coherently; the lead then verified by reading the actual code paths (not on assertion) and, for the completion protocol, traced first-call/replay/inline/cloud agreement by hand.

The layered approach earned its keep on every slice: the CI-reddening throttle-test no-op, the vacuous authority guard, the completion-key-stores-the-wrong-body bug, and the systematic projection-fanout gap were all invisible to compile checks and only fell out of adversarial tracing against the idempotency/consistency contracts.

---

## 6. Verification & tests

| Check | Where | Result |
|-------|-------|--------|
| Full backend `compileall` (every slice + every fix) | offline | ✅ exit 0 |
| Adversarial QA (4 reviewers/slice + per-finding verification) | 3 slices | ✅ findings fixed/verified |
| Slice A emulator + unit (foundation, throttle, authz, `due()`, revoke) | **CI** | ✅ green (PR #5) |
| Slice B emulator (completion protocol, generate resumability, sweeper, reaper) | **CI** | ✅ green (PR #5) |
| Slice C emulator (recompute, off-txn, period bucketing, activation→ACTIVE, reconcile→rollups) | **CI** | ⏳ pending commit |
| `manage.py check` + Django boot (2 new apps: `internal`, `projections`) | **CI** | ✅ (A+B) |

**Tests added:** unit tests for the enqueue/dead-letter seam, the SYSTEM authority guard (incl. the unverified→403 and unknown-`min_role`→fail-closed cases), throttling, and the fanout key table; emulator integration tests for generate-schedule resumability, the tail-completion protocol, the reconciliation sweeper, the lease reaper, the paginated scans, recompute correctness (periodLabel bucketing + terminal-exclusion), and the two projection-freshness traces added by the Slice-C fix (inline activation → ACTIVE summary; reconcile-recovered posting → employer/period rollups).

---

## 7. Issues found & fixed (before merge)

All found by per-slice adversarial QA; all fixed and lead-verified unless marked deferred.

| # | Sev | Slice | Issue | Resolution |
|---|-----|-------|-------|-----------|
| 1 | 🔴 HIGH | A | Throttle unit tests were a **no-op** — DRF binds `THROTTLE_RATES` at import, so `@override_settings` never lowered the rate → the four throttle tests would have **reddened CI** | Patch the bound `SimpleRateThrottle.THROTTLE_RATES` in `setUp` |
| 2 | 🟠 MED | A | The `/internal` authority guard was **vacuous** — the view minted SYSTEM before the guard, so it could never deny | Middleware stamps `internal_verified`; SYSTEM minted only when present; unverified → 403 |
| 3 | 🟡 LOW | A | Dead-letter classified **all** `CommandError` as terminal; `min_role` fail-open | Retry `OperationInProgress`/`StaleWrite`; fail-closed on unknown `min_role` |
| 4 | 🔴 HIGH | B | terminate/resume/employment completed the idempotency key with the **tail's** summary, not the **command response** → replay ≠ original, inline ≠ cloud | Thread `commandResult` through the payload; complete + return the command body |
| 5 | 🟠 MED | B | An `activate` key stranded **PENDING** when `generate-schedule` halts on a concurrent terminate | `idempotency.fail` the key on a halted outcome |
| 6 | 🟠 MED | B | Dead-letter `ACTIVATING→PENDING` rollback **orphaned** already-created contributions (resumable path) | Delete the partial contributions for a true clean slate |
| 7 | 🟡 LOW | B | `resume` used the sync lease TTL for an async handoff | Use `ASYNC_LEASE_TTL_SECONDS` (matching activate/terminate) |
| 8 | 🔴 HIGH | C | The async tails emit the terminal event but **never nudged the fanout** → dashboards stayed stale (benefit stuck `ACTIVATING`) until the `*/15` rebuild | Post-commit **guarded** fanout nudge in generate/cancel/shift/reconcile |
| 9 | 🟠 MED | C | A reconcile-recovered posting **never nudged** the rollups | Nudge the recovered `POSTED`/`FAILED` key set (covers the RECLAIM path) |
| 10 | 🟡 LOW ×2 | B, C | Reaper can't reclaim the employment-cascade key (entityId is a borrowerId); per-period `scheduledCents` not event-refreshed | **Deferred + documented** — client retry re-drives the cascade key; `rebuild-summaries` owns `scheduledCents` |

**Cleared, not bugs (refuted with reproduction attempts):** the inline↔cloud JSON round-trip divergence; a spurious `TASK_FAILED` on suspend-between-scan-and-run; a period doc-id format mismatch; any summary write inside a payment transaction; any recompute folding a delta.

---

## 8. Key decisions

- **202-cloud / 200-inline completion contract**, completing the key with the **command response body** — the only shape where exactly-once, replay-consistency, and inline==cloud all hold. It changes the async commands' HTTP contract (documented for the OpenAPI update).
- **Recompute-from-source, never fold a delta** — the single thing that makes projections idempotent under at-least-once delivery and lets the event-driven and scheduled paths share one engine.
- **Projections off the payment transaction; the hot `portfolioSummaries/current` on the rebuild cadence** — the ~1 write/sec/doc ceiling never touches the authoritative payment.
- **SYSTEM authority derived from verified ingress**, not a self-minted marker — the command-boundary guard can actually deny.
- **Keep the fast single-atomic `generate-schedule` path ≤120 installments** so the common case keeps its latency and the existing test outcome, while bounding the tail above it.
- **Staged slices with adversarial QA between each** — each slice merged CI-green before the next was built on it.

---

## 9. Known limitations (by design / documented)

- **Reaper reclamation of the employment-cascade key** is deferred (the record `entityId` is a borrowerId, not the agreementId the tail needs) — a same-key client retry still re-drives; only the server-side reaper defers. Documented.
- **Reaper re-drive of a crashed terminate/resume** completes the key with the tail summary (the command body is unavailable to the reaper) — a bounded, crash-only replay inconsistency. Documented.
- **`reconcile` INDETERMINATE escalation** bumps `openExceptionCount` without a projection nudge — the open-exception tiles reconcile on the `*/15` rebuild.
- **`scheduledCents` per-period refresh** is owned by `rebuild-summaries`, not per-event fanout (a single activation spans many periods the event can't enumerate).
- **`U12` (queue/scheduler provisioning + readiness flip)** and **`U13` (propagate-denormalized)** are out of this phase — the former is deploy-only + cloud-verified; the latter awaits its producer command.
- **CodeRabbit has not reviewed Phase 3 yet** — it is configured to skip draft PRs; the review runs when PR #5 is marked ready.

---

## 10. What's next

Commit Slice C → PR #5 CI (the real emulator run for the projection flows) → **mark PR #5 ready-for-review** so CodeRabbit reviews the whole phase → address its comments → merge. That completes the async/projection backend. Then **Phase 4** (the Workbench UI over these read models) and, at deploy time, **`U12`** (provision the Cloud Tasks queues + Cloud Scheduler crons, flip readiness to `configured`).

---

*Related: [phase-2-part-2-remaining-commands.md](./phase-2-part-2-remaining-commands.md) · [security-review-phase-1-2.md](./security-review-phase-1-2.md) · [specs/14](../14-async-and-background-jobs.md) (async jobs) · [specs/05](../05-read-models-and-projections.md) (projections) · [specs/08 §8.3](../08-idempotency-and-consistency.md) (lease + reclamation) · [specs/09](../09-payment-processing.md) (two-phase payment + reconcile) · [specs/19 §19.2](../19-delivery-and-scope.md) (phase scope).*
