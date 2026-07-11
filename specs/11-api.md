# 11 — API Specification

Base path: `/api/v1`. All responses are JSON. The API exposes **business commands**, not generic document mutations ([02 P1](./02-architecture.md)).

> **Authoritative contract: [`openapi.yaml`](./openapi.yaml).** This document is the human-readable overview and rationale; the OpenAPI 3.1 file is the machine-readable source of truth for every endpoint's parameters, request/response schemas, and error codes. Generate client/server types and mock servers from it, validate requests against it, and lint it in CI ([17 §17.5](./17-testing.md)). If the two ever disagree, `openapi.yaml` wins — update it in the same PR as any endpoint change.

## 11.1 Read path vs. command path

> **Change from v1 — the read/command split is explicit.** v1 listed several `GET` endpoints while also having the client subscribe to Firestore directly, without saying which reads go where. v2:
> - **Live/list reads are Firestore client-SDK subscriptions** to read models ([05](./05-read-models-and-projections.md)), governed by security rules. These are the default for every screen.
> - **Django `GET` endpoints exist only where a read needs server-side composition or authorization beyond what security rules express** (e.g. a health/readiness probe, an admin export). The MVP keeps this set minimal.
> - **All mutations are Django command endpoints.** No client writes to protected collections.

So the endpoint list below is **command-centric**; most reads are subscriptions, not REST calls.

## 11.2 Conventions for command endpoints

- **`Idempotency-Key: <client-generated-key>`** header is **required** on every mutating command. Missing key ⇒ `400 IDEMPOTENCY_KEY_REQUIRED`.
- **`Authorization: Bearer <Firebase ID token>`** required on every call.
- Optional **`If-Match: <expectedRevision>`** (or `expectedRevision` in body) for stale-UI protection: if the target entity's `revision` differs, the command is rejected `409 STALE_WRITE` ([README conventions](./README.md#global-conventions-normative--every-doc-assumes-these)).
- Requests carry a `correlationId` (generated if absent) echoed into events and logs.

## 11.3 Response & status semantics

| Status | Meaning |
|--------|---------|
| `200 OK` | Command completed; body is the result. A replayed idempotent request returns the **same** `200` body. |
| `202 Accepted` | Operation accepted and **in progress** (async generation, or an in-progress idempotency key). **Any** mutating endpoint may return it (in-progress same-key replay). Body includes operation/entity state + `Retry-After`. **Poll mechanism (normative):** re-POST the *identical* request with the *same* `Idempotency-Key` (safe by [08 §8.2](./08-idempotency-and-consistency.md) — returns `202` while running, the final result when done), or — recommended for the UI — observe the entity document via its Firestore subscription. `Retry-After`: 2 s (in-progress key), 5 s (activation). Never re-issue under a new key. |
| `400` | Validation error (missing key, empty note, bad enum). |
| `401` / `403` | Unauthenticated / role not permitted. |
| `409` | Conflict — typed: `INVALID_TRANSITION`, `INVARIANT_VIOLATION`, `IDEMPOTENCY_KEY_REUSED` (same key, different request hash), `STALE_WRITE`, `BENEFIT_NOT_ACCEPTING_PAYMENTS`. |
| `422` | Well-formed but business-invalid (e.g. activate when employment not ACTIVE). |
| `5xx` | Infrastructure error; safe to retry with the **same** idempotency key. |

Error body shape: `{ "error": { "code": "INVALID_TRANSITION", "message": "...", "correlationId": "..." } }`.

## 11.4 Endpoints

**Benefit agreements**
```
GET  /benefit-agreements/{agreementId}                 # server-composed detail (optional; subscription is default)
POST /benefit-agreements/{agreementId}/activate        # → 202 (async schedule generation)   [MANAGER+]
POST /benefit-agreements/{agreementId}/suspend         [MANAGER+]
POST /benefit-agreements/{agreementId}/resume          [MANAGER+]
POST /benefit-agreements/{agreementId}/terminate       [MANAGER+]
```

**Borrower employment**
```
POST /borrowers/{borrowerId}/employment-status         [MANAGER+]
     body: { "status": "TERMINATED", "effectiveDate": "2026-10-14", "reason": "Employment ended" }
```

**Contributions**
```
POST /contributions/{contributionId}/process           [MANAGER+ manual; or SYSTEM via task]
POST /contributions/{contributionId}/retry             [OPERATIONS+]
```
(Contribution lists for the payment queue are Firestore subscriptions filtered by `status`/`employerId` with pagination — [05 §5.6](./05-read-models-and-projections.md).)

**Exceptions**
```
POST /exceptions                                       [OPERATIONS+]  create a manual exception
     body: { "exceptionType": "EMPLOYMENT_VERIFICATION_REQUIRED", "entityType": "BORROWER",
             "entityId": "bor_x", "summary": "...", "details": "..." }   (severity from the 04 §4.10 map)
POST /exceptions/{exceptionId}/assign                  [OPERATIONS+]  body { assignToUid } — null/omitted = self; explicit null = unassign
POST /exceptions/{exceptionId}/mark-in-review          [OPERATIONS+]
POST /exceptions/{exceptionId}/resolve                 [OPERATIONS+]
POST /exceptions/{exceptionId}/dismiss                 [OPERATIONS+]
```
(Assignment is status-neutral — [06 §6.4](./06-state-machines.md).)

**Notes**
```
POST /loans/{loanId}/notes                             [OPERATIONS+]   body: { "text": "..." }
```

**Admin**
```
POST /admin/users/{uid}/role                           [ADMIN]   body: { "role": "SERVICING_MANAGER" }
     # sets Firebase custom claim AND upserts users/{uid} mirror in one operation
POST /admin/employers/{employerId}/status              [ADMIN]   body: { "status": "INACTIVE" }
     # INACTIVE blocks new benefit activations only (06 §6.6a)
```

**Internal (NOT part of this contract):** Cloud Tasks/Scheduler handlers live under `/internal/tasks/*` and `/internal/jobs/*`, authenticated by Google OIDC + invoker service account, never by Firebase tokens — [12 §12.5](./12-auth-and-security.md).

**Health**
```
GET /health        # liveness
GET /readiness     # dependencies (Firestore reachable, etc.)
```

## 11.5 Pagination (for any list `GET`)

List endpoints use cursor pagination: `?limit=50&cursor=<opaque>`; response `{ items: [...], nextCursor }`. **Normative:** default `limit` 50, max 200 (`400 VALIDATION_ERROR` above); cursor = base64url(JSON of the orderBy values + doc id); unknown/expired cursor → `400`. UI tables default to 25/page. Firestore subscriptions paginate identically via query cursors in the client hooks.

> **Change from v1 — pagination + query params specified.** v1's `GET /loans`, `GET /contributions`, `GET /exceptions` had no filter or pagination contract (an unbounded `GET /contributions` could return the whole collection). v2 requires `limit` + cursor on every list read and documents the filter params (`status`, `employerId`, `severity`, etc.) matching the composite indexes in [13](./13-firestore-indexes.md).

## 11.6 Idempotency examples

- **First call** `POST /contributions/ben_jordan_001__004/process` with key `k1` → `200 { status: POSTED, attemptId, ... }`.
- **Retry** same key `k1`, same body → `200` with the **identical** body (replay from the idempotency record).
- **Reuse** key `k1` with a *different* body → `409 IDEMPOTENCY_KEY_REUSED`.
- **Concurrent** second call with key `k1` while the first is mid-flight → `202 IN_PROGRESS` (client polls). See [08 §8.2–8.3](./08-idempotency-and-consistency.md).
