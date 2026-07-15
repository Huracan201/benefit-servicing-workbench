# DevOps — live deployment runbook & record

**Status:** ✅ **Deployed live** (2026-07-14) — GCP project `bsw-demo` (us-east4) + Vercel. A **password-gated public demo**: Cloud Run backend + Vercel frontend + real Firestore/Auth, seeded. Verified end-to-end against the live URL (Playwright, real browser): the access gate, Firebase sign-in, live Firestore reads, and a live **payment write** (→ POSTED). This is the first real run of the Phase 6 [`infrastructure/`](../../infrastructure) runbook — companion to [phase-6-deployment.md](./phase-6-deployment.md).

**The point of this doc:** the emulator + CI green never exercise a real GCP project, so the live deploy surfaced a batch of concrete gaps (§3). They're all fixed in the scripts now; this is the record of what ran, what broke, and why.

## 1. What's live

| Piece | Detail |
|---|---|
| **Project** | `bsw-demo` (number `460913691023`), region `us-east4`, billing linked |
| **Backend** | Cloud Run `bsw-api` — min-instances **0** (scale-to-zero), max 2, 1 vCPU / 512 MiB, `--allow-unauthenticated` (Django is the auth boundary). Serves `/api/v1` + `/internal`. `ENVIRONMENT=production`; `DJANGO_SECRET_KEY` from Secret Manager. Deterministic URL `https://bsw-api-460913691023.us-east4.run.app`. |
| **Async** | 7 Cloud Tasks queues + 6 Cloud Scheduler jobs (OIDC → `/internal/*`, invoker `bsw-invoker@`) |
| **Data** | Firestore **Native** (us-east4) — rules + indexes from source, `idempotencyKeys` TTL enabled. Seeded via `seed_demo`: the deterministic dataset + 3 role users (`ops`/`mgr`/`admin@demo.test`) |
| **Frontend** | Next.js on Vercel (project `benefitservicing-workbench`), env-wired to live Firebase + the Cloud Run API URL |
| **Access** | free shared-password gate (§4); `/readiness` reports `firestore: ok` + `cloudTasks: configured` |
| **Cost** | scale-to-zero + `MAX_INSTANCES=2` + Firestore free-tier posture + daily `reset-demo`; `teardown.sh` deletes the billable resources |

## 2. The runbook that actually ran

