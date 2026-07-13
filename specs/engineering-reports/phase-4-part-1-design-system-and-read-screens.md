# Engineering Report — Phase 4 Part 1: Design System + Read Screens

**Project:** BenefitServicing Workbench (`Huracan201/benefit-servicing-workbench`)
**Phase:** 4 — Workbench UI, part 1 (the design-system foundation + the two read-only showcase screens; part 2 = detail, worklists, polish/e2e — [specs/19 §19.2](../19-delivery-and-scope.md))
**Scope:** the design system (tokens + self-hosted fonts + the shared component kit) · the command client (write path) · the app shell · the **dashboard** · the **loan portfolio**
**Status:** ✅ Merged to `main` (`1d25e62`, PR #6) — CI-green (frontend `lint`/`test`/`build`) + CodeRabbit reviewed and addressed. Part 2 (the command surface + worklists + e2e) is [PR #7](./phase-4-part-2-command-surface-and-worklists.md).
**Date:** 2026-07-12

---

## 1. Summary

Part 1 turns the Phase-1 Next.js scaffold into a real, designed Workbench: the shared design system, the write-path command client, and the first two operator screens (the portfolio **dashboard** and the **loan portfolio**), which read the Phase-3 projections live via the Firestore client SDK. It deliberately stops before the interactive command surface — the loan/benefit **detail** screen and the payment/exception **worklists** are part 2 — because A+B are a coherent, shippable chunk and the PR is already substantial.

The visual direction is **"ledger + control room"** — a precise financial-servicing instrument where color is reserved almost entirely for status. It was **rendered as a live preview and approved by the product owner before any screen was built**, then implemented faithfully: a Verdigris accent used for chrome only (never "good"), a green-biased graphite neutral, a reserved semantic status palette split for colorblind separation, and IBM Plex Sans / Public Sans / IBM Plex Mono with every money figure and machine token in tabular mono.

Two properties thread the build: **CQRS is honored end-to-end** — screens READ read models via subscription hooks and never read a projection to make a financial decision (aggregates are labelled eventually-consistent); writes (part 2) go only through the command client. And because **`npm`/`tsc` are unavailable in the offline build sandbox**, the verification model shifted: a rigorous static consistency pass per slice, with **CI (`npm run lint|test|build`) as the compilation validator** — every slice was pushed and iterated to green.

**Headline outcomes**
- The design system: RGB-triplet CSS-variable tokens (light / OS-dark / explicit-toggle, both ways), self-hosted fonts via `next/font/google` (runtime CDN-free), and ~18 typed component primitives + zero-dependency inline-SVG charts.
- A typed command client with the 202 + Retry-After same-key poll contract and a typed error→copy map.
- A typed read-model data layer + two full-craft screens reading `portfolioSummaries` / `employerSummaries` / `loanWorkbenches` / `servicingEvents`.
- Per-slice adversarial QA found **2 HIGH + several MED/LOW**; all fixed and CI-verified.

---

## 2. Scope

**Slice A — design-system foundation.** Tokens + self-hosted fonts; the command client (write path, 202-poll, typed errors); the shared component kit (dense table, status pills, severity cells, stat tiles, tabs, filter bar, pagination, timeline, facts grid, card, toast, skeleton, confirm dialog, button, role-gate, theme toggle, + inline-SVG charts); the app shell (nav + top bar).

**Slice B — read screens.** A typed read-model access layer (`lib/readModels.ts`) over the existing subscription hooks; the **dashboard** (8 KPI tiles, scheduled-vs-posted + status-mix + exceptions-by-type charts, employer utilization meters, live activity timeline); the **loan portfolio** (filterable, cursor-paginated real-time table with the non-combinable has-open-exception filter).

**Part 2 (next):** the loan/benefit detail screen (the command surface), the payment queue + exception workbench (role-gated actions), and polish + a11y + a committed lockfile + Playwright e2e. Row clicks in part 1 land on a minimal stub `/loans/[loanId]` route that part 2 fleshes out.

---

## 3. What was delivered

| Area | Module(s) | Highlights |
|------|-----------|-----------|
| Tokens + fonts | `app/globals.css`, `tailwind.config.ts`, `lib/fonts.ts`, `app/layout.tsx` | RGB-triplet CSS vars mapped to Tailwind with `<alpha-value>`; theme cascade light / `@media` OS-dark / `[data-theme]` both ways; Plex Sans/Public Sans/Plex Mono self-hosted (CDN-free) |
| Command client | `lib/commandClient.ts`, `commandTypes.ts`, `errors.ts` | `sendCommand` + 15 typed wrappers; Bearer ID token + `Idempotency-Key` reused byte-identical on a 202 poll; `If-Match` expectedRevision; `ErrorCode`→human copy; 202 body advisory |
| Component kit | `components/*` + `components/charts/*` + `statusMeta.ts` | DenseTable, StatusPill, SeverityCell, StatTile, Tabs, FilterBar, Pagination, Timeline, FactsGrid, Card, Toast, Skeleton, ConfirmDialog, Button, RoleGate, ThemeToggle; Sparkline/Bar/StackedBar/Meter/Legend as zero-dependency inline SVG |
| Shell | `components/AppShell.tsx`, `Nav.tsx`, `TopBar.tsx` | 216px sidebar + top bar (search, View-as role affordance, theme toggle, count badges) |
| Data layer | `lib/readModels.ts` | typed hooks over the existing subscription hooks; index-backed filter constraints; period docId computed in `America/New_York` |
| Dashboard | `app/page.tsx`, `components/dashboard/*` | 8 KPI tiles + charts + meters + activity timeline; skeleton/empty/error; eventual-consistency note |
| Portfolio | `app/loans/page.tsx`, `components/loans/*` | filterable cursor-paginated real-time table; standalone has-open-exception filter; status pills + severity dots |

---

## 4. Architecture highlights

**Token-level theming.** The palette lives as space-separated RGB triplets in CSS variables under `:root` (light), `@media (prefers-color-scheme: dark) :root:not([data-theme="light"])` (OS-follow), and `:root[data-theme="dark"|"light"]` (the viewer toggle wins both ways). Tailwind maps color names to `rgb(var(--x) / <alpha-value>)` so opacity utilities keep working, and `darkMode` targets the `[data-theme="dark"]` selector — so both the CSS-var palette and any `dark:` utilities follow one switch. A pre-hydration inline script applies a persisted choice before paint; OS-followers get no attribute and live-track the OS.

**Reserved-status color, validated for CVD.** The accent is chrome/interaction only — it is never a "good" or status signal. The six semantic colors map to the state machines (Posted/Active = good, Scheduled = info, Processing/Open/Medium = warning, Retry/High = serious, Failed/Critical = critical, Canceled/Pending/Terminated/Low = neutral, Suspended = warning per the wireframe). Because that reserved set is **not** a categorical ramp, the status-mix stacked bar ships mandatory direct segment labels + 2px gaps and never encodes status by color alone.

**CQRS on the client.** Screens subscribe to the read models via `lib/readModels.ts` (which re-exports the interfaces from the single `lib/types.ts` definition to avoid schema drift, and formats money from integer cents). No screen reads a projection to make a financial decision, and the dashboard labels aggregates eventually-consistent so a just-completed action is never implied to have moved a portfolio total. Every read-model query maps to an existing composite index.

**The command client (used by part 2).** A typed `sendCommand` attaches the Firebase ID token + a caller-stable `Idempotency-Key` (reused byte-identical across a 202 + Retry-After poll, never regenerated) and an optional `If-Match` for optimistic concurrency; the error envelope maps to a typed `ErrorCode` with human copy. The 202 body is documented as advisory — the real outcome is resolved from the mutated entity's Firestore subscription.

---

## 5. Process — understand → design → preview → approve → build slices

1. **Understand + design.** A workflow mapped the wireframes, the read models, the command API, and the scaffold, and synthesized the design direction + a dependency-ordered plan.
2. **Design preview + approval.** The "ledger + control room" system was rendered as a live, theme-aware preview (palette, type, components, data-viz) and **approved by the product owner before any screen was built** — with "showcase-focused craft" chosen (full craft on the portfolio-carrying screens).
3. **Per-slice build + adversarial QA.** Each slice ran as a collision-safe workflow with a static-review integration lead (no offline build), then a 4-dimension adversarial QA with per-finding verification, a consolidated fixer, and a lead re-check — pushed to CI for the real compile.

---

## 6. Verification & tests

| Check | Result |
|-------|--------|
| CI frontend `npm run lint`, `npm run test`, and `npm run build` (real `tsc` + lint + `next build` + `next/font` fetch) — every push | ✅ green |
| CI backend + Firestore rules + OpenAPI (unchanged) | ✅ green |
| Per-slice adversarial QA (4 dimensions + per-finding verification) | ✅ findings fixed |
| Offline static consistency review (imports/types/token-wiring/field-drift) | ✅ per slice |
| Playwright e2e | deferred to part 2 (needs a committed lockfile for `npm ci`) |

**Tests added:** command-client unit tests (202 same-key poll, Retry-After, error mapping, unauthenticated pre-flight) and component-kit tests. The offline sandbox cannot run `npm`/`tsc`/`next`, so CI is the compilation authority — the reason each slice was pushed and iterated to green.

---

## 7. Issues found & fixed (per-slice QA, before ship)

| Slice | Sev | Issue | Resolution |
|-------|-----|-------|-----------|
| A | 🟠 HIGH | `ConfirmDialog` focus escaped the modal to the background trigger while a command was in-flight (keydown effect re-ran on `loading`) | Split into a stable focus capture/restore (`[open]`) + a ref-based keydown listener; panel is the focus anchor so the trap holds when both buttons disable |
| A | 🟠 HIGH | `ThemeToggle` stamped `data-theme` on mount → froze OS-followers to the load-time theme | Stamp only on an explicit stored choice; a `matchMedia` listener keeps OS-followers live |
| A | 🟡 MED | `--ink-3` failed WCAG AA as text; clickable `<tr>` used `role="button"` (collapsed cell semantics); `PENDING` mis-coloured; `FilterBar` ids collided | Darkened/lightened `ink-3` to ~5:1; removed the row role (in-cell links for keyboard); `PENDING`→neutral, `SUSPENDED`→warning; `useId` |
| B | 🟡 MED | The "Has open exception" toggle only **sorted** — it returned the whole portfolio | Added `where("openExceptionCount", ">", 0)` (served by the existing index) |
| B | 🟡 MED | Collection cards flashed a false empty state on first load; the flow Bar chart didn't fill its card (misaligned labels) | Gate the skeleton on `loading && empty`; `preserveAspectRatio` + restored the $-axis + the 1.3fr/1fr charts row |
| B | 🟡 MED | Portfolio rows navigated to a not-yet-existent `/loans/[loanId]` (404) | Added a minimal stub detail route (part 2 fleshes it out) |
| B | 🟢 LOW | Pagination "Next" overshot into an empty page on exact page-size multiples | Query one lookahead row (`limit(size + 1)`) in `useCollectionPage`, trim it off the page, and enable "Next" only when that row exists (`hasMore` is exact) |

---

## 8. Key decisions

- **Ship A+B as Phase 4 part 1.** The design system + the two read-only showcase screens are a coherent, reviewable chunk; the interactive command surface (detail + worklists) is a clean seam for part 2, and the PR was already large.
- **`next/font/google`, not committed font files.** Fonts are fetched at build (CI has network) and self-hosted in the output → runtime is CDN-free/CSP-safe, without committing binaries the offline sandbox can't produce.
- **CI is the frontend validator.** No offline `tsc`/build means a static-review-then-push-to-CI loop; the integration lead's static pass is what keeps CI green on the first try.
- **Reserved-status palette + accent-never-good**, and CVD-safe charts (direct labels, never color-alone) — validated, not eyeballed.

---

## 9. Known limitations (by design)

- **Part 2 is not built:** the loan/benefit detail screen (the command surface), the payment queue + exception workbench, and the polish/a11y/e2e pass. Row clicks land on a stub `/loans/[loanId]`.
- **No committed lockfile yet**, so the Playwright e2e job stays skipped until part 2 commits `package-lock.json` and switches it to `npm ci`.
- The legacy `StatusBadge` on the remaining stub pages (payments/exceptions) is untouched — those pages are replaced in part 2.

---

## 10. What's next

Ship part 1 (PR #6): mark ready → CodeRabbit → merge. Then **Phase 4 part 2**: the loan/benefit detail screen wired to the command client (process/retry/suspend/resume/terminate/employment/notes with confirm dialogs + optimistic-concurrency `If-Match`), the payment queue + exception workbench (role-gated actions), and the polish/a11y/lockfile/Playwright pass.

---

*Related: [phase-3-async-workflows.md](./phase-3-async-workflows.md) · [specs/15](../15-ui-and-screens.md) (UI & screens) · [specs/05](../05-read-models-and-projections.md) (read models) · [specs/wireframes.html](../wireframes.html) · [specs/19 §19.2](../19-delivery-and-scope.md).*
