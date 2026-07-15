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

# Retry a flaky gcloud command with linear backoff. A freshly-created service account takes a
# moment to become usable as an IAM policy member, so a bind right after create can transiently
# fail "does not exist" — retry rather than relying on one fixed sleep.
retry() {
  local n=1 max=6
  until "$@"; do
    [ "${n}" -ge "${max}" ] && return 1
    warn "  transient failure (attempt ${n}/${max}); retrying in $((n * 3))s…"
    sleep $((n * 3)); n=$((n + 1))
  done
}

bind_project_role() { # <member-email> <role>
  retry gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member "serviceAccount:${1}" --role "${2}" --condition=None >/dev/null \
    || die "failed to bind ${2} -> ${1}"
  log "  bound ${2} -> ${1}"
}

ensure_sa "bsw-api" "BSW Cloud Run runtime"
ensure_sa "bsw-invoker" "BSW Cloud Tasks/Scheduler invoker"

log "binding runtime roles on ${RUNTIME_SA}… (retry() absorbs fresh-SA propagation lag)"
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
retry gcloud iam service-accounts add-iam-policy-binding "${INVOKER_SA}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role roles/iam.serviceAccountUser \
  --project "${PROJECT_ID}" >/dev/null || die "failed to grant actAs on ${INVOKER_SA}"

# The invoker SA needs run.invoker to call the service. Bound at project scope for simplicity;
# tighten to the service (gcloud run services add-iam-policy-binding) after deploy for least priv.
log "granting run.invoker to ${INVOKER_SA}…"
bind_project_role "${INVOKER_SA}" "roles/run.invoker"

# The runtime SA reads DJANGO_SECRET_KEY from Secret Manager (deploy-api.sh --set-secrets), which
# needs secretAccessor ON that secret. Grant it when the secret exists (create it first — see the
# runbook / provision-all.sh); otherwise warn so it is not silently missed.
# Capture stderr so a genuine not-found (secret to be created later) is distinguished from a
# permission / API error (which must NOT be silently swallowed — deploy-api.sh always mounts it).
if secret_err="$(gcloud secrets describe "${DJANGO_SECRET_KEY_SECRET}" --project "${PROJECT_ID}" 2>&1 >/dev/null)"; then
  log "granting secretAccessor on ${DJANGO_SECRET_KEY_SECRET} to ${RUNTIME_SA}…"
  retry gcloud secrets add-iam-policy-binding "${DJANGO_SECRET_KEY_SECRET}" --project "${PROJECT_ID}" \
    --member "serviceAccount:${RUNTIME_SA}" --role roles/secretmanager.secretAccessor >/dev/null \
    || die "failed to grant secretAccessor on ${DJANGO_SECRET_KEY_SECRET}"
elif printf '%s' "${secret_err}" | grep -qiE "NOT_FOUND|was not found|does not exist"; then
  warn "secret '${DJANGO_SECRET_KEY_SECRET}' not found — create it, then re-run provision-iam.sh (idempotent) so the runtime SA can read it."
else
  die "could not check secret '${DJANGO_SECRET_KEY_SECRET}' — not a not-found error: ${secret_err}"
fi

log "IAM provisioned."
