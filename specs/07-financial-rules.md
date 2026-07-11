# 07 — Financial Rules & Invariants

These rules are enforced in the command layer, inside the transaction that performs the state change, using integer arithmetic only. They are safety-critical; they live in `backend/common/money.py` and `backend/common/invariants.py` and are unit-tested exhaustively ([17](./17-testing.md)).

## 7.1 Money representation

- All monetary values are **integer US cents** (`int`). No `float`, no `Decimal`-to-float round-trips in the money path.
- Field names carry the `Cents` suffix.
- Every money-bearing document carries `currency: "USD"`. The MVP is single-currency; the field exists so multi-currency is an additive change, not a mass migration.

> **Change from v1 — currency field made explicit.** v1 mandated integer cents but had no currency field anywhere, leaving USD implicit. v2 stores `currency` on every money-bearing document.

## 7.2 Core invariants

Checked inside the transaction, before commit. Violations reject the command (`409 INVARIANT_VIOLATION`) and are logged ([16](./16-observability.md)).

| # | Invariant |
|---|-----------|
| I1 | `loan.currentBalanceCents >= 0` at all times. |
| I2 | `agreement.amountPaidCents <= agreement.totalCommitmentCents` at all times. |
| I3 | `agreement.remainingCommitmentCents == agreement.totalCommitmentCents − agreement.amountPaidCents`, always co-updated in the same write. |
| I4 | A **posted** contribution's `postedAmountCents <= min(scheduledAmountCents, loan.currentBalanceCents, agreement.remainingCommitmentCents)` (the balance/commitment caps evaluated at post time). |
| I5 | `Σ(scheduledAmountCents over all installments of an agreement) == agreement.totalCommitmentCents` (established at generation; see §7.3). |
| I6 | A `POSTED` contribution is immutable; balances derived from it are never rewritten (corrections are separate events — §7.5). |
| I7 | Referential integrity of the mutual pointers (`loan.benefitAgreementId` ⇄ `agreement.loanId`, etc.) is maintained atomically ([03 §3.2](./03-domain-model.md)). |

## 7.3 Schedule amounts & the residual (the rounding rule)

When a benefit is activated, the installment amounts are **solved** so they sum exactly to the total commitment, using integer division plus a remainder distributed to the final installment.

```
base      = totalCommitmentCents // termMonths          # integer division
remainder = totalCommitmentCents −  base * termMonths    # 0 <= remainder < termMonths

installment[i].scheduledAmountCents = base               for i in 1..(termMonths−1)
installment[termMonths].scheduledAmountCents = base + remainder
```

**Worked example (the corrected v1 §14 case):** `totalCommitmentCents = 3,000,000`, `termMonths = 36`.
- `base = 3,000,000 // 36 = 83,333`; `remainder = 3,000,000 − 83,333*36 = 3,000,000 − 2,999,988 = 12`.
- Installments 1–35 = **83,333**; installment 36 = **83,345**.
- Σ = `35*83,333 + 83,345 = 2,916,655 + 83,345 = 3,000,000` ✓.
- After 10 posted installments: `amountPaidCents = 833,330`, `remainingCommitmentCents = 2,166,670` — identical to the v1 example's other fields, which were internally consistent; only the flat-83,333 schedule was wrong.

> **Change from v1 — residual solved at generation, not at post time.** v1's rule "final contributions may be *reduced* to the smaller allowable amount" only handled *overage*; a flat schedule that sums to *less* than the commitment (the actual case) had no rule, so `remainingCommitment` never reached 0 and a benefit keyed on `remaining == 0` would **never COMPLETE**. Worse, v1's cap "posted may not exceed *scheduled amount*" *forbade* the natural fix of a larger final payment — a direct contradiction. v2 removes the contradiction by making the schedule sum exact up front (I5). The "reduce to smaller allowable" rule (I4) is then reserved for its only legitimate purpose: a **balance-driven** cap when the remaining loan balance is less than the scheduled amount on the final payoff installment.

## 7.4 Balance-capped final payment (payoff)

If, at post time, the loan's remaining balance is less than the contribution's scheduled amount (the loan is nearly paid off), the posted amount is reduced to the remaining balance:

```
postedAmountCents = min(scheduledAmountCents, loan.currentBalanceCents, agreement.remainingCommitmentCents)
```

- `loan.currentBalanceCents −= postedAmountCents`; if it reaches 0, loan → `PAID_OFF`.
- `agreement.amountPaidCents += postedAmountCents`; `remainingCommitmentCents` co-updated.
- Any remaining scheduled installments for a fully paid-off loan are `CANCELED` (the commitment is satisfied by loan payoff); a `BENEFIT_COMPLETED` event is written and the agreement → `COMPLETED`.
- The contribution records both `scheduledAmountCents` (planned) and `postedAmountCents` (actual), so the audit trail shows the cap.

This is [demo scenario 8](./18-seed-and-demo.md) ("loan balance lower than the next scheduled contribution").

## 7.5 Immutability & corrections

- A `POSTED` contribution and its `SUCCEEDED` attempt are immutable.
- The MVP does **not** support reversing or editing a posted contribution.
- In a production system a correction would be a **separate compensating adjustment** with its own event (`LOAN_BALANCE_ADJUSTED`), never an in-place edit — see [20](./20-production-tradeoffs.md). The event model already supports this (append-only), so it is an additive future capability, not a redesign.

## 7.6 Benefit completion

A benefit reaches `COMPLETED` when either:
- `remainingCommitmentCents == 0` (full commitment disbursed), or
- the loan reaches `PAID_OFF` and remaining installments are cancelled (§7.4).

Because of I5, the first condition is now actually reachable — the schedule sums exactly to the commitment. A `BENEFIT_COMPLETED` servicing event is written at the transition (legal from `ACTIVE` or, for a payoff settling in flight, from `SUSPENDED` — [06 §6.3](./06-state-machines.md)).

## 7.7 Residual commitment on terminal agreements (rollup scoping)

A payoff-`COMPLETED` or `TERMINATED` agreement legitimately retains `remainingCommitmentCents > 0` — I3 keeps the agreement-level field as an accurate historical record (`total − paid`), and it is **not** zeroed. But that residual is money that will never move, so **employer- and portfolio-level "remaining commitment" rollups sum only non-terminal agreements** (`ACTIVE`, `SUSPENDED`, `ACTIVATING`). This is a projection rule ([05](./05-read-models-and-projections.md)); without it, dashboards permanently overstate future outflow by every terminated/paid-off agreement's residue.

## 7.8 Schedule shift on resume

When a suspended benefit is **resumed**, installments whose `scheduledDate` passed during the suspension are **not** fired as an immediate catch-up lump. Instead the remaining schedule **shifts**: every remaining `SCHEDULED` installment is re-dated forward by the suspension duration (rounded up to whole months, preserving day-of-month and the noon rule), and `agreement.endDate` extends by the same amount. Amounts and installment numbers are untouched, so I5 (`Σ == totalCommitment`) and the deterministic IDs are preserved, and the benefit still reaches `COMPLETED`. `RETRY_PENDING`/`FAILED` installments are *past obligations*, not future schedule — they are **not** re-dated; they become processable/retryable again the moment `acceptingPayments` flips true. The shift runs as a bounded async task and writes a `SCHEDULE_SHIFTED` event ([10 §10.2](./10-benefit-and-employment-workflows.md)).