Operator (interactive, can't be scripted): `gcloud auth login` + `application-default login`, `firebase login`, `vercel login`; GCP project + **billing**; the Firebase console **Add-project** (ToS) + **Auth → Email/Password**.

Driven by the scripts + gcloud/firebase/vercel:
1. `gcloud projects create bsw-demo` + link billing + `config set project` + ADC quota project.
2. Enable APIs (run, cloudbuild, artifactregistry, cloudtasks, cloudscheduler, firestore, firebase, identitytoolkit, secretmanager, iam, iamcredentials).
3. `gcloud firestore databases create --location=us-east4` (Native).
4. Firebase: add to project (console — see §3), `apps:create WEB` → the `NEXT_PUBLIC_FIREBASE_*` config.
5. Secret Manager: create `bsw-django-secret-key`.
6. `provision-iam.sh` → the two SAs + roles + `actAs` + `run.invoker` + `secretAccessor`.
7. `deploy-api.sh` → Cloud Build the image + `gcloud run deploy`.
8. `provision-queues.sh` + `provision-scheduler.sh`.
9. `deploy-firebase.sh` → rules + indexes + TTL.
10. `seed_demo` (locally, against live, via ADC).
11. Vercel: `vercel env add` the `NEXT_PUBLIC_*` + `vercel deploy --prod`.
12. Cloud Run `CORS_ALLOWED_ORIGINS` = the Vercel URL; add the access gate (§4).

## 3. Gaps the live deploy exposed (emulator/CI hid them) — all fixed

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | `firebase projects:addfirebase` → 403 even as Owner | Firebase **first-use / ToS gate** on the account | add the project via the **console** (accepts ToS) — one-time operator step, documented |
| 2 | IAM binds fail `Service account … does not exist` right after create | fresh SA **not instantly usable** as a policy member (propagation) | `provision-iam.sh`: `sleep 10` after create; script is idempotent so a re-run also heals it |
| 3 | Cloud Run couldn't read the secret | `provision-iam.sh` never granted **`secretAccessor`** on the secret | added the secret-scoped `secretAccessor` grant |
| 4 | Container refused to boot | `ENVIRONMENT=production` guardrail needs **`ALLOWED_HOSTS`** (explicit) + **`INTERNAL_DEV_SECRET`** (non-default); `deploy-api.sh` set neither | set both — `ALLOWED_HOSTS` **derived** from the deterministic host |
| 5 | `gcloud builds submit` had no repo to push to | `deploy-api.sh` assumed the **Artifact Registry** repo existed | create-if-missing before the build |
| 6 | `/internal` + scheduler would 400 | `status.url` returns a **legacy `*.run.app` alias** that 400s on ALLOWED_HOSTS; scripts used it for `TASKS_AUDIENCE` + scheduler | `lib.sh` `service_url()` returns the **deterministic** `service-projectnumber.region.run.app` (known pre-deploy → no post-patch) |
| 7 | `firebase deploy` **hung** (no output, timed out) | interactive "delete these indexes?" prompt with no TTY | `deploy-firebase.sh`: `--force --non-interactive` |
| 8 | `seed_demo` → `The query requires an index` | a `scheduledContributions(benefitAgreementId, status, installmentNumber)` composite was **missing** from `firestore.indexes.json` (the emulator doesn't enforce composites) | added the index; redeploy → builds in ~2 min on the empty collection |
| 9 | The pretty Vercel URL showed a Google **login wall** | Vercel **Deployment Protection** (`ssoProtection: all_except_custom_domains`) walled the scope-alias | disabled `ssoProtection` via the API (public demo) |
| 10 | Password gate showed raw text, **no browser prompt** | a non-ASCII **em-dash in the `WWW-Authenticate` realm** is an invalid header value → the whole header was dropped | ASCII-only realm |

## 4. Access gate (free)

Vercel's native **Password Protection is Pro-only** (`Advanced Deployment Protection is not enabled on your team`). Equivalent for free: `frontend/middleware.ts` — an edge HTTP Basic-Auth challenge **active only when `SITE_ACCESS_PASSWORD` is set**, so local `make demo`, CI, and the e2e suite (which never set it) stay open; only the Vercel deploy is gated. One shared password (any username), stored as a Vercel env var (never in the repo). Behind Basic-Auth, Next's link *prefetch* falls back to full navigation — benign.

## 5. Verification (by running, against the live URL)

Playwright, real browser: gate returns **401 without / 200 with** the password → app loads → anonymous redirects to `/signin` → sign in as `mgr@demo.test` → **dashboard with live data** → and a live **process-payment → POSTED**. Plus the CORS preflight (`Access-Control-Allow-Origin` = the Vercel origin) and `/readiness` (`firestore: ok`, `cloudTasks: configured`).

Note: `/loans` defaults to "0 loans · pick a filter" — **intended** (specs/13: filter-first, no all-loans index), not a defect.

## 6. Teardown & what's left

- **Stop the meter:** `bash infrastructure/scripts/teardown.sh` (deletes the Cloud Run service, queues, scheduler jobs; `--purge` also the SAs). Scale-to-zero avoids idle Cloud Run *instance* charges, but Firestore, Cloud Tasks/Scheduler, Artifact Registry, and Secret Manager can still incur small charges — teardown + a **budget alert** are the real total-cost controls.
- **Optional:** a GCP budget alert ($5–10) closes the last item from the README security-posture note.
- **Remaining:** the deferred `propagate-denormalized` fan-out (`U13`) — still awaiting its producer command; unrelated to the deploy.
