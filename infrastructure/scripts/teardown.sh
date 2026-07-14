#!/usr/bin/env bash
# infrastructure/scripts/teardown.sh — delete the billable resources so an idle demo costs
# nothing: the Cloud Run service, the Cloud Scheduler jobs, and the Cloud Tasks queues.
#
# Firestore data + rules and the service accounts are retained (free / near-free) so a re-deploy
# is fast. Pass --purge to also delete the service accounts. Safe to re-run (missing = skipped).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cmd gcloud
PURGE="${1:-}"

del() { # human-label  gcloud-args...
  local label="$1"; shift
  if "$@" --quiet 2>/dev/null; then log "  deleted ${label}"; else log "  ${label}: absent (skipped)"; fi
}

log "deleting Cloud Scheduler jobs…"
for name in enqueue-due-contributions reconcile-stuck-payments rebuild-summaries \
            rebuild-summaries-full reap-expired-leases reset-demo; do
  del "job ${name}" gcloud scheduler jobs delete "${name}" --location "${REGION}" --project "${PROJECT_ID}"
done

log "deleting Cloud Tasks queues…"
for name in generate-schedule process-contribution reconcile-contribution \
            cancel-future-contributions shift-schedule propagate-denormalized update-projection; do
  del "queue ${name}" gcloud tasks queues delete "${name}" --location "${REGION}" --project "${PROJECT_ID}"
done

log "deleting the Cloud Run service ${SERVICE_NAME}…"
del "service ${SERVICE_NAME}" gcloud run services delete "${SERVICE_NAME}" --region "${REGION}" --project "${PROJECT_ID}"

if [ "${PURGE}" = "--purge" ]; then
  log "--purge: deleting the service accounts…"
  for email in "${RUNTIME_SA}" "${INVOKER_SA}"; do
    del "sa ${email}" gcloud iam service-accounts delete "${email}" --project "${PROJECT_ID}"
  done
fi

log "teardown complete (Firestore data + rules retained)."
