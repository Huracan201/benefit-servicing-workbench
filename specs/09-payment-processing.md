# 09 — Payment Processing Workflows

Depends on [06 state machines](./06-state-machines.md), [07 financial rules](./07-financial-rules.md), [08 idempotency & consistency](./08-idempotency-and-consistency.md). This is the highest-risk area; read those three first.

## 9.1 Process a scheduled contribution

**Trigger:** manual (`SERVICING_MANAGER` clicks "Process") or scheduled (Cloud Scheduler enqueues due contributions — [14](./14-async-and-background-jobs.md)).
**Endpoint:** `POST /contributions/{contributionId}/process` (requires `Idempotency-Key`).
**Eligibility:** contribution status ∈ {`SCHEDULED`, `RETRY_PENDING`}; benefit `acceptingPayments == true`; loan status ∈ {`ACTIVE`, `DELINQUENT`} (an employer contribution paying down a delinquent loan is desirable, not blocked — `PAID_OFF`/`CLOSED` block); `scheduledDate` reached (for scheduled runs).

**Phase 1 — begin (one transaction):**
1. Idempotency create-or-replay (per [08 §8.2](./08-idempotency-and-consistency.md)).
2. Read contribution; assert status ∈ {SCHEDULED, RETRY_PENDING} and benefit `acceptingPayments`.
3. Compute `attemptNumber = attemptCount + 1`; derive `processorIdempotencyKey = pay_{contributionId}_att_{attemptNumber:03d}`.
4. Writes: contribution → `PROCESSING` (`attemptCount++`, `currentAttemptId`, `lastAttemptAt`); create attempt (`STARTED`); write `PAYMENT_PROCESSING` event; idempotency `PENDING`.
5. Commit.

**Phase 2 — adapter call (no transaction):** call the payment adapter's `charge` with `processorIdempotencyKey` and `requestedAmountCents`. The adapter returns success (with `processorReference`) or a typed failure (`failureCode`, `failureReason`).

**Phase 3 — finalize (one transaction):**

