# 05 — Read Models & Projections

Read models are denormalized documents shaped to render a screen without joins. They are **derived, eventually consistent, and never authoritative** — a command never reads a projection to make a financial decision; it reads the source entities inside its transaction.

## 5.1 The hot-document problem (why projections are off the payment path)

Firestore sustains roughly **one write per second per document**. Any single document mutated on every payment becomes a throughput ceiling and a contention source. Worse, if that write happens *inside* the payment transaction, Firestore's conflict detection aborts and retries the **entire authoritative payment** when the hot document is contended (and the no-read-after-write rule forces the hot doc into the transaction's read set).

> **Change from v1 — aggregates never share a transaction with a payment.** v1 permitted updating `portfolioSummaries`/employer rollups "within the primary transaction when document count is bounded." Document *count* is not the issue; single-document *write contention* is. In v2 the payment transaction touches only `contribution`, `agreement`, `loan`, and the `servicingEvent` (all low-contention: at most a handful of writers per document per month). **All portfolio- and employer-level aggregates are updated out of band** by the projection layer described below.

## 5.2 Projection update mechanisms

Three mechanisms, chosen per read model by contention and criticality:

1. **In-transaction (synchronous).** Only for low-contention, per-account fields that must be exactly consistent with the source — e.g. `loan.benefitStatus`, `loan.nextContributionDate`, `contribution.status`. These are part of the source write, not a separate projection.
2. **Event-driven projection (async, recompute).** The command enqueues an `update-projection` task naming the affected summary key(s) (`loanId`, `employerId`, period). The task **recomputes the summary from source collections with bounded queries** — it does *not* fold the event's delta into the stored value. Recompute is what makes the handler genuinely idempotent under Cloud Tasks' at-least-once delivery (a redelivered increment double-counts; a redelivered recompute converges), makes "coalescing" trivial (N pending tasks for the same key all write the same recomputed value; a cheap `projectionDirty` marker or named-task dedup collapses them), and makes the fold-vs-rebuild race harmless (both writers produce source-derived values). This is the default for `portfolioSummaries`, `employerSummaries`, and `loanWorkbenches`.
3. **Full rebuild (scheduled).** A periodic Cloud Scheduler job recomputes a summary from source collections (aggregation queries), correcting any drift from missed/rejected projection updates. This is the backstop that keeps eventually-consistent counters honest. See [14](./14-async-and-background-jobs.md).

