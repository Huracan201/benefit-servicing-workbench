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

# A freshly-created service account is not instantly usable as an IAM policy member — a bind
# immediately after create can fail "does not exist". Give IAM a few seconds to propagate.
sleep 10

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

# The runtime SA reads DJANGO_SECRET_KEY from Secret Manager (deploy-api.sh --set-secrets), which
# needs secretAccessor ON that secret. Grant it when the secret exists (create it first — see the
# runbook / provision-all.sh); otherwise warn so it is not silently missed.
if gcloud secrets describe "${DJANGO_SECRET_KEY_SECRET}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  log "granting secretAccessor on ${DJANGO_SECRET_KEY_SECRET} to ${RUNTIME_SA}…"
  gcloud secrets add-iam-policy-binding "${DJANGO_SECRET_KEY_SECRET}" --project "${PROJECT_ID}" \
    --member "serviceAccount:${RUNTIME_SA}" --role roles/secretmanager.secretAccessor >/dev/null
else
  warn "secret '${DJANGO_SECRET_KEY_SECRET}' not found — create it, then re-run provision-iam.sh (idempotent) so the runtime SA can read it."
fi

log "IAM provisioned."
