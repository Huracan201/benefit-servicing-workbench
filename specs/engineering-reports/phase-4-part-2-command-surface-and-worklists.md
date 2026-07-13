# Phase 4 part 2 — the command surface, worklists & e2e

**Status:** 🟢 On `release/phase-4-part-2` — **PR #7**, ready. Built + adversarially QA'd + **verified locally by running** (typecheck / lint / test / build green). **CI is fully green — all 6 jobs, including the newly-activated `e2e` job at 5/5** (the full stack seed → Django → Next → Playwright works end-to-end; it took one test-selector fix after a 4/5 first run). CodeRabbit's two rounds (5 comments) are all addressed. Supersedes the "part 2 pending" row from [part 1](./phase-4-part-1-design-system-and-read-screens.md).

## 1. Scope

Part 1 delivered the design system + the two read screens. **Part 2 is the operator write path** over the merged CQRS backend, plus the Playwright critical-path e2e:

- The reusable **interactive write-path engine** (`useCommand` + the action registry + the affordance components).
- The **loan / benefit detail screen** — the full-craft account workspace + its command cluster.
- The **payment queue** and the **exception workbench** (lighter craft).
- A **minimal emulator sign-in + session-expired surface** (so the e2e write paths have a real session; not production auth).
- **Playwright e2e** — 2 critical paths + 3 write-path guarantee flows — CI-activated behind a committed lockfile.

CQRS is honored throughout: every write goes through the typed command client; **post-command truth is read from the transactionally-updated SOURCE docs, never a projection** (specs/05 §5.7). Role affordance is UX only — the server authorizes every write.

## 2. Architecture — one reusable write-path

Every mutating affordance runs the identical flow through `hooks/useCommand.ts`:

> **role affordance → confirm → `If-Match` + Idempotency-Key both FROZEN at `arm()` → send → in-flight lock → 202-poll (same key) → resolve truth from the SOURCE-doc subscription → typed toast.**

Two invariants are load-bearing (specs/08):
- **The Idempotency-Key is frozen at `arm()`** and reused across the client's internal 202 poll *and* across a post-error retry — never regenerated mid-intent (a fresh key could replay a mutation the server already accepted).
- **The `If-Match` revision is frozen at `arm()` too** (snapshotted from the live subscription value the operator was looking at). If a concurrent write advances the revision during the confirm window, submit still sends the armed value, so the server's precondition correctly fails with `STALE_WRITE` instead of silently succeeding against unseen data.

Supporting layers:
- `lib/commandActions.ts` — the 14-action registry (role / confirm / tone / `usesIfMatch` / `mayReturn202` / verb) derived from `openapi.yaml` + specs/06 + specs/12; `lib/permissions.ts` the pure UX role gate.
- `lib/readAccount.ts` + `lib/collectionPaths.ts` — typed SOURCE-doc + subcollection subscription hooks (loans / borrowers / agreements / contributions / attempts / exceptions / the `loans/{id}/events` mirror / notes); each disabled when its id is null.
- `components/{CommandButton,ConfirmAction,CommandFormDialog}` — self-driving affordances bound to a handle (locked-affordance with reason, focus-trapped validation dialogs mirroring the part-1 `ConfirmDialog`).

## 3. Process

Same loop as every prior phase — with **one upgrade**: this session's sandbox had a working `npm` + network + JDK, so unlike the backend phases (where CI was the only validator) the frontend was **verified locally by running** — `npm run typecheck && npm run lint && npm run test && npm run build`, and `npm ci` against the committed lockfile — before the first push.

1. **Understand → design** (read-only multi-agent workflow): parallel readers mapped the detail-screen regions, the command surface, and the worklists; the synthesis returned 8 build units + the write-path contract + the consequential decisions, which were taken to the user (auth surface, assignee display, e2e scope) **before** the build.
2. **Build in dependency-ordered slices** (multi-agent workflow, disjoint file ownership): Foundations (engine + read layer + auth, 3 parallel) → Affordances → Screens (detail + payments + exceptions, 3 parallel) → Polish + lockfile → e2e. 42 files, zero cross-unit collisions.
3. **Adversarial QA** (4 dimensions — CQRS, write-path, a11y/craft, compile-contract — each self-verifying its findings): 8 findings, all CONFIRMED/PLAUSIBLE, none refuted.
4. **Verify by running → consolidated fix → commit → draft PR → CI → CodeRabbit.** The local build was green first-pass; the 8 QA findings were fixed and re-verified green before commit.

## 4. Verification & tests

