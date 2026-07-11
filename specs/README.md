# BenefitServicing Workbench — Engineering Specification (v2)

An operations platform for managing employer-sponsored student-loan repayment benefits. This is the **post-origination servicing** domain: benefit activation, employer-funded contribution schedules, payment processing, employment-status changes, exception handling, and immutable audit history. It is **not** a loan-origination, underwriting, or interest-accrual system.

Firestore (Native mode) is the primary system of record. All authoritative financial and servicing state changes are executed by a Django backend behind explicit transactions, state machines, idempotency records, and immutable events. The frontend receives real-time updates by subscribing to approved read models but never mutates protected state directly.

---

## How to read this spec

Read in numeric order for a first pass. For implementation, the **correctness-critical core** is docs 04–10 and 12 — read those carefully before writing any command handler.

| # | Document | Read if you are… |
|---|----------|------------------|
| 01 | [product-overview.md](./01-product-overview.md) | anyone, first |
| 02 | [architecture.md](./02-architecture.md) | anyone, first |
| 03 | [domain-model.md](./03-domain-model.md) | anyone |
| 04 | [firestore-data-model.md](./04-firestore-data-model.md) | backend, data |
| 05 | [read-models-and-projections.md](./05-read-models-and-projections.md) | backend, frontend |
| 06 | [state-machines.md](./06-state-machines.md) | backend |
| 07 | [financial-rules.md](./07-financial-rules.md) | backend |
| 08 | [idempotency-and-consistency.md](./08-idempotency-and-consistency.md) | backend |
| 09 | [payment-processing.md](./09-payment-processing.md) | backend |
| 10 | [benefit-and-employment-workflows.md](./10-benefit-and-employment-workflows.md) | backend |
| 11 | [api.md](./11-api.md) | backend, frontend |
| 12 | [auth-and-security.md](./12-auth-and-security.md) | backend, frontend, security |
| 13 | [firestore-indexes.md](./13-firestore-indexes.md) | backend, data |
| 14 | [async-and-background-jobs.md](./14-async-and-background-jobs.md) | backend, infra |
| 15 | [ui-and-screens.md](./15-ui-and-screens.md) | frontend |
| 16 | [observability.md](./16-observability.md) | backend, infra |
| 17 | [testing.md](./17-testing.md) | all |
| 18 | [seed-and-demo.md](./18-seed-and-demo.md) | all |
| 19 | [delivery-and-scope.md](./19-delivery-and-scope.md) | all, PM |
| 20 | [production-tradeoffs.md](./20-production-tradeoffs.md) | all, PM |
| 21 | [21-deployment-and-operations.md](./21-deployment-and-operations.md) | backend, infra — pinned constants, queues, crons, IAM, CORS, runbook |
| A | [appendix-a-review-findings.md](./appendix-a-review-findings.md) | reviewers (v1 → v2) |
| B | [appendix-b-handoff-audit.md](./appendix-b-handoff-audit.md) | reviewers (v2 → v2.1 pre-handoff audit) |

---

## Global conventions (normative — every doc assumes these)

These are stated once here and referenced everywhere. Where a doc repeats a rule, this README wins on conflict.

**Money.** All monetary values are **integer minor units (US cents)** stored as `int`. No floating point anywhere in the money path. Every money-bearing document carries a `currency` field fixed to `"USD"` for the MVP (single-currency assumption is explicit; see [07](./07-financial-rules.md)).

**Time & timezone.** All timestamps are Firestore `Timestamp` (UTC instants). A single configured **`SYSTEM_TIMEZONE`** (default `America/New_York`) is the business calendar used for (a) deriving calendar periods (`YYYY-MM`) for schedules and monthly aggregates and (b) computing `scheduledDate`. `scheduledDate` is set to **12:00 in `SYSTEM_TIMEZONE`** on the due day, to avoid midnight/DST edge cases. Never derive a period from a raw UTC date in one place and render it in local time in another.

**Common document fields.** Every top-level document includes:

