# 06 — State Machines

State machines are the backbone of financial-state safety. They are implemented once in `backend/common/state_machines.py` and used by every command and task handler. **The API never accepts a raw target status**; it accepts business commands, and the command layer computes and validates the transition.

Every transition is executed inside a Firestore transaction with a **precondition on the current status** (read the document in the transaction; if its status is not the expected source state, abort). This precondition is also the mechanism that resolves concurrent races — see §6.7.

## 6.1 Scheduled contribution

The core financial state machine.

```
        ┌──────────────────────────────────────────────────────┐
        │                                                       │
   SCHEDULED ──process──► PROCESSING ──success──► POSTED (terminal)
        │                    │  │
        │                    │  └──failure──► FAILED
        │                    │                  │
        │                    │                  ├──retry──► RETRY_PENDING ──process──► PROCESSING
        │                    │                  │                 │
        │                    │                  │                 └──cancel──► CANCELED (terminal)
        │                    │                  └──cancel──► CANCELED (terminal)
        │                    └──settle-then-cancel──► CANCELED (terminal)   [after settle, benefit TERMINATED]
        │
        └──cancel──► CANCELED (terminal)
```

**Allowed transitions:**

| From | To | Command / trigger |
|------|----|--------------------|
| `SCHEDULED` | `PROCESSING` | process contribution |
| `SCHEDULED` | `CANCELED` | cancel (termination cascade) |
| `PROCESSING` | `POSTED` | finalize success |
| `PROCESSING` | `FAILED` | finalize failure |
| `PROCESSING` | `CANCELED` | **settle-then-cancel** — only after the in-flight attempt's adapter outcome is known and it did **not** post; reached via the failure-finalize path when the agreement is `TERMINATED` ([09 §9.1](./09-payment-processing.md)) or via reconciliation ([09 §9.4](./09-payment-processing.md)) |
| `FAILED` | `RETRY_PENDING` | schedule retry |
| `FAILED` | `CANCELED` | cancel |
| `RETRY_PENDING` | `PROCESSING` | process (retry) |
| `RETRY_PENDING` | `CANCELED` | cancel |

**Disallowed (rejected with `409 INVALID_TRANSITION`):** `POSTED → *` (POSTED is terminal and immutable), `CANCELED → *`, `FAILED → POSTED`, `SCHEDULED → POSTED` (must pass through PROCESSING), any transition into `PROCESSING` except from `SCHEDULED`/`RETRY_PENDING`.

> **Change from v1 — `PROCESSING → CANCELED` added, settle-first-only.** v1 disallowed cancelling a PROCESSING contribution, which left a contribution that was in-flight during an employment termination with no legal resolution (it could neither be cancelled nor safely left). v2 permits `PROCESSING → CANCELED` **only after the attempt has settled without posting** — the transition is executed by whichever actor learns that outcome first: the normal Phase-3 failure-finalize when the agreement is `TERMINATED`, or the reconciliation sweeper (`FAILED`/fenced-`NOT_FOUND` verdicts). A user or termination command can never directly cancel a PROCESSING contribution; termination marks intent (`TERMINATED` + `acceptingPayments=false`) and the settle path routes it. This closes the gap without ever cancelling a payment that actually moved money. The guard keys on **agreement status `TERMINATED`**, not on `acceptingPayments` alone — the flag is also false under `SUSPENDED`, where a failed installment must remain recoverable (`FAILED` + exception, retryable after resume).

## 6.2 Payment attempt

```
STARTED ──► SUCCEEDED (terminal)
STARTED ──► FAILED (terminal)
```

`STARTED` is the only non-terminal state. An attempt is resolved to a terminal state either by normal finalization or by the reconciliation sweeper querying the processor. A contribution has **at most one `STARTED` attempt at a time** (enforced: a new attempt can only be created when `currentAttemptId` is null or points to a terminal attempt).

## 6.3 Benefit agreement

```
DRAFT ──► PENDING ──activate──► ACTIVATING ──schedule generated──► ACTIVE
                                    │                                │
                                    │                       ┌────────┼─────────┐
                                    │                    suspend  terminate  complete
                                    │                       │        │         │
                                    ▼                       ▼        ▼         ▼
                              (resume/rollback)          SUSPENDED  TERMINATED  COMPLETED
                                                            │         (terminal) (terminal)
                                                       ┌────┴────┐
                                                    resume    terminate
                                                       │         │
                                                       ▼         ▼
                                                     ACTIVE   TERMINATED
```

