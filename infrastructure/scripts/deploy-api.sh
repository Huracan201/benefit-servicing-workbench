#!/usr/bin/env bash
# infrastructure/scripts/deploy-api.sh — build + deploy the command API to Cloud Run
# (specs/21 §21.2, §21.4 step 1). Idempotent (gcloud run deploy is an upsert).
#
# --allow-unauthenticated because Django is the auth boundary for BOTH /api/v1 (Firebase token)
# and /internal (OIDC) — specs/12 §12.5. Secrets come from Secret Manager, never the image.
# TASKS_AUDIENCE + ALLOWED_HOSTS use the DETERMINISTIC service URL (lib.sh service_url), known
# before the first deploy, so they are set in one shot — no post-deploy patch, no host mismatch.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cmd gcloud

: "${IMAGE:=${REGION}-docker.pkg.dev/${PROJECT_ID}/bsw/${SERVICE_NAME}:latest}"

log "building ${IMAGE} from backend/ via Cloud Build…"
# Ensure the Artifact Registry docker repo exists (idempotent) — the image target needs it.
gcloud artifacts repositories describe bsw --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1 \
  || gcloud artifacts repositories create bsw --repository-format=docker --location "${REGION}" --project "${PROJECT_ID}"

gcloud builds submit "${ROOT}/backend" --tag "${IMAGE}" --project "${PROJECT_ID}"

log "deploying ${SERVICE_NAME} to Cloud Run (${REGION}, min-instances=${MIN_INSTANCES}, max=${MAX_INSTANCES})…"

# Fail BEFORE build/deploy if a prod-guardrail value is missing — otherwise Cloud Run creates a
# revision that crash-loops on the settings.py boot guardrail.
case "${INTERNAL_DEV_SECRET:-}" in
  "" | "dev-internal-secret")
    die "INTERNAL_DEV_SECRET is empty or the dev default — set it in config.env (e.g. \`openssl rand -base64 32\`); the production guardrail rejects it";;
esac

API_URL="$(service_url)"
# ALLOWED_HOSTS ALWAYS includes the deterministic host (API_URL, health checks, and the OIDC task
# audience all use it); any config.env value is APPENDED, never substituted, so an override cannot
# drop the host Django must accept.
allowed_hosts="$(service_host)${ALLOWED_HOSTS:+,${ALLOWED_HOSTS}}"

# '@' delimiter (gcloud ^@^ syntax) so commas inside ALLOWED_HOSTS / CORS_ALLOWED_ORIGINS are
# literal list separators, not env-var-pair separators.
env_vars="^@^ENVIRONMENT=production@DEBUG=0@DJANGO_SETTINGS_MODULE=config.settings"
env_vars="${env_vars}@GOOGLE_CLOUD_PROJECT=${PROJECT_ID}@SYSTEM_TIMEZONE=America/New_York"
env_vars="${env_vars}@TASK_EXECUTION_MODE=cloud@TASKS_LOCATION=${REGION}@TASKS_INVOKER_SA=${INVOKER_SA}"
env_vars="${env_vars}@CORS_ALLOWED_ORIGINS=${CORS_ALLOWED_ORIGINS}"
# ENVIRONMENT=production guardrail (config/settings.py): explicit non-wildcard ALLOWED_HOSTS;
# INTERNAL_DEV_SECRET not the dev default; TASKS_AUDIENCE is the deterministic OIDC audience.
env_vars="${env_vars}@ALLOWED_HOSTS=${allowed_hosts}@INTERNAL_DEV_SECRET=${INTERNAL_DEV_SECRET}@TASKS_AUDIENCE=${API_URL}"

gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" --region "${REGION}" --project "${PROJECT_ID}" \
  --service-account "${RUNTIME_SA}" \
  --allow-unauthenticated \
  --min-instances "${MIN_INSTANCES}" --max-instances "${MAX_INSTANCES}" \
  --concurrency "${CONCURRENCY}" --cpu "${CPU}" --memory "${MEMORY}" \
  --set-env-vars "${env_vars}" \
  --set-secrets "DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY_SECRET}:latest"

log "API deployed: ${API_URL}"
log "  health:    ${API_URL}/health"
log "  readiness: ${API_URL}/readiness   (cloudTasks should read 'configured')"
