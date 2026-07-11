# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

This is the **specification and design handoff package** for the **BenefitServicing Workbench** — an operations platform for servicing employer-sponsored student-loan repayment benefits. **The application is not built yet.** There is no `frontend/`, `backend/`, or `infrastructure/` directory; the specs reference them as the intended layout ([specs/02 §2.5](specs/02-architecture.md)) for Phase 1+ implementation.

What exists today:
- `specs/` — 22 numbered spec docs + `openapi.yaml` (the API contract) + `wireframes.html` (interactive UI mockup). Start at [`specs/README.md`](specs/README.md) — it is the index, the reading order, and the normative global conventions.
- `firebase/` — deployable Firestore security rules, composite indexes, emulator config, and a runnable **security-rules test suite** (TypeScript/Vitest).
- `.github/workflows/ci.yml` + `.spectral.yaml` — CI gates (mostly dormant until app code lands).

When asked to "build," "run the app," or "run the tests," remember most of that surface does not exist yet — the runnable pieces are only the rules tests and the OpenAPI lint (below). Scaffolding the app is the work of [specs/19](specs/19-delivery-and-scope.md) Phase 1.

## Commands that work today

All require Node + Java (both present); `firebase-tools` and Spectral are used via global/npx, not committed as deps.

```bash
# Firestore security-rule tests (needs Java for the emulator)
npm i -g firebase-tools                 # once
cd firebase && npm install              # once
npm run test:rules:ci                   # starts Firestore emulator, runs Vitest, tears down
npm run test:rules                      # if an emulator is already running

# Start the emulator suite (Auth + Firestore + UI) offline
cd firebase && firebase emulators:start --project=demo-benefitservicing-workbench

# Lint the API contract (run from repo root; uses .spectral.yaml → spectral:oas)
npx @stoplight/spectral-cli lint specs/openapi.yaml --fail-severity=error
```

- The emulator/tests use the **`demo-`-prefixed project id** so they run fully offline with no GCP credentials. See [`firebase/emulator/README.md`](firebase/emulator/README.md).
- `firebase.json` lives **inside `firebase/`** (not the repo root) — run `firebase` from that directory or pass `--config firebase/firebase.json`.
- Rules and indexes are the **source of truth**; never edit them in the Firebase console. Deploy with `firebase deploy --only firestore:rules,firestore:indexes`.

**Once app code lands** (per specs/19 & specs/02 §2.5): backend is Django (`python manage.py test --tag=unit|emulator`, Cloud Run); frontend is Next.js (`npm run dev|build|test`, Vitest + Playwright). The CI (`ci.yml`) already defines these jobs, gated on a file-existence `detect` job so they activate automatically when the directories appear.

## Architecture (the load-bearing ideas)

The design answers one question ([specs/01 §1.6](specs/01-product-overview.md)): *responsible use of Firestore for a financial workflow without pretending it removes the need for explicit transactional, idempotency, audit, and async controls.* These pieces interlock — understanding any one requires the others:

- **CQRS-style read/write split** ([specs/02](specs/02-architecture.md) P7). Firestore is the single primary store. **Every write goes through a Django command** (business commands, never generic document updates); Django holds transactions, state-machine validation, invariants, idempotency. **The frontend only reads**, via Firestore client-SDK subscriptions to read models. Consequence that drives everything else: **reads are authorized by Firestore security rules, writes by Django** — both from the same Firebase **custom-claim** role ([specs/12](specs/12-auth-and-security.md)).

- **Financial-correctness core** — read [specs/06](specs/06-state-machines.md) → [07](specs/07-financial-rules.md) → [08](specs/08-idempotency-and-consistency.md) → [09](specs/09-payment-processing.md) together. Integer-cent money; explicit state machines with in-transaction status preconditions (also the race-resolution mechanism); immutable append-only `servicingEvents`; **idempotency records created *inside* the state-transition transaction with a lease**; and the **two-phase payment** (transition → external adapter call → finalize) made crash-safe by a **reconciliation sweeper** that re-queries the processor by the attempt's deterministic key. This recovery/idempotency machinery is the heart of the design, not optional polish.

- **Async for unbounded work** ([specs/14](specs/14-async-and-background-jobs.md)). Cloud Tasks (per-item, retryable) for schedule generation, cancel-future-contributions, projections, and reconciliation; Cloud Scheduler for time triggers. Every handler is idempotent (deterministic IDs + preconditions).

- **Read models are eventually consistent** ([specs/05](specs/05-read-models-and-projections.md)). Portfolio/employer aggregates are updated **off the payment transaction** (to avoid single-document write contention — Firestore's ~1 write/sec/doc limit) and reconciled by a scheduled rebuild. Never read a projection to make a financial decision.

- The `backend/common/` module ([specs/02 §2.5](specs/02-architecture.md)) is intended to hold the **single** implementation of the state machine, money/invariant helpers, and Firestore transaction wrappers — safety-critical code that must not be re-derived per Django app.

`specs/appendix-a-review-findings.md` is the traceability matrix: this is a **v2** spec that closed specific correctness gaps in a v1 draft; each finding maps to where it's resolved.

## Conventions that bind any implementation

From [specs/README.md](specs/README.md) "Global conventions" (normative) — deviating from these will break the design's guarantees:

- **Money is integer US cents** (`*Cents` fields, `int`); every money doc carries `currency: "USD"`. No floats in the money path.
- **`SYSTEM_TIMEZONE`** (default `America/New_York`) derives all calendar periods and `scheduledDate` (set to noon to dodge DST/midnight edges). Never derive a period in UTC in one place and render it locally in another.
- **`revision`** is a per-doc audit counter (not `version`); optimistic concurrency is opt-in via a client-supplied `expectedRevision` / `If-Match`, not implied by the field.
- **Deterministic IDs** where "create exactly once" matters: contribution `{agreementId}__{installmentNumber:03d}`, attempt `{contributionId}__att_{NNN}`, auto-exception `{entityId}__{exceptionType}`, idempotency key = the client header value.
- **Roles** (`OPERATIONS_USER` / `SERVICING_MANAGER` / `ADMINISTRATOR`) come from Firebase custom claims — the authoritative source for both security rules and Django.
- Schedule installment amounts are **solved at generation** so `Σ == totalCommitment` exactly (final installment absorbs the residual — [specs/07 §7.3](specs/07-financial-rules.md)).
- Naming decisions to respect when writing code: `primaryLoanId` (nullable convenience; the canonical borrower→loan link is `loan.borrowerId`), `baseMonthlyContributionCents`, `severityRank` (numeric, for sorting — `severity` string doesn't sort by importance), payment attempts live only in the `scheduledContributions/{id}/attempts` subcollection.

## Working in this repo

- **`specs/openapi.yaml` is the authoritative API contract.** Doc 11 is the human overview; if they disagree, the YAML wins. Change endpoints in the YAML in the same change as the doc, and keep Spectral green.
- **Keep the 22 specs internally consistent.** A modeling decision touches many docs (this bit before: `version`→`revision`, `activeLoanId`→`primaryLoanId`, contribution-ID scheme, `severityRank`, portfolio indexes → `loanWorkbenches`). After a change, grep across `specs/` for stale references; superseded decisions are documented with **"Change from v1"** callouts rather than silent edits.
- The repo is **not currently a git repository**; CI targets GitHub Actions, so initialize git before relying on it.
