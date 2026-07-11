# 03 — Domain Model

This document defines the entities, their relationships and cardinality, and the domain vocabulary. Field-level schemas live in [04](./04-firestore-data-model.md); this is the conceptual model.

## 3.1 Glossary

| Term | Meaning |
|------|---------|
| **Employer / sponsor** | The funding party. Has a total dollar commitment across its borrowers. |
| **Borrower** | An employee whose loan receives employer-funded contributions. |
| **Loan** | An existing (externally originated) student loan being paid down. Read-mostly here. |
| **Benefit agreement** | The contract linking one employer's commitment to one borrower's loan; defines monthly contribution, term, total commitment. |
| **Scheduled contribution (installment)** | One planned employer payment for one period of a benefit agreement. |
| **Payment attempt** | One try at moving money for a contribution. A contribution may have several. |
| **Servicing event** | An immutable audit record of a material state change. |
| **Operational exception** | A problem needing human attention (failed payment, verification required, etc.). |
| **Idempotency key** | Client-supplied token that makes a command safe to retry exactly once. |
| **Read model / projection** | A denormalized document shaped to render a screen without joins. |
| **Posted** | A contribution whose money movement succeeded and whose balances have been applied. Immutable thereafter. |

## 3.2 Entity relationships

```
Employer 1 ──────< many Borrowers
Borrower 1 ──────< many Loans           (see cardinality decision below)
Loan     1 ──1    BenefitAgreement       (one active agreement per loan in MVP)
BenefitAgreement 1 ──< many ScheduledContributions   (one per installment, 1..termMonths)
ScheduledContribution 1 ──< many PaymentAttempts
(any entity) 1 ──< many ServicingEvents
(any entity) 1 ──< many OperationalExceptions
```

### Cardinality decisions (MVP)

- **Employer → Borrower: 1:N.** An employer sponsors many borrowers.
- **Borrower → Loan: 1:N conceptually, constrained to 1 *active* loan in MVP.** Real borrowers hold multiple loans (multiple servicers, Parent PLUS + Stafford, pre/post-refinance). The MVP scopes to **one active loan per borrower at a time**, but the data model does **not** encode that assumption into a singular pointer.

  > **Change from v1 — no singular `activeLoanId` as the canonical link.** v1 put `activeLoanId`/`activeBenefitAgreementId` (singular) on the borrower as the canonical borrower→loan link, which cannot represent a second loan or a refinance (old loan `CLOSED`, new loan `ACTIVE`). In v2 the **canonical direction is Loan → Borrower** (`loan.borrowerId`); a borrower's loans are found by querying `loans where borrowerId == X` (indexed — see [13](./13-firestore-indexes.md)). The borrower doc keeps `primaryLoanId`/`primaryBenefitAgreementId` as a **convenience denormalization for the workbench header only**, explicitly nullable and explicitly not authoritative. This preserves the fast "open the borrower's current loan" path while allowing multiple loans without a schema change.

- **Loan → BenefitAgreement: 1:1 active.** A loan has at most one active benefit agreement. Historical/terminated agreements may exist; exactly one is `ACTIVE`/`SUSPENDED`/`ACTIVATING` at a time (enforced by the activation command — see [10](./10-benefit-and-employment-workflows.md)).
- **BenefitAgreement → ScheduledContribution: 1:N**, exactly `termMonths` installments, numbered 1..N.
- **ScheduledContribution → PaymentAttempt: 1:N**, numbered 1..N; at most one non-terminal attempt at a time.

### The mutual-pointer invariant

Because we denormalize links in both directions for read performance (`loan.benefitAgreementId` and `agreement.loanId`, etc.), any command that re-links entities (activation, refinance) **must update all sides in one transaction**. A partial update that sets `loan.benefitAgreementId` but not `agreement.loanId` leaves a dangling reference. This invariant is enumerated in [07](./07-financial-rules.md) §Invariants and enforced in command handlers.

## 3.3 Core entities (conceptual)

| Entity | Collection | Mutability | Owner of truth |
|--------|------------|------------|----------------|
| Employer | `employers` | mutable (status, denormalized counters) | Django commands + projections |
| Borrower | `borrowers` | mutable (employment status) | Django commands |
| Loan | `loans` | mutable (balance, status) | Django commands |
| Benefit agreement | `benefitAgreements` | mutable (status, amounts) | Django commands |
| Scheduled contribution | `scheduledContributions` | mutable (status) until POSTED, then immutable | Django commands + tasks |
| Payment attempt | `…/attempts` subcollection | append; terminal states immutable | Django commands + reconciliation |
| Servicing event | `servicingEvents` (+ mirror) | **immutable, append-only** | Django commands/tasks |
| Operational exception | `operationalExceptions` | mutable (status/assignment) | Django commands |
| Idempotency record | `idempotencyKeys` | mutable during operation; terminal immutable | Django commands |
| Read models | `portfolioSummaries`, `employerSummaries`, `loanWorkbenches` | derived, eventually consistent | projections only |

"Owner of truth" matters: read models are **never** authoritative and are never read back to make a financial decision — the command reads the source entities inside its transaction.

## 3.4 Common document fields

Every top-level document carries the common fields defined in the [README conventions](./README.md#global-conventions-normative--every-doc-assumes-these): `createdAt`, `updatedAt`, `createdBy`, `updatedBy`, `revision`, `schemaVersion`, and (for money-bearing docs) `currency`. See the README for the `revision` vs `expectedRevision` concurrency policy.

## 3.5 Money basics

- All amounts are integer US cents; field names end in `Cents` (e.g., `currentBalanceCents`).
- No floating point in any money computation; use integer arithmetic and solve residuals explicitly (see [07](./07-financial-rules.md)).
- A `currency` field (`"USD"`) is present on every money-bearing document so a future multi-currency change is additive rather than a migration of every amount.

## 3.6 Status enumerations (index)

The authoritative list of every status value and its allowed transitions is [06 — State Machines](./06-state-machines.md). Quick index:

Entity **creation** (employers, borrowers, loans, agreements) is **seed-only in the MVP** — there are no create endpoints; the API surface is servicing commands over seeded entities ([11](./11-api.md)).

| Entity | States |
|--------|--------|
| Employer | `ACTIVE`, `INACTIVE` (gates new activations only — [06 §6.6a](./06-state-machines.md)) |
| Borrower employment | `PENDING`, `ACTIVE`, `LEAVE`, `TERMINATED` |
| Loan | `ACTIVE`, `PAID_OFF`, `DELINQUENT`, `CLOSED` |
| Benefit agreement | `DRAFT`, `PENDING`, `ACTIVATING`, `ACTIVE`, `SUSPENDED`, `COMPLETED`, `TERMINATED` |
| Scheduled contribution | `SCHEDULED`, `PROCESSING`, `POSTED`, `FAILED`, `RETRY_PENDING`, `CANCELED` |
| Payment attempt | `STARTED`, `SUCCEEDED`, `FAILED` |
| Operational exception | `OPEN`, `IN_REVIEW`, `RESOLVED`, `DISMISSED` |
| Idempotency record | `PENDING`, `COMPLETED`, `FAILED` |

> **Change from v1 — `ACTIVATING` added to benefit agreement.** The activation workflow generates the schedule asynchronously; `ACTIVATING` is the transient state between "activation accepted" and "schedule fully generated + benefit ACTIVE," making partial-progress resumable and visible. See [10](./10-benefit-and-employment-workflows.md).
