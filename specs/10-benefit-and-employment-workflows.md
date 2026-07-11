# 10 — Benefit & Employment Workflows

Depends on [06](./06-state-machines.md), [07](./07-financial-rules.md), [08](./08-idempotency-and-consistency.md). Payment processing itself is [09](./09-payment-processing.md).

## 10.1 Activate benefit agreement

Turns a `PENDING` agreement into an `ACTIVE` one with a fully generated contribution schedule.
**Endpoint:** `POST /benefit-agreements/{agreementId}/activate` (`SERVICING_MANAGER`+, `Idempotency-Key`).
**Preconditions:** agreement `PENDING`; borrower employment `ACTIVE`; loan `ACTIVE`; **employer `ACTIVE`** ([06 §6.6a](./06-state-machines.md)); `startDate` not in the past (else `422`); the loan has no other active agreement.

**Why async:** a schedule of `termMonths` installments plus events could, for long terms, approach Firestore's 500-writes/transaction limit, and generation should not block the request or lose all progress on one transient error. So generation is bounded-batch and resumable.

> **Change from v1 — right-sized async + honest rationale.** v1 justified async generation by "don't create an unbounded schedule in one transaction," but a 36-installment schedule fits comfortably in a single atomic batch (well under 500 writes). v2 states the real reasons — **request latency and resumability for long terms** — and keeps the machinery, but generation for terms under a configured `SYNC_GENERATION_MAX` (~450 writes) **may** run as a single atomic batch (preserving all-or-nothing); only longer terms use the multi-batch resumable path. Either way the outcome and IDs are identical.

**Flow:**
1. **Accept (transaction):** idempotency create; assert preconditions; agreement `PENDING → ACTIVATING`; solve installment amounts (§7.3) and record `plannedInstallmentCount`; write `BENEFIT_ACTIVATION_STARTED` event; enqueue a `generate-schedule` Cloud Task; return `202` (operation in progress).
2. **Generate (task, idempotent, bounded batches):** create contributions with deterministic IDs `{agreementId}__{NNN}` (create-precondition ⇒ redelivery/duplicate activation is a no-op). Advance `agreement.installmentsGenerated` after each batch so the task resumes from where it left off. Each contribution: `SCHEDULED`, `scheduledDate` = 12:00 `SYSTEM_TIMEZONE` on the due day of installment N, `periodLabel`, solved `scheduledAmountCents`.
3. **Finalize (transaction):** when `installmentsGenerated == plannedInstallmentCount`: agreement `ACTIVATING → ACTIVE`, `scheduleGenerated = true`, `acceptingPayments = true`; sync `loan.benefitStatus = ACTIVE`, `loan.nextContributionDate/AmountCents`; write `BENEFIT_ACTIVATED` event; idempotency `COMPLETED`; enqueue projection updates.

**Failure/recovery of generation.** Transient task errors: Cloud Tasks retries; the handler resumes from `installmentsGenerated` (and **halts if the agreement is no longer `ACTIVATING`** — e.g. terminated mid-generation, [06 §6.3](./06-state-machines.md)). Terminal failure (retries exhausted → dead-letter, [14 §14.5](./14-async-and-background-jobs.md)): a `TASK_FAILED` exception is raised and the agreement is moved `ACTIVATING → PENDING` by the dead-letter handler; **already-created installments are retained** — a later re-activation reuses them (create-preconditions make regeneration of existing IDs a no-op), so the agreement is never stranded in `ACTIVATING`.

**Acceptance criteria**
- Repeated activation requests never create duplicate contributions (deterministic IDs + create-precondition + idempotency).
- `Σ(scheduledAmountCents) == totalCommitmentCents` exactly (I5/§7.3).
- Activation fails if employment is not `ACTIVE`, employer not `ACTIVE`, or `startDate` past (`422 UNPROCESSABLE`).
- A partial batch failure resumes safely from `installmentsGenerated`; no gaps, no duplicates; terminal failure lands back in `PENDING` with a `TASK_FAILED` exception, never a stuck `ACTIVATING`.

## 10.2 Suspend benefit / resume benefit

**Suspend:** `POST /benefit-agreements/{agreementId}/suspend` (`SERVICING_MANAGER`+, `Idempotency-Key`).
Transaction: agreement `ACTIVE → SUSPENDED`; set `acceptingPayments = false` and **`suspendedReason`** (`MANUAL` from this command; `LEAVE` from the employment cascade); sync `loan.benefitStatus = SUSPENDED`; write `BENEFIT_SUSPENDED` event. Future `SCHEDULED` contributions are **left in place** (suspension is reversible); because `acceptingPayments == false`, they cannot be processed while suspended ([06 §6.7](./06-state-machines.md)).

**Resume:** `POST /benefit-agreements/{agreementId}/resume` (`SERVICING_MANAGER`+, `Idempotency-Key`) — or automatic on return-from-leave when `suspendedReason == LEAVE` (§10.4).
Transaction: agreement `SUSPENDED → ACTIVE`; `acceptingPayments = true`; clear `suspendedReason`; sync `loan.benefitStatus`; write `BENEFIT_RESUMED` event; enqueue the **`shift-schedule`** task. Per the **schedule-shift policy** ([07 §7.8](./07-financial-rules.md)): the task re-dates every remaining `SCHEDULED` installment forward by the suspension duration (whole months, noon rule) and extends `endDate` — **no catch-up lump**. It runs in bounded batches, is idempotent (re-dating to the already-shifted date is a no-op), updates `loan.nextContributionDate/AmountCents` in its final batch, and writes one `SCHEDULE_SHIFTED` event. `RETRY_PENDING`/`FAILED` installments are not re-dated — they become retryable again immediately.

