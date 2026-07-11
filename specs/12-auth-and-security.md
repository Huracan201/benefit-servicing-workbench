# 12 — Authentication & Authorization

The security model has two enforcement points because the system has two paths ([02 P7](./02-architecture.md)): **Django enforces the write path; Firestore security rules enforce the read path.** Both derive the user's role from the same source of truth: **Firebase custom claims.**

## 12.1 Authentication

- Firebase Authentication. MVP methods: email/password and Google sign-in.
- The client obtains a Firebase **ID token** and sends it as `Authorization: Bearer <token>` on every Django call.
- Django verifies the token with the Firebase Admin SDK on every request (signature, expiry, audience). Failure ⇒ `401`.

## 12.2 Roles & the permission matrix

Roles: `OPERATIONS_USER`, `SERVICING_MANAGER`, `ADMINISTRATOR` (each superset of the previous for servicing actions).

| Capability | OPERATIONS_USER | SERVICING_MANAGER | ADMINISTRATOR |
|-----------|:---:|:---:|:---:|
| View portfolio / accounts / timelines | ✓ | ✓ | ✓ |
| Add servicing note | ✓ | ✓ | ✓ |
| Assign / review / resolve / dismiss exceptions | ✓ | ✓ | ✓ |
| Retry failed payment | ✓ | ✓ | ✓ |
| Process payment (manual) | | ✓ | ✓ |
| Activate / suspend / resume / terminate benefit | | ✓ | ✓ |
| Change employment status | | ✓ | ✓ |
| Manage users & roles | | | ✓ |
| System-level views / config | | | ✓ |

> **Change from v1 — employment-status change is MANAGER+.** v1 listed it under Operations User *and* Manager; because it triggers a financial benefit cascade ([10 §10.4](./10-benefit-and-employment-workflows.md)) v2 restricts the *command* to Manager+. Operations Users flag employment issues via an `EMPLOYMENT_VERIFICATION_REQUIRED` exception.

## 12.3 Role source of truth: custom claims

> **Change from v1 — role propagation is specified.** v1 said clients "may read permitted collections based on role" but never said how a security rule learns the role, and never mentioned custom claims — the single most important omission for read security, since Django is not in the read path.

- A user's role is stored as a **Firebase custom claim** (`role`), set via the Admin SDK.
- The **`POST /admin/users/{uid}/role`** command (ADMIN only) is the sole way to change a role; it sets the claim **and** updates the `users/{uid}` mirror document in one operation, and writes an audit event.
- Custom claims are embedded in the ID token, so **a role change takes effect when the client's token refreshes** (up to ~1 hour, or immediately if the client is forced to refresh). Implication: role *revocation* is not instantaneous. For the MVP this is acceptable and documented; on a sensitive role removal (e.g. offboarding) the admin flow also **disables the Firebase user** (`users/{uid}.status = DISABLED` + Firebase disable).
- **Revocation checking (normative):** plain `verify_id_token` does *not* detect a disabled user until token expiry. Django therefore calls `verify_id_token(token, check_revoked=True)` on **every mutating command** (the extra Auth round-trip is acceptable on writes) and plain verification on reads/health. Consequence stated honestly: disable is immediate on the **write path**; direct-Firestore **reads** remain possible until the token expires (≤ ~1 h) — see §12.4.
- Django reads the role from the **verified token claims** (not from a Firestore lookup) for write-path authorization.
- **Bootstrap (first administrator).** Role-granting requires an ADMIN — circular for user #1. The break-glass path is a management command run by an operator with project credentials: `python manage.py set_role <email> <ROLE>` (calls the Admin SDK directly, upserts the `users/{uid}` mirror, writes a `USER_ROLE_CHANGED` event with `actorType: SYSTEM`). Documented in the deploy runbook ([21](./21-deployment-and-operations.md)); demo users come exclusively from the seed script.
- **Provisioning & discovery.** There is **no auto-provisioning on first sign-in**: an authenticated user with no role claim has zero read or write access until an admin grants a role (the role command **upserts** the mirror doc, creating it if absent). MVP user discovery is the Firebase console / seed list — there is deliberately no `GET /admin/users` endpoint.

## 12.4 Firestore security rules (read path)

Rules deny by default. Reads are allowed only to authenticated users with a valid servicing role **claim** (the rules check the claim only — an account-disable takes effect on the read path when the token expires, ≤ ~1 h; the immediate check is write-path, §12.3). There is **no per-borrower row-level tenancy** in the MVP: this is an internal servicing tool, so any authenticated servicing user may read the whole portfolio. (Multi-tenant isolation is a production concern — [20](./20-production-tradeoffs.md).)

> **Change from v1 — "read based on role" scoped honestly.** v1 implied granular per-role read rules. In practice all three roles read essentially the same operational data; the meaningful boundary is *authenticated servicing user vs. not*, plus admin-only collections. v2 states this so nobody builds elaborate (and slow) per-row read rules, and so the exposure is deliberate rather than accidental.

