# Phase 6 — Deployment & docs

**Status:** ✅ Merged to `main` (PR #11, merge commit `2c3c3cc`). CI **7/7 green** — including a new `infra` job that **builds `backend/Dockerfile` for real** and shellchecks every script. Verified by running where runnable (unit tests, shellcheck, `gunicorn --check-config`, Spectral, CI's Docker build); the live `gcloud` apply + the headless screenshot capture are warm-machine / operator steps (see §4).

**Phase:** 6 — Deployment & docs (`U12`) ([specs/19 §19.2](../19-delivery-and-scope.md))

## 1. Scope — what Phase 6 actually was

The application stack was already built, merged, security-reviewed, and hardened (Phases 1–5 on `main`), and the code was already deploy-shaped — the `TASK_EXECUTION_MODE=inline|cloud` seam existed, rules/indexes deploy from source, `seed_demo`/`set_role` existed, and **[specs/21](../21-deployment-and-operations.md) had already pinned the entire topology** (Cloud Run `bsw-api`, the 7 Cloud Tasks queues, the 6 Cloud Scheduler jobs, the per-environment config matrix, the 8-step runbook). So Phase 6 resolved to the *deploy-time remainder*: turn that pinned runbook into reviewable code, flip `/readiness`, and write the demo.

**The one consequential fork** — the spec's success criterion ([§19.3](../19-delivery-and-scope.md)) says "deployed publicly," but for a portfolio artifact that is a cost/credentials/fragility call. Taken to the user before building; the decision was **layered + cost-sensitive**:

- author the full infrastructure-as-code **and** a one-command local demo now (offline, $0, reproducible), and
- keep the **live cloud apply as an authored runbook** the operator runs when a clickable URL is wanted (accounts exist, but cost-sensitive → scale-to-zero + teardown).

## 2. What shipped — three slices

**Slice 1 — foundation.**
- `backend/Dockerfile` (+ `.dockerignore`): the Cloud Run image — `python:3.12-slim`, non-root, `gunicorn config.wsgi:application` (2 workers × 4 threads, `--timeout 120`, `$PORT`-aware). One process serves both `/api/v1` and `/internal` (Django is the auth boundary — [specs/12 §12.5](../12-auth-and-security.md)); ADC only, no key files.
- `/readiness` cloudTasks **flip** (`core/views.py`): replaced the hard-coded `not_configured` with a **config reflection** (not a live ping — enqueuing a probe has side effects) that mirrors `internal.enqueue`'s `TASK_EXECUTION_MODE` dispatch + the `/internal` OIDC env — `not_configured` inline / `configured` when cloud dispatch is wired (`TASKS_AUDIENCE` + `TASKS_INVOKER_SA`) / `unavailable` if cloud mode is on but misconfigured. **Non-gating** in every case ([specs/16 §16.5](../16-observability.md)). The `openapi.yaml` `Readiness` enum gained `configured`.
- `make demo` → `infrastructure/scripts/demo-up.sh`: emulator + `seed_demo` + Django (inline) + Next.js, one key up / Ctrl-C down. Mirrors the known-good `e2e.sh` + `playwright.config.ts` emulator wiring.

**Slice 2 — provisioning IaC.** The [specs/21 §21.4](../21-deployment-and-operations.md) runbook as **idempotent, reviewable `bash`+`gcloud`**, parameterized by a git-ignored `infrastructure/config.env` (from `config.env.example`):

| Script | Does |
|---|---|
| `provision-iam.sh` | the two service accounts + roles (runtime `bsw-api@`, invoker `bsw-invoker@`), `actAs` + `run.invoker` |
| `deploy-api.sh` | Cloud Build the image + `gcloud run deploy`, then patch `TASKS_AUDIENCE` to the resolved URL (which flips readiness → `configured`) |
| `provision-queues.sh` | the 7 Cloud Tasks queues — retry/backoff/concurrency **mirror `internal/enqueue.py::_TASK_SPECS`** exactly |
| `provision-scheduler.sh` | the 6 Cloud Scheduler jobs (OIDC → `/internal/jobs/*`) |
| `deploy-firebase.sh` | rules/indexes from source + the `idempotencyKeys` TTL policy |
| `provision-all.sh` | the ordered runbook as one command; prints the interactive remainder |
| `teardown.sh` | deletes the billable resources (service, queues, jobs); `--purge` also drops the SAs — the **cost-off switch** |

Idempotency style: `describe → update|create` (queues/scheduler), upsert (`run deploy`), add-only IAM. `bash+gcloud` was chosen over Terraform deliberately — a reviewer reads exactly what is provisioned, and it matches the spec's `infrastructure/scripts/` wording (no state backend to babysit for a demo).

**Slice 3 — docs & demo.**
- `docs/demo-script.md`: a ~2-minute guided walk grounded in the deterministic seed (Jordan Lee healthy-active, Maria Santos failed-awaiting-retry, the `mgr`/`ops`/`admin` demo users) that follows the **real Playwright critical paths** — Path A (process a contribution: 202 → source `POSTED`), Path B (assign/review/resolve an exception), Flow 403 (server-side authz past a locked button) — and names the load-bearing engineering.
- README: new **Run the full demo** (`make demo`), **Architecture** (a mermaid CQRS diagram), and **Deploy** (the runbook + cost knob + teardown) sections; status + "Not yet built" reframed.
- Screenshot tooling: `make screenshots` → `infrastructure/scripts/screenshots.sh` + `frontend/scripts/capture-screenshots.mjs`, reusing the **proven** e2e `signIn` flow + selectors.

## 3. Honest reconciliations (reading the code, not just the spec)

The provisioning scripts were written against the *actual* registries, which surfaced spec/code deltas worth recording:

- **`MIN_INSTANCES=0` (the one deliberate deviation).** specs/21 §21.2 pins min-instances `1` (always warm). For a cost-sensitive demo the scripts default to `0` (scale-to-zero: $0 idle, ~2 s cold start), with `MAX_INSTANCES` capping fan-out. Documented as a **"Change from spec"** callout in specs/21 §21.2 — not a silent edit; set `1` for production.
- **Idempotency-key expiry** is the **Firestore TTL policy** (`deploy-firebase.sh`), so the code's registered `expire-idempotency-keys` job is left a manual `run_job` fallback — **not** provisioned as a redundant cron.
- **`rebuild-summaries`** runs twice (incremental `*/15`, full `0 3` with `{"mode":"full"}`) — matching the two schedules pinned in specs/21 §21.2 against one endpoint.
- The test-only **`noop`** queue/job is not provisioned.

## 4. Verification — by running

| Check | Result |
|---|---|
| `gunicorn --check-config config.wsgi:application` (the Dockerfile CMD target imports + loads) | ✅ exits 0 |
| `/readiness` cloudTasks reporting — 4 new unit tests (inline/configured/unavailable + non-gating via the live view) | ✅ 16 pass in `core.tests.test_security_unit` |
| `bash -n` + **shellcheck 0.11.0** on all 11 `infrastructure/scripts/*.sh` (`-e SC1090 -e SC1091`) | ✅ clean |
| `node --check frontend/scripts/capture-screenshots.mjs` | ✅ OK |
| Spectral (`openapi.yaml` — `Readiness` enum + `retryAfter`) | ✅ no errors |
| Backend regression (framework-free core + `@tag('unit')`) | ✅ 61 + 100 pass |
| **CI — all 7 jobs**, incl. the new `infra` job (shellcheck + **real `docker build`** of `backend/Dockerfile`) | ✅ green (PR #11) |

**Not run here (by design):** the live `gcloud` provisioning (no GCP project / interactive `gcloud auth` + billing — the operator's step), and the headless **screenshot capture** — its tooling is authored + static-verified (`bash -n`, shellcheck, `@playwright/test` import), but the live capture (cold emulator + Next-dev compile) exceeded the sandbox's timeout, so it is a warm-machine step (`make screenshots`); **no screenshots are embedded** — they are generated on demand. This is the same *authored-here / run-live* posture as the `gcloud` scripts.

## 5. What remains (deploy-only)

- **A live public deployment** — the `infrastructure/` IaC + runbook are authored and CI-checked; running them against a real GCP project + Vercel is the operator's cost/credentials step (`bash infrastructure/scripts/provision-all.sh`; `teardown.sh` stops the meter).
- **`propagate-denormalized`** fan-out (`U13`) — still deferred, awaiting its producer command (a name-change command that does not yet exist); the queue is provisioned so the seam is ready.

## 6. Process notes

- The consequential fork (how real the deployment should be) was taken to the user **before** building — the repo's understand→design→decide loop — via a two-question decision (target + cost posture); the answer (layered + cost-sensitive) shaped every default (scale-to-zero, teardown, local-first).
- Built on `release/phase-6` in three committed slices → draft PR #11 (early CI signal on the Docker build) → marked ready → CodeRabbit.
- **CodeRabbit:** 2 🟡 minor quick-wins, both fixed + verified + resolved — `persist-credentials: false` on the `infra` job's checkout (it runs build steps, never pushes → don't linger the `GITHUB_TOKEN`) and a `try/finally` in the capture script so a throw still closes Chromium. CodeRabbit **re-reviewed the fix commit clean** (0 open threads). Nitpicks skipped per the user's call.

---

With Phase 6 merged, the spec-driven build is complete: **Phases 1–6 on `main`.** A reviewer can `make demo` and click through the real workbench in ~30 s, with the full production topology authored as reviewable IaC beside it.