| Check | Result |
|-------|--------|
| Local `npm run typecheck` (strict `tsc --noEmit`) | ✅ clean |
| Local `npm run lint` (`next lint`) | ✅ no warnings or errors |
| Local `npm run test` (Vitest — 3 files, 20 tests; e2e excluded from collection) | ✅ pass |
| Local `npm run build` (`next build`) | ✅ compiled successfully |
| Local `npm ci` against the committed `package-lock.json` | ✅ in sync (exit 0) |
| `npx playwright test --list` (config + 5 specs compile & collect) | ✅ 5 tests / 5 files |
| CI (frontend `npm ci` + lint/test/build; backend; rules; OpenAPI) | ✅ green on PR #7 |
| CI `e2e` job (emulator + Django + Next + Playwright, 5 specs) | ✅ 5/5 on PR #7 (one `.first()` selector fix after a 4/5 first run) |
| CodeRabbit (2 rounds, 5 comments) | ✅ all addressed |

## 5. Issues found & fixed (adversarial QA, before commit)

| Sev | Finding | Fix |
|-----|---------|-----|
| 🟠 HIGH | vitest's glob collected the 5 Playwright specs → `npm run test` fails at collection | `exclude: ["…","e2e/**"]` in `vitest.config.ts` |
| 🟡 MED | `ContributionSchedule` mapped `RETRY_PENDING` → `/retry` (409 `INVALID_TRANSITION`; `/retry` is FAILED-only) | `SCHEDULED`\|`RETRY_PENDING` → `/process`, `FAILED` → `/retry` (matches the payments queue + openapi) |
| 🟡 MED | `If-Match` read live at submit → a concurrent edit during confirm silently skipped stale-write protection | freeze the revision at `arm()`, mirroring the Idempotency-Key |
| 🟡 MED | dialog restored focus to its trigger, which unmounts on success → focus fell to `<body>` | restore only if `isConnected`, else fall back to the main region |
| 🟡 MED | a failed command announced twice (assertive toast + in-dialog `role="alert"`) | drop `role="alert"`; the toast is the single announcer (kept `aria-describedby`) |
| 🟢 LOW | the `awaiting` (pending-202) phase had no exit → a stuck-busy affordance | reset the handle in an effect keyed on `[action, id]` |
| 🟢 LOW | `MetaChip` rendered the employer **name** in the mono/tabular face reserved for machine tokens | make mono opt-in; only the loan id uses it |
| 🟢 LOW | a non-persistent `role="status"` region made the timeline's loading announcement unreliable | persistent live region, toggle text only (matches the Toast pattern) |

## 6. Key decisions

- **Read post-command truth from SOURCE docs, not the projection** (user-approved). The `loanWorkbenches` / summaries projections lag a completed command by seconds; building the detail + worklists on them would show stale state after every action.
- **Minimal emulator sign-in + session-expired banner** (user-approved) — the e2e write paths need a real signed-in session; the affordance-vs-authorization story is rounded out cheaply. Emulator-only.
- **Frontend-only assignee display** (user-approved) — "Assigned to me" resolves the viewer's uid client-side; other assignees show a short-uid chip. No backend `assignedToName` (which `firestore.rules` would forbid resolving), keeping part 2 frontend-only.
- **The 202-async e2e forces the 202 by route-interception**, not Cloud Tasks — the emulator runs follow-up tasks inline (always 200), so the spec intercepts the first `/process` POST with a 202 + `Retry-After`, then lets the same-key re-POST fall through to the real inline backend → POSTED. Exercises the client's real poll path without cloud infra.
- **Correctness deviations from the literal build brief** (each flagged by its build agent and confirmed by QA): the retry/process transition preconditions (specs/09), the index-backed exception sort direction (a mixed DESC/ASC order is not served by the committed index), the employment-status `If-Match` sourcing the **borrower** revision (that endpoint targets `/borrowers/{id}`), and keeping the command dialog mounted through `error` so a retry reuses the frozen key.

## 7. Known limitations / follow-ups

- **`next@14.2.32` carries a security advisory** (surfaced by `npm ci`) — a part-1 dependency; a version bump is a separate, testable follow-up, out of scope for this feature PR.
- **The `e2e` job passes 5/5 on CI** (the full seed → Django → Next → Playwright stack). It runs against `next dev` (on-demand compilation, generous timeouts) rather than a production build — fine for the critical-path specs; a `next build && start` harness would be marginally closer to prod.
- **Three small date-formatting helpers** (`time.tsx`, and the `toDate` in `readContributions.ts` / the exception columns) each correctly coerce a Firestore `Timestamp`; a shared helper could dedupe them.
- **`/signin` renders inside the app shell** (the root layout wraps every route); acceptable for the demo, a chrome-less variant would need a route group.
- **Per-loan exception ordering is client-sorted** (bounded to 100) — no `(loanId, severityRank)` composite index exists; fine at demo scale.

## 8. What's next

[specs/19 §19.2](../19-delivery-and-scope.md) after part 2 merges: **deploy** (`U12`) — provision the Cloud Tasks queues + Cloud Scheduler crons and flip readiness to `configured`. Optional polish: the shared date helper, the Next bump, and a chrome-less sign-in.
