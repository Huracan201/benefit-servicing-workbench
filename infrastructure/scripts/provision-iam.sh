#!/usr/bin/env bash
# infrastructure/scripts/provision-iam.sh — create the two service accounts + bind roles
# (specs/21 §21.2). Idempotent: an existing SA is left in place; role bindings are add-only.
#
#   runtime  bsw-api@…      runs the Cloud Run service (Firestore, Firebase Auth admin, Tasks
#                           enqueuer, logging, metrics) + actAs the invoker to mint OIDC tasks.
#   invoker  bsw-invoker@…  the identity Cloud Tasks/Scheduler authenticate AS (run.invoker).
#
# No JSON key files are ever created — Cloud Run uses ADC (specs/21 §21.2).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cmd gcloud

ensure_sa() { # <account-id> <display-name>
  local email="${1}@${PROJECT_ID}.iam.gserviceaccount.com"
  if gcloud iam service-accounts describe "${email}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    log "service account ${email}: exists"
  else
    log "service account ${email}: creating"
    gcloud iam service-accounts create "${1}" --display-name "${2}" --project "${PROJECT_ID}"
  fi
}

bind_project_role() { # <member-email> <role>
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member "serviceAccount:${1}" --role "${2}" --condition=None >/dev/null
  log "  bound ${2} -> ${1}"
}

ensure_sa "bsw-api" "BSW Cloud Run runtime"
ensure_sa "bsw-invoker" "BSW Cloud Tasks/Scheduler invoker"

log "binding runtime roles on ${RUNTIME_SA}…"
for role in \
  roles/datastore.user \
  roles/firebaseauth.admin \
  roles/cloudtasks.enqueuer \
  roles/logging.logWriter \
  roles/monitoring.metricWriter; do
  bind_project_role "${RUNTIME_SA}" "${role}"
done

# The runtime SA must actAs the invoker SA to mint OIDC-authenticated tasks (specs/21 §21.2).
log "allowing ${RUNTIME_SA} to actAs ${INVOKER_SA}…"
gcloud iam service-accounts add-iam-policy-binding "${INVOKER_SA}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role roles/iam.serviceAccountUser \
  --project "${PROJECT_ID}" >/dev/null

# The invoker SA needs run.invoker to call the service. Bound at project scope for simplicity;
# tighten to the service (gcloud run services add-iam-policy-binding) after deploy for least priv.
log "granting run.invoker to ${INVOKER_SA}…"
bind_project_role "${INVOKER_SA}" "roles/run.invoker"

log "IAM provisioned."
