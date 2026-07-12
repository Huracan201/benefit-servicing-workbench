# Engineering Report — Phase 1: Foundation

**Project:** BenefitServicing Workbench (`Huracan201/benefit-servicing-workbench`)
**Phase:** 1 — Foundation (per [specs/19 §19.2](../19-delivery-and-scope.md))
**Status:** ✅ Complete — merged to `main` via PR #1 (`1261b56`) on 2026-07-11
**Author:** Engineering (Claude Code, multi-agent) · reviewed by Rick Almodovar

---

## 1. Summary

Phase 1 delivered the bootable, tested **foundation** for the BenefitServicing Workbench — a Firestore-first platform for servicing employer-sponsored student-loan repayment benefits. It stands up the backend (Django + DRF, Firestore-only) and frontend (Next.js) skeletons, and — most importantly — the **framework-free, safety-critical core** (`backend/common/`) that every later phase composes: the money/residual solver, deterministic ID scheme, all seven domain state machines, the financial invariants, and the shared enums.

No business workflows ship in Phase 1 (those are Phase 2+); the goal was a **correct, verified skeleton** with green CI, so subsequent phases build on a trustworthy base rather than a moving one.

**Headline outcomes**
- 73 files, +4,494 lines across two commits; **0 net production bugs** shipped after review.
- **60 backend unit tests** (framework-free core) + **12 Firestore security-rule tests** — all green on CI runners.
- Full CI pipeline active and passing: `detect → openapi-lint → firestore-rules → backend → frontend`.
- One **critical** defect and five other issues caught in review and fixed **before** merge (see §6).

---

## 2. Scope

**In scope (delivered)**
- `backend/common/` — the safety-critical core (money, periods, ids, state machines, invariants, enums, errors, firestore client).
- `backend/config/` — lean Django project: DRF, CORS, JSON logging, Firestore-only (no ORM).
- `backend/firebase_auth/` — Firebase token auth, role permissions, `/internal` OIDC middleware, `set_role` bootstrap command.
- `backend/core/` — health/readiness probes, correlation-id middleware, structured logging, document TypedDicts.
- `frontend/` — Next.js App Router + TypeScript + Tailwind scaffold: emulator-aware Firebase client, paginated subscription hooks, typed enums mirroring the backend, app shell + stub screens + a smoke test.
- CI wiring for the above (backend + frontend jobs, gated by a `detect` job).

**Explicitly out of scope (later phases)**
- Domain command endpoints, the two-phase payment, idempotency service, servicing events (Phase 2).
- Cloud Tasks / Scheduler async handlers (Phase 3).
- Real UI screens (Phase 4).

---

## 3. What was delivered

### 3.1 Backend (39 Python modules)

| Package | Modules | Responsibility |
|---------|:------:|----------------|
| `common/` | 16 | **Framework-free safety core** (see §3.2) — imports no Django/Google at module load |
| `config/` | 5 | Django settings (Firestore-only, dummy DB), URLconf, WSGI/ASGI, JSON logging |
| `core/` | 8 | Health/readiness, correlation-id middleware, structured logging, `schema.py` doc TypedDicts, capped pagination |
| `firebase_auth/` | 9 | DRF `FirebaseAuthentication`, role permissions, `/internal` OIDC middleware, `set_role` command |
| — | 1 | `manage.py` |

### 3.2 The safety-critical core (`backend/common/`)

This is the load-bearing module — the single implementation of the rules every future command must honor. It is deliberately **dependency-free** (pure stdlib) so it runs offline and is trivially unit-testable:

- **`money.py`** — `solve_schedule(total, term)` distributes the residual onto the final installment so `Σ == total` exactly (e.g. 3,000,000 / 36 → 35 × 83,333 + 83,345); `cap_posted(...)`. Integer cents only, no floating point.
- **`state_machines.py`** — all seven machines (contribution, attempt, benefit, exception, employment, loan, employer) as frozen transition tables, with `assert_transition` / `can_transition`.
- **`invariants.py`** — the seven financial invariants I1–I7 (balance ≥ 0, `amountPaid ≤ commitment`, `remaining == total − paid`, posted-within-caps, schedule-sums-to-commitment, posted-immutable, mutual-pointer integrity).
- **`ids.py`** — deterministic ID formatters (contribution / attempt / exception / processor key).
- **`periods.py`** — `SYSTEM_TIMEZONE`-aware period labels, the noon scheduling rule, month-shift with end-of-month clamping.
- **`enums.py`** — every domain enum as `StrEnum` (so `f"{status}"` yields the value), plus `SEVERITY_RANK` / `ROLE_ORDER`.

