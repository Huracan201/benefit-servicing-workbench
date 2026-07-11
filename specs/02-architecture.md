# 02 — Architecture

## 2.1 Technology stack

**Frontend**
- Next.js (App Router), React, TypeScript, Tailwind CSS
- Firebase Authentication (client SDK)
- Firestore client SDK — **read-only** real-time subscriptions to approved read models
- Vitest + React Testing Library; Playwright for E2E
- Hosting: Vercel or Firebase Hosting

**Backend**
- Python, Django, Django REST Framework
- Firebase Admin SDK (token verification, Firestore writes, custom claims)
- Cloud Run (container), Cloud Tasks, **Cloud Scheduler**, Pub/Sub (only where fan-out earns its keep)
- Cloud Logging, Cloud Monitoring, Error Reporting

> **Change from v1 — Cloud Scheduler added.** v1's stack listed Cloud Tasks but no scheduler, while workflow 21.2 referenced a "scheduled process" that triggers due contributions. Without a scheduler the MVP is manual-only. v2 adds **Cloud Scheduler** to enqueue due-contribution processing and periodic maintenance jobs. See [14](./14-async-and-background-jobs.md).

**Database**
- Cloud Firestore, Native mode — primary system of record for all authoritative financial and servicing state.

## 2.2 Architecture principles

### P1 — Backend-owned commands (write path)
The frontend must never directly write loan balances, benefit totals, payment statuses, employment-state transitions, exception resolutions, or servicing events. All such changes go through Django **command endpoints** that expose *business operations* ("activate benefit", "process contribution"), not generic document updates. The API never accepts a raw target status.

### P2 — Firestore is the primary store
Firestore holds authoritative account/payment/benefit state, immutable servicing events, operational exceptions, idempotency records, and read-optimized projections. There is no second database in the MVP. Where Firestore's model is a poor fit (cross-entity reporting, ledger-style reconciliation), that is called out as a production tradeoff ([20](./20-production-tradeoffs.md)), not silently worked around.

### P3 — Model around access patterns
Documents are shaped for the screens and workflows they serve. Avoid relational habits: frequent joins, cross-collection scans, ad-hoc reporting across the whole dataset, deep normalization. Denormalize deliberately, and document the propagation/staleness policy for every denormalized field ([04](./04-firestore-data-model.md)).

### P4 — Explicit financial invariants
Money state is protected by: Firestore transactions, document preconditions, deterministic IDs for uniqueness, integer cents, state-machine validation, and append-only events. Invariants are enumerated in [07](./07-financial-rules.md) and enforced in the command layer, not assumed.

### P5 — Event-audited domain
Every material state change writes an immutable `servicingEvent` in the same transaction as the change. See [08](./08-idempotency-and-consistency.md) for the ordering/atomicity rules.

### P6 — Asynchronous work for unbounded operations
Any operation whose write count is variable or large is split: a small, bounded, transactional "commit the decision" step, followed by bounded-batch asynchronous work via Cloud Tasks. Example: employment termination transactionally flips the account/benefit status, then a Cloud Task cancels future contributions in bounded batches. See [14](./14-async-and-background-jobs.md).

### P7 — Reads and writes are separated (CQRS-lite)
This is made explicit in v2 because it drives the security model:

- **Write path:** client → Django command → Firestore transaction. Django is always in the loop; it enforces auth, state machines, invariants, idempotency.
- **Read path:** client → Firestore client SDK subscription → read models. **Django is not in the read path.** Therefore read authorization is enforced entirely by **Firestore security rules**, and those rules must know the user's role via custom claims. See [12](./12-auth-and-security.md).

> **Change from v1 — read-path enforcement made explicit.** v1 said "authorization enforced by Django" and "clients may read permitted collections based on role" without reconciling that Django is not in the read path. v2 states the split plainly: Django enforces the *write* path; security rules enforce the *read* path; both derive role from the same custom claims.

## 2.3 High-level system diagram

```
                 ┌─────────────────────────────────────────┐
                 │   Operations user (browser, desktop)     │
                 └───────────────┬──────────────┬───────────┘
                                 │              │
                Firebase Auth    │              │  Firestore client SDK
                (ID token)       │              │  (read-only subscriptions)
                                 ▼              │
        ┌────────────────────────────────┐     │
        │   Django API (Cloud Run)        │     │
        │   - verify Firebase ID token    │     │
        │   - role check (custom claims)  │     │
        │   - business commands           │     │
        │   - Firestore transactions      │     │
        │   - state-machine validation    │     │
        │   - idempotency enforcement     │     │
        └───────┬─────────────────┬───────┘     │
                │ Admin SDK        │ enqueue     │
                ▼                  ▼             │
        ┌───────────────┐  ┌──────────────────┐ │
        │ Cloud         │  │ Cloud Tasks /    │ │
        │ Firestore     │◄─┤ Cloud Scheduler  │ │
        │ (primary)     │  │ (async handlers, │ │
        │               │  │  also Django API)│ │
        └───────┬───────┘  └──────────────────┘ │
                │  real-time snapshots           │
                └────────────────────────────────┘
                        (read models → browser)
```

Key point: **every arrow that mutates Firestore originates from Django** (either a synchronous command or an async task handler, which is also Django code on Cloud Run). The only client→Firestore arrow is read-only subscriptions.

## 2.4 Security rules deny-by-default

Firestore security rules deny all client writes to protected collections and allow reads only to authenticated users holding a valid servicing role claim. The backend uses a service account that bypasses rules. Full rules and their test plan are in [12](./12-auth-and-security.md).

## 2.5 Repository structure

```
benefit-servicing-workbench/
├── frontend/                 # Next.js app
│   ├── app/ components/ features/ hooks/ lib/ services/ types/ tests/
│   └── package.json
├── backend/                  # Django project
│   ├── config/               # settings, urls, wsgi/asgi
│   ├── auth/                 # Firebase token verification, role decorators
│   ├── employers/ borrowers/ loans/ benefits/
│   ├── contributions/ payments/                # payment processing + reconciliation
│   ├── servicing/ exceptions/ idempotency/     # events, exceptions, idempotency
│   ├── tasks/                # Cloud Tasks + Scheduler handlers
│   ├── projections/          # read-model builders
│   ├── common/               # money, timezone, firestore helpers, state machines
│   ├── tests/
│   └── manage.py
├── firebase/
│   ├── firestore.rules
│   ├── firestore.indexes.json
│   ├── firebase.json
│   └── emulator/
├── infrastructure/
│   ├── cloud-run/ cloud-tasks/ cloud-scheduler/ scripts/ environments/
├── specs/                    # this documentation set
├── docs/                     # architecture diagrams, screenshots, demo script
├── README.md  docker-compose.yml
└── .github/workflows/        # CI
```

> **Change from v1 — shared `common/` module.** The state machine, money helpers, timezone/period helpers, and Firestore transaction wrappers are used by every domain app and by the task handlers. v2 centralizes them in `backend/common/` so the state machine and invariant checks have exactly one implementation (they are the safety-critical code and must not be re-derived per app).

## 2.6 Environments

| Env | Firestore | Auth | Purpose |
|-----|-----------|------|---------|
| local | Emulator Suite | Emulator | dev + emulator integration tests |
| ci | Emulator Suite | Emulator | automated tests |
| demo | dedicated GCP project | Firebase (demo tenant) | public seeded demo |

Firestore indexes and security rules are defined in source control (`firebase/`) and deployed per environment; they are never edited by hand in the console.
