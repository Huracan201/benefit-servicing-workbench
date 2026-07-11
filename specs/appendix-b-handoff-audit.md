# Appendix B — Pre-Handoff Audit (v2 → v2.1)

Before handoff, the v2 package was audited by three independent review passes (adversarial design, cross-artifact consistency, implementability/operations) plus a targeted verification pass — ~55 unique findings after dedup. All were resolved in the **v2.1** fix pass recorded here. Business decisions taken: **D1** resume = schedule **shift** (no catch-up lump); **D2** remaining-commitment rollups scope to **non-terminal agreements**; **D3** employer `INACTIVE` **blocks new activations only**.

## Money-safety (blockers)

| Finding | Resolution | Where |
|---|---|---|
| Sweeper `NOT_FOUND` could double-charge (delayed charge lands after revert; new attempt = new key) | `get_status` **fences/tombstones** unknown keys — later `charge(key)` rejected; Phase-2 timeout < `STUCK_THRESHOLD`; regression test added | [08 §8.4](./08-idempotency-and-consistency.md), [09 §9.4–9.5](./09-payment-processing.md), [17 §17.2](./17-testing.md), [21 §21.1](./21-deployment-and-operations.md) |
| Phase-3 **failure** path lacked the `currentAttemptId` guard (stale driver could strand a live posting invisibly) | Identical guard (attemptId + attempt `STARTED`) on **both** branches; sweeper also scans stale `STARTED` attempts | [09 §9.1, §9.4](./09-payment-processing.md) |
| Sweeper revert resurrected zombie `SCHEDULED` on terminated benefits | `NOT_FOUND` branch checks agreement: `TERMINATED` → `CANCELED` + exception dismissed | [09 §9.4](./09-payment-processing.md) |
| "Indeterminate" sweeper row self-contradictory; 08 vs 09 tables disagreed; no counter field | Indeterminate = `get_status` call failure; never auto-cancel; `attempt.reconcileAttempts` → `PAYMENT_STUCK_PROCESSING` at `MAX_SWEEPS`; tables aligned | [08 §8.4](./08-idempotency-and-consistency.md), [09 §9.4](./09-payment-processing.md), [04 §4.8](./04-firestore-data-model.md) |
| `PROCESSING→CANCELED` "reconciliation-only" (06/17) contradicted 10.4's finalize route; guard keyed on flag couldn't distinguish SUSPENDED; payoff-during-suspension had no legal transition | Settle-first-only wording (either actor); guard keys on **status `TERMINATED`**; suspended-failure stays recoverable; `SUSPENDED→COMPLETED` added | [06 §6.1, §6.3](./06-state-machines.md), [09 §9.1](./09-payment-processing.md), [10 §10.4](./10-benefit-and-employment-workflows.md), [17 §17.1](./17-testing.md) |

## Security & live bugs

| Finding | Resolution | Where |
|---|---|---|
| Task/Scheduler handlers had **no inbound auth** (publicly invokable as SYSTEM) | `/internal/*` namespace + Google **OIDC** middleware (audience + invoker SA) + emulator bypass | [12 §12.5](./12-auth-and-security.md), [14](./14-async-and-background-jobs.md), [21 §21.2](./21-deployment-and-operations.md) |
| CI: `emulators:exec` run from repo root without `--config` (fails on first Phase-1 PR); e2e job activates before its script exists | `--config firebase/firebase.json` on both; `e2e.sh` added to the hashFiles guard; backend deps installed in e2e | [.github/workflows/ci.yml](../.github/workflows/ci.yml) |
| "Disable is immediate" false with plain `verify_id_token`; §12.4 claimed an unimplemented rules condition | `check_revoked=True` on mutating commands; §12.4 prose corrected (read revocation bounded by token expiry) | [12 §12.3–12.4](./12-auth-and-security.md) |
| CORS unspecified; `Retry-After` invisible cross-origin (breaks 202 polling in browsers) | CORS policy + `Access-Control-Expose-Headers: Retry-After` + authorized-domains step | [21 §21.3](./21-deployment-and-operations.md) |
| `requestHash` undefined for body-less commands (same key could replay across entities) | Hash = method + **path** + canonical body; FAILED-key overwrite re-checks hash | [08 §8.2](./08-idempotency-and-consistency.md) |

## Design gaps (decisions D1–D3 + mechanics)

