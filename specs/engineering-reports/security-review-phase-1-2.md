# Security Review — Phase 1 + 2 (merged codebase)

**Project:** BenefitServicing Workbench (`Huracan201/benefit-servicing-workbench`)
**Scope reviewed:** all merged Phase 1 (foundation) + Phase 2 (command layer, parts 1 & 2) — `main` `abf3d33`
**Type:** read-only adversarial security audit + quick-win remediation
**Status:** ✅ Review complete · 🔧 remediation applied on `security/phase-1-2-hardening` (pending QA-verify → CI → merge)
**Date:** 2026-07-12

---

## 1. Summary

A read-only, offline, adversarial security audit of the whole merged codebase, run as a **three-reviewer team plus a lead probe**, each with a disjoint surface. **Verdict: no CRITICAL and no HIGH exploitable findings.** The system is production-minded: money is server-authoritative, the idempotency/fencing machinery holds, there is no injection/RCE surface, no committed secrets, deny-by-default rules, and a correct CORS posture. The scariest surface a servicing product could have — an unauthenticated task handler that executes SYSTEM commands — **does not exist yet** (`/internal` routes are commented out for Phase 3 and the forward-auth middleware is fail-closed).

Everything found is **MEDIUM or below** and clusters into four buckets: (a) **fail-open deploy defaults**, (b) **two pinned dependency CVEs** (neither currently exploitable), (c) **unbounded input/resource growth**, and (d) **Phase-3 prerequisites** to close before the async handlers and real scale land. The quick-win subset (a, b, c-inputs, PENDING TTL) is remediated in this change; the rest is tracked for Phase 3.

---

## 2. Method — the review team

Four independent passes over the merged tree, adversarially framed (each reviewer was told to *find and prove* exploitable issues, not to certify):

| Reviewer | Surface |
|----------|---------|
| **sec-authz** | Auth boundary — token verification (audience/issuer/revocation), the role matrix on every command view, custom-claim propagation, self-escalation |
| **sec-appsec** | Financial correctness — money authority, idempotency/fencing, the two-phase payment, injection/RCE, audit integrity, request-body input handling |
| **sec-infra** | Secrets & info-disclosure, dependency CVEs, CORS, DoS/resource bounding, IAM, CI supply-chain, logging hygiene |
| **lead probe** | Reachability of the `/internal` OIDC-gated task/scheduler handlers (the highest-impact surface if live) |

All work was offline against the source; no changes were made during the review phase.

---

## 3. What's solid (verified, not assumed)

- **Money is server-authoritative.** The charged amount is always the server's `scheduledAmountCents`; `/process` ignores its request body; there is no create-agreement / set-balance endpoint; `cap_posted` and invariants I1–I7 are asserted **inside** the finalize transaction; `simulatedOutcome` is seed-only — a client cannot steer a payment result.
- **Idempotency & fencing.** `requestHash` binds `method + path + canonical-body` and is further guarded by an `entityId` check, so a key cannot be replayed across entities or commands (→ 409). The simulated adapter **tombstones** unknown keys (unbypassable fencing), and the finalize guard prevents a stranded charge.
- **Auth boundary.** DRF defaults are fail-closed (`FirebaseAuthentication` + `IsAuthenticated`, `UNAUTHENTICATED_USER=None`); the correct role gate is present on all command views; tokens are audience/issuer-pinned with `check_revoked` on writes; the actor is always taken from the verified token; `users/{uid}` client-write is denied (no role self-escalation).
- **No injection / RCE.** No `eval` / `exec` / `pickle` / `yaml.load` / `subprocess`; no Firestore query injection (fields and operators are hardcoded).
- **No committed secrets.** ADC-only; zero service-account/private-key files in the tree; idempotency keys are SHA-256-hashed in logs; only `*.example` env files are committed; the demo password is documented, env-overridable, and never logged.
- **Rules & transport.** Firestore rules are deny-by-default and role-gated, with `idempotencyKeys` and `simulatedCharges` client-invisible; CORS is an allowlist with `CORS_ALLOW_CREDENTIALS=False` (the classic wildcard+credentials misconfig is absent); no session/cookie/CSRF surface (stateless Bearer).
- **CI & IAM.** Least-privilege `GITHUB_TOKEN` (`contents: read`), no `pull_request_target`, no untrusted interpolation, no secrets in CI (fully emulator/offline); dedicated runtime + invoker service accounts with scoped roles.
- **The `/internal` surface is not live.** Handlers are commented out (Phase 3) and the forward-auth middleware fails closed (Google OIDC, `aud` + invoker-SA + `email_verified`, constant-time compare; the emulator shared-secret path is gated on `FIRESTORE_EMULATOR_HOST`). **Zero live unauthenticated-exec attack surface today.**