| From | To | Trigger |
|------|----|---------|
| `DRAFT` | `PENDING` | ready for activation — **seed-only in MVP** (no create/promote commands exist; entities enter the system via the seed script) |
| `PENDING` | `ACTIVATING` | activate accepted (schedule generation begins) |
| `ACTIVATING` | `ACTIVE` | schedule fully generated |
| `ACTIVATING` | `PENDING` | generation abandoned after terminal task failure (dead-letter handler or admin re-activate; already-created installments are **retained and reused** — create-preconditions make regeneration a no-op, [10 §10.1](./10-benefit-and-employment-workflows.md)) |
| `ACTIVATING` | `TERMINATED` | employment terminated mid-generation; the generation task checks agreement status per batch and halts ([10 §10.4](./10-benefit-and-employment-workflows.md)) |
| `ACTIVE` | `SUSPENDED` | suspend (employment LEAVE, or manual) — records `suspendedReason: LEAVE \| MANUAL` |
| `ACTIVE`/`SUSPENDED` | `TERMINATED` | terminate (employment TERMINATED, or manual) |
| `SUSPENDED` | `ACTIVE` | resume (manual; or automatic return-from-leave **only when `suspendedReason == LEAVE`**) — triggers the schedule shift ([10 §10.2](./10-benefit-and-employment-workflows.md)) |
| `ACTIVE` | `COMPLETED` | final installment posted / `remainingCommitmentCents == 0`, or loan payoff (§7.4) |
| `SUSPENDED` | `COMPLETED` | loan payoff settles while suspended — an in-flight attempt posting the payoff must be able to complete the agreement ([09 §9.1](./09-payment-processing.md)) |

`COMPLETED` and `TERMINATED` are terminal. See [10](./10-benefit-and-employment-workflows.md) for the commands.

## 6.4 Operational exception

```
OPEN ──mark-in-review──► IN_REVIEW ──resolve──► RESOLVED (terminal)
  │                          └──dismiss──► DISMISSED (terminal)
  ├──resolve (incl. auto, on retry success)──► RESOLVED
  └──dismiss──► DISMISSED
```

`RESOLVED` and `DISMISSED` are terminal. **Assignment is status-neutral**: `assignedTo` is a field change (set via the assign command, cleared via `assignToUid: null`), not a status transition — an exception can be assigned while `OPEN` or `IN_REVIEW`; only the explicit `mark-in-review` command moves status. Auto-resolution (a successful retry) moves the exception directly to `RESOLVED` with `resolution.resolvedByEvent` referencing the `PAYMENT_POSTED` event.

## 6.5 Borrower employment

```
PENDING ──► ACTIVE ──► LEAVE ──► ACTIVE
                 │        │
                 └────────┴──► TERMINATED (terminal for this employment record)
```

Employment transitions drive benefit-status cascades (LEAVE → suspend, TERMINATED → terminate) — see [10](./10-benefit-and-employment-workflows.md).

## 6.6 Loan

```
ACTIVE ──► PAID_OFF (terminal)      # balance reaches 0
ACTIVE ──► DELINQUENT ──► ACTIVE     # informational in MVP (no collections)
ACTIVE/PAID_OFF ──► CLOSED (terminal)
```

Loan status is largely informational in the MVP (no interest, no collections). `PAID_OFF` is set when `currentBalanceCents` reaches 0 as a result of a posted contribution. `DELINQUENT` and `CLOSED` have **no automated trigger in the MVP** — they are set by seed data or a future servicer-sync/admin command (stated so implementers don't hunt for one). **`DELINQUENT` does not block contribution processing** ([09 §9.1](./09-payment-processing.md)) — an employer benefit paying down a delinquent loan is desirable; `PAID_OFF`/`CLOSED` do block.

## 6.6a Employer

```
ACTIVE ⇄ INACTIVE     # set by ADMINISTRATOR command (11 §11.4)
```

`INACTIVE` gates **new benefit activations only** ([10 §10.1](./10-benefit-and-employment-workflows.md) preconditions): existing benefits continue to process — offboarding an employer is done explicitly by terminating its benefits, not by a silent payment freeze.

## 6.7 Concurrency & race resolution

All transitions use a **read-the-status-in-the-transaction precondition**, so concurrent commands on the same document serialize: the first to commit wins; the second's transaction sees a status it no longer expects and aborts with `409 INVALID_TRANSITION` (or is retried by the caller as appropriate). This is the single, uniform race-resolution mechanism.

**Retry-vs-cancel precedence.** A contribution in `RETRY_PENDING` can legally go to either `PROCESSING` (retry) or `CANCELED` (termination cascade). First-writer-wins alone does not encode the business intent that **termination should win**.

> **Change from v1 — explicit cancel-wins precedence for terminating benefits.** When a benefit enters `SUSPENDED`/`TERMINATED`, the command sets a `benefit.acceptingPayments = false` flag in the same transaction. The **process/retry transition additionally checks** `benefit.acceptingPayments == true` as a precondition. So even if a retry-process command races ahead of the cancel task, it aborts because the benefit is no longer accepting payments — termination deterministically wins, regardless of write order. The retry command returns `409 BENEFIT_NOT_ACCEPTING_PAYMENTS`; the contribution is left for the cancel task.

**Cascade idempotency.** Employment-driven benefit cascades ([10 §10.4](./10-benefit-and-employment-workflows.md)) are **idempotent no-ops when the benefit is already at or past the target state**: LEAVE onto an already-`SUSPENDED` benefit records the employment change and leaves the benefit (and its `suspendedReason`) untouched; termination onto an already-`TERMINATED` benefit skips the benefit step. A cascade never fails the employment command with `INVALID_TRANSITION` because the benefit happened to be ahead of it.

## 6.8 Invariants enforced at every transition

Independent of the specific transition, the command layer asserts the financial invariants in [07](./07-financial-rules.md) (balances never negative, `amountPaid` never exceeds commitment, posted amount within caps) *inside the same transaction* before committing. A transition that would violate an invariant is rejected even if the status transition itself is otherwise legal.