### 3.3 Frontend (27 files)

Next.js App Router scaffold: lazy, environment-safe Firebase client (initializes nothing at module load); `useDocument` / `useCollectionPage` hooks that **enforce `limit` + cursor pagination** (per [specs/05 §5.6](../05-read-models-and-projections.md)); typed string-literal enums and document interfaces mirroring the backend with **zero drift**; app shell (nav + top bar), stub screens, a `StatusBadge` component, and a passing smoke test.

---

## 4. Process — how it was built

Phase 1 used a **multi-agent workflow** followed by **independent adversarial QA**, then an external AI reviewer:

1. **Build** — a 5-agent workflow generated the scaffold in parallel from one shared canonical brief (exact enums, ID formats, state-transition tables), with strict directory ownership to prevent cross-contamination.
2. **QA** — three independent review agents (core correctness / Django-auth / frontend) plus a lead adversarial verification pass: 400+ property-tested money cases, a full state-machine reconstruction diff against a hand-encoded copy of the spec, and an AST scan confirming no import-time side effects.
3. **External review** — CodeRabbit ran on the PR and surfaced additional issues (§6).

This layered approach is why a critical runtime defect (§6, item 2) was caught before merge despite the offline sandbox being unable to execute the Firebase SDK.

---

## 5. Verification & test coverage

| Suite | Count | Where it runs | Result |
|-------|:-----:|---------------|--------|
| Backend core unit tests | 60 | Offline (pure stdlib) + CI | ✅ pass |
| Firestore security-rule tests | 12 | Firestore emulator (CI + local) | ✅ pass |
| OpenAPI contract lint | — | CI (Spectral) | ✅ pass |
| `manage.py check` | — | CI (backend job) | ✅ pass |
| Frontend lint + build + smoke test | 1 | CI (Node 20) | ✅ pass |

**Core test emphasis:** the residual solver is property-tested for exact summation across hundreds of `(total, term)` pairs including edge cases (term = 1, total = 0, non-divisible); every allowed state transition is asserted and a representative disallowed set is asserted to raise; all seven machines have exact-table assertions so a dropped/added transition fails CI.

**What could and couldn't be verified offline:** the framework-free core was fully executed locally; Django boot (`manage.py check`), `pip install` resolution, and the Next.js build were verified on GitHub runners (which have network). This split is a permanent property of the dev environment and shaped the design — the safety-critical logic is deliberately runnable without any third-party dependency.

---

## 6. Issues found & fixed (before merge)

Ten issues were caught across the QA passes and CodeRabbit; all were resolved prior to merge. The most significant:

| # | Severity | Issue | Resolution |
|---|----------|-------|-----------|
| 1 | 🟠 QA | `common/periods.py` read `BSW_SYSTEM_TIMEZONE` while settings/spec use `SYSTEM_TIMEZONE` — the deploy var was silently ignored | Aligned on `SYSTEM_TIMEZONE` |
| 2 | 🔴 Critical (CodeRabbit) | `admin_init.py` called `credentials.AnonymousCredentials()`, which does not exist on `firebase_admin.credentials` — the emulator-auth path would raise `AttributeError` the moment `FIREBASE_AUTH_EMULATOR_HOST` was set | Wrapped `google.auth`'s `AnonymousCredentials` in a `credentials.Base` subclass |
| 3 | 🟠 | `Django>=5.0,<5.2` could resolve to **EOL 5.1.x** | Bumped to the 5.2 **LTS** line; DRF → 3.16 |
| 4 | 🟠 | `pyjwt` unpinned (transitive via firebase-admin, floor only 2.5.0) | Added explicit `pyjwt[crypto]>=2.13,<3.0` |
| 5 | 🟠 | DRF `PAGE_SIZE` set a default but not a **max** — a client could request an unbounded page | Added a capped pagination class (`max_limit=200`, per [specs/21 §21.1](../21-deployment-and-operations.md)) |
| 6 | 🟡 | `correlationId` never reached logs (wrong formatter wired) | Wired the contextvar-aware formatter; removed the duplicate |
| 7 | 🟡 | Enums rendered as `Severity.LOW` in f-strings | Switched to `StrEnum` |
| 8 | 🟡 | `set_role` wrote the user mirror and audit event separately | Combined into one Firestore batch (atomic) |
| 9 | 🟡 | RTL v16 peer `@testing-library/dom` undeclared | Declared it explicitly |
| 10 | 🟡 | Per-page `onSnapshot` pagination can shift rows across pages | Documented the per-spec tradeoff (kept by design) |

