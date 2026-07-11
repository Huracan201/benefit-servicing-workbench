# 14 — Asynchronous & Background Jobs

Deferred and scheduled work runs on **Cloud Tasks** (command-oriented deferred work with explicit retry) and **Cloud Scheduler** (time-triggered work). Handlers are ordinary Django endpoints on Cloud Run under **`/internal/tasks/*` and `/internal/jobs/*`**, and are **all idempotent** (Cloud Tasks delivers at-least-once).

**Inbound auth (normative):** every handler verifies its caller is Cloud Tasks/Scheduler via a Google-signed **OIDC token** (audience + invoker service account) — these URLs are internet-reachable and execute as SYSTEM, so this check is mandatory; full mechanism in [12 §12.5](./12-auth-and-security.md). ("Authenticated as the service account" refers to the handler's *outbound* Firestore identity, which is separate.) Queue definitions, retry parameters, cron strings, and all named constants (`STUCK_THRESHOLD`, `LEASE_TTL`, `MAX_SWEEPS`, `BATCH_SIZE`, `SYNC_GENERATION_MAX`) are pinned in [21](./21-deployment-and-operations.md). **Local dev:** `TASK_EXECUTION_MODE=inline` (auto-selected under the emulator) executes handlers synchronously at enqueue time, and `manage.py run_job <name>` fires any scheduler job manually — no Cloud Tasks emulator exists.

> **Change from v1 — Cloud Scheduler added.** v1 listed Cloud Tasks but no scheduler, while the process workflow referenced a "scheduled process." Without a time trigger, due contributions never process automatically. v2 adds Cloud Scheduler as the time source; Cloud Tasks remains the unit-of-work executor.

## 14.1 Why Cloud Tasks (not Pub/Sub) for the work units

Cloud Tasks fits because each work item needs: explicit per-item retry with backoff, individual traceability, command (not event-fanout) semantics, and rate limiting against hot targets. **Pub/Sub is used only where genuine fan-out adds value** (e.g. broadcasting a projection-invalidation to multiple independent consumers) — not as the default.

## 14.2 Scheduler jobs (Cloud Scheduler → enqueues tasks)

| Job | Cadence | Action |
|-----|---------|--------|
| `enqueue-due-contributions` | hourly, business hours ([21](./21-deployment-and-operations.md)) | query contributions `status ∈ {SCHEDULED, RETRY_PENDING}` with `scheduledDate <= now` (indexed); `acceptingPayments` is a **per-item agreement check** (a cross-document predicate — not expressible in the Firestore query; agreements are read per candidate with per-scan caching, and the Phase-1 transaction re-checks it authoritatively); enqueue a `process-contribution` task per eligible item (bounded page size) |
| `reconcile-stuck-payments` | e.g. every 10 min | query `status == PROCESSING, lastAttemptAt < now − STUCK_THRESHOLD`; enqueue a `reconcile-contribution` task per item ([09 §9.4](./09-payment-processing.md)) |
| `rebuild-summaries` | e.g. every 15 min + nightly full | recompute `portfolioSummaries`/`employerSummaries` from source to correct projection drift ([05](./05-read-models-and-projections.md)) |
| `expire-idempotency-keys` | daily | rely on Firestore TTL; this job is a backstop/metric emitter |
| `reap-expired-leases` | every 5 min | find `idempotencyKeys` `PENDING` with expired lease (indexed — [13](./13-firestore-indexes.md)); apply the **per-operation reclamation table** in [08 §8.3](./08-idempotency-and-consistency.md) |
| `reset-demo` | nightly | re-run the seed script against the demo project so shared demo credentials always find a clean portfolio ([18](./18-seed-and-demo.md)) |

## 14.3 Task handlers (Cloud Tasks)

| Handler | Enqueued by | Idempotency mechanism |
|---------|-------------|-----------------------|
| `generate-schedule` | activate-benefit command | deterministic contribution IDs + create-precondition; resumes from `agreement.installmentsGenerated` ([10 §10.1](./10-benefit-and-employment-workflows.md)) |
| `process-contribution` | scheduler / manual re-enqueue | command idempotency key + contribution status precondition ([09 §9.1](./09-payment-processing.md)) |
| `reconcile-contribution` | reconcile scheduler | processor `get_status` by deterministic `processorIdempotencyKey`; finalize guarded on `currentAttemptId` |
| `cancel-future-contributions` | terminate/employment command | per-contribution status-guarded transition over `SCHEDULED`/`RETRY_PENDING`/`FAILED` (dismissing `FAILED` rows' exceptions); bounded batches; skips `PROCESSING` ([10 §10.4](./10-benefit-and-employment-workflows.md)) |
| `shift-schedule` | resume command | re-date remaining `SCHEDULED` installments + extend `endDate` per the shift policy ([07 §7.8](./07-financial-rules.md)); idempotent (re-dating to the shifted date is a no-op) |
| `propagate-denormalized-field` | source name/employer change | idempotent field overwrite; bounded batches ([04 §4.2](./04-firestore-data-model.md)) |
| `update-projection` | any command emitting an event | **recomputes** the affected summary key(s) from source with bounded queries — never folds increments, which double-count under at-least-once delivery ([05 §5.2](./05-read-models-and-projections.md)) |

## 14.4 Bounded batching

Any handler that touches a variable number of documents processes them in **bounded pages**, persisting progress (a cursor or a progress counter like `installmentsGenerated`) so a redelivery or a continuation task resumes without redoing or skipping work. A handler that discovers more work than one batch **re-enqueues a continuation task** for the remainder rather than exceeding the limit.

## 14.5 Retry, backoff, dead-letter

- Cloud Tasks queues are configured with exponential backoff and a max-attempts cap.
- **Cloud Tasks has no native dead-letter queue** (that is a Pub/Sub feature). Dead-lettering is implemented in the handler: it compares `X-CloudTasks-TaskRetryCount` against the queue's max-attempts and, on the final attempt, records a `TASK_FAILED` operational exception (severity HIGH — [04 §4.10](./04-firestore-data-model.md)) and returns 2xx to stop retries — never silently dropped. For `generate-schedule`, the dead-letter handler also rolls the agreement `ACTIVATING → PENDING` ([10 §10.1](./10-benefit-and-employment-workflows.md)).
- Handlers distinguish **retryable** (transient Firestore/adapter errors → return 5xx so Cloud Tasks retries) from **terminal** (bad input, invariant violation → return 2xx to stop retries and record an exception), so a poison task can't retry forever.

> **Change from v1 — explicit DLQ + retryable/terminal distinction.** v1 said "every task handler idempotent" (kept) but didn't specify failure routing. v2 adds dead-lettering and the retryable-vs-terminal contract so failures are visible and bounded.

## 14.6 No silent caps

If a scheduled scan pages through work with a per-run cap (e.g. process at most N due contributions per tick), the handler **logs the number deferred** to the next run ([16](./16-observability.md)). A bounded run must never read as "everything processed" when it wasn't.
