# 20 — Production Tradeoffs

This MVP deliberately keeps Firestore as the single primary store and simulates money movement. This document states plainly what a production build would add or reconsider, so the boundaries are a conscious choice rather than a blind spot. It doubles as the "What would change for production" section of the README.

## 20.1 What a production system would add

- **Dedicated accounting/ledger subsystem.** Firestore is a great operational store but not a double-entry ledger. Real money movement needs an append-only ledger with debits/credits, trial balances, and period close — likely a relational or purpose-built ledger store alongside Firestore.
- **External loan-servicer reconciliation.** Real integrations with servicers, and a reconciliation process comparing our posted contributions against servicer-confirmed receipts (our in-app reconciliation sweeper handles *our* processor; production adds *counterparty* reconciliation).
- **Outbox / durable event publication.** For reliable event delivery to downstream consumers (warehouse, notifications), a transactional outbox pattern rather than best-effort projection tasks.
- **Formal multi-tenant isolation.** The MVP treats all servicing users as one tenant seeing the whole portfolio. Production would add tenant boundaries, per-tenant data isolation, and row-level read authorization (which the current "any authenticated servicing user reads all" model intentionally omits — [12 §12.4](./12-auth-and-security.md)).
- **Data-retention & deletion policies; PII controls.** Field-level encryption for PII, key management (KMS), right-to-erasure workflows, and retention schedules per data class. The MVP uses no production PII by design.
- **Backup & disaster recovery.** Scheduled Firestore exports, tested restore runbooks, and RPO/RTO targets.
- **Formal SLOs, dead-letter queues, replay.** SLOs on processing success/latency; DLQs (the MVP has basic dead-lettering — [14 §14.5](./14-async-and-background-jobs.md)); event-replay capability for rebuilding projections from the event log.
- **Manual adjustment & reversal workflows.** Compensating adjustments for posted contributions (the event model already supports append-only corrections — [07 §7.5](./07-financial-rules.md)); maker-checker approval on financial adjustments.
- **Reporting / warehouse pipeline.** Stream events to BigQuery for analytics and ad-hoc reporting — the access pattern Firestore is *worst* at (see §20.2).
- **Full-text search.** An external search service for borrower/loan search beyond exact/prefix ([13 §13.4](./13-firestore-indexes.md)).
- **Integration-specific circuit breakers & rate limits** around each external processor/servicer.
- **Expanded compliance controls** (disclosures, audit exports, regulatory reporting) per jurisdiction.

## 20.2 Where to reassess Firestore itself

Firestore can remain the primary **operational** store indefinitely. The team should periodically reassess whether these pressures warrant a complementary store:

- **Reporting/analytics across the whole dataset** — Firestore is poor at ad-hoc cross-entity aggregation; a warehouse (BigQuery) is the right complement early.
- **Ledger/accounting semantics** — if double-entry, period close, or financial reconciliation enter scope, add a ledger store; don't stretch Firestore into a ledger.
- **Very hot aggregates at scale** — sharded counters ([05 §5.2](./05-read-models-and-projections.md)) cover a lot, but a large tenant base may justify a streaming aggregation pipeline.

The v2 design keeps these as *additive* changes: the event log is the seam that feeds a future warehouse/ledger, and projections are already isolated from the authoritative path, so introducing a complementary store does not require rewriting the command layer.

## 20.3 Conscious simplifications in the MVP (and why they're safe to demo)

| Simplification | Why it's acceptable for the MVP | What makes it safe to extend later |
|----------------|--------------------------------|-------------------------------------|
| One active loan per borrower | Demo scope | Canonical link is `loan.borrowerId`, not a singular pointer — multi-loan is a data change, not a schema break ([03 §3.2](./03-domain-model.md)) |
| Simulated payments | No real money in scope | Adapter interface (`charge`/`get_status`) matches a real processor; reconciliation is real ([09 §9.5](./09-payment-processing.md)) |
| Single currency (USD) | Domestic demo | `currency` field present everywhere — additive ([07 §7.1](./07-financial-rules.md)) |
| No interest/amortization | Servicing, not origination | `interestRateBasisPoints` stored as display-only |
| All servicing users see all data | Internal tool | Read rules are a deliberate "authenticated servicing user" gate; row-level tenancy is an additive rule change ([12 §12.4](./12-auth-and-security.md)) |
| Projections eventually consistent | Aggregates aren't authoritative | Commands read source-of-truth in-transaction; scheduled rebuild corrects drift ([05](./05-read-models-and-projections.md)) |
