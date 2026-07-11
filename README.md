# BenefitServicing Workbench

An operations platform for servicing **employer-sponsored student-loan repayment benefits**: benefit activation, employer-funded contribution schedules, simulated payment processing with real transactional/idempotency/recovery controls, employment-change cascades, exception handling, and an immutable audit timeline. Firestore is the primary system of record; a Django command backend owns every write; a Next.js workbench subscribes read-only in real time.

**Status: Phase 1 foundation scaffolded.** `backend/` (lean Django + DRF, Firestore-only) and `frontend/` (Next.js App Router) exist; the safety-critical, framework-free `backend/common/` core (money/state-machines/invariants) has **57 passing unit tests**. Business commands, async workers, and real screens are Phase 2+ per [specs/19](specs/19-delivery-and-scope.md).

## Start here

| | |
|---|---|
| 📚 **Spec index & conventions** | [`specs/README.md`](specs/README.md) — reading order + normative global conventions |
| 🔌 **API contract (authoritative)** | [`specs/openapi.yaml`](specs/openapi.yaml) |
| 🖼 **Interactive wireframes** | [`specs/wireframes.html`](specs/wireframes.html) |
| 🔐 **Firestore rules / indexes / emulator** | [`firebase/`](firebase/) — deployable + tested today |
| 🚀 **Deploy & ops runbook** | [`specs/21-deployment-and-operations.md`](specs/21-deployment-and-operations.md) |
| 🧾 **Review traceability** | [Appendix A](specs/appendix-a-review-findings.md) (v1→v2) · [Appendix B](specs/appendix-b-handoff-audit.md) (pre-handoff audit) |

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

CI (`.github/workflows/ci.yml`) runs the OpenAPI lint + rules tests now; backend/frontend/E2E jobs auto-activate as that code lands.

## Planned artifacts (not yet present)

`infrastructure/` (queue/scheduler IaC + `scripts/e2e.sh`), `docs/demo-script.md`, and the Phase 2+ backend command/task layer and Phase 4 UI screens. `docker-compose.yml` and the `backend/`+`frontend/` scaffolds now exist.
