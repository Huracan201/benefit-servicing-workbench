# Demo script — BenefitServicing Workbench (~2 minutes)

A guided walk through the operator workbench that shows the load-bearing engineering, not just
the screens. Every entity named below is deterministic seed data (`seed_demo`), so the script
reproduces exactly.

## Before you start

**Local (zero cost):**

```bash
make demo          # emulator + seeded data + Django (inline) + Next.js; Ctrl-C to stop
```

Then open **http://localhost:3000**. (Live deployment instead? Use its URL — same script.)

**Demo credentials** (password `DemoPass!234` for all three — override with `SEED_DEMO_PASSWORD`):

| Sign in as | Role | Sees / can do |
|------------|------|----------------|
| `mgr@demo.test` | SERVICING MANAGER | everything an operator does **plus** manager-only actions |
| `ops@demo.test` | OPERATIONS USER | day-to-day servicing; manager actions are locked |
| `admin@demo.test` | ADMINISTRATOR | + role administration |

The seed is 20 borrowers across four employers (Memorial Health, Northwind Traders, Globex
Manufacturing, Initech Software), each a different servicing scenario — healthy, failed-awaiting-retry, terminated,
completed, approaching-completion.

---

## The walk

### 1 — Dashboard (10s) · *sign in as `mgr@demo.test`*
Land on the control room: portfolio rollups (active benefits, funds disbursed, open exceptions),
recent activity, the read models updating live. **Point out:** these aggregates are *projections*
recomputed off the write path — never read to make a financial decision (specs/05).

### 2 — Loan portfolio → a healthy account (20s)
Open **Loans** → **Jordan Lee** (Memorial Health) — an active benefit, 12 contributions posted.
The detail screen subscribes to the **source documents** (loan, benefit, schedule), not a
projection — post-command truth comes from the authoritative docs.

### 3 — Process a contribution — the money path (35s) · **Path A**
On the next `SCHEDULED` contribution, click **Process**. Watch the write path:
- the command returns **202** and the button shows an in-progress affordance,
- the client **polls** and the **source** contribution flips to `POSTED`, an `attempt` appears,
  and the loan + benefit balances update **atomically**. *(This is Playwright critical Path A +
  Flow 202.)*

**Point out:** the Idempotency-Key **and** the `If-Match` revision are frozen when the button is
armed — a double-click cannot double-post, and a stale screen cannot overwrite fresh state.

### 4 — Exceptions — assign, review, resolve (30s) · **Path B**
Open **Exceptions**. **Maria Santos** (Memorial) has an open `PAYMENT_FAILED` (servicer timeout).
**Assign** it to yourself, review the failing attempt, and **retry** → the exception leaves the
OPEN queue and the contribution recovers. **Point out:** repeated failures reuse **one**
deterministic exception row with a `failureCount` (not a pile of duplicates), and a successful
retry resolves *that exact* exception. *(Playwright critical Path B.)*

### 5 — Authorization is server-side, not just UI (15s) · *sign in as `ops@demo.test`*
As an operator, a manager-only action (e.g. suspend a benefit) is **locked** in the UI. The
important half: even if you bypass the disabled button, **the server rejects it 403** — the
workbench role gate is an affordance; Django authorizes every write. *(Playwright Flow 403.)*

### 6 — The audit trail (10s)
Open any entity's **timeline**: an immutable, correctly-ordered stream of `servicingEvents` —
every material action, who did it, and the correlation id that ties one command's writes together.

---

## What this demonstrates (the thesis)

Firestore used **responsibly** as the primary store for a financial workflow — with explicit
transactional, idempotency, recovery, audit, and async controls, not a pretense they're
unnecessary (specs/01 §1.6):

- **CQRS split** — every write is a Django *command* (transactions, state machines, invariants);
  the frontend only reads, via security-rule-authorized subscriptions.
- **Two-phase payment, crash-safe** — the idempotency record is created *inside* the state
  transition; a crash between charge and finalize is recovered by the reconciliation sweeper
  re-driving the processor by the attempt's deterministic key — no double charge, no lost post.
- **Integer cents, solved schedules** — `Σ(installments) == commitment` exactly; no floats.
- **Eventually-consistent read models** — updated off the payment transaction, reconciled by a
  scheduled rebuild.

## Reset

Re-run `seed_demo` (idempotent — re-pins users, rebuilds the deterministic dataset):

```bash
python backend/manage.py seed_demo          # local (emulator) — or --project <id> for a live project
```

A deployed demo also resets nightly (the `reset-demo` Cloud Scheduler job, 05:00 ET).