For very hot counters (a large employer's monthly run), use **sharded counters** (write to one of N shard docs, sum on read) rather than a single doc. The MVP's seed scale doesn't require sharding, but the projection interface is designed so sharding is an internal change to the projection writer, not a schema change visible to readers.

## 5.3 Portfolio summary — `portfolioSummaries/{docId}`

Split into a **point-in-time** doc and **period-bucketed** docs.

`portfolioSummaries/current` — point-in-time totals (extended to cover every dashboard tile — [15 §15.3](./15-ui-and-screens.md)):
```jsonc
{
  "activeLoans": 18,
  "activeBenefitAgreements": 16,
  "benefitStatusCounts": { "ACTIVE": 16, "SUSPENDED": 2, "TERMINATED": 2, "COMPLETED": 1, "PENDING": 1 },
  "contributionStatusCounts": { "SCHEDULED": 142, "PROCESSING": 3, "FAILED": 4, "RETRY_PENDING": 3, "POSTED": 96, "CANCELED": 12 },
  "openExceptionCount": 5,
  "openExceptionSeverityCounts": { "CRITICAL": 1, "HIGH": 2, "MEDIUM": 2, "LOW": 0 },
  "openExceptionTypeCounts": { "PAYMENT_FAILED": 3, "SERVICER_SYNC_FAILURE": 1, "EMPLOYMENT_VERIFICATION_REQUIRED": 1 },
  "remainingEmployerCommitmentCents": 72600000,   // NON-TERMINAL agreements only (07 §7.7)
  "updatedAt": "<ts>"
}
```

`portfolioSummaries/{YYYY-MM}` — per-period flow metrics (one doc per calendar month, in `SYSTEM_TIMEZONE`):
```jsonc
{
  "periodLabel": "2026-07",
  "scheduledCents": 1250000,     // maintained by generation/shift + rebuild only (no per-event source)
  "postedCents": 1035000,
  "failedContributionCount": 4,
  "updatedAt": "<ts>"
}
```

**Period attribution (normative).** All period metrics bucket by the contribution's **`periodLabel`** — never by the wall-clock month of the posting event. A July installment that fails in July and posts on Aug 2 counts in **2026-07**'s `postedCents`: the servicing question is "did July's obligations get paid," and periodLabel-bucketing is also what the rebuild naturally computes, so the event-driven recompute and the scheduled rebuild agree by construction. Payment events carry `periodLabel` in metadata ([04 §4.9](./04-firestore-data-model.md)) so projections never need a lookup.

**Commitment-rollup scoping (normative).** `remainingEmployerCommitmentCents` (and the per-employer equivalent) sum only `ACTIVE`/`SUSPENDED`/`ACTIVATING` agreements — terminal agreements' residual commitment is money that will never move ([07 §7.7](./07-financial-rules.md)).

> **Change from v1 — period-bucketed "this month" counters.** v1 stored `scheduledThisMonthCents`/`postedThisMonthCents` as flat fields on a single `current` doc, implying an in-place reset to 0 at month boundary. That reset is (a) non-idempotent against concurrent increments (contradicting "every task handler idempotent"), (b) timezone-ambiguous, and (c) a write to the hottest doc at the busiest moment. v2 reads "this month" from `portfolioSummaries/{current-period}`; there is no reset — a new month is simply a new document. The dashboard resolves the current period in `SYSTEM_TIMEZONE`.

## 5.4 Employer summary — `employerSummaries/{employerId}` (+ periods subcollection)

Base doc — point-in-time per employer:
```jsonc
{
  "employerId": "emp_memorial", "employerName": "Memorial Health",
  "activeBorrowers": 14,
  "activeBenefits": 13,
  "monthlyObligationCents": 1083329,
  "openExceptionCount": 2,
  "totalCommitmentCents": 85000000,          // for the utilization meter (posted / committed)
  "amountPaidCents": 21500000,
  "remainingCommitmentCents": 63500000,      // non-terminal agreements only (07 §7.7)
  "updatedAt": "<ts>"
}
```

`employerSummaries/{employerId}/periods/{YYYY-MM}` — per-employer per-month flow:
```jsonc
{ "periodLabel": "2026-07", "postedCents": 812000, "failedCount": 1, "updatedAt": "<ts>" }
```

## 5.5 Loan workbench — `loanWorkbenches/{loanId}`

Everything needed to render a loan-portfolio row and the account header without joins: borrower summary, employer + employment status, loan summary, benefit summary, next contribution, open-exception count, latest servicing activity marker.

```jsonc
{
  "loanId": "loan_jordan_001",
  "borrowerId": "bor_jordan", "borrowerName": "Jordan Lee", "borrowerEmail": "…",
  "employerId": "emp_memorial", "employerName": "Memorial Health",
  "employmentStatus": "ACTIVE",
  "servicerName": "Demo Student Loan Servicer",
  "currentBalanceCents": 7142000,
  "loanStatus": "ACTIVE",
  "benefitAgreementId": "ben_jordan_001", "benefitStatus": "ACTIVE",
  "baseMonthlyContributionCents": 83333,     // mirrors benefitAgreement.baseMonthlyContributionCents
  "nextContributionDate": "<ts>", "nextContributionAmountCents": 83333,
  "openExceptionCount": 1,
  "lastActivityAt": "<ts>", "lastActivityType": "PAYMENT_POSTED",
  "updatedAt": "<ts>"
}
```

This is the widest live mirror, so it is the biggest staleness surface. It is updated by event-driven projection (mechanism 2) keyed on `loanId`, and corrected by the scheduled rebuild (mechanism 3). Because it is per-loan, its write rate is low (a handful of events per loan per month), so a single doc per loan is fine — no sharding needed.

## 5.6 Frontend subscription rules (normative)

Real-time subscriptions are how the workbench stays live, but an unbounded subscription is a cost and memory footgun.

- Every list subscription **must** include a `limit` and use cursor-based pagination; never subscribe to an entire collection.
- Subscriptions **must** be scoped by an indexed predicate (per-employer, per-status, per-loan) rather than client-side filtering a broad query.
- The dashboard subscribes to `portfolioSummaries/current` and the current-period doc (2 documents), not to raw contribution/exception collections.
- Detail screens subscribe to the specific account's documents (`loanWorkbenches/{loanId}`, that loan's events subcollection with a `limit`), not to global collections.

> **Change from v1 — pagination/scoping is required, not optional.** v1 relied on real-time subscriptions to read models but never bounded them. At scale, an unbounded `onSnapshot` downloads and pins the whole filtered set and re-downloads on reconnect. v2 makes `limit` + cursor pagination + indexed scoping a hard requirement, enforced in the shared data-access hooks ([15](./15-ui-and-screens.md)).

## 5.7 Consistency expectations (state these in the UI)

Read models are eventually consistent. The UI shows authoritative per-account state (from the synchronously-updated source fields it subscribes to) immediately after a command's response, but portfolio/employer **aggregates may lag by seconds** while projections catch up. Screens should not imply that a just-completed payment has instantly moved a portfolio-wide total; the per-account view is the source of immediate truth, the rollups converge shortly after.

> **Change from v1 — derived-field drift acknowledged.** `remainingCommitmentCents = totalCommitmentCents − amountPaidCents` is stored redundantly. It is safe only because it is **always co-updated with `amountPaidCents` in the same write**. The global `remainingEmployerCommitmentCents` sum is a projection and is expected to lag; it is reconciled by the scheduled rebuild.