| Field | Type | Meaning |
|-------|------|---------|
| `createdAt` | Timestamp | server time of creation |
| `updatedAt` | Timestamp | server time of last material write |
| `createdBy` | string | actor id (Firebase uid or `system:<job>`) |
| `updatedBy` | string | actor id of last material write |
| `revision` | int | increments on each material update; **audit counter only** (see below) |
| `schemaVersion` | int | document schema version, for migrations |

> **Change from v1 — `version` → `revision` + `expectedRevision`.** v1's `version` field was described as "may be used for optimistic concurrency." Backend-only writes already run inside Firestore transactions, which provide serializable isolation and automatic retry, so a version precondition adds nothing *between backend writers*. In v2, `revision` is a pure monotonic audit counter. The one legitimate concurrency use — rejecting a command issued from a **stale UI** — is handled explicitly by an optional `expectedRevision` on mutating API calls (see [11](./11-api.md)); it is not implied by the field's presence.

**Identifiers.** Deterministic IDs are used wherever "create exactly once" matters, so a duplicate create fails on a document-existence precondition rather than racing. Canonical formats:

| Entity | ID format | Example |
|--------|-----------|---------|
| Scheduled contribution | `{agreementId}__{installmentNumber:03d}` | `ben_jordan_001__004` |
| Payment attempt | `{contributionId}__att_{attemptNumber:03d}` | `ben_jordan_001__004__att_002` |
| Auto exception | `{entityId}__{exceptionType}` | `ben_jordan_001__004__PAYMENT_FAILED` |
| Idempotency key | client-supplied key (verbatim) | `process-ben_jordan_001__004-a1b2` |
| Monthly summary bucket | `{YYYY-MM}` (in `SYSTEM_TIMEZONE`) | `2026-07` |

> **Change from v1 — contribution ID.** v1 used `{agreementId}_{YYYY_MM}`, which bakes in one-contribution-per-calendar-month and is timezone-ambiguous. v2 keys on **installment number** (1..`termMonths`), which is cadence-agnostic, collision-free, and timezone-independent; the calendar period lives in the `scheduledDate` field and monthly reconciliation is a range query on that field. See [04](./04-firestore-data-model.md).

**Roles.** `OPERATIONS_USER`, `SERVICING_MANAGER`, `ADMINISTRATOR`. Authoritative source is Firebase **custom claims**; see [12](./12-auth-and-security.md).

**Events.** Every material state change appends an immutable `servicingEvent`. Events are never updated or deleted. Denormalized values inside events are **frozen point-in-time snapshots** (correct by design), unlike denormalized values in live read models (which must be kept fresh — see [04](./04-firestore-data-model.md)).

---

## What changed from the v1 spec (summary)

v2 keeps the v1 architecture and intent intact — it closes the recovery/contention/consistency edges the v1 draft named but did not fully specify. The four blocking items:

1. **Payment crash-recovery** — a reconciliation sweeper + attempt-key state-query re-drive closes the "stuck in `PROCESSING`, money moved, balances not updated" gap. See [08](./08-idempotency-and-consistency.md), [09](./09-payment-processing.md).
2. **Idempotency correctness** — the idempotency record is created *inside* the state-transition transaction with a create-precondition, and in-progress keys carry a **lease** so a dead request can't wedge retries forever. See [08](./08-idempotency-and-consistency.md).
3. **Monetary residual** — installment amounts are solved at schedule generation so `Σ(installments) == totalCommitment` exactly; removes the systemic under-disbursement and the "benefit never completes" bug, and the corrected §14 example. See [07](./07-financial-rules.md), [10](./10-benefit-and-employment-workflows.md).
4. **Read authorization** — role via custom claims enforced in *both* Firestore security rules (reads) and Django (writes); `users/{uid}` self-write denied. Closes the "any authenticated user reads all PII" exposure. See [12](./12-auth-and-security.md).

Plus hot-document/summary redesign, period-bucketed counters, deterministic exception IDs, in-flight-payment vs termination resolution, the corrected/expanded index set, added Cloud Scheduler, event ordering (`sequence`), and several editorial fixes. The full traceability matrix — every finding, its severity, and where it is resolved — is in [Appendix A](./appendix-a-review-findings.md).
