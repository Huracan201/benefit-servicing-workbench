# Engineering Report — Phase 2: Domain Command Layer (Part 1)

**Project:** BenefitServicing Workbench (`Huracan201/benefit-servicing-workbench`)
**Phase:** 2 — Domain commands (per [specs/19 §19.2](../19-delivery-and-scope.md))
**Scope of this report:** the command-layer **foundation** + the **benefit-activation → payment-processing vertical slice**
**Status:** ✅ Built & QA-verified on `release/phase-2` (base `main` `1261b56`) — pending CI (emulator run) + merge
**Date:** 2026-07-12

---

## 1. Summary

Phase 2 builds the **domain command layer** — the correctness-critical heart of the system, where every state change composes the Phase-1 `common/` core (state machines, money, invariants) inside Firestore transactions, guarded by idempotency, immutable events, and the two-phase payment protocol.

This first slice delivers the reusable **command foundation** and a complete **vertical slice** through it: activate a benefit → generate its schedule → process a contribution (the two-phase payment) → handle failure, retry, and crash/fence recovery. It's the highest-risk path in the product, so it was built and then reviewed harder than anything before it.

**Headline outcomes**
- 52 new backend modules, ~6,500 lines: the foundation (`repositories`, `commands`, `idempotency`, `servicing`, `exceptions`, `payments/adapter`) plus the `benefits` and `contributions/payments` commands, `seed_demo`, and `/api/v1` wiring.
- The **two-phase payment** (transition → external charge outside the txn → finalize) with a crash-recovery **reconciliation sweeper** and a **fencing** payment adapter that makes a double-charge impossible.
- **15 new tests** — 8 emulator integration tests (incl. the crown-jewel concurrency gate and the fencing/crash-recovery tests) + 7 pure unit tests.
- Reviewed by **3 independent QA agents + adversarial tracing**; **1 HIGH + several MED/LOW** found and **all fixed and verified**. No blockers survived.

---

## 2. Scope

**In scope (this slice)**
- **Foundation** (reusable by all later commands): `repositories/` (Firestore gateways), `commands/base` + `idempotency/service` (the create-in-transaction idempotency mechanism with lease), `servicing/events` (immutable event writer with mirror + sequence), `exceptions/service` (deterministic upsert), `payments/adapter` (simulated processor with fencing).
- **Commands**: `activate_benefit` (specs/10 §10.1); `process_contribution` / `retry_contribution` / `reconcile_contribution` — the two-phase payment + recovery (specs/09).
- **Support**: `seed_demo` (deterministic demo portfolio + the 8 scenarios + role-claimed demo users); `/api/v1` routing for activate/process/retry.

**Deferred to the next Phase-2 slice**
- suspend / resume / terminate benefit; employment-status change + cascade; the exception workflow (assign/review/resolve/dismiss, manual create); add-note; the admin role/employer commands. The state machines, repositories, and event/exception/idempotency services are already in place for them.

**Later phases**
- Cloud Tasks / Scheduler handlers (Phase 3) — Phase 2 runs async work inline (`TASK_EXECUTION_MODE=inline`); read-model projections; the real UI (Phase 4).

---

## 3. What was delivered

### 3.1 Command foundation

