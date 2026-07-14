# 21 — Deployment & Operations

The pinned operational values, infrastructure definitions, and runbook steps the other docs reference. Values here are **decisions, not examples** — change them here first.

## 21.1 Pinned constants

| Constant | Value | Used by |
|----------|-------|---------|
| `STUCK_THRESHOLD` | 10 min | reconciliation scans ([09 §9.4](./09-payment-processing.md)) |
| Phase-2 adapter timeout | 60 s (+ one retry ⇒ ≤ ~2.5 min, safely < `STUCK_THRESHOLD`) | [08 §8.4](./08-idempotency-and-consistency.md) fencing precondition |
| `LEASE_TTL` (sync commands) | 120 s | [08 §8.3](./08-idempotency-and-consistency.md) |
| `ASYNC_LEASE_TTL` | 30 min | async commands (activation, termination) |
| `MAX_SWEEPS` | 6 | indeterminate sweeps → `PAYMENT_STUCK_PROCESSING` |
| `BATCH_SIZE` | 100 items **and** ≤ 450 writes (items × writes-per-item; a cancel = 3 writes ⇒ ≤ 150/batch) | [14 §14.4](./14-async-and-background-jobs.md) |
| `SYNC_GENERATION_MAX` | 120 installments | [10 §10.1](./10-benefit-and-employment-workflows.md) |
| Idempotency retention (`expiresAt`) | 30 days after completion | [04 §4.13](./04-firestore-data-model.md) |
| REST pagination | default 50, max 200; UI tables 25 | [11 §11.5](./11-api.md) |
| `Retry-After` | 2 s (in-progress key) / 5 s (activation) | [11 §11.3](./11-api.md) |

## 21.2 GCP topology

- **Region:** `us-east4`. **Cloud Run service `bsw-api`** (one service, both surfaces): min-instances 1, concurrency 80, 1 vCPU / 512 MiB, `--allow-unauthenticated` (Django is the auth boundary for both `/api/v1` and `/internal` — [12 §12.5](./12-auth-and-security.md)).
  - **Change from spec (demo cost knob):** the `infrastructure/` scripts default **`MIN_INSTANCES=0`** (scale-to-zero: $0 when idle, ~2 s cold start on the first request after idle) rather than the production `1` (always warm, no cold start, billed 24/7). Set `MIN_INSTANCES=1` in `infrastructure/config.env` for the production posture. `MAX_INSTANCES` (default 2) caps the billable fan-out. This is the only deliberate demo deviation from the pinned topology; everything else here is authored as-is.
- **Service accounts:** runtime `bsw-api@…` — `roles/datastore.user`, `roles/firebaseauth.admin` (claims + disable), `roles/cloudtasks.enqueuer`, `roles/logging.logWriter`, `roles/monitoring.metricWriter`, plus `iam.serviceAccounts.actAs` on the invoker SA (to mint OIDC tasks). Invoker `bsw-invoker@…` — `roles/run.invoker` on `bsw-api`. **No JSON key files anywhere** — Cloud Run uses ADC; local dev uses the emulator (no credentials).
- **Cloud Tasks queues** (all with OIDC → `/internal/tasks/*`):

| Queue | max attempts | backoff | notes |
|-------|--------------|---------|-------|
| `generate-schedule` | 5 | 5–60 s | |
| `process-contribution` | 5 | 10–300 s | max-concurrent 10, rate 5/s |
| `reconcile-contribution` | 3 | 30 s | |
| `cancel-future-contributions` | 5 | 10 s | |
| `shift-schedule` | 5 | 10 s | |
| `propagate-denormalized` | 3 | 30 s | |
| `update-projection` | 3 | 5 s | |

- **Cloud Scheduler** (all `America/New_York`, OIDC → `/internal/jobs/*`): `enqueue-due-contributions` `0 9-17 * * 1-5` · `reconcile-stuck-payments` `*/10 * * * *` · `rebuild-summaries` `*/15 * * * *` + full `0 3 * * *` · `reap-expired-leases` `*/5 * * * *` · `reset-demo` `0 5 * * *`.
- **No native Cloud Tasks DLQ** — final-attempt self-detection via `X-CloudTasks-TaskRetryCount` → `TASK_FAILED` exception ([14 §14.5](./14-async-and-background-jobs.md)).

## 21.3 Configuration matrix

| Var | local | ci | demo |
|-----|-------|----|------|
| `FIRESTORE_EMULATOR_HOST` / `FIREBASE_AUTH_EMULATOR_HOST` | `localhost:8080` / `:9099` | same | — |
| `GOOGLE_CLOUD_PROJECT` | `demo-benefitservicing-workbench` | same | real project id |
| `DJANGO_SECRET_KEY` | dev literal | dev literal | **Secret Manager** |
| `DEBUG` / `ALLOWED_HOSTS` | `1` / `*` | `1` / `*` | `0` / Cloud Run host |
| `SYSTEM_TIMEZONE` | `America/New_York` | same | same |
| `TASK_EXECUTION_MODE` | `inline` (auto under emulator) | `inline` | `cloud` |
| `TASKS_AUDIENCE` / `TASKS_INVOKER_SA` | — (dev secret header) | — | Cloud Run URL / `bsw-invoker@…` |
| `INTERNAL_DEV_SECRET` | dev literal | dev literal | — |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000` | same | Vercel URL |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000/api/v1` | same | Cloud Run URL |
| `NEXT_PUBLIC_FIREBASE_*` (full web config) | emulator project | same | real web config (Vercel env) |
| `NEXT_PUBLIC_USE_FIREBASE_EMULATOR` | `true` | `true` | `false` |

**CORS (normative):** django-cors-headers; allow headers `Authorization, Content-Type, Idempotency-Key, If-Match, X-Correlation-Id`; **`Access-Control-Expose-Headers: Retry-After`** (the 202 poll contract is browser-invisible without it); credentials off; `OPTIONS` exempt from auth. Add the Vercel domain to Firebase Auth **authorized domains** or Google sign-in breaks.

## 21.4 Deploy runbook (demo)

1. `gcloud builds submit` → deploy image to Cloud Run `bsw-api` with the env above.
2. `firebase deploy --only firestore:rules,firestore:indexes --config firebase/firebase.json --project <real>`.
3. **Firestore TTL policy (explicit step, easy to miss):** `gcloud firestore fields ttls update expiresAt --collection-group=idempotencyKeys --enable-ttl`.
4. Create queues + scheduler jobs per §21.2 (`infrastructure/scripts/`).
5. **Bootstrap first admin:** `python manage.py set_role admin@demo.test ADMINISTRATOR` (break-glass path — [12 §12.3](./12-auth-and-security.md)).
6. Seed: `python manage.py seed_demo --project <real>` (pinned demo creds — [18 §18.1](./18-seed-and-demo.md)).
7. Deploy frontend to Vercel with `NEXT_PUBLIC_*`; add domain to Firebase authorized domains.
8. Alerts ([16 §16.4](./16-observability.md)): log-based metrics + policies — stuck-PROCESSING > 0 for 30 min; any `TASK_FAILED`; readiness failing 5 min.

## 21.5 Local dev loop

Emulator + `TASK_EXECUTION_MODE=inline` = the full async surface runs synchronously in-process; `manage.py run_job <name>` fires any scheduler job on demand (also used to demo due-processing). Pin Python 3.12 / Node 20 (`backend/requirements.txt`, `.nvmrc`); see [firebase/emulator/README.md](../firebase/emulator/README.md).