| Finding | Resolution | Where |
|---|---|---|
| Suspend→resume fired all missed installments as a lump (unspecified) | **D1: schedule shift** — re-date remaining `SCHEDULED` + extend `endDate`; `RETRY_PENDING`/`FAILED` not re-dated; `shift-schedule` task + `SCHEDULE_SHIFTED` event | [07 §7.8](./07-financial-rules.md), [10 §10.2](./10-benefit-and-employment-workflows.md), [14 §14.3](./14-async-and-background-jobs.md) |
| Terminal agreements' residual commitment overstated dashboards forever | **D2: rollups scope to non-terminal agreements**; agreement field stays historical (I3 intact) | [07 §7.7](./07-financial-rules.md), [05 §5.3](./05-read-models-and-projections.md) |
| Employer `INACTIVE` gated nothing; no setter | **D3: blocks new activations only**; employer machine + admin endpoint added | [06 §6.6a](./06-state-machines.md), [10 §10.1](./10-benefit-and-employment-workflows.md), [11 §11.4](./11-api.md), openapi |
| Termination orphaned pre-existing `FAILED` contributions + their open exceptions | Cancel task covers `SCHEDULED`/`RETRY_PENDING`/**`FAILED`** (+ exception dismissal) | [10 §10.4](./10-benefit-and-employment-workflows.md), [14 §14.3](./14-async-and-background-jobs.md) |
| Manual suspension auto-resumed by return-from-leave; LEAVE onto suspended 409'd; terminate-during-ACTIVATING illegal | `suspendedReason` (LEAVE\|MANUAL); auto-resume only LEAVE-caused; cascades idempotent no-ops; `ACTIVATING→TERMINATED` + generation halt | [06 §6.3, §6.7](./06-state-machines.md), [10 §10.4](./10-benefit-and-employment-workflows.md), [04 §4.6](./04-firestore-data-model.md) |
| `ACTIVATING` dead-end after partial generation | Transient → resume from `installmentsGenerated`; terminal → dead-letter rolls to `PENDING`, installments retained & reused | [10 §10.1](./10-benefit-and-employment-workflows.md), [14 §14.5](./14-async-and-background-jobs.md) |
| Projection folds double-count under at-least-once delivery; coalescing undesigned; fold/rebuild race | Projections **recompute from source** (never fold increments) | [05 §5.2](./05-read-models-and-projections.md), [14 §14.3](./14-async-and-background-jobs.md) |
| Period attribution undefined (late posting: which month?); `scheduledCents` had no event source | Bucket by **`periodLabel`**; payment events carry it in metadata; `scheduledCents` generation/rebuild-maintained | [05 §5.3](./05-read-models-and-projections.md), [04 §4.9](./04-firestore-data-model.md) |
| Async idempotency-lease semantics undefined (healthy async ops looked expired) | `ASYNC_LEASE_TTL` + per-operation reclamation table | [08 §8.3](./08-idempotency-and-consistency.md), [21 §21.1](./21-deployment-and-operations.md) |
| Loan look-ahead (`nextContribution*`) and `openExceptionCount` had no specified writer | Recomputed in Phase-3 success txn / cancel + shift tasks; exception txns include the loan | [09 §9.1](./09-payment-processing.md), [10 §10.4](./10-benefit-and-employment-workflows.md) |
| Due-scan phrased a cross-document predicate as a query; suspended past-dues grow the scan | Per-item agreement check with caching, authoritative re-check in txn; shift policy removes suspended past-dues | [14 §14.2](./14-async-and-background-jobs.md) |
| `DELINQUENT`/`CLOSED` unreachable; blocking semantics unstated; DRAFT→PENDING ownerless | Seed/manual-only stated; `DELINQUENT` does **not** block processing; creation seed-only | [06 §6.6](./06-state-machines.md), [03 §3.6](./03-domain-model.md), [09 §9.1](./09-payment-processing.md) |

## Contract & schema completions

`acceptingPayments` + `plannedInstallmentCount` + `suspendedReason` added to the authoritative schema (04 §4.6, triple-flagged by reviewers) · `simulatedCharges` collection + deny-all rule + test · lease-reaper index (`idempotencyKeys status+leaseExpiresAt`) · `POST /exceptions` + `POST /admin/employers/{id}/status` endpoints · closed `PaymentFailureCode` enum + `TASK_FAILED` type + type→severity map · canonical `eventType` enum · event-mirror rule for entity-less events · 202-on-all-mutating-endpoints + explicit poll mechanism + pagination concretes + 404s · `Loan.benefitStatus`/employment-request enums tightened · common-field exemptions stated · activate error aligned to 422 · assign made status-neutral (+ unassign via null) · scenario 4 retitled to repeated `PAYMENT_FAILED` (occurrenceCount) with a standalone sync-failure exception · seed volumes reworded to full solved schedules + pinned demo credentials (`ops@/mgr@/admin@demo.test`) · wireframe corrections (chronology re-based to a Sep 2025 start, disjoint queue tabs, legal pills only, manager-gated Process, meters sum to the KPI, full filter enums) · simulator persistence + `simulatedOutcome` scripting + crash toggle · Cloud-Tasks-has-no-native-DLQ clarified (handler self-detection) · Firestore TTL gcloud step · first-admin `set_role` bootstrap + no-auto-provisioning statement · `TASK_EXECUTION_MODE=inline` local loop + `run_job` · root README/.gitignore + git init + Node 20 alignment.

All pinned operational values live in [21](./21-deployment-and-operations.md). The audit's "verified consistent" pass list (schemas ↔ enums ↔ rules ↔ indexes ↔ endpoints ↔ ID formats, residual math, stale-terminology grep) held with no regressions.