---

## 4. Findings (consolidated, most-cited first)

All MEDIUM or below. "×3" marks agreement across all three reviewers.

| # | Sev | Finding | Location |
|---|-----|---------|----------|
| 1 | 🟡 MED ×3 | **Fail-open prod defaults** — `DEBUG=True`, `ALLOWED_HOSTS=*`, dev `SECRET_KEY` / `INTERNAL_DEV_SECRET` defaults; one missed env var → traceback disclosure + Host-header poisoning. No startup guardrail. | `config/settings.py` |
| 2 | 🟡 MED | **No DRF exception handler** — an unexpected 500 bypasses the clean envelope → full traceback if DEBUG on (pairs with #1). | `config/settings.py`, all views |
| 3 | 🟡 MED | **`next` 14.2.15 < 14.2.25 — CVE-2025-29927** (critical middleware auth bypass). *Not* exploitable here (no `middleware.ts`; auth is Firebase/Django) but a critical-rated CVE in a pinned dep. | `frontend/package.json` |
| 4 | 🟡 MED | **`gunicorn` <23.0 — CVE-2024-6827** (HTTP request smuggling); mitigated behind Cloud Run's Front End. | `backend/requirements.txt` |
| 5 | 🟡 MED | **Unbounded free-text** (note text, exception summary/details/reason) → doc/payload DoS + latent stored-XSS (harmless now — the frontend is a stub — but persisted unsanitized). | `notes/`, `exceptions/` views |
| 6 | 🟡 MED | **No immediate revocation on demotion** — `set_user_role` doesn't revoke tokens, so a demoted insider keeps access for the ≤1h token TTL (spec §12.3 promised this). | `administration/services.py` |
| 7 | 🟡 MED | **Orphaned PENDING idempotency records never expire** (`expiresAt=None` on PENDING) → unbounded growth without the (unwired) reaper. | `idempotency/service.py` |
| 8 | 🟢 LOW–MED | Body `entityId`/`entityType` unvalidated → a `/` yields an uncaught 500; **no rate limiting** on the mutating money API; unbounded `contributions.due()` scan (feeds the Phase-3 scheduler). | `exceptions/`, `repositories/`, DRF config |
| 9 | 🟢 LOW | Commands re-check role for audit only (close before Phase-3 `/internal`); client-controlled `X-Correlation-Id` (audit confusion); no frontend lockfile / `npm ci`; `/readiness` does an unauth Firestore round-trip; no security response headers. | various |

---

## 5. Remediation applied (this change)

The quick-win subset — small, high-value, and independent of Phase 3 — on `security/phase-1-2-hardening`:

| Finding | Change | Files |
|---------|--------|-------|
| **1** | Default `DEBUG=False` and `ALLOWED_HOSTS` to a localhost list (no wildcard). Add an **`ENVIRONMENT=production` boot guardrail** that raises `ImproperlyConfigured` on any leftover dev default (DEBUG on, dev `SECRET_KEY`/`INTERNAL_DEV_SECRET`, wildcard host) — **fail-closed** instead of fail-open. | `config/settings.py`, `.env.example` |
| **2** | Register a DRF `EXCEPTION_HANDLER`: any uncaught exception renders a generic `INTERNAL_ERROR` 500 with the detail logged server-side (structured logger, `exception` field) — **no traceback in the body, independent of DEBUG**. | `core/exception_handler.py` (new), `config/settings.py` |
| **3** | Bump `next` 14.2.15 → **14.2.32** (and `eslint-config-next` to match). | `frontend/package.json` |
| **4** | Bump `gunicorn` `>=22.0,<23.0` → **`>=23.0,<24.0`**. | `backend/requirements.txt` |
| **5** | Add generous free-text length caps (400 on over-long input): note text & exception details ≤ 10 000, summary / resolution note / dismiss reason ≤ 1 000, entity ids / types ≤ 200. Caps live as shared constants. | `commands/base.py`, `notes/views.py`, `exceptions/views.py` |
| **7** | Stamp a **TTL-eligible `expiresAt`** on PENDING records (now + 7 days) so an orphaned key from a crashed driver stays eligible for Firestore TTL deletion; `complete()`/`fail()` still overwrite it with the 30-day retention window once the outcome is known. | `idempotency/service.py` |

**Design note:** the guardrail is armed only by an explicit `ENVIRONMENT=production`, so CI, local dev, and the emulator (which leave it `development`) are unaffected — while the *secure defaults* protect a real deploy even if that flag is forgotten. Fail-closed with a safe fallback.

---

## 6. Verification

| Check | Result |
|-------|--------|
| `py_compile` all changed backend files | ✅ |
| No existing test asserts on changed behavior (`expiresAt` on PENDING, DEBUG, the 500 body) | ✅ confirmed by grep |
| No test fixture exceeds the new length caps | ✅ confirmed |
| CI never sets `ENVIRONMENT=production` → guardrail dormant in CI/emulator | ✅ confirmed |
| All new imports used (no lint dead-imports) | ✅ |
| Real emulator run (`manage.py check` + integration suite under the new defaults) | ⏳ pending CI |
| `npm install` resolves `next@14.2.32` + frontend build | ⏳ pending CI |

The offline sandbox can only `py_compile`; the authoritative run is the CI emulator job, as with every prior phase.

---

## 7. Deferred (tracked, not fixed here)

Intentionally out of this quick-win change — most are Phase-3 prerequisites that should land **with** the code that makes them reachable:

- **#6 revocation-on-demotion** — call `auth.revoke_refresh_tokens(uid)` in `set_user_role`.
- **#8 rate limiting** — DRF `ScopedRateThrottle` on the command endpoints.
- **#8 `contributions.due()` pagination** — cursor + limit; enqueue per page (feeds the Phase-3 scheduler).
- **#8 entity-id validation** — format check + `entityType` allowlist at the exception views.
- **#9 `/internal` handler auth + command-level role re-check** — close **before** Phase 3 wires the task routes.
- **#9 defense-in-depth** — security response headers, frontend lockfile + `npm ci`, stop trusting the inbound correlation id.

---

## 8. Key decisions

- **Secure-by-default + an explicit fail-closed guardrail**, rather than opt-in strictness. The two highest risks (DEBUG traceback disclosure, wildcard-host poisoning) are now safe even if the prod flag is forgotten; the guardrail catches the dev-secret case loudly at boot.
- **The 500 handler is independent of DEBUG** — a generic envelope is returned even in a misconfigured (DEBUG-on) deploy, so the fix does not rely on #1 also being right.
- **Bump the CVEs even though neither is currently exploitable** (no `middleware.ts`; gunicorn sits behind the GFE). A pinned dependency below a known security release is debt that ages badly; the cost to bump is ~zero.
- **Length caps are DoS bounds, not business rules** — deliberately generous, chosen to reject pathological payloads without constraining real servicing usage.
- **PENDING TTL is a backstop, not the real fix.** The designed sweeper is the Phase-3 `reap-expired-leases` job; the TTL stamp bounds growth until that handler exists.

---

## 9. What's next

QA-verify these fixes (adversarial pass over the guardrail / handler / bounds / TTL) → commit → CI (the real emulator run under the new defaults) → CodeRabbit → merge. Then fold the **deferred** items into the Phase-3 work as the async handlers and projections land, where they become both reachable and testable.

---

*Related: [phase-1-foundation.md](./phase-1-foundation.md) · [phase-2-command-layer.md](./phase-2-command-layer.md) · [phase-2-part-2-remaining-commands.md](./phase-2-part-2-remaining-commands.md) · [specs/12](../12-auth-and-security.md) (auth & roles) · [specs/08](../08-idempotency-and-consistency.md) (idempotency + lease) · [specs/21 §21.3](../21-deployment-and-operations.md) (deployment config).*
