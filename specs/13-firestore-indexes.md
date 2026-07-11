# 13 — Firestore Indexing Strategy

Indexes are defined in `firebase/firestore.indexes.json`, versioned in source control, and deployed per environment. No index is created by hand in the console. Each composite index below maps to a specific screen query or background query; if a query has no listed index, it is not a supported access pattern.

## 13.1 Composite indexes

Reads follow the CQRS split ([02 P7](./02-architecture.md)): the **portfolio UI subscribes to the `loanWorkbenches` read model**, so its filter/sort indexes live there; the authoritative `loans` collection carries only the backend lookup index. Firestore requires indexes for backend (Admin SDK) queries too, not just client reads.

**Loans** (backend authoritative lookups)
| Fields | Serves |
|--------|--------|
| `borrowerId ASC, loanStatus ASC` | **a borrower's loans / active loan** (authoritative borrower→loan lookup — [03 §3.2](./03-domain-model.md)) |

**Loan workbenches** (portfolio screen filters & sorts — the read model the UI subscribes to)
| Fields | Serves |
|--------|--------|
| `employerId ASC, loanStatus ASC` | an employer's loans by status |
| `employerId ASC, benefitStatus ASC, loanStatus ASC` | combined portfolio filters |
| `benefitStatus ASC, nextContributionDate ASC` | active-benefit loans by upcoming contribution |
| `employmentStatus ASC, loanStatus ASC` | filter by employment status |
| `openExceptionCount DESC, updatedAt DESC` | loans with open exceptions, most-recent first |

