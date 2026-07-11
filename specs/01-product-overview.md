# 01 — Product Overview

## 1.1 What this is

BenefitServicing Workbench is an operations platform for servicing employer-sponsored student-loan repayment benefits. It manages the lifecycle *after* a benefit is agreed: activating the benefit, generating the employer-funded contribution schedule, processing payments against the borrower's existing loan, handling failures and retries, reacting to employment changes, and resolving operational exceptions — all with an immutable audit trail and real-time operational visibility.

The actors and objects it coordinates:

- **Borrowers / employees** — the people whose loans receive employer contributions.
- **Employers / benefit sponsors** — the funding party, with a total commitment.
- **Existing student loans** — external loans being paid down (not originated here).
- **Benefit agreements** — the contract linking an employer's commitment to a borrower's loan.
- **Scheduled contributions** — the individual monthly employer payments.
- **Payment processing** — simulated money movement via a swappable adapter.
- **Servicing exceptions** — operational problems requiring human resolution.
- **Audit & operational history** — the immutable record of everything that happened.

## 1.2 Purpose

**Product purpose.** Give servicing operations and management a clear, dense, reliable workbench for running an employer loan-repayment benefit program.

**Technical purpose.** Demonstrate production-minded financial-workflow engineering on the target stack: a modern Next.js/React frontend, a realistic Firestore domain modeled around access patterns, a Django command layer that protects financial invariants, idempotent payment workflows, immutable servicing history, safe handling of third-party/payment failures, and asynchronous processing on GCP — a polished operational product, not a CRUD demo.

## 1.3 Primary goals

1. A functional servicing workbench with **Firestore as the primary database**.
2. Document-oriented modeling driven by known access patterns (screens and workflows).
3. Protect financial invariants through server-side commands, Firestore transactions, integer money, idempotency records, explicit state machines, and immutable events.
4. Support realistic servicing workflows: benefit activation, schedule generation, payment processing, failed-payment retry, employment termination, benefit suspension, exception resolution.
5. Clear architecture and tradeoff documentation.
6. A public demo environment with seeded data.

## 1.4 Non-goals (MVP)

The MVP explicitly does **not** include:

- Loan origination, credit underwriting, credit-bureau integration.
- Real money movement, ACH or card processing (payments are simulated via an adapter — see [09](./09-payment-processing.md)).
- Interest accrual or amortization calculations.
- Regulatory disclosure generation, multi-jurisdiction compliance.
- Production borrower PII, external loan-servicer integration.
- Full collections functionality.

Payment processing is simulated through an adapter that returns success or failure; the surrounding transactional, idempotency, audit, and recovery controls are real.

## 1.5 Target users & responsibilities

**Servicing Operations Specialist** (`OPERATIONS_USER`)
- Review borrower/loan records; monitor scheduled contributions.
- Investigate failed payments; retry eligible contributions.
- Update employment status *(see role note below)*; add servicing notes.
- Resolve operational exceptions.

**Servicing Manager** (`SERVICING_MANAGER`)
- Everything an Operations User can do, plus:
- Monitor portfolio health; review employer commitments.
- Activate, suspend, or terminate benefits.
- Trigger manual payment processing.
- Change employment status (activation/termination cascades).
- Review exception trends; oversee servicing activity.

**Administrator** (`ADMINISTRATOR`)
- All servicing operations, plus user/role management and system-level views.

> **Note on role boundaries.** v1 listed "update employment status" under both Operations User and Servicing Manager. Because an employment change triggers a benefit-state cascade with financial consequences (see [10](./10-benefit-and-employment-workflows.md)), v2 places the *employment-status-change command* at `SERVICING_MANAGER`+. Operations Users can view and flag employment issues (via an `EMPLOYMENT_VERIFICATION_REQUIRED` exception) but not execute the terminating command. The full permission matrix is in [12](./12-auth-and-security.md).

## 1.6 The engineering-review question this design answers

> Does this design demonstrate responsible use of Firestore for a financial servicing workflow **without pretending** Firestore removes the need for explicit transactional, idempotency, audit, and asynchronous-processing controls?

Every subsequent document is written to make the answer "yes," and to make the places where Firestore's constraints bite (single-document write limits, no read-after-write in transactions, eventual consistency of projections) explicit rather than glossed.
