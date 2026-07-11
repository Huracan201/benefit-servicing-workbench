# 16 — Observability & Audit

Two related concerns: **audit** (the immutable business record of what happened, for operations/compliance — the `servicingEvents`) and **observability** (operational telemetry for running the system — logs, metrics, traces). They overlap but are not the same; events are domain truth, logs are operational.

## 16.1 Audit requirements

Every material operation captures, in its `servicingEvent`(s): authenticated actor (`actorId`), actor role (`actorRole`), correlation id, entity ids, previous state, new state, amounts where applicable, timestamp, and outcome. Events are **append-only** and never edited or deleted through the application; notes are append-only; **no destructive delete is exposed in the UI** ([04 §4.9](./04-firestore-data-model.md), [07 §7.5](./07-financial-rules.md)).

## 16.2 Structured logging

All backend logs are structured JSON (Cloud Logging). Every API request and task execution logs at least:

`correlationId`, `requestId`, `userId`, `userRole`, `operation`, `entityType`, `entityId`, `idempotencyKey` (hashed), `result`, `durationMs`, and `errorCode` when applicable.

Log these events explicitly (they are the ones you'll want during an incident or demo):

- payment-processing attempts (start/finalize, outcome);
- **duplicate idempotency requests** (replay served) and **in-progress** responses;
- **invalid state transitions** (rejected `409`s);
- **invariant violations** (rejected commands);
- **reconciliation actions** (what the sweeper found and did) — [09 §9.4](./09-payment-processing.md);
- Cloud Task failures + dead-letter routing;
- authentication and authorization failures;
- exception creation/resolution;
- Firestore transaction retries/aborts;
- projection-update failures and deferred-work counts ([14 §14.6](./14-async-and-background-jobs.md)).

Never log full PII or raw tokens; log the idempotency key as a hash.

## 16.3 Metrics (Cloud Monitoring)

| Metric | Why |
|--------|-----|
| contribution-processing success rate | core health; alert on drop |
| contribution-processing latency (p50/p95) | detect adapter/Firestore slowness |
| failed-contribution count / rate | ops load; demo scenario signal |
| **stuck-PROCESSING count** | must trend to ~0; a rising value means reconciliation isn't keeping up |
| open-exception count (by severity) | ops backlog |
| Cloud Task retry count / dead-letter count | async health |
| **idempotency replay & in-progress rate** | detects client retry storms / stuck operations |
| API error rate (by code) | general health |
| Firestore transaction-abort rate | contention signal; a spike implicates a hot doc |
| projection lag (event → summary applied) | read-model freshness |

> **Change from v1 — stuck-PROCESSING, replay/in-progress, and projection-lag metrics added**, matching the v2 recovery and projection mechanisms so their health is observable, not assumed.

## 16.4 Error reporting & tracing

- Metrics are emitted as **log-based metrics** over the §16.2 structured logs (no metrics client code). Baseline alert policies ([21](./21-deployment-and-operations.md)): stuck-PROCESSING count > 0 for 30 min; any `TASK_FAILED` exception; readiness failing 5 min.
- Cloud Error Reporting captures unhandled exceptions and dead-lettered tasks; alerts on new/critical signatures.
- Propagate `correlationId` from the API request through events, logs, and any enqueued task, so one command is traceable end-to-end across the sync handler, its events, and its async follow-ups.

## 16.5 Health & readiness
`GET /health` (liveness) and `GET /readiness` (dependencies reachable — Firestore, Cloud Tasks) back Cloud Run health checks and the demo status page.
