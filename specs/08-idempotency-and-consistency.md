# 08 — Idempotency & Consistency

This document defines the cross-cutting machinery that makes commands safe to retry and makes the two-phase payment recoverable. It is referenced by every command in [09](./09-payment-processing.md) and [10](./10-benefit-and-employment-workflows.md).

## 8.1 Firestore transaction constraints (design within these)

| Constraint | Consequence for us |
|-----------|--------------------|
| All reads must precede all writes in a transaction. | A handler cannot read a document it just wrote; compute everything from pre-read state. Never pull a hot summary doc into the read set just to update it (that's why aggregates are out of band — [05](./05-read-models-and-projections.md)). |
| A transaction aborts/retries if any document it read changed before commit. | Keep the read set minimal and low-contention. This is also our race-resolution primitive ([06 §6.7](./06-state-machines.md)). |
| ≤ 500 writes per transaction/committed batch. | Bound every multi-write operation; fan out beyond ~450 writes into batched async work ([14](./14-async-and-background-jobs.md)). |
| Transactions are not distributed 2-phase commit across external systems. | The payment adapter call cannot be inside the transaction; hence the two-phase pattern and reconciliation (§8.4). |

## 8.2 Idempotency record lifecycle

Every mutating command requires an `Idempotency-Key` header ([11](./11-api.md)). The key's document (`idempotencyKeys/{key}`, [04 §4.11](./04-firestore-data-model.md)) is created and resolved as follows.

**Create — inside the state-transition transaction, with a create-precondition:**

```
TRANSACTION open:
  read idempotencyKeys/{key}
  if exists:
     COMPLETED  and requestHash matches → return stored result   (replay; commit nothing)
     COMPLETED  and requestHash differs → 409 IDEMPOTENCY_KEY_REUSED
     PENDING    and lease still valid   → 202 IN_PROGRESS (client polls)
     PENDING    and lease expired       → treat as reclaimable (see §8.3)
     FAILED     and requestHash matches → allow a fresh attempt (overwrite to PENDING)
     FAILED     and requestHash differs → 409 IDEMPOTENCY_KEY_REUSED   (same rule as COMPLETED)
  read the target entity; assert current status precondition
  # --- all reads done; now writes ---
  create idempotencyKeys/{key} = { status: PENDING, requestHash, leaseOwner, leaseExpiresAt=now+LEASE_TTL, … }
  write the state transition (e.g. contribution SCHEDULED→PROCESSING)
  write the paymentAttempt (STARTED) / servicingEvent(s)
COMMIT   # idempotency record + state change commit atomically, or not at all
```

> **Change from v1 — the idempotency record is created *inside* the transition transaction.** v1 created it as a separate step *before* the transaction, opening (a) a race window (two concurrent same-key requests both proceed if the create wasn't a create-if-absent) and (b) an orphan window (crash between the two steps). In v2 the create uses a **document-existence precondition inside the same transaction** as the state change: the first request wins atomically; the second's transaction fails the precondition and returns the in-progress/replay response. There is exactly one authoritative "did this operation start" fact.

**Complete:** when the operation finishes (for the two-phase payment, in the finalization transaction), set `status = COMPLETED`, `result = <response to replay>`, `completedAt`, and set `expiresAt` for TTL retention. On unrecoverable business failure set `status = FAILED` (a distinct, retryable outcome from a *completed failure* like a declined payment, which is `COMPLETED` with a failure result).

**`requestHash` definition (normative).** `requestHash = SHA-256( HTTP method + "\n" + request path + "\n" + canonical JSON of the body, or "" if none )`. Including the **path** is essential: several commands (e.g. `process`) have empty bodies, and a body-only hash would let the same key replay against a *different* contribution. The stored `entityId` is asserted against the target as a second guard.

## 8.3 The lease (why an in-progress key can't wedge forever)

A `PENDING` record carries `leaseOwner` and `leaseExpiresAt`. If a request dies after creating the record but before completing it, the lease expires. A later same-key retry (or the `reap-expired-leases` job) that finds a `PENDING` record with an **expired lease** may reclaim it: re-establish ownership (new `leaseOwner`, extended `leaseExpiresAt`) inside a transaction and drive the operation to completion — using the deterministic downstream IDs (attempt id, event ids) so re-driving is itself idempotent. Reclamation always uses `get_status`, never a fresh `charge`. `LEASE_TTL` must exceed the Phase-2 adapter timeout plus margin ([21](./21-deployment-and-operations.md)) so a live driver and a reclaimer cannot overlap.

**Scope of the lease per operation shape.** The lease protects the **synchronous phase** of a command. For the synchronous two-phase payment, that is the whole operation. For **async commands** (activation, termination cascade) the idempotency record stays `PENDING` while the Cloud Task works — that is healthy, not abandoned. The lease is therefore extended at **acceptance** to a generous `ASYNC_LEASE_TTL`, and recovery is owned by the task layer, not the reaper:

| Operation | Expired-lease reclamation action |
|-----------|----------------------------------|
| `PROCESS_CONTRIBUTION` | run reconciliation for the in-flight attempt (`get_status` → finalize), then complete the record |
| `ACTIVATE_BENEFIT` | verify task progress via `agreement.installmentsGenerated`; re-enqueue `generate-schedule` if no task is live (idempotent — deterministic IDs make redelivery a no-op) |
| `TERMINATE_BENEFIT` / `EMPLOYMENT_CHANGE` | re-enqueue `cancel-future-contributions`; status-guarded transitions make it safe |
| exception / note / role commands | synchronous and tiny — re-run the transaction |

> **Change from v1 — leases added.** v1 had no lease, so a crashed in-progress request left the key `PENDING` permanently; a well-behaved client reusing the same key (the whole point of idempotency) got "in progress" forever with no path to resolution. The lease makes abandonment detectable and recoverable.

**Client contract for in-progress:** the API returns **`202 Accepted`** with a `Retry-After` hint and a body describing current operation state ([11](./11-api.md)). The client polls with backoff; it must **not** reissue the command under a *new* key while an attempt is live (doing so is blocked anyway by the contribution's status precondition, but the contract is explicit).

## 8.4 The two-phase payment & reconciliation (the crash-recovery contract)

Money movement cannot be inside a Firestore transaction, so processing is two-phase with an external side effect in the middle:

```
Phase 1 (TXN):  create idempotency (PENDING) + contribution SCHEDULED→PROCESSING
                + attempt(STARTED, processorIdempotencyKey) + PAYMENT_PROCESSING event
Phase 2 (side): call payment adapter WITH the attempt's processorIdempotencyKey
Phase 3 (TXN):  finalize — success: →POSTED, apply balances, resolve exception, event, idempotency COMPLETED
                          failure: →FAILED, upsert exception, event, idempotency COMPLETED(failure result)
```

**The gap and its closure.** If the process dies after Phase 2 succeeds but before Phase 3 commits, the contribution is stuck `PROCESSING`, the attempt `STARTED`, money moved, balances not applied. This is recovered by a **reconciliation sweeper** ([14](./14-async-and-background-jobs.md)):

```
periodically, find contributions where status == PROCESSING
                                 and lastAttemptAt < now − STUCK_THRESHOLD
        (plus: attempts still STARTED older than STUCK_THRESHOLD, regardless of
         contribution status — catches a stale driver that mis-finalized, see below)
for each:
  re-call the payment adapter's STATUS/QUERY endpoint WITH the same processorIdempotencyKey
    → SUCCEEDED  : run Phase 3 success (idempotent; guarded on attemptId + attempt STARTED) → POSTED
    → FAILED     : run Phase 3 failure → FAILED (+ exception)
    → NOT_FOUND  : get_status has FENCED the key (see below) — the charge never happened
                   and can no longer happen. If the benefit is TERMINATED → CANCELED
                   (resolve/dismiss exception); otherwise revert the contribution to its
                   pre-processing state (SCHEDULED or RETRY_PENDING) for a clean retry.
    → INDETERMINATE (the get_status call itself failed — transport/5xx, NOT a
                   NOT_FOUND) : never guess about money. Increment the attempt's
                   reconcileAttempts; after MAX_SWEEPS raise PAYMENT_STUCK_PROCESSING
                   (CRITICAL) for a human. Never auto-cancel on indeterminate.
```

**Fencing — why NOT_FOUND is safe.** Without it, `NOT_FOUND` is a race: the original `charge(k1)` may still be in flight (slow network, processor ingress queue); the sweeper reverts, a re-process creates attempt 2 with a **new** key `k2`, then the delayed `k1` lands — two charges, one installment, and per-key idempotency cannot help because the keys differ. Therefore the adapter contract **requires**: `get_status(key)` on an unknown key atomically **tombstones (fences)** that key — a subsequent `charge(key)` is rejected (`NOT_SUBMITTED`), making `NOT_FOUND` a durable verdict, not a snapshot. Additionally the Phase-2 client timeout (plus retry margin) must be **shorter than `STUCK_THRESHOLD`**, so a live driver and the sweeper cannot both act on the same attempt. See [09 §9.5](./09-payment-processing.md) and the pinned constants in [21](./21-deployment-and-operations.md).

> **Change from v1 — reconciliation is a first-class, specified component.** v1 described the two-phase flow but named no recovery for a crash between phases; the state machine had no exit from `PROCESSING` except the finalize that never ran, so the contribution was unrecoverable. v2's sweeper uses the attempt's **`processorIdempotencyKey` as a *query* key** (not a new charge) to learn the processor's authoritative outcome, then finalizes idempotently. Because the key is deterministic from `(contributionId, attemptNumber)`, the sweeper reconstructs it without needing anything the dead process held in memory.

**Adapter contract requirement.** The simulated payment adapter (and any real processor) **must** expose an idempotent charge (same `processorIdempotencyKey` ⇒ same charge, never double-charges) **and** a status/query endpoint keyed by that same id. This is a hard requirement of the adapter interface ([09 §9.5](./09-payment-processing.md)); a processor without it cannot be made safe under this pattern.

## 8.5 Event atomicity & ordering

- All `servicingEvent`s for a command are written **in the same transaction** as the state change they describe (global doc + entity-subcollection mirror).
- Multiple events in one command are assigned a monotonically increasing `sequence` (1, 2, 3…) sharing the command's `correlationId`.
- Timeline queries order by `(createdAt, sequence)` so co-committed events have a stable, deterministic order ([04 §4.9](./04-firestore-data-model.md)).

## 8.6 Idempotency of async task handlers

Every Cloud Task handler is idempotent (Cloud Tasks has at-least-once delivery). Handlers achieve this via deterministic document IDs + create-preconditions, or by checking a progress marker (`agreement.installmentsGenerated`, `contribution.status`) before acting. A redelivered task must be a no-op if its work is already done. See [14](./14-async-and-background-jobs.md).
