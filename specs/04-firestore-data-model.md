# 04 — Firestore Data Model

Native-mode Firestore. This is the authoritative schema reference. Read models (summaries, workbench) are in [05](./05-read-models-and-projections.md). All documents carry the [common fields](./README.md#global-conventions-normative--every-doc-assumes-these).

## 4.1 Collections & subcollections

```
employers/{employerId}
borrowers/{borrowerId}
loans/{loanId}
  loans/{loanId}/notes/{noteId}
  loans/{loanId}/events/{eventId}          # mirror of loan-scoped servicing events
benefitAgreements/{agreementId}
scheduledContributions/{contributionId}
  scheduledContributions/{contributionId}/attempts/{attemptId}   # authoritative attempt store
servicingEvents/{eventId}                  # global, cross-entity audit stream
operationalExceptions/{exceptionId}
idempotencyKeys/{idempotencyKey}
simulatedCharges/{processorIdempotencyKey}   # payment-simulator ledger (09 §9.5); client-invisible
users/{uid}

# entity-scoped event mirrors (written in the same txn as the global event — §4.9)
borrowers/{borrowerId}/events/{eventId}

# read models (see doc 05)
portfolioSummaries/{docId}                 # docId ∈ { "current", "YYYY-MM" }
employerSummaries/{employerId}
  employerSummaries/{employerId}/periods/{YYYY-MM}
loanWorkbenches/{loanId}
```

> **Change from v1 — payment attempts live in exactly one place.** v1 declared both a top-level `paymentAttempts/{attemptId}` collection and a `scheduledContributions/{id}/attempts` subcollection. v2 makes the **subcollection authoritative** and drops the top-level collection: attempts are only ever accessed in the context of their contribution (per-contribution history, current attempt). If a global "all attempts" operational view is later needed, it becomes a projection, not a second source of truth.

## 4.2 Denormalization & propagation policy (read this before trusting any duplicated field)

We duplicate `borrowerName`, `employerName`, `benefitStatus`, `nextContributionDate`, etc. across documents to serve screens without joins. Every denormalized field falls into exactly one of two classes, and they are handled differently:

| Class | Where | Freshness rule |
|-------|-------|----------------|
| **Frozen snapshot** | inside `servicingEvents`, payment `attempts`, and any historical record | **Never updated.** The value is correct *as of that event*. A later name change does not rewrite history. |
| **Live mirror** | on `loans`, `benefitAgreements`, `scheduledContributions`, `operationalExceptions`, read models | **Kept fresh** by a propagation task when the source changes. |

> **Change from v1 — propagation is specified, not implied.** v1 duplicated names widely "to avoid joins" but named no mechanism to update copies and did not distinguish frozen vs live. v2: (a) name changes are rare, so live mirrors of `borrowerName`/`employerName` are refreshed by a bounded **`propagate-denormalized-field`** Cloud Task fanned out from the source update (see [14](./14-async-and-background-jobs.md)); brief staleness between the source write and fan-out completion is acceptable and documented. (b) `benefitStatus` and contribution look-ahead fields on the loan are updated **synchronously in the same transaction** as the benefit/contribution command that changes them (low document count, no fan-out needed). (c) Event copies are frozen by design and must never be touched by propagation.

## 4.3 Employer — `employers/{employerId}`

```jsonc
{
  "name": "Memorial Health",
  "industry": "Healthcare",
  "status": "ACTIVE",                       // ACTIVE | INACTIVE
  "programName": "Clinical Talent Loan Benefit",
  "currency": "USD",
  "totalCommitmentCents": 85000000,         // sum of agreement commitments
  // --- denormalized rollups: maintained by projections, NOT on the payment hot path ---
  "activeBorrowerCount": 14,
  "amountPaidCents": 21500000,
  "remainingCommitmentCents": 63500000,     // = total - paid, co-updated with amountPaid
  "createdAt": "<ts>", "updatedAt": "<ts>",
  "createdBy": "…", "updatedBy": "…", "revision": 7, "schemaVersion": 1
}
```

> **Change from v1 — employer rollups are projection-owned, never written inside a payment transaction.** `amountPaidCents`/`activeBorrowerCount` are hot counters for a large employer (thousands of borrowers posting in one monthly run → far above Firestore's ~1 write/sec/doc). Writing them inside the payment transaction makes summary contention abort the *authoritative payment*. In v2 these are updated **out of band** by the projection layer ([05](./05-read-models-and-projections.md)); the payment transaction touches only the loan, agreement, contribution, and event.

## 4.4 Borrower — `borrowers/{borrowerId}`

```jsonc
{
  "firstName": "Jordan", "lastName": "Lee", "displayName": "Jordan Lee",
  "email": "jordan.lee@example.com",
  "employerId": "emp_memorial",
  "employerName": "Memorial Health",        // live mirror
  "employmentStatus": "ACTIVE",             // PENDING | ACTIVE | LEAVE | TERMINATED
  "employmentStartDate": "<ts>",
  "employmentEndDate": null,
  "primaryLoanId": "loan_jordan_001",       // convenience only, nullable, NON-authoritative
  "primaryBenefitAgreementId": "ben_jordan_001",  // convenience only
  "createdAt": "<ts>", "updatedAt": "<ts>",
  "createdBy": "…", "updatedBy": "…", "revision": 3, "schemaVersion": 1
}
```

`primaryLoanId`/`primaryBenefitAgreementId` are the workbench-header shortcut only; the authoritative borrower→loan link is `loan.borrowerId` (query `loans where borrowerId ==`). See [03 §3.2](./03-domain-model.md#cardinality-decisions-mvp).

## 4.5 Loan — `loans/{loanId}`

```jsonc
{
  "borrowerId": "bor_jordan",
  "borrowerName": "Jordan Lee",             // live mirror
  "employerId": "emp_memorial",
  "employerName": "Memorial Health",        // live mirror
  "externalLoanReference": "LN-203945",
  "servicerName": "Demo Student Loan Servicer",
  "currency": "USD",
  "originalPrincipalCents": 8400000,
  "currentBalanceCents": 7142000,
  "interestRateBasisPoints": 625,           // informational/display only; no accrual in MVP
  "loanStatus": "ACTIVE",                    // ACTIVE | PAID_OFF | DELINQUENT | CLOSED
  "benefitAgreementId": "ben_jordan_001",
  "benefitStatus": "ACTIVE",                 // live mirror of agreement.status (txn-synced)
  "openExceptionCount": 1,                   // per-loan counter (low contention)
  "nextContributionDate": "<ts>",            // live look-ahead for portfolio row (txn-synced)
  "nextContributionAmountCents": 83333,
  "createdAt": "<ts>", "updatedAt": "<ts>",
  "createdBy": "…", "updatedBy": "…", "revision": 12, "schemaVersion": 1
}
```

> **Change from v1 — `benefitStatus` added and made a first-class synced field.** v1's proposed index `benefitStatus + nextContributionDate` referenced a field the loan doc did not define. v2 adds `benefitStatus` as a live mirror of `benefitAgreement.status`, updated **in the same transaction** as any benefit-status command (activation/suspend/terminate), and documents it as such so the portfolio filter and its index ([13](./13-firestore-indexes.md)) are servable.

## 4.6 Benefit agreement — `benefitAgreements/{agreementId}`

```jsonc
{
  "borrowerId": "bor_jordan", "borrowerName": "Jordan Lee",
  "employerId": "emp_memorial", "employerName": "Memorial Health",
  "loanId": "loan_jordan_001",
  "currency": "USD",
  "totalCommitmentCents": 3000000,
  "baseMonthlyContributionCents": 83333,     // nominal; actual per-installment amounts may differ (residual)
  "termMonths": 36,
  "startDate": "<ts>", "endDate": "<ts>",
  "amountPaidCents": 833330,
  "remainingCommitmentCents": 2166670,       // = total - paid, co-updated in txn
  "status": "ACTIVE",                        // DRAFT|PENDING|ACTIVATING|ACTIVE|SUSPENDED|COMPLETED|TERMINATED
  "acceptingPayments": true,                 // THE cancel-wins gate (06 §6.7): false until activation
                                             // finalize; false under SUSPENDED/TERMINATED; true on resume.
                                             // Checked by every process/retry precondition.
  "suspendedReason": null,                   // LEAVE | MANUAL | null — governs auto-resume (10 §10.4)
  "scheduleGenerated": true,
  "plannedInstallmentCount": 36,             // set at activation accept; generation target
  "installmentsGenerated": 36,               // progress marker for resumable generation
  "createdAt": "<ts>", "updatedAt": "<ts>",
  "createdBy": "…", "updatedBy": "…", "revision": 9, "schemaVersion": 1
}
```

> **Change from v1 — `baseMonthlyContributionCents` + explicit residual.** v1's `monthlyContributionCents` implied a flat amount whose 36× sum (2,999,988) did not equal `totalCommitmentCents` (3,000,000). v2 stores the *nominal* base amount here, but the **actual per-installment amounts are solved at generation** so they sum exactly to the commitment (installments 1–35 = 83,333; installment 36 = 83,345). See [07](./07-financial-rules.md) and [10](./10-benefit-and-employment-workflows.md). `installmentsGenerated` supports resumable async generation.

## 4.7 Scheduled contribution — `scheduledContributions/{contributionId}`

**Deterministic ID:** `{agreementId}__{installmentNumber:03d}` (e.g., `ben_jordan_001__004`).

```jsonc
{
  "benefitAgreementId": "ben_jordan_001",
  "installmentNumber": 4,                    // 1..termMonths
  "borrowerId": "bor_jordan", "borrowerName": "Jordan Lee",   // live mirror
  "employerId": "emp_memorial", "employerName": "Memorial Health",
  "loanId": "loan_jordan_001",
  "currency": "USD",
  "scheduledDate": "<ts>",                   // 12:00 SYSTEM_TIMEZONE on due day
  "periodLabel": "2026-08",                  // YYYY-MM in SYSTEM_TIMEZONE, for display/grouping
  "scheduledAmountCents": 83333,
  "status": "SCHEDULED",                     // SCHEDULED|PROCESSING|POSTED|FAILED|RETRY_PENDING|CANCELED
  "attemptCount": 0,
  "currentAttemptId": null,                  // points into ./attempts subcollection
  "currentExceptionId": null,                // deterministic exception id when failed; else null
  "lastAttemptAt": null,
  "postedAt": null,
  "postedAmountCents": null,                 // may be < scheduled on a balance-capped final installment
  "failureCode": null, "failureReason": null,
  "createdAt": "<ts>", "updatedAt": "<ts>",
  "createdBy": "…", "updatedBy": "…", "revision": 0, "schemaVersion": 1
}
```

> **Change from v1 — `currentExceptionId`, `installmentNumber`, `periodLabel`, `postedAmountCents`.** `currentExceptionId` lets a retry/cancel resolve *exactly* the right exception with no query and no duplicates (see [09](./09-payment-processing.md)). `installmentNumber` is the ID basis. `periodLabel` preserves the human "which month" without encoding it into the ID. `postedAmountCents` records the actual posted amount when a final installment is capped to remaining balance.

## 4.8 Payment attempt — `scheduledContributions/{contributionId}/attempts/{attemptId}`

**Deterministic ID:** `{contributionId}__att_{attemptNumber:03d}`.

```jsonc
{
  "contributionId": "ben_jordan_001__004",
  "loanId": "loan_jordan_001",
  "attemptNumber": 1,
  "processorIdempotencyKey": "pay_ben_jordan_001__004_att_001",  // sent to the payment adapter
  "commandIdempotencyKey": "process-ben_jordan_001__004-a1b2",   // the API-level key that drove it
  "status": "STARTED",                       // STARTED | SUCCEEDED | FAILED
  "reconcileAttempts": 0,                    // indeterminate-sweep counter (09 §9.4); STUCK at MAX_SWEEPS
  "requestedAmountCents": 83333,
  "processorReference": null,                // set when processor responds
  "failureCode": null, "failureReason": null,
  "startedAt": "<ts>", "completedAt": null
}
```

> **Change from v1 — two distinct idempotency keys.** The attempt carries **both** its own `processorIdempotencyKey` (sent to the payment adapter, so a re-drive *queries* the processor for the same charge instead of creating a new one) **and** the `commandIdempotencyKey` that initiated it (for tracing). This distinction is what makes crash-recovery safe — see [08](./08-idempotency-and-consistency.md) and [09](./09-payment-processing.md). The `processorIdempotencyKey` is deterministic from `(contributionId, attemptNumber)`, so reconstructing it during recovery is trivial.

## 4.9 Servicing event — `servicingEvents/{eventId}` (immutable, append-only)

```jsonc
{
  "eventType": "PAYMENT_POSTED",
  "entityType": "SCHEDULED_CONTRIBUTION",
  "entityId": "ben_jordan_001__004",
  "loanId": "loan_jordan_001",               // present for loan-scoped events; else null
  "borrowerId": "bor_jordan",
  "employerId": "emp_memorial",
  "benefitAgreementId": "ben_jordan_001",
  "actorType": "USER",                       // USER | SYSTEM
  "actorId": "firebase_user_123",
  "actorRole": "SERVICING_MANAGER",
  "actorName": "Alex Operator",              // frozen snapshot
  "correlationId": "req_4839",               // shared by all events of one command
  "sequence": 3,                             // monotonic within a correlationId, for stable ordering
  "metadata": { "amountCents": 83333, "previousStatus": "PROCESSING", "newStatus": "POSTED" },
  "createdAt": "<ts>"
}
```

**Mirroring rule.** Every event is written to the global `servicingEvents` collection. It is *additionally* mirrored, in the same transaction, to the most specific entity subcollection: `loans/{loanId}/events/{eventId}` if `loanId` is set; else `borrowers/{borrowerId}/events/{eventId}` if `borrowerId` is set; **else global-only, no mirror** (e.g. admin role changes, employer status changes).

**Canonical `eventType` enum (closed; extend here first):** `BENEFIT_ACTIVATION_STARTED`, `BENEFIT_ACTIVATED`, `BENEFIT_SUSPENDED`, `BENEFIT_RESUMED`, `BENEFIT_TERMINATED`, `BENEFIT_COMPLETED`, `SCHEDULE_SHIFTED`, `PAYMENT_PROCESSING`, `PAYMENT_POSTED`, `PAYMENT_FAILED`, `PAYMENT_RETRY_SCHEDULED`, `PAYMENT_CANCELED`, `PAYMENT_RECONCILED`, `FUTURE_CONTRIBUTIONS_CANCELED`, `LOAN_BALANCE_UPDATED`, `EMPLOYMENT_STATUS_CHANGED`, `EXCEPTION_CREATED`, `EXCEPTION_RESOLVED`, `EXCEPTION_DISMISSED`, `MANUAL_NOTE_ADDED`, `USER_ROLE_CHANGED`, `EMPLOYER_STATUS_CHANGED`. Payment events carry `periodLabel` in `metadata` for period attribution ([05 §5.3](./05-read-models-and-projections.md)).

> **Change from v1 — `sequence` + `actorRole` + defined mirror target for loan-less events.** (a) Two events written in one transaction can share `createdAt`; ordering by timestamp alone is unstable. `sequence` (assigned 1,2,3… by the command) is the tiebreaker for the audit timeline. (b) `actorRole` is captured for audit ([16](./16-observability.md)). (c) v1's dual-write assumed a `loanId` always exists; borrower-scoped events (employment changes) now mirror to the borrower subcollection.

## 4.10 Operational exception — `operationalExceptions/{exceptionId}`

**ID:** auto-generated exceptions use the deterministic form `{entityId}__{exceptionType}` (e.g., `ben_jordan_001__004__PAYMENT_FAILED`); manually-created exceptions use auto-IDs.

```jsonc
{
  "exceptionType": "PAYMENT_FAILED",         // see enum below
  "severity": "HIGH",                        // LOW | MEDIUM | HIGH | CRITICAL (display)
  "severityRank": 30,                        // LOW=10 MEDIUM=20 HIGH=30 CRITICAL=40; numeric, sortable — see doc 13
  "entityType": "SCHEDULED_CONTRIBUTION",
  "entityId": "ben_jordan_001__004",
  "loanId": "loan_jordan_001",
  "borrowerId": "bor_jordan", "borrowerName": "Jordan Lee",   // live mirror
  "employerId": "emp_memorial", "employerName": "Memorial Health",
  "status": "OPEN",                          // OPEN | IN_REVIEW | RESOLVED | DISMISSED
  "assignedTo": null,                        // firebase uid
  "occurrenceCount": 1,                      // incremented on repeat of the same failure
  "firstSeenAt": "<ts>", "lastSeenAt": "<ts>",
  "summary": "Employer contribution failed",
  "details": "Simulated loan-servicer timeout",
  "resolution": null,                        // { resolvedBy, note, resolvedByEvent }
  "createdAt": "<ts>", "updatedAt": "<ts>",
  "resolvedAt": null
}
```

**Exception types → default severity (closed map; auto-created exceptions use it, manual creation may override):**

| Type | Severity | Raised by |
|------|----------|-----------|
| `PAYMENT_FAILED` | HIGH | payment failure finalize |
| `PAYMENT_STUCK_PROCESSING` | CRITICAL | reconciliation sweeper after `MAX_SWEEPS` |
| `LOAN_BALANCE_MISMATCH` | HIGH | (future) servicer sync |
| `TASK_FAILED` | HIGH | dead-letter handler ([14 §14.5](./14-async-and-background-jobs.md)) |
| `SERVICER_SYNC_FAILURE` | MEDIUM | (simulated) sync job |
| `EMPLOYMENT_VERIFICATION_REQUIRED` | MEDIUM | manual (`POST /exceptions`) |
| `BENEFIT_CONFIGURATION_ERROR` | MEDIUM | validation / `INVALID_ACCOUNT` failure |

> **Change from v1 — deterministic IDs + `occurrenceCount` (idempotent upsert).** With opaque IDs, each failure created a *new* exception, so fail→retry→fail left multiple OPEN `PAYMENT_FAILED` exceptions for one contribution and "resolve the related exception" was ambiguous. v2 upserts the exception at the deterministic ID `{entityId}__{exceptionType}`: a repeat failure increments `occurrenceCount` and bumps `lastSeenAt` on the **same** row; a successful retry or a cancel resolves that exact row via `contribution.currentExceptionId`. No duplicates, no query.

## 4.11 Idempotency record — `idempotencyKeys/{idempotencyKey}`

**ID:** the client-supplied `Idempotency-Key` header value, verbatim.

```jsonc
{
  "operation": "PROCESS_CONTRIBUTION",
  "requestHash": "sha256:…",                 // hash of normalized request body
  "status": "PENDING",                       // PENDING | COMPLETED | FAILED
  "entityType": "SCHEDULED_CONTRIBUTION",
  "entityId": "ben_jordan_001__004",
  "leaseOwner": "run_7c3e…",                 // id of the process/attempt holding the lease
  "leaseExpiresAt": "<ts>",                  // PENDING records only; enables reclamation
  "result": null,                            // set on COMPLETED: prior response to replay
  "createdAt": "<ts>", "updatedAt": "<ts>",
  "completedAt": null,
  "expiresAt": "<ts>"                         // retention TTL (Firestore TTL policy)
}
```

> **Change from v1 — `leaseOwner` + `leaseExpiresAt`.** v1 had no lease on in-progress keys, so a request that died mid-flight left the key `PENDING` forever and a well-behaved client reusing the same key got "in progress" on every retry — permanently wedged. v2 adds a lease: a `PENDING` record whose `leaseExpiresAt` has passed is presumed abandoned and may be reclaimed (or resolved by the reconciliation sweeper). The full lifecycle and client contract are in [08](./08-idempotency-and-consistency.md).

## 4.12 User — `users/{uid}`

```jsonc
{
  "uid": "firebase_user_123",
  "email": "alex@demo.example",
  "displayName": "Alex Operator",
  "role": "SERVICING_MANAGER",               // MIRROR of the custom claim; claim is authoritative
  "status": "ACTIVE",                        // ACTIVE | DISABLED
  "createdAt": "<ts>", "updatedAt": "<ts>",
  "createdBy": "…", "updatedBy": "…", "revision": 1, "schemaVersion": 1
}
```

The authoritative role is the Firebase **custom claim**; this doc mirrors it for display/admin listing. Security rules **deny client writes to `users/{uid}`** (including self-write) so a user cannot escalate their own role by editing this doc — role changes go through an admin command that sets the claim *and* updates this mirror. See [12](./12-auth-and-security.md).

## 4.12a Common-field exemptions & seed-only fields

The [README common fields](./README.md) apply to the **command-owned entity collections** (employers, borrowers, loans, benefitAgreements, scheduledContributions). Exempt: `servicingEvents` (immutable — has `createdAt` only), payment `attempts` (append-only, own lifecycle fields), `operationalExceptions` and `idempotencyKeys` (own lifecycle timestamps; no `revision`), and read models (derived; `updatedAt` only). Seed-only field: `scheduledContributions.simulatedOutcome` (optional; drives the payment simulator — [09 §9.5](./09-payment-processing.md); never read by domain logic).

## 4.13 TTL & retention

| Collection | Retention |
|------------|-----------|
| `idempotencyKeys` | Firestore TTL on `expiresAt` (e.g., 30 days after `completedAt`); long enough to absorb client retries, short enough to bound growth. |
| `servicingEvents` | Retained indefinitely in MVP (audit). Production would archive to a warehouse — [20](./20-production-tradeoffs.md). |
| read models | No TTL; rebuilt by projections. |
