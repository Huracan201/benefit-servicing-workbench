# Phase 5 — Testing & hardening

**Status:** ✅ Merged to `main` (PR #8). Backend hardening **verified by running** (12 new tests + the 94-test unit suite + `manage.py check`, green in a venv). Companion artifact: the [Phase 3+4 security review](./security-review-phase-3-4.md).

**Phase:** 5 — Testing & hardening ([specs/19 §19.2](../19-delivery-and-scope.md))

## 1. Scope — what Phase 5 actually was

The spec's Phase-5 checklist (backend unit + emulator integration with the **concurrency / reconciliation / security-rule** gates; frontend tests; **Playwright critical paths A & B**; structured logging; health checks; the authorization-boundary review; the deferred defense-in-depth) was **largely banked per-phase** — every prior phase shipped with its own adversarial QA and tests. So Phase 5 resolved to the *tail*:

1. an **adversarial security review of Phase 3 + 4** (the surface added since the [Phase 1+2 review](./security-review-phase-1-2.md)), and
2. the **hardening** that review — plus the defense-in-depth the Phase 1+2 review deferred to Phase 5 — called for.

What was already delivered before this phase opened:

| Phase-5 requirement | Where it landed |
|---|---|
| Backend unit + emulator integration (concurrency, fencing, reconciliation, security-rule gates) | Phases 2–3 — 32 backend test files + the 12-test Firestore-rules suite |
| Frontend tests | Phase 4 — 3 unit/component test files (20 tests) |
| Playwright critical paths A & B (+ STALE_WRITE / 403 / 202) | Phase 4 part 2 — 5 e2e specs, CI-active |
| Structured logging + correlation-id; health/readiness | Phase 1 |
| Authorization-boundary review | the Phase 1+2 security review (PR #4) + the live 403 e2e |
| Frontend lockfile + `npm ci` | Phase 4 part 2 |

## 2. The security review

Method (the repo's established review process): a multi-agent workflow — **5 parallel dimension finders** (authz boundaries · Firestore rules & data exposure · frontend security · async financial integrity · config/deploy + the deferred-prereq audit) → **per-finding adversarial verification** (each finding handed to an independent skeptic told to *refute* it → CONFIRMED / PLAUSIBLE / REFUTED with a reproduction) → **synthesis + a completeness critic**. 20 agents.

**Result — no CRITICAL / HIGH / MEDIUM; all 6 deferred Phase-3 security prerequisites verified LANDED** (OIDC verify, command-level role re-check, endpoint rate-limiting, `contributions.due()` pagination, exception-input validation, refresh-token-revoke-on-demotion). The verification pass **refuted 6** plausible-but-wrong findings — including a claimed **HIGH** ("emulator sign-in ships to prod → admin bypass") and two MEDIUMs. 8 LOW (production-hardening) remained. The completeness critic flagged the frontend as under-examined; those gaps (XSS on free-text, the command-client token handling, rules deny-by-default) were **closed directly** and came back clean. Full detail — findings, dispositions, the prereq audit, the refuted list — in [`security-review-phase-3-4.md`](./security-review-phase-3-4.md).

## 3. Hardening shipped

| Area | Change |
|---|---|
| **Security response headers** (deferred DiD) | `django.middleware.security.SecurityMiddleware` + `SECURE_*` (nosniff, referrer-policy, prod-only HSTS + SSL-redirect with the health endpoints exempt) and a `SecurityHeadersMiddleware` adding `X-Frame-Options: DENY` + a strict `default-src 'none'; frame-ancestors 'none'` CSP for the JSON API |
| **Correlation-id sanitization** (deferred DiD) | honor the inbound `X-Correlation-Id` only if it matches `[A-Za-z0-9_.-]{1,128}` — no CR/LF/control chars → no log-forging or response-header injection — else mint a fresh id |
| **Review #2** — `/readiness` DoS amplification | TTL-cache the unauthenticated Firestore probe (a burst collapses to one round-trip; a cache, not a throttle, so Cloud Run's own probes are never rejected) |
| **Review #3** — fail-closed guardrail gap | the `ENVIRONMENT=production` guardrail now rejects `FIRESTORE_EMULATOR_HOST` / `FIREBASE_AUTH_EMULATOR_HOST` — the switch that flips `/internal` from Google-OIDC to the dev-secret bypass |
| **Review #4** — prod introspection surface | JSON-only DRF renderer in production (drop the Browsable API) |

## 4. Verification — by running

This phase marks a shift: the sandbox has a working toolchain (npm + network + JDK + pip), so the backend was **verified locally by running** — a throwaway venv, not just static review + CI. All green:

| Check | Result |
|-------|--------|
| `manage.py check` (validates the new middleware + `SECURE_*` + renderer + guardrail) | ✅ no issues |
| The 12 new hardening unit tests (correlation-id sanitization, the wired header stack, the readiness cache, the renderer config) | ✅ pass |
| The full `@tag('unit')` suite (regression) | ✅ 94 pass |
| Guardrail rejects an emulator host under `ENVIRONMENT=production` (subprocess) | ✅ `ImproperlyConfigured` fires with the exact message |
| CI — backend (unit + emulator integration), frontend, rules, OpenAPI, e2e | ✅ green (PR #8, since merged) |

## 5. Process notes

- **The security review used the multi-agent adversarial workflow** (5 finders → refute-first verification → synthesis + a completeness critic). The refute-first pass killed a claimed HIGH and two MEDIUMs — the discipline paid for itself.
- **Verify-by-running for the backend** was possible for the first time (network + pip available), so the hardening was confirmed by execution, not asserted.
- **The fix/defer split was made honestly.** One LOW (the lease-reaper `set-user-role` re-drive) was reclassified mid-implementation from "fix now" to "focused follow-up" on closer reading — it is a refactor of the security-sensitive role command, is emulator-only-testable, and its common case self-heals via the client-retry reclaim path; bundling it into a hardening PR was the wrong risk trade.

## 6. Key decisions

- **Phase 5 = review + hardening, not a from-scratch test phase** — the per-phase QA loop had already banked the test pyramid, logging, health, the authz review, and the e2e.
- **`/readiness`: a TTL-cache, not a throttle** — throttling would reject Cloud Run's own frequent liveness/readiness probes; a short cache collapses a flood without hurting real probes.
- **HTTPS-only hardening (HSTS, SSL-redirect) is production-gated** — armed only under `ENVIRONMENT=production`, so local http, the emulator, CI, and the http health probes are unaffected; the health endpoints are additionally SSL-redirect-exempt for Cloud Run.
- **Correlation-id: allowlist + length, mint-on-mismatch** — an inbound trace id is a convenience, never trusted content; a strict charset makes it safe to echo into logs and the response header.
- **The reaper fix is deferred, not dropped** — tracked with its fix design in the review report.

## 7. Deferred / follow-ups

- **Lease-reaper `set-user-role` re-drive** — extend Phase-1 recovery persistence + extract a reusable idempotent apply for the command and the reaper (a focused, separately-reviewed change).
- **Hash-pinned backend lockfile** (`pip-tools`/`uv` → `requirements.lock` + `--require-hashes`) — supply-chain parity with the frontend lockfile.
- **Shared-cache (Redis/Memorystore) throttle backend** — belongs to the Phase 6 deploy (multi-instance fleet-wide limits).
- **Accepted:** the ~1h role-demotion Firestore-read lag — a documented MVP tradeoff (specs/12 §12.3–12.4), write path already closed immediately.

## 8. What's next

[specs/19 §19.2](../19-delivery-and-scope.md): **Phase 6 — Deployment & docs**, authored as code — the `U12` IaC (Cloud Tasks queues + Cloud Scheduler crons + Cloud Run service + the readiness flip to `configured`), the Vercel/Firebase-Hosting config, monitoring/alerts, architecture diagrams, screenshots, and the 2-minute demo script — plus the deferred follow-ups above. The application stack itself (backend command + async layer, the full operator workbench) is built, merged, and hardened.
