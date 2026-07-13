# Security review — Phase 3 (async layer) + Phase 4 (workbench UI)

**Status:** ✅ Review complete — **no CRITICAL / HIGH / MEDIUM; all 6 deferred Phase-3 security prerequisites LANDED.** 8 LOW / 7 distinct (production-hardening); the adversarial pass refuted 6 (incl. a claimed HIGH + two MEDIUMs). Fixes fold into **Phase 5** (`release/phase-5`). Companion to the [Phase 1+2 review](./security-review-phase-1-2.md).

## 1. Scope & method

Adversarial review of the surface added since the Phase 1+2 review: **Phase 3** — the OIDC-gated `/internal` Cloud Tasks/Scheduler handlers, the `enqueue()` inline↔cloud seam, dead-letter routing, the reconciliation sweeper + lease reaper, idempotency/lease/fencing, and the projections; **Phase 4** — the typed command client, the emulator auth surface, the *expanded* Firestore read surface (the detail screen + worklists now subscribe to source collections), role-gating, XSS, secrets, and CORS.

**Method** (the repo's established process): a multi-agent workflow — **5 parallel dimension finders** (authz boundaries · Firestore rules & data exposure · frontend security · async financial integrity · config/deploy + the deferred-prereq audit) → **per-finding adversarial verification** (each finding handed to an independent skeptic instructed to *refute* it, returning CONFIRMED / PLAUSIBLE / REFUTED with a concrete reproduction) → **synthesis + a completeness critic**. 20 agents; every finding traced to real code. The completeness critic flagged the frontend as under-examined, so its top gaps (XSS on free-text, command-client token handling, rules deny-by-default) were **closed directly** (§6) — all clean.

## 2. Result

| Severity | Count |
|---|---|
| CRITICAL | 0 |
| HIGH | 0 |
| MEDIUM | 0 |
| LOW | 8 (7 distinct after dedupe) |
| Refuted by verification | 6 |

The async layer and the command surface are sound: replay/double-charge is closed by deterministic ids + in-transaction status preconditions + the fencing re-drive; `/internal` is Google-OIDC-verified; the client cannot write any collection; reads require a servicing-role claim. The confirmed findings — **8 raw, 7 distinct** (the role-demotion read-lag was surfaced by two dimensions and is deduped to row #5 below) — are all **production-hardening / defense-in-depth**; none is exploitable for financial loss, data breach, or an authz bypass in the current design.

## 3. The deferred Phase-3 prerequisites — **all LANDED**

The Phase 1+2 review deferred six prerequisites to "land with the code that makes them reachable." Each was verified present:

| Prerequisite | Status | Evidence |
|---|---|---|
| OIDC verification on `/internal` | ✅ LANDED | `firebase_auth/middleware.py` — `verify_oauth2_token` asserts `aud == TASKS_AUDIENCE`, `email == TASKS_INVOKER_SA`, `email_verified`; fails closed |
| Command-level role re-check | ✅ LANDED | `commands/authz.py::require_system_or_role`, invoked at `internal/views.py` (un-forgeable SYSTEM marker or `min_role`) |
| Endpoint rate-limiting | ✅ LANDED | `settings.py` — `ScopedRateThrottle` + `throttle_scope` on all six write-view families (60/min; admin-write 30/min) |
| `contributions.due()` pagination | ✅ LANDED | `repositories/contributions.py::due()` — `limit` + `start_after` cursor, stable `(scheduledDate, __name__)` total order |
| Manual-exception `entityId`/`entityType` validation | ✅ LANDED | `exceptions/views.py::_normalize_entity_type` — allowlist to `{LOAN, BORROWER, EMPLOYER}` |
| Revoke refresh tokens on demotion | ✅ LANDED | `administration/services.py` — `revoke_refresh_tokens(uid)` when `role_rank(new) < role_rank(prev)` |

## 4. Findings — 7 distinct (8 raw LOW; #5 was surfaced by two dimensions)

| # | Finding | File | Disposition |
|---|---------|------|-------------|
| 1 | Lease reaper misclassifies `set-user-role` as a no-side-effect "tiny sync op": an orphaned key (crash between the Firebase claim change and the finalize, with no client retry) is marked FAILED, leaving the `users/{uid}` mirror stale + no audit event | `idempotency/reaper.py:80` | **Follow-up** (see below) |
| 2 | `/readiness` does an unauthenticated, unthrottled Firestore round-trip per call — 1 request amplifies to 1 Firestore query (cost/DoS) | `core/views.py:53` | **Fixed** — TTL-cache |
| 3 | The production fail-closed guardrail validates the dev-secret's *value* but not `FIRESTORE_EMULATOR_HOST` — the env var that actually flips `/internal` from OIDC to the shared-secret bypass | `config/settings.py` | **Fixed** — guardrail now rejects the emulator hosts |
| 4 | DRF Browsable API (an introspection surface) left enabled in production | `config/settings.py` | **Fixed** — JSON-only renderer in prod |
| 5 | Role-demotion is immediate for the write path (token revoked) but **eventually-consistent (~1h)** for Firestore reads — the stale ID token's claim still satisfies the rules until it expires (×2 dimensions) | `firestore.rules:26`, `administration/services.py:277` | **Documented** — accepted MVP tradeoff (specs/12 §12.3–12.4) |
| 6 | Write-endpoint throttles are backed by per-process `LocMemCache` — not enforced fleet-wide on multi-instance Cloud Run | `config/settings.py` | **Defer to deploy** (needs Redis/Memorystore; the settings comment already notes it) |
| 7 | Backend dependencies are floating version ranges with no committed lock/hashes (supply-chain / reproducibility) | `backend/requirements.txt` | **Follow-up** (hash-pinned lock via pip-tools/uv) |

### On #1 (the reaper) — why a follow-up, not a Phase-5 fix
`set_user_role` is a three-phase command (begin → the non-transactional Firebase claim change + revoke → finalize), structurally like the two-phase payment — so the tiny-sync invariant ("a persisted PENDING means the transaction rolled back → no side effect") does not hold for it. The **common recovery case already self-heals**: a same-key client retry hits the Phase-1 reclaim path (which re-reads the persisted `previousRole`, re-asserts the claim idempotently, and finalizes). The residual gap is the *reaper* path (crash **and** no retry): the reaper cannot re-drive because Phase 1 persists only `previousRole`, not the target role/claims. The correct fix is a refactor of the security-sensitive role command — extend Phase-1 persistence + extract a reusable idempotent "apply(Phase 2 + Phase 3)" for both the command and the reaper — and it is **emulator-only testable**. Bundling that into a hardening PR (where a mistake in the role path is high-impact) is the wrong risk trade, so it is tracked as a focused follow-up with its own review.

## 5. What the verification refuted (6)

The adversarial pass earned its keep — it killed six plausible-but-wrong findings, including the only claimed HIGH:

- **[HIGH → refuted]** "The emulator sign-in page + hardcoded admin credential ship in non-emulator builds → full-admin authz bypass." Refuted: the sign-in path and `connectAuthEmulator` are gated on `NEXT_PUBLIC_USE_FIREBASE_EMULATOR`, and the demo password only exists in the emulator seed; a prod build has no path to it.
- **[MEDIUM → refuted]** "Domain command services have no independent role re-check (single-layer authz)." Refuted: the DRF permission classes + `throttle_scope`/role gates enforce the minimum role server-side on every `/api/v1` command, independent of ingress.
- **[MEDIUM → refuted]** "`USER_ROLE_CHANGED` events leak every staff email + the org role map to any OPERATIONS_USER via `servicingEvents`." Refuted on the actual event payload + read model.
- Plus three LOW dependency/config guesses (incl. a duplicate of #3 that one verifier confirmed and two refuted as self-limiting — see note).

> **Note on #3's split verdict.** Two verifiers refuted the emulator-host guardrail gap as *self-limiting* (setting `FIRESTORE_EMULATOR_HOST` in prod also breaks the Firestore client, so it is not a remote exploit); one confirmed it as a real gap in a guardrail whose whole job is to fail closed on unsafe config. It is genuinely both — not remotely exploitable, but a cheap, correct completion of the fail-closed contract — so it is **fixed** (it costs two lines and removes a foot-gun).

## 6. Coverage & gap-closure

The completeness critic flagged the frontend dimension as under-delivered. Those gaps were closed directly (grep + read):

- **XSS:** the only `dangerouslySetInnerHTML` is the static, developer-controlled theme-init script in `layout.tsx` — never user data. No `innerHTML`/`eval`/`javascript:`/`srcdoc`, no data-driven `href`, no `target="_blank"` tabnabbing. React escapes the note/exception free-text by default. **Clean.**
- **Command-client token:** the Firebase ID token is confined to the `Authorization: Bearer` header — never a URL, a log, or an error body — and auth is Bearer-token, not cookie, so the command API is CSRF-immune. **Clean.**
- **Rules deny-by-default:** every source collection is `allow write: if false` (no client writes) and `allow read: if isServicingUser()`, where `isServicingUser()` requires a servicing-role **custom claim** (not merely `request.auth != null`). A signed-in user without a role claim reads nothing. The broad "any servicing user sees the whole portfolio" model is intended + documented (spec 12.4, no per-row tenancy in MVP). **Clean.**

## 7. Remediation — Phase 5

Shipped in `release/phase-5` (backend, verified by running: 12 hardening tests + the 94-test unit suite + `manage.py check`, all green):

- **Security response headers** (deferred defense-in-depth): `django.middleware.security.SecurityMiddleware` + `SECURE_*` (nosniff, referrer-policy, HTTPS-only HSTS/redirect armed in production only) + a `SecurityHeadersMiddleware` for `X-Frame-Options: DENY` and a strict `Content-Security-Policy` on the JSON API.
- **Correlation-id sanitization** (deferred defense-in-depth): the inbound `X-Correlation-Id` is honored only if it matches `[A-Za-z0-9_.-]{1,128}` (no CR/LF/control chars → no log or header injection), else a fresh id is minted.
- **#2** `/readiness` TTL-cache · **#3** guardrail rejects the emulator hosts · **#4** JSON-only renderer in production.

**Deferred (tracked):** #1 the reaper refactor (focused follow-up, self-heals via client retry); #6 shared-cache throttle backend (Phase 6 deploy — needs Redis); #7 a hash-pinned backend lockfile. **Accepted:** #5 the role-demotion read-lag (documented MVP tradeoff).

## 8. Conclusion

The engineering-review question ([01 §1.6](../01-product-overview.md)) holds through the async layer and the operator UI: Firestore is used responsibly, with explicit transactional, idempotency, recovery, audit, and async controls — and the authorization model (server-authorized writes, claim-gated reads, OIDC-verified async ingress) is intact. No exploitable authz bypass, financial-integrity break, or data exposure was found; the residue is production-hardening, most of which lands in Phase 5.
