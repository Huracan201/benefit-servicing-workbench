# 17 — Testing Strategy

Tests are organized by the risk they retire. The **correctness-critical** layer (money, state machine, idempotency, reconciliation, security rules) gets the deepest coverage; UI gets behavioral coverage; two end-to-end paths prove the whole thing hangs together.

## 17.1 Django unit tests (domain logic, fast, no emulator)

Pure logic in `backend/common/` and command handlers with Firestore mocked/faked:

- **Money & residual:** `Σ(installments) == totalCommitment` for many `(commitment, term)` pairs incl. non-divisible ones (the 3,000,000/36 case → final 83,345); balance-capped final payment; no negative balance; `amountPaid ≤ commitment` ([07](./07-financial-rules.md)).
- **State machine:** every allowed transition succeeds; every disallowed transition raises `INVALID_TRANSITION` (table-driven over the full matrix — [06](./06-state-machines.md)); `PROCESSING→CANCELED` reachable only via settle-then-cancel (failure-finalize under a TERMINATED agreement, or reconciliation) — never via a direct command.
- **Deterministic IDs:** contribution/attempt/exception id formats; regeneration yields identical ids.
- **Idempotency:** replay returns prior result; same key + different hash → `409`; in-progress → `202`; expired-lease reclamation.
- **Exception coupling:** repeated failures upsert one exception (`occurrenceCount`), not many; retry-success resolves exactly `currentExceptionId`; cancel resolves it too.
- **Authorization:** each command enforces the role matrix ([12 §12.2](./12-auth-and-security.md)).
- **Employment cascade:** LEAVE→suspend, TERMINATED→terminate + cancel-future; in-flight `PROCESSING` handled ([10 §10.4](./10-benefit-and-employment-workflows.md)).

## 17.2 Firestore emulator integration tests (real transactions & rules)

Run against the Emulator Suite:

- **Transactions:** multi-document atomicity of a posting (contribution + attempt + loan + agreement + events all commit or none).
- **Idempotency under concurrency — the crown-jewel test.** Fire **two simultaneous** `process` requests with the **same** idempotency key at the same `SCHEDULED` contribution; assert **exactly one** attempt is created, exactly one posting occurs, one request gets the result and the other gets a replay/`202`, and balances move exactly once. This single test is the clearest proof the design is sound; it is a **must-pass gate**.

  > **Change from v1 — this test is elevated to a required gate with a precise expected outcome.** v1 listed "concurrent payment-processing attempts" among emulator tests without specifying the exact expected result; v2 pins it down (see [11 §11.6](./11-api.md), [08 §8.2](./08-idempotency-and-consistency.md)).

- **Crash-recovery / reconciliation:** simulate "charge succeeded, finalize never ran" (adapter says `SUCCEEDED`, contribution left `PROCESSING`); run the sweeper; assert it posts exactly once with no double charge. Also the "never charged" and "indeterminate → `PAYMENT_STUCK_PROCESSING`" branches ([09 §9.4](./09-payment-processing.md)).
- **Fencing (double-charge gate):** sweeper gets `NOT_FOUND` (key fenced) and reverts; the *delayed original* `charge(k1)` then arrives at the simulator — assert it is REJECTED (`NOT_SUBMITTED`) and only the re-processed attempt's charge exists. This is the F1 regression test; must-pass.
- **Stale-driver guard:** attempt 1's finalize runs after the sweeper reverted and attempt 2 started — assert the stale failure-finalize aborts on the `currentAttemptId`/attempt-`STARTED` guard and changes nothing.
- **Contract tests (once endpoints exist):** validate real responses against `openapi.yaml` (schemathesis or response-validation middleware in test) — the check that keeps "openapi.yaml wins" true.
- **Security rules:** each protected collection unreadable without a role claim, unwritable by any client, readable with a claim; `users` self-write denied; `idempotencyKeys` fully client-invisible ([12 §12.6](./12-auth-and-security.md)).
- **Bounded batches / resumability:** schedule generation and cancel-future resume correctly after a simulated mid-run interruption; no gaps or duplicates.
- **Projections:** an event folds into the right summary; the scheduled rebuild corrects injected drift.
- **Race — retry vs. cancel:** a retry racing a termination-cancel on a `RETRY_PENDING` contribution resolves cancel-wins ([06 §6.7](./06-state-machines.md)).

## 17.3 Frontend tests (Vitest + React Testing Library)
Dashboard rendering from summaries; loan filters map to the right queries; payment-status badge/label rendering (label present, not color-only); exception workflow interactions; confirmation dialogs (esp. employment termination); API error states surface typed codes; role-based action visibility (buttons hidden/disabled per role).

## 17.4 End-to-end (Playwright)

**Critical path A — failed payment → retry → posted:**
1. Log in as Servicing Manager. 2. Open a failed-payment exception. 3. Schedule a retry. 4. Process the contribution. 5. Contribution becomes `POSTED`. 6. Exception resolves. 7. Loan balance updates. 8. Benefit totals update. 9. Servicing timeline shows all events in order.

**Critical path B — termination stops future benefits:**
1. Open an active borrower. 2. Terminate employment (confirm dialog). 3. Benefit becomes `TERMINATED`. 4. Future contributions become `CANCELED`. 5. Prior `POSTED` contributions unchanged.

## 17.5 CI gates
Every PR runs the gates in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml): **OpenAPI lint** (`specs/openapi.yaml` via Spectral) and **Firestore rules tests** always; **backend** (unit + emulator integration), **frontend** (lint/unit/build), and **E2E** (Playwright critical paths) auto-activate via a file-existence `detect` job as that code lands, so the pipeline is green on the current spec-only repo. The emulator runs inside the job (`firebase emulators:exec`). The crown-jewel concurrency test and both critical-path E2Es must pass before merge to the demo branch. Coverage is reported but the **gate is the critical-path tests**, not a raw coverage percentage.
