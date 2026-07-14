#!/usr/bin/env bash
# infrastructure/scripts/provision-scheduler.sh — create-or-update the Cloud Scheduler jobs.
#
# Idempotent. Schedules + timezone are pinned in specs/21 §21.2. Each job POSTs to
# /internal/jobs/<endpoint> carrying an OIDC token for INVOKER_SA (aud = the Cloud Run URL),
# which firebase_auth/middleware.py validates. Requires the API to be deployed first (for its URL).
#
# Idempotency-key expiry is handled by the Firestore TTL policy (deploy-firebase.sh), NOT a cron,
# so the code's `expire-idempotency-keys` job is left as a manual `run_job` fallback — not scheduled.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cmd gcloud

API_URL="${API_URL:-$(api_url)}"
[ -n "${API_URL}" ] || die "cannot resolve the Cloud Run URL — run deploy-api.sh first (or export API_URL)"
TIME_ZONE="America/New_York"

# name|schedule|body|endpoint   (body '-' => none; endpoint defaults to name)
# rebuild-summaries runs incrementally every 15m AND a full recompute at 03:00 (specs/21 §21.2);
# the full run is a second job hitting the same endpoint with {"mode":"full"}.
JOBS=(
  "enqueue-due-contributions|0 9-17 * * 1-5|-|"
  "reconcile-stuck-payments|*/10 * * * *|-|"
  "rebuild-summaries|*/15 * * * *|-|"
  "rebuild-summaries-full|0 3 * * *|{\"mode\":\"full\"}|rebuild-summaries"
  "reap-expired-leases|*/5 * * * *|-|"
  "reset-demo|0 5 * * *|-|"
)

for spec in "${JOBS[@]}"; do
  IFS='|' read -r name sched body endpoint <<<"${spec}"
  endpoint="${endpoint:-${name}}"
  uri="${API_URL}/internal/jobs/${endpoint}"
  args=(
    --location "${REGION}" --project "${PROJECT_ID}"
    --schedule "${sched}" --time-zone "${TIME_ZONE}"
    --uri "${uri}" --http-method POST
    --oidc-service-account-email "${INVOKER_SA}"
    --oidc-token-audience "${API_URL}"
  )
  if [ "${body}" != "-" ]; then
    args+=(--message-body "${body}" --headers "Content-Type=application/json")
  fi
  if gcloud scheduler jobs describe "${name}" --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    log "scheduler ${name}: updating (-> ${endpoint} @ '${sched}')"
    gcloud scheduler jobs update http "${name}" "${args[@]}"
  else
    log "scheduler ${name}: creating (-> ${endpoint} @ '${sched}')"
    gcloud scheduler jobs create http "${name}" "${args[@]}"
  fi
done

log "scheduler jobs provisioned (${#JOBS[@]})."
