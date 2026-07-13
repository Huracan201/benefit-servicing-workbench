# BenefitServicing Workbench

An operations platform for servicing **employer-sponsored student-loan repayment benefits**: benefit activation, employer-funded contribution schedules, simulated payment processing with real transactional/idempotency/recovery controls, employment-change cascades, exception handling, and an immutable audit timeline. Firestore is the primary system of record; a Django command backend owns every write; a Next.js workbench subscribes read-only in real time.

**Status: Phases 1–3 merged; Phase 4 (Workbench UI) part 1 in review.** Phases 1 (the framework-free `backend/common/` core, 60 unit tests + the scaffold), 2 (the full **domain command layer** — activation, the two-phase payment, suspend/resume/terminate, employment cascade, exceptions, notes, admin), and 3 (the **async layer** — OIDC-gated Cloud Tasks/Scheduler handlers behind a 202-cloud/200-inline completion protocol, a reconciliation sweeper + lease reaper, and recompute-from-source read-model projections) are built, QA'd, and merged to `main` (PRs #1–#3, #5, each CI-green + CodeRabbit-reviewed; a read-only security review + its hardening also merged, PR #4). **Phase 4 part 1** — the *ledger + control room* design system + the dashboard + loan portfolio — is on `release/phase-4` (PR #6); part 2 (the loan detail screen, the worklists, polish/e2e) is next, per [specs/19](specs/19-delivery-and-scope.md). Per-phase [engineering reports](specs/engineering-reports/) track what shipped.

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
```

The backend core tests run offline (`cd backend && python -m unittest discover -s common/tests -p 'test_*.py' -t .`); the Django command + async layer (`manage.py check`, `--tag=unit`, and the emulator integration suite — activation, the two-phase payment, the concurrency + fencing gates, the reconciliation sweeper + lease reaper, and the projection flows) plus the frontend (`npm run lint|test|build`) run on CI. `.github/workflows/ci.yml` gates the backend/frontend/e2e jobs on file presence — backend + frontend are active.

## Not yet built

**Phase 4 part 2** — the loan/benefit detail screen (wired to the command client), the payment queue + exception workbench, and a polish/a11y/e2e pass (with a committed lockfile so the Playwright job runs). Then deploy-time IaC (`infrastructure/` — the Cloud Tasks queues + Cloud Scheduler crons + the readiness flip), the `propagate-denormalized` task (awaiting its producer command), and `docs/demo-script.md`.
