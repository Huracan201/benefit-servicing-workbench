# 15 — UI & Screens

> **Wireframes: [`wireframes.html`](./wireframes.html)** — an interactive, navigable mockup of all five screens (Dashboard, Loan portfolio, Loan & benefit detail, Payment queue, Exception workbench) built with the seed data ([18](./18-seed-and-demo.md)) and the exact statuses/fields from this spec and [06](./06-state-machines.md). Open the file in a browser, or view the hosted version: https://claude.ai/code/artifact/7d6fb94a-f912-4caa-836c-3d251806a975 . The role switcher (top bar) demonstrates the [12](./12-auth-and-security.md) permission matrix by live-gating action buttons; the theme toggle shows both color schemes. These are **mid-fidelity wireframes for layout, hierarchy, and state** — not final visual design; spacing, iconography, and motion are illustrative.

## 15.1 Design direction

The workbench should feel like a premium financial-operations platform: clean, restrained, desktop-first, information-dense without clutter, strong hierarchy, high contrast, accessible. Minimal decoration. Color communicates **status**, never decoration — and **never alone**: every status uses a label *and* color (and an icon/shape where it aids scanning) so it survives color-blindness and grayscale. Clear tables and status badges are the primary idiom.

Primary navigation: **Dashboard · Loans · Payments · Exceptions**.

## 15.2 Data access patterns (normative)

- Screens render from **read models** via Firestore real-time subscriptions ([05](./05-read-models-and-projections.md)); mutations go through Django command endpoints ([11](./11-api.md)).
- Every list subscription is **scoped by an indexed predicate + `limit` + cursor pagination** ([05 §5.6](./05-read-models-and-projections.md), [13 §13.3](./13-firestore-indexes.md)). No unbounded collection subscriptions.
- Shared hooks (`useCollectionPage`, `useDocument`) enforce pagination and index-backed queries so screens can't accidentally over-fetch.
- Every screen implements **loading, empty, and error** states. Command errors surface the typed `error.code` ([11 §11.3](./11-api.md)) as an actionable message (e.g. `STALE_WRITE` → "This record changed; refresh and retry").
- **Role-based action visibility:** actions the user's role can't perform are hidden/disabled per the matrix ([12 §12.2](./12-auth-and-security.md)). This is a UX affordance only — the server is the real authority, so a hidden button is never the security boundary.

## 15.3 Screens

### Dashboard (portfolio health)
Subscribes to `portfolioSummaries/current` + current-period doc (2 docs). Shows: active loans, active benefit agreements, scheduled vs posted this month, failed contributions, open exceptions, remaining employer commitment, recent servicing activity (from `servicingEvents` by `eventType/createdAt`, limited).
Charts: contribution status mix; monthly scheduled vs posted; exceptions by type; employer commitment utilization. (Follow the dataviz guidance: status colors consistent with the badge palette, labels not color-only.)
Actions: open loan portfolio / payment queue / exception queue; search borrower or loan reference (exact/prefix — [13 §13.4](./13-firestore-indexes.md)).

### Loan portfolio
Table over `loanWorkbenches` (or `loans`) — columns: borrower, employer, servicer, current balance, benefit status, monthly contribution, next contribution date, open exceptions, loan status.
Filters (all index-backed — [13](./13-firestore-indexes.md), combination discipline §13.2a): employer, employment status, benefit status, loan status, has-open-exception (**standalone toggle — not combinable with the employer filter**; no composite serves that pair). Filter dropdowns list the *full* status enums from [06](./06-state-machines.md). Search: exact loan reference / borrower id / employer.

### Loan & benefit detail
Sections: borrower summary; employer & employment status; loan summary; benefit agreement; contribution schedule (ordered by `installmentNumber`); payment attempts (per contribution); operational exceptions; servicing timeline (loan events subcollection, ordered by `createdAt, sequence`); internal notes.
Actions (role-gated): activate / suspend / resume / terminate benefit; change employment status (with confirmation dialog); retry failed payment; process payment; add note.

### Payment operations queue
Tabs = contribution statuses: Scheduled · Processing · Failed · Retry pending · Posted · Canceled. Each tab is a paginated subscription on `status (+ employerId) + scheduledDate`.
Columns: borrower, employer, scheduled date, amount, status, attempt count, failure reason, last updated.
Actions: process payment, retry payment, open loan detail. Confirmation dialog before processing.

### Exception workbench
Table over `operationalExceptions` — columns: severity, type, borrower, employer, summary, assigned user, created, status. Default sort open · most-severe · newest.
Actions: assign to me / unassign (status-neutral — [06 §6.4](./06-state-machines.md)), mark in review, resolve, dismiss, open related account; create manual exception (`POST /exceptions`).

## 15.4 Real-time behavior & consistency messaging
Per-account state updates immediately after a command response (the screen subscribes to the synchronously-updated source fields). Portfolio/employer **aggregates may lag a few seconds** while projections converge ([05 §5.7](./05-read-models-and-projections.md)); the UI should not present a just-completed action as having instantly moved a portfolio-wide total. Prefer per-account confirmation ("Payment posted — balance updated") over implying global totals refreshed instantly.

## 15.5 Accessibility & polish
Keyboard-navigable tables and dialogs; focus management on modal open/close; ARIA on status badges (text alternative to color); adequate contrast in both light and dark; skeleton loaders over spinners for dense tables.
