# BenefitServicing Workbench

An operations platform for servicing **employer-sponsored student-loan repayment benefits**: benefit activation, employer-funded contribution schedules, simulated payment processing with real transactional/idempotency/recovery controls, employment-change cascades, exception handling, and an immutable audit timeline. Firestore is the primary system of record; a Django command backend owns every write; a Next.js workbench subscribes read-only in real time.

**Status: specification & design package — the application is not built yet.** `frontend/`, `backend/`, and `infrastructure/` are the planned layout ([specs/02](specs/02-architecture.md) §2.5); implementation follows the phases in [specs/19](specs/19-delivery-and-scope.md).

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

`backend/` (Django + DRF on Cloud Run), `frontend/` (Next.js), `infrastructure/` (queues, scheduler, deploy scripts incl. `scripts/e2e.sh`), `docs/demo-script.md`, `docker-compose.yml`.