| Module | Responsibility |
|--------|----------------|
| `repositories/` (13 files) | Thin Firestore data-access gateways — collection-name constants (specs/04 §4.1), doc/get helpers, common-field stamping (`revision` via `Increment`). No business logic. |
| `commands/base` | `request_hash` (method+path+canonical-body — so empty-body commands don't collide), the `CommandError` hierarchy with HTTP mapping, and the `@transactional` wrapper. |
| `idempotency/service` | `begin`/`complete` — the specs/08 §8.2 **create-in-transaction** protocol (a `txn.create` create-precondition is the "first request wins" primitive) with the §8.3 **lease** for crash reclamation. |
| `servicing/events` | Immutable event append: global `servicingEvents/{id}` + the most-specific entity mirror in one write; `sequence` + a closed `eventType` enum. |
| `exceptions/service` | Deterministic `{entityId}__{type}` upsert with `occurrenceCount`; resolve/dismiss. |
| `payments/adapter` | Simulated processor persisted to Firestore; **fencing** `get_status` (an unknown key is tombstoned so a late `charge` is rejected); scripted outcomes + a crash-after-charge test toggle. |

### 3.2 The two-phase payment (the crown jewel)

`process_contribution` implements specs/09 §9.1 exactly:

1. **Phase 1 (txn):** idempotency `begin` → `SCHEDULED/RETRY_PENDING → PROCESSING` → create the `STARTED` attempt → `PAYMENT_PROCESSING` event.
2. **Adapter charge — outside any transaction** (so a DB transaction is never held across a network call).
3. **Phase 3 (txn):** a finalize **guard** (`currentAttemptId` + attempt `STARTED`, on *both* success and failure branches) → on success: `POSTED`, capped balances applied atomically, exception resolved, look-ahead recomputed, events; on failure: `FAILED` + coupled exception, or **settle-then-cancel** if the agreement is `TERMINATED`.

Crash-recovery is real: `reconcile_contribution` re-queries the processor by the attempt's deterministic key (never a fresh charge) and finalizes idempotently; a `NOT_FOUND` verdict is durable because `get_status` **fences** the key.

### 3.3 Tests

| Suite | Count | What it proves |
|-------|:-----:|----------------|
| Emulator — concurrency (crown jewel) | — | Two real threads racing the **same idempotency key** on one contribution → exactly one attempt, one posting, one charge row; loser replays/202. |
| Emulator — fencing / crash-recovery | — | A charge that succeeds before finalize is recovered to exactly one posting; a fenced key rejects a late charge (no double-charge). |
| Emulator — activate / process / failure | — | Schedule sums to commitment; posting moves balances atomically; a decline leaves balances untouched with one deterministic exception. |
| **Emulator total** | **8** | run on CI via `firebase emulators:exec` |
| **Unit** (pure, `@tag('unit')`) | **7** | `request_hash` path-sensitivity + determinism; `CommandError`→HTTP; the exception severity map. |

---

## 4. Process — how it was built and hardened

1. **Build** — a **12-agent workflow** across four barriered phases (foundation → commands → tests+wiring → review), each phase reading the prior phase's real files so the seams stayed consistent.
2. **First review (in-workflow)** — a correctness pass and a CI pass; findings fixed (idempotency ordering, terminated-payoff guard, discovery bug) and lead-verified.
3. **Second-pass QA (this report's emphasis)** — **three independent review agents** (payment/transaction correctness, data-model fidelity, CI + test efficacy) plus lead adversarial tracing of the Firestore read/write discipline. This is where the HIGH was caught.
4. **Consolidated fix + verify** — all findings fixed in one pass, then lead-verified line-by-line: read-before-write re-traced in the modified `finalize_success`, the pure unit-test logic **executed** offline, a repo-wide read-after-write sweep, and a `common/` regression.

The layered review paid off: the deepest finding (loan-payoff installment cancellation) was invisible to compile-time checks and required tracing the payoff path against specs/07 §7.4 and demo scenario 8.

---

## 5. Verification & test coverage

| Check | Where | Result |
|-------|-------|--------|
| Full backend `py_compile` | offline | ✅ |
| Read-before-write in every transactional function | offline (traced) | ✅ none violated |
| Pure unit-test logic (request_hash, error mapping, severity map) | offline (**executed**) | ✅ pass |
| `common/` regression | offline | ✅ 60/60 |
| Emulator integration (concurrency, fencing, crash-recovery, activate, post) | **CI runner** | pending run |
| `manage.py check` + Django boot | **CI runner** | pending run |

**Offline vs CI split (unchanged from Phase 1):** the pure logic and transaction *structure* are verified locally; the emulator behavior (real Firestore transactions, thread contention, fencing) is verified on CI, which has the network to install deps and run the emulator. The QA agents statically confirmed the CI job will discover and run the tests (a discovery bug that produced a false 0-tests green was fixed — the emulator step now `cd backend` before `manage.py test`).

---

## 6. Issues found & fixed (before commit)

All found in second-pass QA; all fixed and lead-verified.

| # | Severity | Issue | Resolution |
|---|----------|-------|-----------|
| 1 | 🟠 **HIGH** | Loan payoff marked the loan `PAID_OFF`/benefit `COMPLETED` but never cancelled the remaining `SCHEDULED` installments and left a non-null look-ahead (violates specs/07 §7.4, fails demo scenario 8) | Read remaining installments in the read phase; cancel them + null the look-ahead in the write phase (`PAYMENT_CANCELED` events) |
| 2 | 🟡 MED | A reclaimed idempotency key could stay `PENDING` forever when reconcile skipped finalize | Complete the key from current state on the skip branches |
| 3 | 🟡 MED | Stuck-processing escalation during a retry orphaned the prior `PAYMENT_FAILED` exception and undercounted `openExceptionCount` | Count the genuinely-new stuck exception (keyed on its own deterministic id) |
| 4 | 🟢 LOW | Expired-lease reclaim didn't check `requestHash` | Gate reclaim on hash match (else 409, like the other branches) |
| 5 | 🟢 LOW | Two installments racing to payoff could hit an illegal `PAID_OFF→PAID_OFF` | Guard the transition on `!= PAID_OFF` |
| 6 | 🟢 LOW | Collection-name constants duplicated in two modules | Import from `repositories.refs` |
| 7 | 🟡 MED | The CI `--tag=unit` gate ran zero tests (vacuous) | Added 7 pure `@tag('unit')` command-layer tests |
| 8 | ▪ triv | A docstring named a non-existent `PAYMENT_SUCCEEDED` event | → `PAYMENT_POSTED` |

**Cleared, not bugs:** the idempotency reorder from the first fix pass was independently re-confirmed to introduce **no** read-after-write; the exception `upsert`'s read-then-write is intentionally positioned as the first write (legal); the crown-jewel concurrency test genuinely contends (real threads, shared key, hard exactly-once assertions — not vacuous).

---

## 7. Key technical decisions

- **Idempotency is created *inside* the state-transition transaction** with a create-precondition — the atomic "first request wins" guarantee, and the reason the concurrency gate holds.
- **The adapter charge is outside the transaction**; crash-safety comes from the reconciliation sweeper + a **fencing** `get_status`, not from holding a transaction across a network call.
- **The finalize guard is on every branch** (`currentAttemptId` + attempt `STARTED`), so a live finalizer and the sweeper serialize on the attempt — no double-post.
- **Settle-then-cancel keys on agreement status `TERMINATED`**, not the `acceptingPayments` flag (which is also false under `SUSPENDED`, where a failure must stay recoverable).
- **Inline async for Phase 2** (`TASK_EXECUTION_MODE=inline`): schedule generation and recovery run in-process; the same code moves onto Cloud Tasks in Phase 3 with no logic change.

---

## 8. Metrics

| Metric | Value |
|--------|-------|
| New backend modules | 52 (~6,500 lines) |
| Modules/apps added | repositories, commands, idempotency, servicing, exceptions, payments, benefits, contributions, seed, api |
| New tests | 15 (8 emulator + 7 unit) |
| Build agents | 12 (4-phase workflow) |
| QA review agents | 3 + lead adversarial tracing |
| Issues found → fixed | 8 → 8 (1 HIGH) |
| Offline verification | full compile, read/write sweep, executed unit logic, 60/60 common regression |

---

## 9. Known limitations (by design)

- This is **part 1** of Phase 2 — suspend/resume/terminate, employment cascade, exception workflow, notes, and admin commands are the next slice (foundation for them is in place).
- Async work runs **inline** (no Cloud Tasks yet — Phase 3). Long-term schedules cancel inline within the payoff transaction (bounded, safe at demo scale; moves to the async cancel task at production scale).
- Read-model projections (`portfolioSummaries` etc.) are **not** written yet — the dashboard depends on the Phase-3 projection layer; `seed_demo` populates entity docs but no read models.
- Emulator tests are verified structurally offline; their green run is on CI.

---

## 10. What's next

The immediate next step is the CI emulator run (the real proof of the two-phase payment under genuine Firestore transactions and thread contention), then merge. After that: the remaining Phase-2 commands (suspend/terminate/employment/exceptions/notes), then Phase 3 (Cloud Tasks + Scheduler + projections).

---

*Related: [phase-1-foundation.md](./phase-1-foundation.md) · [specs/08](../08-idempotency-and-consistency.md) (idempotency + recovery) · [specs/09](../09-payment-processing.md) (two-phase payment) · [specs/17 §17.2](../17-testing.md) (the required emulator tests).*
