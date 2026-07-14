# BenefitServicing Workbench

An operations platform for servicing **employer-sponsored student-loan repayment benefits**: benefit activation, employer-funded contribution schedules, simulated payment processing with real transactional/idempotency/recovery controls, employment-change cascades, exception handling, and an immutable audit timeline. Firestore is the primary system of record; a Django command backend owns every write; a Next.js workbench subscribes read-only in real time.

**Status: Phases 1–5 merged; Phase 6 (deployment) is the single remaining phase.** Phases 1 (the framework-free `backend/common/` core, 60 unit tests + the scaffold), 2 (the full **domain command layer** — activation, the two-phase payment, suspend/resume/terminate, employment cascade, exceptions, notes, admin), and 3 (the **async layer** — OIDC-gated Cloud Tasks/Scheduler handlers behind a 202-cloud/200-inline completion protocol, a reconciliation sweeper + lease reaper, and recompute-from-source read-model projections) are built, QA'd, and merged to `main` (PRs #1–#3, #5, each CI-green + CodeRabbit-reviewed; a read-only security review + its hardening also merged, PR #4). **Phase 4 (the Workbench UI)** brings the operator app up over that backend: the *ledger + control room* design system + the **dashboard** + **loan portfolio** (**part 1**, PR #6 — merged), and the loan/benefit **detail screen** + the payment/exception **worklists** + a minimal emulator **auth surface** + the Playwright critical-path **e2e** (**part 2**, PR #7 — merged). **Phase 5** — an adversarial security review of the async + UI layers (no CRITICAL/HIGH/MEDIUM; all Phase-3 prerequisites verified) plus its hardening — is merged (PR #8). Next: **Phase 6 — deployment** (`U12` — Cloud Run + the Cloud Tasks queues + Cloud Scheduler crons + hosting + monitoring + the readiness flip + the demo), per [specs/19](specs/19-delivery-and-scope.md). Per-phase [engineering reports](specs/engineering-reports/) track what shipped.

## Start here

| | |
|---|---|
| 📚 **Spec index & conventions** | [`specs/README.md`](specs/README.md) — reading order + normative global conventions |
| 🔌 **API contract (authoritative)** | [`specs/openapi.yaml`](specs/openapi.yaml) |
| 🖼 **Interactive wireframes** | [`specs/wireframes.html`](specs/wireframes.html) |
| 🔐 **Firestore rules / indexes / emulator** | [`firebase/`](firebase/) — deployable + tested today |
| 🚀 **Deploy & ops runbook** | [`specs/21-deployment-and-operations.md`](specs/21-deployment-and-operations.md) |
| 🧾 **Review traceability** | [Appendix A](specs/appendix-a-review-findings.md) (v1→v2) · [Appendix B](specs/appendix-b-handoff-audit.md) (pre-handoff audit) |
| 🧱 **Engineering reports** | [specs/engineering-reports/](specs/engineering-reports/) — per-phase build + QA record |

## What runs today

```bash
# Firestore security-rule tests (Node 20 + Java required)
npm i -g firebase-tools
cd firebase && npm install && npm run test:rules:ci

# Emulator suite (Auth + Firestore + UI), fully offline
cd firebase && firebase emulators:start --project=demo-benefitservicing-workbench

# Lint the API contract
npx @stoplight/spectral-cli lint specs/openapi.yaml --fail-severity=error

# Frontend workbench (needs `npm install`; live data needs the emulator + the Django API)
cd frontend && npm install && npm run dev            # http://localhost:3000
cd frontend && npm run lint && npm run test && npm run build
```

The backend core tests run offline (`cd backend && python -m unittest discover -s common/tests -p 'test_*.py' -t .`); the Django command + async layer (`manage.py check`, `--tag=unit`, and the emulator integration suite — activation, the two-phase payment, the concurrency + fencing gates, the reconciliation sweeper + lease reaper, and the projection flows) plus the frontend (`npm run lint`/`test`/`build`) and the Playwright critical-path **e2e** (seed → Django → Next → Playwright, via `infrastructure/scripts/e2e.sh`) run on CI. `.github/workflows/ci.yml` gates the backend/frontend/e2e jobs on file presence — **backend, frontend, and e2e are all active** now that part 2 shipped a committed lockfile + the e2e harness.

## Not yet built

**Deploy-time IaC** (`infrastructure/` — the Cloud Tasks queues + Cloud Scheduler crons + the readiness flip to `configured`, `U12`), the `propagate-denormalized` fan-out task (`U13`, awaiting its producer command), and `docs/demo-script.md`. The whole application stack — the backend command + async layer and the full operator workbench — is built, merged, security-reviewed, and hardened (Phases 1–5 on `main`).
