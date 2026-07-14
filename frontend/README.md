# Frontend — BenefitServicing Workbench

The operator-facing **Workbench**: a Next.js (App Router) + TypeScript + Tailwind
app over the merged Django command backend. Phase 4 — part 1 (design system +
read screens, PR #6) and part 2 (the command surface + worklists + e2e, PR #7),
**both merged**. The whole stack (this app + the emulator + a seeded Django API)
comes up with one command — `make demo` from the repo root (Phase 6).

See [specs/02](../specs/02-architecture.md) (architecture / CQRS),
[specs/05](../specs/05-read-models-and-projections.md) (read models),
[specs/12](../specs/12-auth-and-security.md) (auth), and
[specs/15](../specs/15-ui-and-screens.md) + [`specs/wireframes.html`](../specs/wireframes.html) (the UI).

## The one load-bearing idea: CQRS

- **Reads** are direct Firestore client-SDK subscriptions to read models and
  source docs — authorized by `firebase/firestore.rules`. The frontend never
  reads through Django.
- **Writes** go *only* through the typed command client (`lib/commandClient.ts`)
  → the Django `/api/v1` command endpoints — authorized by Django from the same
  Firebase custom-claim role. There are **no** generic document writes.
- **Post-command truth comes from the SOURCE docs** (loans / borrowers /
  agreements / contributions / attempts / exceptions / events / notes), never
  the eventually-consistent projections (`loanWorkbenches`, `*Summaries`), which
  lag a completed command by seconds ([specs/05 §5.7](../specs/05-read-models-and-projections.md)).
- **Role affordance is UX only** — a locked/hidden button is never the boundary;
  the server authorizes every write and a real `403 FORBIDDEN` is handled with a
  typed toast.

## Layout

| Path | What |
|------|------|
| `app/` | Routes: `/` dashboard, `/loans` portfolio, `/loans/[loanId]` detail, `/payments` queue, `/exceptions` workbench, `/signin` (emulator auth). |
| `components/` | The *ledger + control room* kit (Table, Card, Pill, SeverityCell, StatTile, Tabs, FilterBar, Pagination, Timeline, FactsGrid, Toast, Skeleton, Button, ConfirmDialog, RoleGate, ThemeToggle) + charts + the affordance components (`CommandButton` / `ConfirmAction` / `CommandFormDialog`) + colocated screen components (`components/loans/detail/*`, `components/payments/*`, `components/exceptions/*`). |
| `hooks/` | `useDocument` / `useCollectionPage` (Firestore subscriptions) and **`useCommand`** — the reusable write-path engine. |
| `lib/` | `commandClient` + `commandActions` (the 14-action registry) + `permissions`; `readModels` / `readAccount` / `readContributions` / `readExceptions` (typed subscription hooks); `session` (emulator auth); `firebase`, `format`, `types`. |
| `e2e/` | Playwright critical-path specs (payment + exception lifecycles + STALE_WRITE / 403 / 202 flows). |

## The write path (`hooks/useCommand.ts`)

Every mutating affordance runs one flow: **role affordance → confirm → the
Idempotency-Key + the `If-Match` revision both frozen at `arm()` → send → in-flight
lock → 202-poll (same key) → resolve truth from the SOURCE subscription → typed
toast**. Freezing the key prevents a retry from replaying an already-accepted
mutation; freezing the revision makes a concurrent edit during the confirm window
correctly `409 STALE_WRITE` ([specs/08](../specs/08-idempotency-and-consistency.md)).

## Commands

```bash
npm install            # deps (also generates/uses the committed package-lock.json)
npm run dev            # dev server on http://localhost:3000
npm run lint           # next lint
npm run test           # Vitest + Testing Library (unit/component; e2e excluded)
npm run build          # next build (real tsc + next/font fetch)
npm run typecheck      # tsc --noEmit

# End-to-end (needs the emulator + a seeded Django API, and the Playwright browser —
# `npx playwright install chromium` once). CI runs it via infrastructure/scripts/e2e.sh
# inside `firebase emulators:exec`; locally, with the emulator + Django up:
npm run test:e2e
```

## Environment

The Firebase client is emulator-aware. For local dev against the emulator suite:

| Var | Value |
|-----|-------|
| `NEXT_PUBLIC_USE_FIREBASE_EMULATOR` | `true` |
| `NEXT_PUBLIC_FIREBASE_PROJECT_ID` | `demo-benefitservicing-workbench` |
| `NEXT_PUBLIC_FIREBASE_AUTH_EMULATOR_HOST` | `http://localhost:9099` |
| `NEXT_PUBLIC_FIRESTORE_EMULATOR_HOST` | `localhost:8080` |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` (the command client appends `/api/v1`) |

Sign in on `/signin` with a seeded demo account — `ops@demo.test`,
`mgr@demo.test`, or `admin@demo.test`
([backend/seed/users.py](../backend/seed/users.py)).

## Verification

CI (`.github/workflows/ci.yml`) is the source of truth: `npm run lint`/`test`/`build`
on the **Frontend** job (via `npm ci` against the committed lockfile) and the full
critical-path suite on the **E2E** job. `tsc`/`next build` also run locally when a
toolchain is present.
