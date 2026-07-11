# Appendix A — v1 Review Findings & Resolutions

This is the traceability matrix from the v1 spec review. Every finding is listed with its severity, how v2 resolves it, and where. Use it to audit that the review was actually folded into the spec. Severity reflects impact on financial correctness / production-readiness at the time of review.

## Tier 1 — Blocking (financial correctness)

| # | Finding (v1) | Sev | Resolution (v2) | Where |
|---|--------------|-----|-----------------|-------|
| 1 | Two-phase payment has no crash-recovery: die after adapter success / before finalize → contribution stuck `PROCESSING`, money moved, balances not applied, unrecoverable | CRITICAL | Reconciliation sweeper queries the processor via the attempt's deterministic `processorIdempotencyKey` (query, not re-charge) and finalizes idempotently; `PROCESSING→CANCELED` added (reconciliation-only, post-settlement); adapter must expose `get_status` | [08 §8.4](./08-idempotency-and-consistency.md), [09 §9.4–9.5](./09-payment-processing.md), [06 §6.1](./06-state-machines.md) |
| 2 | Idempotency record created *outside* the transition txn (race + orphan window); no lease → dead in-progress key wedges retries forever | CRITICAL | Idempotency doc created **inside** the transition txn with a create-precondition; `leaseOwner`/`leaseExpiresAt` enable reclamation; `202` in-progress client contract | [08 §8.2–8.3](./08-idempotency-and-consistency.md), [04 §4.11](./04-firestore-data-model.md) |
| 3 | Residual unhandled: `83333×36 ≠ 3,000,000`; "reduce to smaller" only covers overage; "≤ scheduled" contradicts the fix → benefit never `COMPLETED`; example internally inconsistent | CRITICAL | Installments solved at generation so `Σ == totalCommitment` (final = 83,345); balance-cap reserved for payoff only; example corrected | [07 §7.3](./07-financial-rules.md), [10 §10.1](./10-benefit-and-employment-workflows.md), [04 §4.6](./04-firestore-data-model.md) |
| 4 | Read authorization has no mechanism (Django not in read path); risk of any authenticated user reading all PII/financials | CRITICAL | Role via Firebase **custom claims**, enforced in security rules (reads) *and* Django (writes); `users/{uid}` self-write denied; read model scoped to "authenticated servicing user" | [12](./12-auth-and-security.md), [04 §4.12](./04-firestore-data-model.md) |

## Tier 2 — High

| # | Finding (v1) | Sev | Resolution (v2) | Where |
|---|--------------|-----|-----------------|-------|
| 5 | Hot-doc contention: portfolio/employer summaries written on every payment, permitted *inside* the payment txn → contention aborts the authoritative payment | HIGH | Aggregates removed from the payment path; payment txn touches only contribution/attempt/loan/agreement/event; summaries via event-driven/debounced projection + scheduled rebuild; sharding path documented | [05 §5.1–5.2](./05-read-models-and-projections.md), [04 §4.3](./04-firestore-data-model.md) |
| 6 | "This month" counters: no rollover, timezone, or idempotent reset; reset hits the hot doc | HIGH | Period-bucketed docs `portfolioSummaries/{YYYY-MM}`; new month = new doc (no reset); `SYSTEM_TIMEZONE` | [05 §5.3](./05-read-models-and-projections.md), [README](./README.md) |
| 7 | Contribution ID `{agreementId}_{YYYY_MM}` bakes in one-per-month; timezone-ambiguous | HIGH | ID keyed on `installmentNumber`; `periodLabel` field for display; `scheduledDate` at 12:00 `SYSTEM_TIMEZONE` | [04 §4.7](./04-firestore-data-model.md), [README](./README.md) |
| 8 | In-flight `PROCESSING` vs termination "requires explicit handling" — undefined; `PROCESSING→CANCELED` disallowed → stuck FAILED + permanently-open exception | HIGH | `acceptingPayments=false` flag blocks new/retry processing; cancel task skips `PROCESSING`; in-flight settles (success posts; failure → settle-then-cancel + dismiss exception); reconciliation backstop | [10 §10.4](./10-benefit-and-employment-workflows.md), [06 §6.1](./06-state-machines.md), [09 §9.4](./09-payment-processing.md) |
| 9 | Singular `activeLoanId` can't represent multiple loans / refinance | HIGH | Canonical link is `loan.borrowerId` (indexed); `primaryLoanId` kept as nullable, non-authoritative convenience | [03 §3.2](./03-domain-model.md), [04 §4.4](./04-firestore-data-model.md) |
| 10 | Denormalized name propagation undefined; frozen-vs-live copies not distinguished | HIGH | Explicit policy: event copies frozen; live mirrors refreshed by `propagate-denormalized-field` task (names) or txn-sync (`benefitStatus`) | [04 §4.2](./04-firestore-data-model.md) |
| 11 | Duplicate open exceptions per contribution; "resolve the related exception" ambiguous | HIGH | Deterministic `exceptionId = {entityId}__{type}` + `occurrenceCount` upsert; `contribution.currentExceptionId` pointer resolves exactly | [04 §4.10](./04-firestore-data-model.md), [09 §9.3](./09-payment-processing.md) |
| 12 | Index `benefitStatus + nextContributionDate` references a field the loan doc lacks; borrowers/agreements/global-event indexes missing | HIGH | `benefitStatus` added as txn-synced field; expanded, corrected index set incl. borrowers, agreements, global events | [13](./13-firestore-indexes.md), [04 §4.5](./04-firestore-data-model.md) |

