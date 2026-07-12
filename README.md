# BenefitServicing Workbench

An operations platform for servicing **employer-sponsored student-loan repayment benefits**: benefit activation, employer-funded contribution schedules, simulated payment processing with real transactional/idempotency/recovery controls, employment-change cascades, exception handling, and an immutable audit timeline. Firestore is the primary system of record; a Django command backend owns every write; a Next.js workbench subscribes read-only in real time.

**Status: Phase 2 command layer (part 1) built.** Phase 1 foundation is merged to `main` (`backend/` Django + DRF, Firestore-only; `frontend/` Next.js; the framework-free `backend/common/` core with **60 passing unit tests**). Phase 2 — the **domain command layer**: benefit activation, the two-phase payment (with idempotency, immutable servicing events, and crash/fence recovery) — is built and QA-verified on `release/phase-2` (+15 integration/unit tests), pending CI + merge. Async workers (Phase 3) and real screens (Phase 4) are next, per [specs/19](specs/19-delivery-and-scope.md). Per-phase [engineering reports](specs/engineering-reports/) track what shipped.

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

The backend core tests run offline (`cd backend && python -m unittest discover -s common/tests -p 'test_*.py' -t .`); the Django command layer (`manage.py check`, `--tag=unit`) and the emulator integration tests (activation, the two-phase payment, the crown-jewel concurrency + fencing gates) run on CI. `.github/workflows/ci.yml` gates the backend/frontend/e2e jobs on file presence — backend + frontend are active.

## Planned artifacts (not yet present)

The remaining Phase-2 commands (suspend/terminate/employment cascade/exception workflow/notes), Phase 3 async (`infrastructure/` queue + scheduler IaC, `scripts/e2e.sh`, read-model projections), Phase 4 UI screens, and `docs/demo-script.md`. The spec set, `backend/` (foundation + activation/payment command layer), `frontend/` scaffold, and `docker-compose.yml` exist.