## 10.3 Terminate benefit

**Endpoint:** `POST /benefit-agreements/{agreementId}/terminate` (`SERVICING_MANAGER`+, `Idempotency-Key`).
Transaction: agreement `ACTIVE/SUSPENDED → TERMINATED` (or `ACTIVATING → TERMINATED` — the generation task halts on its next batch); `acceptingPayments = false`; sync `loan.benefitStatus = TERMINATED`; write `BENEFIT_TERMINATED` event; enqueue the **cancel-future-contributions** task (§10.4 step 3). Terminal; not reversible.

## 10.4 Employment status change (incl. termination cascade)

**Endpoint:** `POST /borrowers/{borrowerId}/employment-status` (`SERVICING_MANAGER`+, `Idempotency-Key`).
Request: `{ status, effectiveDate, reason }`. Requires a confirmation step in the UI before submit.

**Cascade mapping** (all cascades are **idempotent no-ops** when the benefit is already at/past the target state — [06 §6.7](./06-state-machines.md)):

| New employment status | Benefit effect |
|-----------------------|----------------|
| `LEAVE` | `ACTIVE` benefit → `SUSPENDED` (`suspendedReason = LEAVE`). Already `SUSPENDED` (manual): no-op — the manager's suspension and its reason stand. |
| `TERMINATED` | `ACTIVE`/`SUSPENDED`/`ACTIVATING` benefit → `TERMINATED` + cancel future contributions |
| `ACTIVE` (return from LEAVE) | `SUSPENDED` benefit → `ACTIVE` **only when `suspendedReason == LEAVE`** (+ schedule shift, §10.2). A manually suspended benefit stays suspended — the manager resumes it explicitly. |

**Flow (termination):**
1. **Transaction (bounded):** borrower `employmentStatus → TERMINATED`, `employmentEndDate = effectiveDate`; find the active agreement (`loan.benefitAgreementId`), set it `TERMINATED` + `acceptingPayments = false`, sync `loan.benefitStatus`; write `EMPLOYMENT_STATUS_CHANGED` and `BENEFIT_TERMINATED` events (shared `correlationId`, `sequence` 1..n); idempotency `PENDING`.
2. **Enqueue** the cancel-future-contributions Cloud Task; commit.
3. **Cancel task (idempotent, bounded batches):** for contributions of this agreement in `SCHEDULED`, `RETRY_PENDING`, **or `FAILED`**, transition each → `CANCELED` (guarded on current status), writing **one `PAYMENT_CANCELED` event per contribution** (every material change is evented; batch sizing counts *writes* — cancel + event + mirror = 3 per item, so ≤ ~150 items/batch — [14 §14.4](./14-async-and-background-jobs.md)). For a `FAILED` contribution, also dismiss its `currentExceptionId` ("benefit terminated") per [09 §9.3](./09-payment-processing.md) — termination must never leave a permanently open, un-retryable exception. The final batch nulls `loan.nextContributionDate/AmountCents`. On completion write `FUTURE_CONTRIBUTIONS_CANCELED` and set idempotency `COMPLETED`.

**In-flight `PROCESSING` contributions — the explicit resolution.**

> **Change from v1 — the in-flight case is fully specified.** v1 said "processing contributions require explicit handling" but never defined it, and `PROCESSING → CANCELED` was disallowed, so a contribution in flight during termination had no legal resolution. v2: the cancel task **does not touch** `PROCESSING` contributions (the money may already be moving). Instead:
> - The `acceptingPayments = false` flag (set in step 1) guarantees no *new* processing starts and no retry can begin ([06 §6.7](./06-state-machines.md)).
> - The in-flight attempt is allowed to **settle** through its normal Phase 3 (or via reconciliation). On settle:
>   - **Success →** it `POSTED` (the payment genuinely moved before termination took effect); balances apply; this is correct and auditable. A benefit already `TERMINATED` simply won't schedule anything further.
>   - **Failure →** finalization sees the agreement status is **`TERMINATED`** (the status, not the `acceptingPayments` flag — the flag is also false under `SUSPENDED`, where the installment must stay recoverable) and routes the contribution to `CANCELED` (settle-then-cancel, [06 §6.1](./06-state-machines.md)); it **suppresses** creating a new exception and dismisses any pre-existing `currentExceptionId` with reason "benefit terminated" instead of leaving it open for a retry that can never happen.
> - The reconciliation sweeper ([09 §9.4](./09-payment-processing.md)) is the backstop that ensures no `PROCESSING` contribution is left dangling after termination.

**Acceptance criteria**
- Past `POSTED` contributions are never reversed.
- Future `SCHEDULED`/`RETRY_PENDING` contributions become `CANCELED`.
- In-flight `PROCESSING` contributions settle correctly and never leave an un-retryable open exception.
- The operation resumes safely after interruption (idempotent task + bounded batches + status-guarded transitions).
- The user confirms before submitting.

## 10.5 Add servicing note

**Endpoint:** `POST /loans/{loanId}/notes` (`OPERATIONS_USER`+).
Transaction: create note in `loans/{loanId}/notes/{noteId}` (`text`, `authorId`, `authorName`, `createdAt`); write `MANUAL_NOTE_ADDED` event. Notes are timestamped, attributed to the authenticated user, non-empty (empty rejected `400`), and **not editable or deletable** in the MVP (append-only, consistent with the audit model). The timeline updates in real time via the loan's events subcollection.
