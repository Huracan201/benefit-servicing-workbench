# 19 — Delivery Plan, Scope & Success Criteria

## 19.1 MVP scope

**Must have**
Firebase Auth + role-based authorization (custom claims); Firestore-first domain model; portfolio dashboard; loan portfolio; loan & benefit detail; contribution schedule; payment state machine; simulated payment adapter **with fencing `get_status`** (persisted to Firestore); failed-payment retry; **reconciliation sweeper**; employment termination + cascade; benefit suspend/**resume with schedule shift**/terminate; exception queue (deterministic, de-duplicated); immutable servicing timeline; idempotency **with leases**; Cloud Tasks **+ Cloud Scheduler** for async with **OIDC-authenticated `/internal` handlers**; structured logging; automated tests (incl. the concurrency + **fencing** + reconciliation gates); seed data; public deployment; architecture docs (this set incl. [21 deployment/ops](./21-deployment-and-operations.md)).

**Nice to have**
Employer dashboard; CSV export; webhook simulator; loan-servicer sync simulation; email notifications; manual balance reconciliation UI; collections status; SLA timers on exceptions; saved filters; user assignment queues.

**Out of scope** (see [01 §1.4](./01-product-overview.md), [20](./20-production-tradeoffs.md))
Real payment processing, ACH, production PII, credit underwriting, loan origination, interest/amortization, regulatory document generation, full accounting ledger, real collections, real loan-servicer integration, multi-jurisdiction compliance.

> **Change from v1 — the recovery/consistency machinery is in Must-have, not implied.** The reconciliation sweeper, idempotency leases, `get_status` adapter method, and Cloud Scheduler are explicit MVP scope because they are what make the financial claims true; they are not optional polish.

## 19.2 Delivery phases

**Phase 1 — Foundation.** Repo; Next.js; Django; Firebase Auth + **custom-claims** plumbing; Firestore + Emulator Suite; token validation; `common/` (money, timezone, state machine, firestore helpers); data-model types; seed script.

**Phase 2 — Domain & commands.** Role authorization; benefit activation + solved schedule generation; payment state machine; simulated adapter (charge + **get_status**); idempotency (**create-in-txn + lease**); servicing events (**sequence + mirror**); exception upsert; employment-status commands + cascade.

**Phase 3 — Async workflows.** Cloud Tasks; **Cloud Scheduler**; schedule-generation task; process-contribution task; **reconciliation sweeper**; cancel-future-contributions; projection updates; retry-safe/idempotent handlers + **dead-letter** routing.

> **Phase 3 security prerequisites** (deferred from the [Phase 1+2 security review](./engineering-reports/security-review-phase-1-2.md) §7 so they land *with* the code that makes them reachable): verify OIDC + add a **command-level role re-check** on the `/internal` handlers wired here; **rate-limit** the mutating command endpoints (DRF `ScopedRateThrottle`); **paginate `contributions.due()`** (cursor + limit) so the scheduler enqueue job never runs an unbounded scan; **validate `entityId`/`entityType`** on manual-exception creation; and **revoke refresh tokens on role demotion** (`auth.revoke_refresh_tokens`). The remaining defense-in-depth items (security response headers, frontend lockfile + `npm ci`, stop trusting the inbound correlation id) fold into Phase 5 hardening.

**Phase 4 — Workbench UI.** Dashboard; loan portfolio; loan detail; payment queue; exception workbench; **paginated** real-time subscriptions; role-based actions; loading/empty/error states.

**Phase 5 — Testing & hardening.** Backend unit; emulator integration (**concurrency + reconciliation + security-rule** gates); frontend; Playwright critical paths A & B; structured logging + metrics; health checks; authorization-boundary review; **defense-in-depth from the [Phase 1+2 security review](./engineering-reports/security-review-phase-1-2.md)** (security response headers, frontend lockfile + `npm ci`, correlation-id trust).

**Phase 6 — Deployment & docs.** Backend → Cloud Run; frontend → Vercel/Firebase Hosting; production Firestore (indexes + rules from source); Cloud Tasks + Scheduler; monitoring/alerts; README; architecture diagrams; screenshots; 2-minute demo.

## 19.3 Success criteria

The MVP is complete when:

- A manager can activate a benefit agreement; schedules generate without duplication and **sum exactly to the commitment**.
- A scheduled contribution can be processed successfully; posting updates loan and benefit values atomically.
- A failed contribution creates **one** operational exception and can be retried safely; a successful retry resolves that exact exception.
- **Duplicate requests cannot create duplicate postings** (the concurrency gate passes).
- **A crash between charge and finalize is recovered** by reconciliation with no double charge and no lost posting.
- Employment termination stops future benefits; past posted contributions remain unchanged; in-flight payments settle correctly.
- Every material action appears in an immutable, correctly-ordered timeline.
- Real-time updates appear in the workbench; aggregates converge.
- Authorization is enforced server-side; **direct client writes to protected collections are denied**; `users` self-write is denied.
- Automated tests cover the critical workflows, including the concurrency and reconciliation gates.
- The system is deployed publicly; the repo clearly explains architecture and tradeoffs.

**The engineering-review question** ([01 §1.6](./01-product-overview.md)) is answered "yes": Firestore is used responsibly as the primary store, with explicit transactional, idempotency, recovery, audit, and async controls — not a pretense that Firestore removes the need for them.