> **Change from v1 — portfolio indexes moved to the `loanWorkbenches` read model; `borrowerId` lookup added.** v1 placed all loan indexes on the authoritative `loans` collection, but the read path should hit read models (P7), and v1's `benefitStatus + nextContributionDate` referenced a field the loan doc didn't define. v2 keeps only the backend `borrowerId` lookup on `loans` and moves the UI portfolio filters/sorts onto `loanWorkbenches`, which carries `benefitStatus`, `employmentStatus`, `nextContributionDate`, and `openExceptionCount` ([05 §5.5](./05-read-models-and-projections.md)). (The `loans` doc still keeps `benefitStatus`/`nextContributionDate` as synced fields for the account-header display — [04 §4.5](./04-firestore-data-model.md) — they're just not the portfolio *query* target.) v1's `borrowerName + loanStatus` is **dropped** — name is a stale, non-unique, non-searchable key; borrower lookups key on `borrowerId`, and text search is out of scope (§13.4).

**Scheduled contributions** (payment queue + scheduler + reconciliation)
| Fields | Serves |
|--------|--------|
| `status ASC, scheduledDate ASC` | payment-queue tabs; **due-contribution scan** for the scheduler |
| `employerId ASC, status ASC, scheduledDate ASC` | payment queue filtered by employer |
| `benefitAgreementId ASC, installmentNumber ASC` | an agreement's schedule in order |
| `loanId ASC, scheduledDate ASC` | a loan's contributions on the detail screen |
| `status ASC, lastAttemptAt ASC` | **stuck-PROCESSING scan** for the reconciliation sweeper ([09 §9.4](./09-payment-processing.md)) |

> **Change from v1 — reconciliation and ordered-schedule indexes added.** The `status + lastAttemptAt` index is required by the new sweeper; `benefitAgreementId + installmentNumber` replaces v1's `benefitAgreementId + scheduledDate` for deterministic in-order schedule display (installment number is the stable order).

**Operational exceptions** (exception workbench)
| Fields | Serves |
|--------|--------|
| `status ASC, severityRank DESC, createdAt DESC` | workbench default: open, most severe, newest |
| `assignedTo ASC, status ASC, createdAt DESC` | "my queue" |
| `exceptionType ASC, status ASC, createdAt DESC` | trend/filter by type |
| `loanId ASC, status ASC` | exceptions on an account |
| `employerId ASC, status ASC, severityRank DESC` | exceptions by employer |

> **Sort by `severityRank`, not `severity`.** `severity` is a string (`LOW…CRITICAL`); string ordering does **not** match importance (alphabetically `CRITICAL < HIGH < LOW < MEDIUM`). The exception doc carries a numeric `severityRank` (LOW=10, MEDIUM=20, HIGH=30, CRITICAL=40 — [04 §4.10](./04-firestore-data-model.md)) written alongside `severity`; "most severe first" orders by `severityRank DESC`.

**Borrowers** (operational lists/search-by-facet)
| Fields | Serves |
|--------|--------|
| `employerId ASC, employmentStatus ASC` | an employer's employees by status |
| `employmentStatus ASC, displayName ASC` | employees by status, alphabetical |

> **Change from v1 — borrowers indexes added.** v1 defined none, though an ops tool lists/filters borrowers constantly.

**Benefit agreements**
| Fields | Serves |
|--------|--------|
| `employerId ASC, status ASC` | an employer's agreements by status |
| `status ASC, endDate ASC` | agreements nearing completion ([scenario 5](./18-seed-and-demo.md)) |

> **Change from v1 — benefitAgreements indexes added.**

**Idempotency keys** (internal — lease reaper)
| Fields | Serves |
|--------|--------|
| `status ASC, leaseExpiresAt ASC` | `reap-expired-leases` scan: `status == PENDING AND leaseExpiresAt < now` ([14 §14.2](./14-async-and-background-jobs.md)) |

**Servicing events** (global audit stream + per-account mirror)
| Fields | Serves | Collection group |
|--------|--------|------------------|
| `entityType ASC, entityId ASC, createdAt DESC, sequence DESC` | an entity's global timeline in stable order | `servicingEvents` |
| `correlationId ASC, sequence ASC` | all events of one command (tracing) | `servicingEvents` |
| `employerId ASC, createdAt DESC` | recent activity for an employer | `servicingEvents` |
| `eventType ASC, createdAt DESC` | activity by type (dashboard "recent activity") | `servicingEvents` |
| `createdAt DESC, sequence DESC` | per-account timeline in the `events` mirror | `events` (COLLECTION scope; covers `loans/*/events` and `borrowers/*/events`) |

> **Change from v1 — global-event indexes added; the per-account mirror needs its own composite.** v1 covered only the per-loan subcollection and assumed no composite was needed, but ordering the mirror by `(createdAt, sequence)` (two fields) does require one — defined on collection group `events` at COLLECTION scope, which covers both the loan and borrower mirrors. Ordering includes `sequence` for the stable within-transaction order ([08 §8.5](./08-idempotency-and-consistency.md)).

## 13.2 Single-field index exemptions (write-cost control)

`servicingEvents` is a high-volume, append-only, immutable log. Firestore auto-indexes **every** field by default, including the `metadata` map — pure write amplification for fields never queried alone.

> **Change from v1 — exemptions specified.** `firestore.indexes.json` declares single-field index **exemptions** (`fieldOverrides` with an empty `indexes` array) for large/free-text fields never queried on their own: `servicingEvents.metadata`, `events.metadata`, `operationalExceptions.details`, `scheduledContributions.failureReason`, and `attempts.failureReason`. This keeps only the fields used by the composite indexes above indexed, cutting write amplification on the high-volume append-only logs.

## 13.2a Filter-combination discipline

The UI may only offer filter **combinations** served by a composite here. Notably: "employer + has-open-exception" on the portfolio is *not* served (equality + range on different fields) — the UI offers has-open-exception as a standalone toggle, not combined with employer ([15 §15.3](./15-ui-and-screens.md)). The exception workbench's severity filter *is* served: equality on `status` + `severityRank` with `createdAt` ordering uses the existing `status+severityRank+createdAt` composite (equality works against a DESC field).

## 13.3 Index ↔ subscription discipline

Every frontend subscription's `where`/`orderBy` must be backed by an index here, with a `limit` and cursor ([05 §5.6](./05-read-models-and-projections.md)). Adding a new screen filter means adding the index in the same PR — an unsupported query fails fast in dev against the emulator rather than surprising us in the demo.

## 13.4 Text search (explicit non-capability)

Substring/fuzzy search over borrower names/emails and loan references is **not** provided by Firestore indexes. The MVP supports **exact/prefix** lookups (e.g. exact loan reference, borrower by id) and facet filters (the composite indexes above). Full-text search is a production add (external search service) — [20](./20-production-tradeoffs.md). The UI search box is scoped accordingly ([15](./15-ui-and-screens.md)).