*Both branches — the finalize guard (normative, identical on success and failure):* re-read the contribution and the attempt; assert contribution still `PROCESSING` **with this `currentAttemptId`**, and assert the attempt is still `STARTED`. A stale driver whose attempt was superseded (sweeper reverted and a new attempt started) fails this guard and must abort without writing — otherwise it can mark the contribution `FAILED` while a *newer* attempt is live at the processor, stranding a successful charge invisibly (the sweeper's stale-`STARTED` scan is the backstop, [08 §8.4](./08-idempotency-and-consistency.md)). Terminal attempts are immutable ([06 §6.2](./06-state-machines.md)); no finalize may overwrite one.

*Success:*
- Compute `postedAmountCents = min(scheduledAmountCents, loan.currentBalanceCents, agreement.remainingCommitmentCents)` (I4/§7.4).
- contribution → `POSTED` (`postedAt`, `postedAmountCents`); attempt → `SUCCEEDED` (`processorReference`, `completedAt`).
- `loan.currentBalanceCents −= postedAmountCents` (→ `PAID_OFF` if 0); `agreement.amountPaidCents += postedAmountCents`, `remainingCommitmentCents` co-updated (→ `COMPLETED` if 0, or on payoff per §7.4 — from `SUSPENDED` too, [06 §6.3](./06-state-machines.md)).
- Recompute the loan look-ahead: read the next `SCHEDULED` installment (via `benefitAgreementId + installmentNumber`, **in the transaction's read set, before writes** — [08 §8.1](./08-idempotency-and-consistency.md)) and set `loan.nextContributionDate/AmountCents` (null when none).
- If `contribution.currentExceptionId` set, resolve that exception → `RESOLVED` (`resolution.resolvedByEvent`).
- Write `PAYMENT_POSTED` (and `LOAN_BALANCE_UPDATED`, `BENEFIT_COMPLETED` if applicable) events — payment-event `metadata` includes `periodLabel` for period attribution ([05 §5.3](./05-read-models-and-projections.md)); idempotency → `COMPLETED` with the success result.
- Enqueue projection update for affected summaries (out of band — [05](./05-read-models-and-projections.md)).

*Failure:*
- If the agreement is `TERMINATED` (`acceptingPayments == false` *because of termination*, not suspension): route to **settle-then-cancel** — contribution → `CANCELED`, attempt → `FAILED`; **suppress** creating a new exception and dismiss a pre-existing `currentExceptionId` with reason "benefit terminated" ([10 §10.4](./10-benefit-and-employment-workflows.md)). Under mere `SUSPENDED`, fall through to the normal failure path below (the installment is recoverable after resume).
- contribution → `FAILED` (`failureCode`, `failureReason`); attempt → `FAILED`.
- **Balances unchanged** (I: failed attempts never move money).
- Upsert the operational exception at deterministic id `{contributionId}__PAYMENT_FAILED` (`occurrenceCount++`, `lastSeenAt`), set `contribution.currentExceptionId`; include the loan doc in the transaction to maintain `loan.openExceptionCount`.
- Write `PAYMENT_FAILED` event (metadata includes `periodLabel`); idempotency → `COMPLETED` with the failure result (a *completed* command whose business outcome is failure — distinct from an infrastructure `FAILED` idempotency status).

**Acceptance criteria**
- Duplicate requests (same key) cannot post twice — replay returns the prior result.
- A `POSTED` contribution cannot be processed again (`409 INVALID_TRANSITION`).
- Loan + benefit balance updates are atomic (one transaction) or not applied at all.
- A failed attempt never changes any balance.
- Every attempt is recorded in the attempts subcollection and is auditable.
- A crash between Phase 2 and Phase 3 is recovered by reconciliation (§9.4) with no double charge and no lost posting.

## 9.2 Retry a failed contribution

**Trigger:** `OPERATIONS_USER`+ opens the `PAYMENT_FAILED` exception → "Schedule Retry".
**Endpoint:** `POST /contributions/{contributionId}/retry` (requires `Idempotency-Key`).
**Preconditions:** contribution `FAILED`; benefit `acceptingPayments == true` (ACTIVE); employment `ACTIVE`; loan `ACTIVE`.

1. Transaction: assert preconditions; contribution `FAILED` → `RETRY_PENDING`; write `PAYMENT_RETRY_SCHEDULED` event; idempotency COMPLETED.
2. Retry processing is then triggered manually or by enqueuing a `process` task ([14](./14-async-and-background-jobs.md)). Processing reuses the `process` workflow (§9.1), which creates a **new attempt** (attemptNumber incremented) — prior attempts are preserved.

**Acceptance criteria**
- `POSTED` and `CANCELED` contributions cannot be retried (`409 INVALID_TRANSITION`).
- Retry is idempotent (same key ⇒ one transition).
- Attempt history is preserved (new attempt appended; old attempts immutable).
- A successful retry resolves the **same** exception via `currentExceptionId` (no duplicate exceptions — [04 §4.10](./04-firestore-data-model.md)).

## 9.3 Exception coupling (no duplicates, deterministic resolution)

- On failure, upsert exception at `{contributionId}__PAYMENT_FAILED`; store its id on the contribution (`currentExceptionId`).
- On the next failure, the **same** exception row is upserted (`occurrenceCount++`), not a new one.
- On a successful retry, resolve exactly `currentExceptionId`; clear it on the contribution.
- On cancel of a `FAILED` contribution, resolve/dismiss `currentExceptionId` too (so cancelling never orphans an open exception).

> **Change from v1 — resolution is pointer-based, not query-based.** v1 had to *query* for "the related open exception" and created a new exception per failure, producing duplicates and ambiguous resolution. v2's deterministic id + `currentExceptionId` pointer makes it exact and query-free.

## 9.4 Reconciliation sweeper (stuck-PROCESSING recovery)

Runs on a schedule ([14](./14-async-and-background-jobs.md)). Two scans: (a) contributions `PROCESSING` with `lastAttemptAt < now − STUCK_THRESHOLD`; (b) attempts still `STARTED` older than `STUCK_THRESHOLD` regardless of contribution status (catches a mis-finalized stale driver). For each, query the adapter with the attempt's `processorIdempotencyKey` and finalize per [08 §8.4](./08-idempotency-and-consistency.md):

| Processor says | Action |
|----------------|--------|
| `SUCCEEDED` | run Phase-3 success (idempotent, guarded on `currentAttemptId` + attempt `STARTED`) → `POSTED` |
| `FAILED` | run Phase-3 failure → `FAILED` (+ exception; settle-then-cancel if agreement `TERMINATED`) |
| `NOT_FOUND` (never charged — **key is now fenced** by the adapter, §9.5) | attempt → `FAILED(NOT_SUBMITTED)`. If agreement `TERMINATED` → contribution `CANCELED` (dismiss exception per §9.3 — never resurrect a zombie `SCHEDULED` on a terminated benefit); else revert contribution to its pre-processing state (`SCHEDULED`/`RETRY_PENDING`), clean for retry |
| `INDETERMINATE` (the `get_status` call itself failed — transport error/5xx) | increment `attempt.reconcileAttempts`; leave everything untouched. At `reconcileAttempts ≥ MAX_SWEEPS`, raise `PAYMENT_STUCK_PROCESSING` (`CRITICAL`) for human handling. **Never auto-cancel on indeterminate** — never guess about money |

The sweeper writes a `PAYMENT_RECONCILED` event recording what it found and did.

## 9.5 Payment adapter interface (simulated)

The adapter is swappable (`backend/payments/adapters/`). The simulator is the MVP implementation; the interface is what a real processor would implement.

```python
@dataclass
class ChargeResult:
    status: Literal["SUCCEEDED", "FAILED"]
    processor_reference: str | None          # set on SUCCEEDED
    failure_code: PaymentFailureCode | None  # set on FAILED
    failure_reason: str | None

@dataclass
class StatusResult:
    status: Literal["SUCCEEDED", "FAILED", "NOT_FOUND"]
    processor_reference: str | None
    failure_code: PaymentFailureCode | None
    failure_reason: str | None
    # INDETERMINATE is not a StatusResult value — it is the *call itself* raising
    # (timeout/5xx); callers treat the exception as indeterminate (§9.4).

class PaymentAdapter(Protocol):
    def charge(self, *, processor_idempotency_key: str, amount_cents: int,
               currency: str, metadata: dict) -> ChargeResult: ...
        # Idempotent: same key ⇒ same charge, never double-charges.
        # A key previously FENCED by get_status is rejected: FAILED(NOT_SUBMITTED).

    def get_status(self, *, processor_idempotency_key: str) -> StatusResult: ...
        # Returns the charge's outcome. On an UNKNOWN key it atomically records a
        # tombstone (FENCES the key) and returns NOT_FOUND — a later charge() with
        # that key must fail. This is what makes the sweeper's NOT_FOUND branch a
        # durable verdict rather than a double-charge race (08 §8.4).
```

**Failure-code taxonomy (closed enum `PaymentFailureCode`):**

| Code | Meaning | Retryability |
|------|---------|--------------|
| `SERVICER_UNAVAILABLE` | downstream servicer down/unreachable | transient — retry |
| `SERVICER_TIMEOUT` | downstream timed out | transient — retry |
| `INSUFFICIENT_FUNDS` | funding account short | retry later |
| `ACCOUNT_FROZEN` | account blocked at servicer | manual review |
| `INVALID_ACCOUNT` | bad loan/account reference | terminal — raise `BENEFIT_CONFIGURATION_ERROR` |
| `NOT_SUBMITTED` | charge never reached the processor (fenced key / reconciliation verdict) | system-internal |

**Simulator requirements (for demos & tests):**
- **Persistence:** charges are stored in Firestore at `simulatedCharges/{processorIdempotencyKey}` (client-invisible; deny-all rules like `idempotencyKeys`). An in-memory simulator would pass every local test and silently break the *deployed* reconciliation demo — the sweeper runs on a different Cloud Run instance than the one that charged.
- **Scripted outcomes:** deterministic, driven by the optional seed-only `contribution.simulatedOutcome` field passed through `charge(metadata=…)` — e.g. Liam Walsh's installment always returns `SERVICER_UNAVAILABLE` (drives [scenario 4](./18-seed-and-demo.md)). Default outcome is success.
- **Fencing + idempotency honored** exactly as the contract above (replayed `charge` returns the same result; `get_status` on unknown keys tombstones).
- A configurable **"crash after charge, before finalize"** toggle to exercise the reconciliation path in tests ([17 §17.2](./17-testing.md)).

> **Change from v1 — the adapter must expose `get_status`, and `get_status` must fence.** v1's adapter only produced success/failure outcomes; reconciliation is impossible without a status endpoint keyed by the same idempotency id, and unsafe without the tombstone semantics. A real processor that cannot fence cannot be made safe under this pattern.