Illustrative rules (full version in `firebase/firestore.rules`, tested in the emulator):

```
rules_version = '2';
service cloud.firestore {
  match /databases/{db}/documents {

    function signedIn()      { return request.auth != null; }
    function hasRole()       { return signedIn() && request.auth.token.role in
                                 ['OPERATIONS_USER','SERVICING_MANAGER','ADMINISTRATOR']; }
    function isAdmin()       { return signedIn() && request.auth.token.role == 'ADMINISTRATOR'; }

    // Operational collections: read for any servicing user; NO client writes.
    match /loans/{id}            { allow read: if hasRole(); allow write: if false; }
    match /borrowers/{id}         { allow read: if hasRole(); allow write: if false; }
    match /benefitAgreements/{id} { allow read: if hasRole(); allow write: if false; }
    match /scheduledContributions/{id} {
                                   allow read: if hasRole(); allow write: if false;
      match /attempts/{aid}       { allow read: if hasRole(); allow write: if false; } }
    match /operationalExceptions/{id}  { allow read: if hasRole(); allow write: if false; }
    match /servicingEvents/{id}   { allow read: if hasRole(); allow write: if false; }
    match /loans/{id}/notes/{n}   { allow read: if hasRole(); allow write: if false; }
    match /loans/{id}/events/{e}  { allow read: if hasRole(); allow write: if false; }

    // Read models: read for any servicing user; writes only by backend.
    match /portfolioSummaries/{id}  { allow read: if hasRole(); allow write: if false; }
    match /employerSummaries/{id}   { allow read: if hasRole(); allow write: if false; }
    match /loanWorkbenches/{id}     { allow read: if hasRole(); allow write: if false; }
    match /employers/{id}           { allow read: if hasRole(); allow write: if false; }

    // Never exposed to clients at all.
    match /idempotencyKeys/{id}     { allow read, write: if false; }
    match /simulatedCharges/{id}    { allow read, write: if false; }

    // Users: a user may read their own doc; NOBODY writes via client (incl. self).
    match /users/{uid} {
      allow read:  if signedIn() && (request.auth.uid == uid || isAdmin());
      allow write: if false;        // role changes go through the backend admin command
    }
  }
}
```

> **Change from v1 — `users/{uid}` self-write denied.** If a client could write its own `users` doc, and if any rule trusted that doc for role, a user could self-escalate. v2 denies all client writes to `users` (including self); the role mirror is written only by the backend admin command, and the *authoritative* role is the claim, not the doc.

## 12.5 Backend write authorization & internal-endpoint ingress

- The backend runs as a Firestore **service account** that bypasses security rules (it must, to perform protected writes).
- Every command handler independently checks the caller's role (from verified claims) against the capability matrix before doing any work. Security rules are **not** the write-path check — Django is.
- Task handlers run as the service account with `actorType: SYSTEM`; they are not user-authorized but are still audited (`createdBy: system:<job>`).

**Inbound auth for `/internal/*` (normative — closes the open-handler hole).** The Cloud Run service is deployed `--allow-unauthenticated` (Firebase ID tokens are app-layer, not Google IAM), so task/scheduler handler URLs are internet-reachable and **must verify their caller**:

- All Cloud Tasks handlers live under **`/internal/tasks/<handler>`**; all Cloud Scheduler job endpoints under **`/internal/jobs/<job>`**. Neither namespace appears in `openapi.yaml` (not part of the public contract).
- Every `CreateTask` and every Scheduler job attaches an **OIDC token** for the dedicated invoker service account (`bsw-invoker@…`), audience = the Cloud Run URL.
- Django middleware on `/internal/*` verifies the Google-signed OIDC JWT (`google.oauth2.id_token.verify_oauth2_token`), asserting `aud == TASKS_AUDIENCE` **and** `email == TASKS_INVOKER_SA`; anything else → `403`. Firebase user tokens are never accepted on `/internal/*`.
- **Local/emulator:** when `FIRESTORE_EMULATOR_HOST` is set the middleware accepts a shared-secret header instead (`X-Internal-Auth: $INTERNAL_DEV_SECRET`), preserving the direct-invocation dev loop ([14](./14-async-and-background-jobs.md), [21](./21-deployment-and-operations.md)).

Summary: Django is the auth boundary for **both** surfaces — Firebase ID token + role claim on `/api/v1`, Google OIDC + invoker identity on `/internal`.

## 12.6 Security testing

Security rules are tested with the Firebase Emulator Suite ([17](./17-testing.md)): each protected collection is asserted **unreadable without a role claim, unwritable by any client, and readable with a role claim**; `users` self-write is asserted denied; idempotency keys are asserted fully client-invisible.
