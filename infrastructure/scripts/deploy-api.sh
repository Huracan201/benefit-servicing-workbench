#!/usr/bin/env bash
# infrastructure/scripts/deploy-api.sh — build + deploy the command API to Cloud Run
# (specs/21 §21.2, §21.4 step 1). Idempotent (gcloud run deploy is an upsert).
#
# --allow-unauthenticated because Django is the auth boundary for BOTH /api/v1 (Firebase token)
# and /internal (OIDC) — specs/12 §12.5. Secrets come from Secret Manager, never the image.
# TASKS_AUDIENCE is the service's own URL, unknown until first deploy, so we deploy then patch it.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cmd gcloud

: "${IMAGE:=${REGION}-docker.pkg.dev/${PROJECT_ID}/bsw/${SERVICE_NAME}:latest}"

log "building ${IMAGE} from backend/ via Cloud Build…"
gcloud builds submit "${ROOT}/backend" --tag "${IMAGE}" --project "${PROJECT_ID}"

log "deploying ${SERVICE_NAME} to Cloud Run (${REGION}, min-instances=${MIN_INSTANCES}, max=${MAX_INSTANCES})…"
env_vars="ENVIRONMENT=production,DEBUG=0,DJANGO_SETTINGS_MODULE=config.settings"
env_vars="${env_vars},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},SYSTEM_TIMEZONE=America/New_York"
env_vars="${env_vars},TASK_EXECUTION_MODE=cloud,TASKS_LOCATION=${REGION},TASKS_INVOKER_SA=${INVOKER_SA}"
env_vars="${env_vars},CORS_ALLOWED_ORIGINS=${CORS_ALLOWED_ORIGINS}"

gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" --region "${REGION}" --project "${PROJECT_ID}" \
  --service-account "${RUNTIME_SA}" \
  --allow-unauthenticated \
  --min-instances "${MIN_INSTANCES}" --max-instances "${MAX_INSTANCES}" \
  --concurrency "${CONCURRENCY}" --cpu "${CPU}" --memory "${MEMORY}" \
  --set-env-vars "${env_vars}" \
  --set-secrets "DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY_SECRET}:latest"

url="$(api_url)"
[ -n "${url}" ] || die "deploy reported success but the service URL did not resolve"

# Patch TASKS_AUDIENCE to the resolved URL so enqueued OIDC tasks target the right audience
# (this also flips /readiness cloudTasks from 'unavailable' to 'configured').
log "wiring TASKS_AUDIENCE=${url}…"
gcloud run services update "${SERVICE_NAME}" \
  --region "${REGION}" --project "${PROJECT_ID}" \
  --update-env-vars "TASKS_AUDIENCE=${url}"

log "API deployed: ${url}"
log "  health:    ${url}/health"
log "  readiness: ${url}/readiness   (cloudTasks should now read 'configured')"