## Tier 3 — Medium

| # | Finding (v1) | Sev | Resolution (v2) | Where |
|---|--------------|-----|-----------------|-------|
| 13 | No Cloud Scheduler though "scheduled process" is referenced → effectively manual-only | MED | Cloud Scheduler added; `enqueue-due-contributions` + maintenance jobs | [02 §2.1](./02-architecture.md), [14 §14.2](./14-async-and-background-jobs.md) |
| 14 | Retry-vs-cancel race precedence undefined (first-writer-wins ≠ business intent) | MED | Terminating benefit sets `acceptingPayments=false`, which the process/retry transition also checks → cancel wins deterministically | [06 §6.7](./06-state-machines.md) |
| 15 | `version`/optimistic concurrency redundant or false-safety | MED | `revision` = audit counter only; explicit optional `expectedRevision`/`If-Match` at the API for stale-UI protection | [README](./README.md), [11 §11.2](./11-api.md) |
| 16 | Same-txn events share a timestamp → nondeterministic timeline order | MED | `sequence` field; order by `(createdAt, sequence)`; events written in the same txn as the change | [04 §4.9](./04-firestore-data-model.md), [08 §8.5](./08-idempotency-and-consistency.md) |
| 17 | Async schedule-generation over-engineered for 36 rows (fits one batch) | MED | Honest rationale (latency/resumability); sync single-batch allowed under `SYNC_GENERATION_MAX`; async path for long terms only | [10 §10.1](./10-benefit-and-employment-workflows.md) |
| 18 | `paymentAttempts` modeled in two places (§10 vs §16) | MED | Subcollection is authoritative; top-level collection dropped | [04 §4.1, §4.8](./04-firestore-data-model.md) |
| 19 | Real-time subscriptions to large lists have no limit/pagination | MED | `limit` + cursor + indexed scoping required, enforced in shared hooks | [05 §5.6](./05-read-models-and-projections.md), [15 §15.2](./15-ui-and-screens.md), [13 §13.3](./13-firestore-indexes.md) |

## Tier 4 — Minor / editorial

| # | Finding (v1) | Sev | Resolution (v2) | Where |
|---|--------------|-----|-----------------|-------|
| 20 | Redundant derived fields (`remaining = total − paid`; global sum) can drift | LOW | Co-updated in the same txn (invariant I3); global sum is a reconciled projection | [07 §7.2](./07-financial-rules.md), [05 §5.7](./05-read-models-and-projections.md) |
| 21 | `servicingEvents` auto-indexes every field (incl. `metadata`) → write amplification | LOW | Single-field index **exemptions** (`fieldOverrides`) | [13 §13.2](./13-firestore-indexes.md) |
| 22 | `borrowerName` as a query key is stale/non-unique/non-searchable | LOW | Dropped the `borrowerName` index; key on `borrowerId`; text search declared out of scope | [13 §13.1, §13.4](./13-firestore-indexes.md) |
| 23 | No currency field anywhere | LOW | `currency: "USD"` on every money-bearing document | [07 §7.1](./07-financial-rules.md), [04](./04-firestore-data-model.md) |
| 24 | `interestRateBasisPoints` stored though interest is a non-goal | LOW | Marked display-only/informational | [04 §4.5](./04-firestore-data-model.md) |
| 25 | GET list endpoints lack params/pagination; Firestore-vs-Django read split fuzzy | LOW–MED | Command-centric API; reads are subscriptions; pagination contract; read/command split stated | [11 §11.1, §11.5](./11-api.md) |
| 26 | Concurrent-idempotency test is the crown-jewel but not elevated | LOW | Elevated to a required **gate** with a precise expected outcome | [17 §17.2](./17-testing.md) |
| 27 | Circular pointers form a multi-doc invariant; partial update → dangling ref | LOW | Invariant I7; all pointer sides updated atomically | [03 §3.2](./03-domain-model.md), [07 §7.2](./07-financial-rules.md) |
| 28 | Employment-status change listed under both Operations User and Manager | LOW | Restricted to `SERVICING_MANAGER`+; Ops flags via `EMPLOYMENT_VERIFICATION_REQUIRED` | [01 §1.5](./01-product-overview.md), [12 §12.2](./12-auth-and-security.md) |

## Carried forward from v1 unchanged (things the review affirmed)

These were called out as genuinely right and are preserved as-is: integer cents + hard financial invariants; backend-owned writes with deny-all client writes; deterministic doc IDs as idempotency primitives; the adapter call *outside* the transaction (two-phase shape); unbounded fan-out → bounded async batches; immutable append-only servicing events; the honest production-tradeoffs framing; and the crisp non-goals. v2 builds on these rather than changing them.