**Notable:** item 2 is exactly the class of bug static review misses — the three QA agents correctly reasoned it was only *called* at runtime, but couldn't *execute* it offline; CodeRabbit's dynamic analysis + dependency lookup caught it. A useful data point on layering static and executable review.

Three CI-configuration bugs were also fixed while standing up the pipeline (invalid job-level `hashFiles()`, a Spectral literal-null crash, and the firebase-tools JDK-21 requirement), so the first real code push landed green.

---

## 7. Key technical decisions

- **Firestore-only, no Django ORM.** The Django app uses a dummy in-memory SQLite that is never touched; all state lives in Firestore. Keeps the framework thin and avoids a second source of truth.
- **Framework-free safety core.** Money, state machines, and invariants import no Django/Google at module load. This makes them offline-testable, prevents import-time side effects (verified by AST scan), and gives every future phase one canonical implementation to compose.
- **Two enforcement points, one source of role truth.** Writes are authorized by Django; reads by Firestore security rules; both derive role from the same Firebase custom claim.
- **`/internal` ingress hardening.** Task/scheduler handler URLs (internet-reachable on Cloud Run) are gated by Google OIDC (audience + invoker SA), with a shared-secret bypass only under the emulator. Fails closed.
- **CI `detect` gate.** A lightweight job checks for `backend/manage.py` / `frontend/package.json` / `e2e.sh` and activates the corresponding jobs, so the pipeline stayed green through the spec-only period and lit up automatically as code landed.

---

## 8. Metrics

| Metric | Value |
|--------|-------|
| Commits (Phase 1) | 2 (`a18f0d8`, `337975d`) + merge `1261b56` |
| Files changed | 73 (+4,494 / −11) |
| Backend Python modules | 39 |
| Frontend files | 27 |
| Backend unit tests | 60 |
| Firestore rule tests | 12 |
| Review issues found → fixed | 10 → 10 |
| CI jobs green | 5 / 5 (E2E correctly skipped) |
| Elapsed | ~1 working day (2026-07-11) |

---

## 9. Known limitations (by design)

- No business workflows yet — activation, payment, exceptions, etc. are Phase 2.
- Frontend screens are stubs; real data binding is Phase 4.
- No committed frontend lockfile yet (CI uses `npm install`); a lockfile + `npm ci` is a fast follow.
- E2E job is defined but inert until `infrastructure/scripts/e2e.sh` lands (Phase 5).
- Two non-blocking CI annotations remain: a Node-20 action-runtime deprecation notice, and one unused OpenAPI component (Spectral warning, not error).

---

## 10. What Phase 1 enables

The foundation is the reason Phase 2 (the domain command layer) can be built with confidence: every command composes `common/`'s already-verified state machines, money math, and invariants inside Firestore transactions. Because the core is frozen and green, Phase 2 review can focus entirely on **orchestration correctness** (idempotency ordering, transaction boundaries, the two-phase payment) rather than re-litigating the primitives.

**Next:** Phase 2 — domain commands (benefit activation, the two-phase payment with idempotency + reconciliation, servicing events, exception workflow), per [specs/19 §19.2](../19-delivery-and-scope.md).

---

*Related: [specs/README.md](../README.md) (spec index) · [appendix-b-handoff-audit.md](../appendix-b-handoff-audit.md) (the v2.1 spec pre-handoff audit) · PR [#1](https://github.com/Huracan201/benefit-servicing-workbench/pull/1).*
